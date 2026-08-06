"""
QUARANTINED LEGACY SCHEMA BOOTSTRAP - DISABLED IN PRODUCTION.

======================================================================
ALEMBIC IS THE SINGLE OWNER OF THE DATABASE SCHEMA.
Nothing in this module runs unless DB_AUTO_BOOTSTRAP=true.
======================================================================

WHAT THIS IS
----------------------------------------------------------------------
Every statement below was moved VERBATIM out of `app/main.py::on_startup()`
on 2026-07-31. Before that date this code was the project's real schema
manager: it called `Base.metadata.create_all()` and then applied ~15
hand-written `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements, a
destructive TP1/TP2/TP3 collapse, and `ALTER TYPE ... ADD VALUE`
statements - on every single application start.

WHY IT WAS QUARANTINED
----------------------------------------------------------------------
Two owners of one schema is precisely how this project's `alembic_version`
table came to be missing. The application built and patched its own schema
at startup, so `alembic upgrade` was never invoked; Alembic therefore never
had an opportunity to create its version table, and the migration history
silently diverged from the live database. The schema was correct but
UNTRACKED - nothing recorded which migrations it corresponded to.

WHY IT WAS NOT DELETED
----------------------------------------------------------------------
It is the only executable record of how the pre-Alembic schema was
actually built, including the one-time TP1/2/3 collapse that no migration
reproduces. Deleting it would destroy that history. It is kept intact,
unreachable, and clearly labelled instead.

THE ONLY LEGITIMATE USE
----------------------------------------------------------------------
A throwaway local database that has no Alembic and never will. For every
other database - including local development inside docker-compose - run
`alembic upgrade head`, which the container command already does before
uvicorn starts.

DANGER
----------------------------------------------------------------------
Enabling DB_AUTO_BOOTSTRAP against a database Alembic manages re-creates
the original divergence. `create_all()` will silently create any table a
migration has not yet added, so a subsequent `alembic upgrade head` then
fails on an object it believes it still has to create. There is no
warning; the failure surfaces later, during a deployment.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.database import engine
from app.core.logging import logger
from app.models.base import Base


class LegacyBootstrapDisabled(RuntimeError):
    """Raised when the quarantined bootstrap is invoked while disabled.

    This is a hard failure rather than a silent no-op on purpose. A caller
    that reaches this point believes it is managing the schema; letting it
    proceed silently would leave it with an unfounded belief that the
    schema had been prepared.
    """


async def run_legacy_schema_bootstrap(*, force: bool = False) -> None:
    """Apply the pre-Alembic in-process schema bootstrap.

    Refuses to run unless `DB_AUTO_BOOTSTRAP=true`. `force=True` exists
    only for tests that need to prove the guard is the thing stopping
    execution, and deliberately still requires an explicit keyword.

    The body below is unchanged from its `app/main.py` original, including
    its comments, so it remains an accurate record of what the legacy
    bootstrap actually did.
    """
    if not force and not get_settings().db_auto_bootstrap:
        raise LegacyBootstrapDisabled(
            "The legacy in-process schema bootstrap is disabled. Alembic "
            "owns this schema - run `alembic upgrade head` instead. Set "
            "DB_AUTO_BOOTSTRAP=true only for a throwaway local database "
            "that Alembic does not manage."
        )

    logger.warning(
        "LEGACY SCHEMA BOOTSTRAP RUNNING - this process is creating and "
        "altering schema directly, BYPASSING Alembic. The resulting "
        "database will not be tracked by any migration revision."
    )

    from app.models import equity_snapshot as _equity_snapshot  # noqa: F401
    from app.models import risk_assessment as _risk_assessment  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all() only creates missing tables, it never ALTERs an
        # existing one - this project has no Alembic migrations set up, so
        # new columns on existing tables need this lightweight, idempotent
        # bootstrap instead. Safe to run on every startup.
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS score_breakdown JSON"
            ))
        except Exception as e:
            logger.warning(f"score_breakdown column bootstrap skipped: {e}")

        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE coins ADD COLUMN IF NOT EXISTS asset_class VARCHAR(20) NOT NULL DEFAULT 'crypto'"
            ))
        except Exception as e:
            logger.warning(f"asset_class column bootstrap skipped: {e}")

        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN NOT NULL DEFAULT false"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_order_id VARCHAR(50)"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_environment VARCHAR(10)"
            ))
        except Exception as e:
            logger.warning(f"Auto Trading execution columns bootstrap skipped: {e}")

        # One-time collapse of TP1/TP2/TP3 -> single take_profit, and the
        # TP1_HIT/TP2_HIT/TP3_HIT status values -> single TP_HIT (see
        # app/models/signal.py). Guarded on take_profit_2 still existing so
        # this whole block only ever runs once, even though this dev
        # instance restarts on every code reload.
        try:
            from sqlalchemy import text
            still_has_old_columns = (await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'signals' AND column_name = 'take_profit_2'"
            ))).first()

            if still_has_old_columns is not None:
                await conn.execute(text("ALTER TABLE signals ADD COLUMN IF NOT EXISTS take_profit DOUBLE PRECISION"))
                await conn.execute(text(
                    "UPDATE signals SET take_profit = take_profit_1 WHERE take_profit IS NULL"
                ))
                await conn.execute(text(
                    "UPDATE signals SET status = 'TP1_HIT' WHERE status::text IN ('TP2_HIT', 'TP3_HIT')"
                ))
                await conn.execute(text("ALTER TYPE signalstatus RENAME TO signalstatus_old"))
                await conn.execute(text("CREATE TYPE signalstatus AS ENUM ('ACTIVE', 'TP_HIT', 'STOPPED', 'CANCELLED')"))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN status DROP DEFAULT"))
                await conn.execute(text(
                    "ALTER TABLE signals ALTER COLUMN status TYPE signalstatus USING "
                    "(CASE status::text WHEN 'TP1_HIT' THEN 'TP_HIT' ELSE status::text END)::signalstatus"
                ))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN status SET DEFAULT 'ACTIVE'"))
                await conn.execute(text("DROP TYPE signalstatus_old"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_1"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_2"))
                await conn.execute(text("ALTER TABLE signals DROP COLUMN take_profit_3"))
                await conn.execute(text("ALTER TABLE signals ALTER COLUMN take_profit SET NOT NULL"))
                logger.info("Migrated signals table: TP1/2/3 -> single take_profit, TPx_HIT -> TP_HIT.")
        except Exception as e:
            logger.warning(f"TP1/2/3 collapse migration skipped/failed: {e}")

        # ---- ICT Pending Limit Entry (2026-07-30) - new columns ----
        try:
            from sqlalchemy import text
            for ddl in (
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_type VARCHAR(20)",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_zone_top DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_zone_bottom DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_expires_at TIMESTAMPTZ",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS actual_fill_price DOUBLE PRECISION",
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_order_id VARCHAR(50)",
            ):
                await conn.execute(text(ddl))
        except Exception as e:
            logger.warning(f"ICT pending-entry columns bootstrap skipped: {e}")

        # ---- initial_stop_loss (2026-07-30) ----
        # This column is owned by the Alembic migration
        # alembic/versions/20260730_01_add_initial_stop_loss.py, which is the
        # authoritative definition (add nullable -> backfill -> NOT NULL).
        # The guarded statements below exist ONLY so a database that has not
        # yet had `alembic upgrade head` run against it still boots with a
        # usable column instead of failing every query with "column does not
        # exist". They are byte-for-byte equivalent to the migration's own
        # steps and are idempotent, so running the migration afterwards is a
        # no-op rather than a conflict.
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE signals ADD COLUMN IF NOT EXISTS initial_stop_loss DOUBLE PRECISION"
            ))
            await conn.execute(text(
                "UPDATE signals SET initial_stop_loss = stop_loss WHERE initial_stop_loss IS NULL"
            ))
            await conn.execute(text(
                "ALTER TABLE signals ALTER COLUMN initial_stop_loss SET NOT NULL"
            ))
        except Exception as e:
            logger.warning(f"initial_stop_loss bootstrap skipped: {e}")

    # ---- ICT Pending Limit Entry - new ENUM VALUES ----
    # Deliberately OUTSIDE the transaction block above. `create_all()` never
    # alters an existing type, and Postgres will not let a value added by
    # `ALTER TYPE ... ADD VALUE` be USED in the same transaction that added
    # it. Running each ADD VALUE in its own auto-committed connection (and
    # before anything inserts a row carrying the new value) is what makes
    # this safe and idempotent across restarts. IF NOT EXISTS requires
    # PostgreSQL 12+, which this project's docker-compose Postgres image
    # satisfies; the try/except keeps a startup on any other backend
    # (e.g. SQLite in a test harness, which has no enum types at all) from
    # failing here.
    for enum_value in ("PENDING_ENTRY", "EXPIRED"):
        try:
            from sqlalchemy import text
            async with engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(
                    f"ALTER TYPE signalstatus ADD VALUE IF NOT EXISTS '{enum_value}'"
                ))
            logger.info(f"signalstatus enum value '{enum_value}' present.")
        except Exception as e:
            logger.warning(f"signalstatus enum bootstrap for '{enum_value}' skipped: {e}")
