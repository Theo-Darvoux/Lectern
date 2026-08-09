import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.pr as pr_service
from app.core.common.exceptions import BadRequestError, ConflictError
from app.core.database.post_commit import PostCommitKey
from app.models.upload import Upload


@pytest.mark.asyncio
async def test_legacy_oversized_object_cannot_be_promoted(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    """An oversize legacy uploads/ object must be rejected before copy_object."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_text_size_mb", 1)

    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    copy = AsyncMock()
    monkeypatch.setattr(pr_service, "copy_object", copy)
    monkeypatch.setattr(
        pr_service,
        "get_object_info",
        AsyncMock(
            return_value={
                "size": 2 * 1024 * 1024,
                "content_type": "text/plain",
            }
        ),
    )

    with pytest.raises(BadRequestError):
        await pr_service._make_version_for_file(
            db_session,
            file_key="uploads/user/upload/test.txt",
            payload={
                "file_name": "test.txt",
                "file_mime_type": "text/plain",
            },
            material_id=uuid.uuid4(),
            version_number=1,
            author_id=uuid.uuid4(),
            pr_id=uuid.uuid4(),
        )

    copy.assert_not_awaited()


@pytest.mark.asyncio
async def test_cas_promotion_uses_authoritative_upload_mime_for_size_policy(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    """CAS promotion must use stored MIME, not attacker-supplied payload MIME.

    Setup:
      - Upload DB row: mime_type=text/plain, size=2 MiB
      - PR payload lies: file_mime_type=video/mp4
      - Config: text max=1 MiB, video max=500 MiB

    With authoritative MIME (text/plain), the 2 MiB file exceeds the
    1 MiB text limit and must be rejected.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "max_text_size_mb", 1)
    monkeypatch.setattr(settings, "max_video_size_mb", 500)

    author_id = uuid.uuid4()
    upload_id = str(uuid.uuid4())
    cas_key = f"cas/{uuid.uuid4().hex}"

    # Create a clean Upload row with authoritative metadata
    upload_row = Upload(
        upload_id=upload_id,
        user_id=author_id,
        final_key=cas_key,
        status="clean",
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=2 * 1024 * 1024,
        content_sha256="abc123",
        cas_ref_count=1,
    )
    db_session.add(upload_row)
    await db_session.flush()

    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    with pytest.raises(BadRequestError):
        await pr_service._make_version_for_file(
            db_session,
            file_key=cas_key,
            payload={
                "file_name": "test.txt",
                "file_mime_type": "video/mp4",
                "file_size": 2 * 1024 * 1024,
            },
            material_id=uuid.uuid4(),
            version_number=1,
            author_id=author_id,
            pr_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_cas_promotion_fails_closed_on_missing_upload_row(
    db_session: AsyncSession,
) -> None:
    """If no verified upload row exists for a CAS key, promotion must fail closed."""
    author_id = uuid.uuid4()
    cas_key = f"cas/{uuid.uuid4().hex}"
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    with pytest.raises(ConflictError):
        await pr_service._make_version_for_file(
            db_session,
            file_key=cas_key,
            payload={
                "file_name": "test.txt",
                "file_mime_type": "text/plain",
                "file_size": 100,
            },
            material_id=uuid.uuid4(),
            version_number=1,
            author_id=author_id,
            pr_id=uuid.uuid4(),
        )
