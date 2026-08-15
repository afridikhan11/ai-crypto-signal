"""
Unified Risk Engine - the ONE risk engine in this codebase.

STATUS (2026-07-30): PRODUCTION, version 2.0.0. The class lives HERE and
only here - `tests/test_legacy_isolation.py` enforces that exactly one
file defines `class RiskEngine`, the same one-of-each invariant this
project applies to its scanners and AI. `app/risk/engine.py` holds only
the metric PIPELINE (an internal implementation detail); every metric is a
pure function in `app/risk/metrics/`.

See INSTITUTIONAL_RISK_ENGINE_ARCHITECTURE.md for the full design and for
the proof that the eight named metrics are seven independent controls.

EVALUATION vs REPORTING ORDER
--------------------------------------------------------------------------
Every metric ALWAYS runs - nothing short-circuits - so a rejection names
every constraint it breached rather than only the first. Order affects
reporting priority: a daily-loss halt is reported before a leverage
breach, because when trading is halted everything else is a consequence
rather than an actionable cause.

  Tier 1 HALT      daily_loss, drawdown
  Tier 2 SOLVENCY  margin_ratio, margin_usage
  Tier 3 MANDATE   open_risk, effective_leverage
  Tier 4 QUALITY   cluster_concentration
  Tier 5 ADVISORY  correlation

UNKNOWN NEVER PASSES SILENTLY. An UNKNOWN metric cannot block (it has no
evidence to block on) but is reported in `unknown_notices`, persisted in
the snapshot, and logged - so a decision made on incomplete information is
always visible as such. It is deliberately kept OUT of `reasons`, which
stays blocking/warning-only for backward compatibility.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from app.risk.context import PositionRisk, RiskContext
from app.risk.engine import METRIC_PIPELINE as _METRIC_PIPELINE
from app.risk.limits import RiskLimits
from app.risk.result import RISK_ENGINE_VERSION, MetricResult, MetricStatus, RiskAssessment
from app.services.position_sizing import calculate_position_size

__all__ = [
    "RiskEngine", "RiskLimits", "RiskAssessment", "RiskContext",
    "PositionRisk", "MetricResult", "MetricStatus", "RISK_ENGINE_VERSION",
]


class RiskEngine:
    """Stateless. `assess()` called twice with the same context returns an
    equivalent `RiskAssessment`."""

    def __init__(self, limits: Optional[RiskLimits] = None) -> None:
        self.limits = limits or RiskLimits()

    # ------------------------------------------------------------------
    # 2.0.0 primary entry point
    # ------------------------------------------------------------------
    def assess(self, ctx: RiskContext) -> RiskAssessment:
        metrics: List[MetricResult] = [m(ctx, self.limits) for m in _METRIC_PIPELINE]

        # `reasons` carries BLOCK + WARN only, preserving the pre-2.0.0
        # contract that an approved, unremarkable trade has an empty list.
        # UNKNOWN metrics are NOT silently dropped - they get their own
        # field, appear in the persisted snapshot, and are logged on every
        # decision. Mixing them into `reasons` made every clean trade look
        # like it had five problems.
        reasons = [m.detail for m in metrics if m.status in (MetricStatus.BLOCK, MetricStatus.WARN)]

        position_size = None
        if ctx.candidate is not None:
            position_size = ctx.candidate.extra_size if hasattr(ctx.candidate, "extra_size") else None

        approved = not any(m.blocks for m in metrics)

        def _value(name: str, default: float = 0.0) -> float:
            m = next((x for x in metrics if x.name == name), None)
            return default if m is None or m.value is None else m.value

        return RiskAssessment(
            approved=approved,
            reasons=reasons,
            position_size=position_size,
            metrics=metrics,
            risk_engine_version=RISK_ENGINE_VERSION,
            open_risk_percent=_value("open_risk"),
            exposure_percent=_value("effective_leverage"),
            daily_pnl_percent=_value("daily_loss"),
            current_drawdown_percent=_value("drawdown"),
            correlation_warning=ctx.correlation_warning,
            context_source=ctx.context_source,
            exchange_snapshot_at=ctx.exchange_snapshot_at,
            unknown_notices=[
                f"{m.name}: NOT EVALUATED - {m.detail}"
                for m in metrics if m.status is MetricStatus.UNKNOWN
            ],
        )

    # ------------------------------------------------------------------
    # Backward-compatible entry point (pre-2.0.0 signature, unchanged)
    # ------------------------------------------------------------------
    def assess_new_trade(
        self,
        account_balance: Optional[float],
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        risk_percent: Optional[float] = None,
        open_positions: Optional[List[Dict[str, float]]] = None,
        closed_trades_today: Optional[List[Dict[str, float]]] = None,
        equity_peak: Optional[float] = None,
        correlation_warning: Optional[str] = None,
        context: Optional[RiskContext] = None,
        max_notional: Optional[float] = None,
    ) -> RiskAssessment:
        """
        Preserved verbatim so `execution_risk.py`, `signal_service.py` and
        every existing test keep working unchanged.

        `max_notional` (optional) caps the sized position's exposure so a
        tight-stop trade is shrunk to fit a leverage ceiling instead of being
        rejected. Must match the cap the caller applied to `context.candidate`
        so the returned `position_size` and the leverage the engine assessed
        describe the SAME position.

        Callers that can supply the richer `context` (real Binance margin,
        leverage, maintenance margin, correlation clusters) get the full
        eight-metric assessment. Callers that cannot get exactly the
        pre-2.0.0 controls, with the margin/leverage/liquidation metrics
        honestly reported as UNKNOWN rather than fabricated.
        """
        effective_risk_percent = (
            risk_percent if risk_percent is not None else self.limits.max_risk_percent_per_trade
        )
        size = calculate_position_size(
            account_balance, entry_price, stop_loss, effective_risk_percent, take_profit,
            max_notional=max_notional,
        )

        if context is None:
            context = self._context_from_legacy_args(
                account_balance, size, open_positions, closed_trades_today,
                equity_peak, correlation_warning, effective_risk_percent,
            )

        assessment = self.assess(context)

        reasons = list(assessment.reasons)
        if size is None:
            # Split from the old combined message - conflating "no balance"
            # with "degenerate stop" is what made the breakeven bug so hard
            # to diagnose.
            if account_balance is None or account_balance <= 0:
                reasons.insert(0, "Position size unavailable: account balance is unknown.")
            else:
                reasons.insert(
                    0,
                    "Position size unavailable: entry and stop are identical, so risk "
                    "distance is zero.",
                )

        return RiskAssessment(
            approved=assessment.approved and size is not None,
            reasons=reasons,
            position_size=size,
            metrics=assessment.metrics,
            risk_engine_version=RISK_ENGINE_VERSION,
            open_risk_percent=assessment.open_risk_percent,
            exposure_percent=assessment.exposure_percent,
            daily_pnl_percent=assessment.daily_pnl_percent,
            current_drawdown_percent=assessment.current_drawdown_percent,
            correlation_warning=correlation_warning,
            context_source=assessment.context_source,
            exchange_snapshot_at=assessment.exchange_snapshot_at,
            unknown_notices=list(assessment.unknown_notices),
        )

    def _context_from_legacy_args(
        self, account_balance, size, open_positions, closed_trades_today,
        equity_peak, correlation_warning, risk_percent,
    ) -> RiskContext:
        """Build a RiskContext from the pre-2.0.0 argument shape.

        Margin, leverage and maintenance margin are genuinely unavailable
        here, so they stay None and their metrics report UNKNOWN - never
        fabricated as zero."""
        positions = tuple(
            PositionRisk(
                symbol=str(p.get("symbol", f"position_{i}")),
                notional_usd=float(p.get("notional_usd", 0) or 0),
                risk_usd=float(p.get("risk_usd", 0) or 0),
            )
            for i, p in enumerate(open_positions or [])
        )
        candidate = None
        if size is not None:
            candidate = PositionRisk(
                symbol="__candidate__",
                notional_usd=size["notional_usd"],
                risk_usd=size["risk_usd"],
                is_candidate=True,
            )

        realized = None
        if closed_trades_today is not None:
            realized = sum(t.get("realized_pnl_usd", 0) or 0 for t in closed_trades_today)

        return RiskContext(
            equity=account_balance,
            wallet_balance=account_balance,
            open_positions=positions,
            candidate=candidate,
            risk_percent=risk_percent,
            realized_pnl_today_usd=realized,
            peak_equity=equity_peak,
            correlation_warning=correlation_warning,
        )
