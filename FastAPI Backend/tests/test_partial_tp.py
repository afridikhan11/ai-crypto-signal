"""
Partial take-profit (owner's rule): at TP1 (entry +/- SIGNAL_TP1_PCT%) bank
SIGNAL_TP1_FRACTION of the position and move the stop to breakeven; the
remainder runs to the signal's existing structure target, which is never
re-priced. Covers the pure level/trigger helpers, the monitor's one-shot
partial logic (paper + executed paths, exchange-failure retry), and the
blended P/L the stats script reports.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.models.signal import Direction, SignalStatus
from app.scheduler.signal_monitor import SignalMonitor, tp1_level_for, tp1_reached


def _run(coro):
    return asyncio.run(coro)


class _FakeSignal:
    def __init__(self, direction=Direction.LONG, entry_price=100.0, stop_loss=95.0,
                 take_profit=115.0, executed=False):
        self.id = uuid.uuid4()
        self.direction = direction
        self.entry_price = entry_price
        self.actual_fill_price = None
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.status = SignalStatus.ACTIVE
        self.closed_at = None
        self.created_at = datetime(2026, 8, 25, tzinfo=timezone.utc)
        self.confidence = 75
        self.executed = executed
        self.executed_environment = "testnet" if executed else None
        self.tp1_price = None
        self.tp1_done = False


class _FakeDataManager:
    def get_dataframe(self, symbol, timeframe, limit=None):  # pragma: no cover
        raise AssertionError("not used by these tests")


@pytest.fixture
def monitor():
    return SignalMonitor(_FakeDataManager())


# ======================================================================
# Pure helpers
# ======================================================================
class TestTp1Level:
    def test_long_level_is_entry_plus_pct(self):
        assert tp1_level_for(100.0, Direction.LONG, 2.0, 115.0) == pytest.approx(102.0)

    def test_short_level_is_entry_minus_pct(self):
        assert tp1_level_for(100.0, Direction.SHORT, 2.0, 90.0) == pytest.approx(98.0)

    def test_no_partial_when_target_nearer_than_tp1(self):
        # Structure target at +1.5% but TP1 would be +2%: tiny-range trade,
        # no partial - the trade runs exactly as before.
        assert tp1_level_for(100.0, Direction.LONG, 2.0, 101.5) is None
        assert tp1_level_for(100.0, Direction.SHORT, 2.0, 98.5) is None

    def test_disabled_or_missing_inputs(self):
        assert tp1_level_for(100.0, Direction.LONG, 0.0, 115.0) is None
        assert tp1_level_for(None, Direction.LONG, 2.0, 115.0) is None
        assert tp1_level_for(100.0, Direction.LONG, 2.0, None) is None


class TestTp1Reached:
    def test_long_reaches_at_or_above(self):
        assert tp1_reached(102.0, 102.0, Direction.LONG) is True
        assert tp1_reached(101.9, 102.0, Direction.LONG) is False

    def test_short_reaches_at_or_below(self):
        assert tp1_reached(98.0, 98.0, Direction.SHORT) is True
        assert tp1_reached(98.1, 98.0, Direction.SHORT) is False


# ======================================================================
# Monitor logic (paper path - no exchange)
# ======================================================================
class TestMaybeTakePartialProfit:
    def test_fires_once_banks_and_moves_stop_to_breakeven(self, monitor):
        s = _FakeSignal()  # LONG 100 -> TP 115, stop 95
        events = _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 102.5))
        assert len(events) == 1 and events[0]["event"] == "tp1_partial"
        assert s.tp1_done is True
        assert s.tp1_price == pytest.approx(102.0)
        assert s.stop_loss == 100.0                     # breakeven
        assert events[0]["stop_moved_to_breakeven"] is True
        # Never fires twice.
        assert _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 110.0)) == []

    def test_below_tp1_does_nothing_but_caches_level(self, monitor):
        s = _FakeSignal()
        assert _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 101.0)) == []
        assert s.tp1_price == pytest.approx(102.0)      # level cached for next polls
        assert s.tp1_done is False
        assert s.stop_loss == 95.0                      # untouched

    def test_short_direction(self, monitor):
        s = _FakeSignal(direction=Direction.SHORT, entry_price=100.0,
                        stop_loss=105.0, take_profit=90.0)
        events = _run(monitor._maybe_take_partial_profit(s, "XRPUSDT", 97.9))
        assert len(events) == 1
        assert s.tp1_price == pytest.approx(98.0)
        assert s.stop_loss == 100.0                     # breakeven for a short

    def test_tiny_range_trade_gets_no_partial(self, monitor):
        s = _FakeSignal(take_profit=101.5)              # target nearer than TP1
        assert _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 101.4)) == []
        assert s.tp1_price is None and s.tp1_done is False

    def test_disabled_via_config(self, monitor):
        settings = get_settings()
        original = settings.signal_tp1_pct
        settings.signal_tp1_pct = 0.0
        try:
            s = _FakeSignal()
            assert _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 110.0)) == []
            assert s.tp1_done is False
        finally:
            settings.signal_tp1_pct = original

    def test_never_worsens_an_already_better_stop(self, monitor):
        # Management already trailed the stop ABOVE entry - BE must not pull it back.
        s = _FakeSignal(stop_loss=103.0)
        events = _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 104.0))
        assert len(events) == 1 and s.tp1_done is True
        assert s.stop_loss == 103.0                     # kept the better stop
        assert events[0]["stop_moved_to_breakeven"] is False


# ======================================================================
# Executed path - exchange leg
# ======================================================================
class TestExecutedPartialClose:
    def test_exchange_failure_retries_next_poll(self, monitor):
        s = _FakeSignal(executed=True)

        async def failing_partial(signal, symbol, fraction):
            return False

        monitor._partial_close_live_position = failing_partial
        assert _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 102.5)) == []
        assert s.tp1_done is False                      # will retry while beyond TP1

    def test_exchange_success_completes_bookkeeping(self, monitor):
        s = _FakeSignal(executed=True)
        calls = {}

        async def ok_partial(signal, symbol, fraction):
            calls["fraction"] = fraction
            return True

        async def ok_sync(signal, symbol, new_stop):
            calls["synced_stop"] = new_stop
            return True

        monitor._partial_close_live_position = ok_partial
        monitor._sync_exchange_stop = ok_sync
        events = _run(monitor._maybe_take_partial_profit(s, "BTCUSDT", 102.5))
        assert len(events) == 1 and s.tp1_done is True
        assert calls["fraction"] == get_settings().signal_tp1_fraction
        assert calls["synced_stop"] == 100.0            # breakeven synced to exchange


# ======================================================================
# Stats blending
# ======================================================================
class TestBlendedPnl:
    def _stats(self):
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "signal_stats.py")
        spec = importlib.util.spec_from_file_location("signal_stats_tp1", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_breakeven_runner_after_tp1_shows_the_banked_half(self):
        # SHORT 100, TP1 98 banked (2%), runner stopped at breakeven (100):
        # blended = 0.5*2 + 0.5*0 = +1.0% - not a misleading +0.00%.
        stats = self._stats()
        s = _FakeSignal(direction=Direction.SHORT, entry_price=100.0,
                        stop_loss=100.0, take_profit=90.0)
        s.status = SignalStatus.STOPPED
        s.tp1_done, s.tp1_price = True, 98.0
        assert stats._pnl_pct(s) == pytest.approx(1.0)

    def test_no_tp1_behaves_exactly_as_before(self):
        stats = self._stats()
        s = _FakeSignal(direction=Direction.SHORT, entry_price=100.0,
                        stop_loss=100.0, take_profit=90.0)
        s.status = SignalStatus.STOPPED
        assert stats._pnl_pct(s) == pytest.approx(0.0)
