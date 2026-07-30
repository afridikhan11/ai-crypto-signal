from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.redis import get_redis
from app.core.logging import logger
from redis.asyncio import Redis

router = APIRouter()


@router.get("/health")
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        logger.error(f"Database health check error: {e}")
        db_status = "error"

    try:
        await redis.ping()
        redis_status = "ok"
    except Exception as e:
        logger.error(f"Redis health check error: {e}")
        redis_status = "error"

    # Real status of the AI module's own Binance public market-data
    # WebSocket (separate from the read-only Account module). "unknown"
    # before the scanner has finished starting up.
    scanner = getattr(request.app.state, "scanner", None)
    if scanner is not None and getattr(scanner, "data_manager", None) is not None:
        binance_status = "ok" if scanner.data_manager.is_connected else "error"
    else:
        binance_status = "unknown"

    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "database": db_status,
        "redis": redis_status,
        "binance": binance_status,
    }