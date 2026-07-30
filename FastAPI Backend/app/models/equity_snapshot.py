"""
Equity time series - the high-water mark Drawdown needs (Phase 4, 2026-07-30).

WHY THIS TABLE EXISTS
--------------------------------------------------------------------------
Drawdown is `(peak_equity - equity) / peak_equity`, and this project had
no equity history at all. `signal_service` previously RECONSTRUCTED a peak
as "balance before today's realized PnL, when today was negative" - an
honest lower bound, explicitly documented as such, but blind to any peak
older than today. With no real series the metric could only ever report
UNKNOWN (see app/risk/metrics/drawdown.py, which refuses to report 0%
rather than claim a drawdown it cannot measure).

This is a genuine time series, not a derived guess: one row per monitor
poll (30s), each recording the equity actually observed at that moment.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EquitySnapshot(Base, TimestampMixin):
    __tablename__ = "equity_snapshots"

    # When the equity was OBSERVED (not when the row was written).
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # EQUITY = margin_balance (wallet + unrealized PnL) - the same
    # denominator every risk ratio uses, so the peak is comparable to the
    # live value it is measured against.
    equity: Mapped[float] = mapped_column(Float, nullable=False)

    wallet_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_maintenance_margin: Mapped[float | None] = mapped_column(Float, nullable=True)

    # testnet | mainnet. A testnet equity curve must never be mistaken for
    # a real one, and the peak must never be drawn across both.
    environment: Mapped[str | None] = mapped_column(String(10), nullable=True)


Index("ix_equity_snapshots_env_captured", EquitySnapshot.environment, EquitySnapshot.captured_at)
