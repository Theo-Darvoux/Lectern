"""Generation counter for data derived from the directory tree.

Directory preview collages are computed over a whole subtree, which is too
costly to redo on every listing, so they are cached in Redis under the current
generation. Any committed change that can alter them (a material or directory
created, deleted, moved, renamed or re-ranked) bumps the generation, which
orphans every cached entry at once. Entries also expire on their own, so a
change made where Redis is unreachable is picked up after the TTL.

The tracking is done with session events, so every write path (routers,
services, workers) is covered without having to remember to invalidate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction

logger = logging.getLogger(__name__)

CONTENT_GENERATION_KEY = "browse:content-gen"
_CHANGED_FLAG = "browse_content_changed"

# Attributes whose change can alter which materials a subtree previews.
_MATERIAL_ATTRS = ("directory_id", "parent_material_id", "deleted_at", "status", "title")
_DIRECTORY_ATTRS = ("parent_id", "deleted_at")

_pending_bumps: set[asyncio.Task[None]] = set()


def _tracked_attrs(obj: object) -> tuple[str, ...] | None:
    from app.models.directory import Directory
    from app.models.material import Material

    if isinstance(obj, Material):
        return _MATERIAL_ATTRS
    if isinstance(obj, Directory):
        return _DIRECTORY_ATTRS
    return None


@event.listens_for(Session, "after_flush")
def _track_flush(session: Session, _flush_context: UOWTransaction) -> None:
    if session.info.get(_CHANGED_FLAG):
        return
    for obj in (*session.new, *session.deleted):
        if _tracked_attrs(obj) is not None:
            session.info[_CHANGED_FLAG] = True
            return
    for obj in session.dirty:
        attrs = _tracked_attrs(obj)
        if attrs is None:
            continue
        state = inspect(obj)
        if any(state.attrs[name].history.has_changes() for name in attrs):
            session.info[_CHANGED_FLAG] = True
            return


@event.listens_for(Session, "do_orm_execute")
def _track_bulk_write(execute_state: ORMExecuteState) -> None:
    if not (execute_state.is_update or execute_state.is_delete):
        return
    mapper = execute_state.bind_mapper
    if mapper is None:
        return
    from app.models.directory import Directory
    from app.models.material import Material

    if mapper.class_ in (Material, Directory):
        execute_state.session.info[_CHANGED_FLAG] = True


@event.listens_for(Session, "after_commit")
def _bump_after_commit(session: Session) -> None:
    if not session.info.pop(_CHANGED_FLAG, False):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(bump_content_generation())
    _pending_bumps.add(task)
    task.add_done_callback(_pending_bumps.discard)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    session.info.pop(_CHANGED_FLAG, None)


def _redis(redis: Any = None) -> Any:
    if redis is not None:
        return redis
    from app.core.database import redis as redis_module

    return redis_module.redis_client


async def bump_content_generation(redis: Any = None) -> None:
    try:
        await _redis(redis).incr(CONTENT_GENERATION_KEY)
    except Exception as exc:
        logger.debug("Could not bump the browse content generation: %s", exc)


async def get_content_generation(redis: Any = None) -> str | None:
    """The current generation, or None when Redis is unreachable (skip caching)."""
    try:
        value = await _redis(redis).get(CONTENT_GENERATION_KEY)
    except Exception as exc:
        logger.debug("Could not read the browse content generation: %s", exc)
        return None
    if value is None:
        return "0"
    return value.decode() if isinstance(value, bytes) else str(value)
