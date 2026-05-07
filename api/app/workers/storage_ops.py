import logging

from app.core.storage import delete_object

logger = logging.getLogger("wikint")


async def delete_storage_objects(ctx: dict, keys: list[str]) -> None:  # type: ignore[type-arg]
    """Delete a list of object keys from S3-compatible storage.

    For keys in the cas/ prefix, this decrements the reference count in Redis
    and only performs the actual S3 DELETE if the count reaches zero.
    """
    redis = ctx.get("redis")
    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    from app.core.cas import _LUA_CAS_DECR

    for key in keys:
        try:
            # 1. Handle shared CAS objects (managed via reference counting)
            if key.startswith("cas/"):
                # The cas_id is the last segment of the key
                cas_id = key.split("/")[-1]
                cas_key = f"upload:cas:{cas_id}"

                # Atomically decrement and check count via Lua
                new_count = await redis.eval(_LUA_CAS_DECR, 1, cas_key)

                if new_count == 0:
                    await delete_object(key)
                    logger.info("Deleted CAS object (ref_count reached 0): %s", key)
                else:
                    logger.info("Decremented CAS ref_count for %s (new_count=%d)", key, new_count)

            # 2. Handle standard user-owned objects (simple delete)
            else:
                await delete_object(key)
                logger.info("Deleted storage object: %s", key)

        except Exception as e:
            logger.error("Failed to delete storage object %s: %s", key, e)
