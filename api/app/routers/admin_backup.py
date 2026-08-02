from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import BadRequestError, NotFoundError
from app.core.database.database import get_db
from app.routers.admin import AdminUser
from app.services.backup import (
    MAX_LOCAL_BACKUPS,
    backup_filename,
    create_backup_zip,
    enforce_backup_rotation,
    list_local_backups,
    restore_from_zip_path,
)

router = APIRouter(prefix="/api/admin/backup", tags=["Admin Backup"])
logger = logging.getLogger(__name__)


def _backup_dir() -> Path:
    d = Path(settings.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_backup(backup_id: str, backup_dir: Path) -> Path:
    path = backup_dir / f"{backup_id}.zip"
    if ".." in backup_id or not path.exists():
        raise NotFoundError(f"Backup not found: {backup_id!r}")
    return path


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("")
async def list_backups(_user: AdminUser) -> list[dict[str, Any]]:
    """List server-local backups (oldest first)."""
    return list_local_backups(_backup_dir())


# ── Save locally ──────────────────────────────────────────────────────────────


@router.post("/save", status_code=201)
async def save_backup(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Create a backup and save it on the server (max 3 kept; oldest rotated out)."""
    backup_dir = _backup_dir()
    filename = backup_filename()
    dest = backup_dir / f"{filename}.zip"

    try:
        manifest = await create_backup_zip(db, dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        logger.exception("Backup creation failed")
        raise BadRequestError(f"Backup failed: {exc}") from exc

    deleted = enforce_backup_rotation(backup_dir, max_count=MAX_LOCAL_BACKUPS)
    if deleted:
        logger.info("Rotated out backups: %s", deleted)

    stat = dest.stat()
    return {
        "status": "ok",
        "backup": {
            "id": filename,
            "filename": dest.name,
            "size_bytes": stat.st_size,
            "created_at": manifest["created_at"],
        },
        "manifest": manifest,
        "rotated": deleted,
    }


# ── Export (download without saving locally) ──────────────────────────────────


@router.get("/export")
async def export_backup(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    """Create a backup and stream it directly to the client (no server copy kept)."""
    filename = backup_filename()

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        await create_backup_zip(db, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        logger.exception("Backup export failed")
        raise BadRequestError(f"Backup failed: {exc}") from exc

    return FileResponse(
        path=str(tmp_path),
        filename=f"{filename}.zip",
        media_type="application/zip",
        background=_cleanup_task(tmp_path),  # type: ignore[arg-type]
    )


class _cleanup_task:
    """Starlette BackgroundTask that deletes a temp file after the response is sent."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def __call__(self) -> None:
        self._path.unlink(missing_ok=True)


# ── Download local backup ─────────────────────────────────────────────────────


@router.get("/{backup_id}/download")
async def download_backup(backup_id: str, _user: AdminUser) -> FileResponse:
    """Stream a specific server-local backup to the client."""
    path = _resolve_backup(backup_id, _backup_dir())
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/zip",
    )


# ── Delete local backup ───────────────────────────────────────────────────────


@router.delete("/{backup_id}")
async def delete_backup(backup_id: str, _user: AdminUser) -> dict[str, str]:
    """Delete a server-local backup."""
    path = _resolve_backup(backup_id, _backup_dir())
    path.unlink()
    return {"status": "ok", "deleted": path.name}


# ── Restore from local backup ─────────────────────────────────────────────────


@router.post("/{backup_id}/restore")
async def restore_local_backup(
    backup_id: str,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Full-replacement restore from a server-local backup."""
    path = _resolve_backup(backup_id, _backup_dir())

    try:
        manifest = await restore_from_zip_path(db, path)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Restore from local backup failed")
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc

    return {"status": "ok", "manifest": manifest}


# ── Restore from uploaded file ────────────────────────────────────────────────


@router.post("/restore/upload")
async def restore_uploaded_backup(
    _user: AdminUser,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Full-replacement restore from an uploaded backup ZIP."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise BadRequestError("Uploaded file must be a .zip backup")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    try:
        # Stream upload to temp file
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp.close()

        manifest = await restore_from_zip_path(db, tmp_path)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Restore from upload failed")
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"status": "ok", "manifest": manifest}
