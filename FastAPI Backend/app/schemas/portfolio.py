"""
Portfolio Intelligence schemas (Phase 7, 2026-07-27).

Every field below is either a direct pass-through of an EXISTING response
model (`PortfolioRiskResponse`/`DailyLossResponse` from app/schemas/signal.py,
reused unchanged - see portfolio_intelligence.py's module docstring) or a
disclosed, fixed-formula reshaping of real, already-computed numbers
(exposure weights, correlation coefficients, HHI concentration). Nothing
here is a new score or a new indicator - see Phase 7's own instructions:
"do not create a second scoring engine."
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.signal import DailyLossResponse, PortfolioRiskResponse


class ExposureItem(BaseModel):
    """One real position or active-signal exposure line. `source` tells the
    caller exactly which real data this came from - never blended or
    guessed. `current_decision`/`current_confidence` (when present) are a
    read of the EXISTING Decision Engine for this same symbol, taken fresh
    at report time (Requirement 1: portfolio analysis reuses the Decision
    Engine) - `decision_note` explains honestly when that read wasn't
    available rather than leaving a silent gap."""

    symbol: str
    asset_class: str  # "crypto" | "commodity" - app.core.constants.asset_class_for_symbol
    asset_type: str   # "crypto" | "gold" | "silver" | "oil" | "forex" - asset_type_for_symbol
    direction: str    # "LONG" | "SHORT"

    # Real numbers - meaning depends on `source` (see ExposureAnalysisResponse.source):
    #   "binance_live"   -> notional_usd/margin_usd/unrealized_pnl_usd are real, live account figures
    #   "active_signals" -> only risk_usd is populated (suggested $ risk if the signal's position were taken)
    notional_usd: Optional[float] = None
    margin_usd: Optional[float] = None
    unrealized_pnl_usd: Optional[float] = None
    risk_usd: Optional[float] = None

    weight_pct: float = Field(..., description="This item's share of total portfolio exposure, 0-100")

    current_decision: Optional[str] = Field(None, description="The existing Decision Engine's CURRENT final_decision for this symbol, read fresh (e.g. 'BUY', 'HOLD')")
    current_confidence: Optional[int] = None
    decision_note: Optional[str] = Field(None, description="Honest reason `current_decision` is null, when it is")


class ExposureAnalysisResponse(BaseModel):
    source: str = Field(..., description="'binance_live' | 'active_signals' | 'none'")
    account_balance: Optional[float] = None
    total_exposure_usd: Optional[float] = None
    items: List[ExposureItem] = Field(default_factory=list)
    by_asset_class_pct: Dict[str, float] = Field(default_factory=dict)
    by_direction_pct: Dict[str, float] = Field(default_factory=dict)
    message: Optional[str] = None


class CorrelationPair(BaseModel):
    symbol_a: str
    symbol_b: str
    correlation: float = Field(..., description="Pearson correlation of 15m pct-change returns, same computation as app/services/correlation_risk.py")
    same_direction: bool
    stacked_risk: bool = Field(..., description="True when same_direction AND correlation >= the existing CORRELATION_THRESHOLD")


class CorrelationAnalysisResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    threshold: Optional[float] = None
    symbols_checked: List[str] = Field(default_factory=list)
    pairs: List[CorrelationPair] = Field(default_factory=list)
    stacked_risk_pairs: List[CorrelationPair] = Field(default_factory=list)
    message: Optional[str] = None


class DiversificationResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    hhi_by_symbol: Optional[float] = Field(None, description="Herfindahl-Hirschman Index (0-10000) over per-symbol exposure weights - standard concentration measure, computed only from weights already in ExposureAnalysisResponse")
    hhi_by_asset_class: Optional[float] = None
    concentration_label_by_symbol: Optional[str] = Field(None, description="Fixed, disclosed band: 'Well diversified' (<1500) | 'Moderately concentrated' (1500-2499) | 'Highly concentrated' (>=2500)")
    concentration_label_by_asset_class: Optional[str] = None
    largest_symbol: Optional[str] = None
    largest_symbol_weight_pct: Optional[float] = None
    largest_asset_class: Optional[str] = None
    largest_asset_class_weight_pct: Optional[float] = None
    message: Optional[str] = None


class PortfolioIntelligenceResponse(BaseModel):
    """Composed view - `risk`/`daily_loss` are the EXISTING, unmodified
    Phase 1 responses (SignalService.get_portfolio_risk/get_daily_loss);
    `exposure`/`correlation`/`diversification` are new in Phase 7, built
    only from real data (see portfolio_intelligence.py)."""

    generated_at: datetime
    risk: PortfolioRiskResponse
    daily_loss: DailyLossResponse
    exposure: ExposureAnalysisResponse
    correlation: CorrelationAnalysisResponse
    diversification: DiversificationResponse
