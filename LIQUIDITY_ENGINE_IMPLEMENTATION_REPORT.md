# Liquidity Engine Implementation Report

**Date:** 2026-07-28
**Scope:** Complete replacement of the legacy Liquidity implementation with a true ICT Liquidity Engine. Third and final module in the SMC-to-ICT migration series (Market Structure -> Order Blocks -> Liquidity).
**Status:** COMPLETE. There is now exactly ONE Liquidity implementation in the project: `app/smc/liquidity_engine.py`.

---

## 1. Files Changed

| File | Change |
|---|---|
| `app/smc/liquidity_engine.py` | **NEW.** The single production ICT Liquidity Engine. |
| `app/smc/market_structure_engine.py` | Additive only: `MarketStructureSnapshot` gained two new fields, `internal_swing_highs`/`internal_swing_lows` (both `List[SwingPoint]`, default `[]`), exposing swing data the engine already computed internally but previously discarded. Required so Internal Liquidity has real internal-tier swing data to work from. `analyze()`'s return statement updated to populate them (keyword-only construction, so no existing caller is affected). |
| `app/strategy/signal_generator.py` | Migrated. Liquidity detection reordered to run after Order Block/FVG detection so sweeps can carry OB/FVG/structure confluence. |
| `app/services/market_scorer.py` | Migrated, same reordering. |
| `app/services/token_scorer.py` | Migrated, same reordering. |
| `app/services/ta_dashboard.py` | Migrated, same reordering; also now threads the internal-tier swings it already computes (`snap_tiers`) into the engine for Internal Liquidity, and passes both `bullish_ob`/`bearish_ob` (not just one direction) since this dashboard evaluates both sides at once. |
| `app/api/v1/endpoints/dashboard.py` | Migrated. Simple drop-in swap - no confluence data available at this call site, none guessed. |
| `scripts/analyze_smc_frequency.py` | Migrated in both replay functions (`measure_smc_frequency`, `measure_confidence_impact`), same reordering pattern. Doc comment corrected. |
| `tests/test_ai_scorer.py` | Import updated; the 3 `LiquidityLevel(...)` fixtures updated with the new required `kind`/`scope`/`strength` fields (`SINGLE_SWING`/`EXTERNAL`/`MODERATE` - neutral values, not asserted on by any test). |
| `app/services/geckoterminal_service.py` | Doc-comment-only fix: stale class names (`LiquidityDetector`, `OrderBlockDetector`) corrected to `LiquidityEngine`, `OrderBlockEngine`. No functional change - this file never imported the Liquidity module, only mentioned it in a comment. |

## 2. Files Removed

- `app/smc/liquidity.py` - the legacy `LiquidityDetector`/`LiquidityLevel`/`LiquidityType` implementation. Fully deleted, not deprecated or aliased.

**Confirmed via project-wide sweep after deletion:** zero remaining imports of `app.smc.liquidity`, zero remaining references to `LiquidityDetector`, in any `.py` file (excluding `.venv`). The only two matches for the old class name anywhere in the tree are inside `liquidity_engine.py`'s own module docstring, documenting what was replaced.

## 3. ICT Concepts Implemented

| # | Concept | Where |
|---|---|---|
| 1 | Buy Side Liquidity (BSL) | `detect_buyside_liquidity()` |
| 2 | Sell Side Liquidity (SSL) | `detect_sellside_liquidity()` |
| 3 | Equal Highs (EQH) | `LiquidityKind.EQUAL_HIGHS` - a BSL pool from >=2 clustered swing highs |
| 4 | Equal Lows (EQL) | `LiquidityKind.EQUAL_LOWS`, mirror |
| 5 | Internal Liquidity | `LiquidityScope.INTERNAL`, built from `MarketStructureEngine`'s internal-tier swings |
| 6 | External Liquidity | `LiquidityScope.EXTERNAL`, built from the external tier |
| 7 | Liquidity Pools | Every `LiquidityLevel` returned IS a liquidity pool - no separate class needed |
| 8 | Liquidity Sweeps | `detect_liquidity_sweeps()` - a real forward scan from each level's own formation candle, not a single-candle snapshot |
| 9 | Liquidity Grabs | `sweep_outcome == GRAB` - swept and closed back on the original side within the confirmation window |
| 10 | Resting Liquidity | Any `LiquidityLevel` with `swept == False` - not a separate method, the natural unswept state |
| 11 | Engineered Liquidity | `LiquidityLevel.engineered` - a disclosed heuristic: an EQH/EQL cluster tighter than the normal clustering tolerance requires |
| 12 | Sweep confirmation | `LiquidityLevel.sweep_confirmed` - True only when a close-based outcome (grab or breakout) was actually observed within the confirmation window; False (never guessed True) if the window ran out first |
| 13 | Strong vs Weak Liquidity | `LiquidityLevel.strength` (STRONG/MODERATE/WEAK) - deterministic scoring on pool size + scope |
| 14 | Multi-Timeframe compatibility | Timeframe-agnostic by construction; optional `timeframe` tag field on every result |

**Integration with the other two completed ICT engines** (all optional constructor args, threaded through wherever a caller already has them):
- `structure_breaks` (from `MarketStructureEngine`) -> `reversal_break_level`/`reversal_is_choch`: was this sweep followed by a real opposite-direction structure break shortly after (the "sweep then reverse" signature)?
- `order_blocks` (from `OrderBlockEngine`) -> `has_ob_confluence`
- `fvgs` (from `FVGDetector`) -> `has_fvg_confluence`

When a caller doesn't have one of these available, the corresponding field stays at its honest default (`False`/`None`) rather than being guessed.

## 4. Before vs After

**Old `LiquidityDetector` (deleted):**
- Only detected Equal Highs/Lows - a lone, untested swing high/low was silently discarded. `if len(cluster) >= 2` was the only path that ever produced a level at all.
- No Internal vs External distinction.
- `detect_liquidity_sweeps()` only checked `self.df["high"/"low"].iloc[-1]` - the single latest candle - against each level. A level swept 20 candles ago and never touched since incorrectly reported `swept=False` "today." Same bug class independently found and fixed in the Market Structure (`BOS_Duplicate_Investigation_Report.md`) and Order Block migrations.
- `swept` was a bare boolean - no grab-vs-breakout distinction, no confirmation state.
- No connection to Market Structure, Order Blocks, or FVGs at all.

**New `LiquidityEngine`:**
- BSL/SSL cover every swing, clustered (`EQUAL_HIGHS`/`EQUAL_LOWS`) or standalone (`SINGLE_SWING`) - nothing is discarded.
- Internal and External scope are both first-class, computed independently from whichever swing tiers the caller supplies.
- Sweep detection is a real forward scan from each level's own formation candle to the end of the window - correctly finds sweeps regardless of how long ago they happened.
- Sweeps are classified GRAB or BREAKOUT based on the first resolving close within a confirmation window, with an honest `sweep_confirmed=False` if the window runs out with no resolving close (never silently assumed True).
- Threads Market Structure/Order Block/FVG data for `reversal_break_level`/`reversal_is_choch`/`has_ob_confluence`/`has_fvg_confluence`.

## 5. Regression Results

Standalone suite (`liq_verification/regression_tests.py`), importing the real production file directly (not a copy) via `importlib.util.spec_from_file_location`, exercising every concept above against targeted synthetic OHLCV fixtures and real `SwingPoint`/`StructureBreak`/`OrderBlock`/`FairValueGap` dataclasses from the other two engines:

**23/23 checks passed**, covering: EQH/EQL clustering, lone-swing BSL (the exact old-engine gap fix), Internal vs External scope (including "empty, not guessed" when internal swings aren't supplied), grab classification, breakout classification, unconfirmed-sweep honesty, resting liquidity, the stale-sweep bug fix (39-candles-later detection), engineered-liquidity flagging (tight vs normal clusters), strength grading (STRONG/MODERATE/WEAK), reversal linkage to opposite-direction structure breaks (including correctly ignoring a same-direction break), OB/FVG confluence tagging, SSL grab mirror, sweep-detection idempotency, timeframe tag passthrough, and no-swings edge case.

## 6. Historical Validation

Ran a rolling-window replay and a full-series comparison against a scratchpad recreation of the deleted old engine (`liq_verification/old_liquidity_reference.py` - read-only, never reachable from production code, recreated verbatim from the file content captured before deletion).

**Stale-sweep bug, demonstrated directly:** a swing high was planted at candle 85, swept once at candle 95 (wicked through, closed back below - a real grab), then never approached again for the rest of a 300-candle series. Checked "today" (end of series): old engine reported **0 levels tracked, 0 swept** (its EQH/EQL-only clustering never even produced a level for this lone swing, and its last-candle-only check couldn't have found the old sweep anyway). New engine reported **3 levels tracked, 1 correctly swept**.

**110-window rolling replay (60-candle warmup):** old engine tracked 0 total levels across all 110 windows in this synthetic series - it never found two swings close enough to cluster as Equal Highs/Lows. New engine tracked 150 levels (1.36/window), of which 150 were `SINGLE_SWING` levels the old engine's design could never produce at all, and correctly flagged 45 sweeps (0.41/window). This is not a fabricated result to flatter the new engine - it's the direct, honest consequence of the old engine's core design gap: in real markets, most individual swing highs/lows never happen to line up as a tight Equal High/Low cluster, so an old engine that only recognizes clusters is blind to the large majority of real resting liquidity.

## 7. AI Impact Analysis

**Disclosed limitation (consistent with the Market Structure and Order Block reports):** `sqlalchemy`/`loguru`/`pytest` are not installed in this sandbox and there is no network access to install them, so `AIScorer.assess()` cannot be called directly. Instead, the exact `liquidity_sweep` scoring formula was copied verbatim from `app/ai/scorer.py` (lines 84-93: `base=62`, `bonus=min(len(swept)*11, 30)` if any level swept, else a flat `45`) and applied read-only to each engine's real output for the same window.

Result on the full 300-candle planted-sweep series: **old engine score = 45.0** (no sweep found), **new engine score = 73** (1 real sweep correctly found: `62 + min(1*11,30) = 73`). Both engines preserve `AIScorer`'s exact `.swept` boolean contract and `.price` field, confirmed directly in `app/ai/scorer.py` - the `liquidity_sweep` block only reads `l.swept`, and the `confluence` block only additionally reads `lvl.price`. **`app/ai/scorer.py` itself required zero changes** - both fields are untouched, unchanged-shape carryovers on the new `LiquidityLevel` dataclass.

## 8. Performance Impact

200 iterations against a 300-candle window, same swings, wall-clock:
- Old engine: 0.058 ms/call
- New engine: 0.251 ms/call
- Ratio: **4.35x slower**

This is the largest per-call ratio of the three ICT migrations (Market Structure ~2-2.9x, Order Blocks ~1.00x), driven by the new engine doing meaningfully more work per level: a full forward sweep scan (vs. one comparison against the last candle), engineered-liquidity tightness checks, strength grading, and structure/OB/FVG confluence lookups. In absolute terms this is still sub-millisecond and is called once per symbol per signal-generation cycle (every 15 minutes, not in a hot loop), so the added cost is negligible in practice - consistent with how the Market Structure engine's larger ratio was assessed.

## 9. Known Limitations

- **Engineered Liquidity is a heuristic, not a certainty.** `LiquidityLevel.engineered` flags Equal-High/Low clusters that are unusually tight relative to the configured clustering tolerance, as a proxy for "this pattern looks deliberately engineered rather than coincidental." This is disclosed in the engine's docstring and should be read as a probabilistic signal, not a guarantee of institutional intent.
- **Sweep confirmation window is a fixed constant** (`DEFAULT_SWEEP_CONFIRMATION_WINDOW = 3`), not wired into `CalibrationProfile` - consistent with how the Order Block Engine's own thresholds were left as plain constants in that migration, since asset-specific tuning of this value was out of scope here.
- **Unconfirmed-sweep default is conservative, not neutral.** When the confirmation window runs out with no resolving close, the engine defaults `sweep_outcome` to `GRAB` but marks `sweep_confirmed=False` - callers that only check `.swept` (like `AIScorer`) will count it as a sweep even though the grab/breakout classification is unconfirmed. This mirrors the old engine's binary `swept` semantics exactly (so `AIScorer` behavior for that field is unchanged) while adding an honest confirmation flag on top for anything that wants to read it.
- **Multi-timeframe compatibility is structural, not orchestrated.** The engine is timeframe-agnostic and tags results with an optional `timeframe` field, but no caller currently runs it across multiple timeframes simultaneously or reconciles cross-timeframe liquidity pools - same disclosed scope boundary as the Order Block Engine's MTF support.
- **`ta_dashboard.py` does not yet surface Internal Liquidity as its own report card**, even though it now has real internal-tier swing data available (`snap_tiers`) and threads it into the engine. Only the existing "Liquidity Sweep"/"Equal Highs"/"Equal Lows" cards (External scope, unchanged from before this migration) were kept, to stay within this task's migration scope rather than adding new dashboard features. `detect_internal_buyside_liquidity()`/`detect_internal_sellside_liquidity()` are fully implemented and regression-tested and available for a future dashboard card if wanted.

## 10. Supply & Demand Dependency Check

Per the explicit stop-condition instruction: **`app/smc/supply_demand.py` was re-read in full during this migration and confirmed to have NO dependency on the Liquidity module, old or new.** `SupplyDemandZones` only reads `df["high"]`/`df["low"]` directly for its own recent-range/premium-discount calculation - it does not import, reference, or otherwise depend on `LiquidityDetector`, `LiquidityLevel`, `LiquidityEngine`, or any Liquidity-derived data. No redesign is required, and none was performed. Supply & Demand remains completely untouched by this migration.

## 11. Verification Summary

- **Compile check:** all 120 `.py` files in the FastAPI Backend tree compile cleanly (`py_compile`), including every migrated caller and the new engine.
- **Import sweep:** zero remaining references to `app.smc.liquidity` or `LiquidityDetector` anywhere in the project outside `liquidity_engine.py`'s own historical docstring.
- **Wiring smoke test:** the real, edited `MarketStructureEngine` -> `FVGDetector`/`OrderBlockEngine` -> `LiquidityEngine` chain was run end-to-end against synthetic OHLCV data through the actual production import paths - swings, BSL/SSL, Internal BSL/SSL, and a confirmed grab sweep with correct confluence/reversal tagging were all produced successfully.
- **Regression suite:** 23/23 passed.
- **Historical replay:** confirms the stale-sweep bug fix and the lone-swing detection gap fix with concrete before/after counts.
- **AI impact:** verbatim-formula comparison confirms `AIScorer` requires zero changes and correctly picks up the new engine's more accurate sweep detection.
- **Performance:** 4.35x slower per call, sub-millisecond in absolute terms, deemed negligible for a once-per-15-minute call site.

**There is now exactly one Liquidity implementation in the project: `app/smc/liquidity_engine.py`. No legacy code, no duplicate logic, no compatibility layer.**
