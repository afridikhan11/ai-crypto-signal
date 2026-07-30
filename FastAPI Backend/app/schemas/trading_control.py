"""
Schemas for the Auto Trading Control Panel (2026-07-30) - the WPF panel that
controls (Start/Pause/Stop/Resume/Kill Switch/Close All/Cancel All) and
visualizes (status/PnL/win-rate/risk-limits/logs) the production backend.
Every field below is backed by a real read in
app/api/v1/endpoints/trading_control.py - nothing here is placeholder data.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RiskLimitsResponse(BaseModel):
    """Mirrors `app.risk.risk_engine.RiskLimits` field-for-field - these are
    the actual configured thresholds RiskEngine enforces at execution (see
    LIVE_EXECUTION_SAFETY_REPORT.md), not a hand-copied duplicate."""

    max_risk_percent_per_trade: float
    max_open_risk_percent: float
    max_daily_loss_percent: float
    max_exposure_percent: float
    max_drawdown_percent: float


class SessionStatusResponse(BaseModel):
    """Read-only status, not a selectable filter - there is no persisted
    'session' dimension on a Signal to select by. Computed live via
    `app.smc.session_engine.SessionEngine` against the reference symbol's
    most recent candle, the same engine `SignalMonitor`/`SignalGenerator`
    already use - never a second implementation."""

    reference_symbol: str
    active_sessions: List[str] = Field(default_factory=list)
    in_kill_zone: bool = False
    kill_zone: Optional[str] = None
    is_london_ny_overlap: bool = False
    message: Optional[str] = Field(
        None, description="Set instead of the fields above when there isn't yet enough candle data to classify."
    )


class TradingStatusResponse(BaseModel):
    """The single aggregated read the control panel polls. Every boolean/
    number here is a real, current backend read - composed from
    `app.services.binance_credentials`, `app.services.trading_settings`,
    `app.state.scanner`/`app.state.signal_monitor`, `RiskEngine.limits`, and
    `SignalService` - not a new computation, not a duplicate of any of
    them."""

    has_credentials: bool
    environment: Optional[str] = None

    auto_trading_enabled: bool
    engine_run_state: str = Field(description="'running' | 'paused' | 'stopped'")

    binance_connected: bool
    scanner_running: bool
    signal_monitor_running: bool
    risk_engine_available: bool = Field(
        description="False when no account balance is linked - RiskEngine cannot assess anything without one."
    )
    ai_calibrated: bool = Field(
        False, description="Whether the AI scorer has enough closed trades to run on its calibrated weights (see app.ai.calibration)."
    )
    ai_status_message: Optional[str] = None

    risk_limits: RiskLimitsResponse
    portfolio_open_risk_percent: Optional[float] = Field(
        None, description="Total % of account at risk across every ACTIVE signal's suggested position (see GET /signals/portfolio-risk)."
    )

    daily_pnl_usd: Optional[float] = None
    daily_pnl_percent: Optional[float] = None
    daily_loss_breached: bool = False

    win_rate: Optional[float] = None
    total_signals: int = 0
    active_trades: int = 0
    pending_signals: int = 0
    # ICT Pending Limit Entry (2026-07-30): an entry ARMED with a real LIMIT
    # order resting on the exchange, waiting for price to reach the ICT zone.
    # Distinct from `pending_signals` (awaiting an Execute click, no order
    # exists) and from `active_trades` (a position is actually open).
    pending_entries: int = 0
    executed_signals: int = 0
    # Which entry model is in force platform-wide: "ict_pending" | "market".
    entry_mode: str = "ict_pending"

    session: SessionStatusResponse


class EngineActionResponse(BaseModel):
    engine_run_state: str
    message: str


class AutoTradingToggleResponse(BaseModel):
    auto_trading_enabled: bool
    message: str


class BulkActionItemResponse(BaseModel):
    symbol: str
    success: bool
    detail: str
    order_id: Optional[int] = None


class BulkActionResponse(BaseModel):
    action: str
    environment: Optional[str] = None
    attempted: int
    succeeded: int
    items: List[BulkActionItemResponse] = Field(default_factory=list)
    message: Optional[str] = None


class KillSwitchResponse(BaseModel):
    auto_trading_disabled: bool
    engine_stopped: bool
    close_positions: BulkActionResponse
    cancel_orders: BulkActionResponse


class LogEntryResponse(BaseModel):
    time: str
    level: str
    logger: str
    message: str


class LogsResponse(BaseModel):
    items: List[LogEntryResponse]
    count: int
