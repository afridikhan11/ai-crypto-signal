"""
Smart AI runner supervision: signals are tagged and delivered, a crashing
strategy is isolated from a healthy one, the supervisor restarts a task whose
loop dies, and runtime enable/disable starts/stops tasks.
"""
import asyncio

import pytest

from app.models.signal import Direction
from app.strategy.base_strategy import BaseStrategy, MarketData, StrategySignal
from app.strategy.smart_ai_runner import SmartAiRunner


def _signal(strategy_id="s"):
    return StrategySignal(
        strategy_id=strategy_id, symbol="BTCUSDT", direction=Direction.SHORT,
        entry_price=100.0, stop_loss=101.0, take_profit=97.0, risk_reward=3.0, confidence=70,
    )


class _Strat(BaseStrategy):
    def __init__(self, sid, *, enabled=True, behavior=None):
        super().__init__(settings=None)
        self.strategy_id = sid
        self.display_name = sid
        self._enabled = enabled
        self._behavior = behavior or (lambda md: _signal(sid))

    @property
    def enabled(self):
        return self._enabled

    async def evaluate(self, market_data):
        return self._behavior(market_data)


async def _provider(strat, symbol):
    return MarketData(symbol=symbol)


def _runner(strats, sink, **kw):
    return SmartAiRunner(strats, symbols=["BTCUSDT"], data_provider=_provider, sink=sink, poll_interval=0.01, **kw)


async def _drain(event, timeout=2.0):
    await asyncio.wait_for(event.wait(), timeout=timeout)


class TestRunner:
    def test_produces_and_tags_signal(self):
        got = []
        done = asyncio.Event()

        async def sink(sig):
            got.append(sig)
            done.set()

        async def scenario():
            runner = _runner([_Strat("alpha")], sink)
            await runner.start()
            await _drain(done)
            await runner.stop()

        asyncio.run(scenario())
        assert got and got[0].strategy_id == "alpha"

    def test_fault_isolation_crashing_strategy_does_not_stop_healthy_one(self):
        def boom(md):
            raise RuntimeError("bad strategy")

        healthy_hits = {"n": 0}
        done = asyncio.Event()

        async def sink(sig):
            healthy_hits["n"] += 1
            if healthy_hits["n"] >= 3:
                done.set()

        async def scenario():
            crashing = _Strat("crashing", behavior=boom)
            healthy = _Strat("healthy", behavior=lambda md: _signal("healthy"))
            runner = _runner([crashing, healthy], sink)
            await runner.start()
            await _drain(done)
            statuses = {s.strategy_id: s for s in runner.status()}
            await runner.stop()
            return statuses

        statuses = asyncio.run(scenario())
        assert healthy_hits["n"] >= 3                     # healthy kept producing
        assert statuses["crashing"].signals == 0          # crashing never produced
        assert statuses["crashing"].last_error is not None  # its error was recorded
        assert statuses["healthy"].signals >= 3

    def test_supervisor_restarts_a_dead_loop(self):
        done = asyncio.Event()

        async def sink(sig):
            pass

        async def scenario():
            strat = _Strat("flaky")
            runner = _runner([strat], sink)
            calls = {"n": 0}

            async def flaky_loop(strategy_id):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("loop died")
                done.set()
                await asyncio.sleep(0.05)

            runner.RESTART_BACKOFF_SECONDS = 0.01
            runner._loop = flaky_loop
            await runner.start()
            await _drain(done)
            st = {s.strategy_id: s for s in runner.status()}["flaky"]
            await runner.stop()
            return st

        st = asyncio.run(scenario())
        assert st.restarts >= 1

    def test_disabled_by_config_does_not_run(self):
        got = []

        async def sink(sig):
            got.append(sig)

        async def scenario():
            runner = _runner([_Strat("off", enabled=False)], sink)
            await runner.start()
            await asyncio.sleep(0.1)
            running = {s.strategy_id: s for s in runner.status()}["off"].running
            await runner.stop()
            return running

        running = asyncio.run(scenario())
        assert running is False
        assert got == []

    def test_runtime_enable_then_disable(self):
        hits = {"n": 0}
        started = asyncio.Event()

        async def sink(sig):
            hits["n"] += 1
            started.set()

        async def scenario():
            strat = _Strat("toggle", enabled=False)
            runner = _runner([strat], sink)
            await runner.start()
            await asyncio.sleep(0.05)
            assert hits["n"] == 0                 # disabled: nothing
            runner.set_enabled("toggle", True)    # turn on at runtime
            await _drain(started)
            count_after_enable = hits["n"]
            runner.set_enabled("toggle", False)   # turn off at runtime
            await asyncio.sleep(0.05)
            frozen = hits["n"]
            await asyncio.sleep(0.05)
            await runner.stop()
            return count_after_enable, frozen, hits["n"]

        enabled_count, frozen, final = asyncio.run(scenario())
        assert enabled_count >= 1                 # ran once enabled
        assert final == frozen                    # stopped producing once disabled

    def test_unknown_strategy_enable_raises(self):
        async def sink(sig):
            pass

        runner = _runner([_Strat("known")], sink)
        with pytest.raises(KeyError):
            runner.set_enabled("nope", True)
