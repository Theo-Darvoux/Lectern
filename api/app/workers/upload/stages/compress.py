import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.core.file_security import compress_file_path
from app.core.processing import ProcessingFile
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
                pf.replace_with(comp_res.path)
            final_mime = comp_res.mime_type
            content_encoding = comp_res.content_encoding
        except Exception as exc:
            logger.warning(
                "Compression failed for %s: %s - proceeding uncompressed",
                original_filename,
                exc,
            )

    return CompressResult(final_mime=final_mime, content_encoding=content_encoding)
