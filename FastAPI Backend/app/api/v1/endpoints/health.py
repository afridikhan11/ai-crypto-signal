from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.redis import get_redis
from redis.asyncio import Redis

router = APIRouter()


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        print(f"Database health check error: {e}")
        db_status = "error"

    try:
        await redis.ping()
        redis_status = "ok"
    except Exception as e:
        print(f"Redis health check error: {e}")
        redis_status = "error"

    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "database": db_status,
        "redis": redis_status,
    }