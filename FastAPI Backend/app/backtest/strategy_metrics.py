"""
Per-strategy backtest metrics for the Smart AI module.

Additive on top of app/backtest/performance_report.py: it reuses that module's
shared metrics (win rate, profit factor, expectancy, max drawdown) and adds the
three the Smart AI task asks for that it does not carry - a Sharpe ratio over
the trade R-multiples, the R-multiple distribution, and a PROMINENT
insufficient-sample flag when fewer than 100 trades resolved (so a tidy summary
can never hide a statistically meaningless result).

Everything here is pure and works on the same `trades` list shape the
backtester produces (each trade dict carries `realized_rr` and `outcome`), so
it is testable without any market data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.backtest.performance_report import (
    LOSS_OUTCOMES,
    WIN_OUTCOMES,
    build_performance_report,
)

# Below this many resolved trades the numbers are not yet meaningful; the task
# insists this be surfaced loudly rather than presented as a clean result.
MIN_MEANINGFUL_TRADES = 100

# R-multiple histogram edges (a trade's realized_rr falls into one bucket).
_R_BUCKETS = [
    ("<= -1R", float("-inf"), -1.0),
    ("-1R..0R", -1.0, 0.0),
    ("0R..1R", 0.0, 1.0),
    ("1R..2R", 1.0, 2.0),
    ("2R..3R", 2.0, 3.0),
    ("3R+", 3.0, float("inf")),
]


def _r_multiples(trades: List[Dict[str, Any]]) -> List[float]:
    out = []
    for t in trades:
        if t.get("outcome") in WIN_OUTCOMES + LOSS_OUTCOMES and t.get("realized_rr") is not None:
            out.append(float(t["realized_rr"]))
    return out


def sharpe_ratio(r_multiples: List[float]) -> Optional[float]:
    """Per-trade Sharpe over R-multiples: mean(R) / stdev(R). Not annualised -
    R-multiples are unitless per-trade returns and the trade record carries no
    dependable calendar spacing to annualise against. None when there are fewer
    than two trades or the returns have zero dispersion."""
    n = len(r_multiples)
    if n < 2:
        return None
    mean = sum(r_multiples) / n
    variance = sum((r - mean) ** 2 for r in r_multiples) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return round(mean / std, 3)


def r_multiple_distribution(trades: List[Dict[str, Any]]) -> Dict[str, int]:
    rs = _r_multiples(trades)
    dist = {label: 0 for label, _, _ in _R_BUCKETS}
    for r in rs:
        for label, lo, hi in _R_BUCKETS:
            if lo < r <= hi or (lo == float("-inf") and r <= hi):
                dist[label] += 1
                break
    return dist


@dataclass
class StrategyBacktestReport:
    strategy_id: str
    total_trades: int
    win_rate_pct: float
    profit_factor: Optional[float]
    expectancy_r: float
    max_drawdown_r: float
    sharpe_ratio: Optional[float]
    r_multiple_distribution: Dict[str, int]
    insufficient_sample: bool
    sample_warning: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "expectancy_r": self.expectancy_r,
            "max_drawdown_r": self.max_drawdown_r,
            "sharpe_ratio": self.sharpe_ratio,
            "r_multiple_distribution": self.r_multiple_distribution,
            "insufficient_sample": self.insufficient_sample,
            "sample_warning": self.sample_warning,
        }


def build_strategy_report(strategy_id: str, trades: List[Dict[str, Any]]) -> StrategyBacktestReport:
    base = build_performance_report(trades)
    rs = _r_multiples(trades)
    resolved = base.resolved_signals
    insufficient = resolved < MIN_MEANINGFUL_TRADES
    warning = (
        f"ONLY {resolved} resolved trades (< {MIN_MEANINGFUL_TRADES}). These "
        f"results are NOT statistically meaningful yet - do not act on them."
        if insufficient
        else None
    )
    return StrategyBacktestReport(
        strategy_id=strategy_id,
        total_trades=resolved,
        win_rate_pct=base.win_rate_pct,
        profit_factor=base.profit_factor,
        expectancy_r=base.expectancy_r,
        max_drawdown_r=base.max_drawdown_r,
        sharpe_ratio=sharpe_ratio(rs),
        r_multiple_distribution=r_multiple_distribution(trades),
        insufficient_sample=insufficient,
        sample_warning=warning,
    )
