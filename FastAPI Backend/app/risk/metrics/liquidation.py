"""Liquidation Margin Ratio - maintenance margin / margin balance.

Binance's OWN liquidation-proximity metric, already fetched by this
project (`total_maintenance_margin`, `margin_balance`) and already
displayed on the Dashboard - but never previously consulted by the risk
engine. Existential: nothing else matters if the account is liquidated."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "margin_ratio"


def evaluate_liquidation_ratio(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")
    if ctx.total_maintenance_margin is None:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "Binance did not report total maintenance margin for this account.",
        )

    pct = ctx.total_maintenance_margin / ctx.equity * 100
    limit = limits.max_margin_ratio_percent
    if pct > limit:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Margin ratio {pct:.2f}% exceeds the maximum {limit}% - the account is too "
            f"close to forced liquidation to add risk.",
            value=round(pct, 2), limit=limit,
            extra={"maintenance_margin_usd": round(ctx.total_maintenance_margin, 2)},
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Margin ratio {pct:.2f}% of {limit}% ceiling (liquidation at 100%).",
        value=round(pct, 2), limit=limit,
        extra={"maintenance_margin_usd": round(ctx.total_maintenance_margin, 2)},
    )
