import asyncio
from fastapi import WebSocket
from loguru import logger
from app.core.redis import redis_client


class SignalWebSocketManager:
    """Manages connected WebSocket clients and broadcasts new signals from Redis."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._listener_task: asyncio.Task | None = None
        self._pubsub = None

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and add it to the active list."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected ({len(self.active_connections)} total)")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected client from the active list."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected ({len(self.active_connections)} remaining)")

    async def broadcast(self, message: str) -> None:
        """Send a message to all connected clients concurrently, handling disconnects gracefully."""
        # Snapshot to avoid runtime errors during concurrent modifications
        connections = list(self.active_connections)
        tasks = [self._safe_send(ws, message) for ws in connections]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_send(self, websocket: WebSocket, message: str) -> None:
        """Send a text message to a single client, removing it on failure."""
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)

    async def start_listener(self) -> None:
        """Start the Redis subscription loop as a background task."""
        self._listener_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        """Listen on Redis signal channels with exponential backoff reconnection."""
        backoff = 1
        while True:
            try:
                self._pubsub = redis_client.pubsub()
                await self._pubsub.subscribe("new_signal", "signal_update")
                logger.info("Redis listener started for new_signal + signal_update channels")
                backoff = 1  # reset after a successful connection
                async for message in self._pubsub.listen():
                    if message["type"] == "message":
                        await self.broadcast(message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Redis listener error: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def stop_listener(self) -> None:
        """Gracefully shut down the Redis listener and cancel the background task."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        if self._pubsub:
            await self._pubsub.unsubscribe("new_signal", "signal_update")
            self._pubsub = None


# Singleton instance to be used across the application
signal_ws_manager = SignalWebSocketManager()