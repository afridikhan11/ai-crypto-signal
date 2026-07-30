from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import func, select, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.signal import Signal, SignalStatus, Direction
from app.models.coin import Coin
from app.schemas.history import HistoryQueryParams

ALLOWED_SORT_FIELDS = {
    "closed_at": Signal.closed_at,
    "created_at": Signal.created_at,
    "updated_at": Signal.updated_at,
    "direction": Signal.direction,
    "result": Signal.status,
    "confidence": Signal.confidence,
    "risk_reward": Signal.risk_reward,
    "entry_price": Signal.entry_price,
    "timeframe": Signal.timeframe,
}

# Statuses that represent a finished signal (i.e. neither still ACTIVE nor
# still waiting on a PENDING_ENTRY fill). EXPIRED is included so an ICT
# pending entry that never filled is still VISIBLE in History - hiding it
# would make setups silently disappear from the record.
CLOSED_STATUSES = [
    SignalStatus.TP_HIT,
    SignalStatus.STOPPED,
    SignalStatus.CANCELLED,
    SignalStatus.EXPIRED,
]

# Statuses counted as a "win" for summary statistics.
WIN_STATUSES = [SignalStatus.TP_HIT]

# Statuses counted toward win/loss totals. CANCELLED is deliberately excluded
# per product decision: cancelled signals should not affect win_rate. EXPIRED
# is excluded for a stronger reason - that trade was never entered at all, so
# it is neither a win nor a loss and must not move the win rate in either
# direction.
COUNTED_STATUSES = WIN_STATUSES + [SignalStatus.STOPPED]


class HistoryRepository:
    """Async repository for querying closed Signal records for the History module."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _safe_direction(self, value: Optional[str]) -> Optional[Direction]:
        if not value:
            return None
        try:
            return Direction(value.upper())
        except ValueError:
            logger.warning(f"Invalid direction filter ignored: {value}")
            return None

    def _safe_result_status(self, value: Optional[str]) -> Optional[SignalStatus]:
        if not value:
            return None
        try:
            return SignalStatus(value.upper())
        except ValueError:
            logger.warning(f"Invalid result filter ignored: {value}")
            return None

    def _build_filters(self, params: HistoryQueryParams) -> list:
        """Build the common WHERE-clause filters shared by list, summary, and export queries."""
        filters = [Signal.status.in_(CLOSED_STATUSES)]

        if params.symbol:
            filters.append(Coin.symbol == params.symbol.upper())

        direction = self._safe_direction(params.direction)
        if direction is not None:
            filters.append(Signal.direction == direction)

        result_status = self._safe_result_status(params.result)
        if result_status is not None:
            filters.append(Signal.status == result_status)

        if params.start_date is not None:
            filters.append(Signal.closed_at >= params.start_date)
        if params.end_date is not None:
            filters.append(Signal.closed_at <= params.end_date)

        return filters

    async def get_history(self, params: HistoryQueryParams) -> Tuple[list[Signal], int]:
        """
        Return a paginated list of closed signals matching the provided filters,
        along with the total number of matching records. Eagerly loads the coin
        relationship to avoid N+1 queries.
        """
        filters = self._build_filters(params)

        base_query = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .join(Coin, Signal.coin_id == Coin.id)
            .where(*filters)
        )

        count_query = (
            select(func.count(Signal.id))
            .select_from(Signal)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(*filters)
        )
        total = (await self.session.execute(count_query)).scalar_one()

        sort_column = ALLOWED_SORT_FIELDS.get(params.sort_by, Signal.closed_at)
        order_fn = desc if params.sort_order == "desc" else asc
        query = base_query.order_by(order_fn(sort_column))

        offset = (params.page - 1) * params.page_size
        query = query.offset(offset).limit(params.page_size)

        result = await self.session.execute(query)
        signals = result.scalars().unique().all()
        return signals, total

    async def get_all_matching(self, params: HistoryQueryParams) -> list[Signal]:
        """
        Return ALL closed signals matching the provided filters (no pagination),
        sorted per the requested sort field/order. Used for CSV export.
        """
        filters = self._build_filters(params)

        sort_column = ALLOWED_SORT_FIELDS.get(params.sort_by, Signal.closed_at)
        order_fn = desc if params.sort_order == "desc" else asc

        query = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .join(Coin, Signal.coin_id == Coin.id)
            .where(*filters)
            .order_by(order_fn(sort_column))
        )

        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def get_summary(self, params: HistoryQueryParams) -> Tuple[int, int]:
        """
        Return (total_trades, wins) for signals matching the provided filters.
        CANCELLED signals are excluded entirely from both counts.
        losses = total_trades - wins is computed by the caller.
        """
        filters = self._build_filters(params)

        total_query = (
            select(func.count(Signal.id))
            .select_from(Signal)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(*filters, Signal.status.in_(COUNTED_STATUSES))
        )
        total_trades = (await self.session.execute(total_query)).scalar_one()

        wins_query = (
            select(func.count(Signal.id))
            .select_from(Signal)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(*filters, Signal.status.in_(WIN_STATUSES))
        )
        wins = (await self.session.execute(wins_query)).scalar_one()

        return total_trades, wins
