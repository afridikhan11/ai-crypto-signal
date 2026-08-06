from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.signal_repository import SignalRepository
from app.schemas.signal import SignalResponse, SignalListResponse, SignalQueryParams
from app.schemas.stats import StatsResponse
from app.models.signal import Signal, SignalStatus
from app.models.coin import Coin


class SignalService:
    """Service layer for signal business logic and schema mapping."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.signal_repo = SignalRepository(session)

    @staticmethod
    def _signal_to_response(signal: Signal) -> SignalResponse:
        """Map a Signal ORM object to a SignalResponse, including the coin symbol."""
        coin_symbol = signal.coin.symbol if signal.coin else "UNKNOWN"

        return SignalResponse(
            id=signal.id,
            coin_id=signal.coin_id,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            risk_reward=signal.risk_reward,
            confidence=signal.confidence,
            reason=signal.reason,
            status=signal.status.value,
            timeframe=signal.timeframe,
            ai_model_version=signal.ai_model_version,
            session=signal.session,
            htf_bias=signal.htf_bias,
            bias_strength=signal.bias_strength,
            max_tp_hit=signal.max_tp_hit or 0,
            created_at=signal.created_at,
            updated_at=signal.updated_at,
            closed_at=signal.closed_at,
            coin_symbol=coin_symbol,
        )

    async def get_signals(self, params: SignalQueryParams) -> SignalListResponse:
        """Return paginated, filtered list of signals."""
        signals, total = await self.signal_repo.get_signals(params)

        items = [self._signal_to_response(s) for s in signals]

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

        return self._signal_to_response(signal)

    async def get_latest_signal(self) -> Optional[SignalResponse]:
        """Get the most recently created signal."""
        signal = await self.signal_repo.get_latest_signal()

        if not signal:
            return None

        return self._signal_to_response(signal)

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

        closed = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status != SignalStatus.ACTIVE
                )
            )
        ).scalar_one()

        wins = (
            await self.session.execute(
                select(func.count(Signal.id)).where(
                    Signal.status.in_(
                        [
                            SignalStatus.TP1_HIT,
                            SignalStatus.TP2_HIT,
                            SignalStatus.TP3_HIT,
                        ]
                    )
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
                    Signal.status.in_(
                        [
                            SignalStatus.TP1_HIT,
                            SignalStatus.TP2_HIT,
                            SignalStatus.TP3_HIT,
                        ]
                    )
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