"""
Risk limits + the coherence assertion.

STATUS (2026-07-30): PRODUCTION.

THE COHERENCE ASSERTION exists because of a real, diagnosed production
bug. `max_open_risk_percent` was unreachable: since

    leverage = open_risk / stop_fraction

a leverage ceiling of 50% bound roughly 9x tighter than the 6% open-risk
cap for typical ICT stops (0.3-1.5% of price), so the risk-based control -
the theoretically correct one for a stop-based strategy - was dead code
and nobody could tell. `assert_coherent()` fails LOUDLY at startup rather
than letting one limit silently mask another again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

# The stop distance the coherence check reasons about, as a fraction of
# price. Not a trading parameter - purely the reference point for "are
# these two limits mutually reachable", set to the loose end of this
# platform's real ICT structural stops so the assertion is conservative.
COHERENCE_REFERENCE_STOP_FRACTION = 0.02  # 2%


@dataclass(frozen=True)
class RiskLimits:
    # ---- Pre-2.0.0 limits (values unchanged) ----
    max_risk_percent_per_trade: float = 2.0
    max_open_risk_percent: float = 6.0
    max_daily_loss_percent: float = 5.0
    max_drawdown_percent: float = 20.0

    # ---- 2.0.0: replaces the mis-named `max_exposure_percent` ----
    # Gross notional / equity, expressed as a PERCENTAGE. This quantity IS
    # leverage x 100, so a value below 100 forbids leverage entirely on a
    # leveraged-futures product. 300% = 3x. See the architecture doc S4.1.
    max_leverage_percent: float = 300.0
    # Collateral locked as initial margin / equity. Solvency, not policy.
    max_margin_usage_percent: float = 60.0
    # maintenance_margin / margin_balance - Binance's own liquidation
    # proximity metric. Binance auto-deleverages well before 100%.
    max_margin_ratio_percent: float = 50.0
    # Share of total open risk held by one correlation cluster.
    max_cluster_concentration_percent: float = 50.0
    warn_cluster_concentration_percent: float = 35.0

    # ---- BACKWARD COMPATIBILITY ----
    # Retained so pre-2.0.0 readers (WPF RiskLimitsDto, GET /trading/status)
    # keep working. It is NO LONGER ENFORCED - `max_leverage_percent` above
    # is the control. Kept at its historical value so nothing that displays
    # it changes meaning silently.
    max_exposure_percent: float = 50.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def coherence_problems(self) -> List[str]:
        """Every way these limits would make one control unreachable."""
        problems: List[str] = []

        implied = self.max_open_risk_percent / COHERENCE_REFERENCE_STOP_FRACTION
        if self.max_leverage_percent < implied:
            problems.append(
                f"max_leverage_percent={self.max_leverage_percent}% makes "
                f"max_open_risk_percent={self.max_open_risk_percent}% UNREACHABLE: at a "
                f"{COHERENCE_REFERENCE_STOP_FRACTION * 100:.1f}% stop, reaching the open-risk "
                f"limit requires {implied:.0f}% leverage. Raise max_leverage_percent to at "
                f"least {implied:.0f}%, or lower max_open_risk_percent."
            )

        if self.max_risk_percent_per_trade > self.max_open_risk_percent:
            problems.append(
                f"max_risk_percent_per_trade={self.max_risk_percent_per_trade}% exceeds "
                f"max_open_risk_percent={self.max_open_risk_percent}% - a single trade could "
                f"never be opened."
            )

        if self.warn_cluster_concentration_percent > self.max_cluster_concentration_percent:
            problems.append(
                "warn_cluster_concentration_percent exceeds its blocking counterpart - the "
                "warning band would be unreachable."
            )

        for name in ("max_margin_usage_percent", "max_margin_ratio_percent"):
            value = getattr(self, name)
            if not 0 < value <= 100:
                problems.append(f"{name}={value} is outside the valid (0, 100] range.")

        return problems

    def assert_coherent(self) -> None:
        """Raise if any limit silently masks another. Called at startup."""
        problems = self.coherence_problems()
        if problems:
            raise ValueError(
                "Incoherent risk limits - one control would silently mask another:\n  - "
                + "\n  - ".join(problems)
            )
