"""
Backtest Performance Report tests (Phase 2, objective 4).

Builds synthetic trade lists in EXACTLY the shape `BacktestEngine.run()`'s
`trades.append({...})` produces (symbol/direction/entry/stop_loss/
take_profit/confidence/score_breakdown/outcome/realized_rr/entry_time/
exit_time) and hand-verifies every metric against a known-by-construction
expected value, so a silent arithmetic mistake in the report would fail a
test rather than only show up as a wrong number in a real report.
"""
from datetime import datetime, timedelta

import pytest

from app.backtest.performance_report import build_performance_report, _UNAVAILABLE_BREAKDOWNS


def _trade(symbol="BTCUSDT", direction="LONG", outcome="TP_HIT", rr=2.0, confidence=70,
           entry_time=None, exit_time=None):
    entry_time = entry_time or datetime(2026, 1, 1, 0, 0)
    exit_time = exit_time if exit_time is not None else (entry_time + timedelta(hours=3) if outcome != "UNRESOLVED" else None)
    return {
        "symbol": symbol, "direction": direction, "entry": 100.0, "stop_loss": 98.0,
        "take_profit": 104.0, "confidence": confidence, "score_breakdown": {},
        "outcome": outcome, "realized_rr": rr, "entry_time": entry_time, "exit_time": exit_time,
    }


class TestWinRateAndProfitFactor:
    def test_all_wins(self):
        trades = [_trade(outcome="TP_HIT", rr=2.0) for _ in range(5)]
        report = build_performance_report(trades)
        assert report.win_rate_pct == 100.0
        assert report.profit_factor == float("inf")

    def test_mixed_wins_and_losses(self):
        trades = [_trade(outcome="TP_HIT", rr=2.0)] * 3 + [_trade(outcome="STOPPED", rr=-1.0)] * 2
        report = build_performance_report(trades)
        assert report.win_rate_pct == 60.0
        # gross_profit = 3*2 = 6, gross_loss = 2*1 = 2 -> PF = 3.0
        assert report.profit_factor == 3.0
        # avg rr = (6 - 2) / 5 = 0.8
        assert report.average_realized_rr == 0.8
        assert report.expectancy_r == 0.8

    def test_unresolved_trades_excluded_from_win_rate_but_counted_in_total(self):
        trades = [_trade(outcome="TP_HIT", rr=2.0), _trade(outcome="UNRESOLVED", rr=0.0)]
        report = build_performance_report(trades)
        assert report.total_signals == 2
        assert report.resolved_signals == 1
        assert report.unresolved_signals == 1
        assert report.win_rate_pct == 100.0  # only the resolved trade counts


class TestMaxDrawdown:
    def test_drawdown_after_a_losing_streak(self):
        # Equity curve: +2, +2, -1, -1, -1, +2 -> peak 4, trough 1 -> dd = 3
        rrs = [2.0, 2.0, -1.0, -1.0, -1.0, 2.0]
        trades = [_trade(outcome="TP_HIT" if r > 0 else "STOPPED", rr=r) for r in rrs]
        report = build_performance_report(trades)
        assert report.max_drawdown_r == 3.0

    def test_no_drawdown_when_only_winning(self):
        trades = [_trade(outcome="TP_HIT", rr=1.0) for _ in range(4)]
        report = build_performance_report(trades)
        assert report.max_drawdown_r == 0.0

    def test_empty_trades_zero_drawdown(self):
        report = build_performance_report([])
        assert report.max_drawdown_r == 0.0
        assert report.win_rate_pct == 0.0
        assert report.profit_factor is None


class TestAverageHoldTime:
    def test_computes_mean_duration(self):
        t1 = _trade(entry_time=datetime(2026, 1, 1, 0, 0), exit_time=datetime(2026, 1, 1, 2, 0))  # 2h
        t2 = _trade(entry_time=datetime(2026, 1, 1, 0, 0), exit_time=datetime(2026, 1, 1, 4, 0))  # 4h
        report = build_performance_report([t1, t2])
        assert report.average_hold_time.startswith("3h 0m")

    def test_unresolved_excluded_from_hold_time(self):
        t1 = _trade(entry_time=datetime(2026, 1, 1, 0, 0), exit_time=datetime(2026, 1, 1, 2, 0))
        t2 = _trade(outcome="UNRESOLVED", rr=0.0)
        report = build_performance_report([t1, t2])
        assert "n=1" in report.average_hold_time

    def test_none_when_no_resolved_trades(self):
        report = build_performance_report([_trade(outcome="UNRESOLVED", rr=0.0)])
        assert report.average_hold_time is None


class TestLongVsShort:
    def test_separates_by_direction(self):
        trades = (
            [_trade(direction="LONG", outcome="TP_HIT", rr=2.0)] * 3
            + [_trade(direction="SHORT", outcome="STOPPED", rr=-1.0)] * 2
        )
        report = build_performance_report(trades)
        assert report.long_vs_short["LONG"]["win_rate_pct"] == 100.0
        assert report.long_vs_short["SHORT"]["win_rate_pct"] == 0.0
        assert report.long_vs_short["LONG"]["total"] == 3
        assert report.long_vs_short["SHORT"]["total"] == 2

    def test_direction_with_no_trades_reports_none_not_zero(self):
        trades = [_trade(direction="LONG", outcome="TP_HIT", rr=1.0)]
        report = build_performance_report(trades)
        assert report.long_vs_short["SHORT"]["total"] == 0
        assert report.long_vs_short["SHORT"]["win_rate_pct"] is None


class TestConfidenceBuckets:
    def test_buckets_group_in_five_point_bands(self):
        trades = [_trade(confidence=72, outcome="TP_HIT", rr=1.0), _trade(confidence=74, outcome="STOPPED", rr=-1.0)]
        report = build_performance_report(trades)
        assert "70-74" in report.by_confidence_bucket
        assert report.by_confidence_bucket["70-74"]["total"] == 2


class TestAssetPerformance:
    def test_groups_by_symbol(self):
        trades = (
            [_trade(symbol="BTCUSDT", outcome="TP_HIT", rr=2.0)] * 2
            + [_trade(symbol="XAUUSDT", outcome="STOPPED", rr=-1.0)]
        )
        report = build_performance_report(trades)
        assert report.by_asset["BTCUSDT"]["win_rate_pct"] == 100.0
        assert report.by_asset["XAUUSDT"]["win_rate_pct"] == 0.0


class TestUnavailableBreakdownsAreDisclosedNotFabricated:
    def test_unavailable_breakdowns_present_and_named(self):
        report = build_performance_report([_trade()])
        d = report.to_dict()
        for key in ("session_performance", "kill_zone_performance", "order_block_performance",
                    "fvg_performance", "ote_performance"):
            assert key in d["unavailable_breakdowns"]
            assert d["unavailable_breakdowns"][key]  # a concrete reason string, not empty

    def test_summary_mentions_unavailable_breakdowns(self):
        report = build_performance_report([_trade()])
        assert "UNAVAILABLE" in report.summary()
