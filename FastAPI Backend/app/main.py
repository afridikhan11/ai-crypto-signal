import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import logger
from app.models.base import Base
from app.core.database import engine
from app.websocket.signal_ws import signal_ws_manager
import asyncio

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.include_router(api_router, prefix="/api/v1")


# Phase 8 (Beta Hardening, 2026-07-28): global exception handling.
#
# Every explicit `raise HTTPException(...)` across the endpoint files
# already returns FastAPI's standard `{"detail": ...}` JSON shape - that
# behavior is untouched. What was missing: any TRULY unexpected exception
# (a real bug, not a deliberately-raised HTTPException) fell through to
# Starlette's bare default, a plain-text "Internal Server Error" response
# with no JSON body at all - inconsistent with every other error response
# this API returns, and gave the caller nothing to log/correlate against
# the server-side traceback. This handler closes that gap without changing
# any existing endpoint's behavior: known errors (HTTPException,
# RequestValidationError) still behave exactly as before; only genuinely
# unhandled exceptions are affected, and they now get the same `{"detail":
# ...}` shape as everything else, plus a correlation id that's also
# written to the log line carrying the real traceback.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Re-emit FastAPI's own default shape explicitly, so this project has
    # exactly one code path producing HTTPException responses instead of
    # silently relying on a framework default that could change.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # FastAPI's existing 422 shape ({"detail": [...]}), re-emitted
    # explicitly for the same reason as above - no change in content.
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = str(uuid.uuid4())
    logger.error(
        f"Unhandled exception [{error_id}] on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}"
    )
    logger.exception(exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error. This has been logged.",
            "error_id": error_id,
        },
    )


def _run_production_readiness_checks() -> None:
    """Phase 8 (Beta Hardening, 2026-07-28): warn loudly about settings that
    are fine for local development but unsafe or broken for a real
    deployment. WARNINGS ONLY - this never raises or exits, so local
    development (which relies on these defaults - see .env) is completely
    unaffected. Mirrors exactly what .env.production.example already tells
    a deploying user to change; this just makes the gap visible in the logs
    too, in case that checklist gets missed.
    """
    checks_failed = 0

    if settings.secret_key == "change_me_in_production":
        checks_failed += 1
        logger.warning(
            "STARTUP CHECK: SECRET_KEY is still the placeholder default "
            "('change_me_in_production'). This key signs JWT auth tokens "
            "AND encrypts saved Binance API credentials (see "
            "app/security/api_key_cipher.py) - anyone with this value can "
            "forge a valid login token or decrypt stored exchange "
            "credentials. Fine for local development; generate a real one "
            "with scripts/generate_secret_key.py before deploying anywhere "
            "reachable outside this machine."
        )

    if not settings.require_auth:
        logger.warning(
            "STARTUP CHECK: REQUIRE_AUTH is disabled - every API endpoint "
            "is reachable with no login. Expected for local development "
            "(the WPF app doesn't send an Authorization header yet); set "
            "REQUIRE_AUTH=true before deploying anywhere reachable outside "
            "this machine (see .env.production.example)."
        )
    elif not settings.admin_password_hash:
        # REQUIRE_AUTH=true with no password hash set locks EVERYONE out,
        # including the legitimate admin - this is a broken config, not
        # just a security gap, so it's worth flagging distinctly.
        checks_failed += 1
        logger.warning(
            "STARTUP CHECK: REQUIRE_AUTH is enabled but ADMIN_PASSWORD_HASH "
            "is empty - no one will be able to log in, including you. "
            "Generate one with scripts/generate_password_hash.py and set "
            "it before relying on this deployment."
        )

    if "user:password@db" in settings.database_url:
        logger.warning(
            "STARTUP CHECK: DATABASE_URL is still using the local dev "
            "default credentials ('user:password@db'). Fine for local "
            "development; use a strong, unique database password before "
            "deploying anywhere reachable outside this machine (see "
            ".env.production.example)."
        )

    if checks_failed:
        logger.warning(
            f"STARTUP CHECK: {checks_failed} setting(s) above would leave a "
            f"real deployment insecure or non-functional if left as-is. "
            f"Local development is NOT affected - the app will continue "
            f"starting normally."
        )


@app.on_event("startup")
async def on_startup():
    logger.info("Starting application...")

    _run_production_readiness_checks()

    # RISK ENGINE 2.0.0 (Phase 3) - COHERENCE ASSERTION.
    # Fails startup LOUDLY if the configured risk limits would let one
    # control silently mask another. This exists because exactly that
    # happened in production: a 50% gross-notional "exposure" cap made
    # max_open_risk_percent=6% mathematically unreachable for any stop
    # closer than 12% of price, so the correct stop-based control was dead
    # code and nothing said so. Raising here is deliberate - misconfigured
    # risk limits must not reach a live account.
    from app.risk.limits import RiskLimits
    RiskLimits().assert_coherent()
    logger.info("Risk limit coherence check passed.")

    # Import the newer models so `create_all()` knows about their tables on
    # a fresh database. Alembic owns schema EVOLUTION (see alembic/versions);
    # this only covers the first-run "table does not exist at all" case, the
    # same role create_all() has always played here.
    from app.models import equity_snapshot as _equity_snapshot  # noqa: F401
    from app.models import risk_assessment as _risk_assessment  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all() only creates missing tables, it never ALTERs an
        # existing one - this project has no Alembic migrations set up, so
        # new columns on existing tables need this lightweight, idempotent
        # bootstrap instead. Safe to run on every startup.
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS score_breakdown JSON"
            ))
        except Exception as e:
            logger.warning(f"score_breakdown column bootstrap skipped: {e}")

        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE coins ADD COLUMN IF NOT EXISTS asset_class VARCHAR(20) NOT NULL DEFAULT 'crypto'"
            ))
        except Exception as e:
            logger.warning(f"asset_class column bootstrap skipped: {e}")

        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN NOT NULL DEFAULT false"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_order_id VARCHAR(50)"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_environment VARCHAR(10)"
            ))
        except Exception as e:
            logger.warning(f"Auto Trading execution columns bootstrap skipped: {e}")

        # One-time collapse of TP1/TP2/TP3 -> single take_profit, and the
        # TP1_HIT/TP2_HIT/TP3_HIT status values -> single TP_HIT (see
        # app/models/signal.py). Guarded on take_profit_2 still existing so
        # this whole block only ever runs once, even though this dev
        # instance restarts on every code reload.
        try:
            from sqlalchemy import text
            still_has_old_columns = (await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'signals' AND column_name = 'take_profit_2'"
            ))).first()

            if still_has_old_columns is not None:
                await conn.execute(text("ALTER TABLE signals ADD COLUMN IF NOT EXISTS take_profit DOUBLE PRECISION"))
                await conn.execute(text(
                    "UPDATE signals SET take_profit = take_profit_1 WHERE take_profit IS NULL"
                ))
                await conn.execute(text(
                    "UPDATE signals SET status = 'TP1_HIT' WHERE status::text IN ('TP2_HIT', 'TP3_HIT')"
                ))
                await conn.execute(text("ALTER TYPE signalstatus RENAME TO signalstatus_old"))
                await conn.execute(text("CREATE TYPE signalstatus AS ENUM ('ACTIVE', 'TP_HIT', 'STOPPED', 'CANCELLED')"))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN status DROP DEFAULT"))
                await conn.execute(text(
                    "ALTER TABLE signals ALTER COLUMN status TYPE signalstatus USING "
                    "(CASE status::text WHEN 'TP1_HIT' THEN 'TP_HIT' ELSE status::text END)::signalstatus"
                ))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN status SET DEFAULT 'ACTIVE'"))
                await conn.execute(text("DROP TYPE signalstatus_old"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_1"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_2"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_3"))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN take_profit SET NOT NULL"))
                logger.info("Migrated signals table: TP1/2/3 -> single take_profit, TPx_HIT -> TP_HIT.")
        except Exception as e:
            logger.warning(f"TP1/2/3 collapse migration skipped/failed: {e}")

        # ---- ICT Pending Limit Entry (2026-07-30) - new columns ----
        try:
            from sqlalchemy import text
            for ddl in (
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_type VARCHAR(20)",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_zone_top DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_zone_bottom DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_expires_at TIMESTAMPTZ",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS actual_fill_price DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_order_id VARCHAR(50)",
            ):
                await conn.execute(text(ddl))
        except Exception as e:
            logger.warning(f"ICT pending-entry columns bootstrap skipped: {e}")

        # ---- initial_stop_loss (2026-07-30) ----
        # This column is owned by the Alembic migration
        # alembic/versions/20260730_01_add_initial_stop_loss.py, which is the
        # authoritative definition (add nullable -> backfill -> NOT NULL).
        # The guarded statements below exist ONLY so a database that has not
        # yet had `alembic upgrade head` run against it still boots with a
        # usable column instead of failing every query with "column does not
        # exist". They are byte-for-byte equivalent to the migration's own
        # steps and are idempotent, so running the migration afterwards is a
        # no-op rather than a conflict.
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS initial_stop_loss DOUBLE PRECISION"
            ))
            await conn.execute(text(
                "UPDATE signals SET initial_stop_loss = stop_loss WHERE initial_stop_loss IS NULL"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ALTER COLUMN initial_stop_loss SET NOT NULL"
            ))
        except Exception as e:
            logger.warning(f"initial_stop_loss bootstrap skipped: {e}")

    # ---- ICT Pending Limit Entry - new ENUM VALUES ----
    # Deliberately OUTSIDE the transaction block above. `create_all()` never
    # alters an existing type, and Postgres will not let a value added by
    # `ALTER TYPE ... ADD VALUE` be USED in the same transaction that added
    # it. Running each ADD VALUE in its own auto-committed connection (and
    # before anything inserts a row carrying the new value) is what makes
    # this safe and idempotent across restarts. IF NOT EXISTS requires
    # PostgreSQL 12+, which this project's docker-compose Postgres image
    # satisfies; the try/except keeps a startup on any other backend
    # (e.g. SQLite in a test harness, which has no enum types at all) from
    # failing here.
    for enum_value in ("PENDING_ENTRY", "EXPIRED"):
        try:
            from sqlalchemy import text
            async with engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(
                    f"ALTER TYPE signalstatus ADD VALUE IF NOT EXISTS '{enum_value}'"
                ))
            logger.info(f"signalstatus enum value '{enum_value}' present.")
        except Exception as e:
            logger.warning(f"signalstatus enum bootstrap for '{enum_value}' skipped: {e}")

    from app.ai.calibration import calibrate_weights, calibrate_all_profiles
    try:
        await calibrate_weights()
    except Exception as e:
        logger.warning(f"AI scorer weight calibration skipped: {e}")

    # 2026-07-26: per-asset-class calibration (Gold/Silver/Oil each get
    # their own weights file, never mixed with crypto or each other - see
    # app/ai/calibration_profiles.py). Additive to the call above, which
    # remains the crypto-era global path still used elsewhere (e.g. the
    # Dashboard AI panel). Safe to fail silently the same way: with zero
    # closed commodity trades so far, every profile just stays on its
    # DEFAULT_WEIGHTS until real trade history exists.
    try:
        await calibrate_all_profiles()
    except Exception as e:
        logger.warning(f"Per-asset-class AI scorer calibration skipped: {e}")

    from app.scheduler.universal_scanner import UniversalScanner
    from app.services.binance_service import fetch_liquid_symbols
    from app.core.constants import COMMODITY_SYMBOLS

    TOP_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "LINKUSDT", "UNIUSDT",
        "ATOMUSDT", "LTCUSDT", "ETCUSDT", "FILUSDT", "OPUSDT", "ARBUSDT",
        "APTUSDT", "NEARUSDT", "VETUSDT", "ICPUSDT", "GRTUSDT", "SANDUSDT",
        "MANAUSDT", "EGLDUSDT", "AAVEUSDT", "ALGOUSDT", "THETAUSDT", "FTMUSDT",
        "FLOWUSDT", "XTZUSDT", "ENJUSDT", "CHZUSDT", "BATUSDT", "ZILUSDT",
        "ONEUSDT", "KSMUSDT", "COMPUSDT", "CRVUSDT", "SNXUSDT", "SUSHIUSDT",
        "YFIUSDT", "ZRXUSDT", "LRCUSDT", "RENUSDT", "BALUSDT", "OCEANUSDT",
        "COTIUSDT", "CELOUSDT",
    ]

    # Drop symbols with thin 24h volume before scanning them - low liquidity
    # means real spread/slippage risk that a signal's confidence score
    # doesn't account for. Dynamic check (not a static hand-edited list).
    TOP_SYMBOLS = await fetch_liquid_symbols(TOP_SYMBOLS)

    # Gold/Silver/Crude/Brent - real Binance USD-M futures contracts
    # (XAUUSDT, XAGUSDT, CLUSDT, BZUSDT), fetched through the same
    # fapi.binance.com klines/WebSocket paths as every crypto pair. They run
    # through the IDENTICAL Universal ICT Pipeline; the only difference is
    # the Asset Profile the scanner resolves for them (London/New York
    # sessions, commodity kill zones including the COMEX open, and their own
    # calibration) - see app/assets/asset_profile.py. Not run through
    # fetch_liquid_symbols: that filter is tuned for altcoin volume
    # thresholds and would drop these legitimate CME-tracked instruments.
    ALL_SYMBOLS = TOP_SYMBOLS + sorted(COMMODITY_SYMBOLS)

    # ONE scanner for every asset class. There is deliberately no
    # CryptoScanner/GoldScanner/ForexScanner in this codebase.
    app.state.scanner = UniversalScanner(ALL_SYMBOLS)

    scanner_task = asyncio.create_task(app.state.scanner.start())
    app.state.scanner_task = scanner_task

    def scanner_done(task: asyncio.Task):
        try:
            exc = task.exception()
            if exc:
                logger.exception(f"Scanner task failed: {exc}")
        except asyncio.CancelledError:
            logger.info("Scanner task cancelled.")

    scanner_task.add_done_callback(scanner_done)

    from app.scheduler.signal_monitor import SignalMonitor

    app.state.signal_monitor = SignalMonitor(app.state.scanner.data_manager)
    monitor_task = asyncio.create_task(app.state.signal_monitor.start())
    app.state.signal_monitor_task = monitor_task

    def monitor_done(task: asyncio.Task):
        try:
            exc = task.exception()
            if exc:
                logger.exception(f"Signal monitor task failed: {exc}")
        except asyncio.CancelledError:
            logger.info("Signal monitor task cancelled.")

    monitor_task.add_done_callback(monitor_done)

    await signal_ws_manager.start_listener()

    logger.info("Scanner, signal monitor, and WebSocket listener started.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down...")

    if hasattr(app.state, "scanner_task"):
        task = app.state.scanner_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if hasattr(app.state, "scanner"):
        await app.state.scanner.stop()

    if hasattr(app.state, "signal_monitor_task"):
        task = app.state.signal_monitor_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if hasattr(app.state, "signal_monitor"):
        await app.state.signal_monitor.stop()

    await signal_ws_manager.stop_listener()

    await engine.dispose()


@app.websocket("/ws/signals")
async def websocket_signals(websocket: WebSocket):
    await signal_ws_manager.connect(websocket)

    heartbeat_task = None

    try:

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        signal_ws_manager.disconnect(websocket)