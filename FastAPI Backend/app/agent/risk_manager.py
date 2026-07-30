"""
Risk Manager (Phase 2, 2026-07-27).

Pure facade over risk logic that already exists in this project - every
method below delegates to a real, previously-shipped implementation. This
file adds no new risk MATH beyond `validate_risk_reward` (a one-line check
that didn't previously exist as a standalone reusable function - it was
only ever inlined as a hard gate inside SignalGenerator.generate()) and
`composite_risk_score` (a documented, disclosed combination of the real
flags below - not a new risk calculation).

Delegates to:
  - app.services.position_sizing.calculate_position_size - position sizing.
  - app.services.trading_settings.get_risk_percent - max risk per trade
    (the account-wide risk-percent setting).
  - app.services.signal_service.SignalService.get_portfolio_risk -
    portfolio exposure across all currently-ACTIVE signals.
  - app.services.signal_service.SignalService.get_daily_loss - daily
    realized-loss limit tracking.
  - app.services.correlation_risk.attach_correlation_warnings - correlation
    checks across active signals (needs the live scanner's data_manager;
    None-safe if the scanner isn't available, e.g. for a read-only agent
    query with no live scanner attached).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.calibration_profiles import CalibrationProfile
from app.services.correlation_risk import attach_correlation_warnings
from app.services.position_sizing import calculate_position_size
from app.services.signal_service import SignalService
from app.services.trading_settings import get_risk_percent
from app.schemas.signal import DailyLossResponse, PortfolioRiskResponse


@dataclass
class AgentRiskAssessment:
    """Bundled risk read for one candidate trade - everything the Decision
    Engine/Explainer needs, all sourced from the delegations above."""
    risk_percent: float
    min_risk_reward: float
    risk_reward: Optional[float]
    risk_reward_ok: Optional[bool]
    position: Optional[Dict[str, float]]
    portfolio_risk: Optional[PortfolioRiskResponse]
    daily_loss: Optional[DailyLossResponse]
    correlation_warning: Optional[str]
    warnings: list[str]


class RiskManager:
    def __init__(self, session: AsyncSession, data_manager: Any = None):
        self._signal_service = SignalService(session)
        self.data_manager = data_manager

    @staticmethod
    def validate_risk_reward(risk_reward: Optional[float], profile: CalibrationProfile) -> Optional[bool]:
        """None if risk_reward is unknown (never guess pass/fail on missing data)."""
        if risk_reward is None:
            return None
        return risk_reward >= profile.min_risk_reward

    def suggest_position_size(
        self,
        account_balance: Optional[float],
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        risk_percent: Optional[float] = None,
    ) -> Optional[Dict[str, float]]:
        return calculate_position_size(
            account_balance, entry_price, stop_loss,
            risk_percent if risk_percent is not None else get_risk_percent(),
            take_profit=take_profit,
        )

    async def portfolio_exposure(self) -> PortfolioRiskResponse:
        return await self._signal_service.get_portfolio_risk()

    async def daily_risk_status(self) -> DailyLossResponse:
        return await self._signal_service.get_daily_loss()

    def attach_correlation(self, signals: Sequence) -> None:
        """No-op (not an error) if this RiskManager wasn't given a live
        data_manager - correlation needs real recent candles, which only the
        live scanner already has cached; a read-only agent query with no
        attached scanner simply skips this check rather than fabricating a
        result."""
        if self.data_manager is None:
            return
        attach_correlation_warnings(signals, self.data_manager)

    @staticmethod
    def composite_risk_score(
        risk_reward_ok: Optional[bool],
        daily_loss: Optional[DailyLossResponse],
        portfolio_risk: Optional[PortfolioRiskResponse],
        correlation_warning: Optional[str],
    ) -> Optional[int]:
        """
        Advisory 0-100 score, 100 = least risky, built ONLY from the real
        flags above (never a new indicator). Starts at 100 and subtracts a
        disclosed, fixed penalty per real risk flag that's actually present:
          -25 if this trade's own RR is below the asset's minimum
          -20 if today's realized loss has already breached the warning threshold
          -15 if portfolio-wide risk exposure is already >= 10% of balance
          -15 if this signal has a correlation warning against another active signal
        Returns None only when there isn't enough real data to say anything
        (e.g. no account linked AND risk_reward unknown) - never a guessed
        default.
        """
        known_inputs = any([
            risk_reward_ok is not None,
            daily_loss is not None and daily_loss.breached is not None,
            portfolio_risk is not None and portfolio_risk.total_risk_percent_of_balance is not None,
            correlation_warning is not None,
        ])
        if not known_inputs:
            return None

        score = 100
        if risk_reward_ok is False:
            score -= 25
        if daily_loss is not None and daily_loss.breached:
            score -= 20
        if (
            portfolio_risk is not None
            and portfolio_risk.total_risk_percent_of_balance is not None
            and portfolio_risk.total_risk_percent_of_balance >= 10.0
        ):
            score -= 15
        if correlation_warning:
            score -= 15
        return max(0, score)

    async def full_risk_report(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float],
        risk_reward: Optional[float],
        profile: CalibrationProfile,
        correlation_warning: Optional[str] = None,
    ) -> AgentRiskAssessment:
        """
        Account balance is NOT a parameter here - it's read once from
        `get_daily_loss()` below (which already fetches it internally, see
        SignalService._get_account_balance) rather than making the caller
        fetch it separately and risk two different balances if the cached
        value changes between calls.
        """
        warnings: list[str] = []

        rr_ok = self.validate_risk_reward(risk_reward, profile)
        if rr_ok is False:
            warnings.append(
                f"Risk/Reward {risk_reward} is below this asset's minimum of {profile.min_risk_reward}."
            )

        try:
            portfolio_risk = await self.portfolio_exposure()
        except Exception:
            portfolio_risk = None
        try:
            daily_loss = await self.daily_risk_status()
        except Exception:
            daily_loss = None

        account_balance = daily_loss.account_balance if daily_loss is not None else None
        position = self.suggest_position_size(account_balance, entry_price, stop_loss, take_profit)
        if position is None and account_balance is None:
            warnings.append("No Binance account linked - can't size this position in dollars.")

        if daily_loss is not None and daily_loss.breached:
            warnings.append(daily_loss.message or "Daily realized-loss warning threshold has been reached.")
        if correlation_warning:
            warnings.append(correlation_warning)

        return AgentRiskAssessment(
            risk_percent=get_risk_percent(),
            min_risk_reward=profile.min_risk_reward,
            risk_reward=risk_reward,
            risk_reward_ok=rr_ok,
            position=position,
            portfolio_risk=portfolio_risk,
            daily_loss=daily_loss,
            correlation_warning=correlation_warning,
            warnings=warnings,
        )
