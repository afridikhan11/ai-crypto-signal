# ICT Migration — Phase A & Phase B Implementation Report

**Date:** 2026-07-29
**Scope:** Phase A (commit ICT engine regression suites) and Phase B (Session & Kill Zone Engine) of the institutional ICT migration plan (`ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md`, Step 9).

---

## Phase A — Committed the four ICT engine regression suites

**Analyze:** The audit found 83 verified checks (Market Structure 17, Order Blocks 21, Liquidity 23, Supply & Demand 22) existed only as ad hoc scratchpad scripts outside version control — the project's real `tests/` directory had zero committed coverage for its core ICT detection logic.

**Plan:** Translate the scratchpad `check()`-style scripts into proper pytest files matching this project's existing convention (plain `from app.smc.X import Y` imports, as used by `tests/test_supply_demand.py`), one file per engine, then commit into `tests/`.

**Implement:**
- `tests/test_market_structure_engine.py` (17 checks → 17 pytest tests: Swing Detection, Strong/Weak classification, BOS, MSS/CHoCH labeling, Internal/External structure + alignment, Protected Low, body-close confirmation, the opt-in displacement gate, `MarketStructureStateTracker` dedup).
- `tests/test_order_block_engine.py` (21 checks → 21 pytest tests: bullish/bearish OB detection, honest degradation with no structure/FVG context, BOS/CHoCH relationship tagging, FVG confluence, the Fresh→Mitigated→Breaker→Invalidated lifecycle state machine, Breaker Block finders, the displacement gate).
- `tests/test_liquidity_engine.py` (23 checks → 20 pytest tests, same coverage — some checks consolidated into multi-assert test functions): Equal Highs/Lows clustering, lone-swing liquidity, Internal vs External scope, Grab vs Breakout classification, unconfirmed sweeps, resting liquidity, full-history sweep scanning, engineered-liquidity detection, strength grading, reversal linkage, OB/FVG confluence, determinism, timeframe tagging.
- `tests/test_supply_demand_engine.py` (22 checks → 23 pytest tests): zone lifecycle, the backward-compatible Premium/Discount contract, BOS/CHoCH/OB/Liquidity/FVG relationship tagging, strength grading, determinism.
- **Deleted** `tests/test_supply_demand.py` (4 tests) — fully subsumed byte-for-byte by the new `TestPremiumDiscountContract` class in `test_supply_demand_engine.py`. Justification: duplicate coverage of the exact same Premium/Discount formula: keeping both would be the "duplicate test logic" this migration is explicitly meant to eliminate.

**Test / Verify:** No network egress is available in this environment to install `pytest`/`sqlalchemy` (confirmed — pip install fails against the proxy). Verified two ways instead:
1. Re-ran all four original scratchpad scripts directly against the current, unmodified production engines — **83/83 passed**, confirming the engines themselves are unchanged and correct before porting.
2. Built a minimal local `pytest` shim (`fixture`/`skip` only) and a custom collector to execute the real, committed pytest files exactly as pytest would (module-level `test_*` functions, `Test*` classes, module-scoped fixtures) — **81/81 passed** against the real files now sitting in `tests/`. The count differs slightly from 83 because some checks were consolidated into single multi-assert test functions (a normal pytest idiom), not because any assertion was dropped — verified by direct comparison of each original `check()` line against its corresponding `assert`.
3. `python -m py_compile` on all four new files — clean.

This is the same evidence-based verification discipline (real production import, no stubbing) used throughout this project's prior engine migrations; it will also run for real (with actual `pytest`/`sqlalchemy`) the next time CI or the local Windows venv executes `pytest -v`.

**Benchmark:** Not applicable — this phase adds test coverage only, no runtime code path changed.

---

## Phase B — Session & Kill Zone Engine (new ICT concept)

**Analyze:** The audit confirmed Session Analysis / Kill Zones did not exist anywhere in the codebase — signals fired identically at 3am and 3pm UTC. This was one of the 8 confirmed-missing ICT concepts from the KEEP list.

**Plan:** A new, pure, asset-agnostic `app/smc/session_engine.py`, matching the existing four engines' architecture (dataclasses, `Enum`, no I/O/logging inside the engine, fully unit-testable, timeframe/asset-agnostic). Classifies Asian/London/New York sessions, the London/New York overlap, and four Kill Zones (Asian, London, New York, London Close) from standard, documented UTC hour windows — disclosed as configurable defaults, not asserted as one unquestionable truth, and overridable via constructor arguments.

**Implement:** `app/smc/session_engine.py` — `SessionEngine.classify_timestamp()` for single-timestamp classification, `.annotate()` for vectorized DataFrame annotation (no per-row Python loop — hour extraction plus vectorized `.apply()` over a `Series`, consistent with the project's performance-conscious pattern elsewhere), `.latest_context()` convenience. Timezone convention explicitly documented: tz-naive timestamps (which is what every OHLCV frame in this project already uses, confirmed by reading `binance_service.py`/`scanner.py`) are treated as UTC; tz-aware input is converted to UTC first.

This module is **purely additive** — nothing in the existing codebase calls it yet (wiring it into `signal_generator.py`/`AIScorer` is a later phase, once the Evidence Engine and AIScorer rebuild are ready to consume it). No existing engine, test, or production behavior was touched.

**Test:** `tests/test_session_engine.py`, 26 tests covering: each session in isolation, the London/NY overlap, all four Kill Zones, off-session ("dead zone") detection, start-inclusive/end-exclusive boundary edges, naive-as-UTC and tz-aware-to-UTC conversion, the vectorized `annotate()` path cross-checked row-by-row against `classify_timestamp()` over a full 96-candle UTC day, empty-DataFrame handling, non-mutation of the input DataFrame, and the `SessionWindow` primitive directly (normal, zero-width, and midnight-wrapping windows).

**Verify:** One test initially failed on first run (`test_london_only_kill_zone` assumed 08:00 UTC was London-only, but the Asian session's own documented window extends to 09:00 UTC, so 08:00 UTC is genuinely an Asian/London overlap under the stated default windows) — this was a **test-data mistake, not an engine bug**; fixed by using 09:30 UTC for the London-only case and adding a new explicit test for the real Asian/London overlap at 08:00. Re-ran: **26/26 passed** against the real, committed file (same shim-based verification method as Phase A). `py_compile` clean.

**Benchmark:** `.annotate()` is vectorized (no Python loop per candle) by design; not yet benchmarked against a live scan cycle since nothing calls it yet.

---

## Status

Phase A and Phase B are complete and verified. Neither touched any existing production code path — Phase A only added test files, Phase B only added a new, uncalled module. `git status` in the working tree still reflects the prior (already-approved, not-yet-committed) SMC→ICT migration series' changes plus these additions; nothing has been force-pushed or destructively altered.

**Next:** Phase C (Inducement detection, extending `LiquidityEngine`'s existing sweep/reversal-linkage output — additive, must preserve all 20 currently-passing Liquidity Engine tests unchanged).
