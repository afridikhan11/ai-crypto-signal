"""
Trading Control Service - Auto Trading Control Panel (2026-07-30).

Pure orchestration over already-existing, already-tested primitives. This
module adds NO new trading decisions, NO new order-placing logic, and NO
ICT/AI/Scanner changes - every action below is a loop over real account
state calling a method that already exists and is already covered by its
own tests (`BinanceTradingService.close_position`, `.cancel_order`,
`BinanceAccountService.get_futures_account`, `.get_all_open_orders`). This
file exists so the WPF Auto Trading Control panel has ONE backend call per
bulk action (Close All Positions / Cancel Pending Orders / Emergency Kill
Switch) instead of looping client-side - "do not duplicate backend logic"
in the panel means the loop lives here, not in WPF.

FAILURE DISCIPLINE: every action below is best-effort and per-item -  a
failure closing/cancelling one symbol's position/order never stops the
loop from attempting the rest, and every item's outcome (success or the
exact error) is returned to the caller. Nothing here silently swallows a
failure the way `place_signal_bracket`/`replace_stop_loss` never do either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from app.services import binance_credentials
from app.services.binance_trading_service import BinanceTradingError, BinanceTradingService
from app.services.trading_settings import set_auto_trading_enabled, set_engine_run_state


@dataclass
class ActionItemResult:
    symbol: str
    success: bool
    detail: str
    order_id: Optional[int] = None


@dataclass
class BulkActionResult:
    action: str
    environment: Optional[str]
    attempted: int
    succeeded: int
    items: List[ActionItemResult] = field(default_factory=list)
    message: Optional[str] = None


async def close_all_positions() -> BulkActionResult:
    """
    Closes EVERY currently-open Futures position with a single reduceOnly
    MARKET order each, reusing `BinanceTradingService.close_position()` -
    the exact same method `POST /trading/close-position/{symbol}` already
    uses per-symbol. The live position list is fetched fresh from Binance
    (never a stored value), matching the "never trust a cached quantity"
    discipline used everywhere else in this codebase.
    """
    try:
        account_service = binance_credentials.build_service_from_saved()
    except FileNotFoundError as exc:
        return BulkActionResult(action="close_all_positions", environment=None, attempted=0, succeeded=0, message=str(exc))

    try:
        futures = await account_service.get_futures_account()
    finally:
        await account_service.close()

    open_positions = [p for p in (futures.open_positions if futures else []) if p.position_amt != 0]
    if not open_positions:
        return BulkActionResult(
            action="close_all_positions", environment=None, attempted=0, succeeded=0,
            message="No open positions to close.",
        )

    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        return BulkActionResult(action="close_all_positions", environment=None, attempted=0, succeeded=0, message=str(exc))

    items: List[ActionItemResult] = []
    try:
        for position in open_positions:
            try:
                order = await trading_service.close_position(position.symbol, position.position_amt)
                items.append(ActionItemResult(
                    symbol=position.symbol, success=True,
                    detail=f"Closed with order #{order.order_id}.", order_id=order.order_id,
                ))
                logger.success(f"Close All Positions: {position.symbol} closed (order #{order.order_id}).")
            except BinanceTradingError as exc:
                items.append(ActionItemResult(symbol=position.symbol, success=False, detail=str(exc)))
                logger.warning(f"Close All Positions: {position.symbol} FAILED to close: {exc}")
    finally:
        await trading_service.close()

    succeeded = sum(1 for i in items if i.success)
    return BulkActionResult(
        action="close_all_positions", environment=trading_service.environment,
        attempted=len(items), succeeded=succeeded, items=items,
        message=None if succeeded == len(items) else f"{len(items) - succeeded} position(s) failed to close - see items.",
    )


async def cancel_all_orders() -> BulkActionResult:
    """
    Cancels EVERY currently-open order account-wide, reusing
    `BinanceTradingService.get_all_open_orders()` + `.cancel_order()` - both
    already-existing, already-tested primitives (the latter is the same
    method `replace_stop_loss()`'s own cancel-old-stop step already uses).
    """
    try:
        trading_service = binance_credentials.build_trading_service_from_saved()
    except FileNotFoundError as exc:
        return BulkActionResult(action="cancel_all_orders", environment=None, attempted=0, succeeded=0, message=str(exc))

    items: List[ActionItemResult] = []
    try:
        try:
            open_orders = await trading_service.get_all_open_orders()
        except BinanceTradingError as exc:
            return BulkActionResult(
                action="cancel_all_orders", environment=trading_service.environment,
                attempted=0, succeeded=0, message=f"Could not read open orders: {exc}",
            )

        if not open_orders:
            return BulkActionResult(
                action="cancel_all_orders", environment=trading_service.environment,
                attempted=0, succeeded=0, message="No pending orders to cancel.",
            )

        for order in open_orders:
            symbol = order.get("symbol", "?")
            order_id = order.get("orderId")
            if order_id is None:
                items.append(ActionItemResult(symbol=symbol, success=False, detail="Order had no orderId - skipped."))
                continue
            try:
                await trading_service.cancel_order(symbol, order_id)
                items.append(ActionItemResult(symbol=symbol, success=True, detail="Cancelled.", order_id=order_id))
                logger.success(f"Cancel Pending Orders: {symbol} order #{order_id} cancelled.")
            except BinanceTradingError as exc:
                items.append(ActionItemResult(symbol=symbol, success=False, detail=str(exc), order_id=order_id))
                logger.warning(f"Cancel Pending Orders: {symbol} order #{order_id} FAILED to cancel: {exc}")
    finally:
        await trading_service.close()

    succeeded = sum(1 for i in items if i.success)

    # Any resting ICT LIMIT entry was just cancelled on the exchange by the
    # loop above (it cancels every open order account-wide). Bring the
    # database into agreement so no signal is left waiting on an order that
    # no longer exists - see `expire_armed_pending_entries()`.
    expired = await expire_armed_pending_entries("Cancel Pending Orders")

    base_message = (
        None if succeeded == len(items)
        else f"{len(items) - succeeded} order(s) failed to cancel - see items."
    )
    if expired:
        suffix = f"{expired} armed ICT pending entr(ies) marked EXPIRED."
        base_message = f"{base_message} {suffix}" if base_message else suffix

    return BulkActionResult(
        action="cancel_all_orders", environment=trading_service.environment,
        attempted=len(items), succeeded=succeeded, items=items,
        message=base_message,
    )


@dataclass
class KillSwitchResult:
    """Composite result of the Emergency Kill Switch: disables Auto Trading
    and stops the engine FIRST (so nothing new can be placed while the
    close/cancel loops below are running), then closes every open position
    and cancels every pending order. Each half is independently reported -
    a failure in one never hides or blocks the other."""

    auto_trading_disabled: bool
    engine_stopped: bool
    close_positions: BulkActionResult
    cancel_orders: BulkActionResult


async def expire_armed_pending_entries(reason: str) -> int:
    """
    Marks every ARMED ICT pending entry (PENDING_ENTRY with a real LIMIT
    order resting on the exchange) as EXPIRED, and returns how many were
    transitioned.

    `cancel_all_orders()` already cancels those LIMIT orders ON THE EXCHANGE
    - it cancels every open order account-wide, which includes them. This
    function is the DATABASE half of the same action: without it the
    exchange would have no resting entry while the database still claimed
    PENDING_ENTRY, and the monitor would keep waiting for a fill that can
    never come. EXPIRED (never entered) is the correct terminal state here,
    not CANCELLED (entered, then closed) - no position ever existed.

    Un-armed pending signals (`executed=False`) are deliberately left alone:
    they are advisory only, hold no order and no capital, and Cancel Pending
    Orders is about real exchange state.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.signal import Signal, SignalStatus
    from datetime import datetime, timezone
    from sqlalchemy import select

    # Deliberately best-effort. By the time this runs, the orders have
    # ALREADY been cancelled on the exchange - which is the part that
    # protects real money. A database problem here must never turn a
    # successful Cancel All / Kill Switch into a failed one, so it is
    # logged loudly and swallowed, exactly like every other per-item
    # failure in this module. The monitor's own reconciliation pass will
    # correct any row missed here on its next poll, because it asks Binance
    # what actually happened to the order rather than trusting the DB.
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(Signal).where(
                    Signal.status == SignalStatus.PENDING_ENTRY,
                    Signal.executed.is_(True),
                )
            )).scalars().unique().all()

            if not rows:
                return 0

            now = datetime.now(timezone.utc)
            for signal in rows:
                signal.status = SignalStatus.EXPIRED
                signal.closed_at = now
            await session.commit()
    except Exception as e:  # noqa: BLE001 - see comment above
        logger.warning(
            f"Could not mark armed pending entries EXPIRED after '{reason}': {e}. "
            "The exchange orders were still cancelled; SignalMonitor's reconciliation "
            "pass will correct the database on its next poll."
        )
        return 0

    logger.warning(f"{len(rows)} armed ICT pending entr(ies) marked EXPIRED: {reason}")
    return len(rows)


async def kill_switch() -> KillSwitchResult:
    """
    Emergency Kill Switch - the single most drastic control the panel
    exposes. Order of operations matters: the engine is stopped and Auto
    Trading disabled BEFORE any position is closed or order cancelled, so a
    signal cannot be executed or a stop cannot be re-placed mid-sequence by
    a concurrent action while this is running. Reuses `close_all_positions()`
    and `cancel_all_orders()` above verbatim - no separate close/cancel
    logic is written for this path.
    """
    set_auto_trading_enabled(False)
    set_engine_run_state("stopped")
    logger.warning("EMERGENCY KILL SWITCH activated - Auto Trading disabled, engine stopped, closing all positions and cancelling all orders.")

    close_result = await close_all_positions()
    cancel_result = await cancel_all_orders()

    # The exchange-side cancel above already removed any resting ICT LIMIT
    # entry; this brings the database into agreement so no signal is left
    # waiting on an order that no longer exists.
    expired = await expire_armed_pending_entries("emergency kill switch")

    logger.warning(
        f"Kill switch complete: positions closed {close_result.succeeded}/{close_result.attempted}, "
        f"orders cancelled {cancel_result.succeeded}/{cancel_result.attempted}, "
        f"pending entries expired {expired}."
    )

    return KillSwitchResult(
        auto_trading_disabled=True,
        engine_stopped=True,
        close_positions=close_result,
        cancel_orders=cancel_result,
    )
