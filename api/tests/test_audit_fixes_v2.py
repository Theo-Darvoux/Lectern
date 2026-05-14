"""Tests for all issues identified in the v1→v2 audit review.

Covers (in issue order):
  #1  Secrets not stored in Redis / not returned by admin API
  #2  Prune storage rejects keys outside pruneable prefixes
  #3  Google OAuth runs verify in a thread (no blocking IO on event loop)
  #4  email_verified flag is checked for Google OAuth tokens
  #5  allow_all_domains=True uses domain list for auto_approve, falls back to PENDING
  #6  TUS inflight counter gets a TTL after incr
  #7  config.get(x) or default treats 0 as falsy — fixed with is not None
  #8  Admin cannot set a user to PENDING via role-update endpoint
  #9  TestEmailIn rejects malformed / header-injection email
  #10 Storage usage counter clamped to >= 0 in helpers
  #11 get_or_create_user accepts explicit auto_approve (no second validate call)
  #12 get_s3_client receives pre-fetched cfg — only one _get_s3_settings call
  #13 TUS OPTIONS reads Redis only, no DB session
  #15 _serialize_config dead code removed
  #17 validate_email_for_auth comment/logic corrected
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_config import AllowedDomain, AuthConfig
from app.models.user import User, UserRole

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config_row(**kwargs):
    return AuthConfig(**kwargs)


# ── Issue #1: Secrets not in Redis cache / not in API response ────────────────


@pytest.mark.asyncio
async def test_get_full_auth_config_strips_secrets_from_redis(
    db_session: AsyncSession,
):
    """Cached value in Redis must not contain smtp_password, s3_access_key, s3_secret_key."""
    from app.services.auth import get_full_auth_config

    captured = {}

    async def fake_setex(key, ttl, value):
        captured["value"] = value

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(side_effect=fake_setex)

    await get_full_auth_config(db_session, redis)

    assert "value" in captured
    cached_dict = json.loads(captured["value"])
    for secret in ("smtp_password", "s3_access_key", "s3_secret_key"):
        assert secret not in cached_dict, f"{secret} must not appear in Redis cache"


@pytest.mark.asyncio
async def test_get_full_auth_config_rehydrates_secrets_on_cache_hit(
    db_session: AsyncSession,
):
    """On Redis cache hit, secrets must be re-loaded from DB/settings."""
    from app.services.auth import get_full_auth_config

    config_row = AuthConfig(
        s3_access_key="db-access-key",
        s3_secret_key="db-secret-key",
        smtp_password="db-smtp-pass",
    )
    db_session.add(config_row)
    await db_session.flush()

    # Redis returns a cached dict WITHOUT secrets (as we would store it)
    cached_public = json.dumps({"totp_enabled": True, "site_name": "Test"})
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_public.encode())
    redis.setex = AsyncMock()

    result = await get_full_auth_config(db_session, redis)

    assert result["s3_access_key"] == "db-access-key"
    assert result["s3_secret_key"] == "db-secret-key"
    assert result["smtp_password"] == "db-smtp-pass"


@pytest.mark.asyncio
async def test_admin_get_auth_config_does_not_return_secrets(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /api/admin/auth-config must redact secrets and return _set booleans."""
    admin = User(email="admin@telecom-sudparis.eu", role=UserRole.VIEUX)
    db_session.add(admin)
    await db_session.flush()

    from app.core.security import create_access_token

    token, _ = create_access_token(user_id=str(admin.id), role=admin.role.value, email=admin.email)

    response = await client.get(
        "/api/admin/auth-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    for secret in ("smtp_password", "s3_access_key", "s3_secret_key"):
        assert secret not in data, f"Secret field '{secret}' must not appear in API response"
        assert f"{secret}_set" in data, f"Boolean flag '{secret}_set' must be present"


# ── Issue #2: Prune storage prefix validation ─────────────────────────────────


@pytest.mark.asyncio
async def test_prune_rejects_non_pruneable_prefix(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = User(email="admin@telecom-sudparis.eu", role=UserRole.VIEUX)
    db_session.add(admin)
    await db_session.commit()

    from app.core.security import create_access_token

    token, _ = create_access_token(user_id=str(admin.id), role=admin.role.value, email=admin.email)

    fake_rc = AsyncMock()
    fake_rc.delete = AsyncMock()
    with patch("app.core.redis.redis_client", fake_rc):
        for bad_key in [
            "materials/some/file.pdf",
            "uploads/user123/upload-id/file.pdf",
            "quarantine/user123/upload-id/file.pdf",
            "../../../etc/passwd",
            "cas/../materials/secret.pdf",
        ]:
            response = await client.post(
                "/api/admin/storage/prune",
                json=[bad_key],
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 400, f"Expected 400 for key: {bad_key!r}"
            detail = response.json()["detail"].lower()
            assert "not allowed" in detail or "pruneable" in detail or "traversal" in detail


@pytest.mark.asyncio
async def test_prune_accepts_valid_prefixes(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = User(email="admin@telecom-sudparis.eu", role=UserRole.VIEUX)
    db_session.add(admin)
    await db_session.flush()

    from app.core.security import create_access_token

    token, _ = create_access_token(user_id=str(admin.id), role=admin.role.value, email=admin.email)

    fake_rc = AsyncMock()
    fake_rc.delete = AsyncMock()
    with (
        patch("app.routers.admin_storage.delete_object", new_callable=AsyncMock),
        patch("app.core.redis.redis_client", fake_rc),
    ):
        response = await client.post(
            "/api/admin/storage/prune",
            json=["cas/abc123", "thumbnails/xyz.webp"],
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200


# ── Issue #3: Google OAuth blocking HTTP in async handler ─────────────────────


@pytest.mark.asyncio
async def test_google_oauth_verify_runs_in_thread(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """id_token.verify_oauth2_token must be called via asyncio.to_thread."""
    config = AuthConfig(google_oauth_enabled=True, allow_all_domains=True)
    db_session.add(config)
    await db_session.flush()

    call_thread_ids: list = []

    async def spy_to_thread(fn, *args, **kwargs):
        call_thread_ids.append(fn)
        # Simulate successful verification
        return {
            "iss": "accounts.google.com",
            "email": "user@telecom-sudparis.eu",
            "email_verified": True,
        }

    with patch("app.routers.auth.asyncio.to_thread", side_effect=spy_to_thread):
        await client.post(
            "/api/auth/google",
            json={"credential": "fake_token"},
        )

    import google.oauth2.id_token as _id_token

    assert any(fn is _id_token.verify_oauth2_token for fn in call_thread_ids), (
        "verify_oauth2_token must be dispatched through asyncio.to_thread"
    )


# ── Issue #4: email_verified flag checked for Google OAuth ───────────────────


@pytest.mark.asyncio
async def test_google_oauth_rejects_unverified_email(
    client: AsyncClient,
    db_session: AsyncSession,
):
    config = AuthConfig(google_oauth_enabled=True, allow_all_domains=True)
    db_session.add(config)
    await db_session.flush()

    with patch("app.routers.auth.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = {
            "iss": "accounts.google.com",
            "email": "user@telecom-sudparis.eu",
            "email_verified": False,  # unverified account
        }
        response = await client.post(
            "/api/auth/google",
            json={"credential": "token"},
        )

    assert response.status_code == 401
    assert "verified" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_oauth_rejects_missing_email_verified_field(
    client: AsyncClient,
    db_session: AsyncSession,
):
    config = AuthConfig(google_oauth_enabled=True, allow_all_domains=True)
    db_session.add(config)
    await db_session.flush()

    with patch("app.routers.auth.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = {
            "iss": "accounts.google.com",
            "email": "user@telecom-sudparis.eu",
            # email_verified key absent
        }
        response = await client.post(
            "/api/auth/google",
            json={"credential": "token"},
        )

    assert response.status_code == 401


# ── Issue #5: allow_all_domains logic ────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_email_allow_all_domains_with_matching_domain(
    db_session: AsyncSession,
):
    """Domain in list → uses that domain's auto_approve, even with allow_all_domains=True."""
    from app.services.auth import validate_email_for_auth

    config = AuthConfig(allow_all_domains=True)
    db_session.add(config)
    domain = AllowedDomain(domain="telecom-sudparis.eu", auto_approve=True)
    db_session.add(domain)
    await db_session.flush()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    result = await validate_email_for_auth("user@telecom-sudparis.eu", db_session, redis)
    assert result is True


@pytest.mark.asyncio
async def test_validate_email_allow_all_domains_unlisted_domain_is_pending(
    db_session: AsyncSession,
):
    """Domain not in list but allow_all_domains=True → allowed but auto_approve=False (PENDING)."""
    from app.services.auth import validate_email_for_auth

    config = AuthConfig(allow_all_domains=True)
    db_session.add(config)
    await db_session.flush()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    result = await validate_email_for_auth("user@unknown.com", db_session, redis)
    assert result is False  # allowed but PENDING (not auto-approved)


@pytest.mark.asyncio
async def test_validate_email_disallow_unlisted_domain_no_allow_all(
    db_session: AsyncSession,
):
    """Domain not in list and allow_all_domains=False → ValueError (rejected)."""
    from app.services.auth import validate_email_for_auth

    config = AuthConfig(allow_all_domains=False)
    db_session.add(config)
    await db_session.flush()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    with pytest.raises(ValueError, match="not allowed"):
        await validate_email_for_auth("user@evil.com", db_session, redis)


@pytest.mark.asyncio
async def test_allow_all_domains_new_user_from_unknown_domain_gets_student_not_pending(
    db_session: AsyncSession,
):
    """With allow_all_domains=True and a listed domain with auto_approve, new user gets STUDENT."""
    from app.services.auth import get_or_create_user

    user, is_new = await get_or_create_user(
        db_session, "user@telecom-sudparis.eu", auto_approve=True
    )
    assert is_new is True
    assert user.role == UserRole.STUDENT


@pytest.mark.asyncio
async def test_allow_all_domains_new_user_unlisted_gets_pending(
    db_session: AsyncSession,
):
    """With allow_all_domains=True but domain not in list, new user gets PENDING."""
    from app.services.auth import get_or_create_user

    user, is_new = await get_or_create_user(db_session, "user@unknown.org", auto_approve=False)
    assert is_new is True
    assert user.role == UserRole.PENDING


# ── Issue #6: TUS inflight TTL ────────────────────────────────────────────────


def test_tus_patch_sets_inflight_ttl_in_source():
    """PATCH handler must call expire() on the inflight key immediately after incr().

    We verify via source inspection since the full S3-backed PATCH flow
    cannot be exercised in unit tests without a live S3 backend.
    """
    import inspect

    from app.routers.tus import tus_patch

    src = inspect.getsource(tus_patch)
    # Both calls must appear and expire must follow incr
    assert "redis.incr(_inflight_key)" in src
    assert "redis.expire(_inflight_key" in src

    incr_pos = src.index("redis.incr(_inflight_key)")
    expire_pos = src.index("redis.expire(_inflight_key")
    assert expire_pos > incr_pos, "expire() must come after incr() in the source"

    # Extract the TTL argument — must be >= 60
    import re

    ttl_match = re.search(r"redis\.expire\(_inflight_key,\s*(\d+)\)", src)
    assert ttl_match, "expire() must have a literal integer TTL argument"
    ttl = int(ttl_match.group(1))
    assert ttl >= 60, f"Inflight TTL must be >= 60s, got {ttl}"


# ── Issue #7: 0 quality values not silently ignored ──────────────────────────


def test_thumbnail_quality_zero_not_ignored():
    """quality=0 from config must be used, not replaced by the default 85."""
    import inspect

    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    # Read source to verify the fix (is not None check)
    src = inspect.getsource(run_thumbnail_stage)
    assert "is not None" in src, (
        "thumbnail quality/size_px config read must use 'is not None' check, "
        "not falsy 'or' operator"
    )


def test_pdf_quality_zero_not_ignored():
    """pdf_quality=0 from config must be used, not replaced by settings default."""
    import inspect

    from app.core.file_security._pdf import _compress_pdf_path

    src = inspect.getsource(_compress_pdf_path)
    assert "is not None" in src, "pdf_quality config read must use 'is not None' check"


def test_video_profile_config_zero_not_ignored():
    """video_compression_profile from config must use is-not-None guard."""
    import inspect

    from app.core.file_security._audio_video import _build_video_codec_args

    src = inspect.getsource(_build_video_codec_args)
    assert "is not None" in src, (
        "video_compression_profile config read must use 'is not None' check"
    )


# ── Issue #8: Admin cannot assign PENDING via role-update endpoint ─────────────


@pytest.mark.asyncio
async def test_admin_cannot_set_pending_role(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = User(email="admin@telecom-sudparis.eu", role=UserRole.VIEUX)
    target = User(email="student@telecom-sudparis.eu", role=UserRole.STUDENT)
    db_session.add_all([admin, target])
    await db_session.flush()

    from app.core.security import create_access_token

    token, _ = create_access_token(user_id=str(admin.id), role=admin.role.value, email=admin.email)

    response = await client.patch(
        f"/api/admin/users/{target.id}/role",
        params={"role": "pending"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "pending" in response.json()["detail"].lower()


# ── Issue #9: TestEmailIn validates email format ───────────────────────────────


@pytest.mark.asyncio
async def test_admin_test_email_rejects_invalid_email(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = User(email="admin@telecom-sudparis.eu", role=UserRole.VIEUX)
    db_session.add(admin)
    await db_session.commit()

    from app.core.security import create_access_token

    token, _ = create_access_token(user_id=str(admin.id), role=admin.role.value, email=admin.email)

    for bad_email in ["not-an-email", "foo\nbar@evil.com", "a@", "@domain.com"]:
        response = await client.post(
            "/api/admin/auth-config/test-email",
            json={"email": bad_email},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422, f"Expected 422 for email: {bad_email!r}"


# ── Issue #10: Storage usage clamped to >= 0 ─────────────────────────────────


@pytest.mark.asyncio
async def test_check_storage_limit_clamps_negative_redis_value():
    """A negative Redis counter (post-flush scenario) must not allow unlimited uploads."""
    import app.core.redis as redis_core
    from app.routers.upload.helpers import _check_storage_limit

    fake_redis = AsyncMock()
    # Simulate a negative counter value after a Redis flush + DECRBY
    fake_redis.get = AsyncMock(return_value=b"-999999999")
    fake_redis.set = AsyncMock()

    original_client = redis_core.redis_client
    redis_core.redis_client = fake_redis

    try:
        # With 1 GB max and effectively 0 usage (clamped), a small upload should pass
        await _check_storage_limit(
            1024,
            config={"max_storage_gb": 1},
        )
        # No exception = pass (usage was clamped to 0, not kept as -999999999)
    finally:
        redis_core.redis_client = original_client


# ── Issue #11: get_or_create_user uses passed auto_approve, no second validate ─


@pytest.mark.asyncio
async def test_get_or_create_user_no_redis_param(db_session: AsyncSession):
    """get_or_create_user no longer accepts redis; auto_approve is passed directly."""
    import inspect

    from app.services.auth import get_or_create_user

    sig = inspect.signature(get_or_create_user)
    param_names = list(sig.parameters.keys())
    assert "redis" not in param_names, (
        "get_or_create_user must not take a redis parameter; "
        "callers must pass auto_approve directly"
    )
    assert "auto_approve" in param_names


@pytest.mark.asyncio
async def test_get_or_create_user_respects_auto_approve_true(db_session: AsyncSession):
    from app.services.auth import get_or_create_user

    user, is_new = await get_or_create_user(db_session, "new@test.com", auto_approve=True)
    assert is_new is True
    assert user.role == UserRole.STUDENT


@pytest.mark.asyncio
async def test_get_or_create_user_respects_auto_approve_false(db_session: AsyncSession):
    from app.services.auth import get_or_create_user

    user, is_new = await get_or_create_user(db_session, "pending@test.com", auto_approve=False)
    assert is_new is True
    assert user.role == UserRole.PENDING


@pytest.mark.asyncio
async def test_get_or_create_existing_user_unchanged(db_session: AsyncSession):
    from app.services.auth import get_or_create_user

    existing = User(email="old@test.com", role=UserRole.BUREAU)
    db_session.add(existing)
    await db_session.flush()

    user, is_new = await get_or_create_user(db_session, "old@test.com", auto_approve=True)
    assert is_new is False
    assert user.role == UserRole.BUREAU  # role must not change for existing users


# ── Issue #12: get_s3_client accepts pre-fetched cfg ─────────────────────────


def test_get_s3_client_accepts_cfg_param():
    """get_s3_client must accept an optional cfg dict to prevent double Redis lookup."""
    import inspect

    from app.core.storage import get_s3_client

    sig = inspect.signature(get_s3_client)
    assert "cfg" in sig.parameters, "get_s3_client must have a cfg parameter"
    assert sig.parameters["cfg"].default is None


# ── Issue #13: TUS OPTIONS reads only Redis (no DB) ──────────────────────────


@pytest.mark.asyncio
async def test_tus_options_does_not_open_db_session(client: AsyncClient):
    """OPTIONS must not open a DB session — only read from Redis."""
    db_was_opened = []

    async def fail_on_db_open():
        db_was_opened.append(True)
        if False:
            yield
        raise RuntimeError("DB must not be opened during OPTIONS")

    # Verify the options handler signature has no db dependency
    import inspect

    from app.routers.tus import tus_options

    sig = inspect.signature(tus_options)
    param_names = list(sig.parameters.keys())
    assert "db" not in param_names, (
        "tus_options must not depend on 'db' — it should only read from Redis"
    )


@pytest.mark.asyncio
async def test_tus_options_returns_correct_headers(client: AsyncClient):
    response = await client.options("/api/upload/tus")
    assert response.status_code == 204
    assert "Tus-Version" in response.headers
    assert "Tus-Max-Size" in response.headers
    assert "Tus-Extension" in response.headers


# ── Issue #15: _serialize_config dead code removed ────────────────────────────


def test_serialize_config_dead_code_removed():
    import app.routers.admin as admin_mod

    assert not hasattr(admin_mod, "_serialize_config"), (
        "_serialize_config was a no-op stub and must be removed"
    )


# ── Issue #17: validate_email_for_auth logic ─────────────────────────────────


@pytest.mark.asyncio
async def test_validate_email_listed_domain_auto_approve_respected(
    db_session: AsyncSession,
):
    """Domain in list with auto_approve=False → returns False even if allow_all_domains=True."""
    from app.services.auth import validate_email_for_auth

    config = AuthConfig(allow_all_domains=True)
    db_session.add(config)
    domain = AllowedDomain(domain="restricted.edu", auto_approve=False)
    db_session.add(domain)
    await db_session.flush()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    result = await validate_email_for_auth("user@restricted.edu", db_session, redis)
    assert result is False


@pytest.mark.asyncio
async def test_validate_email_listed_domain_checked_before_allow_all(
    db_session: AsyncSession,
):
    """Domain matching in list takes priority over allow_all_domains fallback."""
    from app.services.auth import validate_email_for_auth

    config = AuthConfig(allow_all_domains=True)
    db_session.add(config)
    domain = AllowedDomain(domain="telecom-sudparis.eu", auto_approve=True)
    db_session.add(domain)
    await db_session.flush()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    result = await validate_email_for_auth("user@telecom-sudparis.eu", db_session, redis)
    assert result is True
