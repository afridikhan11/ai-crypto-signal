"""Correlation Risk - ADVISORY ONLY.

Preserves `app/services/correlation_risk.py`'s explicit, long-standing
design decision that correlation never blocks. Its teeth live in
`concentration`, which clusters by correlation so that several correlated
positions are measured as the single bet they actually are. That is what
removes the duplicate control - correlation is an INPUT to concentration,
not a parallel check."""
from __future__ import annotations

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "correlation"


def evaluate_correlation(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    if not ctx.correlation_clusters:
        # A caller may still hand us an already-computed warning string
        # (app/services/correlation_risk.py's existing advisory output).
        # Surface it verbatim rather than discarding it - it is real
        # information even without the full cluster map.
        if ctx.correlation_warning:
            return MetricResult(
                NAME, MetricStatus.WARN, ctx.correlation_warning,
                extra={"source": "precomputed_advisory"},
            )
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "No correlation data available (needs a live data manager with cached candles).",
        )

    cluster_count = len(set(ctx.correlation_clusters.values()))
    symbol_count = len(ctx.correlation_clusters)
    correlated = symbol_count - cluster_count

    if correlated > 0:
        return MetricResult(
            NAME, MetricStatus.WARN,
            ctx.correlation_warning
            or f"{symbol_count} symbols collapse into {cluster_count} correlated cluster(s) - "
               f"they are behaving as fewer independent bets than they appear.",
            value=float(correlated),
            extra={"clusters": cluster_count, "symbols": symbol_count},
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"All {symbol_count} symbol(s) are behaving independently.",
        value=0.0, extra={"clusters": cluster_count, "symbols": symbol_count},
    )
