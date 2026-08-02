from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

storage_uri = "memory://" if settings.environment == "test" else settings.redis_url

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=storage_uri,
    default_limits=["60/minute"],
    enabled=not settings.is_dev,
)
