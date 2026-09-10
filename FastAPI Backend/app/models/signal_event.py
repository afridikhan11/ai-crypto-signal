"""
One row per trade-management decision, with the price it was taken at.

WHY (2026-09-10): the first clean measurement era showed 60% of executed
trades ending in a structure-failure exit, and we could not say whether that
rule was saving money or throwing it away - the monitor logged a line and
moved on. A log line is not data. This table is: every breakeven move, TP1
partial, trailing step and structure-failure close is written here with the
live price and the stop before/after, so `signal_stats.py` can give each
rule its own ledger instead of one blended number.

Append-only. Nothing reads it on the trading path; it exists for analysis.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SignalEvent(Base, TimestampMixin):
    __tablename__ = "signal_events"

    signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ManagementActionType.value, or one of the terminal outcomes
    # ("tp_hit" / "stopped" / "reconciled" / "unprotected_close").
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Live price at the moment the decision was taken.
    price: Mapped[float] = mapped_column(Float, nullable=False)
    # For stop moves: the stop as it stood, and where it went. NULL otherwise.
    stop_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
