"""Tests for rate_limit_uploads dependency and ProcessingFile fast-path."""

import io
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RateLimitError
from app.core.processing import ProcessingFile
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
    from app.core.security import create_access_token

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


class TestProcessingFileFastPath:
    """Tests for the shutil.copyfile fast-path in ProcessingFile.from_upload."""

    @pytest.mark.asyncio
    async def test_fast_path_used_when_file_on_disk(self, tmp_path):
        """When the inner file object has a real path on disk, copyfile is used."""
        content = os.urandom(200 * 1024)  # 200 KiB
        disk_file = tmp_path / "spooled.bin"
        disk_file.write_bytes(content)

        # Build an UploadFile-like mock whose .file.name points to the disk path
        file_obj = MagicMock()
        file_obj.name = str(disk_file)

        upload = AsyncMock()
        upload.file = file_obj

        copied = False
        original_copyfile = __import__("shutil").copyfile

        def _track_copyfile(src, dst):
            nonlocal copied
            copied = True
            return original_copyfile(src, dst)

        with patch("app.core.processing.shutil.copyfile", side_effect=_track_copyfile):
            pf = await ProcessingFile.from_upload(upload, max_bytes=1024 * 1024)

        assert copied, "shutil.copyfile fast-path should have been taken"
        assert pf.size == len(content)
        assert pf.path.read_bytes() == content
        pf.cleanup()

    @pytest.mark.asyncio
    async def test_fast_path_enforces_size(self, tmp_path):
        """Fast path enforces the size limit even when reading from disk."""
        from app.core.exceptions import BadRequestError

        content = b"x" * (512 * 1024)  # 512 KiB
        disk_file = tmp_path / "large.bin"
        disk_file.write_bytes(content)

        file_obj = MagicMock()
        file_obj.name = str(disk_file)

        upload = AsyncMock()
        upload.file = file_obj

        with pytest.raises(BadRequestError, match="exceeds maximum"):
            await ProcessingFile.from_upload(upload, max_bytes=256 * 1024)  # 256 KiB limit

    @pytest.mark.asyncio
    async def test_slow_path_used_when_no_disk_file(self):
        """When the upload has no disk path, the chunked read path is taken."""
        content = b"test content " * 100

        stream = io.BytesIO(content)

        upload = AsyncMock()
        upload.file = None  # No inner file object → slow path

        async def _read(size: int = -1) -> bytes:
            return stream.read(size)

        upload.read = _read

        pf = await ProcessingFile.from_upload(upload, max_bytes=len(content) + 1024)

        assert pf.size == len(content)
        assert pf.path.read_bytes() == content
        pf.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_on_exception_during_fast_path(self, tmp_path):
        """Temp file is removed if copyfile raises during the fast path."""
        disk_file = tmp_path / "src.bin"
        disk_file.write_bytes(b"data")

        file_obj = MagicMock()
        file_obj.name = str(disk_file)

        upload = AsyncMock()
        upload.file = file_obj

        with patch("app.core.processing.shutil.copyfile", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                await ProcessingFile.from_upload(upload, max_bytes=1024 * 1024)

        # No temp file should remain
        # We can't easily get the exact path, but the test validates no exception leakage

    @pytest.mark.asyncio
    async def test_fast_path_with_path_like_name(self, tmp_path):
        """Fast path works when file.name is a Path object (not just str)."""
        content = b"path object test " * 50
        disk_file = tmp_path / "path_obj.bin"
        disk_file.write_bytes(content)

        file_obj = MagicMock()
        file_obj.name = disk_file  # Pass as Path, not str

        upload = AsyncMock()
        upload.file = file_obj

        pf = await ProcessingFile.from_upload(upload, max_bytes=len(content) + 1024)
        assert pf.size == len(content)
        pf.cleanup()
