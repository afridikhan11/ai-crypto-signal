#!/bin/sh
#
# Container entrypoint: wait for PostgreSQL, migrate, then serve.
#
# POSIX sh only (the base image's /bin/sh is dash, not bash).
#
# ORDER MATTERS AND IS NOT NEGOTIABLE
#   1. wait   - Alembic must not be the thing that discovers the database
#               is not up yet. It reports that as a connection traceback,
#               which reads like a migration failure and is not one.
#   2. migrate- Alembic is the single owner of the schema (see alembic.ini).
#               The application performs no DDL, so if this step is skipped
#               the app serves against whatever schema happens to exist.
#   3. exec   - replaces this shell with the server process, so the server
#               becomes PID 1 and receives SIGTERM directly. Without exec,
#               `docker stop` signals this script instead, dash does not
#               forward it, and the container is SIGKILLed after the
#               10-second grace period - cutting off graceful shutdown
#               (app/main.py's on_shutdown cancels the scanner task and
#               disposes the engine).
#
# `set -e` makes any failure abort before the server starts, so the
# container exits non-zero and the orchestrator restarts it rather than
# serving traffic against an unmigrated database.
set -e

# `-m`, not a file path. `python /app/scripts/wait_for_db.py` puts
# /app/scripts on sys.path[0], NOT the working directory, so `import app`
# fails with ModuleNotFoundError regardless of WORKDIR. `python -m` puts
# the current directory on sys.path, which is why `alembic` (via
# `prepend_sys_path = .` in alembic.ini) and `uvicorn` (via --app-dir,
# default ".") have always worked here and this script did not.
#
# This relies on cwd being the project root, which WORKDIR /app sets.
echo "[entrypoint] waiting for the database..."
python -m scripts.wait_for_db

echo "[entrypoint] running migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
