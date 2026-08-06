"""Standalone background engine (scanner + signal tracker).

Run this as a *single* dedicated process when the API is scaled horizontally
(``RUN_SCANNER=false`` on the API service):

    python -m app.worker

Keeping the engine in one process avoids duplicate WebSocket streams and
duplicate signals that would occur if every API worker ran its own scanner.
"""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.logging import logger


async def _run() -> None:
    settings = get_settings()

    # Ensure schema exists (dev convenience; use Alembic in production).
    if settings.auto_create_tables:
        from app.core.database import engine
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    from app.scheduler.scanner import CryptoScanner
    from app.scheduler.signal_tracker import SignalTracker

    scanner = CryptoScanner()
    tracker = None

    await scanner.start()
    if settings.tracker_enabled:
        tracker = SignalTracker(
            scanner.data_manager, interval_seconds=settings.tracker_interval_seconds
        )
        tracker.start()

    logger.info("Background engine running. Press Ctrl+C to stop.")

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    await stop_event.wait()

    logger.info("Stopping background engine...")
    if tracker:
        await tracker.stop()
    await scanner.stop()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
