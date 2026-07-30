"""
Backtest Performance Report - Phase 2 Validation (2026-07-30), objective 4.

STATUS: PRODUCTION (validation/reporting tooling). Additive-only: reads the
`trades` list `BacktestEngine.run()` already returns; does not modify
BacktestEngine, SignalGenerator, or any ICT engine.

WHAT THIS COMPUTES, AND WHAT IT HONESTLY CANNOT (YET)
--------------------------------------------------------------------------
Phase 2 objective 4 asks for: Win Rate, Profit Factor, Average RR,
Expectancy, Max Drawdown, Average Hold Time, Long vs Short, Session
Performance, Kill Zone Performance, Order Block Performance, FVG
Performance, OTE Performance, Asset Performance.

Each trade dict `BacktestEngine.run()` currently appends only carries:
symbol, direction, entry, stop_loss, take_profit, confidence,
score_breakdown, outcome, realized_rr, entry_time, exit_time (see
`BacktestEngine.run()`'s `trades.append({...})` block). It does NOT carry
the richer `signal_data` fields SignalGenerator actually computes per
signal - `session`, `decision` (the full TradeDecision, including
`evidence`), `institutional_bias`, `htf_alignment` - because BacktestEngine
discards them before appending. That gap is documented in
`BACKTEST_VALIDATION_REPORT.md`'s "Backtest vs Live Pipeline Diff" section
as a disclosed limitation of BacktestEngine, which is out of scope to
modify in this validation-only phase.

Consequently, this module computes every metric that IS derivable from the
current trade record honestly (Win Rate, Profit Factor, Average RR,
Expectancy, Max Drawdown, Average Hold Time, Long vs Short, Asset
Performance, Confidence-Bucket Performance), and reports Session/Kill
Zone/Order Block/FVG/OTE Performance as `UNAVAILABLE` with the exact
reason and the exact one-line extension BacktestEngine would need (listed,
never silently applied - modifying BacktestEngine is out of this phase's
scope).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

WIN_OUTCOMES = ("TP_HIT",)
LOSS_OUTCOMES = ("STOPPED",)

# Fields a trade record would need for these breakdowns to be computable -
# see this module's docstring. Listed so the report names exactly what is
# missing rather than only saying "unavailable".
_UNAVAILABLE_BREAKDOWNS = {
    "session_performance": "signal_data['session']",
    "kill_zone_performance": "signal_data['session'] (kill-zone label) or session_context.in_kill_zone",
    "order_block_performance": "signal_data['decision']['evidence'] entries tagged as order-block, or the raw ob_dict",
    "fvg_performance": "signal_data['decision']['evidence'] entries tagged as FVG, or the raw relevant_fvgs list",
    "ote_performance": "signal_data['decision']['evidence'] entries tagged as OTE, or the raw ote_zone",
}


@dataclass
class PerformanceReport:
    total_signals: int
    resolved_signals: int
    unresolved_signals: int
    win_rate_pct: float
    profit_factor: Optional[float]
    average_realized_rr: float
    expectancy_r: float
    max_drawdown_r: float
    average_hold_time: Optional[str]
    long_vs_short: Dict[str, Dict[str, Any]]
    by_confidence_bucket: Dict[str, Dict[str, Any]]
    by_asset: Dict[str, Dict[str, Any]]
    unavailable_breakdowns: Dict[str, str] = field(default_factory=lambda: dict(_UNAVAILABLE_BREAKDOWNS))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_signals": self.total_signals,
            "resolved_signals": self.resolved_signals,
            "unresolved_signals": self.unresolved_signals,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "average_realized_rr": self.average_realized_rr,
            "expectancy_r": self.expectancy_r,
            "max_drawdown_r": self.max_drawdown_r,
            "average_hold_time": self.average_hold_time,
            "long_vs_short": self.long_vs_short,
            "by_confidence_bucket": self.by_confidence_bucket,
            "by_asset": self.by_asset,
            "unavailable_breakdowns": self.unavailable_breakdowns,
        }

    def summary(self) -> str:
        lines = [
            f"Signals: {self.total_signals} total, {self.resolved_signals} resolved, "
            f"{self.unresolved_signals} unresolved",
            f"Win rate: {self.win_rate_pct}%",
            f"Profit factor: {self.profit_factor}",
            f"Average realized RR: {self.average_realized_rr}",
            f"Expectancy: {self.expectancy_r}R per trade",
            f"Max drawdown (R-multiple equity curve): {self.max_drawdown_r}R",
            f"Average hold time: {self.average_hold_time}",
            f"Long vs Short: {self.long_vs_short}",
        ]
        if self.unavailable_breakdowns:
            lines.append("UNAVAILABLE (see docstring for the exact field each needs): "
                         + ", ".join(self.unavailable_breakdowns))
        return "\n".join(lines)


def _direction_stats(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for direction in ("LONG", "SHORT"):
        subset = [t for t in trades if t["direction"] == direction and t["outcome"] != "UNRESOLVED"]
        wins = sum(1 for t in subset if t["outcome"] in WIN_OUTCOMES)
        out[direction] = {
            "total": len(subset),
            "wins": wins,
            "win_rate_pct": round(wins / len(subset) * 100, 2) if subset else None,
            "avg_realized_rr": round(sum(t["realized_rr"] for t in subset) / len(subset), 2) if subset else None,
        }
    return out


def _confidence_bucket_stats(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        if t["outcome"] == "UNRESOLVED":
            continue
        floor = (t["confidence"] // 5) * 5
        key = f"{floor}-{floor + 4}"
        b = buckets.setdefault(key, {"total": 0, "wins": 0})
        b["total"] += 1
        if t["outcome"] in WIN_OUTCOMES:
            b["wins"] += 1
    for b in buckets.values():
        b["win_rate_pct"] = round(b["wins"] / b["total"] * 100, 2) if b["total"] else None
    return dict(sorted(buckets.items()))


def _asset_stats(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    symbols = sorted({t["symbol"] for t in trades})
    for symbol in symbols:
        subset = [t for t in trades if t["symbol"] == symbol and t["outcome"] != "UNRESOLVED"]
        wins = sum(1 for t in subset if t["outcome"] in WIN_OUTCOMES)
        realized = [t["realized_rr"] for t in subset]
        out[symbol] = {
            "total": len(subset),
            "wins": wins,
            "win_rate_pct": round(wins / len(subset) * 100, 2) if subset else None,
            "avg_realized_rr": round(sum(realized) / len(realized), 2) if realized else None,
        }
    return out


def _average_hold_time(trades: List[Dict[str, Any]]) -> Optional[str]:
    durations = [
        (t["exit_time"] - t["entry_time"]) for t in trades
        if t["outcome"] != "UNRESOLVED" and t.get("exit_time") is not None and t.get("entry_time") is not None
    ]
    if not durations:
        return None
    avg_seconds = sum(d.total_seconds() for d in durations) / len(durations)
    hours, remainder = divmod(int(avg_seconds), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m (n={len(durations)})"


def _max_drawdown_r(trades: List[Dict[str, Any]]) -> float:
    """
    Max drawdown expressed in R-multiples over the trade-sequence equity
    curve (cumulative sum of realized_rr in the ORDER trades occurred,
    which is the order BacktestEngine.run() appends them in - i.e.
    chronological). This is a relative/R-multiple drawdown, not a dollar
    drawdown - no account size or position sizing is available at this
    layer (see app/services/position_sizing.py - sizing is computed later,
    from a real account balance, not during backtesting).
    """
    resolved = [t["realized_rr"] for t in trades if t["outcome"] != "UNRESOLVED"]
    if not resolved:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in resolved:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def build_performance_report(trades: List[Dict[str, Any]]) -> PerformanceReport:
    total = len(trades)
    resolved = [t for t in trades if t["outcome"] != "UNRESOLVED"]
    unresolved = total - len(resolved)

    wins = [t for t in resolved if t["outcome"] in WIN_OUTCOMES]
    losses = [t for t in resolved if t["outcome"] in LOSS_OUTCOMES]
    win_rate_pct = round(len(wins) / len(resolved) * 100, 2) if resolved else 0.0

    gross_profit = sum(t["realized_rr"] for t in wins if t["realized_rr"] > 0)
    gross_loss = abs(sum(t["realized_rr"] for t in losses if t["realized_rr"] < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))

    realized_all = [t["realized_rr"] for t in resolved]
    avg_rr = round(sum(realized_all) / len(realized_all), 2) if realized_all else 0.0
    expectancy = round(sum(realized_all) / len(realized_all), 4) if realized_all else 0.0

    return PerformanceReport(
        total_signals=total,
        resolved_signals=len(resolved),
        unresolved_signals=unresolved,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        average_realized_rr=avg_rr,
        expectancy_r=expectancy,
        max_drawdown_r=_max_drawdown_r(trades),
        average_hold_time=_average_hold_time(trades),
        long_vs_short=_direction_stats(trades),
        by_confidence_bucket=_confidence_bucket_stats(trades),
        by_asset=_asset_stats(trades),
    )
