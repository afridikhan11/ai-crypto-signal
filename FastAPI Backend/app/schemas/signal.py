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
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward: float
    confidence: int
    reason: str
    status: str
    timeframe: str
    ai_model_version: Optional[str] = None
    session: Optional[str] = None
    htf_bias: Optional[str] = None
    bias_strength: Optional[float] = None
    max_tp_hit: int = 0
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None


class SignalListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SignalResponse]


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