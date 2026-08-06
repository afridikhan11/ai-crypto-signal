"""Add Signal.initial_stop_loss - the frozen risk reference.

Separates the ORIGINAL stop (used for every risk / position-sizing /
exposure calculation) from the LIVE stop (moved by Trade Management to
breakeven and then trailed). Reading the live stop for sizing made the
computed position size depend on how far the stop had already trailed;
at breakeven the engine sets the stop exactly to entry_price, collapsing
the risk distance to zero and breaking position sizing entirely.

Revision ID: 20260730_01
Revises: 20260730_00
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_01"
down_revision: Union[str, None] = "20260730_00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Three deliberate steps. The column is NOT NULL in its final state, but
    it cannot be added as NOT NULL in one statement on a table that already
    holds rows - there would be no value for them. This is the standard
    add-nullable / backfill / enforce sequence, not a nullable shortcut:
    the column ends up NOT NULL exactly as the model declares it.
    """
    # 1. Add nullable so existing rows are accepted.
    op.add_column(
        "signals",
        sa.Column("initial_stop_loss", sa.Float(), nullable=True),
    )

    # 2. Backfill. For every pre-existing signal the only honest value for
    #    the original stop is its current stop_loss: this project has never
    #    recorded stop history, so for a trade whose stop has already been
    #    trailed the true original is unrecoverable. Using the current stop
    #    is the closest correct value available and is never a fabricated
    #    number. Newly created signals set both fields at creation, so this
    #    approximation applies only to rows that already exist today.
    op.execute(
        "UPDATE signals SET initial_stop_loss = stop_loss WHERE initial_stop_loss IS NULL"
    )

    # 3. Enforce NOT NULL, matching app/models/signal.py.
    op.alter_column("signals", "initial_stop_loss", nullable=False)


def downgrade() -> None:
    op.drop_column("signals", "initial_stop_loss")
