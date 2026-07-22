import redis.asyncio as aioredis
from app.core.config import get_settings

settings = get_settings()

redis_client = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    max_connections=10,
)

async def get_redis():
    yield redis_client
