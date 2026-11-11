"""Material-version creation from a PR operation's uploaded file."""

import typing
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ConflictError
from app.core.common.upload_limits import enforce_upload_size_limit
from app.core.database.post_commit import PostCommitKey, register_transaction_callbacks
from app.core.security.async_utils import shielded_await
from app.core.storage.facade import copy_object, delete_object, get_object_info
from app.models.cas_staging_claim import CasStagingClaim
from app.models.material import MaterialVersion
from app.models.security import VirusScanResult
from app.models.upload import Upload
from app.services.pr_visibility import anonymous_material_key


def _resolve_mime_type(
    file_key: str, payload: dict[str, typing.Any], s3_mime: str | None = None
) -> str:
    """Determine MIME type. Trusts S3 metadata or payload hint over re-scanning."""
    if s3_mime and s3_mime != "application/octet-stream":
        return s3_mime
    return payload.get("file_mime_type") or "application/octet-stream"


async def _resolve_thumbnail_info(
    db: AsyncSession,
    cas_file_key: str,
    owner_id: uuid.UUID | None,
) -> tuple[str | None, str | None]:
    """Resolve only the contribution author's active upload thumbnail."""
    from app.models.upload import Upload as _Upload

    row = await db.execute(
        select(_Upload.thumbnail_key, _Upload.thumbnail_status)
        .where(
            _Upload.final_key == cas_file_key,
            _Upload.user_id == owner_id,
            _Upload.status == "clean",
            _Upload.cas_ref_count > 0,
        )
        .order_by(_Upload.updated_at.desc())
        .limit(1)
    )
    result = row.one_or_none()
    if result is None:
        return None, None
    return result[0], result[1]


_PR_PREPARED_VERSION_FILES_KEY = "pr_prepared_version_files"


_PR_PREPARED_VERSION_FILES_ACTIVE_KEY = "pr_prepared_version_files_active"


class _PreparedVersionFile(typing.TypedDict):
    file_key: str
    file_size: int
    file_mime_type: str
    cas_sha256: str | None
    thumbnail_key: str | None
    thumbnail_status: str | None


async def _prepare_version_file(
    db: AsyncSession,
    *,
    file_key: str,
    payload: dict[str, typing.Any],
    author_id: uuid.UUID | None,
    is_anonymous: bool = False,
) -> _PreparedVersionFile:
    """Resolve/copy external file state before any hierarchy transaction lock is taken."""
    stored_content_sha256: str | None = None
    if file_key.startswith("cas/"):
        upload_row = (
            await db.execute(
                select(Upload.size_bytes, Upload.content_sha256, Upload.mime_type)
                .where(
                    Upload.final_key == file_key,
                    Upload.user_id == author_id,
                    Upload.status == "clean",
                    Upload.cas_ref_count > 0,
                )
                .order_by(Upload.updated_at.desc())
                .limit(1)
            )
        ).one_or_none()

        if upload_row is not None:
            upload_size = upload_row[0]
            content_sha = upload_row[1] if len(upload_row) > 1 else None
            stored_mime = upload_row[2] if len(upload_row) > 2 else None

            if (
                upload_size is None
                or not stored_mime
                or stored_mime == "application/octet-stream"
                or not content_sha
            ):
                raise ConflictError("Verified upload metadata is incomplete")

            real_size = int(upload_size)
            mime_type = stored_mime
            stored_content_sha256 = content_sha
        else:
            claim = await db.scalar(
                select(CasStagingClaim)
                .where(
                    CasStagingClaim.user_id == author_id,
                    CasStagingClaim.file_key == file_key,
                    CasStagingClaim.consumed_at.is_not(None),
                )
                .with_for_update()
            )

            if claim is None:
                raise ConflictError("Verified CAS ownership is unavailable")

            info = await get_object_info(file_key)

            real_size = int(info["size"])
            mime_type = info.get("content_type")

            if not mime_type or mime_type == "application/octet-stream":
                raise ConflictError("Verified generated-CAS MIME metadata is unavailable")

            stored_content_sha256 = claim.sha256

        enforce_upload_size_limit(mime_type, real_size)
        thumbnail_key, thumbnail_status = await _resolve_thumbnail_info(db, file_key, author_id)
        final_key = file_key
    else:
        info = await get_object_info(file_key)
        real_size = int(info["size"])
        mime_type = _resolve_mime_type(file_key, payload, s3_mime=info.get("content_type"))

        enforce_upload_size_limit(mime_type, real_size)

        final_key = (
            anonymous_material_key(file_key)
            if is_anonymous
            else file_key.replace("uploads/", "materials/", 1)
        )

        async def rollback_copy() -> None:
            await delete_object(final_key)

        async def finalize_copy() -> None:
            return None

        managed_transaction = register_transaction_callbacks(
            db,
            on_rollback=rollback_copy,
            on_commit=finalize_copy,
        )

        try:
            await copy_object(file_key, final_key)
        except BaseException:
            if not managed_transaction:
                await shielded_await(
                    rollback_copy(), description="legacy material copy compensation"
                )
            raise
        thumbnail_key, thumbnail_status = await _resolve_thumbnail_info(db, file_key, author_id)

    return {
        "file_key": final_key,
        "file_size": real_size,
        "file_mime_type": mime_type,
        "cas_sha256": stored_content_sha256 if final_key.startswith("cas/") else None,
        "thumbnail_key": thumbnail_key,
        "thumbnail_status": thumbnail_status,
    }


async def _prepare_pr_version_files(
    db: AsyncSession,
    operations: list[dict[str, typing.Any]],
    author_id: uuid.UUID | None,
    is_anonymous: bool = False,
) -> None:
    """Perform every external file read/copy before PR hierarchy locking begins."""
    prepared: dict[int, _PreparedVersionFile] = {}
    for op in operations:
        op_type = str(op.get("op") or op.get("pr_type") or "")
        payloads: list[dict[str, typing.Any]] = []
        if op_type == "create_material":
            payloads.append(op)
            payloads.extend(typing.cast(list[dict[str, typing.Any]], op.get("attachments") or []))
        elif op_type == "edit_material":
            payloads.append(op)

        for payload in payloads:
            raw_key = payload.get("file_key")
            if not raw_key:
                continue
            prepared[id(payload)] = await _prepare_version_file(
                db,
                file_key=str(raw_key),
                payload=payload,
                author_id=author_id,
                is_anonymous=is_anonymous,
            )

    db.info[_PR_PREPARED_VERSION_FILES_KEY] = prepared
    db.info[_PR_PREPARED_VERSION_FILES_ACTIVE_KEY] = True


async def _make_version_for_file(
    db: AsyncSession,
    *,
    file_key: str,
    payload: dict[str, typing.Any],
    material_id: uuid.UUID,
    version_number: int,
    author_id: uuid.UUID | None,
    pr_id: uuid.UUID,
    diff_summary: str | None = None,
    version_lock: int = 0,
    is_anonymous: bool = False,
) -> MaterialVersion:
    """Resolve file metadata and create+flush a MaterialVersion.

    Handles CAS V2 (cas/ prefix) and legacy V1 (uploads/ prefix) keys.
    V1 keys are copied to the materials/ prefix and scheduled for deletion.
    CAS keys get their content-addressed ref incremented.
    """
    prepared_files = typing.cast(
        dict[int, _PreparedVersionFile],
        db.info.get(_PR_PREPARED_VERSION_FILES_KEY, {}),
    )
    prepared = prepared_files.pop(id(payload), None)
    if prepared is None:
        if db.info.get(_PR_PREPARED_VERSION_FILES_ACTIVE_KEY) is True:
            raise RuntimeError("PR file was not prepared before hierarchy mutation")
        prepared = await _prepare_version_file(
            db,
            file_key=file_key,
            payload=payload,
            author_id=author_id,
            is_anonymous=is_anonymous,
        )

    file_key = prepared["file_key"]
    real_size = prepared["file_size"]
    mime_type = prepared["file_mime_type"]
    stored_content_sha256 = prepared["cas_sha256"]
    thumbnail_key = prepared["thumbnail_key"]
    thumbnail_status = prepared["thumbnail_status"]

    mv = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material_id,
        version_number=version_number,
        file_key=file_key,
        file_name=payload.get("file_name"),
        file_size=real_size,
        file_mime_type=mime_type,
        cas_sha256=stored_content_sha256 if file_key.startswith("cas/") else None,
        author_id=author_id,
        is_anonymous=is_anonymous,
        pr_id=pr_id,
        virus_scan_result=VirusScanResult.CLEAN,
        version_lock=version_lock,
        diff_summary=diff_summary,
        thumbnail_key=thumbnail_key,
        thumbnail_status=thumbnail_status,
    )
    db.add(mv)
    await db.flush()

    if mv.file_key and mv.file_key.startswith("cas/") and mv.cas_sha256:
        db.info.setdefault(PostCommitKey.JOBS, []).append(
            (
                "add_cas_references",
                [
                    {
                        "sha256": mv.cas_sha256,
                        "operation_id": f"pr:{pr_id}:version:{mv.id}:add",
                        "initial_data": {
                            "final_key": mv.file_key,
                            "size": mv.file_size,
                            "mime_type": mv.file_mime_type,
                            "file_name": mv.file_name,
                        },
                    }
                ],
            )
        )

    return mv
