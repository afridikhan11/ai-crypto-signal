"""initial schema: users, coins, signals

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

direction_enum = sa.Enum("LONG", "SHORT", name="direction")
status_enum = sa.Enum(
    "ACTIVE", "TP1_HIT", "TP2_HIT", "TP3_HIT", "STOPPED", "CANCELLED",
    name="signalstatus",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "coins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.Column("volume_24h", sa.Float(), nullable=True),
    )
    op.create_index("ix_coins_id", "coins", ["id"])
    op.create_index("ix_coins_symbol", "coins", ["symbol"], unique=True)

    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("coin_id", sa.Uuid(), sa.ForeignKey("coins.id"), nullable=False),
        sa.Column("direction", direction_enum, nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit_1", sa.Float(), nullable=False),
        sa.Column("take_profit_2", sa.Float(), nullable=False),
        sa.Column("take_profit_3", sa.Float(), nullable=False),
        sa.Column("risk_reward", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("status", status_enum, server_default="ACTIVE", nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeframe", sa.String(length=10), server_default="15m", nullable=False),
        sa.Column("ai_model_version", sa.String(length=20), nullable=True),
        sa.Column("session", sa.String(length=20), nullable=True),
        sa.Column("htf_bias", sa.String(length=10), nullable=True),
        sa.Column("bias_strength", sa.Float(), nullable=True),
        sa.Column("max_tp_hit", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_signals_id", "signals", ["id"])
    op.create_index("ix_signals_coin_id", "signals", ["coin_id"])
    op.create_index("ix_signals_status", "signals", ["status"])


def downgrade() -> None:
    op.drop_table("signals")
    op.drop_table("coins")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
    status_enum.drop(op.get_bind(), checkfirst=True)
    direction_enum.drop(op.get_bind(), checkfirst=True)
