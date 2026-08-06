"""Signal lifecycle tracker.

Previously signals were created and never resolved, so every statistic
(win-rate especially) was meaningless and a coin's first signal blocked it
forever. This tracker periodically checks every ACTIVE signal against live
price and transitions it through its lifecycle:

    ACTIVE ──SL──▶ STOPPED
    ACTIVE ──TP1─▶ (breakeven armed) ──▶ TP1_HIT / TP2_HIT / TP3_HIT

Progress toward targets is stored in ``max_tp_hit`` so ``status`` only flips
on a *terminal* close (keeping the win-rate stats correct). After TP1 the stop
is moved to breakeven — a standard risk-free runner.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.models.signal import Direction, Signal, SignalStatus

# Candle duration ranking to pick the most responsive streamed timeframe.
_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "2h": 7200, "4h": 14400}


class SignalTracker:
    def __init__(self, data_manager, interval_seconds: int = 15):
        self.data_manager = data_manager
        self.interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Most responsive timeframe we actually stream.
        tfs = getattr(data_manager, "timeframes", ["5m"])
        self.price_tf = min(tfs, key=lambda t: _TF_SECONDS.get(t, 999999))

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Signal tracker started (price TF={self.price_tf}, every {self.interval}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    async def _loop(self):
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Tracker tick failed: {e}")
            await asyncio.sleep(self.interval)

    def _latest_hl(self, symbol: str):
        """Return (high, low, close) of the latest streamed candle, or None."""
        candles = self.data_manager.get_candles(symbol, self.price_tf)
        if not candles:
            return None
        c = candles[-1]
        return c.high, c.low, c.close

    # ------------------------------------------------------------------
    async def _tick(self):
        async with AsyncSessionLocal() as session:
            active = (
                await session.execute(
                    select(Signal)
                    .options(selectinload(Signal.coin))
                    .where(Signal.status == SignalStatus.ACTIVE)
                )
            ).scalars().all()

            for sig in active:
                symbol = sig.coin.symbol if sig.coin else None
                if not symbol:
                    continue
                hl = self._latest_hl(symbol)
                if hl is None:
                    continue
                high, low, _close = hl
                update = self._evaluate(sig, high, low)
                if update is not None:
                    status, closed = update
                    await self._publish(sig, status, closed)

            await session.commit()

    # ------------------------------------------------------------------
    def _evaluate(self, sig: Signal, high: float, low: float):
        """Mutate the signal in place. Returns (status, closed) if changed."""
        long = sig.direction == Direction.LONG
        eff_stop = sig.entry_price if sig.max_tp_hit >= 1 else sig.stop_loss

        terminal_status = None
        if long:
            if high >= sig.take_profit_3:
                terminal_status = SignalStatus.TP3_HIT
            elif low <= eff_stop:
                terminal_status = self._stop_status(sig.max_tp_hit)
        else:
            if low <= sig.take_profit_3:
                terminal_status = SignalStatus.TP3_HIT
            elif high >= eff_stop:
                terminal_status = self._stop_status(sig.max_tp_hit)

        if terminal_status is not None:
            sig.status = terminal_status
            sig.closed_at = datetime.now(timezone.utc)
            if terminal_status == SignalStatus.TP3_HIT:
                sig.max_tp_hit = 3
            logger.success(f"{sig.coin.symbol}: {terminal_status.value} (rr={sig.risk_reward})")
            return terminal_status.value, True

        # Non-terminal: advance TP progress + arm breakeven.
        reached = sig.max_tp_hit
        if long:
            if high >= sig.take_profit_2:
                reached = max(reached, 2)
            elif high >= sig.take_profit_1:
                reached = max(reached, 1)
        else:
            if low <= sig.take_profit_2:
                reached = max(reached, 2)
            elif low <= sig.take_profit_1:
                reached = max(reached, 1)

        if reached != sig.max_tp_hit:
            sig.max_tp_hit = reached
            logger.info(f"{sig.coin.symbol}: reached TP{reached}, stop → breakeven")
            return f"TP{reached}_PROGRESS", False
        return None

    @staticmethod
    def _stop_status(max_tp: int) -> SignalStatus:
        return {
            0: SignalStatus.STOPPED,
            1: SignalStatus.TP1_HIT,
            2: SignalStatus.TP2_HIT,
        }.get(max_tp, SignalStatus.STOPPED)

    async def _publish(self, sig: Signal, status: str, closed: bool):
        payload = {
            "type": "signal_update",
            "id": str(sig.id),
            "symbol": sig.coin.symbol if sig.coin else None,
            "status": status,
            "max_tp_hit": sig.max_tp_hit,
            "closed": closed,
        }
        try:
            await redis_client.publish("signal_update", json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to publish signal update: {e}")
