from __future__ import annotations

import uuid
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import func, select, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.signal import Signal, SignalStatus, Direction
from app.models.coin import Coin
from app.schemas.signal import SignalQueryParams

ALLOWED_SORT_FIELDS = {
    "created_at": Signal.created_at,
    "updated_at": Signal.updated_at,
    "direction": Signal.direction,
    "status": Signal.status,
    "confidence": Signal.confidence,
    "risk_reward": Signal.risk_reward,
    "entry_price": Signal.entry_price,
    "timeframe": Signal.timeframe,
}


class SignalRepository:
    """Async repository for Signal CRUD operations with eager loading and safe filtering."""

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

    def _safe_signal_status(self, value: Optional[str]) -> Optional[SignalStatus]:
        if not value:
            return None
        try:
            return SignalStatus(value.upper())
        except ValueError:
            logger.warning(f"Invalid status filter ignored: {value}")
            return None

    async def get_signals(self, params: SignalQueryParams) -> Tuple[list[Signal], int]:
        """
        Returns a paginated list of signals matching the provided filters,
        along with the total number of matching records.
        Eagerly loads the coin relationship to avoid N+1 queries.
        """

        # Base query with eager loading of Coin relationship
        base_query = select(Signal).options(selectinload(Signal.coin))

        # Build filters
        filters = []
        if params.symbol:
            filters.append(Coin.symbol == params.symbol.upper())
        direction = self._safe_direction(params.direction)
        if direction is not None:
            filters.append(Signal.direction == direction)
        status = self._safe_signal_status(params.status)
        if status is not None:
            filters.append(Signal.status == status)
        if params.min_confidence is not None:
            filters.append(Signal.confidence >= params.min_confidence)
        if params.timeframe:
            filters.append(Signal.timeframe == params.timeframe)

        # Apply join only if symbol filter requires it
        if params.symbol:
            query = base_query.join(Coin, Signal.coin_id == Coin.id).where(*filters)
        else:
            query = base_query.where(*filters)

        # Count query (no eager load)
        count_query = select(func.count(Signal.id)).select_from(Signal)
        if params.symbol:
            count_query = count_query.join(Coin, Signal.coin_id == Coin.id)
        if filters:
            count_query = count_query.where(*filters)
        total = (await self.session.execute(count_query)).scalar_one()

        # Sorting – whitelist to prevent arbitrary column access
        sort_column = ALLOWED_SORT_FIELDS.get(params.sort_by, Signal.created_at)
        order_fn = desc if params.sort_order == "desc" else asc
        query = query.order_by(order_fn(sort_column))

        # Pagination
        offset = (params.page - 1) * params.page_size
        query = query.offset(offset).limit(params.page_size)

        result = await self.session.execute(query)
        signals = result.scalars().unique().all()
        return signals, total

    async def get_signal_by_id(self, signal_id: uuid.UUID) -> Optional[Signal]:
        """Fetch a single signal by its primary key, eagerly loading the coin relationship."""
        stmt = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .where(Signal.id == signal_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_signal(self) -> Optional[Signal]:
        """Return the most recently created signal, with coin relationship loaded."""
        stmt = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .order_by(desc(Signal.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()