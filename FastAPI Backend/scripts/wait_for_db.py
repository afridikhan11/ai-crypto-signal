#!/usr/bin/env python3
"""
Block until PostgreSQL actually accepts a connection, or give up loudly.

WHY THIS EXISTS
--------------------------------------------------------------------------
`depends_on: condition: service_healthy` solves the common case, but it is
a Compose feature and this image must not depend on Compose being the
thing that starts it. It does nothing for:

  * `docker run` without Compose, Kubernetes, ECS, Nomad
  * PostgreSQL restarting AFTER the app is already up (both compose files
    set `restart: unless-stopped`, so the database can bounce underneath a
    running app container)
  * a healthy container whose database or role is not yet usable

So the wait lives in the image too, and Compose's healthcheck becomes an
optimisation rather than the only line of defence.

WHY IT USES THE APPLICATION'S OWN URL AND DRIVER
--------------------------------------------------------------------------
`pg_isready` checks that a server answers on a port. That is not the same
question as "can this application connect". This connects with the exact
DATABASE_URL, driver, credentials and database name the app and Alembic
will use, and runs a real `SELECT 1`. A server that is up but has not
finished creating the role or the database fails here, correctly, instead
of passing a port check and then failing inside Alembic.

It also needs no extra packages: `python:3.12-slim` ships no
`postgresql-client`, so a `pg_isready` wait would mean installing one.

Env:
  DB_WAIT_TIMEOUT_SECONDS   total budget before giving up (default 60)
Exit:
  0  the database answered
  1  gave up, or the URL is not usable by the async driver
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# IMPORT PATH - why this is here and why it is not PYTHONPATH.
#
# `python /app/scripts/wait_for_db.py` puts THE SCRIPT'S OWN DIRECTORY on
# sys.path[0] - /app/scripts - not the working directory. WORKDIR does not
# matter; cwd is only prepended for `python -m`, `python -c` and the REPL.
# So `import app` failed with ModuleNotFoundError even though cwd was /app
# and /app/app/__init__.py existed.
#
# The entrypoint now invokes this as `python -m scripts.wait_for_db`, which
# does put /app on sys.path. This block is defence in depth for the other
# ways the script legitimately gets run - `docker exec ... python
# scripts/wait_for_db.py` while debugging, or from any other cwd. Anchoring
# on __file__ is deterministic and self-contained; PYTHONPATH would move
# the fix into the environment where it is invisible from the code and easy
# to lose when the invocation changes.
# --------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_TIMEOUT = 60.0
FIRST_DELAY = 0.5
MAX_DELAY = 5.0


def _log(message: str) -> None:
    # Unbuffered so the ordering in `docker logs` matches reality.
    print(f"[wait-for-db] {message}", flush=True)


async def _try_once(url: str) -> None:
    """One connection attempt. Raises on any failure."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    # NullPool + a fresh engine per attempt: a pooled engine can cache a
    # broken connection across attempts, which turns a recoverable startup
    # race into a permanent failure.
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def main() -> int:
    from app.core.config import get_settings

    url = get_settings().database_url

    if "+asyncpg" not in url:
        # Fail immediately rather than retrying for a minute: no amount of
        # waiting fixes a sync driver URL, and the app itself would fail
        # the same way the moment it started.
        _log(
            f"DATABASE_URL does not use the async driver this application "
            f"requires (expected 'postgresql+asyncpg://', got "
            f"'{url.split('://')[0]}://'). Not retrying."
        )
        return 1

    try:
        budget = float(os.getenv("DB_WAIT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    except ValueError:
        budget = DEFAULT_TIMEOUT

    # Never print the URL: it contains the password.
    target = url.rsplit("@", 1)[-1] if "@" in url else url
    _log(f"waiting up to {budget:.0f}s for postgres at {target}")

    started = time.monotonic()
    delay = FIRST_DELAY
    attempt = 0
    last_error = "no attempt was made"

    while time.monotonic() - started < budget:
        attempt += 1
        try:
            await _try_once(url)
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready yet"
            last_error = f"{type(exc).__name__}: {exc}"
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                break
            _log(
                f"attempt {attempt} failed ({type(exc).__name__}), retrying in "
                f"{min(delay, remaining):.1f}s ({remaining:.0f}s left)"
            )
            await asyncio.sleep(min(delay, remaining))
            # Exponential backoff, capped: fast enough that a database
            # ready in 2s does not cost 10, slow enough not to hammer a
            # server that is still running initdb.
            delay = min(delay * 2, MAX_DELAY)
        else:
            elapsed = time.monotonic() - started
            _log(f"postgres is accepting connections (after {elapsed:.1f}s, attempt {attempt})")
            return 0

    _log(f"GAVE UP after {budget:.0f}s and {attempt} attempts. Last error: {last_error}")
    _log(
        "The container will exit non-zero so the orchestrator restarts it "
        "rather than starting an application that cannot reach its database."
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
