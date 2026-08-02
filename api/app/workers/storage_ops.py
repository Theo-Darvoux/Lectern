import logging

from app.core.storage.facade import delete_object

logger = logging.getLogger(__name__)


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


async def release_cas_references(ctx: dict, references: list[dict]) -> None:  # type: ignore[type-arg]
    """Release explicit CAS references; physical deletion is left to safe GC."""
    redis = ctx.get("redis")
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client
    from app.core.security.cas import decrement_cas_ref

    errors: list[Exception] = []
    for reference in references:
        sha256 = str(reference["sha256"])
        try:
            await decrement_cas_ref(
                redis, sha256, operation_id=str(reference["operation_id"])
            )
        except Exception as exc:
            logger.error("Failed to release CAS reference %.16s…: %s", sha256, exc)
            errors.append(exc)
    if errors:
        raise ExceptionGroup("One or more CAS references could not be released", errors)


async def delete_storage_objects(ctx: dict, keys: list[str]) -> None:  # type: ignore[type-arg]
    """Delete a list of object keys from S3-compatible storage.

    CAS keys are deliberately rejected: their object IDs are HMAC digests and
    cannot be used as the original SHA-256 needed by the reference-count API.
    """
    errors: list[Exception] = []
    for key in keys:
        try:
            if key.startswith("cas/"):
                raise ValueError("CAS deletion requires the original SHA-256 reference key")
            await delete_object(key)
            logger.info("Deleted storage object: %s", key)

        except Exception as e:
            logger.error("Failed to delete storage object %s: %s", key, e)
            errors.append(e)
    if errors:
        raise ExceptionGroup("One or more storage objects could not be deleted", errors)


async def release_upload_quota(
    ctx: dict, user_id: str, members: list[str]
) -> None:  # type: ignore[type-arg]
    """Remove synthetic upload reservations after their DB owners are deleted."""
    redis = ctx.get("redis")
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client
    if members:
        await redis.zrem(f"quota:uploads:{user_id}", *members)
