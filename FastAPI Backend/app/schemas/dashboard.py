from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field


class AccountPanel(BaseModel):
    """Read-only snapshot for the Dashboard's Account panel. All fields are
    None/empty when no Binance credentials are saved yet - no fake data."""

    has_credentials: bool = False
    environment: Optional[str] = None  # "mainnet" | "testnet"
    wallet_balance: Optional[float] = None
    available_balance: Optional[float] = None
    margin_ratio_pct: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    open_positions: int = 0
    open_orders: int = 0
    win_rate: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)


class AiPanel(BaseModel):
    """
    Real AIScorer internals - NOT reinforcement-learning training metrics
    (this system is a hand-weighted, outcome-calibrated scorer, not an
    RL-trained model, so there is no episode/epoch/replay-buffer/entropy
    to report).
    """

    weights: Dict[str, float]
    is_calibrated: bool
    wins_collected: int
    losses_collected: int
    min_required_per_group: int
    last_signal_symbol: Optional[str] = None
    last_signal_direction: Optional[str] = None
    last_signal_confidence: Optional[int] = None
    last_signal_score_breakdown: Optional[Dict[str, float]] = None
    last_signal_reason: Optional[str] = None


class MarketPanel(BaseModel):
    """BTC-wide market context (the scanner's anchor symbol) - shared
    macro/technical backdrop rather than per-symbol data."""

    btc_trend_1h: str
    btc_trend_4h: str
    market_structure: str
    liquidity_sweep: str
    funding_rate: Optional[float] = None
    open_interest_trend: str
    fear_greed_value: int
    fear_greed_classification: str
    btc_dominance: Optional[float] = None
    binance_connected: bool


class DashboardResponse(BaseModel):
    account: AccountPanel
    ai: AiPanel
    market: MarketPanel
