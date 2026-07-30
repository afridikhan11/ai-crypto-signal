"""Maximum Drawdown - equity vs its all-time peak.

Uses EQUITY (realized + unrealized) against a persisted high-water mark,
which is what distinguishes it from Daily Loss (realized only, resets
daily).

UNKNOWN IS LOAD-BEARING HERE. Without real equity history there is no
honest peak, and reporting 0% would assert "no drawdown" when the truth is
"we do not know" - a fabrication this project's conventions forbid. The
metric reports UNKNOWN and is excluded from the verdict until
`equity_snapshots` has data."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "drawdown"


def evaluate_drawdown(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.is_available:
        return MetricResult(NAME, MetricStatus.UNKNOWN, "No account equity available.")
    if not ctx.peak_equity or ctx.peak_equity <= 0:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "No recorded equity peak yet - drawdown cannot be measured until equity "
            "history exists. Deliberately NOT reported as 0%.",
        )

    pct = max(0.0, (ctx.peak_equity - ctx.equity) / ctx.peak_equity * 100)
    limit = limits.max_drawdown_percent
    if pct >= limit:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"Current drawdown {pct:.2f}% has reached the maximum {limit}% from the peak equity "
            f"of {ctx.peak_equity:.2f}.",
            value=round(pct, 2), limit=limit,
            extra={"peak_equity": round(ctx.peak_equity, 2)},
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Current drawdown {pct:.2f}% of {limit}% tolerance (peak {ctx.peak_equity:.2f}).",
        value=round(pct, 2), limit=limit,
        extra={"peak_equity": round(ctx.peak_equity, 2)},
    )
