import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.process_upload_post_scan import process_upload_post_scan


@pytest.mark.asyncio
async def test_post_scan_preserves_immutable_cas_metadata_in_db_and_cache():
    """Derived processing must not replace authoritative CAS hash/size metadata."""
    # Setup mocks
    mock_redis = AsyncMock()
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_upload = MagicMock(status="clean", size_bytes=10000, content_sha256="cas-sha")
    mock_db.scalar = AsyncMock(return_value=mock_upload)

    ctx = {"redis": mock_redis, "db_sessionmaker": MagicMock(return_value=mock_db), "job_try": 1}

    # Mock ProcessingFile with a specific size
    sanitized_size = 5000
    mock_pf = MagicMock()
    mock_pf.size = sanitized_size
    mock_pf.path = "/tmp/fake.pdf"
    mock_pf.sha256 = AsyncMock(return_value="sha256")
    mock_pf.cleanup = MagicMock()

    # Mock download result
    dr = MagicMock()
    dr.pf = mock_pf
    dr.original_sha256 = "sha256"
    dr.initial_size = 10000
    dr.actual_mime = "application/pdf"
    dr.mime_category = "document"
    dr.cas_key = "cas_key"

    # Mock existing Redis status
    initial_payload = {"status": "clean", "result": {"size": 10000, "processing_status": "pending"}}
    mock_redis.get = AsyncMock(return_value=json.dumps(initial_payload))
    mock_redis.rpush = AsyncMock(return_value=1)
    mock_redis.publish = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_strip_only",
            AsyncMock(),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage", AsyncMock(return_value=None)
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("app.workers.process_upload_post_scan.notify_user", AsyncMock()),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.update_processing_status = AsyncMock()
        repo_instance.get_auth_config = AsyncMock(return_value={})
        repo_instance.maybe_dispatch_webhook = AsyncMock()

        await process_upload_post_scan(
            ctx,
            upload_id="upload_id",
            user_id="user_id",
            quarantine_key="quarantine_key",
            original_filename="file.pdf",
            mime_type="application/pdf",
            original_sha256="sha256",
            cas_key="cas_key",
            cas_s3_key="cas/s3_key",
            initial_size=10000,
        )

        # Verify the conditionally locked DB row was updated.
        assert mock_upload.size_bytes == 10000
        assert mock_upload.content_sha256 == "cas-sha"
        assert mock_upload.processing_status == "complete"

        # Verify Redis cache update
        # emit_event calls rpush and publish
        assert mock_redis.publish.called
        published_payload = json.loads(mock_redis.publish.call_args[0][1])
        assert published_payload["result"]["size"] == 10000
        assert published_payload["result"]["processing_status"] == "complete"


@pytest.mark.asyncio
async def test_exec_create_material_fetches_db_size():
    """Directly test _exec_create_material uses Upload table size."""
    from app.services.pr import _exec_create_material

    mock_db = AsyncMock()
    mock_db.info = {}

    directory_id = uuid.uuid4()
    payload = {
        "op": "create_material",
        "title": "Test",
        "type": "document",
        "directory_id": str(directory_id),
        "file_key": "cas/abc",
        "file_size": 10000,  # Old size
    }

    sanitized_size = 5555

    # The same result shape supports the tag query and the Upload metadata query.
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.one_or_none.return_value = (sanitized_size, None)
    mock_db.execute = AsyncMock(return_value=result)

    with (
        patch("app.services.pr.Material", return_value=MagicMock(id=uuid.uuid4())) as mock_mat,
        patch("app.services.pr.MaterialVersion") as mock_ver,
        patch("app.services.pr._unique_material_slug", AsyncMock(return_value="slug")),
        patch("app.services.pr.Tag", MagicMock()),
        patch("app.services.pr.select", MagicMock()),
        patch("app.core.security.cas.increment_cas_ref", AsyncMock()),
    ):
        await _exec_create_material(mock_db, payload, MagicMock(), id_map={})

        # Verify MaterialVersion was created with the authoritative upload size.
        _, kwargs = mock_ver.call_args
        assert kwargs["file_size"] == sanitized_size
