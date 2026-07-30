"""Effective Portfolio Leverage - total notional / equity.

This quantity IS leverage x 100. A limit below 100% forbids leverage
entirely, which is what the pre-2.0.0 `max_exposure_percent=50` did while
being described as "exposure". It is a POLICY ceiling, not a solvency
constraint - see margin_usage for the latter."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "effective_leverage"


def evaluate_leverage(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")

    total_notional = ctx.total("notional_usd")
    if total_notional is None:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "Position notional unavailable.")

    pct = total_notional / ctx.equity * 100
    limit = limits.max_leverage_percent
    detail_x = f"{pct / 100:.2f}x"
    if pct > limit:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Effective leverage {pct:.1f}% ({detail_x}) would exceed the maximum "
            f"{limit}% ({limit / 100:.1f}x).",
            value=round(pct, 2), limit=limit,
            extra={"leverage_x": round(pct / 100, 2), "notional_usd": round(total_notional, 2)},
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Effective leverage {pct:.1f}% ({detail_x}) of {limit}% ceiling.",
        value=round(pct, 2), limit=limit,
        extra={"leverage_x": round(pct / 100, 2), "notional_usd": round(total_notional, 2)},
    )
