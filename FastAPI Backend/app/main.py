import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.core.logging import logger
from app.models.base import Base
from app.websocket.signal_ws import signal_ws_manager

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    logger.info("Starting application...")

    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    app.state.scanner = None
    app.state.scanner_task = None
    app.state.tracker = None

    if settings.run_scanner:
        from app.scheduler.scanner import CryptoScanner
        from app.scheduler.signal_tracker import SignalTracker

        scanner = CryptoScanner()
        app.state.scanner = scanner
        scanner_task = asyncio.create_task(scanner.start())
        app.state.scanner_task = scanner_task

        def scanner_done(task: asyncio.Task):
            try:
                if task.exception():
                    logger.exception(f"Scanner task failed: {task.exception()}")
            except asyncio.CancelledError:
                logger.info("Scanner task cancelled.")

        scanner_task.add_done_callback(scanner_done)

        if settings.tracker_enabled:
            tracker = SignalTracker(
                scanner.data_manager,
                interval_seconds=settings.tracker_interval_seconds,
            )
            tracker.start()
            app.state.tracker = tracker
        logger.info("Scanner and tracker started (in-process).")
    else:
        logger.info("RUN_SCANNER=false — API-only mode; engine runs separately.")

    await signal_ws_manager.start_listener()
    logger.info("WebSocket listener started.")

    yield

    # ---- shutdown ----
    logger.info("Shutting down...")
    if getattr(app.state, "tracker", None):
        await app.state.tracker.stop()

    if getattr(app.state, "scanner_task", None):
        app.state.scanner_task.cancel()
        try:
            await app.state.scanner_task
        except asyncio.CancelledError:
            pass

    if getattr(app.state, "scanner", None):
        await app.state.scanner.stop()

    await signal_ws_manager.stop_listener()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


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
