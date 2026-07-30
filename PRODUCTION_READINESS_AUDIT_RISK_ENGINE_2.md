# Production Readiness Audit — Risk Engine 2.0.0

**Date:** 2026-07-31
**Verdict: CONDITIONALLY READY.** Everything verifiable in this environment passes. **Three items cannot be verified here at all** (real PostgreSQL, Docker, live startup) and are listed as blocking pre-deployment gates rather than assumed good.

---

## 0. What I Could and Could Not Verify

| # | Item | Status |
|---|---|---|
| 1 | Migrations on real PostgreSQL | ❌ **NOT VERIFIED — no PostgreSQL in this environment** |
| 2 | Docker build from scratch | ❌ **NOT VERIFIED — no Docker daemon** |
| 3 | Full regression suite | ✅ **719 passing** |
| 4 | No N+1 in audit/snapshot code | ✅ Verified by AST analysis |
| 5 | No memory leak in RiskContext cache | ✅ Measured |
| 6 | No race conditions in monitor writes | ✅ Reviewed |
| 7 | Snapshot persistence transactional | ✅ Reviewed |
| 8 | Audit writes cannot deadlock execution | ✅ Reviewed — one real interaction found and shown safe |
| 9 | Alembic downgrade works | ⚠️ **Structurally correct, never executed** |
| 10 | Startup on empty database | ⚠️ Reviewed, not executed |
| 11 | Startup on existing production database | ⚠️ Reviewed, not executed |
| 12 | Performance impact | ✅ Measured |
| 13 | Concurrency and locking | ✅ Reviewed — one capacity finding |
| 14 | Deployment checklist + rollback | ✅ §8 / §9 |

I will not report a green tick for anything I did not actually run. Items 1, 2, 9, 10 and 11 all require infrastructure that does not exist in this sandbox — that has been true throughout this engagement and is not something I can engineer around.

---

## 3. Regression Suite — PASS

```
Half A: 21 files, 338 passed
Half B: 21 files, 381 passed
TOTAL:  719 passed, 1 skipped
```

**One real failure**, unchanged and pre-existing since long before this work: `test_universal_scanner::test_triggers_on_primary_timeframe`. It does not mock `get_engine_run_state()` and reads your real on-disk `data/trading_settings.json`, which currently holds `engine_run_state: "stopped"`. Environmental, not a code defect.

Four `test_correlation_risk` entries appear as failures in the split runner only — it cannot inject `@pytest.fixture` arguments into module-level test functions. The canonical runner passes them. This is a limitation of the tooling I built to work around the 45-second shell timeout, not of the code.

`python -m compileall app tests` → exit 0.

---

## 4. N+1 Analysis — PASS

AST walk over every `For` / `AsyncFor` / `ListComp` / `While` node in the new modules, looking for `.execute()`, `.commit()`, `.add()`, `.scalar_one_or_none()`:

```
app/services/risk_audit.py       NO DB CALLS INSIDE ANY LOOP
app/risk/context_builder.py      NO DB CALLS INSIDE ANY LOOP
app/services/execution_risk.py   NO DB CALLS INSIDE ANY LOOP
```

Query budget per execution attempt: **1 INSERT** (`risk_assessments`) + **1 SELECT** (`get_peak_equity`, once per *request* inside `_build_risk_context`, not per signal). Per monitor poll: **1 INSERT** (`equity_snapshots`).

`get_peak_equity` is `ORDER BY equity DESC LIMIT 1`, served directly by `ix_equity_snapshots_env_captured` — it does **not** scan the growing table.

---

## 5. Memory — PASS

```
5,000 consecutive assessments retained: +1.2 KiB
RiskEngine instance attrs: ['limits']   (stateless apart from limits)
RiskContext: frozen=True
_ACCOUNT_BALANCE_CACHE keys: ['fetched_at', 'fetched_wall', 'futures', 'value']
```

The cache is a **fixed four-key dict**, not keyed by symbol or request, so it structurally cannot grow unboundedly — it holds exactly one `FuturesAccountInfo` at a time and replaces it on refresh. `RiskContext` is frozen and per-call; `MetricResult` objects are per-assessment and unreferenced afterwards. +1.2 KiB across 5,000 runs is allocator noise, not retention.

---

## 6. Race Conditions in Monitor Writes — PASS

`_capture_equity_snapshot()` performs an **append-only INSERT** — no read-modify-write, so there is no lost-update window. It opens its own short-lived session and commits immediately.

The monitor is documented single-instance and `check_active_signals()` runs sequentially in one asyncio loop, so there is no intra-process concurrency. **Multi-process caveat:** running two monitors would write duplicate equity points. That is harmless for correctness — `get_peak_equity` is a `MAX`, and duplicates cannot move a maximum — but it would inflate row count. The existing single-process deployment assumption (documented in `signal_monitor.py` and `binance_credentials.py`) still holds.

---

## 7. Transactionality and Deadlock — PASS, with one interaction worth understanding

**Transactional:** all three audit functions use `async with AsyncSessionLocal() as session: ... await session.commit()`. Each is one atomic unit in its own connection; the context manager rolls back on exception. The snapshot row is written whole or not at all — there is no partial-snapshot state.

**Deadlock analysis — the one real interaction.** `risk_assessments.signal_id` is an FK to `signals.id`. Inserting an audit row takes a `FOR KEY SHARE` lock on the referenced `signals` row. Later in the same request, `execute_signal()` UPDATEs that same row (`signal.executed = True`).

These are **two different transactions from one request**, which is exactly the shape that can self-deadlock. It does not, because:

1. `record_assessment()` commits and closes **before** returning — the `FOR KEY SHARE` lock is released before the UPDATE is ever issued.
2. The order is fixed: audit insert → (raise or proceed) → signal update. There is no path where the UPDATE is held open across the audit insert.

**Had the audit session been left open, or nested inside the request transaction, this would deadlock under load.** It is worth stating explicitly so nobody "optimises" it later by reusing the request session.

**Audit failure cannot block trading:** every audit function catches `Exception` and returns `None`/`False`. The decision has already been made by the time these run; refusing a trade because its audit row failed to write would be strictly worse than the missing row. Same discipline as `expire_armed_pending_entries`.

---

## 12–13. Performance and Concurrency

**Measured, full eight-metric assessment:**

| Open positions | Per assessment | Throughput |
|---|---|---|
| 0 | 52.1 µs | ~19,200/s |
| 5 | 54.8 µs | ~18,200/s |
| 20 | 59.6 µs | ~16,800/s |
| 100 | 102.4 µs | ~9,800/s |

`build_risk_context()`: 19 µs (1 position) → 351 µs (100). Growth is linear, no quadratic term.

**Assessment:** negligible. At a realistic 5–20 positions the whole engine costs **~55–60 µs**, against a 30-second monitor poll and a human-triggered execution path. The dominant cost in any real request remains the Binance HTTP round-trip (tens of milliseconds), which this work did **not** add to — the margin/leverage/liquidation data reuses the already-cached `get_full_snapshot()`.

**Connection pool finding (real, worth acting on).** `_build_risk_context()` runs inside the request's session (via `Depends`) and calls `get_peak_equity()`, which opens a **second** session. So a risk-context build briefly holds **two** pool connections. With `pool_size=20, max_overflow=10` that halves effective concurrency for those requests. Not a correctness problem and not a deadlock (the second is a short read-only SELECT on a different table), but it is the one thing I would watch under load. Cheapest fix if it ever matters: pass the existing session into `get_peak_equity()`.

---

## 8. Deployment Checklist

Run in order. Do not skip 1–3; they are the gates this environment could not close.

**Pre-deployment (must pass before any deploy)**
1. `docker compose build --no-cache` — must succeed from scratch.
2. Restore a **copy** of the production database. Run `alembic upgrade head`. Confirm `20260730_01` then `20260730_02` apply cleanly.
3. On that copy, run `alembic downgrade 20260730_01`, then `alembic upgrade head` again. Confirms the rollback path actually works **before** you need it.
4. Verify PostgreSQL ≥ 12 (`ALTER TYPE ... ADD VALUE IF NOT EXISTS` in the startup bootstrap requires it).
5. Take a full production database backup. Record the exact filename.

**Deployment**
6. Stop the backend (scheduler must not be mid-poll during migration).
7. `alembic upgrade head` against production.
8. Start the backend. **Watch the log for:**
   - `Risk limit coherence check passed.` — if this raises instead, the deploy has stopped itself on purpose; see §9.
   - `signalstatus enum value 'PENDING_ENTRY' present.` / `'EXPIRED' present.`
   - No `initial_stop_loss bootstrap skipped` warning.
9. Confirm both new tables exist and are empty: `equity_snapshots`, `risk_assessments`.
10. Wait ~2 minutes. Confirm `equity_snapshots` is accumulating (~1 row per 30s). If it stays empty, the monitor is not capturing — check for `Equity snapshot capture skipped` warnings.
11. Attempt one execution on **Testnet**. Confirm a `risk_assessments` row appears with a populated `snapshot`, correct `risk_engine_version`, and `context_source = 'binance_exchange'`.
12. If `context_source` reads `database_fallback` on a live account, the exchange snapshot is not reaching the engine — margin/leverage/liquidation will be UNKNOWN. Investigate before trusting the gate.

**Post-deployment**
13. Confirm drawdown flips from UNKNOWN to a real value once equity history exists (a few minutes).
14. Spot-check that a *rejected* execution also produced a `risk_assessments` row — rejections must be as auditable as approvals.

---

## 9. Rollback Procedure

**Tier 1 — behavioural rollback, no schema change (seconds, preferred).**
Set `entry_mode` to `"market"` in `data/trading_settings.json`. Reverts the entry model platform-wide. Independent of the risk engine.

**Tier 2 — risk-limit rollback.** The engine's limits live in `app/risk/limits.py`. To restore pre-2.0.0 gating behaviour, set `max_leverage_percent = 50.0`. ⚠️ **This will fail `assert_coherent()` at startup by design** — that combination is exactly the misconfiguration that made `max_open_risk_percent` unreachable. If you deliberately want it, you must also lower `max_open_risk_percent` to ≤ 1.0 to keep the limits coherent.

**Tier 3 — schema rollback.**
```
alembic downgrade 20260730_01   # drops risk_assessments + equity_snapshots
alembic downgrade base          # additionally drops signals.initial_stop_loss
```
⚠️ `downgrade base` is **destructive and irreversible for risk data**: it drops `initial_stop_loss`, and the original stops for currently-open trades are unrecoverable (this project never recorded stop history). Prefer restoring the §8.5 backup over downgrading past `20260730_01` on a live database.

**Tier 4 — full restore.** Deploy the previous image and restore the §8.5 backup. Note the previous code does not know about `initial_stop_loss`, `equity_snapshots` or `risk_assessments`; extra columns and tables are harmless to it, so a code-only rollback with the new schema in place is also viable and is the safer option.

---

## Remaining Risks

1. **Alembic has never been executed anywhere.** Both migrations are syntactically valid and the revision chain is linear (`None → 20260730_01 → 20260730_02`) with real `downgrade()` bodies, but "never run" is not "works". Checklist steps 2–3 exist precisely to close this.
2. **Docker build unverified.** No new Python dependencies were added (Alembic was already in `requirements.txt`), so risk is low — but unverified.
3. **Two schema mechanisms coexist.** `main.py`'s idempotent `ALTER TABLE` bootstraps still run alongside Alembic. They are equivalent and idempotent, so this is safe but untidy. Consolidating onto Alembic (`alembic stamp head`, then delete the bootstraps) remains worthwhile.
4. **Double connection during risk-context build** (§12–13). Watch under concurrency.
5. **WPF still never compiled** — no .NET SDK here, unchanged from earlier phases. The new `initial_stop_loss` field is exposed by the API but unused by the client; extra JSON fields are ignored harmlessly.
6. **Live Testnet execution still never exercised end-to-end** — no network egress to Binance from this environment, unchanged throughout.
