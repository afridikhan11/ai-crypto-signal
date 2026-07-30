from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class HistoryItemResponse(BaseModel):
    """A single closed signal record as shown in the History module."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    coin_id: uuid.UUID
    coin_symbol: str = Field(..., description="Symbol of the associated coin (e.g. BTCUSDT)")
    direction: str
    entry_price: float
    stop_loss: float
    # The stop as originally set. `stop_loss` above is where the stop ended
    # up after Trade Management trailed it, so on a closed trade the two
    # together show how far the stop was moved before the trade resolved.
    initial_stop_loss: Optional[float] = None
    take_profit: float
    risk_reward: float
    confidence: int
    reason: str
    result: str = Field(..., description="Final outcome status (e.g. TP_HIT, STOPPED, CANCELLED)")
    timeframe: str
    ai_model_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None


class HistoryListResponse(BaseModel):
    """Paginated list of closed signal history records."""

    total: int
    page: int
    page_size: int
    items: list[HistoryItemResponse]


class HistoryQueryParams(BaseModel):
    """Filter, sort, and pagination parameters for querying signal history."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: str = Field(default="closed_at")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")
    symbol: Optional[str] = None
    direction: Optional[str] = None
    result: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class HistorySummaryResponse(BaseModel):
    """Aggregated win/loss statistics for closed signal history (CANCELLED signals excluded)."""

    total_trades: int
    wins: int
    losses: int
    win_rate: float
