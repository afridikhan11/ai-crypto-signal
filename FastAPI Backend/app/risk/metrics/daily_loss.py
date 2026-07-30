"""Daily Loss - REALIZED loss booked since UTC midnight, as % of equity.

Realized-only by design. That is what separates it from Drawdown (which
uses equity, i.e. realized + unrealized): without the split, a single bad
day would be counted twice by two controls claiming to measure different
things."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "daily_loss"


def evaluate_daily_loss(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")
    if ctx.realized_pnl_today_usd is None:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "Today's realized PnL is unavailable.")

    pct = ctx.realized_pnl_today_usd / ctx.equity * 100
    limit = limits.max_daily_loss_percent
    if pct <= -abs(limit):
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Daily loss {pct:.2f}% has reached the maximum {limit}% - trading is halted "
            f"for the rest of the UTC day.",
            value=round(pct, 2), limit=limit,
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Today's realized PnL {pct:+.2f}% (halt at -{limit}%).",
        value=round(pct, 2), limit=limit,
    )
