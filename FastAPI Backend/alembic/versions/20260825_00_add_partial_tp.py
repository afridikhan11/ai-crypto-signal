"""Add Signal.tp1_price / Signal.tp1_done - partial take-profit.

At TP1 (entry +/- SIGNAL_TP1_PCT%) the monitor closes SIGNAL_TP1_FRACTION of
the position and moves the stop to breakeven; the remainder runs to the
signal's existing structure target. `tp1_price` stores the level (NULL =
feature off, or no partial for this trade); `tp1_done` records that the
partial fired so it cannot fire twice and stats can blend the halves.

Both additive and nullable/defaulted - existing rows and every existing
reader are unaffected.

Revision ID: 20260825_00
Revises: 20260820_00
Create Date: 2026-08-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_00"
down_revision: Union[str, None] = "20260820_00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("tp1_price", sa.Float(), nullable=True))
    op.add_column(
        "signals",
        sa.Column("tp1_done", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("signals", "tp1_done")
    op.drop_column("signals", "tp1_price")
