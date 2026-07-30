from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel


class OrderResultResponse(BaseModel):
    order_id: int
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: float
    price: Optional[float] = None


class ExecuteSignalResponse(BaseModel):
    signal_id: uuid.UUID
    environment: str  # "mainnet" | "testnet" - which one this order actually went to
    symbol: str
    quantity: float
    entry_order: OrderResultResponse
    stop_loss_order: Optional[OrderResultResponse] = None
    take_profit_order: Optional[OrderResultResponse] = None
    warnings: list[str] = []


class ClosePositionResponse(BaseModel):
    environment: str
    order: OrderResultResponse
