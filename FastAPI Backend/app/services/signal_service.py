from __future__ import annotations

import csv
import io
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.signal_repository import SignalRepository
from app.repositories.history_repository import HistoryRepository
from app.schemas.signal import (
    SignalResponse,
    SignalListResponse,
    SignalQueryParams,
    PortfolioRiskItem,
    PortfolioRiskResponse,
    DailyLossResponse,
)
from app.schemas.stats import StatsResponse
from app.schemas.history import (
    HistoryItemResponse,
    HistoryListResponse,
    HistoryQueryParams,
    HistorySummaryResponse,
)
from app.models.signal import (
    DECIDED_STATUSES,
    NON_TERMINAL_STATUSES,
    Signal,
    SignalStatus,
)
from app.models.coin import Coin
from app.risk.risk_engine import RiskEngine
from app.services import binance_credentials
from app.services.position_sizing import calculate_position_size
from app.services.risk_audit import get_peak_equity
from app.services.trading_settings import get_risk_percent

# Module-level (not per-SignalService-instance, since a fresh instance is
# built per request via Depends) short-TTL cache for the account balance
# used in position sizing - avoids hitting Binance's API on every Live
# Signals poll/auto-refresh. Falls back to the last known-good balance on a
# transient fetch failure rather than blanking out suggested sizes.
# RISK ENGINE 2.0.0 (Phase 3): the cache now holds the WHOLE
# FuturesAccountInfo, not just the wallet balance. The same
# `get_full_snapshot()` call already being made yields margin_balance,
# total_maintenance_margin, per-position leverage and positionInitialMargin -
# everything Margin Usage / Effective Leverage / Liquidation Ratio need.
# Keeping only the balance meant those metrics had no data despite the API
# response already containing it. NO ADDITIONAL API CALL is introduced.
_ACCOUNT_BALANCE_CACHE: Dict[str, Any] = {
    "value": None, "futures": None, "fetched_at": 0.0,
    # WALL-CLOCK fetch time. `fetched_at` above is monotonic (correct for
    # TTL arithmetic, meaningless as a timestamp), but the audit record has
    # to state WHEN the exchange data behind a decision was actually read -
    # the gap between that and the decision time is its staleness.
    "fetched_wall": None,
}
_ACCOUNT_BALANCE_CACHE_TTL = 30.0  # seconds


async def _get_account_balance() -> Optional[float]:
    now = time.monotonic()
    if now - _ACCOUNT_BALANCE_CACHE["fetched_at"] < _ACCOUNT_BALANCE_CACHE_TTL:
        return _ACCOUNT_BALANCE_CACHE["value"]

    if not binance_credentials.has_saved_credentials():
        _ACCOUNT_BALANCE_CACHE.update(value=None, fetched_at=now)
        return None

    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError:
        _ACCOUNT_BALANCE_CACHE.update(value=None, fetched_at=now)
        return None

    try:
        snapshot = await service.get_full_snapshot()
        futures = snapshot.futures
        balance = futures.wallet_balance if futures else None
    except Exception as e:
        logger.warning(f"Could not fetch account balance for position sizing: {e}")
        balance = _ACCOUNT_BALANCE_CACHE["value"]
        futures = _ACCOUNT_BALANCE_CACHE["futures"]
    finally:
        await service.close()

    _ACCOUNT_BALANCE_CACHE.update(
        value=balance, futures=futures, fetched_at=now,
        fetched_wall=datetime.now(timezone.utc),
    )
    return balance


async def _get_futures_account():
    """The cached FuturesAccountInfo behind `_get_account_balance()`.

    Deliberately routed through the same call + same 30s cache so asking
    for margin/leverage/liquidation data costs ZERO extra Binance requests.
    Returns None when no account is linked or the fetch failed - callers
    must treat that as UNKNOWN, never as zero."""
    await _get_account_balance()
    return _ACCOUNT_BALANCE_CACHE["futures"]


def _futures_snapshot_time():
    """Wall-clock time the cached futures snapshot was fetched, for the
    audit record. None when nothing has been fetched."""
    return _ACCOUNT_BALANCE_CACHE.get("fetched_wall")


# % of account balance in realized loss (since UTC midnight) that flags
# DailyLossResponse.breached - advisory only, nothing auto-pauses on this.
DAILY_LOSS_WARNING_PERCENT = 5.0


@dataclass(frozen=True)
class PortfolioRiskContext:
    """Whole-account risk state, gathered ONCE per request and reused for
    every signal in it.

    `RiskEngine.assess_new_trade()` needs portfolio-wide inputs (open
    positions, today's closed trades, equity peak). Fetching those per
    signal inside `_signal_to_response()` would be a classic N+1 - one
    balance fetch and two queries per row in a 100-row page. Building the
    context once and threading it through keeps the risk read genuinely
    portfolio-aware without that cost.
    """

    account_balance: Optional[float] = None
    risk_percent: Optional[float] = None
    open_positions: tuple = ()
    closed_trades_today: tuple = ()
    equity_peak: Optional[float] = None
    # PHASE 3: SYMBOL -> risk_usd, computed from `Signal.initial_stop_loss`.
    # Binance reports a position's size and margin but has no idea where our
    # stop is, so this is the only source for its risk. A live position with
    # no entry here (opened outside this platform) keeps risk_usd=None, which
    # makes open_risk honestly UNKNOWN rather than silently risk-free.
    risk_usd_by_symbol: Dict[str, float] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.account_balance is not None and self.risk_percent is not None


class SignalService:
    """Service layer for signal business logic and schema mapping."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.signal_repo = SignalRepository(session)
        self.history_repo = HistoryRepository(session)
        # Stateless - one instance reused for every assessment in a request.
        self.risk_engine = RiskEngine()

    # ------------------------------------------------------------------
    # Portfolio risk context
    # ------------------------------------------------------------------
    async def build_portfolio_risk_context(
        self, exclude_signal_id: Optional[uuid.UUID] = None, committed_only: bool = False
    ) -> PortfolioRiskContext:
        """Public entry point for callers outside this service - e.g.
        `POST /trading/execute/{signal_id}` (app/api/v1/endpoints/trading.py),
        which needs the exact same portfolio-wide risk inputs the signal
        list/detail endpoints already display, so the number a user sees
        before clicking Execute and the number `RiskEngine` enforces at
        execution time can never disagree (see LIVE_EXECUTION_SAFETY_REPORT.md,
        Objective 1).

        `committed_only=True` (used by the execution gate) counts ONLY
        positions whose order actually exists on the exchange. The advisory
        display leaves it False and keeps its "if every signal were taken"
        meaning - see `SignalRepository.get_active_signals`."""
        return await self._build_risk_context(exclude_signal_id, committed_only=committed_only)

    async def _build_risk_context(
        self, exclude_signal_id: Optional[uuid.UUID] = None, committed_only: bool = False
    ) -> PortfolioRiskContext:
        """Gather the whole-account inputs `RiskEngine` needs.

        `exclude_signal_id` leaves one signal out of the open-positions
        tally so it isn't double-counted when RiskEngine is about to add it
        back as the trade being assessed.
        """
        account_balance = await _get_account_balance()
        risk_percent = get_risk_percent()

        if account_balance is None:
            return PortfolioRiskContext(account_balance=None, risk_percent=risk_percent)

        active_signals = await self.signal_repo.get_active_signals(committed_only=committed_only)
        open_positions = []
        risk_usd_by_symbol: Dict[str, float] = {}
        for s in active_signals:
            if exclude_signal_id is not None and s.id == exclude_signal_id:
                continue
            # RISK REFERENCE: `initial_stop_loss`, never the live `stop_loss`.
            # A trade whose stop has been trailed toward entry has NOT grown
            # its position - but sizing off the trailed stop would compute a
            # far larger quantity and therefore a far larger notional,
            # inflating portfolio exposure with capital that was never
            # deployed. At breakeven (stop == entry) it would collapse to
            # None and drop the position from the tally entirely.
            pos = calculate_position_size(
                account_balance, s.entry_price, s.initial_stop_loss, risk_percent
            )
            if pos:
                sym = s.coin.symbol.upper() if getattr(s, "coin", None) else None
                if sym:
                    risk_usd_by_symbol[sym] = risk_usd_by_symbol.get(sym, 0.0) + pos["risk_usd"]
                open_positions.append({
                    "risk_usd": pos["risk_usd"],
                    "notional_usd": pos["notional_usd"],
                })

        period_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        closed = await self.signal_repo.get_closed_signals_since(period_start)
        closed_trades_today = []
        for s in closed:
            # Quantity is sized from the ORIGINAL stop (that is the size
            # actually taken), but a STOPPED trade's realized loss is
            # measured against the stop that was actually HIT - the live
            # `stop_loss` as it stood at closure. A trade trailed to
            # breakeven and then stopped lost ~0, not a full R, and
            # reporting it as a full R would overstate the daily loss the
            # Risk Engine gates on.
            pos = calculate_position_size(
                account_balance, s.entry_price, s.initial_stop_loss, risk_percent,
                take_profit=s.take_profit,
            )
            if not pos:
                continue
            if s.status == SignalStatus.STOPPED:
                realized_loss = pos["quantity"] * abs(s.entry_price - s.stop_loss)
                closed_trades_today.append({"realized_pnl_usd": -round(realized_loss, 2)})
            elif s.status == SignalStatus.TP_HIT:
                closed_trades_today.append({"realized_pnl_usd": pos.get("profit_usd", 0.0)})

        # Equity peak is reconstructed as "balance before today's realized
        # P&L" whenever today has been net-negative. This is an honest
        # lower bound on the real peak, not a claim about all-time equity -
        # the platform has no equity-curve table to read a true historical
        # peak from, and inventing one would be fabrication. It correctly
        # catches an intraday drawdown, which is what the limit is for.
        realized_today = sum(t["realized_pnl_usd"] for t in closed_trades_today)
        # PHASE 4: the REAL high-water mark from the equity series, when one
        # exists. The same-day reconstruction below remains only as a
        # fallback for a database with no history yet - it is an honest
        # lower bound but cannot see a peak older than today, which is
        # exactly why `equity_snapshots` was added.
        equity_peak = await get_peak_equity()
        if equity_peak is None and realized_today < 0:
            equity_peak = account_balance - realized_today

        return PortfolioRiskContext(
            account_balance=account_balance,
            risk_percent=risk_percent,
            open_positions=tuple(open_positions),
            closed_trades_today=tuple(closed_trades_today),
            equity_peak=equity_peak,
            risk_usd_by_symbol=risk_usd_by_symbol,
        )

    def _signal_to_response(
        self,
        signal: Signal,
        account_balance: Optional[float] = None,
        risk_percent: Optional[float] = None,
        risk_context: Optional[PortfolioRiskContext] = None,
    ) -> SignalResponse:
        """Map a Signal ORM object to a SignalResponse, including the coin symbol.

        When `risk_context` is supplied the position size comes from the
        Unified Risk Engine (portfolio-aware: open risk, exposure, daily
        loss, drawdown) rather than a bare per-trade sizing call. Without
        it, sizing falls back to `calculate_position_size` exactly as
        before - RiskEngine reuses that same function internally, so the
        suggested numbers are identical either way; the context only adds
        the portfolio-level approval and its reasons.
        """
        coin_symbol = signal.coin.symbol if signal.coin else "UNKNOWN"

        position = None
        risk_approved = None
        risk_reasons: list[str] = []
        open_risk_percent = None
        exposure_percent = None

        if risk_context is not None and risk_context.is_available:
            assessment = self.risk_engine.assess_new_trade(
                account_balance=risk_context.account_balance,
                entry_price=signal.entry_price,
                stop_loss=signal.initial_stop_loss,
                take_profit=signal.take_profit,
                risk_percent=risk_context.risk_percent,
                open_positions=list(risk_context.open_positions),
                closed_trades_today=list(risk_context.closed_trades_today),
                equity_peak=risk_context.equity_peak,
            )
            position = assessment.position_size
            risk_approved = assessment.approved
            risk_reasons = list(assessment.reasons)
            open_risk_percent = assessment.open_risk_percent
            exposure_percent = assessment.exposure_percent
        elif account_balance is not None and risk_percent is not None:
            position = calculate_position_size(
                account_balance,
                signal.entry_price,
                signal.initial_stop_loss,
                risk_percent,
                take_profit=signal.take_profit,
            )

        return SignalResponse(
            risk_approved=risk_approved,
            risk_reasons=risk_reasons,
            portfolio_open_risk_percent=open_risk_percent,
            portfolio_exposure_percent=exposure_percent,
            id=signal.id,
            coin_id=signal.coin_id,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            initial_stop_loss=signal.initial_stop_loss,
            take_profit=signal.take_profit,
            risk_reward=signal.risk_reward,
            confidence=signal.confidence,
            reason=signal.reason,
            status=signal.status.value,
            timeframe=signal.timeframe,
            ai_model_version=signal.ai_model_version,
            created_at=signal.created_at,
            updated_at=signal.updated_at,
            closed_at=signal.closed_at,
            coin_symbol=coin_symbol,
            suggested_risk_usd=position["risk_usd"] if position else None,
            suggested_quantity=position["quantity"] if position else None,
            suggested_notional_usd=position["notional_usd"] if position else None,
            suggested_profit_usd=position.get("profit_usd") if position else None,
            suggested_loss_sl_usd=position.get("loss_sl_usd") if position else None,
            executed=signal.executed,
            executed_order_id=signal.executed_order_id,
            executed_at=signal.executed_at,
            executed_environment=signal.executed_environment,
        )

    async def get_signals(self, params: SignalQueryParams) -> SignalListResponse:
        """Return paginated, filtered list of signals."""
        signals, total = await self.signal_repo.get_signals(params)

        # Built once for the whole page - see PortfolioRiskContext's docstring
        # for why this is not done per signal.
        risk_context = await self._build_risk_context()
        items = [
            self._signal_to_response(
                s, risk_context.account_balance, risk_context.risk_percent, risk_context,
            )
            for s in signals
        ]

        return SignalListResponse(
            total=total,
            page=params.page,
            page_size=params.page_size,
            items=items,
        )

    async def get_signal_by_id(self, signal_id: uuid.UUID) -> Optional[SignalResponse]:
        """Get a single signal by ID, or None if not found."""
        signal = await self.signal_repo.get_signal_by_id(signal_id)

        if not signal:
            return None

        # Exclude this signal from the open-position tally so RiskEngine
        # doesn't count it twice when it adds the trade being assessed.
        risk_context = await self._build_risk_context(exclude_signal_id=signal.id)
        return self._signal_to_response(
            signal, risk_context.account_balance, risk_context.risk_percent, risk_context,
        )

    async def get_latest_signal(self) -> Optional[SignalResponse]:
        """Get the most recently created signal."""
        signal = await self.signal_repo.get_latest_signal()

        if not signal:
            return None

        risk_context = await self._build_risk_context(exclude_signal_id=signal.id)
        return self._signal_to_response(
            signal, risk_context.account_balance, risk_context.risk_percent, risk_context,
        )

    async def get_portfolio_risk(self) -> PortfolioRiskResponse:
        """
        Advisory-only: if every currently-ACTIVE signal's suggested
        position were taken right now, how much of the account would be at
        risk simultaneously? The bot never executes trades, so this can't
        enforce a cap - it's visibility into stacked exposure, especially
        useful alongside correlation_warning on individual signals.
        """
        active_signals = await self.signal_repo.get_active_signals()
        account_balance = await _get_account_balance()
        risk_percent = get_risk_percent()

        if account_balance is None:
            return PortfolioRiskResponse(
                active_signal_count=len(active_signals),
                message="No Binance account linked - can't compute $ risk exposure.",
            )

        items = []
        total_risk = 0.0
        for s in active_signals:
            pos = calculate_position_size(
                account_balance, s.entry_price, s.initial_stop_loss, risk_percent
            )
            if pos:
                items.append(PortfolioRiskItem(
                    coin_symbol=s.coin.symbol if s.coin else "UNKNOWN",
                    direction=s.direction.value,
                    risk_usd=pos["risk_usd"],
                ))
                total_risk += pos["risk_usd"]

        total_pct = round((total_risk / account_balance) * 100, 2) if account_balance else None

        return PortfolioRiskResponse(
            active_signal_count=len(active_signals),
            account_balance=account_balance,
            total_risk_usd=round(total_risk, 2),
            total_risk_percent_of_balance=total_pct,
            items=items,
        )

    async def get_daily_loss(self) -> DailyLossResponse:
        """
        Advisory-only: realized loss/profit (using suggested position
        sizing) across signals closed since UTC midnight today. Never
        auto-pauses anything - `breached` just flags that
        DAILY_LOSS_WARNING_PERCENT has been crossed so a human can decide
        whether to keep going.
        """
        period_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        closed = await self.signal_repo.get_closed_signals_since(period_start)

        # Reuses the SAME per-trade realized-P&L computation the Unified
        # Risk Engine's daily-loss limit reads, rather than a second copy of
        # it here - the two can never disagree about what today's P&L was.
        risk_context = await self._build_risk_context()
        account_balance = risk_context.account_balance

        realized_pnl = sum(t["realized_pnl_usd"] for t in risk_context.closed_trades_today)
        realized_loss = abs(min(realized_pnl, 0.0))
        loss_pct = round((realized_loss / account_balance) * 100, 2) if account_balance else None
        breached = loss_pct is not None and loss_pct >= DAILY_LOSS_WARNING_PERCENT

        message = None
        if account_balance is None:
            message = "No Binance account linked - counting closed trades only, can't compute $ P&L."
        elif breached:
            message = (
                f"Today's realized loss ({loss_pct}%) has reached the "
                f"{DAILY_LOSS_WARNING_PERCENT}% warning threshold - consider pausing for today."
            )

        return DailyLossResponse(
            period_start=period_start,
            account_balance=account_balance,
            realized_loss_usd=round(realized_loss, 2),
            realized_pnl_usd=round(realized_pnl, 2),
            loss_percent_of_balance=loss_pct,
            warning_threshold_percent=DAILY_LOSS_WARNING_PERCENT,
            breached=breached,
            closed_trade_count=len(closed),
            message=message,
        )

    async def get_signal_counts_for_control_panel(self) -> Dict[str, int]:
        """
        Three plain counts the Auto Trading Control Panel's status cards
        need (2026-07-30) - Active Trades, Pending Signals, Executed
        Signals. Same `select(func.count(...))` pattern `get_stats()` below
        already uses; a dedicated method rather than reusing `get_signals()`
        because none of these three are expressible with that endpoint's
        existing filters (it has no `executed` query parameter).

          - active_trades: executed=True AND status=ACTIVE - a real,
            currently-open position this platform placed.
          - pending_signals: executed=False AND status=ACTIVE - a signal
            still awaiting an Execute click, not yet a real order.
          - executed_signals: executed=True, any status - total signals
            ever sent to Binance through this platform (open or since
            closed by TP/SL/structure failure).
        """
        active_trades = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status == SignalStatus.ACTIVE, Signal.executed.is_(True),
                )
            )
        ).scalar_one()

        # NOTE the deliberate distinction, which matters under the ICT
        # pending-entry model:
        #   pending_signals    - awaiting an Execute click. No order exists.
        #                        (unchanged meaning, now spanning both
        #                        PENDING_ENTRY and ACTIVE not-yet-executed.)
        #   pending_entries    - NEW: an ICT entry is armed with a real
        #                        LIMIT order resting on the exchange,
        #                        waiting for price to reach the zone. Real
        #                        committed capital, but no position yet.
        pending_signals = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status.in_(NON_TERMINAL_STATUSES), Signal.executed.is_(False),
                )
            )
        ).scalar_one()

        pending_entries = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status == SignalStatus.PENDING_ENTRY, Signal.executed.is_(True),
                )
            )
        ).scalar_one()

        executed_signals = (
            await self.session.execute(
                select(func.count(Signal.id)).where(Signal.executed.is_(True))
            )
        ).scalar_one()

        return {
            "active_trades": active_trades,
            "pending_signals": pending_signals,
            "pending_entries": pending_entries,
            "executed_signals": executed_signals,
        }

    async def get_stats(self) -> StatsResponse:
        """Calculate aggregate trading statistics over all signals."""

        total = (
            await self.session.execute(
                select(func.count(Signal.id))
            )
        ).scalar_one()

        active = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status == SignalStatus.ACTIVE
                )
            )
        ).scalar_one()

        # "Closed" must mean DECIDED - a trade that actually ran and
        # reached an outcome. It is deliberately NOT `!= ACTIVE`: under the
        # ICT pending-entry model that would sweep in PENDING_ENTRY (not
        # started yet) and EXPIRED (never entered at all), inflating the
        # win-rate denominator with trades that were never taken and
        # silently corrupting every statistic on the Dashboard, the
        # Statistics screen and the AI Performance report.
        closed = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status.in_(DECIDED_STATUSES)
                )
            )
        ).scalar_one()

        wins = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status.in_([SignalStatus.TP_HIT])
                )
            )
        ).scalar_one()

        win_rate = (wins / closed * 100) if closed else 0.0

        avg_conf = (
            await self.session.execute(
                select(func.avg(Signal.confidence))
            )
        ).scalar_one() or 0.0

        avg_rr = (
            await self.session.execute(
                select(func.avg(Signal.risk_reward))
            )
        ).scalar_one() or 0.0

        best_row = (
            await self.session.execute(
                select(
                    Coin.symbol,
                    func.count(Signal.id).label("wins"),
                )
                .join(Signal, Signal.coin_id == Coin.id)
                .where(
                    Signal.status.in_([SignalStatus.TP_HIT])
                )
                .group_by(Coin.symbol)
                .order_by(func.count(Signal.id).desc())
                .limit(1)
            )
        ).first()

        # Production-safe fallback
        best_symbol = best_row[0] if best_row else ""

        return StatsResponse(
            total_signals=total,
            active_signals=active,
            closed_signals=closed,
            win_rate=round(win_rate, 2),
            avg_confidence=round(float(avg_conf), 2),
            avg_risk_reward=round(float(avg_rr), 2),
            best_performing_symbol=best_symbol,
        )

    @staticmethod
    def _signal_to_history_item(signal: Signal) -> HistoryItemResponse:
        """Map a closed Signal ORM object to a HistoryItemResponse."""
        coin_symbol = signal.coin.symbol if signal.coin else "UNKNOWN"

        return HistoryItemResponse(
            id=signal.id,
            coin_id=signal.coin_id,
            coin_symbol=coin_symbol,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            initial_stop_loss=signal.initial_stop_loss,
            take_profit=signal.take_profit,
            risk_reward=signal.risk_reward,
            confidence=signal.confidence,
            reason=signal.reason,
            result=signal.status.value,
            timeframe=signal.timeframe,
            ai_model_version=signal.ai_model_version,
            created_at=signal.created_at,
            updated_at=signal.updated_at,
            closed_at=signal.closed_at,
        )

    async def get_history(self, params: HistoryQueryParams) -> HistoryListResponse:
        """Return a paginated, filtered list of closed signal history records."""
        signals, total = await self.history_repo.get_history(params)

        items = [self._signal_to_history_item(s) for s in signals]

        return HistoryListResponse(
            total=total,
            page=params.page,
            page_size=params.page_size,
            items=items,
        )

    async def get_history_summary(self, params: HistoryQueryParams) -> HistorySummaryResponse:
        """
        Return aggregated win/loss statistics for closed signal history matching
        the provided filters. CANCELLED signals are excluded from all counts.
        """
        total_trades, wins = await self.history_repo.get_summary(params)
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades else 0.0

        return HistorySummaryResponse(
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 2),
        )

    async def export_history_csv(self, params: HistoryQueryParams) -> str:
        """
        Return the full (unpaginated) filtered signal history as CSV text.
        """
        signals = await self.history_repo.get_all_matching(params)

        buffer = io.StringIO()
        fieldnames = [
            "id",
            "coin_symbol",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "confidence",
            "result",
            "timeframe",
            "created_at",
            "closed_at",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()

        for signal in signals:
            item = self._signal_to_history_item(signal)
            writer.writerow(
                {
                    "id": str(item.id),
                    "coin_symbol": item.coin_symbol,
                    "direction": item.direction,
                    "entry_price": item.entry_price,
                    "stop_loss": item.stop_loss,
                    "take_profit": item.take_profit,
                    "risk_reward": item.risk_reward,
                    "confidence": item.confidence,
                    "result": item.result,
                    "timeframe": item.timeframe,
                    "created_at": item.created_at.isoformat() if item.created_at else "",
                    "closed_at": item.closed_at.isoformat() if item.closed_at else "",
                }
            )

        return buffer.getvalue()