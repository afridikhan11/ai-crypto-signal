#!/usr/bin/env bash
#
# Prove that `alembic upgrade head` builds this schema from NOTHING.
#
# WHY THIS SCRIPT EXISTS
# ---------------------------------------------------------------------
# The architecture cleanup removed all in-process schema management, so
# Alembic is now the only thing that can create this database. That claim
# is only worth anything if it has actually been executed against an empty
# PostgreSQL. This script does exactly that, and checks the result.
#
# WHAT IT DOES
# ---------------------------------------------------------------------
#   1. creates a THROWAWAY database beside the real one
#   2. runs `alembic upgrade head` against it, and nothing else
#   3. verifies tables, columns, enums, indexes, primary keys, foreign
#      keys, NOT NULL constraints, and that alembic_version == head
#   4. drops the throwaway database
#
# It never touches the application database. The only name it will ever
# drop is the one it created, and it refuses to run if that name collides
# with the database in DATABASE_URL.
#
# USAGE
#   docker compose exec backend bash scripts/verify_fresh_database.sh
#
# Exit 0 = the chain builds an empty database correctly.
set -euo pipefail

VERIFY_DB="${VERIFY_DB:-crypto_signals_alembic_verify}"

python3 - "$VERIFY_DB" <<'PYEOF'
import re
import subprocess
import sys

from sqlalchemy import create_engine, text

from app.core.config import get_settings

verify_db = sys.argv[1]
url = get_settings().database_url

# Normalise the async driver to a sync one for the admin/inspection work;
# alembic itself keeps using the app's real async URL.
sync_url = url.replace("+asyncpg", "")
m = re.match(r"^(.*/)([^/?]+)(\?.*)?$", sync_url)
if not m:
    sys.exit(f"could not parse DATABASE_URL: {sync_url}")
prefix, real_db, query = m.group(1), m.group(2), m.group(3) or ""

if verify_db == real_db:
    sys.exit(
        f"REFUSING TO RUN: the throwaway database name ({verify_db}) is the "
        f"application database. Set VERIFY_DB to something else."
    )

admin_url = f"{prefix}postgres{query}"
verify_sync_url = f"{prefix}{verify_db}{query}"
verify_async_url = verify_sync_url.replace("postgresql://", "postgresql+asyncpg://")

fail = []


def step(msg):
    print(f"\n=== {msg}")


# ---------------------------------------------------------------- 1. create
step(f"creating throwaway database {verify_db}")
admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text(f'DROP DATABASE IF EXISTS "{verify_db}"'))
    c.execute(text(f'CREATE DATABASE "{verify_db}"'))
print("  created (completely empty)")

try:
    # ------------------------------------------------------------ 2. upgrade
    step("alembic upgrade head")
    proc = subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**__import__("os").environ, "DATABASE_URL": verify_async_url},
        capture_output=True, text=True,
    )
    print(proc.stdout.strip() or "(no stdout)")
    if proc.returncode != 0:
        print(proc.stderr.strip())
        fail.append(f"alembic upgrade head failed with exit {proc.returncode}")

    # ------------------------------------------------------------ 3. verify
    eng = create_engine(verify_sync_url)
    with eng.connect() as c:
        step("tables")
        tables = {
            r[0] for r in c.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ))
        }
        for expected in ("coins", "signals", "equity_snapshots", "risk_assessments", "alembic_version"):
            ok = expected in tables
            print(f"  {'OK  ' if ok else 'MISS'} {expected}")
            if not ok:
                fail.append(f"table missing: {expected}")

        step("enum types and their values")
        enums = {}
        for name, label in c.execute(text(
            "SELECT t.typname, e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid ORDER BY t.typname, e.enumsortorder"
        )):
            enums.setdefault(name, []).append(label)
        for name, expected in (
            ("direction", {"LONG", "SHORT"}),
            ("signalstatus", {"ACTIVE", "TP_HIT", "STOPPED", "CANCELLED", "PENDING_ENTRY", "EXPIRED"}),
        ):
            got = set(enums.get(name, []))
            print(f"  {name}: {sorted(got)}")
            if missing := expected - got:
                fail.append(f"enum {name} missing values: {sorted(missing)}")

        step("NOT NULL columns that risk logic depends on")
        for table, column in (("signals", "initial_stop_loss"),
                              ("signals", "take_profit"),
                              ("equity_snapshots", "equity"),
                              ("risk_assessments", "snapshot"),
                              ("risk_assessments", "risk_engine_version"),
                              ("risk_assessments", "context_source")):
            row = c.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).first()
            if row is None:
                print(f"  MISS {table}.{column}")
                fail.append(f"column missing: {table}.{column}")
            else:
                ok = row[0] == "NO"
                print(f"  {'OK  ' if ok else 'NULL'} {table}.{column}")
                if not ok:
                    fail.append(f"{table}.{column} should be NOT NULL")

        step("indexes")
        idx = {r[0] for r in c.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ))}
        for expected in ("ix_coins_symbol", "ix_equity_snapshots_env_captured",
                         "ix_risk_assessments_symbol_created"):
            ok = expected in idx
            print(f"  {'OK  ' if ok else 'MISS'} {expected}")
            if not ok:
                fail.append(f"index missing: {expected}")

        step("primary keys and foreign keys")
        cons = list(c.execute(text(
            "SELECT tc.table_name, tc.constraint_type, tc.constraint_name "
            "FROM information_schema.table_constraints tc "
            "WHERE tc.table_schema = 'public' AND tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY') "
            "ORDER BY tc.table_name, tc.constraint_type"
        )))
        for table, ctype, name in cons:
            print(f"  {table:20s} {ctype:12s} {name}")
        pks = {t for t, ct, _ in cons if ct == "PRIMARY KEY"}
        for expected in ("coins", "signals", "equity_snapshots", "risk_assessments"):
            if expected not in pks:
                fail.append(f"no primary key on {expected}")
        fks = {(t, ct) for t, ct, _ in cons if ct == "FOREIGN KEY"}
        if ("signals", "FOREIGN KEY") not in fks:
            fail.append("signals.coin_id -> coins.id foreign key missing")
        if ("risk_assessments", "FOREIGN KEY") not in fks:
            fail.append("risk_assessments.signal_id -> signals.id foreign key missing")

        step("alembic_version")
        version = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"  stamped at: {version}")

    heads = subprocess.run(
        ["alembic", "heads"],
        env={**__import__("os").environ, "DATABASE_URL": verify_async_url},
        capture_output=True, text=True,
    ).stdout
    head_id = heads.split()[0] if heads.split() else "?"
    print(f"  chain head: {head_id}")
    if version != head_id:
        fail.append(f"alembic_version ({version}) != head ({head_id})")

finally:
    step(f"dropping throwaway database {verify_db}")
    with admin.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"
        ), {"d": verify_db})
        c.execute(text(f'DROP DATABASE IF EXISTS "{verify_db}"'))
    print("  dropped")

print("\n" + "=" * 60)
if fail:
    print("FAILED")
    for f in fail:
        print(f"  ! {f}")
    sys.exit(1)
print("PASSED: `alembic upgrade head` builds this schema from an empty database.")
PYEOF
