"""Record where and why a trade ended, and every management decision.

Signal.exit_price / Signal.exit_reason: the level and cause of the terminal
transition. A CANCELLED structure-failure exit previously stored nothing, so
the most common outcome of the first clean measurement era (60% of executed
trades) had no P/L at all.

signal_events: append-only, one row per trade-management decision
(breakeven, TP1 partial, trailing step, structure-failure close, terminal
outcome) with the live price and the stop before/after - so each rule can be
judged on its own ledger.

Both additive; existing rows and readers are unaffected.

Revision ID: 20260910_00
Revises: 20260825_00
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_00"
down_revision: Union[str, None] = "20260825_00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("exit_price", sa.Float(), nullable=True))
    op.add_column("signals", sa.Column("exit_reason", sa.String(length=160), nullable=True))

    op.create_table(
        "signal_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "signal_id", sa.Uuid(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("stop_before", sa.Float(), nullable=True),
        sa.Column("stop_after", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_signal_events_id", "signal_events", ["id"])
    op.create_index("ix_signal_events_signal_id", "signal_events", ["signal_id"])
    op.create_index("ix_signal_events_event_type", "signal_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_signal_events_event_type", table_name="signal_events")
    op.drop_index("ix_signal_events_signal_id", table_name="signal_events")
    op.drop_index("ix_signal_events_id", table_name="signal_events")
    op.drop_table("signal_events")
    op.drop_column("signals", "exit_reason")
    op.drop_column("signals", "exit_price")
