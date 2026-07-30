"""Maximum Open Risk - if every open stop is hit, how much is gone?

THE primary control for a stop-based strategy: leverage-independent, and
the only metric that measures what is genuinely at risk rather than how
large the positions are."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "open_risk"


def evaluate_open_risk(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")

    total_risk = ctx.total("risk_usd")
    if total_risk is None:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "At least one open position has no known stop, so total risk cannot be "
            "summed without understating it.",
        )

    pct = total_risk / ctx.equity * 100
    limit = limits.max_open_risk_percent
    if pct > limit:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Open risk {pct:.2f}% would exceed the maximum {limit}%.",
            value=round(pct, 2), limit=limit,
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Open risk {pct:.2f}% of {limit}% budget.",
        value=round(pct, 2), limit=limit,
    )
