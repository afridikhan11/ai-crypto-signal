from __future__ import annotations

import uuid
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import and_, func, or_, select, asc, desc
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
        if params.asset_class:
            filters.append(Coin.asset_class == params.asset_class.lower())
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

        # Apply join only if a Coin-column filter requires it
        needs_coin_join = bool(params.symbol or params.asset_class)
        if needs_coin_join:
            query = base_query.join(Coin, Signal.coin_id == Coin.id).where(*filters)
        else:
            query = base_query.where(*filters)

        # Count query (no eager load)
        count_query = select(func.count(Signal.id)).select_from(Signal)
        if needs_coin_join:
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

    async def get_active_signals(self, committed_only: bool = False) -> list[Signal]:
        """
        Every signal carrying real, committed risk right now - used for
        portfolio-level risk aggregation.

        Under the ICT pending-entry model this deliberately includes a
        PENDING_ENTRY signal whose LIMIT order is actually RESTING on the
        exchange (`executed=True`): that capital is committed and will
        become a position the moment price reaches the zone, so excluding it
        would let the portfolio quietly exceed its open-risk limit by
        arming more entries than the account can carry. A PENDING_ENTRY
        signal with no order placed (`executed=False`) is advisory only and
        is correctly excluded.

        `committed_only=True` restricts the result to signals whose order
        has ACTUALLY been placed on the exchange (`executed=True`).

        This distinction matters enormously and the two callers genuinely
        need different answers:

          - The ADVISORY portfolio-risk display answers "what would my
            exposure be if I took every signal on the board right now?", so
            it wants every ACTIVE signal, executed or not
            (`committed_only=False`, the default - unchanged behaviour).

          - The EXECUTION GATE must answer "what am I actually exposed to?"
            Counting un-executed suggestions there let signals the user
            never acted on consume the exposure budget and block real
            trades. Since the scanner produces signals continuously, that
            budget filled up with purely hypothetical positions.

        Note the pre-existing asymmetry this closes: PENDING_ENTRY already
        required `executed=True` (an armed LIMIT order is real committed
        capital), while ACTIVE did not - so an un-executed ACTIVE signal
        counted toward exposure but an un-executed pending one did not.
        """
        conditions = [
            or_(
                Signal.status == SignalStatus.ACTIVE,
                and_(
                    Signal.status == SignalStatus.PENDING_ENTRY,
                    Signal.executed.is_(True),
                ),
            )
        ]
        if committed_only:
            conditions.append(Signal.executed.is_(True))

        stmt = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .where(*conditions)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_closed_signals_since(self, since) -> list[Signal]:
        """
        Signals that actually RAN and were decided at-or-after `since` -
        used for daily realized-loss tracking.

        EXPIRED is excluded alongside CANCELLED: a trade that was never
        entered produced no realized profit or loss, and counting it would
        misreport the daily loss the Risk Engine gates on.
        """
        stmt = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .where(
                Signal.status.notin_(
                    (
                        SignalStatus.ACTIVE,
                        SignalStatus.PENDING_ENTRY,
                        SignalStatus.CANCELLED,
                        SignalStatus.EXPIRED,
                    )
                ),
                Signal.closed_at.is_not(None),
                Signal.closed_at >= since,
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())