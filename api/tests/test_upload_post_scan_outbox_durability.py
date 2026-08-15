import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxJob
from app.models.upload import Upload
from app.workers.upload.context import WorkerContext
from app.workers.upload.repository import UploadWorkerRepository, _post_scan_outbox_id


@pytest.mark.asyncio
async def test_clean_publication_persists_post_scan_intent_atomically(
    db_session: AsyncSession, mock_redis
) -> None:
    import app.core.database.database as database

    upload_id = str(uuid.uuid4())
    user_id = uuid.uuid4()
    db_session.add(
        Upload(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=f"quarantine/{user_id}/{upload_id}/file.pdf",
            status="processing",
            filename="file.pdf",
        )
    )
    await db_session.commit()

    repo = UploadWorkerRepository(
        WorkerContext(redis=mock_redis, db_sessionmaker=database.async_session_factory)
    )
    post_scan_kwargs = {
        "upload_id": upload_id,
        "user_id": str(user_id),
        "quarantine_key": f"quarantine/{user_id}/{upload_id}/file.pdf",
        "original_filename": "file.pdf",
        "mime_type": "application/pdf",
        "original_sha256": "a" * 64,
        "cas_key": "upload:cas:" + "b" * 64,
        "cas_s3_key": "cas/" + "b" * 64,
        "initial_size": 123,
    }

    assert await repo.publish_clean_upload(
        upload_id,
        sha256="a" * 64,
        content_sha256="c" * 64,
        final_key="cas/" + "b" * 64,
        cas_key="upload:cas:" + "b" * 64,
        cas_ref_count=1,
        post_scan_kwargs=post_scan_kwargs,
    )

    outbox = await db_session.scalar(
        select(OutboxJob).where(OutboxJob.id == _post_scan_outbox_id(upload_id))
    )
    assert outbox is not None
    assert outbox.job_name == "process_upload_post_scan"
    assert outbox.delivered_at is None
    assert outbox.args[-1]["__outbox_kwargs__"] == post_scan_kwargs

    # Retrying after an ambiguous outcome reuses the deterministic row instead of
    # creating a second continuation.
    assert await repo.publish_clean_upload(
        upload_id,
        sha256="a" * 64,
        content_sha256="c" * 64,
        final_key="cas/" + "b" * 64,
        cas_key="upload:cas:" + "b" * 64,
        cas_ref_count=1,
        post_scan_kwargs=post_scan_kwargs,
    )
    rows = list(
        (
            await db_session.scalars(
                select(OutboxJob).where(OutboxJob.job_name == "process_upload_post_scan")
            )
        ).all()
    )
    assert len(rows) == 1
