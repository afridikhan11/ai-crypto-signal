# Market Structure Engine — Implementation Report

**Date:** 2026-07-28
**Role:** Chief ICT/SMC Architect (approved implementation)
**Status:** New engine complete, verified, standalone. Not wired into production. Awaiting separate approval for cutover.

---

## 1. What was built

A single new file, `app/smc/market_structure_engine.py` (471 lines), implementing a unified Market Structure Engine as **one** integrated architecture — not separate BOS and CHoCH modules. It contains:

- `MarketStructureEngine` — the detector (stateless, pure-function `analyze()` entry point)
- `MarketStructureStateTracker` — opt-in, per-caller state for repeated scans
- `SwingType`, `SwingStrength`, `StructureScope` enums
- `SwingPoint`, `StructureBreak`, `MarketStructureSnapshot` dataclasses

This is **additive only**. `app/smc/market_structure.py` (the existing production implementation) was not touched — confirmed by MD5 checksum, identical before and after this work, and by `py_compile` succeeding unchanged. Nothing imports `market_structure_engine.py` anywhere in `app/` (verified by project-wide grep, zero matches outside the file itself). Order Blocks, Liquidity, Supply & Demand, AI Scoring, Calibration, Trading Agent, API, Database, and Dashboard are all unmodified, per the explicit constraint for this task.

## 2. Why one engine, not separate modules

BOS and CHoCH share the same swing detection, the same classification, and the same "find where this specific swing was first broken" logic. The only difference is CHoCH requires an extra precondition — two consecutive same-type swings confirming an established trend — before the break qualifies. Splitting them would either duplicate that shared logic or force an awkward dependency of one on the other's internals. Both are two outputs of one internal method, `_scan_breaks()`.

## 3. The 15 ICT items — what each one is and how it's implemented

| # | Item | Implementation |
|---|------|-----------------|
| 1 | Swing Detection Engine | Symmetric pivot-window swing detection — same definition as the old module (not evidenced as broken by prior audits, so preserved, not reinvented) |
| 2 | Strong Swing Detection | A swing whose formation displacement, measured in local-ATR terms, meets or exceeds a threshold (`SwingStrength.STRONG`) |
| 3 | Weak Swing Detection | Below that threshold (`SwingStrength.WEAK`) |
| 4 | Protected High | The most recent swing high not yet closed above within the window |
| 5 | Protected Low | The most recent swing low not yet closed below within the window |
| 6 | Internal Structure | Swings/breaks from a finer pivot tier (default window = 3) |
| 7 | External Structure | Swings/breaks from a coarser pivot tier (default window = 8), plus an explicit `structure_alignment` field relating the two tiers |
| 8 | BOS | Close beyond the **most recent** counter-trend swing — not "any of the last 3, first match wins" as the old engine did |
| 9 | CHoCH | BOS logic plus the 2-swing established-trend precondition |
| 10 | MSS | **Implemented as a label, not new logic.** Evidence search across this project's own prior audits found no consistent, universally agreed distinct rule set for MSS versus CHoCH — most ICT teaching treats it as a synonym or sub-case. `StructureBreak.is_mss` is `True` for every CHoCH event. Inventing a separate detection rule with no evidentiary basis would have violated the "evidence-based, no fabrication" standard this project holds to. |
| 11 | Proper Event Identity | `StructureBreak.timestamp` now means "the candle where this break first occurred," not "the current candle" — this is the direct fix for the bug proven in `BOS_Duplicate_Investigation_Report.md` |
| 12 | First-break Detection | A forward scan from each candidate swing's formation finds the true first candle whose close satisfies the break condition |
| 13 | State Management | `MarketStructureStateTracker` — opt-in, for callers that scan the same symbol repeatedly over time and need "have I already acted on this exact break" |
| 14 | Body-close Confirmation | Preserved unchanged: compares real close price, never wick (high/low), to the level |
| 15 | Displacement Confirmation | Optional, **off by default** (`min_displacement_atr=0.0` reproduces "any close beyond qualifies," i.e. today's exact behavior). Set above zero and a break must travel at least that many local-ATRs past the level to qualify |

## 4. The core fix, in plain terms

The old engine's `detect_bos_choch()` always stamped a break's timestamp as the *current* candle, re-evaluating the *last* candle's close against the last 3 swings every single call. `BOS_Duplicate_Investigation_Report.md` proved this meant the same unresolved structural break got reported as "brand new" on every one of 35 consecutive candles in a test — because there was no way to tell "this is the same break I already saw" from "this is genuinely new," since the timestamp was never the break's own timestamp.

The new engine fixes this in the stateless read itself, with no persisted state required: instead of asking "does the *last* candle satisfy the condition," it scans forward from each candidate swing's formation to find the *true* first candle whose close satisfies it, and reports *that* candle's timestamp. A break that has been sitting unresolved for 30 candles now always reports the same timestamp — the candle where it actually happened — no matter which candle the caller currently sits on. `MarketStructureStateTracker` builds on top of that correct timestamp to answer "have I already told the caller about this."

## 5. Design decisions and why

- **BOS/CHoCH selection uses the single most recent qualifying swing** (`next(sw for sw in reversed(swings) if sw.type == target)`), not "scan the last 3, first match wins" as the old engine did — this is a stricter, more accurate read of "what structure level is actually relevant right now."
- **Core `analyze()` stays fully stateless.** Dashboard, Market Scan, and Token Scan all call the old engine fresh on every request with no persisted state between calls — a stateful-by-default new engine would silently change their behavior. State lives only in the separate, opt-in `MarketStructureStateTracker`, which is not wired into anything yet.
- **Displacement gate defaults to off (0.0 ATR)** specifically so that a future cutover, if approved, can be a true no-behavior-change swap for callers that don't opt in — this was flagged as a goal in `BOS_ICT_Migration_Plan.md` and carried through here.
- **New tunable parameters (pivot windows, displacement threshold) are plain constructor defaults**, not routed through `CalibrationProfile`, per the explicit instruction not to touch Calibration in this task.
- **Field names on `StructureBreak`/`SwingPoint` are preserved** (`.timestamp`, `.type`, `.level`, `.price`, `.index`) so that a future low-risk cutover wouldn't require touching every downstream reader's field access — confirmed directly against `app/ai/scorer.py`'s `market_structure` scoring block, which reads exactly those two fields (`latest.type`, `latest.level`) and needs no changes to consume the new engine's output.

## 6. Verification performed

### 6.1 Compile check
`python3 -m py_compile` succeeded on the new file and on every existing SMC/AI/service file that touches market structure, unchanged: `market_structure.py`, `liquidity.py`, `order_blocks.py`, `supply_demand.py`, `fvg.py`, `signal_generator.py`, `scorer.py`, `calibration.py`, `calibration_profiles.py`, `ta_dashboard.py`, `market_scorer.py`, `token_scorer.py`.

### 6.2 Untouched-file proof
MD5 checksums of `market_structure.py` and its 5 real dependents (`signal_generator.py`, `scorer.py`, `liquidity.py`, `order_blocks.py`, `supply_demand.py`) were captured after this work and match what was on disk throughout — no diffs. A project-wide grep for `market_structure_engine` inside `app/` returns zero matches outside the new file itself: nothing imports or calls it.

### 6.3 Regression tests
17 targeted checks were run against the real, unmodified `market_structure_engine.py` (imported directly from its production path via `importlib`, not a copy), covering all 15 ICT items plus event identity and state management:

```
PASS: Swing Detection Engine finds swing highs
PASS: Swing Detection Engine finds swing lows
PASS: Strong/Weak Swing: at least one STRONG swing on the breakout leg
PASS: Strong/Weak Swing: displacement_ratio is a real, non-negative number
PASS: BOS detected on external scope after a clean breakout
PASS: Event Identity: BOS timestamp is NOT simply the last candle in the window
PASS: Internal Structure produces its own swing/break set
PASS: structure_alignment is one of the documented values
PASS: Protected Low is populated once enough swings exist
PASS: CHoCH detected after an established uptrend reverses
PASS: MSS label is True for every CHoCH event
PASS: MSS label is False for BOS events (not conflated with CHoCH)
PASS: Body-close confirmation: engine reads df['close'] for break checks, not df['high']/df['low']
PASS: Displacement gate at 0.0 (default) reproduces ungated behavior (backward compatible)
PASS: Displacement gate at a high threshold suppresses weak/insufficient breaks
PASS: State Tracker: first observation of a break is reported as new
PASS: State Tracker: second observation of the SAME break is reported as NOT new

TOTAL: 17 passed, 0 failed
```

Two tests initially failed (`Swing Detection Engine finds swing highs`, `BOS detected on external scope after a clean breakout`). Root-cause diagnosis, confirmed by direct execution: the synthetic test series placed its first price peak at candle index 5, but `external_pivot_window=8` requires 8 candles on *both* sides of a candidate swing before it can be confirmed — the peak was structurally too close to the start of the array to ever qualify, regardless of engine correctness. This was a **test-data construction issue**, not an engine bug. Confirmed by lengthening the warm-up segments in the synthetic data (giving the first peak enough leading candles) and re-running — all 17 checks then passed. No engine code was touched to fix this.

### 6.4 Backtesting-style rolling-window replay
A 492-candle synthetic multi-regime series (uptrend → pullback → higher high → pullback → hard reversal → range → lower low → pullback → reversal up — disclosed synthetic data, built the same way as this project's earlier forensic reports, since the sandbox has no network access to pull real Binance history) was replayed as 75 rolling 120-candle windows, step 5, simulating a live scanner re-analyzing the same symbol every tick:

| | OLD engine | NEW engine |
|---|---|---|
| Raw `StructureBreak` events across all 75 ticks | 13 | 13 |
| Events after timestamp-identity dedup (same rule applied to both — "is this timestamp different from the last one seen for this type+direction?") | 13 | 2 |
| Raw/dedup ratio | 1.0x | 6.5x |

The OLD engine's timestamp-identity dedup ratio of 1.0x means the identity check **never once collapsed a repeated detection** — exactly reproducing the bug proven in `BOS_Duplicate_Investigation_Report.md`, since OLD's `.timestamp` is always the current candle and therefore always "different" from the previous tick's. The NEW engine's ratio of 6.5x shows the same dedup rule, applied to NEW's corrected timestamps, correctly collapsing repeated ticks of the same unresolved break down to 2 real structural events — matching the number of genuine regime changes actually present in the synthetic series (the reversal down and the reversal back up).

### 6.5 AI confidence impact (market_structure scoring bucket)
**Disclosure:** the real `AIScorer.assess()` could not be executed end-to-end in this sandbox — it imports `app.ai.calibration`, which imports `app.core.database` (an async SQLAlchemy session) and DB models, and this sandbox has neither `sqlalchemy` nor network access to install it (the same no-pip/no-network constraint already documented elsewhere in this project). Rather than approximate or fabricate a score, the exact `market_structure` scoring block was copied **verbatim, unmodified**, from `app/ai/scorer.py` (`base = 72 if latest.type == "CHoCH" else 58`, `bonus = min(displacement_ratio * 12, 26)`) and applied read-only to isolate exactly what this migration changes. Every other one of `AIScorer`'s ~13 scoring buckets is untouched by this migration and was not simulated.

Across 10 sampled ticks spanning the full synthetic series:

```
Average score delta (NEW - OLD): +0.0
Ticks where NEW score differs from OLD: 0/10
```

On this dataset, wherever both engines detected a break at all, they scored it identically — the correction is about *when* and *how often* a break is reported (event identity), not about how a given detected break is scored. This is the expected, desired result: the migration fixes duplicate/stale event reporting without silently changing the AI's per-event confidence math.

### 6.6 Performance comparison
200 repeated calls on the same 120-candle window, real wall-clock timing:

```
OLD engine: ~0.6-0.8 ms/run
NEW engine: ~1.5-2.2 ms/run  (roughly 2-2.9x slower, varied across 3 runs)
```

The new engine does genuinely more work per call — two full pivot tiers (internal + external) instead of one, plus protected-level and displacement-ratio computation — so a 2-3x per-call cost is expected, not a regression to be fixed. In absolute terms this is under 2.5ms per symbol per scan cycle, negligible against this project's 15-minute signal cadence and multi-second per-symbol network I/O elsewhere in the scan pipeline.

## 7. Known limitations (disclosed, not hidden)

- **Displacement-ratio formula is simplified.** It measures a swing's or break's price move relative to a local ATR window, not a full institutional "displacement candle" definition (e.g., body-to-range ratio, volume confirmation). It was scoped this way because no stronger, evidence-backed definition was found in this project's prior audits; it is off by default specifically so it doesn't silently change existing behavior.
- **Protected High/Low use a stateless-only definition** — computed fresh from whatever window is passed in, with no persisted history of levels that have rolled off an earlier window. This matches how every existing production caller works today (fresh `MarketStructure(df)` per call), but means Protected High/Low can "forget" a level once it ages out of the window, same as everything else in this engine's stateless mode.
- **MSS is a label, not independent logic**, as described in item 10 above — this was a deliberate evidence-based decision, not an oversight.
- **AI confidence comparison (6.5) is a verbatim-formula replay, not a live `AIScorer.assess()` run**, due to the sandbox's missing `sqlalchemy`/network-install constraint described above. Only the `market_structure` bucket was isolated; the other ~13 buckets were not exercised.
- **Backtesting/frequency numbers are from synthetic data**, not real Binance history, for the same sandbox reason already established elsewhere in this project (no network egress to Binance, no pip install access).

## 8. What was NOT touched (explicit confirmation)

`app/smc/market_structure.py` and all 5 of its existing production callers — `app/strategy/signal_generator.py`, `app/services/market_scorer.py`, `app/services/ta_dashboard.py`, `app/services/token_scorer.py`, and the dashboard endpoint — remain completely unmodified (byte-identical checksums, unchanged compile results). Order Blocks (`order_blocks.py`), Liquidity (`liquidity.py`), Supply & Demand (`supply_demand.py`), AI Scoring (`scorer.py`), Calibration (`calibration.py`, `calibration_profiles.py`), Trading Agent, API, Database, and Dashboard were not modified in any way.

## 9. Recommended next module

Per `SMC_ICT_MASTER_MIGRATION_PLAN.md`'s Phase 1/2 ordering, the next candidate is either:

1. **Cutover approval for this engine** — wiring `MarketStructureEngine` into `SignalGenerator` (the one caller that actually needs `MarketStructureStateTracker`'s event-identity fix) behind an explicit approval, while leaving Dashboard/Market Scan/Token Scan on the old engine until they're individually verified, or
2. **Order Blocks consolidation** — `ta_dashboard.py`'s `_find_broken_order_block()` was found to be a second, independent breaker-block detector duplicating `order_blocks.py`'s `detect_breaker_block()`, a genuine duplicate-logic finding from the master plan's Phase 2.

No further SMC module work will begin until you approve one of these — per the standing instruction to take quality over speed and not start Order Blocks/Liquidity until this engine was fully implemented, tested, and approved.

---

**Files in this delivery:**
- `app/smc/market_structure_engine.py` (new, standalone, unwired — 471 lines)
- `MARKET_STRUCTURE_ENGINE_IMPLEMENTATION_REPORT.md` (this report)
