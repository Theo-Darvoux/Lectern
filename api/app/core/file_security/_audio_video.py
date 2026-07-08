"""Audio and video metadata stripping and compression.

Provides:
- _strip_video_from_path: ffmpeg stream-copy metadata strip (path-based)
- _strip_audio_from_path: mutagen tag removal (path-based)
- _compress_video_path: ffmpeg H.264/VP9 re-encode (path-based)
- _convert_to_opus_path: ffmpeg Opus conversion (path-based)
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import mutagen

from app.core.file_security._concurrency import _get_concurrency_guard
from app.core.sandbox import sandboxed_run

logger = logging.getLogger(__name__)

# Threshold above which we skip video compression to avoid long timeouts
VIDEO_COMPRESS_THRESHOLD = 500 * 1024 * 1024  # 500 MB

_VIDEO_EXTENSION_HINTS: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
}

# Filename hints help mutagen auto-detect format when magic bytes are ambiguous
_AUDIO_FILENAME_HINTS: dict[str, str] = {
    "audio/mpeg": "audio.mp3",
    "audio/mp3": "audio.mp3",
    "audio/flac": "audio.flac",
    "audio/x-flac": "audio.flac",
    "audio/ogg": "audio.ogg",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/mp4": "audio.m4a",
    "audio/x-m4a": "audio.m4a",
    "audio/aac": "audio.aac",
    "audio/x-aac": "audio.aac",
}


def _build_video_codec_args(suffix: str, config: dict | None = None) -> list[str]:  # type: ignore[type-arg]
    """Return ffmpeg codec arguments for the given video container suffix based on compression profile."""
    from app.config import settings

    cfg_profile = config.get("video_compression_profile") if config else None
    profile = cfg_profile if cfg_profile is not None else settings.video_compression_profile

    # Determine arguments based on profile
    if profile == "light":
        scale_vf = None
        framerate = None
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "32",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "39",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
        ]
    elif profile == "medium":
        scale_vf = None
        framerate = None
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "36",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "46",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
        ]
    elif profile == "aggressive":
        scale_vf = "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2"
        framerate = None
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "40",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "50",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
        ]
    elif profile == "heavy":
        scale_vf = "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2"
        framerate = None
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "44",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "54",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
        ]
    elif profile == "extreme":
        scale_vf = "scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2"
        framerate = "24"
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "51",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "48k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "60",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
        ]
    else:  # Fallback to medium
        scale_vf = None
        framerate = None
        mp4_args = [
            "-c:v",
            "libx264",
            "-crf",
            "36",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
        webm_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "46",
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
        ]

    base_args = webm_args if suffix == ".webm" else mp4_args
    final_args = []

    if scale_vf:
        final_args.extend(["-vf", scale_vf])
    if framerate:
        final_args.extend(["-r", framerate])

    final_args.extend(base_args)
    final_args.extend(["-map_metadata", "-1"])
    if suffix != ".webm":
        final_args.extend(["-movflags", "+faststart"])

    return final_args


async def _strip_video_from_path(file_path: Path, mime_type: str) -> Path:
    """Remove metadata from video files using ffmpeg on disk (stream copy, no re-encoding)."""
    ext = _VIDEO_EXTENSION_HINTS.get(mime_type, ".mp4")

    def _run(src_name: str, dst_name: str) -> "object":
        return sandboxed_run(
            [
                "ffmpeg",
                "-y",
                "-i",
                src_name,
                "-map_metadata",
                "-1",  # strip all global/stream metadata
                "-c",
                "copy",  # no re-encoding
                dst_name,
            ],
            rw_paths=[Path(src_name).parent, Path(dst_name).parent],
            timeout=30,
        )

    dst_name = tempfile.NamedTemporaryFile(suffix=ext, delete=False).name
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await asyncio.to_thread(_run, str(file_path), dst_name)
        if result.returncode != 0:  # type: ignore[attr-defined]
            stderr_str = result.stderr.decode("utf-8", errors="replace")  # type: ignore[attr-defined]
            logger.warning(
                "ffmpeg metadata strip path failed (rc=%d): %s",
                result.returncode,  # type: ignore[attr-defined]
                stderr_str[-500:],
            )
            Path(dst_name).unlink(missing_ok=True)
            return file_path
        return Path(dst_name)
    except Exception:
        Path(dst_name).unlink(missing_ok=True)
        raise


def _strip_audio_from_path(file_path: Path, mime_type: str) -> Path:
    """Remove ID3/Vorbis/MP4 tags from audio files on disk."""
    import shutil

    new_path = Path(tempfile.NamedTemporaryFile(delete=False).name)
    try:
        shutil.copyfile(file_path, new_path)

        hint = _AUDIO_FILENAME_HINTS.get(mime_type, "audio.mp3")
        # mutagen.File is used dynamically here to avoid export issues with mypy
        audio = mutagen.File(str(new_path), filename=hint)  # type: ignore[attr-defined]
        if audio is None or audio.tags is None:
            new_path.unlink(missing_ok=True)
            return file_path

        audio.delete()
        audio.save()
        return new_path
    except Exception as exc:
        logger.warning("Audio metadata strip failed: %s", exc)
        new_path.unlink(missing_ok=True)
        return file_path


async def _compress_video_path(file_path: Path, suffix: str, config: dict | None = None) -> Path:  # type: ignore[type-arg]
    from app.config import settings

    cfg_profile = config.get("video_compression_profile") if config else None
    profile = cfg_profile if cfg_profile is not None else settings.video_compression_profile
    if profile == "none":
        return file_path

    if file_path.stat().st_size > VIDEO_COMPRESS_THRESHOLD:
        return file_path

    out_name = tempfile.NamedTemporaryFile(suffix=suffix, delete=False).name
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await asyncio.to_thread(
                sandboxed_run,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(file_path),
                    *_build_video_codec_args(suffix, config=config),
                    out_name,
                ],
                rw_paths=[Path(out_name).parent, file_path.parent],
                timeout=1200,
            )
        if result.returncode == 0:
            compressed_size = Path(out_name).stat().st_size
            if compressed_size > 0 and compressed_size < file_path.stat().st_size:
                return Path(out_name)
    except Exception:
        Path(out_name).unlink(missing_ok=True)
        raise
    Path(out_name).unlink(missing_ok=True)
    return file_path


async def _convert_to_opus_path(file_path: Path) -> Path:
    """Convert audio to Opus (lossy, high compression) using FFmpeg."""
    out_name = tempfile.NamedTemporaryFile(suffix=".opus", delete=False).name
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await asyncio.to_thread(
                sandboxed_run,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(file_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "96k",
                    "-map_metadata",
                    "-1",
                    out_name,
                ],
                rw_paths=[Path(out_name).parent, file_path.parent],
                timeout=60,
            )
        if result.returncode == 0:
            converted_size = Path(out_name).stat().st_size
            if converted_size > 0 and converted_size < file_path.stat().st_size:
                return Path(out_name)
    except Exception:
        Path(out_name).unlink(missing_ok=True)
        raise
    Path(out_name).unlink(missing_ok=True)
    return file_path
