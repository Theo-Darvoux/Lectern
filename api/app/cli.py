import asyncio

import typer

app = typer.Typer(help="WikINT CLI")


@app.command()
def seed(
    email: str = typer.Option(..., help="Email for the first Bureau account"),
    role: str = typer.Option("bureau", help="Role to assign"),
) -> None:
    asyncio.run(_seed(email, role))


async def _seed(email: str, role: str) -> None:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.user import User, UserRole

    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.email == email))
        user = user_res.scalar_one_or_none()

        role_enum = UserRole(role)
        if user is not None:
            user.role = role_enum
            typer.echo(f"Updated {email} to role '{role}'")
        else:
            user = User(email=email, role=role_enum)
            session.add(user)
            typer.echo(f"Created user {email} with role '{role}'")

        await session.commit()
        typer.echo("Seed complete.")


@app.command()
def reindex() -> None:
    asyncio.run(_reindex())


async def _reindex() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import async_session_factory
    from app.core.meilisearch import meili_client, setup_meilisearch
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
            await meili_client.index("materials").add_documents(m_docs)
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
            await meili_client.index("directories").add_documents(d_docs)
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

    from app.core.cas import hmac_cas_key, increment_cas_ref
    from app.core.database import async_session_factory
    from app.core.redis import redis_client
    from app.core.storage import copy_object, init_s3_client, object_exists
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
                    await increment_cas_ref(redis_client, sha256)

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
    batch_size: int = typer.Option(50, help="Number of files to process"),
    dry_run: bool = typer.Option(True, help="Preview changes without writing"),
    force: bool = typer.Option(False, help="Regenerate even if thumbnail exists"),
) -> None:
    """Regenerate missing thumbnails for all existing materials."""
    asyncio.run(_recalculate_thumbnails(batch_size, dry_run, force))


async def _recalculate_thumbnails(batch_size: int, dry_run: bool, force: bool) -> None:
    import shutil
    import tempfile
    from pathlib import Path

    from sqlalchemy import func, or_, select, update

    from app.core.database import async_session_factory
    from app.core.processing import ProcessingFile
    from app.core.storage import download_file, init_s3_client, upload_file
    from app.models.material import MaterialVersion
    from app.services.auth import get_full_auth_config
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    await init_s3_client()

    async with async_session_factory() as db:
        auth_config = await get_full_auth_config(db)

        query = select(MaterialVersion)
        if not force:
            # Target materials with no thumbnail OR that previously failed generation.
            query = query.where(
                or_(
                    MaterialVersion.thumbnail_key.is_(None),
                    MaterialVersion.thumbnail_status == "failed",
                )
            )

        total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0

        typer.echo(
            f"Found {total} material(s) {'missing/failed' if not force else 'queued for'} thumbnails."
        )
        if total == 0:
            return

        rows = (await db.execute(query.limit(batch_size))).scalars().all()

    processed = 0
    generated = 0
    errors = 0

    for mv in rows:
        typer.echo(f"Processing {mv.id} ({mv.file_name})...")
        if dry_run:
            processed += 1
            continue

        if not mv.file_name or not mv.file_mime_type or not mv.file_key:
            typer.echo(f"Skipping {mv.id} due to missing file details (name, mime type, or key)")
            errors += 1
            continue

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            # 1. Download source
            local_path = tmp_dir / mv.file_name
            await download_file(mv.file_key, local_path)

            # 2. Setup processing file
            pf = ProcessingFile(local_path, local_path.stat().st_size)

            # 3. Generate thumbnail (pass auth_config for consistent size/quality)
            thumb_path_str = await run_thumbnail_stage(
                pf,
                mv.file_mime_type,
                mv.file_name,
                config=auth_config,  # type: ignore[arg-type]
            )

            if thumb_path_str:
                thumb_path = Path(thumb_path_str)
                s3_thumb_key = f"thumbnails/{mv.id}.webp"

                with open(thumb_path, "rb") as f:
                    await upload_file(f.read(), s3_thumb_key, content_type="image/webp")

                async with async_session_factory() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == mv.id)
                        .values(thumbnail_key=s3_thumb_key, thumbnail_status="ok")
                    )
                    await db.commit()

                generated += 1
                typer.echo(f"  OK: {s3_thumb_key}")
            else:
                # Unsupported type — mark skipped so re-runs don't retry unnecessarily.
                async with async_session_factory() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == mv.id)
                        .values(thumbnail_status="skipped")
                    )
                    await db.commit()
                typer.echo("  SKIP: No thumbnail generated for this type.")

            processed += 1
        except Exception as e:
            typer.echo(f"  ERROR: {e}")
            errors += 1
            # Persist the failure so the CLI can target just failed materials on retry.
            try:
                async with async_session_factory() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == mv.id)
                        .values(thumbnail_status="failed")
                    )
                    await db.commit()
            except Exception:
                pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    typer.echo(f"\nDone. Processed: {processed}, Generated: {generated}, Errors: {errors}")
    if dry_run:
        typer.echo("Run with --no-dry-run to apply changes.")


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

    from app.core.database import async_session_factory

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
    from app.core.redis import redis_client
    from app.services.auth import generate_magic_token, store_magic_token

    token = generate_magic_token()
    await store_magic_token(redis_client, email, token)

    link = f"{settings.frontend_url}/login/verify?token={token}"
    typer.echo(f"Magic link for {email}:")
    typer.echo(link)


if __name__ == "__main__":
    app()
