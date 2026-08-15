import asyncio
from pathlib import Path

import typer

app = typer.Typer(help="Lectern CLI")


@app.command(name="create-backup-offline")
def create_backup_offline(
    destination: Path = typer.Argument(..., dir_okay=False),
    confirm_offline: bool = typer.Option(
        False,
        "--confirm-offline",
        help="Confirm that every API and worker instance is stopped.",
    ),
) -> None:
    """Create a cross-datastore backup while all mutating processes are stopped."""
    if not confirm_offline:
        typer.echo("Refusing backup: stop every API/worker and pass --confirm-offline.")
        raise typer.Exit(code=2)
    resolved = destination.resolve()
    if resolved.exists():
        typer.echo(f"Refusing backup: destination already exists: {resolved}")
        raise typer.Exit(code=2)
    if not resolved.parent.is_dir():
        typer.echo(f"Refusing backup: destination directory does not exist: {resolved.parent}")
        raise typer.Exit(code=2)
    asyncio.run(_create_backup_offline(resolved))


async def _create_backup_offline(destination: Path) -> None:
    from app.core.database.database import async_session_factory
    from app.services.backup import create_backup_zip

    try:
        async with async_session_factory() as session:
            try:
                manifest = await create_backup_zip(session, destination)
            finally:
                await session.rollback()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    typer.echo(
        "Offline backup complete "
        f"(version {manifest.get('version')}, {manifest.get('s3_object_count')} objects): "
        f"{destination}"
    )


@app.command(name="restore-backup-offline")
def restore_backup_offline(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    confirm_offline: bool = typer.Option(
        False,
        "--confirm-offline",
        help="Confirm that every API and worker instance is stopped.",
    ),
) -> None:
    """Restore a full backup while all mutating application processes are stopped."""
    if not confirm_offline:
        typer.echo("Refusing restore: stop every API/worker and pass --confirm-offline.")
        raise typer.Exit(code=2)
    asyncio.run(_restore_backup_offline(path.resolve()))


async def _restore_backup_offline(path: Path) -> None:
    from app.core.database.database import async_session_factory
    from app.core.database.post_commit import (
        PostCommitKey,
        finalize_transaction_callbacks,
        rollback_transaction_callbacks,
    )
    from app.core.security.async_utils import settle_awaitable
    from app.services.backup import restore_from_zip_path

    async with async_session_factory() as session:
        session.info[PostCommitKey.MANAGED_TRANSACTION] = True
        commit_attempted = False
        try:
            manifest = await restore_from_zip_path(session, path)
            commit_attempted = True
            await session.commit()
        except BaseException:
            _result, rollback_error, rollback_cancellation = await settle_awaitable(
                session.rollback()
            )
            compensation_error: BaseException | None = None
            if commit_attempted:
                # Keep both restored storage and the rollback journal. A lost
                # COMMIT acknowledgement cannot be compensated safely.
                session.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)
                session.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
            else:
                try:
                    await rollback_transaction_callbacks(session)
                except BaseException as exc:
                    compensation_error = exc
            if compensation_error is not None and not isinstance(
                compensation_error, asyncio.CancelledError
            ):
                raise RuntimeError(
                    "Offline restore failed and external-resource compensation was incomplete"
                ) from compensation_error
            if rollback_error is not None:
                raise RuntimeError("Offline restore database rollback failed") from rollback_error
            if rollback_cancellation is not None:
                raise rollback_cancellation
            if compensation_error is not None:
                raise compensation_error
            raise
        else:
            await finalize_transaction_callbacks(session)
        finally:
            session.info.pop(PostCommitKey.MANAGED_TRANSACTION, None)
    typer.echo(f"Offline restore complete (backup version {manifest.get('version')}).")


@app.command()
def seed(
    email: str = typer.Option(..., help="Email for the first Bureau account"),
    role: str = typer.Option("bureau", help="Role to assign"),
    confirm_offline: bool = typer.Option(
        False,
        "--confirm-offline",
        help="Confirm that every API and worker instance is stopped (required in production).",
    ),
) -> None:
    from app.config import settings

    if settings.environment == "production" and not confirm_offline:
        typer.echo(
            "Refusing production seed while services may be live; stop API/workers "
            "and pass --confirm-offline."
        )
        raise typer.Exit(code=2)
    asyncio.run(_seed(email, role))


async def _seed(email: str, role: str) -> None:
    from sqlalchemy import select

    from app.core.database.database import async_session_factory
    from app.models.user import User, UserRole

    async with async_session_factory() as session:
        role_enum = UserRole(role)
        from app.services.auth import (
            acquire_setup_lock,
            ensure_admin_removal_safe,
            mark_installation_bootstrapped,
        )

        # Seed is an offline/admin tool, but serialize it with the same authority
        # boundary so it cannot violate the application's role invariant when used.
        await acquire_setup_lock(session)
        user = await session.scalar(
            select(User)
            .where(User.email == email)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is not None:
            if user.is_admin and role_enum not in (UserRole.BUREAU, UserRole.VIEUX):
                await ensure_admin_removal_safe(session, user.id)
            user.role = role_enum
            typer.echo(f"Updated {email} to role '{role}'")
        else:
            user = User(email=email, role=role_enum)
            session.add(user)
            typer.echo(f"Created user {email} with role '{role}'")

        if role_enum in (UserRole.BUREAU, UserRole.VIEUX):
            await mark_installation_bootstrapped(session)

        await session.commit()
        typer.echo("Seed complete.")


@app.command(name="recover-admin-offline")
def recover_admin_offline(
    email: str = typer.Option(..., help="Email of the administrator to create or recover"),
    password: str = typer.Option(
        ...,
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Recovery password (at least 8 characters)",
    ),
    confirm_offline: bool = typer.Option(
        False,
        "--confirm-offline",
        help="Confirm that every API and worker instance is stopped.",
    ),
) -> None:
    """Recover administrative authority without reopening HTTP bootstrap."""
    if not confirm_offline:
        typer.echo("Refusing admin recovery: stop every API/worker and pass --confirm-offline.")
        raise typer.Exit(code=2)
    if len(password) < 8 or len(password) > 128:
        typer.echo("Recovery password must contain between 8 and 128 characters.")
        raise typer.Exit(code=2)
    asyncio.run(_recover_admin_offline(email, password))


async def _recover_admin_offline(email: str, password: str) -> None:
    from app.core.database.database import async_session_factory
    from app.services.auth import recover_admin_account

    normalized = email.strip().lower()
    if "@" not in normalized or "+" in normalized or len(normalized) > 254:
        typer.echo("Invalid recovery email address.")
        raise typer.Exit(code=2)

    async with async_session_factory() as session:
        user, created = await recover_admin_account(session, normalized, password)
        await session.commit()
        action = "Created" if created else "Recovered"
        typer.echo(f"{action} recovery administrator {normalized}")
        typer.echo(
            "Admin recovery complete. HTTP bootstrap remains permanently consumed and "
            "all credentials issued before recovery for this account are invalid. "
            "If classic authentication is disabled, use an enabled login method for this account."
        )


@app.command()
def reindex() -> None:
    asyncio.run(_reindex())


async def _reindex() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database.database import async_session_factory
    from app.core.events.meilisearch import meili_admin_client, setup_meilisearch
    from app.models.directory import Directory
    from app.models.material import Material
    from app.services.directory import get_directory_path

    def split_identifiers(text: str) -> str:
        import re

        if not text:
            return ""
        # Add space between letters and digits
        s = re.sub(r"([a-zA-Z]+)(\d+)", r"\1 \2", text)
        # Add space between digits and letters
        s = re.sub(r"(\d+)([a-zA-Z]+)", r"\1 \2", s)
        return s

    # First ensure indexes exist
    await setup_meilisearch()

    async with async_session_factory() as session:
        # Reindex Materials
        m_result = await session.execute(
            select(Material).options(
                selectinload(Material.tags),
                selectinload(Material.author),
                selectinload(Material.versions),
            )
        )
        materials = m_result.scalars().all()

        m_docs = []
        for mat in materials:
            ancestor_path = ""
            browse_path = "/browse"
            if mat.directory_id:
                path_parts = await get_directory_path(session, mat.directory_id)
                if path_parts:
                    ancestor_path = " ".join(p["name"] for p in path_parts)
                    browse_path += "/" + "/".join(p["slug"] for p in path_parts)

            browse_path += f"/{mat.slug}"

            # Find current version metadata
            file_name = None
            for v in mat.versions:
                if v.version_number == mat.current_version:
                    file_name = v.file_name
                    # Break after finding current version info
                    break

            # Build extra searchable fields (identifiers)
            extra = f"{split_identifiers(mat.title)} {split_identifiers(ancestor_path)} {split_identifiers(file_name or '')}"

            m_docs.append(
                {
                    "id": str(mat.id),
                    "title": mat.title,
                    "slug": mat.slug,
                    "description": mat.description or "",
                    "type": mat.type,
                    "tags": [t.name for t in mat.tags] if mat.tags else [],
                    "authorName": mat.author.display_name if mat.author else None,
                    "directory_id": str(mat.directory_id) if mat.directory_id else None,
                    "created_at": mat.created_at.isoformat()
                    if mat.created_at is not None  # type: ignore[redundant-expr]
                    else None,
                    "ancestor_path": ancestor_path,
                    "extra_searchable": extra,
                    "browse_path": browse_path,
                }
            )

        if m_docs:
            await meili_admin_client.index("materials").add_documents(m_docs)
            typer.echo(f"Reindexed {len(m_docs)} materials.")
        else:
            typer.echo("0 materials to reindex.")

        # Reindex Directories
        d_result = await session.execute(select(Directory).options(selectinload(Directory.tags)))
        directories = d_result.scalars().all()

        d_docs = []
        for dir_obj in directories:
            ancestor_path = ""
            browse_path = "/browse"
            if dir_obj.parent_id:
                path_parts = await get_directory_path(session, dir_obj.parent_id)
                if path_parts:
                    ancestor_path = " ".join(p["name"] for p in path_parts)
                    browse_path += "/" + "/".join(p["slug"] for p in path_parts)

            browse_path += f"/{dir_obj.slug}"

            metadata = dir_obj.metadata_ or {}
            code = metadata.get("code") or ""

            # Build extra searchable fields (identifiers)
            extra = f"{split_identifiers(dir_obj.name)} {split_identifiers(code)} {split_identifiers(ancestor_path)}"  # type: ignore[arg-type]

            d_docs.append(
                {
                    "id": str(dir_obj.id),
                    "name": dir_obj.name,
                    "slug": dir_obj.slug,
                    "type": dir_obj.type.value if dir_obj.type else "folder",
                    "description": dir_obj.description or "",
                    "tags": [t.name for t in dir_obj.tags] if dir_obj.tags else [],
                    "code": code,
                    "parent_id": str(dir_obj.parent_id) if dir_obj.parent_id else None,
                    "created_at": dir_obj.created_at.isoformat()
                    if dir_obj.created_at is not None  # type: ignore[redundant-expr]
                    else None,
                    "ancestor_path": ancestor_path,
                    "extra_searchable": extra,
                    "browse_path": browse_path,
                }
            )

        if d_docs:
            await meili_admin_client.index("directories").add_documents(d_docs)
            typer.echo(f"Reindexed {len(d_docs)} directories.")
        else:
            typer.echo("0 directories to reindex.")

    typer.echo("Done reindexing both materials and directories.")


@app.command(name="gdpr-cleanup")
def gdpr_cleanup() -> None:
    """Purge soft-deleted users past the 30-day grace period."""
    asyncio.run(_gdpr_cleanup())


async def _gdpr_cleanup() -> None:
    from app.workers.gdpr_cleanup import gdpr_cleanup as run_cleanup

    await run_cleanup({})
    typer.echo("GDPR cleanup complete.")


@app.command(name="year-rollover")
def year_rollover() -> None:
    """Bump academic years: 1A->2A, 2A->3A+, 3A+ stays."""
    asyncio.run(_year_rollover())


async def _year_rollover() -> None:
    from app.workers.year_rollover import year_rollover as run_rollover

    await run_rollover({})
    typer.echo("Year rollover complete.")


@app.command(name="migrate-cas-v2")
def migrate_cas_v2(
    dry_run: bool = typer.Option(True, help="Preview changes without writing"),
    batch_size: int = typer.Option(100, help="Process this many rows per batch"),
) -> None:
    """Migrate MaterialVersion file_keys from materials/ to cas/ (CAS V2).

    For each MaterialVersion with a materials/ file_key:
    1. Find the original SHA-256 via the linked Upload row
    2. Compute the HMAC CAS key and verify the cas/ S3 object exists
    3. If missing, copy from materials/ to cas/
    4. Update file_key to cas/{hmac} and set cas_sha256
    5. Initialize CAS ref count in Redis
    """
    asyncio.run(_migrate_cas_v2(dry_run, batch_size))


async def _migrate_cas_v2(dry_run: bool, batch_size: int) -> None:

    from sqlalchemy import func, select

    from app.core.database.database import async_session_factory
    from app.core.database.redis import redis_client
    from app.core.security.cas import hmac_cas_key, increment_cas_ref
    from app.core.storage.facade import copy_object, init_s3_client, object_exists
    from app.models.material import MaterialVersion
    from app.models.upload import Upload

    await init_s3_client()

    async with async_session_factory() as db:
        total = (
            await db.scalar(
                select(func.count())
                .select_from(MaterialVersion)
                .where(MaterialVersion.file_key.like("materials/%"))
            )
            or 0
        )

    typer.echo(f"Found {total} MaterialVersion(s) with materials/ file_keys")
    if total == 0:
        typer.echo("Nothing to migrate.")
        return

    migrated = 0
    skipped = 0
    copied = 0
    errors = 0
    offset = 0

    while offset < total:
        async with async_session_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(MaterialVersion)
                        .where(MaterialVersion.file_key.like("materials/%"))
                        .order_by(MaterialVersion.id)
                        .offset(offset)
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                break

            for mv in rows:
                try:
                    # Find the Upload row that produced this file.
                    # Strategy: match by upload_id embedded in the materials/ key path.
                    # Key format: materials/{user_id}/{upload_id}/{filename}
                    parts = mv.file_key.split("/")  # type: ignore[union-attr]
                    if len(parts) < 4:
                        typer.echo(f"  SKIP {mv.id}: unexpected key format {mv.file_key}")
                        skipped += 1
                        continue

                    upload_id_str = parts[2]

                    upload = await db.scalar(
                        select(Upload).where(Upload.upload_id == upload_id_str)
                    )

                    sha256: str | None = None
                    if upload and upload.sha256:
                        sha256 = upload.sha256
                    elif upload and upload.content_sha256:
                        sha256 = upload.content_sha256

                    if not sha256:
                        typer.echo(f"  SKIP {mv.id}: no SHA-256 found for upload {upload_id_str}")
                        skipped += 1
                        continue

                    cas_hmac = hmac_cas_key(sha256).split(":")[-1]
                    cas_s3_key = f"cas/{cas_hmac}"

                    if dry_run:
                        exists = await object_exists(cas_s3_key)
                        typer.echo(
                            f"  [DRY] {mv.id}: {mv.file_key} -> {cas_s3_key} (CAS exists: {exists})"
                        )
                        migrated += 1
                        continue

                    # Ensure CAS object exists
                    if not await object_exists(cas_s3_key):
                        await copy_object(mv.file_key, cas_s3_key)  # type: ignore[arg-type]
                        copied += 1

                    # Update DB
                    mv.file_key = cas_s3_key
                    mv.cas_sha256 = sha256

                    # Initialize CAS ref count
                    await increment_cas_ref(
                        redis_client,
                        sha256,
                        initial_data={
                            "final_key": cas_s3_key,
                            "size": mv.file_size,
                            "mime_type": mv.file_mime_type,
                            "file_name": mv.file_name,
                        },
                        operation_id=f"migrate-cas:material-version:{mv.id}:add",
                    )

                    migrated += 1

                except Exception as exc:
                    typer.echo(f"  ERROR {mv.id}: {exc}")
                    errors += 1

            if not dry_run:
                await db.commit()

        offset += batch_size
        typer.echo(f"  Progress: {min(offset, total)}/{total}")

    typer.echo(
        f"\nMigration {'preview' if dry_run else 'complete'}: "
        f"{migrated} migrated, {copied} S3 copies, {skipped} skipped, {errors} errors"
    )
    if dry_run:
        typer.echo("Run with --no-dry-run to apply changes.")


@app.command(name="recalculate-thumbnails")
def recalculate_thumbnails(
    batch_size: int = typer.Option(50, help="Number of files to query per batch"),
    max_concurrency: int = typer.Option(
        50, help="Maximum in-flight recalculation jobs queued on workers"
    ),
    limit: int | None = typer.Option(None, help="Maximum number of materials to process"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without dispatching"),
    force: bool = typer.Option(False, "--force", "-f", help="Regenerate even if thumbnail exists"),
) -> None:
    """Regenerate thumbnails by dispatching tasks to background workers until complete."""
    try:
        asyncio.run(
            _recalculate_thumbnails(
                batch_size=batch_size,
                max_concurrency=max_concurrency,
                limit=limit,
                dry_run=dry_run,
                force=force,
            )
        )
    except KeyboardInterrupt:
        typer.echo("\nInterrupted by user (Ctrl+C). Exiting...")


async def _recalculate_thumbnails(
    batch_size: int,
    max_concurrency: int,
    limit: int | None,
    dry_run: bool,
    force: bool,
) -> None:
    import sys
    from typing import Any, cast

    from arq.jobs import Job, JobStatus
    from sqlalchemy import func, or_, select

    import app.core.database.redis as redis_core
    from app.core.database.database import async_session_factory
    from app.core.database.redis import close_arq_pool, init_arq_pool, redis_client
    from app.models.material import MaterialVersion
    from app.routers.upload.helpers import _processing_queue_name

    async with async_session_factory() as db:
        query = select(MaterialVersion).where(MaterialVersion.file_key.is_not(None))
        if not force:
            # Target materials with no thumbnail OR that previously failed generation.
            query = query.where(
                or_(
                    MaterialVersion.thumbnail_key.is_(None),
                    MaterialVersion.thumbnail_status == "failed",
                )
            )

        total_available = await db.scalar(select(func.count()).select_from(query.subquery())) or 0

    if total_available == 0:
        typer.echo("No materials found needing thumbnail recalculation.")
        return

    target_total = min(total_available, limit) if limit is not None else total_available
    typer.echo(
        f"Found {total_available} material(s) {'missing/failed' if not force else 'targeted for'} thumbnails."
    )
    if limit is not None:
        typer.echo(f"Processing up to {target_total} item(s) due to --limit={limit}.")

    if dry_run:
        typer.echo(f"[Dry Run] Would dispatch {target_total} recalculation jobs to workers.")
        return

    await init_arq_pool()
    if redis_core.arq_pool is None:
        typer.echo("ERROR: Failed to connect to Redis ARQ worker pool.", err=True)
        return

    dispatched = 0
    completed = 0
    failed = 0
    skipped = 0
    offset = 0
    active_jobs: dict[str, Job] = {}
    cancelled = False

    typer.echo(
        f"Dispatching jobs to worker pool (max in-flight: {max_concurrency}). Press Ctrl+C to stop at any time.\n"
    )

    async def poll_active_jobs() -> None:
        nonlocal completed, failed, skipped
        finished_ids = []
        for job_id, job in list(active_jobs.items()):
            try:
                status = await job.status()
                if status == JobStatus.complete:
                    result_info = await job.result_info()
                    if result_info and result_info.success:
                        if result_info.result is True:
                            completed += 1
                        else:
                            skipped += 1
                    else:
                        failed += 1
                    finished_ids.append(job_id)
                elif status == JobStatus.not_found:
                    completed += 1
                    finished_ids.append(job_id)
            except Exception:
                finished_ids.append(job_id)
                failed += 1

        for jid in finished_ids:
            active_jobs.pop(jid, None)

    try:
        while dispatched < target_total and not cancelled:
            await poll_active_jobs()

            while len(active_jobs) >= max_concurrency:
                await asyncio.sleep(0.1)
                await poll_active_jobs()
                progress_str = f"Progress: {completed + failed + skipped}/{target_total} | Dispatched: {dispatched} | Active: {len(active_jobs)} | OK: {completed} | Skipped: {skipped} | Failed: {failed}"
                sys.stdout.write(f"\r{progress_str}")
                sys.stdout.flush()

            current_batch = min(batch_size, target_total - dispatched)
            async with async_session_factory() as db:
                batch_query = query.offset(offset).limit(current_batch)
                versions = (await db.execute(batch_query)).scalars().all()

            if not versions:
                break

            offset += len(versions)

            for mv in versions:
                if cancelled:
                    break

                while len(active_jobs) >= max_concurrency:
                    await asyncio.sleep(0.1)
                    await poll_active_jobs()
                    progress_str = f"Progress: {completed + failed + skipped}/{target_total} | Dispatched: {dispatched} | Active: {len(active_jobs)} | OK: {completed} | Skipped: {skipped} | Failed: {failed}"
                    sys.stdout.write(f"\r{progress_str}")
                    sys.stdout.flush()

                if redis_client is not None:
                    try:
                        await redis_client.delete(f"thumbnail:v1:{mv.material_id}")
                    except Exception:
                        pass

                queue_name = _processing_queue_name(
                    mv.file_mime_type or "",
                    mv.file_size or 0,
                )

                enqueue_job = cast(Any, redis_core.arq_pool.enqueue_job)
                job = await enqueue_job(
                    "recalculate_thumbnail",
                    _queue_name=queue_name,
                    material_id=str(mv.material_id),
                    version_id=str(mv.id),
                )
                if job:
                    active_jobs[job.job_id] = job

                dispatched += 1
                progress_str = f"Progress: {completed + failed + skipped}/{target_total} | Dispatched: {dispatched} | Active: {len(active_jobs)} | OK: {completed} | Skipped: {skipped} | Failed: {failed}"
                sys.stdout.write(f"\r{progress_str}")
                sys.stdout.flush()

        # Drain remaining active jobs
        if not cancelled and active_jobs:
            while active_jobs:
                await asyncio.sleep(0.2)
                await poll_active_jobs()
                progress_str = f"Progress: {completed + failed + skipped}/{target_total} | Dispatched: {dispatched} | Active: {len(active_jobs)} | OK: {completed} | Skipped: {skipped} | Failed: {failed}"
                sys.stdout.write(f"\r{progress_str}")
                sys.stdout.flush()

    except (KeyboardInterrupt, asyncio.CancelledError):
        cancelled = True
        sys.stdout.write("\n\nExecution interrupted (Ctrl+C).\n")
    finally:
        sys.stdout.write("\n")
        sys.stdout.flush()
        typer.echo(
            f"Done. Dispatched: {dispatched}, Completed: {completed}, "
            f"Skipped: {skipped}, Failed: {failed}, In flight: {len(active_jobs)}"
        )
        await close_arq_pool()


@app.command(name="config-export-env")
def config_export_env() -> None:
    """Export current DB auth_configs row as .env-ready KEY=value lines.

    Run this ONCE against the live DB before the auth_configs drop migration.
    The output is paste-ready into your .env file.

    WARNING: output contains secrets (SMTP password, S3 keys). Keep it secure.
    """
    asyncio.run(_config_export_env())


async def _config_export_env() -> None:
    import shlex

    from sqlalchemy import text

    from app.core.database.database import async_session_factory

    # Column → env-var name overrides.  Columns not listed here map to UPPERCASE(column).
    column_map: dict[str, str] = {
        "jwt_access_expire_days": "JWT_ACCESS_TOKEN_EXPIRE_DAYS",
        "jwt_refresh_expire_days": "JWT_REFRESH_TOKEN_EXPIRE_DAYS",
    }

    async with async_session_factory() as session:
        # Fetch column names directly — auth_configs table may not have a model
        # at this point (model was dropped from Python but table still exists in DB).
        try:
            result = await session.execute(text("SELECT * FROM auth_configs LIMIT 1"))
        except Exception as e:
            typer.echo(f"# Could not read auth_configs: {e}")
            typer.echo("# Table may already be dropped or does not exist.")
            return

        row = result.mappings().first()
        if row is None:
            typer.echo("# auth_configs table is empty — nothing to export.")
            return

        skip = {"id", "created_at", "updated_at"}

        lines = []
        for col, val in row.items():
            if col in skip or val is None:
                continue
            env_key = column_map.get(col, col.upper())
            if isinstance(val, bool):
                env_val = "true" if val else "false"
            elif isinstance(val, (int, float)):
                env_val = str(val)
            else:
                env_val = shlex.quote(str(val))
            lines.append(f"{env_key}={env_val}")

        # Append allowed_domains rows as ALLOWED_DOMAINS
        try:
            domain_result = await session.execute(
                text("SELECT domain, auto_approve FROM allowed_domains ORDER BY domain")
            )
            domain_rows = domain_result.all()
            if domain_rows:
                parts = ",".join(
                    f"{d.domain}:{'auto' if d.auto_approve else 'manual'}" for d in domain_rows
                )
                lines.append(f"ALLOWED_DOMAINS={shlex.quote(parts)}")
        except Exception:
            pass

        if lines:
            typer.echo("# Exported from auth_configs — paste into .env")
            for line in lines:
                typer.echo(line)
        else:
            typer.echo("# All columns are NULL — env defaults are already in effect.")


@app.command(name="magic-link")
def magic_link(
    email: str = typer.Argument(..., help="Email of the user"),
) -> None:
    """Generate a magic login link for a user without sending an email."""
    asyncio.run(_magic_link(email))


async def _magic_link(email: str) -> None:
    from app.config import settings
    from app.core.database.database import async_session_factory
    from app.core.database.redis import redis_client
    from app.services.auth import (
        generate_code,
        generate_magic_token,
        login_challenge_auth_generation,
        store_login_challenge,
    )

    normalized_email = email.strip().lower()
    async with async_session_factory() as session:
        auth_generation = await login_challenge_auth_generation(session, normalized_email)

    token = generate_magic_token()
    await store_login_challenge(
        redis_client,
        normalized_email,
        generate_code(),
        token,
        auth_generation=auth_generation,
    )

    link = f"{settings.frontend_url.rstrip('/')}/login/verify#token={token}"
    typer.echo(f"Magic link for {normalized_email}:")
    typer.echo(link)


@app.command(name="change-email")
def change_email(
    current_email: str = typer.Argument(..., help="Current email of the account"),
    new_email: str = typer.Argument(..., help="New email to set"),
) -> None:
    """Change a user's email address (e.g. to fix a typo from first-run setup)."""
    asyncio.run(_change_email(current_email, new_email))


async def _change_email(current_email: str, new_email: str) -> None:
    from sqlalchemy import func, select

    from app.core.database.database import async_session_factory
    from app.models.user import User

    current = current_email.strip().lower()
    new = new_email.strip().lower()

    if "@" not in new:
        typer.echo(f"'{new_email}' is not a valid email address.")
        raise typer.Exit(code=1)
    if new == current:
        typer.echo("New email is the same as the current one; nothing to do.")
        raise typer.Exit(code=1)

    async with async_session_factory() as session:
        # Look up the target account (emails are stored lowercase).
        user = await session.scalar(select(User).where(func.lower(User.email) == current))
        if user is None:
            typer.echo(f"No user found with email '{current_email}'.")
            raise typer.Exit(code=1)

        # Refuse to collide with an existing account.
        clash = await session.scalar(
            select(User.id).where(func.lower(User.email) == new, User.id != user.id)
        )
        if clash is not None:
            typer.echo(f"Another account already uses '{new_email}'.")
            raise typer.Exit(code=1)

        user.email = new
        await session.commit()
        typer.echo(f"Updated email: {current} -> {new}")


if __name__ == "__main__":
    app()
