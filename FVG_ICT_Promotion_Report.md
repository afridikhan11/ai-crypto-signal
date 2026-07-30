# FVG ICT Promotion — Implementation Report

**Approved scope:** remove the legacy 2-candle FVG detector; promote the ICT 3-candle detector (`detect_fvg_ict()`) to be the one and only `detect_fvg()`; update every internal caller; remove now-obsolete legacy-only code. No change to the ICT algorithm itself, thresholds, AI scoring, calibration, or any other SMC module.
**Date:** 2026-07-28
**Related investigation:** `BOS_ICT_Audit_Report.md`, `BOS_Duplicate_Investigation_Report.md`, and the earlier FVG comparison analysis (`smc_frequency_report_20260727_230446.json`), which found the legacy detector produced zero FVG detections across all 9 tracked symbols.

---

## Files changed

### 1. `app/smc/fvg.py`

- **Removed** the legacy `detect_fvg()` method (the 2-candle adjacent-gap check).
- **Renamed** `detect_fvg_ict()` → `detect_fvg()`. It is now the class's only gap-detection method.
- **Rewrote its docstring** to describe it as the production implementation (previously it was written as "additive, currently-unused, comparison-only"), and to explain why the legacy method was removed rather than kept alongside it.
- **No other code in the file changed.** `_has_been_filled()`, `is_fvg_filled()`, and `detect_inverse_fvg()` are untouched — `detect_inverse_fvg()` already called `self.detect_fvg()` by name, so it required no edit to pick up the promoted algorithm.

**Why required:** this is the direct implementation of the approval — one file, one method, one algorithm, exactly as instructed.

### 2. `scripts/analyze_smc_frequency.py`

- **Removed** `FVG_ALGO_METHODS` (the `{"current": "detect_fvg", "ict": "detect_fvg_ict"}` mapping) and its introductory comment.
- **Removed** `measure_fvg_algorithm_comparison()` — the function that ran both algorithms side by side and tracked their gap lifecycles.
- **Removed** `measure_fvg_confidence_distribution()` — the function that ran the AI Scorer twice per step, once per algorithm, to compare confidence distributions.
- **Removed** `_fmt()` and `_print_fvg_comparison_report()` — display-only helpers that existed solely to print the two-algorithm comparison (Part 3 of the report).
- **Updated** `analyze_symbol()` to stop calling the two removed measure functions and stop attaching `fvg_algorithm_comparison`/`fvg_confidence_distribution` to its returned dict.
- **Updated** `main()`: removed the call to `_print_fvg_comparison_report()`, and removed the now-nonexistent keys from the exception-handler's fallback result dict (previously it would have raised a `KeyError`-adjacent inconsistency by referencing removed data on a failure path).
- **Removed** the now-unused `import time` (only used inside the removed Part 3 functions for wall-clock timing).
- **Left completely unchanged:** `measure_smc_frequency()` (Part 1 — raw SMC frequency counting, calls `FVGDetector(window).detect_fvg()` at 2 call sites), `measure_confidence_impact()` / `_build_signal_data()` (Part 2 — confidence/win-rate impact), `_print_report()` (Part 1/2 printing), and `main()`'s CLI argument handling / JSON-writing logic.

**Why required:** this script is an internal caller of `FVGDetector` (task 4's "update every internal caller"). Its entire "Part 3" section existed only to compare the legacy detector against the ICT candidate — once there is only one algorithm, that comparison has nothing left to compare (both sides would be identical by construction), which is exactly the "obsolete code that only existed for the legacy detector" the approval called out for removal. Leaving it in place would have caused an `AttributeError` the next time the script ran, since it called `getattr(FVGDetector(window), "detect_fvg_ict")`, a name that no longer exists.

### 3. No changes needed (verified, not assumed)

| File | FVG usage | Why no change was needed |
|---|---|---|
| `app/strategy/signal_generator.py` | `FVGDetector(df).detect_fvg()` | Calls the method by name — automatically resolves to the promoted ICT algorithm |
| `app/services/market_scorer.py` | `FVGDetector(df).detect_fvg()` | Same |
| `app/services/ta_dashboard.py` | `FVGDetector(df).detect_fvg()`, `.detect_inverse_fvg()` | Same — `detect_inverse_fvg()` also unaffected, see above |
| `app/services/token_scorer.py` | `FVGDetector(df).detect_fvg()` | Same |
| `tests/test_ai_scorer.py`, `tests/test_calibration.py` | Construct `FairValueGap`/`FVGType` objects directly | Never call `.detect_fvg()` or `.detect_fvg_ict()` — unaffected by the rename |
| `setup_module2.sh` | Contains a heredoc-embedded copy of the old `FVGDetector` class (from original project scaffolding) | Not Python, not imported, not executed by the running application (confirmed in the earlier `Market_Structure_Dependency_Report.md` dependency trace for the equivalent `market_structure.py` case) — left untouched as an inert historical artifact, consistent with the standing "never delete/refactor without approval" policy; this specific removal was not part of the approved scope |

---

## Verification

### Compile check

```
python3 -m py_compile app/smc/fvg.py scripts/analyze_smc_frequency.py \
    app/strategy/signal_generator.py app/services/market_scorer.py \
    app/services/ta_dashboard.py app/services/token_scorer.py
→ PY_COMPILE OK

find app scripts tests -name "*.py" | xargs -n1 python3 -m py_compile
→ zero errors across the full backend tree
```

### Regression tests

The sandbox has no `httpx`/network egress to Binance (a pre-existing limitation, not something this change introduced — `scripts/analyze_smc_frequency.py` is documented to require real network access to `fapi.binance.com` and cannot be run end-to-end from here). To verify as much as is possible without that access, the real, unmodified `app/smc/fvg.py` was imported directly (not stubbed, not copied) and exercised against realistic synthetic 15m-style candle data:

```
PASS: detect_fvg_ict no longer exists as a separate method
PASS: detect_fvg() body is the ICT 3-candle algorithm (range(2, len(df)), not range(1, len(df)))
detect_fvg() found 450 FVGs on 700 synthetic candles (was ALWAYS 0 with the legacy detector)
PASS: detect_fvg() now returns real, non-zero detections
detect_inverse_fvg() ran without error, returned 7 inverse FVGs
PASS: detect_inverse_fvg() still functions correctly against the promoted method
PASS: FairValueGap.filled is computed correctly (bool) on sample gaps

ALL FVG PROMOTION REGRESSION CHECKS PASSED
```

`tests/test_ai_scorer.py` and `tests/test_calibration.py` were inspected and confirmed to construct `FairValueGap` objects directly rather than calling either detection method — they are unaffected by this change and require no update.

### `analyze_smc_frequency.py`

Full end-to-end execution (real Binance data) could not be run from this sandbox for the reason above. What was verified instead:
- The script now compiles and its structure is consistent (581 lines, down from 979 — the removed Part 3 functions accounted for the difference; `measure_smc_frequency`, `measure_confidence_impact`, `_build_signal_data`, `analyze_symbol`, `_avg`, `_print_report`, and `main` are the only top-level functions remaining, matching the intended Part 1/Part 2-only shape).
- `measure_smc_frequency()` (Part 1 — the function whose `fvg_new_per_100_steps`/`fvg_present_per_100_steps` fields are the ones referenced by "confirm FVG detections are no longer zero") was not modified, and it calls `FVGDetector(window).detect_fvg()` exactly as before — the regression test above proves that exact call pattern (`FVGDetector(...).detect_fvg()`) now returns real detections on realistic data.
- **This is evidence the fix will produce non-zero results, not a substitute for running the real script.** Please run `python scripts/analyze_smc_frequency.py --days 60` yourself (as its own docstring instructs) to get the final, real confirmation against live Binance data across all 9 tracked symbols.

---

## Confirmation: single ICT-standard FVG implementation only

- `FVGDetector` now exposes exactly one gap-detection method: `detect_fvg()`, implementing the standard ICT 3-candle definition (compares candle `i-2` to candle `i`, skipping the middle displacement candle).
- No file in the project (excluding the inert, non-executed `setup_module2.sh` scaffold noted above) references `detect_fvg_ict` as a callable method anymore — confirmed by a full-project search.
- Every production caller (`signal_generator.py`, `market_scorer.py`, `ta_dashboard.py`, `token_scorer.py`) and the diagnostic script now go through this one method.
- The AI Scorer, calibration system, and every other SMC module (`market_structure.py`, `liquidity.py`, `order_blocks.py`, `supply_demand.py`) were not touched, per the approval's explicit constraints.

Waiting for your review before any further SMC module work.
