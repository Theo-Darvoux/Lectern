import logging

from app.core.storage import delete_object

logger = logging.getLogger(__name__)


async def delete_storage_objects(ctx: dict, keys: list[str]) -> None:  # type: ignore[type-arg]
    """Delete a list of object keys from S3-compatible storage.

    For keys in the cas/ prefix, this decrements the reference count via
    decrement_cas_ref() (which runs the correct two-key Lua script) and only
    performs the actual S3 DELETE if the count reaches zero.
    """
    redis = ctx.get("redis")
    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    for key in keys:
        try:
            # 1. Handle shared CAS objects (managed via reference counting)
            if key.startswith("cas/"):
                from app.core.cas import decrement_cas_ref

                sha256 = key.split("/")[-1]
                new_count = await decrement_cas_ref(redis, sha256)
                if new_count == 0:
                    await delete_object(key)
                    logger.info("Deleted CAS object (ref_count reached 0): %s", key)
                elif new_count > 0:
                    logger.info("Decremented CAS ref_count for %s (new_count=%d)", key, new_count)
                # new_count == -1 means error already logged in decrement_cas_ref; skip S3 delete

            # 2. Handle standard user-owned objects (simple delete)
            else:
                await delete_object(key)
                logger.info("Deleted storage object: %s", key)

        except Exception as e:
            logger.error("Failed to delete storage object %s: %s", key, e)
