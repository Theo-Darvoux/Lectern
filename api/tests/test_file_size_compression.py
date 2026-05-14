import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.process_upload_post_scan import process_upload_post_scan


@pytest.mark.asyncio
async def test_post_scan_updates_size_in_db_and_cache():
    """Test that process_upload_post_scan updates Upload.size_bytes and Redis cache."""
    # Setup mocks
    mock_redis = AsyncMock()
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    ctx = {
        "redis": mock_redis,
        "db_sessionmaker": MagicMock(return_value=mock_db),
        "job_try": 1
    }

    # Mock ProcessingFile with a specific size
    compressed_size = 5000
    mock_pf = MagicMock()
    mock_pf.size = compressed_size
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
    initial_payload = {
        "status": "clean",
        "result": {
            "size": 10000,
            "processing_status": "pending"
        }
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(initial_payload))
    mock_redis.rpush = AsyncMock(return_value=1)
    mock_redis.publish = AsyncMock()

    with (
        patch("app.workers.process_upload_post_scan.run_download_and_validate", AsyncMock(return_value=dr)),
        patch("app.workers.process_upload_post_scan.run_compress_stage", AsyncMock(return_value=MagicMock(final_mime="application/pdf", content_encoding=None))),
        patch("app.workers.process_upload_post_scan.run_thumbnail_stage", AsyncMock(return_value=None)),
        patch("app.workers.process_upload_post_scan.upload_file_multipart", AsyncMock()),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("app.workers.process_upload_post_scan.notify_user", AsyncMock()),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.update_upload_status = AsyncMock()
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
            initial_size=10000
        )

        # Verify DB update
        args, kwargs = repo_instance.update_upload_status.call_args
        assert kwargs["size_bytes"] == compressed_size
        assert kwargs["processing_status"] == "complete"

        # Verify Redis cache update
        # emit_event calls rpush and publish
        assert mock_redis.publish.called
        published_payload = json.loads(mock_redis.publish.call_args[0][1])
        assert published_payload["result"]["size"] == compressed_size
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
        "file_size": 10000  # Old size
    }

    compressed_size = 5555

    # Mock scalar to return the compressed size when querying Upload
    mock_db.scalar = AsyncMock(return_value=compressed_size)

    # Mock material creation
    mock_db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

    with (
        patch("app.services.pr.Material", return_value=MagicMock(id=uuid.uuid4())) as mock_mat,
        patch("app.services.pr.MaterialVersion") as mock_ver,
        patch("app.services.pr._unique_material_slug", AsyncMock(return_value="slug")),
        patch("app.services.pr.Tag", MagicMock()),
        patch("app.services.pr.select", MagicMock()),
        patch("app.core.cas.increment_cas_ref", AsyncMock()),
    ):
        await _exec_create_material(mock_db, payload, MagicMock(), id_map={})

        # Verify MaterialVersion was created with the compressed size
        args, kwargs = mock_ver.call_args
        assert kwargs["file_size"] == compressed_size
