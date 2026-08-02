"""Regression tests for the upload-flow security audit (2026-04).

Each test reproduces a specific finding from the audit report.
The test FAILS while the bug exists and PASSES once it is fixed.
"""

import inspect
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security.security import create_access_token
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
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# CRITICAL-1: MAGIC_HEADER_SIZE NameError in tus.py
# ═══════════════════════════════════════════════════════════════════════════════


class TestCritical1TusMagicHeaderImport:
    """tus.py uses MAGIC_HEADER_SIZE but never imports it from app.core.constants."""

    def test_magic_header_size_is_importable_from_tus_module(self):
        """The MIME sniffing code inside tus_patch references MAGIC_HEADER_SIZE.
        If it is not in the module's namespace, tus PATCH will crash at runtime."""
        import app.routers.tus as tus_mod

        # The constant must be reachable in the module's global scope
        source = inspect.getsource(tus_mod)
        assert "MAGIC_HEADER_SIZE" in source, "tus.py must reference MAGIC_HEADER_SIZE"

        # And it must be actually importable (not just referenced in dead code)
        assert (
            hasattr(tus_mod, "MAGIC_HEADER_SIZE")
            or "MAGIC_HEADER_SIZE" in dir(tus_mod)
            or "MAGIC_HEADER_SIZE" in {name for name, _ in inspect.getmembers(tus_mod)}
            or _name_in_module_globals(tus_mod, "MAGIC_HEADER_SIZE")
        ), (
            "MAGIC_HEADER_SIZE is referenced in tus.py but not imported — will cause NameError at runtime"
        )


def _name_in_module_globals(mod, name: str) -> bool:
    """Check if a name is accessible in a module's global namespace."""
    try:
        # Attempt to evaluate the name in the module's global context
        return name in vars(mod)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# CRITICAL-2: Presigned multipart complete uses client-declared size
# ═══════════════════════════════════════════════════════════════════════════════


class TestCritical2MultipartSizeValidation:
    """presigned_multipart_complete validates size against intent['size'] (client
    declared) instead of actual S3 object size after completion."""

    async def test_multipart_complete_validates_actual_s3_size(
        self, client: AsyncClient, db_session: AsyncSession, mock_redis, fake_redis_setup
    ):
        """If the client declares size=1MB at init but uploads 201MB, the
        complete endpoint must reject based on ACTUAL S3 size, not declared."""
        user = await _create_user(db_session)
        await db_session.commit()
        headers = _auth_headers(user)
        user_id = str(user.id)

        # Plant an intent in Redis with a small declared size
        upload_id = str(uuid.uuid4())
        quarantine_key = f"quarantine/{user_id}/{upload_id}/test.pdf"
        intent = {
            "user_id": user_id,
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "s3_multipart_id": "fake_mp_id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size": 1 * 1024 * 1024,  # Client LIED: declared 1 MB
        }
        intent_key = f"upload:intent:{upload_id}"
        await mock_redis.set(intent_key, json.dumps(intent))

        # The ACTUAL object in S3 is 201 MB (over the 200 MiB PDF per-type limit)
        actual_s3_size = 201 * 1024 * 1024

        with (
            patch("app.routers.upload.presigned.complete_multipart_upload", new_callable=AsyncMock),
            patch("app.routers.upload.presigned.get_object_info", new_callable=AsyncMock) as m_info,
            patch("app.core.storage.facade.read_object_bytes", new_callable=AsyncMock) as m_read,
            patch("app.core.database.redis.arq_pool", new_callable=AsyncMock) as m_arq,
        ):
            m_read.return_value = b"%PDF-1.4 fake"
            # This is the actual size in S3 — much bigger than declared
            m_info.return_value = {"size": actual_s3_size, "content_type": "application/pdf"}
            m_arq.enqueue_job = AsyncMock()

            resp = await client.post(
                "/api/upload/presigned-multipart/complete",
                json={
                    "upload_id": upload_id,
                    "parts": [{"PartNumber": 1, "ETag": '"etag1"'}],
                },
                headers=headers,
            )

            # The endpoint MUST reject this because actual size (201 MB) exceeds
            # the per-type limit for PDFs.  If it uses intent["size"] (1 MB),
            # it will wrongly accept.
            assert resp.status_code == 400, (
                f"Expected 400 (size limit exceeded) but got {resp.status_code}. "
                "The endpoint is using client-declared size instead of actual S3 size."
            )


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-1: Presigned multipart race condition (non-atomic intent deletion)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHigh1MultipartAtomicIntent:
    """Multipart completion must serialize work without consuming retry state early."""

    def test_multipart_complete_uses_atomic_intent_consumption(self):
        """A renewable lock prevents races and intent deletion follows enqueue."""
        import app.routers.upload.presigned as presigned_mod

        source = inspect.getsource(presigned_mod.presigned_multipart_complete)
        assert "redis_lock" in source
        assert "GETDEL" not in source
        assert source.index("_enqueue_processing") < source.index("redis.delete(intent_key)")


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-2: Frontend multipart no abort cleanup on failure
# ═══════════════════════════════════════════════════════════════════════════════


class TestHigh2FrontendMultipartAbortCleanup:
    """_presignedMultipartUpload in upload-client.ts has no try/catch that
    calls the abort endpoint when upload fails."""

    def test_multipart_upload_has_abort_on_failure(self):
        """The frontend multipart upload function must call the abort endpoint
        on failure to prevent orphaned S3 multipart uploads."""
        upload_client_path = (
            Path(__file__).parent.parent.parent / "web" / "src" / "lib" / "upload-client.ts"
        )
        if not upload_client_path.exists():
            pytest.skip("Frontend code not available in this test environment")

        source = upload_client_path.read_text()

        # Find the _presignedMultipartUpload function
        fn_start = source.find("async function _presignedMultipartUpload")
        assert fn_start != -1, "Cannot find _presignedMultipartUpload function"

        # Extract roughly the function body (find next top-level function)
        fn_body = source[fn_start : fn_start + 5000]

        # Must have a catch/finally that calls the abort endpoint
        has_abort_cleanup = (
            "presigned-multipart" in fn_body
            and ("catch" in fn_body or "finally" in fn_body)
            and "DELETE" in fn_body
        )

        assert has_abort_cleanup, (
            "_presignedMultipartUpload does not clean up on failure. "
            "Failed multipart uploads leave orphaned S3 parts until the 24h cleanup cron."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-3: MalwareBazaar fail-soft default
# ═══════════════════════════════════════════════════════════════════════════════


class TestHigh3MalwareBazaarFailClosed:
    """malwarebazaar_fail_closed defaults to False, allowing uploads when
    MalwareBazaar is unreachable."""

    def test_malwarebazaar_default_is_fail_closed(self):
        """The default setting should be fail-closed (True) to prevent
        known malware from bypassing the scan during MalwareBazaar outages."""
        assert settings.malwarebazaar_fail_closed is True, (
            f"malwarebazaar_fail_closed defaults to {settings.malwarebazaar_fail_closed}. "
            "Should default to True (fail-closed) to prevent bypassing the scan "
            "during MalwareBazaar outages."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIUM-1: download_file_with_hash hashes on event loop
# ═══════════════════════════════════════════════════════════════════════════════


class TestMedium1HashingOffEventLoop:
    """download_file_with_hash delegates disk write to asyncio.to_thread
    but runs hasher.update() on the event loop, blocking it."""

    def test_hash_update_runs_in_thread(self):
        """hasher.update() should run inside asyncio.to_thread alongside f.write(),
        not on the main event loop."""
        import app.core.storage.facade as storage_mod

        source = inspect.getsource(storage_mod.download_file_with_hash)

        # The fix: both write and hash should be inside to_thread
        # Bad pattern: "await asyncio.to_thread(f.write, chunk)\n            hasher.update(chunk)"
        # Good pattern: a combined function inside to_thread
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "hasher.update" in line:
                # Check that hasher.update is NOT on its own line outside to_thread
                stripped = line.strip()
                if stripped.startswith("hasher.update"):
                    # It's a standalone call — check if the preceding line is to_thread
                    if i > 0 and "to_thread" in lines[i - 1]:
                        pytest.fail(
                            "hasher.update(chunk) runs on the event loop after "
                            "asyncio.to_thread(f.write, chunk). Both should be batched "
                            "inside a single to_thread call to avoid blocking."
                        )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIUM-2: SSE progress stuck at 99%
# ═══════════════════════════════════════════════════════════════════════════════


class TestMedium2ProgressReaches100:
    """uploadFile() never explicitly calls onProgress(100) after SSE completion."""

    def test_upload_file_calls_progress_100(self):
        """After _waitForUploadCompletion returns, onProgress(100) must be called."""
        upload_client_path = (
            Path(__file__).parent.parent.parent / "web" / "src" / "lib" / "upload-client.ts"
        )
        if not upload_client_path.exists():
            pytest.skip("Frontend code not available")

        source = upload_client_path.read_text()

        # Find the section after _waitForUploadCompletion in the uploadFile function
        fn_start = source.find("export async function uploadFile")
        assert fn_start != -1
        fn_body = source[fn_start:]

        # After _waitForUploadCompletion, there should be an explicit onProgress(100)
        wait_pos = fn_body.find("_waitForUploadCompletion")
        assert wait_pos != -1
        after_wait = fn_body[wait_pos:]

        # Look for onProgress?.(100) before the return statement
        return_pos = after_wait.find("return {")
        assert return_pos != -1
        between = after_wait[:return_pos]

        assert "onProgress?.(100)" in between or "onProgress!(100)" in between, (
            "uploadFile() does not call onProgress(100) after _waitForUploadCompletion. "
            "The progress bar gets stuck at 99% because 80 + Math.round(1.0 * 19) = 99."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIUM-3: No client-side SSE total timeout
# ═══════════════════════════════════════════════════════════════════════════════


class TestMedium3ClientSseTimeout:
    """_waitForUploadCompletion has no total timeout — if the server keeps
    sending keepalive pings, the client waits indefinitely."""

    def test_sse_client_has_total_timeout(self):
        """_waitForUploadCompletion must have a total deadline to prevent
        waiting forever when the worker crashes but SSE pings continue."""
        upload_client_path = (
            Path(__file__).parent.parent.parent / "web" / "src" / "lib" / "upload-client.ts"
        )
        if not upload_client_path.exists():
            pytest.skip("Frontend code not available")

        source = upload_client_path.read_text()

        fn_start = source.find("async function _waitForUploadCompletion")
        assert fn_start != -1
        fn_body = source[fn_start : fn_start + 4000]

        # Should have a total deadline/timeout mechanism
        has_total_timeout = any(
            kw in fn_body
            for kw in [
                "TOTAL_TIMEOUT",
                "totalTimeout",
                "deadline",
                "Date.now()",
                "total_deadline",
                "MAX_WAIT",
            ]
        )

        assert has_total_timeout, (
            "_waitForUploadCompletion has no total timeout. If the server keeps "
            "sending keepalive pings but the worker is dead, the client waits "
            "for the full 10-minute SSE timeout with no feedback."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIUM-4: Upload queue persists 'uploading' state across sessions
# ═══════════════════════════════════════════════════════════════════════════════


class TestMedium4UploadQueueRehydration:
    """Zustand upload queue persists 'uploading' items to localStorage without
    resetting them on rehydration."""

    def test_upload_queue_handles_stale_uploading_on_rehydrate(self):
        """On store rehydration, items with status 'uploading' should be
        transitioned to 'paused' (if tusUrl exists) or 'error'."""
        queue_path = Path(__file__).parent.parent.parent / "web" / "src" / "lib" / "upload-queue.ts"
        if not queue_path.exists():
            pytest.skip("Frontend code not available")

        source = queue_path.read_text()

        has_rehydration_handler = "onRehydrateStorage" in source or "onRehydrate" in source

        assert has_rehydration_handler, (
            "Upload queue store has no onRehydrateStorage handler. "
            "Items stuck in 'uploading' state after page reload will show as "
            "in-progress forever with no way to resume or cancel."
        )
