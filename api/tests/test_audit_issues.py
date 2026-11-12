"""Tests that reproduce every issue found in the upload-flow security audit.

Each test is named after its audit ID and is written to FAIL when the bug
exists.  Once the corresponding fix is applied the test should turn green.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole

# ── Helpers ──────────────────────────────────────────────────────────────────


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
    await db.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH #1 — Batch status IDOR via substring ownership check
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_high1_status_idor_via_substring(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
):
    """The upload status route must NOT leak statuses across users when one
    user_id is a substring of another.

    User A (id=abc) must NOT be able to query files belonging to User B
    (id=xyzabcdef) just because 'abc' is in 'xyzabcdef'.
    """
    user_a = await _create_user(db_session)
    user_b = await _create_user(db_session)
    headers_a = _auth_headers(user_a)

    # user_b's file key contains user_b's ID
    victim_key = f"quarantine/{user_b.id}/somefile/test.pdf"

    # Ensure user_a's ID is NOT a prefix of user_b's key
    # but could be a substring match (the old bug)
    # We test the ownership logic directly.

    resp = await client.get(f"/api/upload/status/{victim_key}", headers=headers_a)

    # User A must NOT see user B's file status
    assert resp.status_code == 403, f"IDOR: user {user_a.id} can see file belonging to {user_b.id}."


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH #2 — SVG detection in MIME sniffer is too loose
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_high2_svg_detection_rejects_non_svg_with_svg_substring():
    """A text file mentioning '<svg' deep in the content must NOT be
    detected as image/svg+xml.
    """
    from app.core.media.mimetypes import guess_mime_from_bytes

    # A plain text file that happens to discuss SVGs
    text_about_svg = b"This is a text file.\nHere we discuss <svg elements in HTML.\n"

    mime = guess_mime_from_bytes(text_about_svg)
    assert mime != "image/svg+xml", (
        "Plain text containing '<svg' substring was misidentified as SVG. "
        "SVG detection should only match when <svg is at/near the document start."
    )


@pytest.mark.asyncio
async def test_high2_svg_detection_accepts_real_svg():
    """A real SVG file must still be correctly detected."""
    from app.core.media.mimetypes import guess_mime_from_bytes

    real_svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert guess_mime_from_bytes(real_svg) == "image/svg+xml"

    # Also test bare <svg at start
    bare_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
    assert guess_mime_from_bytes(bare_svg) == "image/svg+xml"


# ═══════════════════════════════════════════════════════════════════════════════
# LOW #1 — application/octet-stream bypasses per-type size limits
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_low1_octet_stream_still_has_size_limit():
    """Files with MIME type application/octet-stream must still be
    subject to the global size limit via _check_per_type_size.
    """
    from app.config import settings
    from app.routers.upload.validators import _check_per_type_size

    # This should not raise — it's under the global limit
    _check_per_type_size("application/octet-stream", 1024)

    # This should raise — over the global limit
    from app.core.common.exceptions import BadRequestError

    over_limit = (settings.max_file_size_mb * 1024 * 1024) + 1
    with pytest.raises(BadRequestError):
        _check_per_type_size("application/octet-stream", over_limit)
