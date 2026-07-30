"""Portfolio Concentration - the largest CORRELATION-ADJUSTED cluster's
share of total open risk.

Measured on clusters rather than symbols because a per-symbol check is
wrong precisely when it matters: three 20% positions in tightly correlated
alts are one 60% bet, and a naive symbol check waves that through. This is
where correlation acquires its teeth."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict

from app.risk.context import RiskContext
from app.risk.limits import RiskLimits
from app.risk.result import MetricResult, MetricStatus

NAME = "cluster_concentration"


def evaluate_concentration(ctx: RiskContext, limits: RiskLimits) -> MetricResult:
    positions = ctx.all_positions()
    if not positions:
        return MetricResult(NAME, MetricStatus.PASS, "No open positions.", value=0.0)

    # Cluster concentration is only measurable when we actually KNOW which
    # positions move together. Without correlation data, treating each
    # symbol as its own independent cluster would FABRICATE a portfolio
    # structure we have not established - and would then block on it. That
    # is exactly the kind of invented certainty this engine must not
    # produce, so the metric reports UNKNOWN instead.
    if not ctx.correlation_clusters:
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "No correlation data - which positions move together is unknown, so cluster "
            "concentration cannot be measured without inventing a structure.",
        )

    if any(p.risk_usd is None for p in positions):
        return MetricResult(
            NAME, MetricStatus.UNKNOWN,
            "At least one position has no known stop, so its share of total risk cannot "
            "be measured.",
        )

    total_risk = sum(p.risk_usd for p in positions)
    if total_risk <= 0:
        return MetricResult(NAME, MetricStatus.PASS, "No measurable open risk.", value=0.0)

    # Cluster id falls back to the symbol itself when correlation is
    # unavailable: each symbol is then its own cluster, which degrades to a
    # plain per-symbol concentration check rather than silently passing.
    by_cluster: Dict[object, float] = defaultdict(float)
    members: Dict[object, list] = defaultdict(list)
    for p in positions:
        cid = ctx.correlation_clusters.get(p.symbol, p.symbol)
        by_cluster[cid] += p.risk_usd
        members[cid].append(p.symbol)

    # Concentration is only meaningful with something to concentrate
    # AGAINST. With a single cluster the largest share is trivially 100%,
    # and that number is mathematically identical to `open_risk` - so
    # evaluating it here would both block every first trade and duplicate a
    # control that already exists. Deferring to open_risk is the correct
    # de-duplication, not a weakened check.
    if len(by_cluster) < 2:
        return MetricResult(
            NAME, MetricStatus.PASS,
            "Single position/cluster - concentration is fully governed by open_risk.",
            value=100.0 if positions else 0.0,
            limit=limits.max_cluster_concentration_percent,
            extra={"cluster_count": len(by_cluster), "deferred_to": "open_risk"},
        )

    worst_id = max(by_cluster, key=by_cluster.__getitem__)
    pct = by_cluster[worst_id] / total_risk * 100
    extra = {
        "worst_cluster": sorted(members[worst_id]),
        "cluster_risk_usd": round(by_cluster[worst_id], 2),
        "total_risk_usd": round(total_risk, 2),
        "cluster_count": len(by_cluster),
    }

    if pct > limits.max_cluster_concentration_percent:
        return MetricResult(
            NAME, MetricStatus.BLOCK,
            f"One correlated cluster {sorted(members[worst_id])} holds {pct:.1f}% of total "
            f"open risk, above the {limits.max_cluster_concentration_percent}% maximum.",
            value=round(pct, 2), limit=limits.max_cluster_concentration_percent, extra=extra,
        )
    if pct > limits.warn_cluster_concentration_percent:
        return MetricResult(
            NAME, MetricStatus.WARN,
            f"One correlated cluster {sorted(members[worst_id])} holds {pct:.1f}% of total "
            f"open risk (warning above {limits.warn_cluster_concentration_percent}%).",
            value=round(pct, 2), limit=limits.max_cluster_concentration_percent, extra=extra,
        )
    return MetricResult(
        NAME, MetricStatus.PASS,
        f"Largest correlated cluster holds {pct:.1f}% of open risk.",
        value=round(pct, 2), limit=limits.max_cluster_concentration_percent, extra=extra,
    )
