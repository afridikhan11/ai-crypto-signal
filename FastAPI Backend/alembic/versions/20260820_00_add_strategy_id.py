"""Add Signal.strategy_id and Signal.rules_fired - Smart AI attribution.

The Smart AI module runs multiple strategies behind one interface, and its
entire value is being able to attribute every signal / fill / PnL to the
strategy that produced it. `strategy_id` carries that tag; `rules_fired` stores
the per-condition pass/fail log ("which rules fired") the UI surfaces.

Both are additive and nullable: legacy signals (the production ICT pipeline
that predates this module) simply carry strategy_id='legacy' and a NULL
rules_fired, so nothing existing breaks.

Revision ID: 20260820_00
Revises: 20260730_02
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_00"
down_revision: Union[str, None] = "20260730_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # strategy_id stays nullable in its final state (a signal may legitimately
    # predate the concept), but existing rows are backfilled to 'legacy' so
    # per-strategy stats never confuse a pre-Smart-AI signal with a new one.
    op.add_column("signals", sa.Column("strategy_id", sa.String(length=40), nullable=True))
    op.add_column("signals", sa.Column("rules_fired", sa.JSON(), nullable=True))
    op.execute("UPDATE signals SET strategy_id = 'legacy' WHERE strategy_id IS NULL")
    op.create_index("ix_signals_strategy_id", "signals", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_signals_strategy_id", table_name="signals")
    op.drop_column("signals", "rules_fired")
    op.drop_column("signals", "strategy_id")
