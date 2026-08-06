"""
Manual Trading endpoints - A5-lite (G1), 2026-08-01.
Final Architecture Specification v1.0 §12.3, implemented against the
existing schema as an approved sequencing deviation (facade-compatible:
Phase A3 later re-routes these internals through the Trade aggregate
without changing their contracts).

TWO actions, never merged (§12.3):

  POST /manual/execute-market/{signal_id}
      IGNORE the signal's entry. MARKET order at whatever the market is,
      right now, knowingly. Records the REAL Binance average fill price -
      never a copy of signal.entry_price (G6).

  POST /manual/place-signal-order/{signal_id}
      RESPECT the signal's entry. The §7 matrix (entry vs CURRENT price)
      resolves LIMIT vs STOP-MARKET; inside the configured band the
      configured policy applies (default: reject).

EXECUTION RULE (spec): Signal.status is never used to choose the order
type. It is only checked as an ANALYSIS-validity guard (a concluded idea -
TP_HIT/STOPPED/CANCELLED/EXPIRED - cannot be traded), plus the existing
`executed` once-only guard, which is trade bookkeeping, not analysis.
This is precisely what makes the 2026-07-31 incident (stale-ACTIVE signal
market-sold 3.4% away from its planned entry) unrepresentable here: no
code path in this module can reach the exchange without first comparing
entry_price to the live price - except Execute Market, whose entire
declared meaning is "ignore the entry".

Shared gates, identical to POST /trading/execute: Auto Trading master
switch first, then the mandatory Risk Engine gate (invariant 2 -
risk-in-the-middle). Nothing here creates a second ungated path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.core.constants import PENDING_ENTRY_EXPIRY_MINUTES
from app.models.signal import Signal, SignalStatus, TERMINAL_STATUSES
from app.schemas.common import ErrorResponse
from app.schemas.trading import ManualTradeResponse, OrderResultResponse
from app.services import binance_credentials
from app.services.binance_trading_service import BinanceTradingError
from app.services.entry_order_resolver import (
    EntryInsideBandError,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    resolve_entry_order_type,
)
from app.services.execution_risk import TradeRiskRejected, assess_execution_risk
from app.services.signal_service import SignalService
from app.services.trading_settings import (
    get_auto_trading_enabled,
    get_entry_band_policy,
    get_entry_band_ticks,
)

router = APIRouter(prefix="/manual", tags=["manual-trading"])


async def _load_tradeable_signal(signal_id: uuid.UUID, db: AsyncSession) -> Signal:
    """Shared guards for both actions. Status is used ONLY to refuse
    concluded analysis - never to choose an order type."""
    result = await db.execute(
        select(Signal).options(selectinload(Signal.coin)).where(Signal.id == signal_id)
    )
    signal = result.scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.executed:
        raise HTTPException(
            status_code=409,
            detail=f"Signal already executed ({signal.executed_environment}, order {signal.executed_order_id}).",
        )
    if signal.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Signal is {signal.status.value} - this trading idea has concluded and cannot be traded.",
        )
    if signal.coin is None:
        raise HTTPException(status_code=400, detail="Signal has no associated coin/symbol.")
    return signal


async def _gate_and_size(signal: Signal, db: AsyncSession) -> dict:
    """Auto-Trading master switch + mandatory Risk Engine gate - the same
    two gates POST /trading/execute enforces, in the same order."""
    if not get_auto_trading_enabled():
        raise HTTPException(
            status_code=400,
            detail="Auto Trading is turned OFF. Turn it back on from the Auto Trading Control panel to place orders.",
        )
    signal_service = SignalService(db)
    try:
        assessment = await assess_execution_risk(signal_service, signal)
    except TradeRiskRejected as exc:
        raise HTTPException(status_code=400, detail={"message": exc.message, "reasons": exc.reasons, **exc.extra})
    if assessment.position_size is None:
        raise HTTPException(
            status_code=400,
            detail="Could not size this trade - no linked account balance, or a degenerate entry/stop.",
        )
    return assessment.position_size


@router.post(
    "/execute-market/{signal_id}",
    response_model=ManualTradeResponse,
    summary="Execute Market: enter NOW at market price, ignoring the signal's entry",
    description=(
        "Places a real MARKET entry plus reduceOnly stop-loss/take-profit immediately. "
        "The signal's entry price is deliberately ignored - that is this action's meaning. "
        "The REAL Binance average fill price is recorded; the planned entry is never "
        "substituted for it."
    ),
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def execute_market(
    signal_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    signal = await _load_tradeable_signal(signal_id, db)
    position = await _gate_and_size(signal, db)

    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        execution = await trading_service.place_signal_bracket(
            symbol=signal.coin.symbol,
            direction=signal.direction.value,
            quantity=position["quantity"],
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            # Reference for the MIN_NOTIONAL pre-check only - the entry is
            # MARKET and carries no price.
            entry_price=signal.entry_price,
            signal_id=str(signal.id),
        )
    except BinanceTradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await trading_service.close()

    now = datetime.now(timezone.utc)
    real_fill = execution.entry_order.avg_fill_price  # None when Binance reported no fill price
    warnings = list(execution.warnings)
    if real_fill is None:
        warnings.append(
            "Binance did not report an average fill price in the order response; "
            "actual_fill_price is stored as unknown (never substituted with the planned entry)."
        )

    # Legacy-column bookkeeping (dual-write until Phase A3's Trade aggregate).
    signal.executed = True
    signal.executed_order_id = str(execution.entry_order.order_id)
    signal.executed_at = now
    signal.executed_environment = execution.environment
    signal.filled_at = now
    # G6: the REAL fill or None - never signal.entry_price.
    signal.actual_fill_price = real_fill
    # The idea is now live as a market-entered trade, so the monitor must
    # manage TP/SL from this poll on (existing machinery keys on ACTIVE).
    signal.status = SignalStatus.ACTIVE
    await db.commit()

    logger.success(
        f"{signal.coin.symbol}: MANUAL Execute Market | {signal.direction.value} | "
        f"qty={execution.quantity} | real fill={real_fill} (planned entry {signal.entry_price} ignored "
        f"by design) | order {execution.entry_order.order_id} | env={execution.environment}"
    )

    return ManualTradeResponse(
        signal_id=signal.id,
        action="execute_market",
        environment=execution.environment,
        symbol=execution.symbol,
        quantity=execution.quantity,
        resolved_order_type=ORDER_TYPE_MARKET,
        resolution_reason="Execute Market: immediate fill requested; the signal's entry price is ignored by definition.",
        entry_order=OrderResultResponse(**execution.entry_order.model_dump()),
        stop_loss_order=(
            OrderResultResponse(**execution.stop_loss_order.model_dump())
            if execution.stop_loss_order else None
        ),
        take_profit_order=(
            OrderResultResponse(**execution.take_profit_order.model_dump())
            if execution.take_profit_order else None
        ),
        warnings=warnings,
    )


@router.post(
    "/place-signal-order/{signal_id}",
    response_model=ManualTradeResponse,
    summary="Place Signal Order: rest a pending order AT the signal's entry (matrix-resolved)",
    description=(
        "Compares the signal's entry with the CURRENT market price and places the order "
        "type the execution matrix dictates: LIMIT (entry on the retracement side) or "
        "STOP-MARKET (entry on the breakout side). Inside the configured band the "
        "configured policy applies (default: reject with 409). Stop-loss/take-profit are "
        "attached automatically by the monitor when the entry fills."
    ),
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def place_signal_order(
    signal_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    signal = await _load_tradeable_signal(signal_id, db)
    position = await _gate_and_size(signal, db)

    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        current_price, tick_size = await trading_service.get_price_and_tick(signal.coin.symbol)

        # THE §7 MATRIX - the execution decision comes from prices, never
        # from Signal.status.
        try:
            resolved = resolve_entry_order_type(
                direction=signal.direction.value,
                entry_price=signal.entry_price,
                current_price=current_price,
                tick_size=tick_size,
                band_ticks=get_entry_band_ticks(),
                band_policy=get_entry_band_policy(),
            )
        except EntryInsideBandError as exc:
            # Default policy: refuse the disguised market order. 409, not
            # silent conversion - Execute Market exists for that choice.
            raise HTTPException(status_code=409, detail=str(exc))

        if resolved.order_type == ORDER_TYPE_MARKET:
            # Only reachable under the explicit 'immediate_market' band
            # policy: delegate to the market action's own code path is NOT
            # done here (the two actions never share an implementation);
            # instead we refuse and point at the explicit action, so the
            # operator's intent is always recorded unambiguously.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{resolved.reason}. Use POST /manual/execute-market/{signal.id} to take "
                    "the immediate fill explicitly."
                ),
            )

        if resolved.order_type == ORDER_TYPE_LIMIT:
            entry_order = await trading_service.place_limit_entry(
                symbol=signal.coin.symbol,
                direction=signal.direction.value,
                quantity=position["quantity"],
                entry_price=signal.entry_price,
                signal_id=str(signal.id),
            )
        else:  # STOP_MARKET entry (G3)
            entry_order = await trading_service.place_stop_market_entry(
                symbol=signal.coin.symbol,
                direction=signal.direction.value,
                quantity=position["quantity"],
                stop_price=signal.entry_price,
                signal_id=str(signal.id),
            )
    except BinanceTradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await trading_service.close()

    now = datetime.now(timezone.utc)
    # Legacy-column bookkeeping (dual-write until Phase A3): the existing
    # SignalMonitor pending machinery reconciles ANY resting entry order by
    # `entry_order_id` against the exchange (order type agnostic - it reads
    # status/executedQty/avgPrice), attaches protection on fill, and expires
    # the signal if the window elapses. An advisory signal that had already
    # touched its zone (status ACTIVE, un-executed) returns to
    # PENDING_ENTRY with a fresh window: it now has a REAL resting order,
    # which is exactly what PENDING_ENTRY+executed means in this schema.
    signal.executed = True
    signal.executed_order_id = str(entry_order.order_id)
    signal.entry_order_id = str(entry_order.order_id)
    signal.executed_at = now
    signal.executed_environment = trading_service.environment
    if signal.status is not SignalStatus.PENDING_ENTRY:
        signal.status = SignalStatus.PENDING_ENTRY
    signal.entry_expires_at = now + timedelta(minutes=PENDING_ENTRY_EXPIRY_MINUTES)
    await db.commit()

    logger.success(
        f"{signal.coin.symbol}: MANUAL Place Signal Order | {signal.direction.value} | "
        f"{resolved.order_type} @ {signal.entry_price} (current {current_price}) | "
        f"qty={entry_order.quantity} | order {entry_order.order_id} | env={trading_service.environment} | "
        f"{resolved.reason}"
    )

    return ManualTradeResponse(
        signal_id=signal.id,
        action="place_signal_order",
        environment=trading_service.environment,
        symbol=entry_order.symbol,
        quantity=entry_order.quantity,
        resolved_order_type=resolved.order_type,
        resolution_reason=resolved.reason,
        entry_order=OrderResultResponse(**entry_order.model_dump()),
        stop_loss_order=None,
        take_profit_order=None,
        warnings=[
            "Pending entry is resting on the exchange. The stop-loss and take-profit are "
            "placed automatically the moment it fills; if price never reaches the entry, "
            "the signal expires and the order is cancelled."
        ],
    )
