import json
from typing import Any

from app.config import settings
from app.core.events.sse import broadcast_to_topic, uploads_topic


class UploadCacheRepository:
    """Encapsulates Redis operations for upload pipeline."""

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def emit_event(
        self,
        status_key: str,
        user_id: str,
        payload_json: str,
    ) -> None:
        """Store the latest status, then tell the uploader's browsers about it.

        The stored status is authoritative. The live event is best-effort, so a
        client that misses it reconciles through the status endpoint.
        """
        await self.redis.set(
            status_key,
            payload_json,
            ex=settings.cache_ttl_seconds if hasattr(settings, "cache_ttl_seconds") else 3600,
        )
        broadcast_to_topic(uploads_topic(user_id), {"type": "upload", **json.loads(payload_json)})

    async def is_cancelled(self, cancel_key: str) -> bool:
        return int(await self.redis.exists(cancel_key)) > 0
