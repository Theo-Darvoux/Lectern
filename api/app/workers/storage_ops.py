import logging

from app.core.database.post_commit import (
    acknowledge_outbox_completion,
    record_outbox_execution_failure,
)
from app.core.storage.facade import delete_object

logger = logging.getLogger(__name__)


def _completion_session_factory(ctx: dict, outbox_id: str | None):  # type: ignore[type-arg]
    if outbox_id is None:
        return None
    session_factory = ctx.get("db_sessionmaker")
    if session_factory is None:
        raise RuntimeError("Completion-tracked storage job has no DB session factory")
    return session_factory


async def _acknowledge_completion(session_factory: object, outbox_id: str) -> None:
    acknowledged = await acknowledge_outbox_completion(session_factory, outbox_id)
    if not acknowledged:
        raise RuntimeError(f"Unable to acknowledge storage outbox row {outbox_id}")


async def _record_completion_failure(
    session_factory: object, outbox_id: str, exc: BaseException
) -> None:
    try:
        await record_outbox_execution_failure(session_factory, outbox_id, exc)
    except Exception:
        logger.exception("Failed to persist storage worker failure for outbox %s", outbox_id)


async def add_cas_references(ctx: dict, references: list[dict]) -> None:  # type: ignore[type-arg]
    """Create/increment explicit CAS refs from transactionally queued metadata."""
    redis = ctx.get("redis")
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client
    from app.core.security.cas import increment_cas_ref

    errors: list[Exception] = []
    for reference in references:
        try:
            await increment_cas_ref(
                redis,
                str(reference["sha256"]),
                initial_data=dict(reference["initial_data"]),
                operation_id=str(reference["operation_id"]),
            )
        except Exception as exc:
            logger.error("Failed to add CAS reference: %s", exc)
            errors.append(exc)
    if errors:
        raise ExceptionGroup("One or more CAS references could not be added", errors)


async def release_cas_references(
    ctx: dict,  # type: ignore[type-arg]
    references: list[dict],
    *,
    outbox_id: str | None = None,
) -> None:
    """Release explicit CAS references and acknowledge only after every release succeeds."""
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        redis = ctx.get("redis")
        if redis is None:
            from app.core.database.redis import redis_client

            redis = redis_client
        from app.core.security.cas import CasReferenceMissingError, decrement_cas_ref

        errors: list[Exception] = []
        for reference in references:
            sha256 = str(reference["sha256"])
            try:
                await decrement_cas_ref(redis, sha256, operation_id=str(reference["operation_id"]))
            except CasReferenceMissingError:
                # Redis CAS refs are an evictable coordination cache. If the key is
                # absent, there is no live ref to leak and destructive cleanup was already avoided.
                logger.debug(
                    "CAS reference %.16s… already absent during release; skipping decrement",
                    sha256,
                )
            except Exception as exc:
                logger.error("Failed to release CAS reference %.16s…: %s", sha256, exc)
                errors.append(exc)
        if errors:
            raise ExceptionGroup("One or more CAS references could not be released", errors)

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_completion_failure(session_factory, outbox_id, exc)
        raise


async def delete_storage_objects(
    ctx: dict,  # type: ignore[type-arg]
    keys: list[str],
    reservation_ids: list[str] | None = None,
    promoted_legacy: bool = False,
    *,
    outbox_id: str | None = None,
) -> None:
    """Delete a list of object keys from S3-compatible storage.

    CAS keys are deliberately rejected: their object IDs are HMAC digests and
    cannot be used as the original SHA-256 needed by the reference-count API.
    """
    session_factory = _completion_session_factory(ctx, outbox_id)
    try:
        errors: list[Exception] = []
        for key in keys:
            try:
                if key.startswith("cas/"):
                    raise ValueError("CAS deletion requires the original SHA-256 reference key")
                await delete_object(key)
                logger.info("Deleted storage object: %s", key)

            except Exception as exc:
                logger.error("Failed to delete storage object %s: %s", key, exc)
                errors.append(exc)
        if errors:
            raise ExceptionGroup("One or more storage objects could not be deleted", errors)
        if reservation_ids:
            # Keep reservations until every object deletion succeeds. Approved
            # legacy promotions additionally fence stale DB snapshots atomically
            # with reservation release.
            await release_storage_reservations(
                ctx,
                {
                    "reservation_ids": reservation_ids,
                    "refresh_legacy_usage": promoted_legacy,
                },
            )

        if outbox_id is not None:
            assert session_factory is not None
            await _acknowledge_completion(session_factory, outbox_id)
    except Exception as exc:
        if outbox_id is not None and session_factory is not None:
            await _record_completion_failure(session_factory, outbox_id, exc)
        raise


async def release_upload_quota(ctx: dict, user_id: str, members: list[str]) -> None:  # type: ignore[type-arg]
    """Remove synthetic upload reservations after their DB owners are deleted."""
    redis = ctx.get("redis")
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client
    if members:
        await redis.zrem(f"quota:uploads:{user_id}", *members)


async def release_storage_reservations(
    ctx: dict,  # type: ignore[type-arg]
    payload: dict | list[dict],
) -> None:
    """Release reservations, fencing stale legacy snapshots for promoted objects."""
    redis = ctx.get("redis")
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client

    from app.core.storage.capacity import (
        release_promoted_legacy_storage_reservation,
        release_storage_reservation,
    )

    items = payload if isinstance(payload, list) else [payload]
    errors: list[Exception] = []
    for item in items:
        promoted_legacy = bool(item.get("refresh_legacy_usage"))
        reservation_ids = item.get("reservation_ids") or []
        for reservation_id in reservation_ids:
            try:
                if promoted_legacy:
                    await release_promoted_legacy_storage_reservation(reservation_id, redis)
                else:
                    await release_storage_reservation(reservation_id, redis)
            except Exception as exc:
                logger.error("Failed to release storage reservation %s: %s", reservation_id, exc)
                errors.append(exc)
    if errors:
        raise ExceptionGroup("One or more storage reservations could not be released", errors)
