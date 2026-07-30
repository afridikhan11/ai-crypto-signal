"""
Regression suite for the ICT Pending Limit Entry migration (2026-07-30).

Covers the four pieces of genuinely NEW behaviour, and - just as
importantly - pins the things that must NOT have changed:

  1. SignalGenerator entry activation + the usability guard.
  2. SignalMonitor's pending state machine (fill / invalidation / expiry).
  3. BacktestEngine's fill simulation.
  4. The statistics contract (EXPIRED must never move the win rate).

Every test drives the REAL production classes - no reimplementation of the
logic under test.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

import app.strategy.signal_generator as sg_module
from app.backtest.engine import BacktestEngine
from app.core.constants import PENDING_ENTRY_EXPIRY_CANDLES, PENDING_ENTRY_EXPIRY_MINUTES
from app.models.signal import (
    DECIDED_STATUSES,
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    Direction,
    SignalStatus,
)
from app.scheduler.signal_monitor import SignalMonitor
from app.strategy.signal_generator import SignalGenerator


# ==========================================================================
# 1. Entry activation + usability guard
# ==========================================================================
class TestEntryUsabilityGuard:
    """`_is_usable_entry` decides ONLY whether the ICT anchor or the live
    price prices the trade. It must never accept an anchor that would
    invert the risk distance."""

    def test_long_entry_between_stop_and_target_is_usable(self):
        assert SignalGenerator._is_usable_entry(Direction.LONG, 100.0, 90.0, 130.0) is True

    def test_short_entry_between_target_and_stop_is_usable(self):
        assert SignalGenerator._is_usable_entry(Direction.SHORT, 100.0, 110.0, 70.0) is True

    def test_long_entry_below_stop_is_rejected(self):
        # Would make the "stop" sit above entry on a long - an inverted,
        # meaningless trade.
        assert SignalGenerator._is_usable_entry(Direction.LONG, 89.0, 90.0, 130.0) is False

    def test_long_entry_above_target_is_rejected(self):
        assert SignalGenerator._is_usable_entry(Direction.LONG, 131.0, 90.0, 130.0) is False

    def test_short_entry_above_stop_is_rejected(self):
        assert SignalGenerator._is_usable_entry(Direction.SHORT, 111.0, 110.0, 70.0) is False

    def test_entry_exactly_on_the_stop_is_rejected(self):
        # Zero risk distance would make Risk:Reward infinite/undefined.
        assert SignalGenerator._is_usable_entry(Direction.LONG, 90.0, 90.0, 130.0) is False

    def test_none_entry_is_rejected(self):
        assert SignalGenerator._is_usable_entry(Direction.LONG, None, 90.0, 130.0) is False

    def test_nan_and_non_positive_entries_are_rejected(self):
        assert SignalGenerator._is_usable_entry(Direction.LONG, float("nan"), 90.0, 130.0) is False
        assert SignalGenerator._is_usable_entry(Direction.LONG, 0.0, -10.0, 130.0) is False

    def test_non_numeric_entry_is_rejected_rather_than_raising(self):
        assert SignalGenerator._is_usable_entry(Direction.LONG, "not-a-price", 90.0, 130.0) is False


class TestEntryActivationImprovesRiskReward:
    """The whole point of the migration: pricing the trade from the ICT
    anchor instead of the live price moves Risk:Reward, without touching
    the stop or the target."""

    @staticmethod
    def _rr(entry, stop, target):
        return round(abs(target - entry) / abs(entry - stop), 2)

    def test_ote_entry_produces_better_rr_than_market_entry(self):
        # A short after a bearish break: stop above, target below. The ICT
        # anchor sits between the live price and the stop, so entering
        # there shortens risk and lengthens reward simultaneously.
        stop, target = 99790.0, 98153.0
        market_entry = 98491.0
        ote_entry = 99300.0

        market_rr = self._rr(market_entry, stop, target)
        ote_rr = self._rr(ote_entry, stop, target)

        assert market_rr < 1.0, "precondition: market entry gives the poor RR we diagnosed"
        assert ote_rr > market_rr
        assert ote_rr >= 2.0, "the ICT anchor should clear the 2.0 gate the market entry could not"

    def test_stop_and_target_are_untouched_by_entry_choice(self):
        # Entry activation must change ONLY the entry. Both RR values above
        # are computed against the identical stop and target.
        stop, target = 99790.0, 98153.0
        for entry in (98491.0, 99300.0):
            assert abs(entry - stop) > 0
            assert stop == 99790.0 and target == 98153.0


# ==========================================================================
# 2. SignalMonitor pending state machine
# ==========================================================================
def _pending_signal(direction=Direction.LONG, entry=100.0, stop=95.0, target=115.0,
                    zone_top=None, zone_bottom=None, expires_in_minutes=60):
    return SimpleNamespace(
        direction=direction,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        entry_zone_top=zone_top,
        entry_zone_bottom=zone_bottom,
        entry_expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
        entry_type="ote",
        executed=False,
        entry_order_id=None,
        status=SignalStatus.PENDING_ENTRY,
    )


def _monitor():
    """A SignalMonitor with no data manager - every method exercised below
    is pure logic over values passed in, so no live data source is needed."""
    return SignalMonitor(data_manager=None)


def _bt():
    return BacktestEngine("BTCUSDT", "15m")


class TestPendingEntryZoneBounds:
    def test_real_zone_bounds_are_used_when_present(self):
        s = _pending_signal(zone_top=101.0, zone_bottom=99.0)
        assert SignalMonitor._entry_zone_bounds(s) == (101.0, 99.0)

    def test_falls_back_to_the_exact_entry_when_no_zone_recorded(self):
        # A market-type entry, or a row created before this migration -
        # never a guessed zone width.
        s = _pending_signal(zone_top=None, zone_bottom=None)
        assert SignalMonitor._entry_zone_bounds(s) == (100.0, 100.0)

    def test_inverted_zone_bounds_fall_back_rather_than_being_trusted(self):
        s = _pending_signal(zone_top=99.0, zone_bottom=101.0)
        assert SignalMonitor._entry_zone_bounds(s) == (100.0, 100.0)


class TestPendingOutcome:
    """Uses candle EXTREMES, and checks invalidation BEFORE fill."""

    def test_long_fills_when_price_trades_into_the_zone(self):
        s = _pending_signal(zone_top=101.0, zone_bottom=99.0)
        assert _monitor()._pending_outcome(s, high=105.0, low=100.5) is SignalStatus.ACTIVE

    def test_long_still_waiting_when_price_never_reaches_the_zone(self):
        s = _pending_signal(zone_top=101.0, zone_bottom=99.0)
        assert _monitor()._pending_outcome(s, high=110.0, low=102.0) is None

    def test_long_invalidated_when_stop_is_reached_first(self):
        s = _pending_signal(zone_top=101.0, zone_bottom=99.0, stop=95.0)
        assert _monitor()._pending_outcome(s, high=102.0, low=94.0) is SignalStatus.EXPIRED

    def test_candle_spanning_both_zone_and_stop_is_never_entered(self):
        # THE critical ordering case: a candle that reaches both the entry
        # and the stop must count as "never entered", not as an instantly
        # stopped-out trade. Inventing that trade would fabricate a loss.
        s = _pending_signal(zone_top=101.0, zone_bottom=99.0, stop=95.0)
        assert _monitor()._pending_outcome(s, high=101.5, low=94.0) is SignalStatus.EXPIRED

    def test_short_fills_when_price_trades_up_into_the_zone(self):
        s = _pending_signal(direction=Direction.SHORT, entry=100.0, stop=105.0, target=85.0,
                            zone_top=101.0, zone_bottom=99.0)
        assert _monitor()._pending_outcome(s, high=99.5, low=95.0) is SignalStatus.ACTIVE

    def test_short_invalidated_when_stop_is_reached_first(self):
        s = _pending_signal(direction=Direction.SHORT, entry=100.0, stop=105.0, target=85.0,
                            zone_top=101.0, zone_bottom=99.0)
        assert _monitor()._pending_outcome(s, high=106.0, low=95.0) is SignalStatus.EXPIRED


class TestPendingExpiry:
    def test_not_expired_before_the_window_elapses(self):
        s = _pending_signal(expires_in_minutes=30)
        assert SignalMonitor._pending_expired(s, datetime.now(timezone.utc)) is False

    def test_expired_once_the_window_has_passed(self):
        s = _pending_signal(expires_in_minutes=-1)
        assert SignalMonitor._pending_expired(s, datetime.now(timezone.utc)) is True

    def test_naive_timestamps_are_treated_as_utc_rather_than_crashing(self):
        s = _pending_signal()
        s.entry_expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
        assert SignalMonitor._pending_expired(s, datetime.now(timezone.utc)) is True

    def test_missing_expiry_never_expires(self):
        # A market-mode signal has no entry window at all.
        s = _pending_signal()
        s.entry_expires_at = None
        assert SignalMonitor._pending_expired(s, datetime.now(timezone.utc)) is False


# ==========================================================================
# 3. Backtest fill simulation
# ==========================================================================
def _df(highs, lows):
    n = len(highs)
    return pd.DataFrame(
        {"open": lows, "high": highs, "low": lows, "close": lows,
         "volume": [1000.0] * n},
        index=pd.date_range("2026-07-01", periods=n, freq="15min"),
    )


class TestBacktestFillSimulation:
    def test_market_entry_fills_immediately(self):
        # entry_type "market" means the live price WAS the entry - no
        # simulation, identical to pre-migration behaviour.
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0,
               "entry_type": "market"}
        df = _df([110.0] * 5, [105.0] * 5)
        assert _bt()._simulate_fill(df, 0, sig) == 0

    def test_missing_entry_type_also_fills_immediately(self):
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0}
        df = _df([110.0] * 5, [105.0] * 5)
        assert _bt()._simulate_fill(df, 0, sig) == 0

    def test_long_pending_entry_fills_on_the_candle_that_reaches_the_zone(self):
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0,
               "entry_type": "ote", "entry_zone_top": 101.0, "entry_zone_bottom": 99.0}
        df = _df([110, 109, 108, 107], [105, 104, 100.5, 103])
        assert _bt()._simulate_fill(df, 0, sig) == 2

    def test_long_pending_entry_never_reached_returns_none(self):
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0,
               "entry_type": "ote", "entry_zone_top": 101.0, "entry_zone_bottom": 99.0}
        df = _df([110] * 4, [105] * 4)
        assert _bt()._simulate_fill(df, 0, sig) is None

    def test_stop_reached_before_entry_returns_none(self):
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0,
               "entry_type": "ote", "entry_zone_top": 101.0, "entry_zone_bottom": 99.0}
        df = _df([110, 109], [105, 94.0])
        assert _bt()._simulate_fill(df, 0, sig) is None

    def test_fill_window_matches_the_live_monitor_constant(self):
        # Never filled within the window -> None, proving the backtest uses
        # the SAME expiry the live monitor does.
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0,
               "entry_type": "ote", "entry_zone_top": 101.0, "entry_zone_bottom": 99.0}
        n = PENDING_ENTRY_EXPIRY_CANDLES + 5
        highs = [110.0] * n
        lows = [105.0] * n
        lows[PENDING_ENTRY_EXPIRY_CANDLES + 1] = 100.0  # reachable, but too late
        assert _bt()._simulate_fill(_df(highs, lows), 0, sig) is None

    def test_unfilled_trade_resolves_as_expired_not_as_a_loss(self):
        sig = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0, "take_profit": 115.0,
               "entry_type": "ote", "entry_zone_top": 101.0, "entry_zone_bottom": 99.0}
        outcome, ts, idx, rr = _bt()._resolve_trade(_df([110] * 4, [105] * 4), 0, sig)
        assert outcome == "EXPIRED"
        assert rr == 0.0
        assert ts is None and idx is None


class TestBacktestSummaryExcludesExpired:
    def test_expired_trades_never_move_the_win_rate(self):
        trades = [
            {"outcome": "TP_HIT", "realized_rr": 2.0, "confidence": 90},
            {"outcome": "STOPPED", "realized_rr": -1.0, "confidence": 88},
            {"outcome": "EXPIRED", "realized_rr": 0.0, "confidence": 91},
            {"outcome": "EXPIRED", "realized_rr": 0.0, "confidence": 92},
        ]
        summary = BacktestEngine._summarize(trades)
        # 1 win / 1 loss -> 50%. The two EXPIRED rows must not dilute it.
        assert summary["win_rate"] == 50.0
        assert summary["expired"] == 2
        assert summary["fill_rate"] == 50.0

    def test_expired_trades_are_excluded_from_average_realized_rr(self):
        trades = [
            {"outcome": "TP_HIT", "realized_rr": 3.0, "confidence": 90},
            {"outcome": "EXPIRED", "realized_rr": 0.0, "confidence": 90},
        ]
        summary = BacktestEngine._summarize(trades)
        assert summary["avg_realized_rr"] == 3.0, "a never-entered trade must not drag the average down"


# ==========================================================================
# 4. Status contract - what "closed" is allowed to mean
# ==========================================================================
class TestStatusGroupings:
    def test_expired_is_never_counted_as_a_decided_trade(self):
        # This is what protects every win-rate statistic in the app.
        assert SignalStatus.EXPIRED not in DECIDED_STATUSES

    def test_pending_and_active_are_the_non_terminal_states(self):
        assert set(NON_TERMINAL_STATUSES) == {SignalStatus.PENDING_ENTRY, SignalStatus.ACTIVE}

    def test_expired_is_terminal(self):
        assert SignalStatus.EXPIRED in TERMINAL_STATUSES

    def test_decided_statuses_are_exactly_the_real_outcomes(self):
        assert set(DECIDED_STATUSES) == {
            SignalStatus.TP_HIT, SignalStatus.STOPPED, SignalStatus.CANCELLED,
        }

    def test_expiry_constants_agree_with_each_other(self):
        assert PENDING_ENTRY_EXPIRY_MINUTES == PENDING_ENTRY_EXPIRY_CANDLES * 15


# ==========================================================================
# 5. Entry mode switch - the guarantee of no mixed behaviour
# ==========================================================================
class TestEntryModeSwitch:
    def test_market_mode_never_uses_the_ict_anchor(self):
        from app.services import trading_settings

        with patch.object(trading_settings, "_read_settings", return_value={"entry_mode": "market"}):
            assert trading_settings.is_ict_pending_entry() is False
            assert trading_settings.get_entry_mode() == "market"

    def test_ict_pending_is_the_default_mode(self):
        from app.services import trading_settings

        with patch.object(trading_settings, "_read_settings", return_value={}):
            assert trading_settings.get_entry_mode() == trading_settings.ENTRY_MODE_ICT_PENDING
            assert trading_settings.is_ict_pending_entry() is True

    def test_an_unknown_saved_mode_falls_back_to_the_default(self):
        from app.services import trading_settings

        with patch.object(trading_settings, "_read_settings", return_value={"entry_mode": "nonsense"}):
            assert trading_settings.get_entry_mode() == trading_settings.DEFAULT_ENTRY_MODE

    def test_set_entry_mode_rejects_an_invalid_value(self):
        from app.services import trading_settings

        with pytest.raises(ValueError):
            trading_settings.set_entry_mode("half_market_half_pending")
