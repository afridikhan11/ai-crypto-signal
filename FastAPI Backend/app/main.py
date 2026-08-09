import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import logger
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

    # ------------------------------------------------------------------
    # SCHEMA OWNERSHIP (2026-07-31)
    #
    # ALEMBIC IS THE SINGLE OWNER OF THE DATABASE SCHEMA. This process
    # performs NO DDL of any kind. Migrations run from the container
    # command (`alembic upgrade head && uvicorn ...`) before this process
    # starts serving, and a failed migration stops the container rather
    # than letting the app come up against a half-migrated database.
    #
    # The pre-Alembic in-process bootstrap (`create_all()` plus ~15
    # hand-written ALTER TABLE / ALTER TYPE statements) has been moved
    # intact to app/core/legacy_schema_bootstrap.py, where it is
    # quarantined behind DB_AUTO_BOOTSTRAP and refuses to run while that
    # flag is False. Two owners of one schema is exactly why
    # `alembic_version` never existed: the app kept building its own
    # schema, so Alembic was never invoked and had nothing to record.
    # ------------------------------------------------------------------
    if settings.db_auto_bootstrap:
        logger.warning(
            "DB_AUTO_BOOTSTRAP is enabled - this process will create/alter "
            "schema directly, BYPASSING Alembic. Never enable this against a "
            "database Alembic manages."
        )
        from app.core.legacy_schema_bootstrap import run_legacy_schema_bootstrap
        await run_legacy_schema_bootstrap()
    else:
        logger.info(
            "Schema bootstrap skipped - Alembic owns the schema "
            "(`alembic upgrade head` runs from the container command)."
        )


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

    # Opt-in, testnet-only hands-off execution. Off by default; the executor
    # itself also refuses anything but testnet credentials (see its docstring).
    if get_settings().auto_execute_testnet:
        from app.scheduler.auto_executor import AutoExecutor

        app.state.auto_executor = AutoExecutor()
        await app.state.auto_executor.start()
        logger.warning(
            "AUTO_EXECUTE_TESTNET is ON - signals will be auto-executed on "
            "Binance TESTNET (never mainnet)."
        )

    logger.info("Scanner, signal monitor, and WebSocket listener started.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down...")

    if hasattr(app.state, "auto_executor"):
        await app.state.auto_executor.stop()

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