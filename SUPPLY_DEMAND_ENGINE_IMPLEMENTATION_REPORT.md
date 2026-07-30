# Supply & Demand Engine Implementation Report

**Date:** 2026-07-28
**Scope:** Complete replacement of the legacy Supply & Demand implementation with a true ICT Supply & Demand Engine. Fourth and final module in the SMC-to-ICT migration series (Market Structure -> Order Blocks -> Liquidity -> Supply & Demand).
**Status:** COMPLETE. There is now exactly ONE Supply & Demand implementation in the project: `app/smc/supply_demand_engine.py`.

---

## 1. Files Changed

| File | Change |
|---|---|
| `app/smc/supply_demand_engine.py` | **NEW.** The single production ICT Supply & Demand Engine. A correctness bug found during regression testing (see Section 5) was fixed before this report was written. |
| `app/strategy/signal_generator.py` | Migrated: `SupplyDemandZones` -> `SupplyDemandEngine`. `calculate_recent_range()`/`get_zone()`/`range_high`/`range_low` calls unchanged - pure class-name swap. |
| `app/services/market_scorer.py` | Same drop-in swap. |
| `app/services/ta_dashboard.py` | Migrated, plus upgraded: `structure_breaks`/`order_blocks`/`liquidity_levels`/`fvgs` (all already computed for the Market Structure/Order Block/Liquidity/FVG sections) now thread into the engine, and the "Supply Zones"/"Demand Zones" report cards were rebuilt to show a real nearest non-broken zone (state, strength, confluence) instead of the old flat range endpoint. The "Premium/Discount" card is completely unchanged. |
| `scripts/analyze_smc_frequency.py` | Migrated, same drop-in swap (both the module-level import and the local re-import inside `measure_confidence_impact`). |
| `tests/test_supply_demand.py` | Import and class name updated to `SupplyDemandEngine`; all 4 existing assertions preserved verbatim since the Premium/Discount formula is byte-for-byte unchanged. |
| `app/services/geckoterminal_service.py` | Doc-comment-only fix: stale class name `SupplyDemandZones` corrected to `SupplyDemandEngine`. No functional change - this file never imported the module, only mentioned it in a comment. |

**`app/services/token_scorer.py` and `app/api/v1/endpoints/dashboard.py` were checked and confirmed to have NO Supply & Demand usage at all** - no migration needed in either file.

## 2. Files Removed

- `app/smc/supply_demand.py` - the legacy `SupplyDemandZones` implementation (17 lines: `range_high`/`range_low` + Premium/Discount only, no zone detection whatsoever). Fully deleted.

**Confirmed via project-wide sweep after deletion:** zero remaining imports of `app.smc.supply_demand` or references to `SupplyDemandZones` anywhere in the tree (excluding `.venv`). The only three matches for the old class/file name anywhere are inside `supply_demand_engine.py`'s own module docstring, documenting what was replaced.

## 3. ICT Concepts Implemented

| # | Concept | Where |
|---|---|---|
| 1 | Demand Zones | `find_demand_zones()`: a consolidation base immediately followed by genuine bullish displacement |
| 2 | Supply Zones | `find_supply_zones()`: mirror, bearish displacement |
| 3 | Fresh Zones | `Zone.state == ZoneState.FRESH` - never revisited since formation |
| 4 | Tested Zones | `ZoneState.TESTED` - price wicked into the zone but CLOSED back outside it (touched and respected) |
| 5 | Mitigated Zones | `ZoneState.MITIGATED` - a candle CLOSED inside the zone (some resting orders genuinely consumed) |
| 6 | Broken Zones | `ZoneState.BROKEN` - a candle closed fully through the far side (invalidated, terminal state) |
| 7 | Zone Strength | `Zone.strength` (STRONG/MODERATE/WEAK) - deterministic combination of displacement magnitude and BOS/CHoCH causation |
| 8 | Zone Freshness | Is exactly `Zone.state` - not a separate field, same "don't invent a second concept for the same thing" pattern used for Liquidity Engine's "Liquidity Pools" |
| 9 | Zone Invalidations | `Zone.state == ZoneState.BROKEN` + `Zone.broken_at` - the exact candle, from a real forward scan (not a single-price snapshot) |
| 10 | BOS relationship | `Zone.caused_bos` + `related_break_level`, set when a caller-supplied `structure_breaks` list contains a matching-direction BOS shortly after the zone's displacement leg |
| 11 | CHoCH relationship | `Zone.caused_choch`, same mechanism |
| 12 | Order Block relationship | `Zone.has_ob_confluence` - a caller-supplied Order Block overlaps this zone's price range |
| 13 | Liquidity relationship | `Zone.has_liquidity_confluence` - a caller-supplied Liquidity level sits inside this zone |
| 14 | FVG relationship | `Zone.has_fvg_confluence` - a caller-supplied Fair Value Gap overlaps this zone |
| 15 | Multi-Timeframe compatibility | Timeframe-agnostic by construction; every `Zone` carries an optional `timeframe` tag |

**Integration with the three other completed ICT engines** (all optional constructor args, threaded through wherever a caller has them, same "optional enrichment, honest degradation" pattern used across this entire migration series): `structure_breaks` (Market Structure), `order_blocks` (Order Blocks), `liquidity_levels` (Liquidity), `fvgs` (FVG). When a caller doesn't supply one, the related field stays at its honest default rather than being guessed.

## 4. Before vs After

**Old `SupplyDemandZones` (deleted, 17 lines):** never detected an actual Supply or Demand ZONE at all. `calculate_recent_range()` just took the max high / min low of the last 50 candles as `range_high`/`range_low` - a single flat range, not a base-and-displacement zone. `ta_dashboard.py`'s "Supply Zones"/"Demand Zones" report cards were built directly from this SAME single pair - i.e. "Supply Zone" was literally just "the highest high of the last 50 candles," with no list of distinct zones, no lifecycle, no strength, no relationship to anything else, and no connection whatsoever to Market Structure, Order Blocks, Liquidity, or FVGs.

**New `SupplyDemandEngine`:** detects real base-and-displacement zones (a tight consolidation immediately followed by genuine ATR-relative displacement), tracks a real Fresh -> Tested -> Mitigated -> Broken lifecycle via a full forward scan from each zone's own formation candle, grades strength from displacement magnitude and structural causation, and tags BOS/CHoCH/Order Block/Liquidity/FVG confluence. Critically, it **byte-for-byte preserves** the old engine's Premium/Discount `calculate_recent_range()`/`get_zone()` contract, so nothing consuming that specific output changes.

## 5. A Real Bug Found and Fixed During Regression Testing

The initial zone-scanning loop tried the SHORTEST candidate base first at every candle position, and accepted a match as soon as a peeking multi-candle "displacement window" cleared the ATR threshold - without requiring that window's own first candle to actually be genuine displacement rather than more consolidation. Concretely: a 1-candle "base" consisting of just the FIRST candle of a longer 3-candle consolidation could pass the tightness check, and its 3-candle-ahead "displacement window" could still contain 2 more base-like candles plus only 1 real displacement candle - enough to superficially clear the ATR threshold. This caused the engine to anchor zones on the wrong candle and, worse, made the immediately-following lifecycle scan see more of the SAME consolidation as if it were price "returning" to the zone - **a clean, never-revisited 5x-ATR displacement was being incorrectly reported as MITIGATED at the moment of formation**, confirmed with a concrete synthetic reproduction (`sd_verification/probe_lifecycle_bug.py`).

Fixed by (1) trying the LONGEST candidate base first, not the shortest, so the scanner anchors on the true end of a consolidation run, and (2) adding an explicit guard requiring the candle immediately after the candidate base to NOT itself be base-like (tight-range) before accepting it as genuine displacement. Re-verified against the same reproduction case: the zone now correctly reports `state=fresh` with the full, undiminished displacement ratio. This fix is applied directly in the production file - not a separate patch layer.

## 6. Regression Results

Standalone suite (`sd_verification/regression_tests.py`), importing the real production file directly (not a copy) via `importlib.util.spec_from_file_location`, exercising every concept above against targeted synthetic OHLCV fixtures and real `StructureBreak`/`OrderBlock`/`LiquidityLevel`/`FairValueGap` objects from the other three production engines:

**22/22 checks passed**, covering: clean fresh demand/supply zone detection, a later genuine test (wick in, close out -> TESTED), full breakthrough on both zone types (-> BROKEN), weak displacement below threshold correctly producing no zone, all 5 Premium/Discount backward-compatibility cases (premium/discount/equilibrium/unknown/zero-range, matching the pre-existing `tests/test_supply_demand.py` assertions exactly), correct BOS/CHoCH linkage including correctly ignoring an opposite-direction break, Order Block confluence (including correctly rejecting a non-overlapping OB), Liquidity confluence, FVG confluence, strength grading at both STRONG/MODERATE and WEAK ends, timeframe tag passthrough, empty/flat-data edge case, and sweep-detection idempotency (stateless-per-call determinism).

All four engines' regression suites (Market Structure 17/17, Order Blocks 21/21, Liquidity 23/23, Supply & Demand 22/22) were also re-run together as a final combined sanity check - all still pass.

## 7. Historical Validation

**Premium/Discount backward-compatibility, empirically confirmed across 340 rolling windows** (60-candle warmup, 400-candle synthetic series): **0 mismatches** between the old and new engine's `zone`/`range_high`/`range_low` output at every single step. This is the exact contract `app/ai/scorer.py`'s `supply_demand_zone` block depends on.

**New zone-detection capability, demonstrated concretely:** the old engine has no equivalent output whatsoever for real zones - `range_high`/`range_low` is a single flat pair, not a list of zones with their own state, strength, or relationships. Against the same 400-candle series (with 2 explicitly planted clean base-and-displacement formations), the new engine correctly found a real demand zone at `[122.447, 123.175]`, state `TESTED` (a later candle genuinely wicked in and closed back out), strength `WEAK`, displacement ratio 1.43 - a result the old engine's design could never produce under any circumstances.

## 8. AI Impact Analysis

**Disclosed limitation (consistent with all three prior reports in this series):** `sqlalchemy`/`loguru`/`pytest` are not installed in this sandbox and there is no network access to install them, so `AIScorer.assess()` cannot be called directly. Instead, the exact `supply_demand_zone` scoring formula was copied verbatim from `app/ai/scorer.py` (lines 206-222) and applied read-only to both engines' real output.

Result on a real sample window: **old engine score = 31.4** (zone=premium, zone_position=0.9792), **new engine score = 31.4** (identical zone and zone_position values). **`app/ai/scorer.py` requires ZERO changes.** This is a stronger, cleaner finding than the prior three migrations: because `calculate_recent_range()`/`get_zone()` are byte-for-byte identical to the deleted engine, this migration produces **zero change to any live confidence score** from AI Scoring's `supply_demand_zone` category - confirmed both by the 340-window mismatch count (Section 7) and by this direct formula-level comparison. Per the mission's explicit stop condition, this finding means **no AI Scoring or Calibration redesign is required or was performed**, and none is being requested for approval - the migration is fully self-contained.

## 9. Performance Impact

200 iterations against a 60-candle window, wall-clock:
- Old engine (Premium/Discount only): 0.236 ms/call
- New engine (Premium/Discount only, same two methods): 0.197 ms/call (0.84x - effectively identical, the formula is unchanged)
- New engine (+ real zone detection, both directions): **203.246 ms/call (861x vs the Premium/Discount-only baseline)**

This is a much larger ratio than any of the prior three engine migrations, and is disclosed honestly rather than minimized: real zone detection is genuinely more expensive than the old engine's single min/max read, because the scanner re-evaluates a local ATR window at every candle/base-length combination using pandas slicing, and each candidate base is checked against multiple base lengths. In `signal_generator.py`, `market_scorer.py`, and `scripts/analyze_smc_frequency.py`, this cost does not apply at all - those callers only use the unchanged Premium/Discount methods (0.2ms, no change). It DOES apply in `ta_dashboard.py`, which now calls `find_demand_zones()`/`find_supply_zones()` once per dashboard build. ~200ms is a real, noticeable addition to a dashboard API response and should be watched - see Known Limitations below for a disclosed follow-up recommendation.

## 10. Known Limitations

- **Zone detection performance (~200ms/call) is only exercised by `ta_dashboard.py`**, and only there. It is a real, disclosed cost, not optimized away in this migration - the scope here was correctness and a complete, verified cutover, not micro-optimization. A future pass could vectorize the local-ATR/base-tightness scan (currently one pandas slice+mean per candle/base-length combination) if this response time becomes a practical concern; this is flagged as a follow-up, not fixed here.
- **The 4-state lifecycle (Fresh/Tested/Mitigated/Broken) is a defensible but not universally standardized ICT interpretation.** Some ICT material treats "tested" and "mitigated" as interchangeable; this engine deliberately distinguishes them (TESTED = wicked in, closed back outside, respected; MITIGATED = a candle actually closed inside the zone, genuinely consumed) since the mission's objective list named both as separate concepts to support. This distinction is documented in the engine's own module docstring.
- **The zone-detection algorithm requires the base to be the maximal valid consolidation ending exactly where real displacement begins** (see Section 5's fix). This is a sound, tested design, but like the other three engines' own displacement/momentum thresholds, the specific constants (`DEFAULT_MAX_BASE_CANDLES=3`, `DEFAULT_BASE_BODY_ATR_RATIO=0.5`, `DEFAULT_MIN_DISPLACEMENT_ATR=1.5`) are plain, disclosed constants, not wired into `CalibrationProfile` for asset-specific tuning - consistent with how the Order Block and Liquidity engines' own thresholds were left as constants in their respective migrations.
- **`ta_dashboard.py`'s upgraded "Supply Zones"/"Demand Zones" cards show only the single NEAREST non-broken zone per side**, not the full list. The complete zone list (with all lifecycle states and confluence flags) is available from `find_demand_zones()`/`find_supply_zones()` for any future consumer that wants the full picture (e.g. a future Trading Agent Evidence Engine extension) - this is a presentation choice for the existing single-card dashboard layout, not an engine limitation.
- **Multi-timeframe compatibility is structural, not orchestrated** - same disclosed scope boundary as all three prior engines in this series: the engine is timeframe-agnostic and tags results with an optional `timeframe` field, but no caller currently runs it across multiple timeframes simultaneously.

## 11. Verification Summary

- **Compile check:** all 120 `.py` files in the FastAPI Backend tree compile cleanly (`py_compile`), including every migrated caller and the new engine.
- **Import sweep:** zero remaining references to `app.smc.supply_demand` or `SupplyDemandZones` anywhere in the project outside `supply_demand_engine.py`'s own historical docstring.
- **Wiring smoke test:** the real, edited `MarketStructureEngine` -> `FVGDetector`/`OrderBlockEngine` -> `LiquidityEngine` -> `SupplyDemandEngine` chain was run end-to-end against synthetic OHLCV data through the actual production import paths - a real demand zone was produced with correct FVG confluence tagging.
- **Regression suite:** 22/22 passed, including a real bug caught and fixed before shipping.
- **Combined regression run:** all four ICT engines (Market Structure, Order Blocks, Liquidity, Supply & Demand) pass together: 17/17, 21/21, 23/23, 22/22.
- **Historical replay:** 0/340 mismatches confirms Premium/Discount is byte-for-byte preserved; a planted-zone demonstration confirms real zone detection produces output the old engine could never produce.
- **AI impact:** verbatim-formula comparison confirms `AIScorer` requires zero changes and produces an identical score before and after this migration.
- **Performance:** Premium/Discount methods unchanged (0.2ms); real zone detection adds a disclosed ~200ms/call, isolated to `ta_dashboard.py` only.

**There is now exactly one Supply & Demand implementation in the project: `app/smc/supply_demand_engine.py`. No legacy code, no duplicate logic, no compatibility layer.**

**AI Scoring / Calibration dependency check (per the mission's explicit stop condition):** confirmed empirically (Sections 7-8) that this migration requires **zero changes** to `app/ai/scorer.py` or any calibration module - the Premium/Discount contract those modules depend on is byte-for-byte preserved. No redesign was discovered to be necessary, none was performed, and none is being requested. **This completes the full 4-module ICT migration series** (Market Structure, Order Blocks, Liquidity, Supply & Demand) approved across this and the three preceding mission messages.
