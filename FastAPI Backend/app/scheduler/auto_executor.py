"""
Testnet Auto-Executor — hands-off paper trading.

Normally a generated signal sits in the database as PENDING_ENTRY/ACTIVE and a
human (the WPF app) clicks "Execute" to place the order. That is impossible on a
headless VM, so this background task performs that same execution automatically
- but ONLY on Binance Testnet, so it can validate the strategy end-to-end
(real fills, real TP/SL tracking by SignalMonitor) with paper money and zero
real-money risk.

SAFETY - three independent gates, every cycle:
  1. OPT-IN: only started at all when settings.auto_execute_testnet is true
     (AUTO_EXECUTE_TESTNET=true), which defaults OFF.
  2. TESTNET-ONLY: refuses to place anything unless the saved credentials are
     flagged testnet. If they are mainnet (or missing) it logs and does nothing
     - it can NEVER place a mainnet order.
  3. AUTO-TRADING SWITCH: honours the same get_auto_trading_enabled() master
     switch the manual Execute endpoint checks.

It reuses the EXACT execution path the POST /trading/execute endpoint uses
(RiskEngine gate -> saved trading service -> place_limit_entry/place_signal_bracket
-> mark executed), so what it proves is what the product does. Each signal is
attempted independently; one failure never stops the loop or the others.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.signal import Signal, SignalStatus, NON_TERMINAL_STATUSES
from app.services import binance_credentials
from app.services.binance_trading_service import BinanceTradingError
from app.services.execution_risk import assess_execution_risk, TradeRiskRejected
from app.services.signal_service import SignalService
from app.services.trading_settings import get_auto_trading_enabled


# Signals this executor is allowed to place orders for: ONLY the legacy
# production pipeline (strategy_id NULL for freshly-generated signals, or
# "legacy" for pre-Smart-AI rows). Smart AI signals carry a named strategy_id
# and are deliberately excluded - see pending_execution_stmt().
LEGACY_STRATEGY_ID = "legacy"


def daily_cap_reached(trades_last_24h: int, cap: int) -> bool:
    """DAILY TRADE CAP (pure): True when `cap` trades have already been placed
    in the rolling 24h window - LONGs and SHORTs counted TOGETHER (the owner's
    rule: 3 trades a day, whichever direction). A discipline brake against
    over-trading and resting-order stacking, not a quality filter. 0 disables."""
    return cap > 0 and trades_last_24h >= cap


def executed_last_24h_stmt(now: datetime):
    """Count of trades the executor placed in the rolling 24h before `now`
    (any direction, any outcome - what matters is that an order was placed
    and fees/exposure were committed)."""
    return select(func.count(Signal.id)).where(
        Signal.executed.is_(True),
        Signal.executed_at.isnot(None),
        Signal.executed_at >= now - timedelta(hours=24),
    )


def open_positions_cap_reached(live_executed: int, cap: int) -> bool:
    """OPEN-POSITIONS CAP (pure): True when `cap` executor-placed trades are
    still LIVE (resting entry order or open position, any direction). Closes
    the 24h-cap leak the owner spotted: trades placed yesterday that are still
    open roll out of the rolling window, so without this a fresh 3 would be
    stacked on top of yesterday's 3 - yesterday's open trades must consume
    today's allowance until they close. 0 disables."""
    return cap > 0 and live_executed >= cap


def live_executed_stmt():
    """Count of executor-placed trades that have not reached a terminal state:
    a resting entry order (PENDING_ENTRY) or an open position (ACTIVE)."""
    return select(func.count(Signal.id)).where(
        Signal.executed.is_(True),
        Signal.status.in_(NON_TERMINAL_STATUSES),
    )


def pending_execution_stmt():
    """The select for signals eligible for auto-execution.

    Observe-only guard for the Smart AI module: Smart AI signals (strategy_id =
    a named strategy) are recorded for attribution/analysis but MUST NOT be
    auto-executed here. Their execution is a separate, deliberate opt-in, so
    enabling a strategy can never silently start placing real testnet orders
    mixed in with the legacy bot's trades.

    PAPER FILLS ARE NOT EXECUTABLE. When a cap holds a signal back it is still
    RECORDED and still tracked - the monitor watches price and marks it ACTIVE
    if the entry zone is reached, so the stats can answer "what would the cap
    have cost us". That paper trade must never then be handed to the executor:
    entering it now would be a MARKET fill, hours after the zone was touched,
    at a price the decision was never made at - which destroys the very
    property pending entries exist for (`entry_price` == the real fill, so
    sizing, 1R breakeven and backtested RR are correct by construction).

    A pending-mode signal carries `entry_expires_at`; a market-mode one is born
    ACTIVE with it NULL and SHOULD be executed at once. So the rule is: a
    pending-mode signal is executable only while it is still PENDING_ENTRY.

    Without this the same three signals (XRP, DOGE, AAVE - 2026-08-31 to 09-02)
    sat in the queue being re-offered every cycle, each already trailed to a
    breakeven stop that no sizing call can use."""
    return (
        select(Signal)
        .options(selectinload(Signal.coin))
        .where(
            Signal.executed.is_(False),
            Signal.status.in_(NON_TERMINAL_STATUSES),
            or_(Signal.strategy_id.is_(None), Signal.strategy_id == LEGACY_STRATEGY_ID),
            or_(
                Signal.status == SignalStatus.PENDING_ENTRY,
                Signal.entry_expires_at.is_(None),   # market-mode: born ACTIVE
            ),
        )
    )


def execution_allowed(creds: dict | None, auto_trading_enabled: bool) -> bool:
    """The safety decision, isolated and pure so it can be tested exactly.

    Auto-execution is allowed ONLY when the saved credentials exist AND are
    flagged testnet AND the master auto-trading switch is on. Missing creds or
    mainnet creds always return False - this is what guarantees the executor can
    never place a mainnet order.
    """
    if not creds or not creds.get("testnet"):
        return False
    if not auto_trading_enabled:
        return False
    return True


class AutoExecutor:
    def __init__(self, interval_seconds: int = 20, retry_cooldown_seconds: int = 300):
        self._interval = interval_seconds
        self._retry_cooldown = retry_cooldown_seconds
        self._task: asyncio.Task | None = None
        self._stopped = False
        # A signal that hit a hard, non-retryable Binance error (e.g. a
        # duplicate client order id - the order already exists) must NEVER be
        # hammered every cycle. Skip it permanently (until restart).
        self._failed_ids: set[str] = set()
        # Everything else (notably risk-rejections, whose verdict can change as
        # positions open/close) is retried, but at most once per cooldown, so a
        # persistently-pending signal doesn't re-run risk + re-log every 20s.
        self._next_attempt: dict[str, float] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Testnet AutoExecutor started (every {self._interval}s, testnet-only)."
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self._run_once()
            except Exception as e:  # noqa: BLE001 - a loop must never die
                logger.exception(f"AutoExecutor cycle failed: {e}")
            await asyncio.sleep(self._interval)

    async def _run_once(self) -> None:
        # Gates 2 & 3 (TESTNET-ONLY + master switch). Checked FIRST, before
        # touching the database. See execution_allowed()'s docstring.
        creds = binance_credentials.load_credentials()
        if not execution_allowed(creds, get_auto_trading_enabled()):
            if not creds or not creds.get("testnet"):
                logger.warning(
                    "AutoExecutor: saved credentials are NOT testnet (or missing) "
                    "- refusing. This task never places mainnet orders."
                )
            return

        async with AsyncSessionLocal() as session:
            # DAILY TRADE CAP - see daily_cap_reached(). Counted once per
            # cycle from the DB, then tracked locally so a single cycle can
            # never place more than the remaining allowance either.
            settings = get_settings()
            cap = settings.max_trades_per_day
            open_cap = settings.max_open_positions
            placed_24h = 0
            if cap > 0:
                placed_24h = (
                    await session.execute(executed_last_24h_stmt(datetime.now(timezone.utc)))
                ).scalar_one()
            live_open = 0
            if open_cap > 0:
                live_open = (await session.execute(live_executed_stmt())).scalar_one()

            signals = (await session.execute(pending_execution_stmt())).scalars().unique().all()
            now = asyncio.get_event_loop().time()
            for signal in signals:
                if daily_cap_reached(placed_24h, cap):
                    logger.info(
                        f"AutoExecutor: daily trade cap reached ({placed_24h}/{cap} in 24h, "
                        f"long+short combined) - remaining signals stay recorded (paper) only."
                    )
                    break
                if open_positions_cap_reached(live_open, open_cap):
                    logger.info(
                        f"AutoExecutor: {live_open}/{open_cap} placed trades still live "
                        f"(resting or open) - no new placements until one closes; "
                        f"remaining signals stay recorded (paper) only."
                    )
                    break
                sid = str(signal.id)
                if sid in self._failed_ids:
                    continue                       # hard-failed once, never retry
                if now < self._next_attempt.get(sid, 0.0):
                    continue                       # still in cooldown
                self._next_attempt[sid] = now + self._retry_cooldown
                try:
                    await self._execute_one(session, signal)
                    if signal.executed:
                        placed_24h += 1            # count only real placements
                        live_open += 1             # ...which are now live too
                except Exception as e:  # noqa: BLE001 - isolate per-signal failures
                    await session.rollback()
                    sym = signal.coin.symbol if signal.coin else "?"
                    logger.warning(f"AutoExecutor: {sym} execution skipped: {e}")

    async def _execute_one(self, session, signal: Signal) -> None:
        if signal.coin is None or signal.executed:
            return

        symbol = signal.coin.symbol
        signal_service = SignalService(session)

        # RiskEngine gate - mandatory, mirrors POST /trading/execute exactly.
        try:
            assessment = await assess_execution_risk(signal_service, signal)
        except TradeRiskRejected as exc:
            logger.info(f"AutoExecutor: {symbol} rejected by risk engine: {exc.reasons}")
            return
        position = assessment.position_size
        if position is None:
            logger.info(f"AutoExecutor: {symbol} - could not size trade, skipping.")
            return

        trading_service = binance_credentials.build_trading_service_from_saved()
        try:
            if signal.status is SignalStatus.PENDING_ENTRY:
                entry_order = await trading_service.place_limit_entry(
                    symbol=symbol,
                    direction=signal.direction.value,
                    quantity=position["quantity"],
                    entry_price=signal.entry_price,
                    signal_id=str(signal.id),
                )
                signal.entry_order_id = str(entry_order.order_id)
                order_id = entry_order.order_id
            else:  # ACTIVE (market entry)
                execution = await trading_service.place_signal_bracket(
                    symbol=symbol,
                    direction=signal.direction.value,
                    quantity=position["quantity"],
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    entry_price=signal.entry_price,
                    signal_id=str(signal.id),
                )
                order_id = execution.entry_order.order_id
                signal.filled_at = datetime.now(timezone.utc)
                signal.actual_fill_price = execution.entry_order.avg_fill_price
        except BinanceTradingError as exc:
            # A Binance-side rejection is not going to fix itself on the next
            # cycle (a duplicate client order id means the order already
            # exists), so retire this signal instead of hammering the exchange.
            self._failed_ids.add(str(signal.id))
            logger.warning(
                f"AutoExecutor: {symbol} order rejected by Binance (won't retry): {exc}"
            )
            return
        finally:
            await trading_service.close()

        signal.executed = True
        signal.executed_order_id = str(order_id)
        signal.executed_at = datetime.now(timezone.utc)
        signal.executed_environment = trading_service.environment
        await session.commit()
        logger.success(
            f"AutoExecutor: EXECUTED {symbol} {signal.direction.value} "
            f"| status={signal.status.value} | order {order_id} "
            f"| env={trading_service.environment}"
        )
