from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    coin_id: uuid.UUID
    coin_symbol: str = Field(..., description="Symbol of the associated coin (e.g. BTCUSDT)")
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: int
    reason: str
    status: str
    timeframe: str
    ai_model_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None

    # Position-size suggestion (see app/services/position_sizing.py) - null
    # whenever no real account balance is available (no saved Binance
    # credentials, or the balance fetch failed), never a fabricated 0.
    suggested_risk_usd: Optional[float] = Field(
        None, description="Suggested dollar amount to risk, based on real account balance x risk_percent"
    )
    suggested_quantity: Optional[float] = Field(
        None, description="Suggested position size in base-asset units for the above risk amount"
    )
    suggested_notional_usd: Optional[float] = Field(
        None, description="suggested_quantity x entry_price"
    )
    suggested_profit_usd: Optional[float] = Field(
        None, description="Dollar profit if suggested_quantity exits at take_profit"
    )
    suggested_loss_sl_usd: Optional[float] = Field(
        None, description="Dollar loss if suggested_quantity exits at stop_loss (equals suggested_risk_usd by construction)"
    )

    # Advisory only (see app/services/correlation_risk.py) - never blocks or
    # filters signal generation, just flags stacked same-direction exposure
    # on ACTIVE signals when it's real (recent price correlation, not a
    # hand-picked coin list).
    correlation_warning: Optional[str] = None

    # Unified Risk Engine assessment (see app/risk/risk_engine.py), computed
    # against the WHOLE portfolio - open risk across every ACTIVE signal,
    # total notional exposure, today's realized P&L, and drawdown from the
    # account's equity peak - not just this one trade in isolation.
    #
    # Advisory, like every other risk read in this project: the bot never
    # places a trade on its own (Auto Trading is an explicit per-signal user
    # action - see POST /trading/execute), so `risk_approved=False` is a
    # clear warning that taking this position would breach a configured
    # portfolio limit, not an enforced block. All fields are null when no
    # real account balance is available, never a fabricated 0.
    risk_approved: Optional[bool] = Field(
        None, description="False when taking this position would breach a configured portfolio risk limit"
    )
    risk_reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable reasons behind risk_approved (limit breaches and advisory notes)",
    )
    portfolio_open_risk_percent: Optional[float] = Field(
        None, description="Total % of account at risk across all open positions INCLUDING this one"
    )
    portfolio_exposure_percent: Optional[float] = Field(
        None, description="Total notional exposure as a % of account balance INCLUDING this one"
    )

    # Auto Trading execution tracking (see app/services/binance_trading_service.py) -
    # executed_environment lets the UI clearly distinguish a testnet
    # execution (no real money) from a mainnet one.
    executed: bool = False
    executed_order_id: Optional[str] = None
    executed_at: Optional[datetime] = None
    executed_environment: Optional[str] = None

    # The ORIGINAL stop, frozen at signal creation. `stop_loss` above is the
    # LIVE stop and moves as Trade Management trails it; this is the stable
    # reference every risk/sizing/exposure number is computed from, so the
    # UI can show both "where the stop is now" and "what this trade was
    # actually sized against".
    initial_stop_loss: Optional[float] = Field(
        None,
        description="The stop as originally set. Risk/position sizing always measures from this, never from the trailed stop.",
    )

    # ---- ICT Pending Limit Entry (2026-07-30) ----
    # Under entry_mode "ict_pending" a signal is created PENDING_ENTRY at the
    # ICT anchor price and becomes ACTIVE only when price actually trades
    # into the zone. These fields let the UI show the difference between "an
    # entry is armed and waiting" and "a position is open".
    entry_type: Optional[str] = Field(
        None,
        description="Which ICT anchor priced this entry: ote | order_block | fvg | supply_demand | limit | market",
    )
    entry_zone_top: Optional[float] = Field(
        None, description="Top of the ICT entry zone - price trading anywhere inside the zone fills the entry"
    )
    entry_zone_bottom: Optional[float] = Field(
        None, description="Bottom of the ICT entry zone"
    )
    entry_expires_at: Optional[datetime] = Field(
        None, description="When an unfilled pending entry expires and is abandoned (never entered)"
    )
    filled_at: Optional[datetime] = Field(
        None, description="When the entry actually filled. Null while still PENDING_ENTRY."
    )
    actual_fill_price: Optional[float] = Field(
        None,
        description=(
            "The real fill price. Equals entry_price for a LIMIT entry by definition - "
            "recorded separately so the two remain independently auditable."
        ),
    )
    entry_order_id: Optional[str] = Field(
        None, description="The resting LIMIT entry order on Binance, when Auto Trading has armed this entry"
    )


class SignalListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SignalResponse]


class PortfolioRiskItem(BaseModel):
    coin_symbol: str
    direction: str
    risk_usd: float


class PortfolioRiskResponse(BaseModel):
    """
    Advisory only - nothing here places or blocks a trade by itself (Auto
    Trading execution is a separate, explicit user action per signal - see
    POST /trading/execute). This just answers "if I took every ACTIVE
    signal's suggested position right now, how much of my account would be
    at risk at once?"
    """

    active_signal_count: int
    account_balance: Optional[float] = None
    total_risk_usd: Optional[float] = None
    total_risk_percent_of_balance: Optional[float] = None
    items: list[PortfolioRiskItem] = Field(default_factory=list)
    message: Optional[str] = None


class DailyLossResponse(BaseModel):
    """
    Advisory only - counts realized loss/profit (using suggested position
    sizing) across signals closed since UTC midnight. Never auto-pauses
    anything; `breached` just flags that the configured warning threshold
    has been crossed today.
    """

    period_start: datetime
    account_balance: Optional[float] = None
    realized_loss_usd: float
    realized_pnl_usd: float
    loss_percent_of_balance: Optional[float] = None
    warning_threshold_percent: float
    breached: bool
    closed_trade_count: int
    message: Optional[str] = None


class SignalQueryParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: str = Field(default="created_at")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")
    symbol: Optional[str] = None
    direction: Optional[str] = None
    status: Optional[str] = None
    min_confidence: Optional[int] = None
    timeframe: Optional[str] = None
    asset_class: Optional[str] = None