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
    # G6 (A5-lite, 2026-08-01): the REAL Binance average fill price, when
    # the response carried one; None otherwise. Additive + optional, so
    # every existing consumer (including the WPF client, whose
    # System.Text.Json ignores unknown members) is unaffected.
    avg_fill_price: Optional[float] = None


class ExecuteSignalResponse(BaseModel):
    signal_id: uuid.UUID
    environment: str  # "mainnet" | "testnet" - which one this order actually went to
    symbol: str
    quantity: float
    entry_order: OrderResultResponse
    stop_loss_order: Optional[OrderResultResponse] = None
    take_profit_order: Optional[OrderResultResponse] = None
    warnings: list[str] = []


class ManualTradeResponse(BaseModel):
    """Response of the two A5-lite Manual Trading actions (spec §12.3).

    Shaped deliberately close to ExecuteSignalResponse so the WPF client
    reuses its display path, plus the two fields that make the action
    auditable: WHICH order type the §7 matrix resolved, and WHY.
    """

    signal_id: uuid.UUID
    action: str  # "execute_market" | "place_signal_order"
    environment: str
    symbol: str
    quantity: float
    # The §7 matrix's decision for this execution: MARKET | LIMIT | STOP_MARKET.
    resolved_order_type: str
    # The comparison that produced it (entry vs current price + band) -
    # every execution decision must be able to explain itself.
    resolution_reason: str
    entry_order: OrderResultResponse
    stop_loss_order: Optional[OrderResultResponse] = None
    take_profit_order: Optional[OrderResultResponse] = None
    warnings: list[str] = []


class ClosePositionResponse(BaseModel):
    environment: str
    order: OrderResultResponse
