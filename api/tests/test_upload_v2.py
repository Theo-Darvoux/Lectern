"""Tests for V2 upload pipeline improvements.

Covers:
- Scanner ARQ context caching
- Structured progress events (stage_index / overall_percent)
- Upload cancellation endpoint (DELETE /api/upload/{upload_id})
- Per-user SHA-256 dedup check (POST /api/upload/check-exists)
- Priority queue routing (fast / slow queues)
- PDF dangerous-construct safety check
- SVG DOM-based sanitisation
"""

import asyncio
import io
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pikepdf
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.file_security import SvgSecurityError, check_pdf_safety, check_svg_safety
from app.core.security.scanner import MalwareScanner
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.routers.upload import (
    _FAST_QUEUE_NAME,
    _FAST_QUEUE_THRESHOLD,
    _SLOW_QUEUE_NAME,
)
from app.schemas.material import UploadStatus, UploadStatusOut
from app.workers.process_upload import _STAGES, _overall

# ── Shared helpers ────────────────────────────────────────────────────────────


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


def _make_pdf_file(content: bytes = b"%PDF-1.4 test") -> dict:
    return {"file": ("test.pdf", io.BytesIO(content), "application/pdf")}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Structured progress — schema & math
# ─────────────────────────────────────────────────────────────────────────────


def test_stage_weights_sum_to_one() -> None:
    """Stage weights must sum to exactly 1.0 (within floating-point tolerance)."""
    total = sum(w for _, _, w in _STAGES)
    assert abs(total - 1.0) < 1e-9


def test_overall_progress_boundaries() -> None:
    """overall_percent at stage start/end must be consistent with weight layout."""
    accumulated = 0.0
    for i, (_, _, weight) in enumerate(_STAGES):
        assert abs(_overall(i, 0.0) - accumulated) < 1e-6, f"stage {i} start mismatch"
        accumulated += weight
        assert abs(_overall(i, 1.0) - accumulated) < 1e-6, f"stage {i} end mismatch"

    assert abs(accumulated - 1.0) < 1e-9, "final stage end must reach 1.0"


def test_upload_status_out_accepts_structured_fields() -> None:
    """UploadStatusOut must accept and round-trip the new V2 progress fields."""
    status = UploadStatusOut(
        file_key="quarantine/uid/123/file.pdf",
        status=UploadStatus.PROCESSING,
        detail="Scanning for malware",
        stage_index=0,
        stage_total=4,
        stage_percent=0.5,
        overall_percent=0.2,
    )
    assert status.stage_index == 0
    assert status.stage_total == 4
    assert status.overall_percent == 0.2


def test_upload_status_out_backward_compatible() -> None:
    """Older clients and workers that omit V2 fields must still deserialise correctly."""
    status = UploadStatusOut(
        file_key="uploads/uid/123/file.pdf",
        status=UploadStatus.CLEAN,
    )
    assert status.stage_index is None
    assert status.overall_percent is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Scanner ARQ context caching
# ─────────────────────────────────────────────────────────────────────────────


def test_fallback_scanner_created_when_ctx_empty() -> None:
    """_get_fallback_scanner returns an initialised MalwareScanner."""
    from app.workers.process_upload import _get_fallback_scanner

    with patch.object(MalwareScanner, "initialize") as mock_init:
        scanner = _get_fallback_scanner()
        mock_init.assert_called_once()
    assert isinstance(scanner, MalwareScanner)


@pytest.mark.asyncio
async def test_worker_startup_initialises_scanner() -> None:
    """startup() must store an initialised MalwareScanner in ctx['scanner']."""
    from app.workers.settings import startup

    ctx: dict = {}
    with (
        patch("shutil.which", return_value="/usr/bin/bwrap"),
        patch.dict("sys.modules", {"oletools": MagicMock(), "oletools.olevba": MagicMock()}),
        patch.object(MalwareScanner, "initialize"),
        patch("app.workers.settings.init_arq_pool", new_callable=AsyncMock),
    ):
        await startup(ctx)

    assert "scanner" in ctx
    assert isinstance(ctx["scanner"], MalwareScanner)


@pytest.mark.asyncio
async def test_worker_startup_initialises_arq_pool() -> None:
    """startup() must call init_arq_pool so workers can enqueue follow-up jobs."""
    from app.workers.settings import startup

    ctx: dict = {}
    mock_init_arq = AsyncMock()
    with (
        patch("shutil.which", return_value="/usr/bin/bwrap"),
        patch.dict("sys.modules", {"oletools": MagicMock(), "oletools.olevba": MagicMock()}),
        patch.object(MalwareScanner, "initialize"),
        patch("app.workers.settings.init_arq_pool", mock_init_arq),
    ):
        await startup(ctx)

    mock_init_arq.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_startup_arq_pool_failure_is_soft() -> None:
    """startup() must not raise when init_arq_pool fails (degraded mode)."""
    from app.workers.settings import startup

    ctx: dict = {}
    with (
        patch("shutil.which", return_value="/usr/bin/bwrap"),
        patch.dict("sys.modules", {"oletools": MagicMock(), "oletools.olevba": MagicMock()}),
        patch.object(MalwareScanner, "initialize"),
        patch("app.workers.settings.init_arq_pool", side_effect=ConnectionError("redis down")),
    ):
        await startup(ctx)  # must not raise

    assert "scanner" in ctx


@pytest.mark.asyncio
async def test_worker_shutdown_closes_scanner() -> None:
    """shutdown() must call scanner.close()."""
    from app.workers.settings import shutdown

    mock_scanner = AsyncMock(spec=MalwareScanner)
    ctx = {"scanner": mock_scanner}
    with patch("app.workers.settings.close_arq_pool", new_callable=AsyncMock):
        await shutdown(ctx)
    mock_scanner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_shutdown_closes_arq_pool() -> None:
    """shutdown() must call close_arq_pool to release the worker's Redis connection."""
    from app.workers.settings import shutdown

    mock_close_arq = AsyncMock()
    with patch("app.workers.settings.close_arq_pool", mock_close_arq):
        await shutdown({})

    mock_close_arq.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_shutdown_noop_without_scanner() -> None:
    """shutdown() must not raise when ctx has no scanner key."""
    from app.workers.settings import shutdown

    with patch("app.workers.settings.close_arq_pool", new_callable=AsyncMock):
        await shutdown({})  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# 3. Priority queue routing
# ─────────────────────────────────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_small_file_routed_to_fast_queue(
    mock_s3_cm: MagicMock,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_arq_pool: AsyncMock,
) -> None:
    """Files below the threshold must be enqueued on the fast queue."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    small_content = b"just text " + b"x" * 100  # well below 5 MiB
    response = await client.post(
        "/api/upload",
        files={"file": ("test.txt", io.BytesIO(small_content), "text/plain")},
        headers=_auth_headers(user),
    )
    assert response.status_code == 202

    call_kwargs = mock_arq_pool.enqueue_job.call_args.kwargs
    assert call_kwargs.get("_queue_name") == _FAST_QUEUE_NAME


@patch("app.routers.upload.direct.get_s3_client")
async def test_large_file_routed_to_slow_queue(
    mock_s3_cm: MagicMock,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_arq_pool: AsyncMock,
) -> None:
    """Files at or above the threshold must be enqueued on the slow queue."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    # Exactly at the threshold — goes to slow queue
    large_content = b"%PDF-1.4 " + b"x" * _FAST_QUEUE_THRESHOLD
    response = await client.post(
        "/api/upload",
        files=_make_pdf_file(large_content),
        headers=_auth_headers(user),
    )
    assert response.status_code == 202

    call_kwargs = mock_arq_pool.enqueue_job.call_args.kwargs
    assert call_kwargs.get("_queue_name") == _SLOW_QUEUE_NAME


def test_fast_slow_queue_names_distinct() -> None:
    """The two queue name constants must be different strings."""
    assert _FAST_QUEUE_NAME != _SLOW_QUEUE_NAME


# ─────────────────────────────────────────────────────────────────────────────
# 4. Upload cancellation endpoint
# ─────────────────────────────────────────────────────────────────────────────


async def test_cancel_upload_idempotent_when_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    """DELETE /api/upload/{id} returns 204 even when the upload_id is unknown."""
    mock_redis.zrange = AsyncMock(return_value=[])

    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.delete(
        f"/api/upload/{uuid.uuid4()}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 204


async def test_cancel_upload_removes_quarantine_key(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    """DELETE /api/upload/{id} removes the matching quarantine key from the quota set."""
    user = await _create_user(db_session)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.pdf"

    up = Upload(
        upload_id=upload_id,
        user_id=user.id,
        filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        quarantine_key=quarantine_key,
        status="pending",
    )
    db_session.add(up)
    await db_session.commit()

    # Simulate the key being present in the quota sorted set
    mock_redis.zrange = AsyncMock(return_value=[quarantine_key.encode()])

    with patch("app.routers.upload.status.delete_object", new_callable=AsyncMock) as mock_delete:
        response = await client.delete(
            f"/api/upload/{upload_id}",
            headers=_auth_headers(user),
        )

    assert response.status_code == 204
    mock_delete.assert_awaited_once_with(quarantine_key)
    mock_redis.zrem.assert_awaited_once()


async def test_concurrent_cancel_upload_releases_staging_cas_reference_once() -> None:
    """Concurrent retries share one CAS operation and cannot double-decrement."""
    from app.routers.upload.status import cancel_upload

    user_id = uuid.uuid4()
    upload_id = str(uuid.uuid4())
    staging_key = f"staging:{user_id}:{upload_id}"
    sha256 = "a" * 64

    redis = AsyncMock()
    redis.zrange = AsyncMock(return_value=[staging_key.encode()])
    redis.zrem = AsyncMock(return_value=1)

    ref_count = 2
    completed_operations: set[str] = set()

    async def eval_cas_decrement(_script: str, numkeys: int, *keys: object) -> int:
        nonlocal ref_count
        operation_key = str(keys[1]) if numkeys == 2 else str(uuid.uuid4())
        if operation_key in completed_operations:
            return ref_count
        completed_operations.add(operation_key)
        ref_count -= 1
        return ref_count

    redis.eval = AsyncMock(side_effect=eval_cas_decrement)

    row = MagicMock(
        sha256=sha256,
        content_sha256=sha256,
        cas_ref_count=1,
        quarantine_key=None,
        final_key=None,
        status="clean",
        error_detail=None,
        user_id=user_id,
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(return_value=row)
    session.commit = AsyncMock()

    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def serialized_lock(_redis, name: str, **_kwargs):
        async with locks.setdefault(name, asyncio.Lock()):
            yield

    # ``thumbnail_key`` is deliberately absent from the unrestricted MagicMock.
    # Cancellation must not mistake its synthetic child attribute for an S3 key.
    with (
        patch("app.routers.upload.status.redis_lock", serialized_lock),
        patch(
            "app.routers.upload.status._release_storage_reservation",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.status.delete_object",
            new_callable=AsyncMock,
        ) as delete_object,
    ):
        await asyncio.gather(
            cancel_upload(upload_id, MagicMock(id=user_id), redis, session),
            cancel_upload(upload_id, MagicMock(id=user_id), redis, session),
        )

    assert ref_count == 1
    assert len(completed_operations) == 1
    delete_object.assert_not_awaited()


async def test_cancel_upload_requires_auth(client: AsyncClient) -> None:
    """DELETE /api/upload/{id} must reject unauthenticated requests."""
    response = await client.delete(f"/api/upload/{uuid.uuid4()}")
    assert response.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 5. Check-exists endpoint
# ─────────────────────────────────────────────────────────────────────────────


async def test_check_exists_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    """POST /upload/check-exists returns exists=False when no match in Redis."""
    mock_redis.get = AsyncMock(return_value=None)

    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/upload/check-exists",
        json={"sha256": "a" * 64, "size": 1024},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is False
    assert data["file_key"] is None


@patch("app.core.storage.facade.object_exists", new_callable=AsyncMock, return_value=True)
async def test_check_exists_found(
    _mock_obj_exists: AsyncMock,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    """POST /upload/check-exists returns exists=True with file_key when cached."""
    cached_key = "uploads/uid/123/file.pdf"
    sha256 = "b" * 64

    user = await _create_user(db_session)
    db_session.add(
        Upload(
            upload_id=str(uuid.uuid4()),
            user_id=user.id,
            quarantine_key=f"quarantine/{user.id}/dedup/file.pdf",
            final_key=cached_key,
            filename="file.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            sha256=sha256,
            content_sha256=sha256,
            cas_ref_count=1,
            status="clean",
        )
    )
    await db_session.commit()

    # Only return the cached key for the sha256 lookup; everything else returns None
    # (a blanket override breaks JWT revocation checks that also call redis.get)
    sha256_cache_key = f"upload:sha256:{user.id}:{sha256}"

    async def selective_get(key: str) -> bytes | None:
        if key == sha256_cache_key:
            return cached_key.encode()
        return None

    mock_redis.get = AsyncMock(side_effect=selective_get)

    response = await client.post(
        "/api/upload/check-exists",
        json={"sha256": sha256, "size": 2048},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert data["file_key"] == cached_key


async def test_check_exists_requires_auth(client: AsyncClient) -> None:
    """POST /upload/check-exists must reject unauthenticated requests."""
    response = await client.post(
        "/api/upload/check-exists",
        json={"sha256": "c" * 64, "size": 512},
    )
    assert response.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 6. PDF dangerous-construct safety check
# ─────────────────────────────────────────────────────────────────────────────


def _make_pdf_with_openaction(tmp_path: Path) -> Path:
    """Create a real PDF with /OpenAction (auto-execute on open)."""
    pdf_path = tmp_path / "dangerous.pdf"
    pdf = pikepdf.Pdf.new()
    # pikepdf.pages.append() requires a pikepdf.Page object
    page_dict = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    # Inject /OpenAction into the document catalog
    js_action = pikepdf.Dictionary(
        Type=pikepdf.Name("/Action"),
        S=pikepdf.Name("/JavaScript"),
        JS=pikepdf.String("app.alert('xss')"),
    )
    pdf.Root["/OpenAction"] = js_action
    pdf.save(str(pdf_path))
    return pdf_path


def _make_clean_pdf(tmp_path: Path) -> Path:
    """Create a PDF without any dangerous constructs."""
    pdf_path = tmp_path / "clean.pdf"
    pdf = pikepdf.Pdf.new()
    page_dict = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    pdf.save(str(pdf_path))
    return pdf_path


def test_pdf_with_openaction_rejected(tmp_path: Path) -> None:
    """check_pdf_safety must raise ValueError for PDFs with a dangerous /OpenAction action dict."""
    pdf_path = _make_pdf_with_openaction(tmp_path)
    with pytest.raises(ValueError, match="dangerous /OpenAction"):
        check_pdf_safety(pdf_path)


def test_clean_pdf_passes_safety_check(tmp_path: Path) -> None:
    """check_pdf_safety must not raise for a benign PDF."""
    pdf_path = _make_clean_pdf(tmp_path)
    check_pdf_safety(pdf_path)  # must not raise


def test_pdf_safety_check_fails_closed_on_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"not a pdf at all")

    with pytest.raises(ValueError, match="malformed"):
        check_pdf_safety(p)


# ─────────────────────────────────────────────────────────────────────────────
# 7. SVG DOM-based sanitisation
# ─────────────────────────────────────────────────────────────────────────────


def test_svg_dom_rejects_script_element() -> None:
    """DOM check must reject an SVG with a <script> element."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    with pytest.raises(SvgSecurityError, match="script"):
        check_svg_safety(svg, "test.svg")


def test_svg_dom_rejects_event_handler() -> None:
    """DOM check must reject an SVG with an on* event handler attribute."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle onclick="alert(1)" r="50"/></svg>'

    with pytest.raises(SvgSecurityError, match="event handler"):
        check_svg_safety(svg, "test.svg")


def test_svg_dom_rejects_javascript_href() -> None:
    """DOM check must reject an SVG with a javascript: href."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<a href="javascript:alert(1)"><text>click</text></a>'
        b"</svg>"
    )

    with pytest.raises(SvgSecurityError, match="URI"):
        check_svg_safety(svg, "test.svg")


def test_svg_dom_rejects_foreignobject() -> None:
    """DOM check must reject a <foreignObject> element (HTML injection vector)."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<foreignObject><div>hi</div></foreignObject>"
        b"</svg>"
    )

    with pytest.raises(SvgSecurityError, match="(?i)foreignobject"):
        check_svg_safety(svg, "test.svg")


def test_svg_dom_accepts_safe_svg() -> None:
    """DOM check must pass a benign SVG without raising."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        b'<circle cx="50" cy="50" r="40" fill="blue"/>'
        b"</svg>"
    )
    check_svg_safety(svg, "safe.svg")  # must not raise


def test_svg_dom_accepts_safe_style_element() -> None:
    """Embedded CSS and local paint-server references are allowed."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        b"<style>.logo{fill:#fff}.accent{fill:url(#paint0)}</style>"
        b'<defs><linearGradient id="paint0"><stop stop-color="#00f"/></linearGradient></defs>'
        b'<rect class="logo" width="20" height="20"/>'
        b'<rect class="accent" x="20" width="20" height="20"/>'
        b"</svg>"
    )
    check_svg_safety(svg, "styled-logo.svg")


@pytest.mark.parametrize(
    "css",
    [
        '@import "https://tracker.example/style.css";',
        ".logo{fill:url(https://tracker.example/pattern.svg)}",
        ".logo{fill:url(data:image/svg+xml;base64,PHN2Zz4=)}",
        r".logo{fill:u\72l(https://tracker.example/pattern.svg)}",
    ],
)
def test_svg_dom_rejects_unsafe_style_element(css: str) -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f"<style>{css}</style>"
        '<rect class="logo" width="20" height="20"/>'
        "</svg>"
    ).encode()
    with pytest.raises(SvgSecurityError, match="CSS"):
        check_svg_safety(svg, "unsafe-style.svg")


@pytest.mark.parametrize(
    "attribute",
    [
        'href=" https://tracker.example/pixel"',
        'fill="url(https://tracker.example/pattern.svg)"',
        'filter="url(//tracker.example/filter.svg)"',
    ],
)
def test_svg_dom_rejects_external_iri_attributes(attribute: str) -> None:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg"><rect {attribute}/></svg>'.encode()
    with pytest.raises(SvgSecurityError, match="external|CSS"):
        check_svg_safety(svg, "external.svg")


def test_svg_safety_two_pass_catches_encoded_payload() -> None:
    """Full check_svg_safety must catch HTML-entity-encoded <script> tags."""
    # &#x3C;script&#x3E; decodes to <script> after HTML entity unescaping
    svg = b'<svg xmlns="http://www.w3.org/2000/svg">&#x3C;script&#x3E;alert(1)&#x3C;/script&#x3E;</svg>'

    with pytest.raises(SvgSecurityError):
        check_svg_safety(svg, "encoded.svg")


def test_svg_safety_accepts_clean_svg() -> None:
    """Full check_svg_safety must pass a plain geometric SVG."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100" fill="red"/></svg>'
    )
    check_svg_safety(svg, "clean.svg")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 8. Worker startup / shutdown integration with priority queue names
# ─────────────────────────────────────────────────────────────────────────────


def test_worker_settings_classes_exist() -> None:
    """All three worker settings classes must be importable."""
    from app.workers.settings import (
        UploadFastWorkerSettings,
        UploadSlowWorkerSettings,
        WorkerSettings,
    )

    assert WorkerSettings.functions
    assert UploadFastWorkerSettings.queue_name == _FAST_QUEUE_NAME
    assert UploadSlowWorkerSettings.queue_name == _SLOW_QUEUE_NAME


def test_upload_fast_worker_has_process_upload() -> None:
    """Fast upload worker must include process_upload in its function list."""
    from app.workers.process_upload import process_upload
    from app.workers.settings import UploadFastWorkerSettings

    assert process_upload in UploadFastWorkerSettings.functions


def test_upload_slow_worker_has_process_upload() -> None:
    """Slow upload worker must include process_upload in its function list."""
    from app.workers.process_upload import process_upload
    from app.workers.settings import UploadSlowWorkerSettings

    assert process_upload in UploadSlowWorkerSettings.functions
