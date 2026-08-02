"""Audio and video metadata stripping and compression.

Provides:
- _strip_video_from_path: ffmpeg stream-copy metadata strip (path-based)
- _strip_audio_from_path: mutagen tag removal (path-based)
- _compress_video_path: ffmpeg H.264/VP9 re-encode (path-based)
- _convert_to_opus_path: ffmpeg Opus conversion (path-based)
"""

import logging
from pathlib import Path
from typing import Any

import mutagen

from app.core.security.file_security._concurrency import _get_concurrency_guard, _make_temp_path
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)

# Threshold above which we skip video compression to avoid long timeouts
VIDEO_COMPRESS_THRESHOLD = 500 * 1024 * 1024  # 500 MB

_VIDEO_EXTENSION_HINTS: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
}

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


# Video compression profiles
_VIDEO_PROFILES: dict[str, tuple[str | None, str | None, str, str, str, str]] = {
    "light": (None, None, "32", "128k", "39", "96k"),
    "medium": (None, None, "36", "128k", "46", "96k"),
    "aggressive": (
        "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        None,
        "40",
        "96k",
        "50",
        "64k",
    ),
    "heavy": (
        "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        None,
        "44",
        "64k",
        "54",
        "48k",
    ),
    "extreme": (
        "scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "24",
        "51",
        "48k",
        "60",
        "32k",
    ),
}


def _build_video_codec_args(suffix: str, config: dict[str, Any] | None = None) -> list[str]:
    """Return ffmpeg codec arguments for the given video container suffix based on compression profile."""
    from app.config import settings

    cfg_profile = config.get("video_compression_profile") if config else None
    profile = cfg_profile if cfg_profile is not None else settings.video_compression_profile

    scale_vf, framerate, mp4_crf, mp4_ab, webm_crf, webm_ab = _VIDEO_PROFILES.get(
        profile, _VIDEO_PROFILES["medium"]
    )

    if suffix == ".webm":
        base_args = [
            "-c:v",
            "libvpx-vp9",
            "-crf",
            webm_crf,
            "-b:v",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            webm_ab,
        ]
    else:
        base_args = [
            "-c:v",
            "libx264",
            "-crf",
            mp4_crf,
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            mp4_ab,
        ]

    final_args: list[str] = []

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

    dst_name = str(_make_temp_path(suffix=ext))
    success = False
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await async_sandboxed_run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(file_path),
                    "-map_metadata",
                    "-1",
                    "-c",
                    "copy",
                    dst_name,
                ],
                ro_paths=[file_path],
                rw_paths=[dst_name],
                timeout=30,
            )
        if result.returncode != 0:
            stderr_str = result.stderr.decode("utf-8", errors="replace")
            logger.warning(
                "ffmpeg metadata strip path failed (rc=%d): %s",
                result.returncode,
                stderr_str[-500:],
            )
            return file_path
        success = True
        return Path(dst_name)
    finally:
        if not success:
            Path(dst_name).unlink(missing_ok=True)


def _strip_audio_from_path(file_path: Path, mime_type: str) -> Path:
    """Remove ID3/Vorbis/MP4 tags from audio files on disk."""
    import shutil

    new_path = _make_temp_path()
    success = False
    try:
        shutil.copyfile(file_path, new_path)

        hint = _AUDIO_FILENAME_HINTS.get(mime_type, "audio.mp3")
        audio = mutagen.File(str(new_path), filename=hint)
        if audio is None or audio.tags is None:
            return file_path

        audio.delete()
        audio.save()
        success = True
        return new_path
    except Exception as exc:
        logger.warning("Audio metadata strip failed: %s", exc)
        return file_path
    finally:
        if not success:
            new_path.unlink(missing_ok=True)


async def _compress_video_path(
    file_path: Path, suffix: str, config: dict[str, Any] | None = None
) -> Path:
    from app.config import settings

    cfg_profile = config.get("video_compression_profile") if config else None
    profile = cfg_profile if cfg_profile is not None else settings.video_compression_profile
    if profile == "none":
        return file_path

    if file_path.stat().st_size > VIDEO_COMPRESS_THRESHOLD:
        return file_path

    out_name = str(_make_temp_path(suffix=suffix))
    success = False
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await async_sandboxed_run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(file_path),
                    *_build_video_codec_args(suffix, config=config),
                    out_name,
                ],
                ro_paths=[file_path],
                rw_paths=[out_name],
                timeout=1200,
            )
        if result.returncode == 0:
            compressed_size = Path(out_name).stat().st_size
            if compressed_size > 0 and compressed_size < file_path.stat().st_size:
                success = True
                return Path(out_name)
    finally:
        if not success:
            Path(out_name).unlink(missing_ok=True)

    return file_path


async def _convert_to_opus_path(file_path: Path) -> Path:
    """Convert audio to Opus (lossy, high compression) using FFmpeg."""
    out_name = str(_make_temp_path(suffix=".opus"))
    success = False
    try:
        async with _get_concurrency_guard("subprocess"):
            result = await async_sandboxed_run(
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
                ro_paths=[file_path],
                rw_paths=[out_name],
                timeout=60,
            )
        if result.returncode == 0:
            converted_size = Path(out_name).stat().st_size
            if converted_size > 0 and converted_size < file_path.stat().st_size:
                success = True
                return Path(out_name)
    finally:
        if not success:
            Path(out_name).unlink(missing_ok=True)

    return file_path
