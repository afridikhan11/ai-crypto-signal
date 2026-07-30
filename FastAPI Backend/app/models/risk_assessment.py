"""
Risk decision audit trail (Phase 4, 2026-07-30) - Mandatory Addition #2.

EVERY risk assessment, approved or rejected, is persisted here with the
EXACT metric values used to reach it. The snapshot is written once and
never recomputed: recomputing later would use a different account state
and possibly different rules, which is precisely what an audit trail
exists to prevent.

`risk_engine_version` (Mandatory Addition #1) is stored on every row so a
historical decision can always be re-read against the rules that actually
produced it, however much the engine changes afterwards.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RiskAssessmentRecord(Base, TimestampMixin):
    __tablename__ = "risk_assessments"

    # Nullable: an assessment can be made for a signal that is later
    # deleted, and a rejected assessment is still worth keeping even if
    # nothing downstream ever references it.
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)

    # Mandatory Addition #1 - the rules that produced this verdict.
    risk_engine_version: Mapped[str] = mapped_column(String(20), nullable=False)

    # PROVENANCE. `context_source` records WHERE the inputs came from:
    #   "binance_exchange"   - a real futures snapshot backed this decision
    #   "database_fallback"  - no exchange snapshot; margin/leverage/
    #                          liquidation metrics were UNKNOWN
    # Without this, a historical decision cannot be interpreted: the same
    # verdict means very different things depending on whether the engine
    # could actually see the account.
    context_source: Mapped[str] = mapped_column(String(30), nullable=False)

    # When the exchange snapshot behind this decision was FETCHED. Null for
    # a database-fallback decision. Distinct from created_at, which is when
    # the decision was made - the gap between them is the staleness of the
    # data the decision rested on.
    exchange_snapshot_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Mandatory Addition #2 - the complete metric snapshot, verbatim.
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


Index("ix_risk_assessments_symbol_created", RiskAssessmentRecord.symbol,
      RiskAssessmentRecord.created_at)
