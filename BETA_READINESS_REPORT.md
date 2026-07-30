# Beta Readiness Report

Date: 2026-07-28
Scope: full-project audit per your Beta Stabilization instructions — architecture review, hidden bugs, performance bottlenecks, exception handling, async usage, database queries, memory usage, API consistency, UI consistency, logging, security, dependency health. Audit-only: no code was modified during this phase, as instructed.

Method: `python3 -m py_compile` across the full backend tree, a custom AST-based static scanner (pyflakes/bandit could not be installed — no network access in this sandbox, so a purpose-built scanner was written instead), targeted manual review of flagged files, and a manual trace of the WPF frontend's actual navigation code (`MainWindow.xaml.cs`) to determine what's really reachable by you as a user versus what's dead code.

## 1. Blockers before Beta

**None found.** Nothing in this audit prevents a beta release. Everything below is either a Should-Fix (recommended before wider use, none are urgent) or a Nice-to-have.

## 2. Should-Fix (recommended before Beta, not urgent)

1. **Two `print()` calls instead of proper logging** — `app/api/v1/endpoints/health.py` lines 21 and 28. Cosmetic/consistency issue: every other file uses `loguru`'s `logger`; these two lines bypass it, so they won't show up wherever you're centralizing logs.

2. **WebSocket signal broadcaster doesn't clean up disconnected clients immediately** — `app/main.py`, the `/ws/signals` endpoint. When a client disconnects, it isn't removed from the connection list right away; it's only pruned the next time a signal broadcast fails to reach it. Self-healing (it does clear itself out), but stale entries can pile up during quiet periods between signals. Low risk, easy fix: call the manager's `disconnect()` in the endpoint's cleanup block.

3. **Silent fallback on risk-percent setting** — `app/services/trading_settings.py` (`get_risk_percent`). If your saved risk-percent setting can't be read, it silently falls back to a default (1%) with no log message. This feeds directly into real order sizing (`POST /trading/execute`). It fails safe (defaults low, not high), but if you'd configured a different risk % and it silently reverted, you'd have no way to notice. Recommend adding a log warning so this is visible if it ever happens.

4. **Analytics queries with no upper bound** — `app/services/ai_performance_service.py` (win-rate-by-confidence, by-symbol, by-asset-class, calibration health). These currently pull every closed trade in your history on every dashboard load. Fine at today's data volume; will slow down as your trade history grows over months/years. Recommend adding a date-range cap once history grows large.

5. **PyJWT version is behind two published fixes** — `pyjwt==2.9.0` in `requirements.txt`. Two issues (an HMAC/JWK confusion issue and a header-validation gap) were fixed in later 2.x releases. This library signs your own login tokens (`app/core/security.py`). Recommend bumping to `pyjwt>=2.13.0` — this is a version-number change in `requirements.txt`, not a code rewrite, but per your standing rule I'm not making any change without your explicit approval.

6. **No formal database migration tool in use** — `alembic` is listed as a dependency but isn't actually wired up (no `alembic.ini`, no migration folder). Schema changes instead happen through one-time, idempotent "add this column if it's missing" statements at startup (`app/main.py`). This has worked fine so far and isn't broken, but it has no rollback path and gets harder to manage the more the schema grows. Worth planning a proper migration setup before the database schema changes much further — not urgent for beta.

7. **No global error handler for unexpected exceptions** — most endpoints explicitly return clean error responses, but if something truly unexpected happens outside those explicit checks, FastAPI's generic default error page shows instead of your app's normal error format. Cosmetic/consistency, not a functional risk.

## 3. Nice-to-have (safe to leave for a later pass)

- `signal_service.get_stats()` makes 6 separate small database queries instead of 1 combined one — works correctly today, just more round-trips than necessary.
- A liquidation-tracking cache in `binance_service.py` keeps a permanent (empty) entry for every symbol that's ever had a liquidation event across all of Binance, not just the coins you actually track. Bounded by Binance's total symbol count (a few hundred), so this isn't a real memory leak, just wider scope than intended.
- A couple of minor `except Exception` blocks that catch errors without logging them (`trading_settings.py`, one spot in `portfolio_intelligence.py` around account-balance display). Neither is in the order-placement path — the order-placement code itself was checked separately and is exemplary (see Security section).
- `cryptography` and `passlib` are both slightly behind current releases; nothing found ties them to a currently-exploitable issue in this app's setup, but worth a routine bump next maintenance pass.

## 4. Frontend (WPF desktop app) finding — already flagged, now conclusively resolved

Your project memory already flagged the Market and Portfolio dashboard files as "pending cleanup, not yet approved for removal." This audit went further and traced the actual navigation code (`MainWindow.xaml.cs`) to settle a question that memory had left open: **are they something you could actually click into, or are they invisible?**

Conclusively invisible. Your sidebar has exactly 9 items — Dashboard, Crypto Signals, Gold Signal, Statistics, History, Account, Auto Trading, Token Scanner, Settings — and the navigation code only has a case for those 9. There is no Market or Portfolio entry in the sidebar at all, and no code path that would ever show either view. Both `MarketViewModel.cs` and `PortfolioViewModel.cs` are dead files with hardcoded sample numbers (e.g. Portfolio's `$48,320.50` balance, `9` open positions — all fixed values, never touched by any API call). They cannot appear on your screen no matter what you click.

This downgrades the finding from "you might be looking at fake numbers" to "harmless dead code sitting unused in the project." It's still worth removing per your no-fabrication standing rule and your existing cleanup approval note — but it's not urgent and it's not something you're currently seeing.

Separately, and more significant long-term: everything built in Phase 6 (AI Trading Coach) and Phase 7 (Portfolio Intelligence, AI Performance Monitoring) exists only on the backend. There is zero WPF frontend code referencing any of those new endpoints — no screen in the desktop app currently lets you use the Trading Coach or see Portfolio Intelligence/Performance Monitoring at all. This isn't a bug (nothing is broken or showing wrong data), it's a features-built-but-not-yet-wired-into-the-UI gap. Not a beta blocker by itself, but worth knowing before you consider Phases 6-7 "done" from your perspective as a user, since right now they're API-only.

## 5. What was reviewed and found clean

- **Async usage**: zero blocking calls (no `time.sleep`, no synchronous `requests`, no blocking file I/O) inside any `async def` anywhere in the backend.
- **Database query patterns**: no N+1 query loops anywhere; both repository files consistently use eager-loading and pagination correctly.
- **Order placement / real-money code path**: reviewed in full. Every exception is caught narrowly (specific error types, never a bare catch-all), every failure either surfaces as an explicit warning to you or aborts the operation — nothing fails silently in this path.
- **Read-only account/balance code**: every degraded-data path logs and/or reports itself via an explicit `warnings` field rather than silently hiding a problem.
- **Known caches from earlier phases** (health telemetry, conversation session state, account balance cache): re-confirmed still correctly bounded (TTL or idle-sweep eviction), no leaks.
- **CORS**: not configured at all, which is correct and safe for a desktop-client-only app — not a gap.
- **Injection risk**: the only raw SQL in the project (startup schema checks in `main.py`) is fixed, hardcoded text with zero user input interpolated — no injection risk.

## 6. Security — already covered by your existing production checklist

Two related items, both already addressed by files already in your project (`.env.production.example`, `scripts/generate_secret_key.py`):

- The app's secret key defaults to a placeholder value (`change_me_in_production`) if left unset. This key both signs login tokens and encrypts your stored Binance API credentials, so it matters. Your `.env.production.example` already documents generating a real one before going live.
- Authentication is off by default (`require_auth=False`) for easier local development. `.env.production.example` already documents turning it on for any non-local deployment.

Neither is a code bug — both are "make sure you follow your own deployment checklist" items. An optional, low-risk improvement (not made, pending your approval) would be a startup check that refuses to boot — or loudly warns — if the secret key is still the placeholder value when auth is required.

## 7. Recommendation

This project is in good shape for a beta release. Nothing found rises to "must fix before beta." The 7 Should-Fix items above are all small, targeted, low-risk changes — most are a few lines each. I'd suggest tackling them together as a single "Beta hardening" pass whenever you're ready to approve it, followed separately (your call on timing) by the Market/Portfolio dead-file cleanup that's already pending your sign-off.

No code has been changed in this phase. Waiting for your approval before making any of the fixes above.
