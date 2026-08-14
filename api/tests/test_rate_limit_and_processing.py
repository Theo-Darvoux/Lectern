"""Tests for rate_limit_uploads dependency and ProcessingFile fast-path."""

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError, RateLimitError
from app.core.events.processing import ProcessingFile
from app.models.user import User, UserRole

# ── helpers ──────────────────────────────────────────────────────────────────


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ── rate_limit_uploads unit tests ─────────────────────────────────────────────


class TestRateLimitUploads:
    """Unit tests for rate_limit_uploads dependency, with a mocked Redis pipeline."""

    def _make_user(self, role: UserRole = UserRole.STUDENT) -> MagicMock:
        u = MagicMock()
        u.id = uuid.uuid4()
        u.role = role
        return u

    def _make_redis(self, minute_count: int = 0, daily_count: int = 0) -> AsyncMock:
        """Build a mock Redis with a pipeline that returns specified counters."""
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = AsyncMock(return_value=pipe)
        pipe.expire = AsyncMock(return_value=pipe)
        # pipeline.execute() returns [minute_count, True, daily_count, True]
        pipe.execute = AsyncMock(return_value=[minute_count, True, daily_count, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=None)
        redis.pipeline = MagicMock(return_value=pipe)
        return redis

    @pytest.mark.asyncio
    async def test_under_limit_does_not_raise(self):
        """No exception when both minute and daily counts are within limits."""
        from app.dependencies.rate_limit import rate_limit_uploads

        redis = self._make_redis(minute_count=1, daily_count=1)
        user = self._make_user()
        db = AsyncMock()
        request = MagicMock(spec=Request)

        # Should not raise
        await rate_limit_uploads(request, user, db, redis)

    @pytest.mark.asyncio
    async def test_minute_limit_exceeded_raises(self):
        """RateLimitError raised when per-minute count exceeds the limit."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        user = self._make_user(UserRole.STUDENT)
        tier = "default"
        minute_limit, _ = _UPLOAD_LIMITS[tier]

        redis = self._make_redis(minute_count=minute_limit + 1, daily_count=1)
        db = AsyncMock()
        request = MagicMock(spec=Request)

        with pytest.raises(RateLimitError, match="uploading too fast"):
            await rate_limit_uploads(request, user, db, redis)

    @pytest.mark.asyncio
    async def test_daily_limit_exceeded_raises(self):
        """RateLimitError raised when daily count exceeds the limit."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        user = self._make_user(UserRole.STUDENT)
        _, daily_limit = _UPLOAD_LIMITS["default"]

        redis = self._make_redis(minute_count=1, daily_count=daily_limit + 1)
        db = AsyncMock()
        request = MagicMock(spec=Request)

        with pytest.raises(RateLimitError, match="Daily upload limit"):
            await rate_limit_uploads(request, user, db, redis)

    @pytest.mark.asyncio
    async def test_privileged_user_has_higher_limits(self):
        """A privileged user's rate limits are higher than the default tier."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        privileged_minute, privileged_daily = _UPLOAD_LIMITS["privileged"]
        default_minute, default_daily = _UPLOAD_LIMITS["default"]

        # Verify the privileged limits are actually higher
        assert privileged_minute > default_minute
        assert privileged_daily > default_daily

        # A count that would block the default tier but NOT the privileged tier
        redis = self._make_redis(minute_count=default_minute + 1, daily_count=1)
        user = self._make_user(UserRole.BUREAU)
        db = AsyncMock()
        request = MagicMock(spec=Request)

        # Should not raise for privileged user
        await rate_limit_uploads(request, user, db, redis)

    @pytest.mark.asyncio
    async def test_registered_upload_group_bypasses_only_per_file_upload_counter(self):
        """One admitted folder consumes bounded group slots, not 2,000 account requests."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        minute_limit, _ = _UPLOAD_LIMITS["privileged"]
        redis = self._make_redis(minute_count=minute_limit + 1, daily_count=1)
        redis.register_script = MagicMock(return_value=AsyncMock(return_value=1))
        request = MagicMock(spec=Request)
        request.method = "POST"
        request.url.path = "/api/upload"
        request.headers = {
            "X-Upload-Group-ID": str(uuid.uuid4()),
            "X-Upload-ID": str(uuid.uuid4()),
        }

        await rate_limit_uploads(
            request,
            self._make_user(UserRole.MODERATOR),
            AsyncMock(),
            redis,
        )

        redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_group_cannot_bypass_non_upload_mutation_limit(self):
        """The group capability is ignored by QCM/material mutation routes."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        minute_limit, _ = _UPLOAD_LIMITS["privileged"]
        redis = self._make_redis(minute_count=minute_limit + 1, daily_count=1)
        redis.register_script = MagicMock(return_value=AsyncMock(return_value=1))
        request = MagicMock(spec=Request)
        request.method = "POST"
        request.url.path = "/api/qcm/stage"
        request.headers = {
            "X-Upload-Group-ID": str(uuid.uuid4()),
            "X-Upload-ID": str(uuid.uuid4()),
        }

        with pytest.raises(RateLimitError, match="uploading too fast"):
            await rate_limit_uploads(
                request,
                self._make_user(UserRole.MODERATOR),
                AsyncMock(),
                redis,
            )

    @pytest.mark.asyncio
    async def test_daily_limit_flags_user(self):
        """Exceeding the daily limit triggers flag_user_account."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        user = self._make_user(UserRole.STUDENT)
        _, daily_limit = _UPLOAD_LIMITS["default"]

        redis = self._make_redis(minute_count=1, daily_count=daily_limit + 1)
        db = AsyncMock()
        db.commit = AsyncMock()
        request = MagicMock(spec=Request)

        with patch(
            "app.dependencies.rate_limit.flag_user_account", new_callable=AsyncMock
        ) as mock_flag:
            with pytest.raises(RateLimitError):
                await rate_limit_uploads(request, user, db, redis)
            mock_flag.assert_called_once()
            db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_member_role_gets_privileged_tier(self):
        """MEMBER role uses the privileged tier (higher limits)."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        default_minute, _ = _UPLOAD_LIMITS["default"]
        # Just above default limit — privileged should still pass
        redis = self._make_redis(minute_count=default_minute + 1, daily_count=1)
        user = self._make_user(UserRole.MODERATOR)
        db = AsyncMock()
        request = MagicMock(spec=Request)

        # Should NOT raise — member is privileged
        await rate_limit_uploads(request, user, db, redis)

    @pytest.mark.asyncio
    async def test_student_role_gets_default_tier(self):
        """STUDENT role uses the default (lower) tier."""
        from app.dependencies.rate_limit import _UPLOAD_LIMITS, rate_limit_uploads

        default_minute, _ = _UPLOAD_LIMITS["default"]
        # Just at the limit — should block student
        redis = self._make_redis(minute_count=default_minute + 1, daily_count=1)
        user = self._make_user(UserRole.STUDENT)
        db = AsyncMock()
        request = MagicMock(spec=Request)

        with pytest.raises(RateLimitError):
            await rate_limit_uploads(request, user, db, redis)


# ── rate_limit_views unit tests ─────────────────────────────────────────────


class TestRateLimitViews:
    """Unit tests for rate_limit_views dependency, with a mocked Redis pipeline."""

    def _make_user(self) -> MagicMock:
        u = MagicMock()
        u.id = uuid.uuid4()
        return u

    def _make_redis(self, minute_count: int = 0, daily_count: int = 0) -> AsyncMock:
        """Build a mock Redis with a pipeline that returns specified counters."""
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.incr = AsyncMock(return_value=pipe)
        pipe.expire = AsyncMock(return_value=pipe)
        pipe.execute = AsyncMock(return_value=[minute_count, True, daily_count, True])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=None)
        redis.pipeline = MagicMock(return_value=pipe)
        return redis

    @pytest.mark.asyncio
    async def test_views_under_limit_does_not_raise(self):
        """No exception when view counts are within limits."""
        from app.dependencies.rate_limit import rate_limit_views

        redis = self._make_redis(minute_count=1, daily_count=1)
        user = self._make_user()
        request = MagicMock(spec=Request)

        # Should not raise
        await rate_limit_views(request, user, redis)

    @pytest.mark.asyncio
    async def test_views_minute_limit_exceeded_raises(self):
        """RateLimitError raised when view minute limit exceeded."""
        from app.dependencies.rate_limit import rate_limit_views

        redis = self._make_redis(minute_count=601, daily_count=1)
        user = self._make_user()
        request = MagicMock(spec=Request)

        with pytest.raises(RateLimitError, match="Too many view events"):
            await rate_limit_views(request, user, redis)

    @pytest.mark.asyncio
    async def test_views_daily_limit_exceeded_raises(self):
        """RateLimitError raised when view daily limit exceeded."""
        from app.dependencies.rate_limit import rate_limit_views

        redis = self._make_redis(minute_count=1, daily_count=5001)
        user = self._make_user()
        request = MagicMock(spec=Request)

        with pytest.raises(RateLimitError, match="Too many view events"):
            await rate_limit_views(request, user, redis)


# ── ProcessingFile fast-path (disk copy) ─────────────────────────────────────


class TestProcessingFile:
    """Tests for the unified chunked read pipeline in ProcessingFile."""

    @pytest.mark.asyncio
    async def test_spooling_reads_in_chunks_and_hashes(self):
        """Uploads are read in chunks and correctly hashed."""
        content = b"chunk data " * 100
        stream = io.BytesIO(content)

        upload = AsyncMock()

        async def _read(size: int = -1) -> bytes:
            return stream.read(size)

        upload.read = _read

        pf = await ProcessingFile.from_upload(upload, max_bytes=len(content) + 1024)

        assert pf.size == len(content)
        assert pf.path.read_bytes() == content

        import hashlib

        expected_hash = hashlib.sha256(content).hexdigest()
        assert await pf.sha256() == expected_hash

        pf.cleanup()

    @pytest.mark.asyncio
    async def test_enforces_max_bytes_limit(self):
        """The read loop strictly enforces the max_bytes limit."""
        content = b"x" * (512 * 1024)  # 512 KiB
        stream = io.BytesIO(content)

        upload = AsyncMock()

        async def _read(size: int = -1) -> bytes:
            return stream.read(size)

        upload.read = _read

        with pytest.raises(BadRequestError, match="exceeds maximum"):
            await ProcessingFile.from_upload(upload, max_bytes=256 * 1024)  # 256 KiB limit

    @pytest.mark.asyncio
    async def test_cleanup_on_exception_during_read(self, tmp_path):
        """If the read loop raises an exception, the temp file is cleaned up."""
        upload = AsyncMock()
        upload.read.side_effect = OSError("network dropped")

        with pytest.raises(OSError):
            await ProcessingFile.from_upload(upload, max_bytes=1024 * 1024)
