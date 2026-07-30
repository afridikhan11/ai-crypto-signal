"""
Risk metric results - the vocabulary every metric speaks.

STATUS (2026-07-30): PRODUCTION. Part of the Institutional Risk Engine
(see INSTITUTIONAL_RISK_ENGINE_ARCHITECTURE.md).

A metric NEVER returns a bare number. It returns a `MetricResult` carrying
the measured value, the limit it was measured against, and a status. This
is what makes UNKNOWN a first-class outcome instead of a silent zero: a
metric whose inputs are unavailable reports UNKNOWN and is excluded from
the verdict, rather than passing as 0 and claiming safety it cannot
verify.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Bump on ANY change to a metric formula, a status rule, or the set of
# blocking checks. Persisted with every decision so a historical rejection
# can always be re-read against the rules that actually produced it.
RISK_ENGINE_VERSION = "2.0.0"


class MetricStatus(str, enum.Enum):
    PASS = "PASS"
    WARN = "WARN"        # surfaced to the user, never blocks
    BLOCK = "BLOCK"      # prevents execution
    UNKNOWN = "UNKNOWN"  # inputs unavailable - excluded from the verdict, never treated as PASS


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: MetricStatus
    detail: str
    value: Optional[float] = None
    limit: Optional[float] = None
    # Free-form, metric-specific evidence (e.g. per-cluster breakdown).
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        return self.status is MetricStatus.BLOCK

    @property
    def is_known(self) -> bool:
        return self.status is not MetricStatus.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "value": self.value,
            "limit": self.limit,
            "detail": self.detail,
            "extra": self.extra or {},
        }


@dataclass(frozen=True)
class RiskAssessment:
    """
    The complete, self-describing outcome of one risk evaluation.

    `metric_snapshot` is the MANDATORY audit record: the exact values used
    to reach this decision, captured at decision time. It is persisted
    verbatim and must never be recomputed later - a later recomputation
    would use a different account state and different rules, which is
    precisely what an audit trail exists to prevent.
    """

    approved: bool
    reasons: List[str]
    position_size: Optional[Dict[str, float]]
    metrics: List[MetricResult] = field(default_factory=list)
    risk_engine_version: str = RISK_ENGINE_VERSION

    # ---- Backward-compatible fields (pre-2.0.0 callers read these) ----
    open_risk_percent: float = 0.0
    exposure_percent: float = 0.0
    daily_pnl_percent: float = 0.0
    current_drawdown_percent: float = 0.0
    correlation_warning: Optional[str] = None

    # Metrics that could not be evaluated. Kept OUT of `reasons` (which is
    # blocking/warning only, preserving the pre-2.0.0 contract) but never
    # discarded: they are logged on every decision and persisted in the
    # snapshot, so a decision taken with incomplete information is always
    # visible as such.
    unknown_notices: List[str] = field(default_factory=list)

    # Provenance of the inputs behind this verdict - see RiskContext.
    context_source: str = "database_fallback"
    exchange_snapshot_at: Optional[Any] = None

    def metric(self, name: str) -> Optional[MetricResult]:
        return next((m for m in self.metrics if m.name == name), None)

    @property
    def blocking_metrics(self) -> List[MetricResult]:
        return [m for m in self.metrics if m.blocks]

    @property
    def warnings(self) -> List[MetricResult]:
        return [m for m in self.metrics if m.status is MetricStatus.WARN]

    @property
    def unknown_metrics(self) -> List[MetricResult]:
        return [m for m in self.metrics if not m.is_known]

    def metric_snapshot(self, limits: Optional[Any] = None) -> Dict[str, Any]:
        """The structured audit record persisted with every decision."""
        return {
            "risk_engine_version": self.risk_engine_version,
            "approved": self.approved,
            "context_source": self.context_source,
            "exchange_snapshot_timestamp": (
                self.exchange_snapshot_at.isoformat()
                if hasattr(self.exchange_snapshot_at, "isoformat")
                else self.exchange_snapshot_at
            ),
            "metrics": {m.name: m.to_dict() for m in self.metrics},
            "status": {m.name: m.status.value for m in self.metrics},
            "limits": limits.to_dict() if limits is not None and hasattr(limits, "to_dict") else {},
            "position_size": self.position_size,
            "reasons": list(self.reasons),
            "unknown": [m.name for m in self.unknown_metrics],
            "unknown_notices": list(self.unknown_notices),
        }
