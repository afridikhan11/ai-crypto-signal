"""Capital Margin Usage - collateral locked / equity.

A SOLVENCY constraint: if the collateral is not there, Binance rejects the
order regardless of any policy this platform holds. Distinct from leverage
only because exchange leverage is per-symbol and configured outside this
platform (`binance_trading_service.py` never calls /fapi/v1/leverage)."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "margin_usage"


def evaluate_margin_usage(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")

    if not ctx.has_exchange_data:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "No exchange snapshot available - collateral in use cannot be read. "
            "Deliberately NOT reported as 0%.",
        )

    total_margin = ctx.total("initial_margin_usd")
    if total_margin is None:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "Initial margin unknown for at least one position - exchange leverage is "
            "configured outside this platform and was not reported.",
        )

    pct = total_margin / ctx.equity * 100
    limit = limits.max_margin_usage_percent
    if pct > limit:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Margin usage {pct:.2f}% would exceed the maximum {limit}% - too little "
            f"free collateral to absorb adverse movement.",
            value=round(pct, 2), limit=limit,
            extra={"initial_margin_usd": round(total_margin, 2)},
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Margin usage {pct:.2f}% of {limit}% ceiling.",
        value=round(pct, 2), limit=limit,
        extra={"initial_margin_usd": round(total_margin, 2)},
    )
