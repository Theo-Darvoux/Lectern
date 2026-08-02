from unittest.mock import AsyncMock

import pytest

from app.core.security.cas import hmac_cas_key
from app.routers.upload import check_file_exists
from app.schemas.material import CheckExistsRequest


def test_hmac_cas_key_is_deterministic():
    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    key1 = hmac_cas_key(sha256)
    key2 = hmac_cas_key(sha256)
    assert key1 == key2
    assert key1.startswith("upload:cas:")


def test_hmac_cas_key_varies_by_hash():
    sha1 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    sha2 = "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    assert hmac_cas_key(sha1) != hmac_cas_key(sha2)


@pytest.mark.asyncio
async def test_check_exists_uses_hmac_key():
    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    data = CheckExistsRequest(sha256=sha256, size=100)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    # Expected key using HKDF-derived signing key (matches cas.py implementation)
    expected_key = hmac_cas_key(sha256)

    from app.models.user import User

    mock_user = User(id="user-123", email="test@example.com")

    await check_file_exists(data, mock_user, mock_redis)

    # Verify redis.get was called with the HKDF-derived CAS key
    mock_redis.get.assert_any_call(expected_key)

    # Ensure that the call to the GLOBAL CAS key did NOT contain the raw sha256 in the digest part
    # (The per-user cache key 'upload:sha256:...' DOES contain it, and that's fine for per-user isolation)
    global_cas_calls = [c for c in mock_redis.get.call_args_list if "upload:cas:" in str(c)]
    for call in global_cas_calls:
        assert sha256 not in str(call).split("upload:cas:")[-1]
