import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.core.events.processing import ProcessingFile
from app.core.security.file_security import compress_file_path
from app.core.security.file_security.errors import SanitizationError
from app.workers.upload.constants import _compression_timeout

logger = logging.getLogger(__name__)


@dataclass
class CompressResult:
    final_mime: str
    content_encoding: str | None


async def run_compress_stage(
    pf: ProcessingFile,
    mime_type: str,
    original_filename: str,
    tracer: Any,
    config: dict[str, Any] | None = None,
) -> CompressResult:
    final_mime = mime_type
    content_encoding = None

    with tracer.start_as_current_span("upload.compress"):
        comp_timeout = _compression_timeout(mime_type)
        generated_path = None
        try:
            comp_res = await asyncio.wait_for(
                compress_file_path(
                    pf.path,
                    mime_type,
                    original_filename,
                    config=config,
                ),
                timeout=comp_timeout,
            )
            if comp_res.path != pf.path:
                generated_path = comp_res.path
                await pf.replace_with(generated_path)
                generated_path = None  # ProcessingFile now owns the path.
            final_mime = comp_res.mime_type
            content_encoding = comp_res.content_encoding
        except BaseException as exc:
            if generated_path is not None and generated_path != pf.path:
                generated_path.unlink(missing_ok=True)
            if isinstance(exc, (asyncio.CancelledError, SanitizationError)):
                raise
            logger.warning(
                "Compression failed for %s: %s - proceeding uncompressed",
                original_filename,
                exc,
            )

    return CompressResult(final_mime=final_mime, content_encoding=content_encoding)
