from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.models.signal import Signal, SignalStatus
from app.schemas.common import ErrorResponse
from app.schemas.trading import ClosePositionResponse, ExecuteSignalResponse, OrderResultResponse
from app.services import binance_credentials
from app.services.binance_trading_service import BinanceTradingError
from app.services.execution_risk import TradeRiskRejected, assess_execution_risk
from app.services.signal_service import SignalService
from app.services.trading_settings import get_auto_trading_enabled

router = APIRouter(prefix="/trading", tags=["trading"])


@router.post(
    "/execute/{signal_id}",
    response_model=ExecuteSignalResponse,
    summary="Execute a signal's suggested trade on Binance (Auto Trading)",
    description=(
        "The ONLY endpoint in this API that places a real order. Only fires when "
        "explicitly called (the WPF Auto Trading tab's Execute button) - never "
        "automatic. Places a MARKET entry + reduceOnly STOP_MARKET stop-loss + "
        "reduceOnly LIMIT take-profit (TP1 only) on USD-M Futures, sized from the "
        "real account balance and the configured risk %. A signal can only be "
        "executed once."
    ),
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def execute_signal(
    signal_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Auto Trading Control Panel master switch (2026-07-30) - checked FIRST,
    # before even loading the signal: when a user has explicitly turned Auto
    # Trading OFF from the control panel, no real order may be placed,
    # period - cheaper to refuse here than to do a DB lookup first. Defaults
    # on (see app/services/trading_settings.py), so this is purely additive:
    # every signal that could be executed before this switch existed still
    # can be, until a user explicitly flips it off.
    if not get_auto_trading_enabled():
        raise HTTPException(
            status_code=400,
            detail="Auto Trading is turned OFF. Turn it back on from the Auto Trading Control panel to execute signals.",
        )

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

    # ICT Pending Limit Entry (2026-07-30): PENDING_ENTRY is now an
    # executable state - it is the state an ICT signal is BORN into, and
    # executing it rests a LIMIT order at the ICT entry price. ACTIVE
    # remains executable for signals created under entry_mode "market".
    # Every terminal status (TP_HIT/STOPPED/CANCELLED/EXPIRED) is still
    # rejected exactly as before.
    if signal.status not in (SignalStatus.ACTIVE, SignalStatus.PENDING_ENTRY):
        raise HTTPException(
            status_code=400,
            detail=f"Signal is {signal.status.value}, not ACTIVE or PENDING_ENTRY - it can't be executed.",
        )

    if signal.coin is None:
        raise HTTPException(status_code=400, detail="Signal has no associated coin/symbol.")

    # Risk Engine gate - MANDATORY, runs before any Binance call. This is
    # the ONLY path that computes position size for a real order - there is
    # no longer a bare calculate_position_size() fallback that bypasses
    # RiskEngine. The actual decision logic lives in
    # app.services.execution_risk (framework-independent, unit-tested in
    # tests/test_execution_risk.py); this endpoint only translates a
    # rejection into an HTTPException(400) with every reason attached. See
    # LIVE_EXECUTION_SAFETY_REPORT.md, Objective 1.
    signal_service = SignalService(db)
    try:
        assessment = await assess_execution_risk(signal_service, signal)
    except TradeRiskRejected as exc:
        raise HTTPException(status_code=400, detail={"message": exc.message, "reasons": exc.reasons, **exc.extra})
    position = assessment.position_size
    if position is None:
        # RiskEngine approved (no limit breached) but still could not
        # produce a position size (e.g. a degenerate entry/stop pair) -
        # same user-facing message as before this change.
        raise HTTPException(
            status_code=400,
            detail="Could not size this trade - no linked account balance, or a degenerate entry/stop.",
        )

    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ------------------------------------------------------------------
    # ICT PENDING LIMIT ENTRY vs MARKET ENTRY.
    #
    # Exactly one of these runs, decided by the single process-wide
    # `entry_mode` switch (app/services/trading_settings.py), so the
    # platform is never half one model and half the other. Both paths share
    # the identical Auto-Trading gate, RiskEngine gate and position size
    # computed above - only the ORDER TYPE and the moment protective orders
    # are attached differ.
    # ------------------------------------------------------------------
    if signal.status is SignalStatus.PENDING_ENTRY:
        # Stage 1 only: rest the LIMIT entry. The stop-loss and take-profit
        # are attached by SignalMonitor the moment this order fills - a
        # reduceOnly order cannot exist before the position does.
        try:
            entry_order = await trading_service.place_limit_entry(
                symbol=signal.coin.symbol,
                direction=signal.direction.value,
                quantity=position["quantity"],
                entry_price=signal.entry_price,
                signal_id=str(signal.id),
            )
        except BinanceTradingError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            await trading_service.close()

        # `executed` here means "this platform has placed this signal's
        # order on the exchange", which is what the double-execution guard
        # above needs. It does NOT mean a position is open - `filled_at`
        # is what records that, once the monitor observes the fill.
        signal.executed = True
        signal.executed_order_id = str(entry_order.order_id)
        signal.entry_order_id = str(entry_order.order_id)
        signal.executed_at = datetime.now(timezone.utc)
        signal.executed_environment = trading_service.environment
        await db.commit()

        logger.success(
            f"{signal.coin.symbol}: ICT pending entry ARMED | {signal.direction.value} | "
            f"limit={signal.entry_price} | qty={entry_order.quantity} | "
            f"order {entry_order.order_id} | env={trading_service.environment}"
        )
        return ExecuteSignalResponse(
            signal_id=signal.id,
            environment=trading_service.environment,
            symbol=entry_order.symbol,
            quantity=entry_order.quantity,
            entry_order=OrderResultResponse(**entry_order.model_dump()),
            stop_loss_order=None,
            take_profit_order=None,
            warnings=[
                "ICT pending entry: a LIMIT order is now resting at the entry price. "
                "The stop-loss and take-profit are placed automatically as soon as it fills. "
                "If price never reaches the entry zone the signal expires and the order is cancelled."
            ],
        )

    try:
        execution = await trading_service.place_signal_bracket(
            symbol=signal.coin.symbol,
            direction=signal.direction.value,
            quantity=position["quantity"],
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            entry_price=signal.entry_price,
            signal_id=str(signal.id),
        )
    except BinanceTradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await trading_service.close()

    signal.executed = True
    signal.executed_order_id = str(execution.entry_order.order_id)
    signal.executed_at = datetime.now(timezone.utc)
    signal.executed_environment = execution.environment
    # A market entry fills immediately, so the position is open right now.
    signal.filled_at = signal.executed_at
    signal.actual_fill_price = signal.entry_price
    await db.commit()

    return ExecuteSignalResponse(
        signal_id=signal.id,
        environment=execution.environment,
        symbol=execution.symbol,
        quantity=execution.quantity,
        entry_order=OrderResultResponse(**execution.entry_order.model_dump()),
        stop_loss_order=(
            OrderResultResponse(**execution.stop_loss_order.model_dump())
            if execution.stop_loss_order
            else None
        ),
        take_profit_order=(
            OrderResultResponse(**execution.take_profit_order.model_dump())
            if execution.take_profit_order
            else None
        ),
        warnings=execution.warnings,
    )


@router.post(
    "/close-position/{symbol}",
    response_model=ClosePositionResponse,
    summary="Fully close an open Futures position with a MARKET order (Auto Trading)",
    description=(
        "Fetches the LIVE position for `symbol` fresh from Binance (never trusts a "
        "client-supplied quantity), then places a single reduceOnly MARKET order for "
        "its exact current size. 404 if there is no open position on that symbol."
    ),
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def close_position(
    symbol: str,
    _user: str = Depends(get_current_user),
):
    try:
        account_service = binance_credentials.build_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        futures = await account_service.get_futures_account()
    finally:
        await account_service.close()

    symbol = symbol.upper()
    position = next((p for p in (futures.open_positions if futures else []) if p.symbol == symbol), None)
    if position is None or position.position_amt == 0:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol}.")

    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        order = await trading_service.close_position(symbol, position.position_amt)
    except BinanceTradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await trading_service.close()

    return ClosePositionResponse(
        environment=trading_service.environment,
        order=OrderResultResponse(**order.model_dump()),
    )
