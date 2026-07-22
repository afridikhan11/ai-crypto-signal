from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CoinResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: Optional[str] = None
    is_active: bool
    rank: Optional[int] = None
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class CoinListResponse(BaseModel):
    items: list[CoinResponse]