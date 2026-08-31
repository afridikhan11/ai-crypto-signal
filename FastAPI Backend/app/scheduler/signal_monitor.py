"""
Signal outcome tracker and live ICT Trade Management.

Nothing previously closed a signal once it was created - every Signal
stayed status=ACTIVE forever regardless of what price actually did
afterward. That meant the History screen was always empty, win-rate
stats were meaningless, and AI scorer calibration (app/ai/calibration.py)
could never activate, since it only counts real closed WIN/LOSS trades.

This module polls live price (from the same cached WebSocket data the
scanner already streams - no extra API calls) against every ACTIVE
signal's stop-loss/take-profit levels, and closes the signal the moment
one is hit.

ICT TRADE MANAGEMENT (2026-07-29)
--------------------------------------------------------------------------
`TradeManagementEngine` (app/strategy/trade_management_engine.py) is now
applied to every open signal on each poll, making the stop DYNAMIC rather
than static for the life of the trade:

  - MOVE_TO_BREAKEVEN  - once price reaches 1R, the stop moves to entry.
  - TRAIL_STOP          - a fresh same-direction BOS/CHoCH trails the stop
                          to the newest protected structural level.
  - CLOSE_STRUCTURE_FAILURE - an opposing BOS/CHoCH while the trade is
                          open closes it as CANCELLED (a structural exit,
                          deliberately NOT recorded as STOPPED, since price
                          never reached the stop - conflating the two would
                          corrupt both the win-rate stats and the scorer
                          calibration that reads them).

SAFETY INVARIANTS
--------------------------------------------------------------------------
1. Stops only ever move TOWARD profit. `TradeManagementEngine` enforces
   forward-only trailing internally, and `_apply_stop_update()` re-checks
   it here before persisting - a stop can never be widened, so managing a
   trade can only ever reduce its risk, never increase it.
2. TP/SL resolution runs FIRST, against the stop as currently persisted.
   A stop tightened on this poll therefore only takes effect from the NEXT
   poll onward - a trailed stop can never retroactively close a trade at a
   level that was not in force when price traded there.
3. Only structure breaks that occurred strictly AFTER the signal was
   created are considered. Without this, the very break that produced the
   signal (and every older one) would be re-read as "structure failure"
   the instant the trade opened.

DELIBERATELY NOT DONE HERE (disclosed, not an oversight)
--------------------------------------------------------------------------
`PARTIAL_CLOSE` recommendations are logged and published but never
persisted or executed. This platform's execution model is one order per
signal (see `Signal.executed` / `executed_order_id` - a single boolean and
a single order id), and there is no schema field to record "50% already
closed". Acting on a partial without that state would re-trigger the same
partial on every subsequent 30-second poll. Persisting partial-fill state
requires new `Signal` columns, and this repository has no migration tool
configured (no Alembic), so adding columns would break any already-
deployed database. That is a schema decision that needs explicit approval
rather than a silent change - see the implementation report.

EXCHANGE STOP SYNCHRONIZATION (2026-07-30, Live Execution Safety)
--------------------------------------------------------------------------
Previously, every stop adjustment below only ever updated `Signal.stop_loss`
in the database - the real STOP_MARKET order resting on Binance for an
EXECUTED signal (`signal.executed is True`) was never touched, so the
database, the dashboard, and the exchange could silently disagree about how
a live position was actually protected.

`_sync_exchange_stop()` now closes that gap for every executed signal:

  - Only attempted when `signal.executed` - a signal that was never sent to
    Binance has no real order to keep in sync, so behavior for it is
    UNCHANGED (DB-only, exactly as before).
  - Uses `signal.executed_environment` (recorded at execution time) to
    refuse syncing against a MISMATCHED currently-saved credential
    environment (e.g. Settings flipped from mainnet to testnet after this
    signal was executed on mainnet) rather than silently guessing.
  - The DATABASE stop_loss is only updated once the EXCHANGE sync has
    actually succeeded (or wasn't applicable) - never before, and never on
    a failed sync - so `Signal.stop_loss` can never claim a tighter stop
    than what is really resting on Binance. See `BinanceTradingService.
    replace_stop_loss()` for the new-stop-before-cancel-old ordering
    guarantee that makes a failed sync leave existing protection untouched
    rather than briefly absent.
  - A live position's `CLOSE_STRUCTURE_FAILURE` now also closes the REAL
    position (a single reduceOnly MARKET order, reusing
    `BinanceTradingService.close_position`, the same method
    `POST /trading/close-position` already uses) and cancels any remaining
    resting stop order - a structure-failure verdict means "this trade is
    over," and for an executed signal that must mean the live position
    actually closes, not just a database label. If the live close itself
    fails, the DB is still marked CANCELLED (that judgement is about the
    ICT trade thesis, not about execution), but a loud warning is logged
    and published so a human can close the position manually - the same
    "never silently continue" discipline `place_signal_bracket` already
    uses for a failed bracket leg.

NOT ADDRESSED (deployment-model assumption, disclosed)
--------------------------------------------------------------------------
This module assumes a single running `SignalMonitor` instance (matches this
project's documented single-user, single-process deployment - see
`app/services/binance_credentials.py`). `check_active_signals()` runs
sequentially inside one `asyncio` loop with no concurrent pollers, so there
is no cross-poll race within this process. Running multiple worker
processes against the same database would require a real distributed lock,
which is out of scope for this validation-safety phase.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.constants import PENDING_ENTRY_EXPIRY_MINUTES
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.diagnostics.signal_pipeline_diagnostics import diagnostics
from app.models.signal import Signal, SignalStatus, Direction
from app.services import binance_credentials
from app.services.binance_account_service import BinanceAccountService
from app.services.binance_service import BinanceDataManager
from app.services.binance_trading_service import BinanceTradingError, BinanceTradingService
from app.services.trading_settings import get_engine_run_state, get_trade_management_enabled
from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.session_engine import SessionEngine
from app.strategy.trade_management_engine import (
    ManagementAction,
    ManagementActionType,
    TradeManagementEngine,
)

# Structure timeframe used for trade management - the SAME timeframe
# SignalGenerator used to open the trade, so "structure failed" here means
# failure of the exact structure the entry was based on, not a faster
# timeframe's noise.
MANAGEMENT_TIMEFRAME = "15m"
MANAGEMENT_CANDLES = 500

# Binance error code for "order type not supported for this endpoint - use the
# Algo Order API instead". It means the venue (notably Binance Demo/Mock
# Trading) does not accept resting STOP_MARKET conditional orders at all. When
# we see it, exchange stop-syncing is pointless for that whole environment, so
# we stop attempting it and let the SOFTWARE stop (this monitor) enforce the
# stop instead - rather than re-failing, and re-logging, every single poll.
_UNSUPPORTED_ORDER_CODE = "-4120"


def tp1_level_for(entry, direction, tp1_pct: float, take_profit) -> Optional[float]:
    """PARTIAL TAKE-PROFIT level (pure): entry +/- tp1_pct%, in the trade's
    favorable direction. Returns None when the feature is disabled, inputs are
    missing, or the signal's own final target sits NEARER than TP1 - a
    tiny-range trade gets no partial and runs exactly as before. The final
    target itself is never re-priced (the PR #21 lesson)."""
    if tp1_pct <= 0 or not entry or not take_profit:
        return None
    if direction == Direction.LONG:
        tp1 = entry * (1 + tp1_pct / 100.0)
        return tp1 if take_profit > tp1 else None
    tp1 = entry * (1 - tp1_pct / 100.0)
    return tp1 if take_profit < tp1 else None


def tp1_reached(price: float, tp1: float, direction) -> bool:
    """Pure: has the live price reached the TP1 level in the trade's favor?"""
    return price >= tp1 if direction == Direction.LONG else price <= tp1


class SignalMonitor:
    POLL_INTERVAL_SECONDS = 30.0

    # An executed ACTIVE signal whose live position is absent from the
    # exchange is only reconciled (closed) after this many CONSECUTIVE polls
    # confirm the absence - never on a single blank, which could be a
    # transient/stale account snapshot. ~5 polls * 30s ~= 2.5 min.
    POSITION_VANISHED_RECONCILE_POLLS = 5

    def __init__(self, data_manager: BinanceDataManager):
        self.data_manager = data_manager
        self._running = False
        # Stateless engines - built once, reused across every poll.
        self.trade_manager = TradeManagementEngine()
        self.session_engine = SessionEngine()
        # signal id -> number of consecutive polls its live position has been
        # missing from the exchange. Drives vanished-position reconciliation.
        self._position_absent_polls: Dict[str, int] = {}
        # Environments ("testnet"/"mainnet") that rejected an exchange STOP
        # order with -4120 (e.g. Binance Demo). Once an environment is in here,
        # stop management for its executed signals is routed to the software
        # stop instead of re-attempting (and re-spamming) the exchange sync.
        self._exchange_stops_unsupported: set[str] = set()

    def _resolve_status(self, signal: Signal, price: float) -> Optional[SignalStatus]:
        """
        Return the terminal status `signal` should transition to given the
        current live `price`, or None if it should stay ACTIVE.
        """
        if signal.direction == Direction.LONG:
            if price >= signal.take_profit:
                return SignalStatus.TP_HIT
            if price <= signal.stop_loss:
                return SignalStatus.STOPPED
        else:
            if price <= signal.take_profit:
                return SignalStatus.TP_HIT
            if price >= signal.stop_loss:
                return SignalStatus.STOPPED
        return None

    def _reconcile_vanished_position(
        self, signal: Signal, symbol: str, open_position_symbols: set
    ) -> Optional[SignalStatus]:
        """Decide whether an executed ACTIVE signal whose live position is
        absent from the exchange should be closed.

        An executed ACTIVE signal is supposed to have a live position. When
        the exchange shows none, the position closed for a reason the
        close-price TP/SL resolver did not catch: the venue reset the account
        (common on Binance Demo), the exit filled on a wick between two 1m
        closes, or it was closed manually. Left alone, that signal can never
        resolve by TP/SL - it stays ACTIVE forever, a false open trade that
        also re-spams the stop-sync warning every poll.

        The absence is only acted on after POSITION_VANISHED_RECONCILE_POLLS
        CONSECUTIVE confirmations, so one transient or stale snapshot can never
        close a real trade. On the threshold it returns CANCELLED - the trade
        WAS open and closed for a reason other than our recorded TP/SL, which
        is exactly what CANCELLED means (and, unlike STOPPED/TP_HIT, does not
        fabricate a win or a loss for an outcome we cannot actually determine).
        Returns None while the position is present or the count is still below
        the threshold.
        """
        sid = str(signal.id)
        if symbol.upper() in open_position_symbols:
            self._position_absent_polls.pop(sid, None)
            return None
        count = self._position_absent_polls.get(sid, 0) + 1
        if count < self.POSITION_VANISHED_RECONCILE_POLLS:
            self._position_absent_polls[sid] = count
            return None
        self._position_absent_polls.pop(sid, None)
        return SignalStatus.CANCELLED

    # ------------------------------------------------------------------
    # ICT Pending Limit Entry (2026-07-30) - the pending state machine.
    #
    # A PENDING_ENTRY signal has NO position and nothing at risk. Exactly
    # one of three things can happen to it, checked in this order because
    # the order matters for correctness:
    #
    #   1. INVALIDATED - price reached the stop level before ever reaching
    #      the entry zone. The idea was wrong before it began. Checked
    #      FIRST: if a single candle spans both the entry zone and the stop,
    #      treating it as a fill would invent a trade that, on that same
    #      candle, was already stopped out. Refusing to enter is the honest
    #      and the safe reading.
    #   2. FILLED - price traded into the entry zone. Becomes ACTIVE and is
    #      managed exactly as any other open trade from then on.
    #   3. EXPIRED - the entry window passed with no fill.
    #
    # A resting LIMIT order on the exchange is reconciled against Binance
    # itself rather than inferred from candles (see
    # `_reconcile_pending_exchange_order`), because the exchange is the only
    # authority on whether an order actually filled and for how much.
    # ------------------------------------------------------------------
    @staticmethod
    def _entry_zone_bounds(signal: Signal) -> tuple[float, float]:
        """The price band that counts as a fill. Falls back to the exact
        entry price when no zone bounds were recorded (a "market"-type entry
        under entry_mode "market", or an older row created before this
        migration) - never a guessed width."""
        top = signal.entry_zone_top
        bottom = signal.entry_zone_bottom
        if top is None or bottom is None or top < bottom:
            return signal.entry_price, signal.entry_price
        return top, bottom

    def _pending_outcome(self, signal: Signal, high: float, low: float) -> Optional[SignalStatus]:
        """
        What this candle's real high/low did to a pending entry. Uses the
        candle EXTREMES, not the close: price genuinely trading through a
        level is what fills an order or takes out a stop, and a close-only
        test would miss both.
        """
        zone_top, zone_bottom = self._entry_zone_bounds(signal)

        if signal.direction == Direction.LONG:
            # Stop is below a long's entry - invalidation first (see above).
            if low <= signal.stop_loss:
                return SignalStatus.EXPIRED
            if low <= zone_top:
                return SignalStatus.ACTIVE
        else:
            if high >= signal.stop_loss:
                return SignalStatus.EXPIRED
            if high >= zone_bottom:
                return SignalStatus.ACTIVE
        return None

    @staticmethod
    def _pending_expired(signal: Signal, now: datetime) -> bool:
        expires_at = signal.entry_expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now >= expires_at

    async def _cancel_resting_entry_order(self, signal: Signal, symbol: str) -> bool:
        """
        Cancels this signal's resting LIMIT entry on the exchange. Called
        whenever a pending signal stops being valid (expired or
        invalidated) so an order can never outlive the reasoning that
        placed it. Returns True when a cancel was actually sent.

        A failure is logged loudly and swallowed: the DB transition to
        EXPIRED still happens either way (that is a judgement about the ICT
        setup, not about the exchange), and the Auto Trading panel's Cancel
        Pending Orders / Kill Switch remain the backstop.
        """
        if not signal.executed or not signal.entry_order_id:
            return False
        service = self._build_trading_service_for(signal)
        if service is None:
            return False
        try:
            await service.cancel_order(symbol, int(signal.entry_order_id))
            logger.info(f"{symbol}: resting LIMIT entry {signal.entry_order_id} cancelled (pending entry ended).")
            return True
        except (BinanceTradingError, ValueError, Exception) as e:  # noqa: BLE001
            logger.warning(
                f"{symbol}: could not cancel resting LIMIT entry {signal.entry_order_id}: {e}. "
                "Use Cancel Pending Orders on the Auto Trading panel if it is still open."
            )
            return False
        finally:
            await service.close()

    async def _reconcile_pending_exchange_order(self, signal: Signal, symbol: str) -> Optional[dict]:
        """
        Asks Binance what actually happened to this signal's resting LIMIT
        entry. The exchange is the only authority on a fill, so an executed
        pending signal is resolved from this rather than from candle data.

        Returns the raw order dict, or None when it cannot be read (no
        credentials, environment mismatch, network failure) - in which case
        the caller leaves the signal pending and tries again next poll,
        rather than guessing.
        """
        if not signal.executed or not signal.entry_order_id:
            return None
        service = self._build_trading_service_for(signal)
        if service is None:
            return None
        try:
            return await service.get_order(symbol, int(signal.entry_order_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{symbol}: could not read resting entry order {signal.entry_order_id}: {e}")
            return None
        finally:
            await service.close()

    async def _close_unprotected_position(
        self, signal: Signal, symbol: str, service
    ) -> bool:
        """Flatten a position that has no stop resting behind it, and cancel
        whatever else is left over (typically the take-profit LIMIT, which
        would otherwise rest against a flat position).

        Returns True when the position is confirmed gone - including when
        there was nothing to close - and False when the exchange refused,
        which is the case the caller must shout about."""
        try:
            quantity = await self._get_live_position_quantity(symbol)
            if quantity:
                await service.close_position(symbol, quantity)
                logger.success(
                    f"{symbol}: unprotected position ({quantity}) MARKET-closed "
                    f"(signal {signal.id})."
                )
            for order in await service.get_all_open_orders(symbol):
                if order.get("orderId") is not None:
                    await service.cancel_any_order(symbol, order)
            return True
        except BinanceTradingError as exc:
            logger.error(f"{symbol}: emergency close of an unprotected position FAILED: {exc}")
            return False

    async def _place_protection_after_fill(
        self, signal: Signal, symbol: str, filled_qty: float
    ) -> List[str]:
        """Stage 2 of ICT pending execution: attach the stop-loss and
        take-profit now that a real position exists. `filled_qty` is the
        REAL executed quantity from the exchange, so a partial fill is
        protected at the size that actually filled."""
        service = self._build_trading_service_for(signal)
        if service is None:
            return ["Entry filled but no trading service available - position is UNPROTECTED."]
        try:
            _sl, _tp, warnings = await service.place_protective_orders(
                symbol=symbol,
                direction=signal.direction.value,
                quantity=filled_qty,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                signal_id=str(signal.id),
            )
            if warnings:
                for w in warnings:
                    logger.error(f"{symbol}: {w}")
            else:
                logger.success(
                    f"{symbol}: protective SL/TP attached after pending entry filled "
                    f"| qty={filled_qty} | SL={signal.stop_loss} | TP={signal.take_profit}"
                )

            # VERIFY, don't assume. A filled entry with no resting stop is the
            # one state this platform must never leave behind - and it is
            # exactly what happened to ETHUSDT on 2026-08-30, where a 0.15%
            # stop distance was already breached by the time the stop was
            # placed (-2021 "order would immediately trigger") and the
            # position sat open and naked.
            #
            # Asking the exchange what is actually resting covers every reason
            # the stop might be missing, not just the ones we thought to
            # handle. If none is, the position is closed at market: when the
            # stop is already breached that IS the stop doing its job, and
            # when it is anything else an unprotected position is not a thing
            # to keep. The software stop only protects while this process is
            # alive, so it is not an answer here.
            try:
                resting = await service.get_open_stop_orders(symbol)
            except BinanceTradingError as exc:
                logger.warning(f"{symbol}: could not verify the resting stop after fill: {exc}")
                return warnings

            if not resting:
                logger.error(
                    f"{symbol}: entry filled but NO stop is resting on the exchange - "
                    f"closing the position at market rather than leaving it unprotected."
                )
                if await self._close_unprotected_position(signal, symbol, service):
                    # The trade is over, so the signal must say so rather than
                    # sit ACTIVE against a position that no longer exists.
                    # STOPPED is the honest label: the usual reason a stop
                    # cannot be placed is that price has already passed it.
                    # `signal_stats` classifies win/loss by REAL P/L, not by
                    # this status, so a close nearer entry is still reported
                    # for what it actually was.
                    signal.status = SignalStatus.STOPPED
                    signal.closed_at = datetime.now(timezone.utc)
                    warnings.append(
                        "Entry filled but no stop could be placed - the position was closed "
                        "at market immediately."
                    )
                else:
                    warnings.append(
                        "Entry filled, NO stop is resting, and the emergency close FAILED - "
                        "this position is open and unprotected. Close it manually NOW."
                    )
            return warnings
        except Exception as e:  # noqa: BLE001
            logger.exception(f"{symbol}: FAILED to attach protective orders after fill: {e}")
            return [f"Entry filled but protective orders failed: {e}. Protect this position manually."]
        finally:
            await service.close()

    # ------------------------------------------------------------------
    # Trade management helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _breaks_after(structure_breaks: List, created_at: Optional[datetime]) -> List:
        """Only structure breaks strictly newer than the signal itself.

        Safety invariant 3 (see module docstring): without this filter the
        break that CREATED the signal would immediately read as a structure
        event against it. Timestamps on the dataframe are tz-naive UTC (the
        convention documented in app/smc/session_engine.py); `created_at`
        is tz-aware, so it is normalized to tz-naive UTC before comparing.
        """
        if not structure_breaks or created_at is None:
            return []
        cutoff = pd.Timestamp(created_at)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_convert("UTC").tz_localize(None)
        return [b for b in structure_breaks if pd.Timestamp(b.timestamp) > cutoff]

    def _improves_stop(self, signal: Signal, new_stop: float) -> bool:
        """Safety invariant 1 - a stop may only ever move toward profit."""
        if signal.direction == Direction.LONG:
            return new_stop > signal.stop_loss
        return new_stop < signal.stop_loss

    def _evaluate_management(self, signal: Signal, symbol: str, price: float) -> List[ManagementAction]:
        """Run TradeManagementEngine against fresh structure for this signal.

        Returns an empty list (never a guess) when the structure dataframe
        isn't available - a missing frame must not be read as "no structure
        failure", it simply means nothing can be concluded this poll.
        """
        df = self.data_manager.get_dataframe(symbol, MANAGEMENT_TIMEFRAME, limit=MANAGEMENT_CANDLES)
        if df.empty or len(df) < 50:
            return []

        snapshot = MarketStructureEngine(df, external_pivot_window=5).analyze()
        fresh_breaks = self._breaks_after(snapshot.external_breaks, signal.created_at)
        session_ctx = self.session_engine.latest_context(df)

        return self.trade_manager.evaluate(
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            current_price=price,
            structure_breaks=fresh_breaks,
            # exit_plan is intentionally omitted - partial closes are not
            # executable or persistable in this execution model. See module
            # docstring.
            exit_plan=None,
            session_context=session_ctx,
        )

    # ------------------------------------------------------------------
    # Exchange stop synchronization
    # ------------------------------------------------------------------
    def _build_trading_service_for(self, signal: Signal) -> Optional[BinanceTradingService]:
        """
        Returns a `BinanceTradingService` for `signal`'s ORIGINAL execution
        environment, or None (with a logged reason) when exchange sync
        cannot safely proceed. Never guesses: a missing credential or an
        environment mismatch skips the sync entirely rather than sending a
        request against the wrong account/environment.
        """
        if not signal.executed:
            return None

        creds = binance_credentials.load_credentials()
        if creds is None:
            logger.warning(
                f"Signal {signal.id}: executed but no saved Binance credentials are available - "
                f"cannot sync its exchange stop this poll."
            )
            return None

        current_environment = "testnet" if creds.get("testnet", False) else "mainnet"
        if signal.executed_environment and signal.executed_environment != current_environment:
            logger.warning(
                f"Signal {signal.id}: was executed on '{signal.executed_environment}' but the currently "
                f"saved credentials are '{current_environment}' - refusing to sync its exchange stop "
                f"against a mismatched environment. Existing exchange-side protection is untouched."
            )
            return None

        return BinanceTradingService(
            api_key=creds["api_key"], api_secret=creds["api_secret"], testnet=bool(creds.get("testnet", False)),
        )

    async def _get_live_position_quantity(self, symbol: str) -> Optional[float]:
        """
        The LIVE, signed position size for `symbol` fetched fresh from
        Binance - never trusted from any stored value, matching the same
        "never trust a client-supplied quantity" discipline
        `POST /trading/close-position` already uses. Returns None (not 0)
        when no open position is found, so the caller can tell "nothing to
        sync" apart from "a genuinely zero-size position". Reads the same
        saved credentials `_build_trading_service_for` already validated
        for this signal - this is a second (cheap, local-disk) read rather
        than a threaded-through object, keeping this method independently
        callable/testable.
        """
        creds = binance_credentials.load_credentials()
        if creds is None:
            return None
        account_service = BinanceAccountService(
            api_key=creds["api_key"], api_secret=creds["api_secret"], testnet=bool(creds.get("testnet", False)),
        )
        try:
            futures = await account_service.get_futures_account()
        except Exception as exc:
            logger.warning(f"{symbol}: could not fetch live position for exchange stop sync: {exc}")
            return None
        finally:
            await account_service.close()

        if futures is None:
            return None
        position = next((p for p in futures.open_positions if p.symbol == symbol.upper()), None)
        if position is None or position.position_amt == 0:
            return None
        return abs(position.position_amt)

    def _current_environment(self) -> Optional[str]:
        """"testnet"/"mainnet" for the currently saved credentials, or None
        when none are available. Matches BinanceTradingService.environment."""
        creds = binance_credentials.load_credentials()
        if not creds:
            return None
        return "testnet" if creds.get("testnet") else "mainnet"

    def _note_exchange_stops_unsupported(self, environment: str, symbol: str) -> None:
        """Remember (once, with one log line) that `environment` rejects
        exchange STOP orders, so we stop re-attempting the sync every poll."""
        if environment in self._exchange_stops_unsupported:
            return
        self._exchange_stops_unsupported.add(environment)
        logger.warning(
            f"{symbol}: environment '{environment}' rejects exchange STOP orders (code "
            f"{_UNSUPPORTED_ORDER_CODE}) - routing stop management to the SOFTWARE stop for all "
            f"executed signals here. Positions stay protected by this monitor; no exchange stop "
            f"order rests. This is expected on Binance Demo and is logged once, not every poll."
        )

    async def _sync_exchange_stop(self, signal: Signal, symbol: str, new_stop: float) -> bool:
        """
        Moves the REAL resting stop order to `new_stop` for an executed
        signal. Returns True when the exchange now genuinely reflects
        `new_stop` (or when no sync was needed/possible - see below), False
        when the database's `stop_loss` must NOT be advanced this poll
        because the exchange could not be confirmed at the new price.

        Returns True for a non-executed signal (nothing to sync - DB-only
        behavior, unchanged from before this feature existed) so callers
        can treat "may I update the DB stop now" uniformly for both cases.
        """
        if not signal.executed:
            return True

        trading_service = self._build_trading_service_for(signal)
        if trading_service is None:
            # Already logged inside _build_trading_service_for. Do not
            # advance the DB stop - it must not claim a tighter stop than
            # what is actually resting (or unknown) on the exchange.
            return False

        # Fast path: an environment already known to reject exchange STOP
        # orders (Binance Demo, -4120) is not retried. Return False silently -
        # _apply_management routes this to the software stop. No API call, no
        # per-poll spam.
        if trading_service.environment in self._exchange_stops_unsupported:
            await trading_service.close()
            return False

        try:
            quantity = await self._get_live_position_quantity(symbol)
            if quantity is None:
                logger.warning(
                    f"{symbol}: no live position found for executed signal {signal.id} - skipping "
                    f"exchange stop sync this poll (DB stop not advanced)."
                )
                return False

            result = await trading_service.replace_stop_loss(
                symbol=symbol,
                direction=signal.direction.value,
                quantity=quantity,
                new_stop_price=new_stop,
                signal_id=str(signal.id),
            )
        except BinanceTradingError as exc:
            if _UNSUPPORTED_ORDER_CODE in str(exc):
                self._note_exchange_stops_unsupported(trading_service.environment, symbol)
            else:
                logger.warning(f"{symbol}: Exchange Sync Failed - stop sync for signal {signal.id}: {exc}")
            return False
        finally:
            await trading_service.close()

        if not result.success:
            if _UNSUPPORTED_ORDER_CODE in (result.warning or ""):
                self._note_exchange_stops_unsupported(trading_service.environment, symbol)
            else:
                logger.warning(f"{symbol}: Exchange Sync Failed - signal {signal.id}: {result.warning}")
            return False

        logger.success(
            f"{symbol}: Exchange Sync Success - stop replaced at {new_stop} "
            f"(order {result.new_order.order_id if result.new_order else '?'}, "
            f"cancelled {result.cancelled_order_ids})."
        )
        if result.warning:
            logger.warning(f"{symbol}: exchange sync succeeded with a note: {result.warning}")
        return True

    async def _close_live_position_on_structure_failure(self, signal: Signal, symbol: str) -> None:
        """
        For an EXECUTED signal, a structure-failure verdict must also close
        the REAL position - otherwise the database says the trade is over
        while a real position with real risk stays open, unmanaged,
        forever. Reuses `BinanceTradingService.close_position` (the exact
        method `POST /trading/close-position` already uses) rather than a
        second implementation. A failure here is logged loudly - it never
        silently leaves the DB looking consistent when the exchange isn't.
        """
        if not signal.executed:
            return

        trading_service = self._build_trading_service_for(signal)
        if trading_service is None:
            return

        try:
            quantity = await self._get_live_position_quantity(symbol)
            if quantity is None:
                logger.info(f"{symbol}: structure failure on signal {signal.id} - no live position found to close.")
                return

            direction_sign = 1 if signal.direction == Direction.LONG else -1
            await trading_service.close_position(symbol, direction_sign * quantity)
            logger.success(f"{symbol}: Exchange Sync Success - live position closed on structure failure (signal {signal.id}).")

            # Clean up any stop order left resting against a now-flat
            # position - harmless if Binance already auto-cancelled it,
            # never fatal if this fails (logged, not raised).
            try:
                stray_stops = await trading_service.get_open_stop_orders(symbol)
                for stop in stray_stops:
                    order_id = stop.get("orderId")
                    if order_id is not None:
                        await trading_service.cancel_order(symbol, order_id)
                        logger.info(f"{symbol}: stray stop order {order_id} cancelled after structure-failure close.")
            except BinanceTradingError as exc:
                logger.warning(f"{symbol}: could not clean up stray stop order after structure-failure close: {exc}")
        except BinanceTradingError as exc:
            logger.warning(
                f"{symbol}: Exchange Sync Failed - could not close the live position for signal {signal.id} "
                f"after structure failure: {exc}. The database marks this signal CANCELLED, but the REAL "
                f"position may still be open - close it manually."
            )
        finally:
            await trading_service.close()

    async def _force_close_resolved_position(
        self, signal: Signal, symbol: str, price: float, status_label: str
    ) -> None:
        """Once a signal resolves (TP_HIT or STOPPED), close ANY remaining live
        position with a reduceOnly MARKET order and cancel every resting order.

        Two venue realities make this necessary rather than trusting the resting
        exchange orders alone:
          - STOP_MARKET is rejected on some venues (Binance Demo returns -4120),
            so a hit stop never actually closes the position.
          - A reduceOnly LIMIT take-profit can PARTIALLY fill in thin books
            (observed on Demo: a 4599 position with only 321 filled at the TP),
            leaving a large residual open after the monitor has already marked
            the signal TP_HIT and stopped managing it.

        Closing the residual here makes the DB status match reality. It is a
        harmless no-op where the exchange order already flattened the position
        (`_get_live_position_quantity` returns nothing), and reduceOnly can only
        reduce, never open, exposure. Best-effort - a failure is logged, never
        raised, and never blocks outcome resolution. All resting orders (the
        stop AND the take-profit LIMIT) are then cancelled so nothing stray
        remains against a now-flat position.
        """
        if not signal.executed:
            return
        trading_service = self._build_trading_service_for(signal)
        if trading_service is None:
            return
        try:
            quantity = await self._get_live_position_quantity(symbol)
            if quantity:
                direction_sign = 1 if signal.direction == Direction.LONG else -1
                await trading_service.close_position(symbol, direction_sign * quantity)
                logger.success(
                    f"{symbol}: {status_label} - residual position ({quantity}) "
                    f"MARKET-closed at {price} (signal {signal.id}, "
                    f"env={trading_service.environment})."
                )
            # Cancel every resting order (stop + take-profit LIMIT) - harmless if
            # Binance already auto-cancelled them against a flat position.
            try:
                for order in await trading_service.get_all_open_orders(symbol):
                    if order.get("orderId") is not None:
                        # The list mixes both services - cancel_any_order routes
                        # each row to the endpoint that owns it.
                        await trading_service.cancel_any_order(symbol, order)
            except BinanceTradingError as exc:
                logger.warning(f"{symbol}: could not clean up resting orders after {status_label}: {exc}")
            # Conditional (algo) stops live on a separate service and never
            # appear in /fapi/v1/openOrders, so they need their own sweep or
            # a stray stop would keep resting against a now-flat position.
            try:
                await trading_service.cancel_all_conditional_orders(symbol)
            except BinanceTradingError as exc:
                logger.debug(f"{symbol}: no algo orders to clean up after {status_label} ({exc}).")
        except BinanceTradingError as exc:
            logger.warning(
                f"{symbol}: force-close on {status_label} FAILED for signal {signal.id}: {exc}. "
                f"DB marks {status_label} but the real position may still be open - check it."
            )
        finally:
            await trading_service.close()

    async def _maybe_take_partial_profit(
        self, signal: Signal, symbol: str, price: float
    ) -> List[Dict]:
        """PARTIAL TAKE-PROFIT: at TP1 (entry +/- SIGNAL_TP1_PCT%), close
        SIGNAL_TP1_FRACTION of the live position and move the stop to
        breakeven; the remainder runs to the signal's existing structure
        target. Fires at most once per trade (tp1_done). Returns the event
        dicts to publish (empty when nothing happened). Best-effort on the
        exchange leg: a failed partial close is logged and retried next poll
        (tp1_done is only set once the bookkeeping is committed here)."""
        settings = get_settings()
        tp1_pct = settings.signal_tp1_pct
        fraction = settings.signal_tp1_fraction
        if tp1_pct <= 0 or not (0 < fraction < 1) or signal.tp1_done:
            return []

        entry = signal.actual_fill_price or signal.entry_price
        if signal.tp1_price is None:
            level = tp1_level_for(entry, signal.direction, tp1_pct, signal.take_profit)
            if level is None:
                return []          # feature off for this trade (target nearer than TP1)
            signal.tp1_price = level

        if not tp1_reached(price, signal.tp1_price, signal.direction):
            return []

        # 1. Bank the partial on the exchange (executed trades only).
        if signal.executed:
            closed = await self._partial_close_live_position(signal, symbol, fraction)
            if not closed:
                # Exchange leg failed - do NOT mark tp1_done, so the next poll
                # retries while price is still beyond TP1.
                return []

        # 2. Move the stop to breakeven (never worsen an already-better stop).
        stop_moved = False
        if entry and self._improves_stop(signal, entry):
            signal.stop_loss = entry
            stop_moved = True
            if signal.executed:
                try:
                    await self._sync_exchange_stop(signal, symbol, entry)
                except Exception as e:  # noqa: BLE001 - software stop still enforces it
                    logger.warning(f"{symbol}: TP1 breakeven stop sync failed (software stop active): {e}")

        signal.tp1_done = True
        logger.success(
            f"{symbol}: TP1 HIT at {price} (level {signal.tp1_price}) - "
            f"{fraction:.0%} banked{' , stop -> breakeven' if stop_moved else ''}; "
            f"runner continues to {signal.take_profit} (signal {signal.id})."
        )
        return [{
            "event": "tp1_partial",
            "symbol": symbol,
            "price": price,
            "tp1_price": signal.tp1_price,
            "fraction": fraction,
            "stop_moved_to_breakeven": stop_moved,
        }]

    async def _partial_close_live_position(
        self, signal: Signal, symbol: str, fraction: float
    ) -> bool:
        """Close `fraction` of the live position with a reduceOnly MARKET order
        (same venue mechanics as _force_close_resolved_position). True on
        success OR when there is nothing to close / no service (bookkeeping
        may proceed); False only when the exchange call itself failed."""
        trading_service = self._build_trading_service_for(signal)
        if trading_service is None:
            return True
        try:
            quantity = await self._get_live_position_quantity(symbol)
            if not quantity:
                return True        # already flat on the exchange - nothing to bank
            part = quantity * fraction
            direction_sign = 1 if signal.direction == Direction.LONG else -1
            await trading_service.close_position(symbol, direction_sign * part)
            logger.success(
                f"{symbol}: partial close {part} ({fraction:.0%} of {quantity}) "
                f"at TP1 (signal {signal.id}, env={trading_service.environment})."
            )
            return True
        except BinanceTradingError as exc:
            logger.warning(f"{symbol}: TP1 partial close FAILED (will retry next poll): {exc}")
            return False
        finally:
            await trading_service.close()

    async def _apply_management(
        self, signal: Signal, symbol: str, price: float, actions: List[ManagementAction],
    ) -> List[Dict]:
        """Persist whatever an action list actually changes on the Signal.

        Returns the events to publish. Structure failure takes precedence
        over stop adjustments: if the trade is being closed, moving its stop
        first would be meaningless bookkeeping.
        """
        events: List[Dict] = []

        structure_failure = next(
            (a for a in actions if a.action_type is ManagementActionType.CLOSE_STRUCTURE_FAILURE), None,
        )
        if structure_failure is not None:
            was_executed = signal.executed
            signal.status = SignalStatus.CANCELLED
            signal.closed_at = datetime.now(timezone.utc)
            logger.warning(
                f"{symbol}: Signal CLOSED by structure failure | {structure_failure.reason} | "
                f"Price={price} | Entry={signal.entry_price}"
            )
            # For an EXECUTED signal this must also close the REAL position -
            # see module docstring "EXCHANGE STOP SYNCHRONIZATION". A failed
            # live close is logged loudly by the helper itself; the DB
            # transition to CANCELLED still happens either way (that
            # judgement is about the ICT trade thesis, not execution).
            if was_executed:
                await self._close_live_position_on_structure_failure(signal, symbol)
            events.append({
                "event": "signal_closed",
                "symbol": symbol,
                "status": SignalStatus.CANCELLED.value,
                "price": price,
                "management_action": structure_failure.action_type.value,
                "reason": structure_failure.reason,
                "live_position_closed": was_executed,
            })
            return events

        # Apply the single best stop improvement. TRAIL_STOP is preferred
        # over MOVE_TO_BREAKEVEN when both fire and both are valid, since a
        # trailed structural stop is by definition at least as protective
        # as breakeven at that point in the trade.
        best_stop: Optional[float] = None
        best_action: Optional[ManagementAction] = None
        for action in actions:
            if action.action_type not in (
                ManagementActionType.MOVE_TO_BREAKEVEN, ManagementActionType.TRAIL_STOP,
            ):
                continue
            if action.new_stop_loss is None or not self._improves_stop(signal, action.new_stop_loss):
                continue
            is_better = best_stop is None or (
                action.new_stop_loss > best_stop if signal.direction == Direction.LONG
                else action.new_stop_loss < best_stop
            )
            if is_better:
                best_stop, best_action = action.new_stop_loss, action

        if best_action is not None and best_stop is not None:
            new_stop = round(best_stop, 8)
            previous_stop = signal.stop_loss

            # Exchange sync FIRST - the database's stop_loss is only ever
            # advanced once the exchange (or the "nothing to sync, not
            # executed yet" case) confirms it. See module docstring
            # "EXCHANGE STOP SYNCHRONIZATION" and
            # `BinanceTradingService.replace_stop_loss()`'s ordering
            # guarantee. A failed sync leaves BOTH the DB and the exchange
            # at the previous, still-valid stop - never a partial/
            # inconsistent state.
            synced = await self._sync_exchange_stop(signal, symbol, new_stop)
            software_stop = (
                signal.executed
                and not synced
                and self._current_environment() in self._exchange_stops_unsupported
            )
            if synced or software_stop:
                signal.stop_loss = new_stop
                if software_stop:
                    # No exchange order rests here (e.g. Binance Demo, -4120),
                    # so the software stop is the operative stop and reads
                    # signal.stop_loss directly - advancing it means breakeven/
                    # trailing genuinely protect the position, with no spam.
                    logger.info(
                        f"{symbol}: Stop managed {previous_stop} -> {signal.stop_loss} "
                        f"({best_action.action_type.value}) via SOFTWARE stop | {best_action.reason}"
                    )
                else:
                    logger.success(
                        f"{symbol}: Stop managed {previous_stop} -> {signal.stop_loss} "
                        f"({best_action.action_type.value}) | {best_action.reason}"
                    )
                events.append({
                    "event": "signal_stop_updated",
                    "symbol": symbol,
                    "previous_stop_loss": previous_stop,
                    "stop_loss": signal.stop_loss,
                    "management_action": best_action.action_type.value,
                    "reason": best_action.reason,
                    # True only for a REAL exchange stop replacement: a
                    # non-executed (DB-only) signal or a software-stop advance
                    # (unsupported env) both report False.
                    "exchange_synced": bool(signal.executed and synced),
                })
            else:
                logger.warning(
                    f"{symbol}: stop improvement to {new_stop} ({best_action.action_type.value}) computed but "
                    f"NOT applied - exchange sync did not succeed, database stop_loss remains {previous_stop}."
                )

        for action in actions:
            if action.action_type is ManagementActionType.PARTIAL_CLOSE:
                # Advisory only - not executable or persistable here. See
                # the module docstring for the full reasoning.
                logger.info(f"{symbol}: Partial-close opportunity (advisory) | {action.reason}")

        return events

    # ------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------
    async def _capture_equity_snapshot(self) -> None:
        """One point on the equity curve, from the already-cached futures
        snapshot. Environment-tagged so a testnet curve can never
        contribute a peak to a mainnet drawdown."""
        try:
            from app.services.risk_audit import record_equity_snapshot
            from app.services.signal_service import _get_futures_account

            futures = await _get_futures_account()
            if futures is None:
                return
            creds = binance_credentials.load_credentials()
            environment = ("testnet" if creds.get("testnet") else "mainnet") if creds else None
            await record_equity_snapshot(
                equity=getattr(futures, "margin_balance", None) or getattr(futures, "wallet_balance", 0.0),
                wallet_balance=getattr(futures, "wallet_balance", None),
                unrealized_pnl=getattr(futures, "unrealized_pnl", None),
                total_maintenance_margin=getattr(futures, "total_maintenance_margin", None),
                environment=environment,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Equity snapshot capture skipped: {e}")

    async def _process_pending_entries(self) -> None:
        """
        One pass over every PENDING_ENTRY signal. Nothing here can lose
        money: a pending signal holds no position, so the only outcomes are
        "becomes a real trade", "never happened", or "keep waiting".

        Fill detection has two sources, and the more authoritative one wins:
          - EXECUTED signals (a real LIMIT order is resting on Binance) are
            resolved from the EXCHANGE's own view of that order.
          - Un-executed signals (advisory only, no order was ever placed)
            are resolved from real candle highs/lows.
        """
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            stmt = (
                select(Signal)
                .options(selectinload(Signal.coin))
                .where(Signal.status == SignalStatus.PENDING_ENTRY)
            )
            pending = (await session.execute(stmt)).scalars().unique().all()
            if not pending:
                return

            events: List[Dict] = []
            dirty = False

            for signal in pending:
                symbol = signal.coin.symbol if signal.coin else None
                if not symbol:
                    continue

                # ---- 1. Exchange truth for signals with a resting order ----
                if signal.executed and signal.entry_order_id:
                    raw = await self._reconcile_pending_exchange_order(signal, symbol)
                    if raw is not None:
                        status = str(raw.get("status", "")).upper()
                        executed_qty = float(raw.get("executedQty", 0) or 0)
                        if status == "FILLED" or (status == "PARTIALLY_FILLED" and executed_qty > 0):
                            # G6 (A5-lite, 2026-08-01): prefer the exchange's
                            # own avgPrice. When it is absent, falling back to
                            # entry_price is CORRECT only for a LIMIT entry
                            # (it fills at its limit price by construction).
                            # For any other entry type (STOP_MARKET entries
                            # exist as of G3, and fill with slippage) the fill
                            # price is genuinely unknown -> None, never the
                            # planned entry.
                            avg_price = float(raw.get("avgPrice", 0) or 0) or None
                            if avg_price is None and str(raw.get("type", "")).upper() == "LIMIT":
                                avg_price = signal.entry_price
                            if avg_price is None:
                                logger.warning(
                                    f"{symbol}: entry order {signal.entry_order_id} filled but Binance "
                                    f"reported no avgPrice and the order is not a LIMIT - recording "
                                    f"actual_fill_price as unknown rather than substituting the planned entry."
                                )
                            signal.status = SignalStatus.ACTIVE
                            signal.filled_at = now
                            signal.actual_fill_price = avg_price
                            dirty = True
                            if status == "PARTIALLY_FILLED":
                                logger.warning(
                                    f"{symbol}: pending entry PARTIALLY filled ({executed_qty}) - "
                                    "protecting the filled size only."
                                )
                            warnings = await self._place_protection_after_fill(signal, symbol, executed_qty)
                            logger.success(
                                f"{symbol}: ICT pending entry FILLED @ {avg_price} "
                                f"(limit {signal.entry_price}) - now ACTIVE."
                            )
                            diagnostics.record_pending_entry_outcome(symbol, filled=True)
                            events.append({
                                "event": "pending_entry_filled", "symbol": symbol,
                                "status": SignalStatus.ACTIVE.value, "price": avg_price,
                                "warnings": warnings,
                            })
                            continue
                        if status in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
                            signal.status = SignalStatus.EXPIRED
                            signal.closed_at = now
                            dirty = True
                            logger.info(f"{symbol}: resting entry order is {status} - signal EXPIRED (never entered).")
                            diagnostics.record_pending_entry_outcome(
                                symbol, filled=False, reason="Pending Entry Order Cancelled On Exchange")
                            events.append({
                                "event": "pending_entry_expired", "symbol": symbol,
                                "status": SignalStatus.EXPIRED.value, "reason": f"exchange order {status}",
                            })
                            continue
                        # Still NEW / unfilled - fall through to the expiry
                        # check below so a stale resting order still dies.

                # ---- 2. Candle truth (fill / invalidation) ----
                else:
                    df = self.data_manager.get_dataframe(symbol, "1m", limit=5)
                    if not df.empty:
                        high = float(df["high"].max())
                        low = float(df["low"].min())
                        outcome = self._pending_outcome(signal, high, low)
                        if outcome is SignalStatus.EXPIRED:
                            signal.status = SignalStatus.EXPIRED
                            signal.closed_at = now
                            dirty = True
                            logger.info(
                                f"{symbol}: pending entry INVALIDATED - price reached the stop "
                                f"({signal.stop_loss}) before the entry zone. Never entered."
                            )
                            diagnostics.record_pending_entry_outcome(
                                symbol, filled=False, reason="Pending Entry Invalidated Before Fill")
                            events.append({
                                "event": "pending_entry_expired", "symbol": symbol,
                                "status": SignalStatus.EXPIRED.value, "reason": "invalidated before entry",
                            })
                            continue
                        if outcome is SignalStatus.ACTIVE:
                            signal.status = SignalStatus.ACTIVE
                            signal.filled_at = now
                            signal.actual_fill_price = signal.entry_price
                            dirty = True
                            logger.success(
                                f"{symbol}: ICT pending entry reached @ {signal.entry_price} "
                                f"({signal.entry_type}) - now ACTIVE."
                            )
                            diagnostics.record_pending_entry_outcome(symbol, filled=True)
                            events.append({
                                "event": "pending_entry_filled", "symbol": symbol,
                                "status": SignalStatus.ACTIVE.value, "price": signal.entry_price,
                            })
                            continue

                # ---- 3. Expiry ----
                if self._pending_expired(signal, now):
                    await self._cancel_resting_entry_order(signal, symbol)
                    signal.status = SignalStatus.EXPIRED
                    signal.closed_at = now
                    dirty = True
                    logger.info(
                        f"{symbol}: pending entry EXPIRED unfilled after "
                        f"{PENDING_ENTRY_EXPIRY_MINUTES} minutes - price never reached the entry zone."
                    )
                    diagnostics.record_pending_entry_outcome(symbol, filled=False)
                    events.append({
                        "event": "pending_entry_expired", "symbol": symbol,
                        "status": SignalStatus.EXPIRED.value, "reason": "entry window elapsed",
                    })

            if dirty:
                await session.commit()
                for event in events:
                    await redis_client.publish("new_signal", json.dumps(event, default=str))

    async def check_active_signals(self) -> None:
        # Auto Trading Control Panel (2026-07-30) - Pause/Stop gate. Skips
        # this ENTIRE poll (no TP/SL resolution, no trade management, no
        # exchange sync) - real orders already resting on Binance for an
        # executed signal are completely untouched either way; this only
        # freezes the platform's own bookkeeping/decision-making. Defaults
        # "running", so this is a no-op until a user explicitly pauses/stops
        # from the panel. See app/services/trading_settings.py.
        if get_engine_run_state() != "running":
            return

        management_enabled = get_trade_management_enabled()

        # PHASE 4: append one point to the equity curve each poll. This is
        # the ONLY source of the high-water mark Drawdown needs - without
        # it that metric can only report UNKNOWN. Reuses the already-cached
        # futures snapshot, so no extra Binance call. Best-effort: a failed
        # write must never interrupt trade monitoring.
        await self._capture_equity_snapshot()

        # ICT pending entries are resolved BEFORE open trades, in their own
        # transaction, so a signal that fills on this poll is immediately
        # eligible for TP/SL resolution and trade management in the same
        # poll rather than waiting another 30s while unprotected.
        await self._process_pending_entries()

        async with AsyncSessionLocal() as session:
            stmt = (
                select(Signal)
                .options(selectinload(Signal.coin))
                .where(Signal.status == SignalStatus.ACTIVE)
            )
            result = await session.execute(stmt)
            active_signals = result.scalars().unique().all()

            if not active_signals:
                return

            # Reconciliation snapshot: reuse the SAME cached futures account
            # the equity snapshot above just populated (30s TTL) so this costs
            # ZERO extra Binance calls. `open_position_symbols` is the set of
            # symbols with a live, non-zero position right now. It stays None
            # when the snapshot is unavailable this poll (no creds / never
            # fetched) - reconciliation is then skipped entirely, because a
            # blank snapshot must never be read as "the position closed". The
            # cache already falls back to the last-good snapshot on a transient
            # fetch error, so a single API glitch does not blank this.
            open_position_symbols: Optional[set] = None
            try:
                from app.services.signal_service import _get_futures_account
                futures = await _get_futures_account()
                if futures is not None:
                    open_position_symbols = {
                        p.symbol.upper()
                        for p in futures.open_positions
                        if p.position_amt != 0
                    }
            except Exception as e:  # noqa: BLE001 - never break the poll
                logger.warning(f"Reconciliation snapshot unavailable this poll: {e}")
            creds = binance_credentials.load_credentials()
            current_environment = (
                ("testnet" if creds.get("testnet") else "mainnet") if creds else None
            )

            events: List[Dict] = []
            dirty = False
            for signal in active_signals:
                symbol = signal.coin.symbol if signal.coin else None
                if not symbol:
                    continue

                df = self.data_manager.get_dataframe(symbol, "1m", limit=1)
                if df.empty:
                    continue
                price = float(df["close"].iloc[-1])

                # Safety invariant 2 - resolve against the stop as it stands
                # right now, BEFORE any management adjustment this poll.
                new_status = self._resolve_status(signal, price)
                if new_status is not None:
                    signal.status = new_status
                    signal.closed_at = datetime.now(timezone.utc)
                    dirty = True
                    events.append({
                        "event": "signal_closed",
                        "symbol": symbol,
                        "status": new_status.value,
                        "price": price,
                    })
                    logger.success(
                        f"{symbol}: Signal closed | {new_status.value} | "
                        f"Close price={price} | Confidence={signal.confidence}"
                    )
                    # Force-close backstop for BOTH terminal outcomes:
                    #  - STOPPED: the exchange STOP order may never have rested
                    #    (e.g. Binance Demo rejects STOP_MARKET), so nothing
                    #    closed the position.
                    #  - TP_HIT: the resting reduceOnly LIMIT take-profit can
                    #    PARTIALLY fill (e.g. 321 of 4599 NEAR), leaving a
                    #    residual position open even though price reached the TP.
                    # Either way, flatten any residual quantity and cancel every
                    # leftover order so the trade is truly closed.
                    if new_status in (SignalStatus.STOPPED, SignalStatus.TP_HIT):
                        try:
                            await self._force_close_resolved_position(
                                signal, symbol, price, new_status.value
                            )
                        except Exception as e:  # noqa: BLE001 - never break resolution
                            logger.warning(f"{symbol}: force-close backstop errored: {e}")
                    continue

                # Reconcile a VANISHED live position. Only for an executed
                # signal whose ORIGINAL environment matches the currently saved
                # credentials (mirrors _build_trading_service_for - never judge
                # a mainnet-executed trade against a testnet snapshot, or vice
                # versa). A non-executed advisory signal has no exchange
                # position by definition and is deliberately left untouched.
                if (
                    open_position_symbols is not None
                    and signal.executed
                    and (
                        not signal.executed_environment
                        or signal.executed_environment == current_environment
                    )
                ):
                    reconciled = self._reconcile_vanished_position(
                        signal, symbol, open_position_symbols
                    )
                    if reconciled is not None:
                        signal.status = reconciled
                        signal.closed_at = datetime.now(timezone.utc)
                        dirty = True
                        events.append({
                            "event": "signal_closed",
                            "symbol": symbol,
                            "status": reconciled.value,
                            "price": price,
                            "reason": "live position no longer on exchange (reconciled)",
                        })
                        logger.warning(
                            f"{symbol}: Signal RECONCILED to {reconciled.value} - its live "
                            f"position was missing from the exchange for "
                            f"{self.POSITION_VANISHED_RECONCILE_POLLS} consecutive polls "
                            f"(signal {signal.id}). Cancelling any stray resting orders."
                        )
                        try:
                            await self._force_close_resolved_position(
                                signal, symbol, price, reconciled.value
                            )
                        except Exception as e:  # noqa: BLE001 - never break resolution
                            logger.warning(f"{symbol}: reconcile cleanup errored: {e}")
                        continue

                # PARTIAL TAKE-PROFIT - after resolution (a trade that already
                # hit its final TP/stop this poll is handled above), before
                # trade management (which may trail the stop further). Errors
                # never break the poll; an exchange-leg failure retries next
                # poll because tp1_done is only set on success.
                try:
                    tp1_events = await self._maybe_take_partial_profit(signal, symbol, price)
                    if tp1_events:
                        dirty = True
                        events.extend(tp1_events)
                except Exception as e:  # noqa: BLE001 - never break the poll
                    logger.warning(f"{symbol}: partial-TP check errored: {e}")

                if not management_enabled:
                    continue

                try:
                    actions = self._evaluate_management(signal, symbol, price)
                except Exception as e:
                    # Trade management must never be able to take down the
                    # outcome tracker - a management failure on one symbol
                    # leaves that trade on its existing static stop, which
                    # is exactly the pre-management behavior.
                    logger.exception(f"{symbol}: Trade management evaluation failed: {e}")
                    continue

                try:
                    management_events = await self._apply_management(signal, symbol, price, actions)
                except Exception as e:
                    # Same containment discipline as the evaluation step
                    # above - an exchange-sync failure (network, Binance
                    # error, etc.) must never take down the outcome
                    # tracker or leave a partially-applied DB change; the
                    # signal simply stays on its previous, still-valid
                    # stop/status until the next poll.
                    logger.exception(f"{symbol}: Trade management application failed: {e}")
                    continue
                if management_events:
                    dirty = True
                    events.extend(management_events)

            if dirty:
                await session.commit()
                for event in events:
                    # Reuse the same channel/message shape the WPF client
                    # already listens on for new signals - it triggers a
                    # list reload on any non-ping message, so closure and
                    # stop-update events both refresh the UI the same way.
                    await redis_client.publish("new_signal", json.dumps(event, default=str))

    async def start(self):
        self._running = True
        logger.info("Signal outcome monitor started.")
        while self._running:
            try:
                await self.check_active_signals()
            except Exception as e:
                logger.exception(f"Signal monitor check failed: {e}")
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    async def stop(self):
        self._running = False
        logger.info("Signal outcome monitor stopped.")

    @property
    def is_running(self) -> bool:
        """Whether the monitor's own asyncio lifecycle is alive - distinct
        from `engine_run_state` (Pause/Resume), which governs whether each
        poll actually does anything. Read by `GET /trading/status`."""
        return self._running
