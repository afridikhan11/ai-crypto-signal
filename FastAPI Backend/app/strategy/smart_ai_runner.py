"""
Smart AI runner - fault-isolated supervision of the enabled strategies.

Each strategy runs in its OWN supervised asyncio task. A strategy that raises -
inside `evaluate` or by crashing its whole loop - is caught, logged, and its
task is restarted with backoff; it can never take another strategy (or the app)
down with it. This is the "each strategy in its own process" requirement met at
task-isolation granularity (the confirmed design), not separate OS processes.

The runner is deliberately I/O-agnostic and unit-testable: market data comes
from an injected `data_provider` and produced signals go to an injected `sink`,
so the supervision/fault-isolation/enable-disable logic is exercised without a
live exchange or database. The live wiring (scanner data in, DB rows out,
tagged with `strategy_id`) is a thin adapter supplied at startup.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from loguru import logger

from app.strategy.base_strategy import BaseStrategy, MarketData, StrategySignal

# (strategy, symbol) -> MarketData for this evaluation tick.
DataProvider = Callable[[BaseStrategy, str], Awaitable[Optional[MarketData]]]
# Persist / route a produced signal (already tagged with its strategy_id).
SignalSink = Callable[[StrategySignal], Awaitable[None]]


@dataclass
class StrategyStatus:
    strategy_id: str
    display_name: str
    enabled: bool
    running: bool = False
    evaluations: int = 0
    signals: int = 0
    restarts: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "running": self.running,
            "evaluations": self.evaluations,
            "signals": self.signals,
            "restarts": self.restarts,
            "last_error": self.last_error,
        }


class SmartAiRunner:
    """Supervises the enabled Smart AI strategies. Enable/disable is a runtime
    override on top of each strategy's config default, so the UI can toggle a
    strategy without a restart."""

    RESTART_BACKOFF_SECONDS = 2.0

    def __init__(
        self,
        strategies: List[BaseStrategy],
        symbols: List[str],
        data_provider: DataProvider,
        sink: SignalSink,
        poll_interval: float = 60.0,
    ) -> None:
        self._strategies: Dict[str, BaseStrategy] = {s.strategy_id: s for s in strategies}
        self._symbols = list(symbols)
        self._data_provider = data_provider
        self._sink = sink
        self._poll_interval = poll_interval
        # Runtime enable override: None => use the strategy's own config default.
        self._enabled_override: Dict[str, bool] = {}
        self._status: Dict[str, StrategyStatus] = {
            sid: StrategyStatus(sid, s.display_name or sid, self._is_enabled(sid))
            for sid, s in self._strategies.items()
        }
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stopping = False

    # ---- enable/disable -------------------------------------------------
    def _is_enabled(self, strategy_id: str) -> bool:
        override = self._enabled_override.get(strategy_id)
        if override is not None:
            return override
        strat = self._strategies.get(strategy_id)
        return bool(strat and strat.enabled)

    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        if strategy_id not in self._strategies:
            raise KeyError(strategy_id)
        self._enabled_override[strategy_id] = enabled
        self._status[strategy_id].enabled = enabled
        logger.info("Smart AI strategy {} {} at runtime", strategy_id, "ENABLED" if enabled else "DISABLED")
        if not self._stopping:
            self._reconcile(strategy_id)

    def status(self) -> List[StrategyStatus]:
        return [self._status[sid] for sid in self._strategies]

    # ---- supervision ----------------------------------------------------
    async def start(self) -> None:
        self._stopping = False
        for sid in self._strategies:
            self._reconcile(sid)

    def _reconcile(self, strategy_id: str) -> None:
        """Ensure a strategy's task is running iff it is enabled."""
        want = self._is_enabled(strategy_id)
        task = self._tasks.get(strategy_id)
        alive = task is not None and not task.done()
        if want and not alive:
            self._tasks[strategy_id] = asyncio.ensure_future(self._supervise(strategy_id))
        elif not want and alive:
            task.cancel()

    async def _supervise(self, strategy_id: str) -> None:
        """Run one strategy's loop, restarting it (with backoff) if it crashes -
        so a bug in one strategy is isolated to that strategy."""
        status = self._status[strategy_id]
        while not self._stopping and self._is_enabled(strategy_id):
            status.running = True
            try:
                await self._loop(strategy_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - isolate: never propagate
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.restarts += 1
                logger.exception("Smart AI strategy {} crashed; restarting (#{})", strategy_id, status.restarts)
                await asyncio.sleep(self.RESTART_BACKOFF_SECONDS)
        status.running = False

    async def _loop(self, strategy_id: str) -> None:
        strat = self._strategies[strategy_id]
        status = self._status[strategy_id]
        while not self._stopping and self._is_enabled(strategy_id):
            for symbol in self._symbols:
                try:
                    market_data = await self._data_provider(strat, symbol)
                    if market_data is None:
                        continue
                    signal = await strat.evaluate(market_data)
                    status.evaluations += 1
                    if signal is not None:
                        signal.strategy_id = strat.strategy_id  # authoritative tag
                        await self._sink(signal)
                        status.signals += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a bad symbol must not kill the loop
                    status.last_error = f"{symbol}: {type(exc).__name__}: {exc}"
                    logger.warning("Smart AI {} eval error on {}: {}", strategy_id, symbol, exc)
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for status in self._status.values():
            status.running = False
