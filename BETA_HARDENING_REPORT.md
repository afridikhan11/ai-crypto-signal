# Beta Hardening Report (Phase 8)

Date: 2026-07-28
Scope: the 7 approved Beta Hardening items from `BETA_READINESS_REPORT.md`, and nothing else. No architecture changes, no new AI engines, no new scoring systems, no feature removal.

## 1. What was done

**1. Replaced remaining `print()` calls with the logger** — `app/api/v1/endpoints/health.py`. Both lines (database and Redis health-check failures) now go through the same `loguru` logger every other file uses, instead of bypassing it.

**2. WebSocket disconnect cleanup — already correct, no change made.** I went to fix this and found the prior audit's finding was wrong: `app/main.py`'s `/ws/signals` endpoint already calls `signal_ws_manager.disconnect(websocket)` unconditionally in its `finally` block, on every disconnect path (normal close, error, or exception). There was nothing to fix. I'm flagging this correction openly rather than making a no-op change just to check a box.

**3. Warning logs on trading-settings fallback** — `app/services/trading_settings.py`. `get_risk_percent()` now logs a `logger.warning` whenever it has to fall back to the 1% default: once for an out-of-range saved value, once for a corrupted/unreadable settings file. A missing file on first run still returns the default silently (that's expected, not an error). This affects real position sizing, so a silent revert is no longer silent.

**4. Optional date-range filtering for AI Performance analytics** — `app/services/ai_performance_service.py` and `app/api/v1/endpoints/performance.py`. `GET /performance/overview` now accepts optional `start_date`/`end_date` query params. When provided, the confidence-band, by-symbol, and by-asset-class breakdowns only include trades closed in that window — same date-filtering convention the History module already uses (`Signal.closed_at`), reused rather than reinvented. Omitting both params reproduces the exact pre-Phase-8 behavior (full history), so this is purely additive - nothing that previously worked changes. `stats` and `calibration_health` intentionally always reflect full history regardless of the filter, since calibration readiness is a lifetime concept, not a recent-window one - the response schema documents this explicitly, and echoes back whichever `date_from`/`date_to` was actually applied so a caller never has to guess.

**5. PyJWT upgraded to 2.13.0** — `requirements.txt` (was 2.9.0). I checked the official changelog for every version between those two: nothing changes the specific API surface this app uses (`jwt.encode`, `jwt.decode` with an explicit `algorithms=` list, `jwt.PyJWTError`) - all of it fixes/security patches, no breaking signature changes. 2.13.0 itself closes five real security advisories (JWK/HMAC confusion, algorithm-allowlist bypass with `PyJWK`, unsafe URI schemes in `PyJWKClient`, a JWKS-cache-clearing DoS, and a `b64=false` DoS amplifier) - none of which this app's usage pattern was exposed to (no `PyJWKClient`, no JWK-based keys), but the version bump is still the right call for defense in depth.

**6. Global FastAPI exception handler** — `app/main.py`. Added three handlers: one that re-emits FastAPI's own `{"detail": ...}` shape for `HTTPException` explicitly (so this project has one obvious code path producing that shape instead of an implicit framework default), one that does the same for validation errors (`{"detail": [...]}`, unchanged from FastAPI's default), and one new one for genuinely unexpected exceptions - previously these fell through to a bare plain-text "Internal Server Error" with no JSON body at all. Now every unhandled exception returns `{"detail": "Internal server error. This has been logged.", "error_id": "<uuid>"}` with a 500, and the same error_id is written to the server log next to the full traceback, so you (or I) can correlate a user-facing error to exactly what happened server-side. None of the 30+ existing `raise HTTPException(...)` call sites across the endpoint files needed to change - their behavior is identical to before.

**7. Startup validation warnings** — `app/main.py`, new `_run_production_readiness_checks()` function, called first thing in the startup handler. Warns (never raises, never blocks startup) on: `SECRET_KEY` still at its placeholder default, `REQUIRE_AUTH` disabled, `REQUIRE_AUTH` enabled with no admin password hash set (a broken-login config, not just a security gap), and `DATABASE_URL` still using the local dev default credentials. Every check mirrors what `.env.production.example` already tells you to change - this just makes the gap visible in the startup logs too, in case that checklist gets missed before a real deployment. Verified: local development (today's `.env`) produces exactly the expected warnings and the app still starts; a fully production-configured settings object produces zero warnings.

## 2. A mistake I made and caught

While adding the startup-checks function, my first edit put the `@app.on_event("startup")` decorator on the wrong function - it landed on the new `_run_production_readiness_checks()` instead of staying on the real `on_startup()` (the function that runs your database migrations, starts the scanner, the signal monitor, and the WebSocket listener). Had that shipped as-is, none of the actual startup sequence would have run - the app would have booted looking fine but with no scanner, no signals, no live data. I caught this during my own verification pass (traced every decorator in the file against the function it's attached to, via both a plain read and an AST-level check), fixed it, and re-verified. Flagging this so it's on the record, not hidden - the maintenance policy calls for structured explanation, not just a clean diff.

## 3. Compile check

`python3 -m py_compile` across the entire `app/` tree: **PASSED**, zero syntax errors. Re-ran after the decorator fix above to confirm.

## 4. Static analysis

Re-ran the same custom AST scanner used for the Beta Readiness audit. Result: `print_statements` count dropped from 2 to 0 (confirms fix #1, no new print() calls introduced anywhere). No new `bare_except`, `except_pass`, `mutable_default_arg`, or blocking-call findings. The `broad_except_exception`/`except_no_log_no_reraise` counts are unchanged from the audit baseline (same pre-existing, already-reviewed instances, just at shifted line numbers from the new code above them) - nothing new was introduced.

## 5. Regression testing

Same sandbox limitation as every previous phase: no real Postgres, no FastAPI/Starlette/SQLAlchemy/PyJWT/passlib installed here, so live HTTP request/response testing and live JWT round-trips against the exact pinned 2.13.0 aren't possible in this environment. Within that constraint, everything that COULD be exercised was:

- **Trading settings fallback logic**: exercised all four paths directly - no file (default, no warning), valid saved value (returned as-is, no warning), out-of-range value (falls back AND warns), corrupted JSON (falls back AND warns). All four behaved exactly as designed.
- **Date-range filter logic**: the new `_date_range_filters()` helper was exercised directly against a stub comparable column - confirmed it returns zero filters when both dates are omitted (byte-identical to pre-Phase-8 behavior), and the correct `>=`/`<=` filter(s) in the correct order when one or both dates are supplied. Full query execution against a real database was not possible in this sandbox (same disclosed limitation as Phase 7) - recommend a live check via the commands in section 6 below.
- **JWT round-trip**: `create_access_token`/`decode_access_token`/`hash_password`/`verify_password` were exercised end-to-end (encode, decode, tamper-detection, wrong-password rejection, empty-hash guard) using the sandbox's available PyJWT 2.3.0, since 2.13.0 isn't installed here. Combined with the changelog review in item 5 above (no breaking change to the API surface this app uses across that entire version range), this is disclosed as sufficient-but-not-identical verification - a live install-and-test with the real pin is still worth doing on your side.
- **Startup validation checks**: exercised the exact function body against three scenarios - fresh dev defaults (produces the 3 expected warnings, none about the password hash since auth is off), a fully production-ready settings object (zero warnings), and auth-enabled-with-no-password-hash (produces exactly that one distinct warning). All matched expectations.
- **Global exception handler**: FastAPI/Starlette aren't installed in this sandbox, so the three `@app.exception_handler(...)`-decorated functions could not be invoked live. Verified instead via full manual code review plus an AST-level check confirming every decorator in `app/main.py` is attached to the correct function (this is exactly the check that caught the mistake in section 2) - recommend exercising this live via the commands below.
- **Full Phase 6/7 regression suite**: re-ran unchanged - all prior assertions (Trading Coach, Evidence Engine, Research Engine, Conversation Context, Portfolio Intelligence, AI Performance) still pass with Phase 8's changes in place. One test-harness gap was found and fixed along the way: the harness's fake `Settings` stub was missing a few attributes (`log_level`, etc.) that `trading_settings.py` now needs because it imports the real logger - a harness fix, not an app fix.
- **Full-tree compile**: re-ran after every change, always green.

**Recommended verification on your side** (Docker, real Postgres, real dependency versions):
```
docker compose up -d --build
docker compose exec app python -m pytest
curl -s http://localhost:8000/api/v1/performance/overview?start_date=2026-01-01T00:00:00Z
curl -s http://localhost:8000/nonexistent-route-to-trigger-500-handler
```
Check that the date-filtered `/performance/overview` response's `by_symbol`/`by_asset_class` narrow correctly, that `date_from`/`date_to` echo back what you passed, and that a genuinely broken request returns the new consistent `{"detail": ..., "error_id": ...}` JSON shape instead of a plain-text error.

## 6. Architecture compliance

No new AI engine, no new scoring system, no duplicated logic, no feature removed. Every change is additive or a straightforward fix within an existing file. The date-range filtering reuses the History module's exact existing convention rather than inventing a new one. The exception handler re-emits FastAPI's own existing response shapes for known error types and only adds new behavior for the previously-unhandled case. Nothing in `app/agent/` or `app/ai/` was touched.

## 7. What's next

All 7 approved items are done, verified to the extent this sandbox allows, and disclosed honestly where it doesn't. Per your instructions, moving directly into Phase 9 (WPF Integration) now - no separate approval gate was requested between Phase 8 and Phase 9.
