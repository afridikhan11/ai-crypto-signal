"""Baseline schema - coins + signals as they stood BEFORE 20260730_01.

WHY THIS EXISTS
--------------------------------------------------------------------------
Before this revision the migration chain began at `20260730_01`, whose very
first operation is `op.add_column("signals", ...)`. That silently assumed a
`signals` table already existed - true in practice, because
`app/main.py::on_startup()` had always created it with
`Base.metadata.create_all()`. The consequence was that `alembic upgrade
head` against an EMPTY database could never work: it would fail with
"relation signals does not exist". The chain was a set of incremental
patches on a schema Alembic did not own.

This revision closes that hole by defining the pre-01 schema explicitly,
so `alembic upgrade head` can build the database from nothing.

IDEMPOTENT BY DESIGN
--------------------------------------------------------------------------
Every existing deployment ALREADY has these tables (created by
`create_all()`), and is being marked with `alembic stamp head`, so this
revision will never execute there. But a database that has the tables
without an `alembic_version` row - the exact situation this project was
just in - must not be destroyed by a re-run. Each step therefore checks
the live catalogue first and skips what is already present. That makes
this safe to run against a fresh database, a stamped database, or a
half-bootstrapped one.

FIXED 2026-07-31 - DuplicateObjectError on a fresh database
--------------------------------------------------------------------------
The first live run of this revision against an empty PostgreSQL failed:

    asyncpg.exceptions.DuplicateObjectError: type "direction" already exists
    [SQL: CREATE TYPE direction AS ENUM ('LONG', 'SHORT')]

The enum types were created explicitly here AND again by `create_table`,
because the column definitions used the generic `sa.Enum` with a
`create_type=False` that only `postgresql.ENUM` understands. See the ENUM
TYPES block below for the full explanation. Both types are now created in
exactly one place, `_create_enum_types()`.

Revision ID: 20260730_00
Revises:
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_00"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The enum as it stood BEFORE the ICT pending-entry work. PENDING_ENTRY and
# EXPIRED are added further down, mirroring `app/main.py`'s own
# `ALTER TYPE ... ADD VALUE IF NOT EXISTS` bootstrap.
_BASE_STATUSES = ("ACTIVE", "TP_HIT", "STOPPED", "CANCELLED")
_DIRECTIONS = ("LONG", "SHORT")


# ==========================================================================
# ENUM TYPES - CREATED EXACTLY ONCE, BY THE `_create_enum_types()` CALL.
#
# These MUST be `postgresql.ENUM`, not the generic `sa.Enum`, and they MUST
# carry `create_type=False`.
#
# `create_type` is a PostgreSQL-dialect keyword. The generic `sa.Enum`
# accepts arbitrary keywords and forwards them to `SchemaType.__init__`,
# which discards the ones it does not recognise - so `sa.Enum(...,
# create_type=False)` is accepted WITHOUT ERROR and then IGNORED. The type
# keeps its default behaviour of emitting `CREATE TYPE` immediately before
# the `CREATE TABLE` that references it. Combined with the explicit
# creation below, that emitted `CREATE TYPE direction ...` twice and failed
# the whole migration on a fresh database with:
#
#     asyncpg.exceptions.DuplicateObjectError: type "direction" already exists
#
# `postgresql.ENUM` is the only class that actually honours the flag.
# Column types therefore reference these objects; nothing else creates a
# type. See tests/test_schema_ownership.py, which now fails if a generic
# `sa.Enum` reappears inside a `create_table`.
# ==========================================================================
signal_status_enum = postgresql.ENUM(*_BASE_STATUSES, name="signalstatus", create_type=False)
direction_enum = postgresql.ENUM(*_DIRECTIONS, name="direction", create_type=False)


# ==========================================================================
# NULLABILITY - STATE IT EXPLICITLY ON EVERY COLUMN.
#
# `sa.Column(...)` defaults to nullable=TRUE.
# `Mapped[X]` (not Optional) in the models is nullable=FALSE.
#
# The two defaults are OPPOSITE, so a column transcribed from a model
# without an explicit `nullable=` silently inverts its constraint. That is
# how `alembic check` came to report twelve `modify_nullable` operations
# against a database this migration had just built: created_at, updated_at,
# coins.is_active, signals.reason, signals.status and signals.timeframe
# were all NOT NULL in the model and NULL here.
#
# A Python-side `default=` in a model does NOT make a column nullable - it
# means the ORM supplies the value. Every column below therefore says what
# it means, including where it agrees with the sa.Column default.
# ==========================================================================


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _create_enum_types(bind) -> None:
    """Create both enum types, exactly once, before anything references them.

    `checkfirst=True` queries `pg_type` first, so this is a no-op on a
    database that already has them - an existing deployment, or a re-run
    after a partial failure. The objects created here are DISTINCT from the
    `create_type=False` ones used by the columns: an explicit `.create()`
    is unconditional, while `create_type=False` suppresses only the
    automatic emission that `CREATE TABLE` would otherwise trigger. Keeping
    them separate means neither behaviour depends on the other.
    """
    postgresql.ENUM(*_BASE_STATUSES, name="signalstatus").create(bind, checkfirst=True)
    postgresql.ENUM(*_DIRECTIONS, name="direction").create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()

    # Fail fast and legibly if the installed SQLAlchemy does not honour
    # `create_type`. Without this, an unhonoured flag surfaces as a bare
    # `DuplicateObjectError: type "direction" already exists` several
    # statements later, which says nothing about the actual cause.
    for enum_type in (direction_enum, signal_status_enum):
        if getattr(enum_type, "create_type", None) is not False:
            raise RuntimeError(
                f"{type(enum_type).__module__}.{type(enum_type).__name__} did not "
                f"retain create_type=False for '{enum_type.name}'. CREATE TYPE would "
                f"be emitted twice and this migration would fail. Use "
                f"sqlalchemy.dialects.postgresql.ENUM, not sqlalchemy.Enum."
            )

    # ---- enums -------------------------------------------------------
    # THE ONLY PLACE EITHER TYPE IS CREATED. The column definitions below
    # reference `direction_enum` / `signal_status_enum`, which carry
    # create_type=False and therefore emit no DDL of their own.
    _create_enum_types(bind)

    # ---- coins -------------------------------------------------------
    if not _has_table("coins"):
        op.create_table(
            "coins",
            sa.Column("id", sa.Uuid(), primary_key=True),
            # nullable=False is NOT redundant. `sa.Column` defaults to
            # nullable=True, while the model's `Mapped[datetime]` (not
            # Optional) is NOT NULL - see the NULLABILITY note at the top.
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            # NOT `unique=True`: that would emit a UNIQUE CONSTRAINT in
            # addition to the unique INDEX created below, leaving a fresh
            # database with two unique indexes on this column where the
            # model (`mapped_column(String(20), unique=True, index=True)`)
            # produces exactly one.
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=True),
            # Model: `Mapped[bool] = mapped_column(Boolean, default=True)`.
            # `default=` is a PYTHON-side default, which does not make the
            # column nullable - `Mapped[bool]` is NOT NULL.
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("asset_class", sa.String(length=20), nullable=False,
                      server_default="crypto"),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("market_cap", sa.Float(), nullable=True),
            sa.Column("volume_24h", sa.Float(), nullable=True),
        )
        op.create_index("ix_coins_id", "coins", ["id"])
        op.create_index("ix_coins_symbol", "coins", ["symbol"], unique=True)

    # ---- signals -----------------------------------------------------
    if not _has_table("signals"):
        op.create_table(
            "signals",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("coin_id", sa.Uuid(), sa.ForeignKey("coins.id"), nullable=False),
            sa.Column("direction", direction_enum, nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("stop_loss", sa.Float(), nullable=False),
            sa.Column("take_profit", sa.Float(), nullable=False),
            sa.Column("risk_reward", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Integer(), nullable=False),
            # Model: `Mapped[str] = mapped_column(String(500))` - NOT NULL
            # with no default at all, so every INSERT must supply it. That
            # is already true of the live database (create_all built it
            # from this same model), so this is not a new constraint.
            sa.Column("reason", sa.String(length=500), nullable=False),
            # Model: `Mapped[SignalStatus] = mapped_column(..., default=ACTIVE)`
            # - NOT NULL, filled by the ORM. The server_default matches
            # what the legacy bootstrap's TP-collapse block set.
            sa.Column("status", signal_status_enum,
                      server_default="ACTIVE", nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            # Model: `Mapped[str] = mapped_column(String(10), default="1m")`
            # - NOT NULL, Python-side default.
            sa.Column("timeframe", sa.String(length=10),
                      server_default="1m", nullable=False),
            sa.Column("ai_model_version", sa.String(length=20), nullable=True),
            sa.Column("score_breakdown", sa.JSON(), nullable=True),
            # Auto Trading execution tracking.
            sa.Column("executed", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("executed_order_id", sa.String(length=50), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_environment", sa.String(length=10), nullable=True),
            # ICT Pending Limit Entry.
            sa.Column("entry_type", sa.String(length=20), nullable=True),
            sa.Column("entry_zone_top", sa.Float(), nullable=True),
            sa.Column("entry_zone_bottom", sa.Float(), nullable=True),
            sa.Column("entry_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_fill_price", sa.Float(), nullable=True),
            sa.Column("entry_order_id", sa.String(length=50), nullable=True),
        )
        op.create_index("ix_signals_id", "signals", ["id"])
    else:
        # Table pre-exists (created by the legacy create_all() bootstrap).
        # Add only what is genuinely missing - never re-add, never drop.
        for column, ddl in (
            ("score_breakdown", sa.Column("score_breakdown", sa.JSON(), nullable=True)),
            ("executed", sa.Column("executed", sa.Boolean(), nullable=False,
                                   server_default="false")),
            ("executed_order_id", sa.Column("executed_order_id", sa.String(length=50), nullable=True)),
            ("executed_at", sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True)),
            ("executed_environment", sa.Column("executed_environment", sa.String(length=10), nullable=True)),
            ("entry_type", sa.Column("entry_type", sa.String(length=20), nullable=True)),
            ("entry_zone_top", sa.Column("entry_zone_top", sa.Float(), nullable=True)),
            ("entry_zone_bottom", sa.Column("entry_zone_bottom", sa.Float(), nullable=True)),
            ("entry_expires_at", sa.Column("entry_expires_at", sa.DateTime(timezone=True), nullable=True)),
            ("filled_at", sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True)),
            ("actual_fill_price", sa.Column("actual_fill_price", sa.Float(), nullable=True)),
            ("entry_order_id", sa.Column("entry_order_id", sa.String(length=50), nullable=True)),
        ):
            if not _has_column("signals", column):
                op.add_column("signals", ddl)

    # ---- enum values added after the base four ------------------------
    # AUTOCOMMIT: PostgreSQL will not let a value added by
    # `ALTER TYPE ... ADD VALUE` be USED in the transaction that added it.
    # Running these on an autocommit connection keeps the rest of this
    # migration transactional while still allowing later revisions (and the
    # application) to use the new values immediately.
    with op.get_context().autocommit_block():
        for value in ("PENDING_ENTRY", "EXPIRED"):
            op.execute(f"ALTER TYPE signalstatus ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """Drops the base schema.

    DESTRUCTIVE and effectively irreversible for trading history. Provided
    for completeness and for disposable test databases; on anything real,
    restore from backup instead. PostgreSQL cannot remove a value from an
    enum, so PENDING_ENTRY/EXPIRED are left in place - harmless once the
    tables using them are gone.
    """
    op.drop_table("signals")
    op.drop_table("coins")
    bind = op.get_bind()
    # postgresql.ENUM for the same reason as upgrade(): it is the class
    # that actually understands PostgreSQL enum types.
    postgresql.ENUM(name="signalstatus").drop(bind, checkfirst=True)
    postgresql.ENUM(name="direction").drop(bind, checkfirst=True)
