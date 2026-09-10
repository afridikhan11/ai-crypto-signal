#!/bin/sh
#
# Post-deploy smoke check: did the thing we just deployed actually work?
#
# WHY THIS EXISTS (2026-09-10)
# ---------------------------------------------------------------------------
# The suite passed 1159 tests. The deploy printed success. `docker compose ps`
# said `Up`. And the bot had not monitored a single signal for seven hours,
# because every ORM query was raising at mapper-configuration time. It was
# found only because a human happened to run a check later that day.
#
# A green test suite says the code is right in the shape the TESTS import it.
# This says the code is right in the shape the APP runs it - which is where
# that outage lived, and where the next one will too.
#
# Exits non-zero on failure, so it can gate a deploy instead of leaving
# someone to read logs and decide.
#
# Usage, from the FastAPI Backend directory:
#     ./scripts/smoke_check.sh            # after a deploy
#     ./scripts/smoke_check.sh 300        # allow 300s for the app to come up
set -e

WAIT_SECONDS="${1:-180}"
COMPOSE="docker compose"
FAILED=0

note()  { printf '  %s\n' "$1"; }
pass()  { printf '  PASS  %s\n' "$1"; }
fail()  { printf '  FAIL  %s\n' "$1"; FAILED=1; }

echo "=== 1. Containers running ==="
for svc in db redis app; do
    state=$($COMPOSE ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s {print $2}')
    case "$state" in
        running) pass "$svc is running" ;;
        "")      fail "$svc is not present" ;;
        *)       fail "$svc is '$state'" ;;
    esac
done

echo "=== 2. Migrations at head ==="
# `alembic current` prints the revision plus "(head)" only when nothing is
# pending. A deploy that silently skipped a migration is a deploy that will
# fail later, on a write, in production.
if $COMPOSE exec -T app alembic current 2>/dev/null | grep -q '(head)'; then
    pass "database is at the latest migration"
else
    fail "database is NOT at head - a migration did not apply"
    $COMPOSE exec -T app alembic current 2>&1 | sed 's/^/        /' || true
fi

echo "=== 3. The app answers /health ==="
# Polled, not asked once: the scanner's first sweep takes a while, and a
# check that fails during normal startup is a check people learn to ignore.
deadline=$(( $(date +%s) + WAIT_SECONDS ))
body=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    body=$($COMPOSE exec -T app python -c "
import json, urllib.request
try:
    r = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=10)
    print(json.dumps({'code': r.status, 'body': json.loads(r.read())}))
except urllib.error.HTTPError as e:
    print(json.dumps({'code': e.code, 'body': json.loads(e.read())}))
except Exception as e:
    print(json.dumps({'code': 0, 'body': {'error': str(e)}}))
" 2>/dev/null) || body=""
    case "$body" in
        *'"code": 200'*) break ;;
    esac
    sleep 10
done

case "$body" in
    *'"code": 200'*) pass "/health returned 200" ;;
    "")              fail "/health could not be reached at all" ;;
    *)               fail "/health did not become healthy within ${WAIT_SECONDS}s"
                     echo "$body" | sed 's/^/        /' ;;
esac

echo "=== 4. The ORM can actually query ==="
# The specific failure of 2026-09-10: a mapped SELECT in the app's own
# process, reached through the app's own import surface.
#
# `import app.main` on purpose, not the individual models. The first version
# of this check imported only `app.models.signal` and failed on a HEALTHY
# deployment with "expression 'Coin' failed to locate a name" - because
# `Signal.coin` needs `app.models.coin` loaded too. A probe that imports
# differently from the app tests a program nobody runs; that mismatch is the
# entire bug class this script exists to catch, so the probe must not repeat
# it. Importing the real entrypoint pulls in exactly what the app pulls in.
ORM_PROBE="
import asyncio
import app.main  # the app's real import surface
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.signal import Signal

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(select(Signal.id).limit(1))
asyncio.run(main())
"
if $COMPOSE exec -T app python -c "$ORM_PROBE" >/dev/null 2>&1; then
    pass "a mapped query succeeds"
else
    fail "the ORM cannot query - mappers or the database are broken"
    $COMPOSE exec -T app python -c "$ORM_PROBE" 2>&1 | tail -5 | sed 's/^/        /' || true
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "SMOKE CHECK PASSED - the app is running and doing its job."
else
    echo "SMOKE CHECK FAILED - do not walk away from this deploy."
fi
exit "$FAILED"
