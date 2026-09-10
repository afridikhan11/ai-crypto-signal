"""
GET /health - is this process doing its job, right now?

The original version reported database / redis / binance-websocket
reachability. Every one of those said "ok" on 2026-09-10 while the bot had
not monitored a single signal for seven hours: a relationship added to
`Signal` pointed at a class the application never imported, so every ORM
query raised at mapper-configuration time. The suite passed 1159 tests, the
deploy reported success, and `docker compose ps` said `Up`.

`Up` is not health, and neither is `SELECT 1` - a raw text query never
touches a mapper, so it answered "ok" throughout the outage. Two checks are
therefore added to the three that were already here, and the database probe
is upgraded to one that actually configures the ORM:

  - the database probe now selects from a MAPPED entity, which forces
    `configure_mappers()` - the exact step that failed;
  - `scanner` and `signal_monitor` report how long ago each loop last
    COMPLETED real work (see app/core/liveness.py). An asyncio task that
    raises every cycle is not cancelled, it just retries forever - which is
    the shape this outage took, and which no "is the task alive" check can
    see.

The response now returns 503 when anything is unhealthy, so a machine can
act on it rather than a human reading logs: Docker's own healthcheck
restarts the container and `scripts/smoke_check.sh` fails the deploy.

Every original response key is preserved with its original meaning, so any
existing consumer keeps working. Deliberately unauthenticated and cheap - it
must work when the app is too broken to authenticate.
"""
from fastapi import APIRouter, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select

from app.core import liveness
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.core.redis import get_redis
from fastapi import Depends

router = APIRouter()


async def _orm_ok() -> tuple[bool, str]:
    """A real query through the real session factory, against a MAPPED entity.

    `SELECT 1` would not have caught the 2026-09-10 outage: raw text never
    resolves a mapper. Selecting `Signal.id` does.
    """
    try:
        from app.models.signal import Signal

        async with AsyncSessionLocal() as session:
            await session.execute(select(Signal.id).limit(1))
        return True, "ok"
    except Exception as e:  # noqa: BLE001 - reporting health must not raise
        logger.error(f"Database health check error: {e}")
        return False, f"{type(e).__name__}: {e}"[:300]


@router.get("/health")
async def health_check(
    request: Request,
    response: Response,
    redis: Redis = Depends(get_redis),
):
    db_healthy, db_detail = await _orm_ok()
    db_status = "ok" if db_healthy else "error"

    try:
        await redis.ping()
        redis_status = "ok"
    except Exception as e:  # noqa: BLE001
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

    scanner_health = liveness.component_status(liveness.SCANNER)
    monitor_health = liveness.component_status(liveness.MONITOR)

    # `binance` is excluded on purpose: this deployment runs with
    # BINANCE_WS_ENABLED=false and REST polling, so its websocket is
    # legitimately down. It stays reported, but it does not fail the check.
    healthy = (
        db_healthy
        and redis_status == "ok"
        and scanner_health["healthy"]
        and monitor_health["healthy"]
    )
    if not healthy:
        response.status_code = 503

    return {
        # ---- original keys, unchanged meaning ----
        "status": "ok" if healthy else "degraded",
        "database": db_status,
        "redis": redis_status,
        "binance": binance_status,
        # ---- added 2026-09-10 ----
        "healthy": healthy,
        "uptime_seconds": round(liveness.uptime_seconds(), 1),
        "database_detail": db_detail,
        "scanner": scanner_health,
        "signal_monitor": monitor_health,
    }
