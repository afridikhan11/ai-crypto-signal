"""Request/response schemas for the Smart AI premium API."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class SmartAiLoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class SmartAiTokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in_minutes: int


class SmartAiRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class StrategyInfo(BaseModel):
    strategy_id: str
    display_name: str
    enabled: bool


class SmartAiStatus(BaseModel):
    module_enabled: bool
    testnet: bool
    strategies: List[StrategyInfo]


class SmartAiSignal(BaseModel):
    id: str
    strategy_id: Optional[str]
    symbol: str
    direction: str
    status: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: int
    rules_fired: Optional[List[Any]] = None
    created_at: Optional[str] = None


class StrategyPerformance(BaseModel):
    strategy_id: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    # Placeholder until the backtester feeds real metrics; flagged so the UI
    # (and I) never mistake a thin live sample for a validated result.
    note: str
