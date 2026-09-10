"""
Exit record + management ledger (2026-09-10).

The first clean measurement era showed 60% of executed trades ending in a
structure-failure exit - and no P/L for any of them, because a CANCELLED
signal stored no exit level. The most common outcome was the one we could
not see. This suite covers the three pieces that close that gap:

  1. `Signal.exit_price` / `exit_reason`, set once at the terminal transition.
  2. `signal_events`, one row per management decision with the live price.
  3. `signal_stats` reading both, so a structure exit finally has a P/L and
     each rule gets its own ledger.
"""
import importlib.util
import os
from types import SimpleNamespace

import pytest

from app.models.base import Base
from app.models.signal import Direction, Signal, SignalStatus
from app.models.signal_event import SignalEvent
from app.scheduler.signal_monitor import SignalMonitor


def _load_stats_module():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "signal_stats.py")
    spec = importlib.util.spec_from_file_location("signal_stats_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ======================================================================
# 1. Exit record - set once, never overwritten
# ======================================================================
class TestRecordExit:
    def test_sets_price_and_reason(self):
        sig = SimpleNamespace(exit_price=None, exit_reason=None)
        SignalMonitor._record_exit(sig, 101.5, "structure_failure: opposing BOS")
        assert sig.exit_price == 101.5
        assert sig.exit_reason == "structure_failure: opposing BOS"

    def test_never_overwrites_the_first_record(self):
        # A resolution followed by a reconcile on a later poll must not
        # rewrite history - the FIRST terminal transition is the real one.
        sig = SimpleNamespace(exit_price=100.0, exit_reason="tp_hit")
        SignalMonitor._record_exit(sig, 95.0, "reconciled")
        assert sig.exit_price == 100.0
        assert sig.exit_reason == "tp_hit"

    def test_reason_is_bounded_to_the_column(self):
        sig = SimpleNamespace(exit_price=None, exit_reason=None)
        SignalMonitor._record_exit(sig, 1.0, "x" * 500)
        assert len(sig.exit_reason) == 160


# ======================================================================
# 2. Event ledger
# ======================================================================
class TestRecordEvent:
    def test_a_detached_signal_is_skipped_not_raised(self):
        # No session -> nothing to append to. Must never throw into the
        # trading path.
        SignalMonitor._record_event(Signal(), "move_to_breakeven", 100.0, reason="r")

    def test_table_is_registered_with_the_expected_columns(self):
        table = Base.metadata.tables["signal_events"]
        cols = set(table.columns.keys())
        assert {"id", "signal_id", "event_type", "price", "stop_before", "stop_after", "reason", "created_at"} <= cols
        assert table.columns["price"].nullable is False
        assert table.columns["event_type"].nullable is False

    def test_signal_carries_exit_columns(self):
        table = Base.metadata.tables["signals"]
        assert "exit_price" in table.columns
        assert "exit_reason" in table.columns
        assert table.columns["exit_price"].nullable is True

    def test_event_model_round_trips_fields(self):
        ev = SignalEvent(event_type="trail_stop", price=10.0, stop_before=9.0, stop_after=9.5, reason="BOS")
        assert (ev.event_type, ev.price, ev.stop_before, ev.stop_after) == ("trail_stop", 10.0, 9.0, 9.5)


# ======================================================================
# 3. signal_stats reads the record
# ======================================================================
def _sig(status, direction, entry, exit_price=None, take_profit=None, stop_loss=None):
    return SimpleNamespace(
        status=status, direction=direction, entry_price=entry, actual_fill_price=None,
        take_profit=take_profit, stop_loss=stop_loss, exit_price=exit_price,
        tp1_done=False, tp1_price=None,
    )


class TestPnlUsesRecordedExit:
    def setup_method(self):
        self.stats = _load_stats_module()

    def test_cancelled_with_a_recorded_exit_has_a_real_pnl(self):
        # The whole point: a structure exit is no longer "n/a".
        long_exit = self.stats._pnl_pct(_sig(SignalStatus.CANCELLED, Direction.LONG, 100.0, exit_price=101.0))
        short_exit = self.stats._pnl_pct(_sig(SignalStatus.CANCELLED, Direction.SHORT, 100.0, exit_price=101.0))
        assert long_exit == pytest.approx(1.0)
        assert short_exit == pytest.approx(-1.0)   # a short that exited higher LOST

    def test_legacy_cancelled_without_a_record_stays_unknown(self):
        assert self.stats._pnl_pct(_sig(SignalStatus.CANCELLED, Direction.LONG, 100.0)) is None

    def test_recorded_exit_takes_precedence_over_the_planned_level(self):
        # TP_HIT resolved at the live price, which can differ from take_profit
        # (a gap through it). The record is what actually happened.
        sig = _sig(SignalStatus.TP_HIT, Direction.LONG, 100.0, exit_price=103.0, take_profit=102.0)
        assert self.stats._pnl_pct(sig) == pytest.approx(3.0)

    def test_pre_record_rows_still_fall_back_to_planned_levels(self):
        sig = _sig(SignalStatus.STOPPED, Direction.LONG, 100.0, stop_loss=99.0)
        assert self.stats._pnl_pct(sig) == pytest.approx(-1.0)


class TestEventSummary:
    def setup_method(self):
        self.stats = _load_stats_module()

    def test_counts_and_averages_per_rule(self):
        out = self.stats._summarise_events([
            ("close_structure_failure", -0.4),
            ("close_structure_failure", +0.2),
            ("close_structure_failure", None),     # unknown move still counts as fired
            ("move_to_breakeven", 1.1),
        ])
        sf = out["close_structure_failure"]
        assert sf["n"] == 3
        assert sf["measured"] == 2
        assert sf["avg_move_pct"] == pytest.approx(-0.1)
        assert out["move_to_breakeven"] == {"n": 1, "avg_move_pct": pytest.approx(1.1), "measured": 1}

    def test_a_rule_with_no_measured_moves_reports_none(self):
        out = self.stats._summarise_events([("reconciled", None)])
        assert out["reconciled"]["avg_move_pct"] is None
        assert out["reconciled"]["n"] == 1

    def test_empty_input(self):
        assert self.stats._summarise_events([]) == {}
