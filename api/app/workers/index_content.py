import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.core.database.database as db_core
from app.core.database.post_commit import (
    acknowledge_outbox_completion,
    record_outbox_execution_failure,
)
from app.core.events.meilisearch import meili_admin_client
from app.models.directory import Directory
from app.models.material import Material
from app.services.search_documents import build_directory_search_document as _build_directory_doc
from app.services.search_documents import build_material_search_document as _build_material_doc

logger = logging.getLogger(__name__)

_INDEX_ORDER_LOCK_PERSON = b"WikINTIndexV1"


def _index_order_lock_key(index_name: str, item_id: uuid.UUID) -> int:
    """Return a stable signed 64-bit advisory-lock key for one search document."""
    digest = hashlib.blake2b(
        f"{index_name}:{item_id}".encode(),
        digest_size=8,
        person=_INDEX_ORDER_LOCK_PERSON,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _acquire_index_order_locks(
    db: AsyncSession, index_name: str, item_ids: list[uuid.UUID] | set[uuid.UUID]
) -> None:
    """Serialize all writers for the same Meilisearch document before DB snapshotting.

    PostgreSQL transaction-scoped advisory locks are deliberately worker-only: they
    do not block application writes, but every single and batch index worker uses
    the same key and deterministic ordering.  Therefore an older worker cannot
    resume after a newer worker and overwrite the newer authoritative snapshot.
    The lock is acquired before the PostgreSQL read and held through Meilisearch
    task completion by the worker session transaction.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    for item_id in sorted(set(item_ids), key=lambda value: value.int):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _index_order_lock_key(index_name, item_id)},
        )


async def _submit_index_documents(index_name: str, docs: list[dict]) -> None:  # type: ignore[type-arg]
    """Submit a normal index update and return only after Meilisearch commits it."""
    if not docs:
        return
    task = await meili_admin_client.index(index_name).add_documents(docs)
    await _wait_for_meili_task(task)


def _completion_session_factory(ctx: dict, outbox_id: str | None):  # type: ignore[type-arg]
    if outbox_id is None:
        return None
    session_factory = ctx.get("db_sessionmaker")
    if session_factory is None:
        raise RuntimeError("Completion-tracked index job has no DB session factory")
    return session_factory


async def _acknowledge_index_completion(session_factory: object, outbox_id: str) -> None:
    acknowledged = await acknowledge_outbox_completion(session_factory, outbox_id)
    if not acknowledged:
        raise RuntimeError(f"Unable to acknowledge index outbox row {outbox_id}")


async def _record_index_failure(
    session_factory: object, outbox_id: str, exc: BaseException
) -> None:
    try:
        await record_outbox_execution_failure(session_factory, outbox_id, exc)
    except Exception:
        logger.exception("Failed to persist index worker failure for outbox %s", outbox_id)


async def index_material(
    ctx: dict,  # type: ignore[type-arg]
    material_id: uuid.UUID | str,
    *,
    outbox_id: str | None = None,
) -> None:
    """Index or update a single material and durably acknowledge Meili completion."""
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        normalized_id = uuid.UUID(str(material_id))
        async with db_core.async_session_factory() as db:
            await _acquire_index_order_locks(db, "materials", [normalized_id])
            result = await db.execute(
                select(Material)
                .options(
                    selectinload(Material.tags),
                    selectinload(Material.author),
                    selectinload(Material.versions),
                )
                .where(Material.id == normalized_id, Material.deleted_at.is_(None))
            )
            material = result.scalar_one_or_none()
            if not material:
                logger.warning("Material %s not found for indexing.", material_id)
            else:
                from app.services.directory import get_directory_path

                ancestor_path = ""
                browse_path = "/browse"
                if material.directory_id:
                    path_parts = await get_directory_path(db, material.directory_id)
                    if path_parts:
                        ancestor_path = " ".join(p["name"] for p in path_parts)
                        browse_path += "/" + "/".join(p["slug"] for p in path_parts)
                browse_path += f"/{material.slug}"

                doc = _build_material_doc(material, ancestor_path, browse_path)
                await _submit_index_documents("materials", [doc])
                logger.info("Indexed material %s", material_id)

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_index_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_index_failure(session_factory, outbox_id, exc)
        raise


async def index_materials_batch(
    ctx: dict,  # type: ignore[type-arg]
    material_ids: list[uuid.UUID | str],
    *,
    outbox_id: str | None = None,
) -> None:
    """Index multiple materials and acknowledge only after the Meili task succeeds."""
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        normalized_ids = [uuid.UUID(str(material_id)) for material_id in material_ids]
        if normalized_ids:
            async with db_core.async_session_factory() as db:
                await _acquire_index_order_locks(db, "materials", normalized_ids)
                result = await db.execute(
                    select(Material)
                    .options(
                        selectinload(Material.tags),
                        selectinload(Material.author),
                        selectinload(Material.versions),
                    )
                    .where(Material.id.in_(normalized_ids), Material.deleted_at.is_(None))
                )
                materials = result.scalars().all()

                if materials:
                    from app.services.directory import get_ancestor_map

                    dir_ids = {m.directory_id for m in materials if m.directory_id}
                    ancestor_map = await get_ancestor_map(db, dir_ids) if dir_ids else {}

                    docs = []
                    for material in materials:
                        ancestor_path = ""
                        browse_path = "/browse"
                        if material.directory_id:
                            paths = ancestor_map.get(material.directory_id)
                            if paths:
                                ancestor_path, slug_path = paths
                                browse_path += "/" + slug_path
                        browse_path += f"/{material.slug}"
                        docs.append(_build_material_doc(material, ancestor_path, browse_path))

                    await _submit_index_documents("materials", docs)
                    logger.info("Batch-indexed %d materials", len(docs))

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_index_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_index_failure(session_factory, outbox_id, exc)
        raise


async def index_directory(
    ctx: dict,  # type: ignore[type-arg]
    directory_id: uuid.UUID | str,
    *,
    outbox_id: str | None = None,
) -> None:
    """Index or update one directory and durably acknowledge Meili completion."""
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        normalized_id = uuid.UUID(str(directory_id))
        async with db_core.async_session_factory() as db:
            await _acquire_index_order_locks(db, "directories", [normalized_id])
            result = await db.execute(
                select(Directory)
                .options(selectinload(Directory.tags))
                .where(
                    Directory.id == normalized_id,
                    Directory.deleted_at.is_(None),
                )
            )
            directory = result.scalar_one_or_none()
            if not directory:
                logger.warning("Directory %s not found for indexing.", directory_id)
            else:
                from app.services.directory import get_directory_path

                ancestor_path = ""
                browse_path = "/browse"
                if directory.parent_id:
                    path_parts = await get_directory_path(db, directory.parent_id)
                    if path_parts:
                        ancestor_path = " ".join(p["name"] for p in path_parts)
                        browse_path += "/" + "/".join(p["slug"] for p in path_parts)
                browse_path += f"/{directory.slug}"

                doc = _build_directory_doc(directory, ancestor_path, browse_path)
                await _submit_index_documents("directories", [doc])
                logger.info("Indexed directory %s", directory_id)

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_index_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_index_failure(session_factory, outbox_id, exc)
        raise


async def index_directories_batch(
    ctx: dict,  # type: ignore[type-arg]
    directory_ids: list[uuid.UUID | str],
    *,
    outbox_id: str | None = None,
) -> None:
    """Index multiple directories and acknowledge only after Meili completion."""
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        normalized_ids = [uuid.UUID(str(directory_id)) for directory_id in directory_ids]
        if normalized_ids:
            async with db_core.async_session_factory() as db:
                await _acquire_index_order_locks(db, "directories", normalized_ids)
                result = await db.execute(
                    select(Directory)
                    .options(selectinload(Directory.tags))
                    .where(Directory.id.in_(normalized_ids), Directory.deleted_at.is_(None))
                )
                directories = result.scalars().all()

                if directories:
                    from app.services.directory import get_ancestor_map

                    parent_ids = {d.parent_id for d in directories if d.parent_id}
                    ancestor_map = await get_ancestor_map(db, parent_ids) if parent_ids else {}

                    docs = []
                    for directory in directories:
                        ancestor_path = ""
                        browse_path = "/browse"
                        if directory.parent_id:
                            paths = ancestor_map.get(directory.parent_id)
                            if paths:
                                ancestor_path, slug_path = paths
                                browse_path += "/" + slug_path
                        browse_path += f"/{directory.slug}"
                        docs.append(_build_directory_doc(directory, ancestor_path, browse_path))

                    await _submit_index_documents("directories", docs)
                    logger.info("Batch-indexed %d directories", len(docs))

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_index_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_index_failure(session_factory, outbox_id, exc)
        raise


async def _wait_for_meili_task(task: object) -> None:
    task_uid = getattr(task, "task_uid", None)
    if not isinstance(task_uid, int):
        raise RuntimeError("Meilisearch mutation did not return a valid task id")
    # Meilisearch mutations are asynchronous. Queue acceptance is not durable
    # completion; only acknowledge the DB outbox after the task itself succeeds.
    await meili_admin_client.wait_for_task(
        task_uid,
        timeout_in_ms=30_000,
        raise_for_status=True,
    )


async def _delete_search_documents_if_still_deleted(
    session_factory: Callable[[], AsyncSession],
    index_name: str,
    model: type[Material] | type[Directory],
    item_ids: list[str],
    *,
    prefer_batch: bool = False,
    on_complete: Callable[[], Awaitable[None]] | None = None,
) -> tuple[list[str], list[str]]:
    """Delete only search documents whose PostgreSQL state still permits it.

    Candidate discovery is never an authority boundary. Immediately before the
    remote mutation, this helper re-reads every parseable application row with
    ``include_deleted=True`` and locks existing rows in deterministic UUID order.
    Live/restored rows are discarded. Locks for still-deleted rows are held
    through the Meilisearch task completion (and optional durable acknowledgement),
    so restore/reindex is linearly ordered after deletion when deletion wins.

    Missing/malformed document IDs have no restorable application row to lock and
    are safe to remove from the derived index. The returned tuple is
    ``(deleted_ids, superseded_live_ids)``.
    """
    if not item_ids:
        if on_complete is not None:
            await on_complete()
        return [], []

    # Preserve Meilisearch document order while preventing duplicate remote work.
    candidates = list(dict.fromkeys(str(item_id) for item_id in item_ids))
    parsed_by_raw: dict[str, uuid.UUID | None] = {}
    valid_ids: set[uuid.UUID] = set()
    for raw in candidates:
        try:
            parsed = uuid.UUID(raw)
        except (TypeError, ValueError, AttributeError):
            parsed = None
        parsed_by_raw[raw] = parsed
        if parsed is not None:
            valid_ids.add(parsed)

    async with session_factory() as authority_db:
        rows_by_id: dict[uuid.UUID, object] = {}
        if valid_ids:
            rows = (
                await authority_db.execute(
                    select(model.id, model.deleted_at)
                    .where(model.id.in_(valid_ids))
                    .order_by(model.id)
                    .execution_options(include_deleted=True)
                    .with_for_update()
                )
            ).all()
            rows_by_id = {row.id: row.deleted_at for row in rows}

        deleted_ids: list[str] = []
        superseded_ids: list[str] = []
        for raw in candidates:
            parsed = parsed_by_raw[raw]
            if parsed is not None and parsed in rows_by_id and rows_by_id[parsed] is None:
                superseded_ids.append(raw)
            else:
                deleted_ids.append(raw)

        if deleted_ids:
            index = meili_admin_client.index(index_name)
            if prefer_batch:
                task = await index.delete_documents(deleted_ids)
            else:
                if len(deleted_ids) != 1:
                    raise RuntimeError(
                        "Single-document deindex path received multiple search document IDs"
                    )
                task = await index.delete_document(deleted_ids[0])
            await _wait_for_meili_task(task)

        # The callback is deliberately inside the authority transaction. For
        # completion-tracked outbox work this retains the v2 guarantee that the
        # target row lock is held until the remote effect is durably acknowledged.
        if on_complete is not None:
            await on_complete()
        await authority_db.commit()

    return deleted_ids, superseded_ids


async def delete_indexed_item(
    ctx: dict,  # type: ignore[type-arg]
    index_name: str,
    item_id: str,
    *,
    outbox_id: str | None = None,
) -> None:
    """Delete an item iff PostgreSQL still authoritatively says it is deleted.

    Both normal outbox execution and scheduled reconciliation use the same
    restoration-aware locking primitive, so no stale deletion intent can erase a
    document after its application row has been restored.
    """
    model_by_index: dict[str, type[Material] | type[Directory]] = {
        "materials": Material,
        "directories": Directory,
    }
    model = model_by_index.get(index_name)
    if model is None:
        raise ValueError(f"Unsupported search index for deindex: {index_name!r}")

    session_factory = ctx.get("db_sessionmaker")
    if outbox_id and session_factory is None:
        raise RuntimeError("Completion-tracked deindex job has no DB session factory")

    async def _acknowledge() -> None:
        if outbox_id is None:
            return
        acknowledged = await acknowledge_outbox_completion(session_factory, outbox_id)
        if not acknowledged:
            raise RuntimeError(f"Unable to acknowledge deindex outbox row {outbox_id}")

    try:
        if session_factory is not None:
            deleted_ids, superseded_ids = await _delete_search_documents_if_still_deleted(
                session_factory,
                index_name,
                model,
                [item_id],
                on_complete=_acknowledge if outbox_id else None,
            )
        else:
            # Explicit maintenance/smoke calls without an outbox may lack the
            # worker DB factory. Production completion-tracked jobs never do.
            task = await meili_admin_client.index(index_name).delete_document(item_id)
            await _wait_for_meili_task(task)
            deleted_ids, superseded_ids = [item_id], []
    except Exception as exc:
        logger.error("Failed to deindex %s from %s: %s", item_id, index_name, exc)
        if outbox_id and session_factory is not None:
            try:
                await record_outbox_execution_failure(session_factory, outbox_id, exc)
            except Exception:
                logger.exception(
                    "Failed to persist deindex worker failure for outbox %s", outbox_id
                )
        raise

    if superseded_ids:
        logger.info("Skipped obsolete deindex for restored %s %s", index_name, item_id)
    elif deleted_ids:
        logger.info("Deleted %s from %s", item_id, index_name)


async def _collect_orphan_search_ids(
    session_factory: Callable[[], AsyncSession],
    index_name: str,
    model: type[Material] | type[Directory],
    *,
    batch_size: int = 500,
) -> list[str]:
    """Return Meili document IDs that do not map to a live PostgreSQL row.

    The scan does not mutate Meilisearch until every page has been read, so
    offset pagination cannot skip documents because earlier pages shrink.
    PostgreSQL sessions are opened per page and closed before the next remote
    request, avoiding a database transaction held across a full index scan.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    index = meili_admin_client.index(index_name)
    offset = 0
    orphan_ids: list[str] = []

    while True:
        page = await index.get_documents(offset=offset, limit=batch_size)
        documents = list(page.results)
        if not documents:
            return orphan_ids

        parsed: list[tuple[str, uuid.UUID | None]] = []
        valid_ids: set[uuid.UUID] = set()
        for document in documents:
            raw_id = (
                document.get("id") if isinstance(document, dict) else getattr(document, "id", None)
            )
            if raw_id is None:
                logger.warning(
                    "Search document in %s has no primary-key id; cannot reconcile it", index_name
                )
                continue
            raw = str(raw_id)
            try:
                parsed_id = uuid.UUID(raw)
            except (TypeError, ValueError, AttributeError):
                parsed_id = None
            parsed.append((raw, parsed_id))
            if parsed_id is not None:
                valid_ids.add(parsed_id)

        live_ids: set[uuid.UUID] = set()
        if valid_ids:
            async with session_factory() as db:
                live_ids = set(await db.scalars(select(model.id).where(model.id.in_(valid_ids))))

        # Invalid UUID primary keys can never map to application rows and are
        # therefore stale by construction. Missing/deleted UUID rows are stale
        # because the global soft-delete criteria returns live rows only.
        orphan_ids.extend(
            raw for raw, parsed_id in parsed if parsed_id is None or parsed_id not in live_ids
        )

        offset += len(documents)
        if len(documents) < batch_size:
            return orphan_ids


async def _reconcile_search_index(
    session_factory: Callable[[], AsyncSession],
    index_name: str,
    model: type[Material] | type[Directory],
    *,
    batch_size: int = 500,
) -> int:
    orphan_ids = await _collect_orphan_search_ids(
        session_factory,
        index_name,
        model,
        batch_size=batch_size,
    )
    if not orphan_ids:
        return 0

    deleted_count = 0
    for offset in range(0, len(orphan_ids), batch_size):
        batch = orphan_ids[offset : offset + batch_size]
        # Candidate collection may be arbitrarily stale by the time a full index
        # scan finishes. Re-enter the same authority boundary as normal outbox
        # deindexing immediately before every remote deletion batch.
        deleted_ids, _superseded_ids = await _delete_search_documents_if_still_deleted(
            session_factory,
            index_name,
            model,
            batch,
            prefer_batch=True,
        )
        deleted_count += len(deleted_ids)

    return deleted_count


async def reconcile_search_documents(ctx: dict) -> None:  # type: ignore[type-arg]
    """Reconcile Meilisearch against authoritative live PostgreSQL rows.

    New deletions are protected by the completion-acknowledged outbox. This
    scheduled full-index reconciliation is the repair path for historical drift,
    including deindex rows that were cleaned up before completion acknowledgements
    existed and documents whose PostgreSQL row was later hard-deleted. The public
    read path independently filters/counts/paginates through PostgreSQL-live
    hits, so reconciliation latency cannot disclose stale document fields or
    stale Meilisearch-only result counts.
    """
    session_factory = ctx["db_sessionmaker"]
    first_error: Exception | None = None

    for index_name, model in (("materials", Material), ("directories", Directory)):
        try:
            count = await _reconcile_search_index(session_factory, index_name, model)
            if count:
                logger.info("Reconciled %d orphan documents from %s", count, index_name)
        except Exception as exc:
            # Try both indexes even if one is unhealthy, then fail the cron run
            # so operations still receive a visible error signal.
            logger.exception("Search reconciliation failed for %s", index_name)
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error
