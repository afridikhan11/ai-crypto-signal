"""Add equity_snapshots and risk_assessments (Risk Engine 2.0.0, Phase 4).

equity_snapshots gives Drawdown a real high-water mark - without it the
metric can only report UNKNOWN, since this project had no equity history
at all and the previous reconstruction could not see past today.

risk_assessments is the audit trail: the complete metric snapshot for
every decision, approved or rejected, with the engine version and the
provenance of the inputs, written once and never recomputed.

Revision ID: 20260730_02
Revises: 20260730_01
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_02"
down_revision: Union[str, None] = "20260730_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # nullable=False: `sa.Column` defaults to nullable=True, but the
        # model's TimestampMixin declares `Mapped[datetime]` (not Optional),
        # which is NOT NULL. See the NULLABILITY note in 20260730_00.
        #
        # server_default on updated_at is REQUIRED, not cosmetic. The model
        # gives it `server_default=func.now()`, so SQLAlchemy OMITS the
        # column from every INSERT and expects the database to fill it.
        # NOT NULL with no database default would therefore make every
        # equity-snapshot insert fail with a NotNullViolation.
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("wallet_balance", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("total_maintenance_margin", sa.Float(), nullable=True),
        sa.Column("environment", sa.String(length=10), nullable=True),
    )
    op.create_index("ix_equity_snapshots_id", "equity_snapshots", ["id"])
    op.create_index("ix_equity_snapshots_captured_at", "equity_snapshots", ["captured_at"])
    # Peak lookup is `ORDER BY equity DESC LIMIT 1` scoped by environment -
    # this composite serves it directly.
    op.create_index(
        "ix_equity_snapshots_env_captured", "equity_snapshots", ["environment", "captured_at"]
    )

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # Same reasoning as equity_snapshots above. This one matters more:
        # a NotNullViolation here would fail every risk-decision audit
        # write - and those are deliberately best-effort (see
        # app/services/risk_audit.py), so the failure would be swallowed
        # and the audit trail would silently be empty.
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), sa.ForeignKey("signals.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("risk_engine_version", sa.String(length=20), nullable=False),
        sa.Column("context_source", sa.String(length=30), nullable=False),
        sa.Column("exchange_snapshot_timestamp", sa.DateTime(timezone=True), nullable=True),
        # JSON rather than JSONB: this project's other structured column
        # (signals.score_breakdown) is JSON, and the snapshot is written
        # once and read whole - there is no querying INTO it that would
        # justify diverging from the established convention here.
        sa.Column("snapshot", sa.JSON(), nullable=False),
    )
    op.create_index("ix_risk_assessments_id", "risk_assessments", ["id"])
    op.create_index("ix_risk_assessments_signal_id", "risk_assessments", ["signal_id"])
    op.create_index("ix_risk_assessments_symbol", "risk_assessments", ["symbol"])
    op.create_index("ix_risk_assessments_approved", "risk_assessments", ["approved"])
    op.create_index("ix_risk_assessments_symbol_created", "risk_assessments",
                    ["symbol", "created_at"])


def downgrade() -> None:
    op.drop_table("risk_assessments")
    op.drop_table("equity_snapshots")
