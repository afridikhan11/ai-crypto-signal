from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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


@app.on_event("startup")
async def on_startup():
    logger.info("Starting application...")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.scheduler.scanner import CryptoScanner

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

    app.state.scanner = CryptoScanner(TOP_SYMBOLS)

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

    await signal_ws_manager.start_listener()

    logger.info("Scanner and WebSocket listener started.")


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