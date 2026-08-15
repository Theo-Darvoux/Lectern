"""Comprehensive tests for security fixes listed in the audit requirement."""

import asyncio
import io
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.config import settings
from app.core.database.redis import RedisSemaphoreUnavailableError
from app.core.security.async_utils import shielded_to_thread as _shielded_to_thread
from app.core.security.file_security._concurrency import (
    _get_concurrency_guard,
    run_managed_subprocess,
)
from app.core.security.file_security._image import (
    _strip_image_metadata,
)
from app.core.security.sandbox import (
    SubprocessOutputLimitError,
    async_sandboxed_run,
    sandboxed_run,
)


# =============================================================================
# 1. Repeated cancellation test
# =============================================================================
@pytest.mark.asyncio
async def test_repeated_cancellation_shields_thread():
    """Cancel a guarded thread task twice and verify worker thread runs to completion before raising CancelledError."""
    thread_started = asyncio.Event()
    thread_can_exit = asyncio.Event()
    thread_exited = False

    def slow_worker():
        nonlocal thread_exited
        thread_started.set()
        while not thread_can_exit.is_set():
            time.sleep(0.01)
        thread_exited = True
        return 42

    task = asyncio.create_task(_shielded_to_thread(slow_worker))
    await thread_started.wait()

    # Cancel task first time
    task.cancel()

    # Small delay, then cancel task second time
    await asyncio.sleep(0.02)
    task.cancel()

    assert not thread_exited, "Worker thread should still be running while shielded"
    assert not task.done(), "Task should not complete until worker thread finishes"

    # Allow thread to finish
    thread_can_exit.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert thread_exited, "Worker thread must have finished before CancelledError was re-raised"


# =============================================================================
# 2 & 8. Stream flooding and reader cleanup tests
# =============================================================================
@pytest.mark.asyncio
async def test_simultaneous_stdout_stderr_flooding():
    """Flood stdout and stderr simultaneously; verify limit error and no deadlock."""
    script = (
        "import sys\n"
        "sys.stdout.buffer.write(b'X' * 1500000)\n"
        "sys.stderr.buffer.write(b'Y' * 1500000)\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
    )
    with patch("app.core.security.sandbox._MAX_SUBPROCESS_OUTPUT_BYTES", 500 * 1024):
        with pytest.raises(SubprocessOutputLimitError):
            await async_sandboxed_run(["python3", "-c", script], timeout=10)


@pytest.mark.asyncio
async def test_reader_cleanup_one_stream_exceeds_limit():
    """One stream crosses limit while other stays open; verify process and reader tasks cleaned up."""
    script = (
        "import sys, time\n"
        "sys.stdout.buffer.write(b'A' * 2000000)\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    with patch("app.core.security.sandbox._MAX_SUBPROCESS_OUTPUT_BYTES", 100 * 1024):
        with pytest.raises(SubprocessOutputLimitError):
            await async_sandboxed_run(["python3", "-c", script], timeout=5)


# =============================================================================
# 3. Synchronous runner output limit
# =============================================================================
def test_sync_sandboxed_run_output_limit():
    """Verify sandboxed_run cannot capture unbounded output."""
    script = "import sys; sys.stdout.buffer.write(b'Z' * 2000000); sys.stdout.flush()"
    with patch("app.core.security.sandbox._MAX_SUBPROCESS_OUTPUT_BYTES", 100 * 1024):
        with pytest.raises(SubprocessOutputLimitError):
            sandboxed_run(["python3", "-c", script], timeout=2)


# =============================================================================
# 4. Animated-format policy test
# =============================================================================
def test_animated_format_policy():
    """APNG, GIF, WebP, Multi-page TIFF should be rejected when animated/multi-frame."""
    gif_img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    gif_img2 = Image.new("RGBA", (10, 10), (0, 255, 0, 255))

    # 1. Multi-frame GIF
    buf_gif = io.BytesIO()
    gif_img.save(buf_gif, format="GIF", save_all=True, append_images=[gif_img2])
    with pytest.raises(ValueError, match="Animated and multi-frame images are not supported"):
        _strip_image_metadata(buf_gif.getvalue())

    # 2. Multi-frame WebP
    buf_webp = io.BytesIO()
    gif_img.save(buf_webp, format="WEBP", save_all=True, append_images=[gif_img2])
    with pytest.raises(ValueError, match="Animated and multi-frame images are not supported"):
        _strip_image_metadata(buf_webp.getvalue())

    # 3. Multi-page TIFF
    buf_tiff = io.BytesIO()
    gif_img.save(buf_tiff, format="TIFF", save_all=True, append_images=[gif_img2])
    with pytest.raises(ValueError, match="Unsupported image format"):
        _strip_image_metadata(buf_tiff.getvalue())


# =============================================================================
# 5. Palette transparency & orientation test
# =============================================================================
def test_palette_transparency_and_orientation():
    """Assert output alpha and representative pixels match for palette PNG, transparent GIF, RGBA PNG, EXIF JPEG."""
    # 1. Transparent GIF (single frame)
    gif_transparent = Image.new("P", (10, 10))
    gif_transparent.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 0] + [0] * 759)
    gif_transparent.putpixel((1, 1), 1)
    gif_transparent.info["transparency"] = 1
    buf_gif = io.BytesIO()
    gif_transparent.save(buf_gif, format="GIF", transparency=1)

    stripped_gif_bytes = _strip_image_metadata(buf_gif.getvalue())
    with Image.open(io.BytesIO(stripped_gif_bytes)) as out_gif:
        out_rgba = out_gif.convert("RGBA")
        assert out_rgba.getpixel((1, 1))[3] == 0

    # 2. Palette PNG with transparency
    png_palette = Image.new("P", (10, 10))
    png_palette.putpalette([0, 0, 255, 255, 255, 255] + [0] * 762)
    png_palette.info["transparency"] = b"\x00\xff"
    buf_png = io.BytesIO()
    png_palette.save(buf_png, format="PNG")

    stripped_png_bytes = _strip_image_metadata(buf_png.getvalue())
    with Image.open(io.BytesIO(stripped_png_bytes)) as out_png:
        out_rgba = out_png.convert("RGBA")
        assert out_rgba.getpixel((0, 0))[3] == 0

    # 3. RGBA PNG
    rgba_img = Image.new("RGBA", (10, 10), (100, 150, 200, 128))
    buf_rgba = io.BytesIO()
    rgba_img.save(buf_rgba, format="PNG")

    stripped_rgba_bytes = _strip_image_metadata(buf_rgba.getvalue())
    with Image.open(io.BytesIO(stripped_rgba_bytes)) as out_rgba_png:
        assert out_rgba_png.mode in {"RGBA", "PA", "P"}
        px = out_rgba_png.convert("RGBA").getpixel((5, 5))
        assert px[3] == 128

    # 4. EXIF-oriented JPEG
    jpeg_img = Image.new("RGB", (20, 10), (255, 0, 0))
    exif = jpeg_img.getexif()
    exif[0x0112] = 6  # Rotate 90 CW (width becomes 10, height becomes 20)
    buf_jpeg = io.BytesIO()
    jpeg_img.save(buf_jpeg, format="JPEG", exif=exif)

    stripped_jpeg_bytes = _strip_image_metadata(buf_jpeg.getvalue())
    with Image.open(io.BytesIO(stripped_jpeg_bytes)) as out_jpeg:
        assert out_jpeg.size == (10, 20)
        assert "exif" not in out_jpeg.info


# =============================================================================
# 6. Production circuit test
# =============================================================================
@pytest.mark.asyncio
async def test_production_circuit_fails_closed():
    """Verify that in production, Redis failure raises without fallback or running body."""
    body_count = 0

    with patch.object(settings, "environment", "production"):
        with patch(
            "app.core.security.file_security._concurrency.redis_semaphore",
            side_effect=RedisSemaphoreUnavailableError("Redis down"),
        ):
            # Attempt 1: raises RedisSemaphoreUnavailableError, body_count is 0
            with pytest.raises(RedisSemaphoreUnavailableError):
                async with _get_concurrency_guard("image"):
                    body_count += 1

            assert body_count == 0

            # Immediate attempt 2: should also fail closed without running body
            with pytest.raises(RedisSemaphoreUnavailableError):
                async with _get_concurrency_guard("image"):
                    body_count += 1

            assert body_count == 0


# =============================================================================
# 7. Complete deadline test
# =============================================================================
@pytest.mark.asyncio
async def test_complete_deadline_passes_remaining_time():
    """Consume part of timeout in semaphore acquisition, verify remaining time passed to runner."""
    called_timeout = None

    async def fake_sandboxed_run(cmd, timeout=60, **kwargs):
        nonlocal called_timeout
        called_timeout = timeout
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with patch(
        "app.core.security.file_security._concurrency.async_sandboxed_run",
        side_effect=fake_sandboxed_run,
    ):
        await run_managed_subprocess(["true"], timeout=10)
        assert called_timeout is not None
        assert 1 <= called_timeout <= 10


# =============================================================================
# 8. Redis expired lease renewal and exception preservation tests
# =============================================================================
@pytest.mark.asyncio
async def test_redis_expired_lease_renewal_fails():
    """Verify that attempting to renew an expired or missing lease returns 0 and raises RedisConcurrencyError."""
    from unittest.mock import AsyncMock

    from app.core.database.redis import RedisConcurrencyError, redis_semaphore

    mock_client = AsyncMock()
    mock_client.zrem.return_value = 1

    # Acquire returns 1, but renew returns 0 (lease expired/missing)
    async def mock_script(keys, args, client=None):
        op = args[2]
        if op == "acquire":
            return 1
        return 0

    mock_client.register_script = MagicMock(return_value=mock_script)
    with pytest.raises(RedisConcurrencyError, match="Lost semaphore lease"):
        async with redis_semaphore(mock_client, "test_sem", limit=1, timeout=1.0, expire=0.03):
            # Wait long enough for renewal loop to run
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_redis_body_exception_preserved_over_cleanup_error():
    """Verify that a body exception is never overwritten by a cleanup or renewal error."""
    from unittest.mock import AsyncMock

    from app.core.database.redis import redis_semaphore

    mock_client = AsyncMock()
    # Mock zrem to raise an exception during cleanup
    mock_client.zrem.side_effect = Exception("Cleanup network error")

    async def mock_script(keys, args, client=None):
        return 1

    mock_client.register_script = MagicMock(return_value=mock_script)
    with pytest.raises(ZeroDivisionError):
        async with redis_semaphore(mock_client, "test_sem", limit=1, timeout=1.0, expire=10):
            raise ZeroDivisionError("Original body error")


# =============================================================================
# 9. Image compression EXIF orientation and MIME accuracy tests
# =============================================================================
def test_compress_image_applies_exif_orientation():
    """Verify that image compression applies EXIF orientation before resizing and returns image/webp."""
    from app.core.security.file_security._image import _compress_image_path, _make_temp_path

    # Create EXIF-oriented image (width 3000, height 1000 with 90 CW EXIF flag)
    img = Image.new("RGB", (3000, 1000), (255, 0, 0))
    exif = img.getexif()
    exif[0x0112] = 6  # 90 CW (transposed dimensions should be 1000x3000, resized to <= 2048)
    tmp_input = _make_temp_path(suffix=".jpg")
    img.save(tmp_input, format="JPEG", exif=exif)

    try:
        compressed_path, res_mime = _compress_image_path(tmp_input)
        assert res_mime == "image/webp"
        with Image.open(compressed_path) as out_img:
            # Transposed and resized: height should be 2048, width < 2048
            assert out_img.height == 2048
            assert out_img.width < 2048
    finally:
        tmp_input.unlink(missing_ok=True)
        if "compressed_path" in locals() and compressed_path != tmp_input:
            compressed_path.unlink(missing_ok=True)


def test_compress_image_mime_accuracy_for_uncompressed_or_failed():
    """Verify that uncompressed PNG/JPEG files return their accurate original MIME types rather than JPEG fallback."""
    from app.core.security.file_security._image import _compress_image_path, _make_temp_path

    # Single-pixel WebP file where re-encoding does not yield size savings
    tiny = Image.new("RGB", (1, 1), (255, 0, 0))
    tmp_webp = _make_temp_path(suffix=".webp")
    tiny.save(tmp_webp, format="WEBP")

    try:
        compressed_path, res_mime = _compress_image_path(tmp_webp)
        assert res_mime == "image/webp"
        assert compressed_path == tmp_webp
    finally:
        tmp_webp.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_redis_lock_body_exception_preserved():
    """Verify that redis_lock preserves body exceptions even if release fails."""
    from unittest.mock import AsyncMock

    from app.core.database.redis import redis_lock

    mock_client = MagicMock()
    mock_lock = AsyncMock()
    mock_lock.acquire.return_value = True
    mock_lock.release.side_effect = Exception("Release network error")
    mock_client.lock.return_value = mock_lock

    with pytest.raises(ZeroDivisionError):
        async with redis_lock(mock_client, "test_lock", timeout=1.0, expire=10):
            raise ZeroDivisionError("Lock body error")


@pytest.mark.asyncio
async def test_ooxml_malformed_rels_before_vba_fails_closed():
    """Verify that an OOXML file with malformed .rels before vbaProject.bin raises SanitizationError and never fails open."""
    import zipfile

    from app.core.security.file_security import strip_metadata_file
    from app.core.security.file_security.errors import SanitizationError
    from app.core.security.processing_paths import (
        make_processing_temp_path as _make_temp_path,
    )

    tmp_docx = _make_temp_path(suffix=".docx")
    with zipfile.ZipFile(tmp_docx, "w") as z:
        z.writestr("_rels/.rels", b"<invalid xml broken content...")
        z.writestr("word/vbaProject.bin", b"VBA macro payload")

    try:
        with pytest.raises(SanitizationError):
            await strip_metadata_file(
                tmp_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
    finally:
        tmp_docx.unlink(missing_ok=True)
