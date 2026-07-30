# Order Block Engine — Implementation Report

**Date:** 2026-07-28
**Role:** Chief ICT/SMC Architect (approved implementation)
**Status:** Complete. Only one Order Block engine now exists in the project, and every production caller uses it.

---

## 1. Mission

Replace the legacy `app/smc/order_blocks.py` completely with a professional ICT Order Block Engine (`app/smc/order_block_engine.py`). Not a patch — a full replacement, built after a direct read of the old implementation against real ICT Order Block methodology. The old file has been deleted; no compatibility shim exists.

## 2. What was wrong with the old implementation (evidence-based)

Direct reading of `app/smc/order_blocks.py` found it detected "the last opposite-direction momentum candle before a confirming momentum candle" — a legitimate starting point, but missing everything that makes an Order Block an *ICT* Order Block:

- **Zero displacement validation** beyond a single candle's body/range ratio ≥ 0.6. No measure of how large the confirming move actually was.
- **Zero connection to Market Structure.** `find_bullish_order_block()`/`find_bearish_order_block()` took no BOS/CHoCH context at all — direction was decided entirely by the caller, and the method just hunted for the nearest matching candle pair, regardless of whether it ever caused a real structural break.
- **`check_mitigation(ob, current_price)` was a single-price snapshot**, not a scan of history. An OB that was genuinely tested and left days ago could report "not mitigated" simply because the current price happened to be elsewhere at the moment of the call.
- **`detect_breaker_block(ob, current_price)`** was a redundant re-check of logic already computed internally by `_is_ob_broken()` — not a real "find me the breaker zones" feature.
- **`app/services/ta_dashboard.py` had built a second, completely separate breaker-block detector** (`_find_broken_order_block()`, with its own copied momentum-candle check) — genuine duplicate logic, already flagged in `SMC_ICT_MASTER_MIGRATION_PLAN.md`'s Phase 2.
- **`BlockType.BREAKER` and `BlockType.MITIGATION`** were enum members that were never actually assigned anywhere in the codebase — dead code.

## 3. The new engine: `app/smc/order_block_engine.py`

One class, `OrderBlockEngine`, stateless per call (same design philosophy as `MarketStructureEngine`): calling it twice with the same inputs returns identical results. It optionally accepts `structure_breaks` (from `MarketStructureEngine.analyze()`) and `fvgs` (from `FVGDetector.detect_fvg()`) — real data every caller already computes before Order Block detection — to attach genuine ICT relationships instead of guessing them.

### ICT concepts implemented

| # | Item | Implementation |
|---|------|-----------------|
| 1 | Bullish Order Blocks | `find_bullish_order_block()` — same base candle-pair definition as before (not evidenced as broken, so preserved), now displacement-validated and lifecycle-tracked |
| 2 | Bearish Order Blocks | `find_bearish_order_block()`, mirror |
| 3 | Valid displacement | `OrderBlock.displacement_ratio` — the confirming candle's move past the OB's invalidation edge, in local-ATR terms. Optional `min_displacement_atr` gate (default 0.0, reproduces old "any qualifying pair counts" behavior) |
| 4 | BOS relationship | `OrderBlock.caused_bos` + `related_break_level`, set when a caller-supplied `structure_breaks` list contains a matching-direction BOS within a configurable lookahead window (default 20 candles) after the OB formed |
| 5 | CHoCH relationship | `OrderBlock.caused_choch`, same mechanism |
| 6 | Mitigation detection | A real forward scan of the entire window after the OB formed — `mitigated_at` is the first candle whose close ever traded back into the zone, not a snapshot of "right now" |
| 7 | Breaker Blocks | `find_bullish_breaker_block()` / `find_bearish_breaker_block()` — a former OB that was violated and flipped role. Direction convention spelled out explicitly in the docstrings since it's easy to get backwards (a "bullish breaker" is a former *bearish* OB) |
| 8 | Invalidated Order Blocks | `OrderBlock.state == OBState.INVALIDATED` — a breaker that was itself later broken again (price reclaimed back through it), fully dead |
| 9 | Fresh vs Mitigated OBs | `OrderBlock.state` (FRESH / MITIGATED / BREAKER / INVALIDATED) — a real lifecycle, computed once per `analyze()` call from the full window, not re-derived from a single boolean each time |
| 10 | Order Block strength | `OrderBlock.strength` (STRONG / MODERATE / WEAK) — simple, deterministic combination of displacement magnitude, BOS/CHoCH causation, and FVG confluence. Documented arithmetic, not a black box — same philosophy as `ta_dashboard.py`'s existing scoring sections |
| 11 | Multi-Timeframe compatibility | The engine takes any OHLCV `df` with no hardcoded timeframe assumptions (unchanged from the old engine in this respect), and every `OrderBlock` carries an optional `timeframe` tag for future cross-timeframe comparison — see Known Limitations for why this wasn't wired into an actual MTF feature |

### Backward-compatible fields

`OrderBlock.type` is still a `BlockType` enum with the exact same two string values the old engine used (`"bullish_ob"` / `"bearish_ob"`) — `app/ai/scorer.py` (out of scope for this migration) reads these two exact strings and needed zero changes. `OrderBlock.mitigated` is still a plain bool (`True` whenever `state != FRESH`) for the same reason.

## 4. Files changed

| File | Change |
|---|---|
| `app/strategy/signal_generator.py` | Import swapped; `OrderBlockDetector` → `OrderBlockEngine(df, structure_breaks=breaks, fvgs=fvgs)`; FVG detection reordered to run before Order Block detection so `fvgs` is available for confluence tagging |
| `app/services/market_scorer.py` | Same pattern (this module intentionally duplicates `signal_generator.py`'s feature-building block — see its own docstring) |
| `app/services/token_scorer.py` | Same pattern inside `_run_technical_analysis()`; `_compute_levels()` needed no changes (`ob.type`/`.low`/`.high` fields unchanged) |
| `app/services/ta_dashboard.py` | Same pattern, plus: removed the duplicate `_is_momentum_candle()`/`_find_broken_order_block()` helpers entirely, replaced with `ob_engine.find_bullish_breaker_block()`/`find_bearish_breaker_block()`; the "Mitigation Blocks" card's status wording updated from "Price Inside X Order Block" to "X Order Block Mitigated" to honestly reflect the corrected persisted-lifecycle semantic (see section 6) |
| `scripts/analyze_smc_frequency.py` | Same pattern in both `measure_smc_frequency()` and `measure_confidence_impact()` |

## 5. Files removed

**`app/smc/order_blocks.py`** — deleted entirely. Zero production references remain (verified in section 8). No compatibility layer or re-export shim was added or needed.

## 6. Design decisions and why

- **FVG detection was reordered to run before Order Block detection in every caller.** It was previously computed after OBs in all four real callers. This is a small, low-risk reordering (FVGDetector has no dependency on Order Blocks) that lets the new engine check FVG confluence — a real ICT concept the objectives explicitly asked for, not achievable without this change.
- **`structure_breaks` and `fvgs` are both optional, not required.** Every real production caller already has both on hand at the point it calls Order Block detection, so they're threaded through everywhere it's safe to do so. When omitted, the engine still finds real, displacement-validated Order Blocks — it just reports `caused_bos=False`, `caused_choch=False`, `has_fvg_confluence=False` honestly rather than guessing a relationship that wasn't checked.
- **No hard gate was added requiring BOS/CHoCH causation for an OB to be returned at all.** This was a deliberate choice: making causation mandatory would have been a much stricter filter, dramatically changing how often `SignalGenerator`'s hard gates fire — a cascading behavior change to a module (SignalGenerator's gating logic) not authorized for redesign in this task. Instead, causation is exposed as data (`caused_bos`/`caused_choch`) and folds into `strength`, so callers and future work can use it without this migration silently changing who gets a signal today.
- **Mitigation semantics were corrected, not just relabeled.** The old `check_mitigation(ob, current_price)` only ever answered "is price inside the zone right now." The new `mitigated` field answers "has price ever traded back into this zone since it formed" — a real difference (see section 9's replay data: the old check reported 0 mitigated OBs across an entire 75-tick replay, because "currently inside" is a narrow, mostly-false condition; the new engine correctly identified all 11 as mitigated). This is listed as a fix, not a redesign, because it's the same field serving the same purpose, computed correctly.
- **Breaker Block direction convention was made explicit and correct.** A "bullish breaker" is a former *bearish* OB that got violated and flipped to a bullish (support) role — this is real ICT terminology, and it's easy to get backwards. Both finder methods' docstrings spell this out.
- **`ta_dashboard.py`'s duplicate breaker-block detector is gone.** `find_bullish_breaker_block()`/`find_bearish_breaker_block()` now does that job as a first-class, correctly life-cycled engine feature instead of a local, independently-maintained copy.

## 7. Verification performed

### 7.1 Compile check
Every `.py` file in the backend tree (120 files, excluding `.venv`) compiles clean with `python3 -m py_compile`, including all 5 modified files and the new engine.

### 7.2 Regression tests
21 targeted checks were run against the real, unmodified `order_block_engine.py` (imported via `importlib` from its production path), covering all 11 ICT items:

```
PASS: Bullish OB detected on a down-then-strong-up sequence
PASS: Bullish OB has correct BlockType string value
PASS: Bullish OB displacement_ratio is a real, non-negative number
PASS: Bullish OB with no structure_breaks supplied reports caused_bos=False (honest, not guessed)
PASS: Bullish OB with no fvgs supplied reports has_fvg_confluence=False
PASS: Bullish OB starts FRESH on a clean breakout with no retest
PASS: Bullish OB .mitigated bool matches state != FRESH
PASS: Bearish OB detected on an up-then-strong-down sequence
PASS: Bearish OB has correct BlockType string value
PASS: Same OB candle found with or without structure_breaks supplied
PASS: BOS/CHoCH relationship: caused_bos or caused_choch becomes True when a real matching break is supplied
PASS: Strength is upgraded (or equal) when a real structural relationship is found vs when none is supplied
PASS: FVG confluence: has_fvg_confluence becomes True when a matching-direction FVG is supplied in the same leg
PASS: Lifecycle: stays FRESH when price never returns to the zone
PASS: Lifecycle: MITIGATED when price closes back inside the zone
PASS: Lifecycle: BREAKER when price closes fully below the bullish OB's low
PASS: Lifecycle: INVALIDATED when a breaker is later reclaimed back through in the original direction
PASS: Bullish breaker finder returns a former BEARISH OB (type stays bearish_ob, state is BREAKER)
PASS: Bearish breaker finder returns a former BULLISH OB (type stays bullish_ob, state is BREAKER)
PASS: Displacement gate at 0.0 (default) reproduces ungated behavior (backward compatible)
PASS: Displacement gate at an extreme threshold suppresses weak/insufficient OBs

TOTAL: 21 passed, 0 failed
```

### 7.3 Wiring smoke tests
Two smoke tests executed the actual edited code paths (not a copy) end-to-end against synthetic OHLCV data: `signal_generator.py`'s full chain (`MarketStructureEngine` → `LiquidityDetector` → `FVGDetector` → `OrderBlockEngine` → `detect_chart_pattern` → swing-based SL/TP) and `ta_dashboard.py`'s Order Block + breaker-finder pattern. Both ran without error. One run usefully demonstrated the lifecycle logic in situ: `find_bearish_order_block()` correctly returned `None` on a window where the only candidate bearish OB had already flipped to a bullish breaker — exactly the intended "don't return a zone that's no longer serving its original role" behavior, not a bug.

### 7.4 Historical validation / backtesting-style replay
A 492-candle synthetic multi-regime series was replayed as 75 rolling 120-candle windows (step 5), comparing the OLD engine (recreated read-only from this session's pre-deletion read of the file, for comparison only — never restored to production) against the NEW engine:

```
OLD engine - bullish OB found: 11/75 ticks (14.7%)
OLD engine - of those, "mitigated" by single-price-snapshot check: 0
NEW engine - bullish OB found: 11/75 ticks (14.7%)
NEW engine - lifecycle breakdown: FRESH=0 MITIGATED=11 BREAKER(skipped)=0 INVALIDATED(skipped)=0
NEW engine - of found OBs, caused a real BOS/CHoCH: 0/11
NEW engine - of found OBs, had FVG confluence: 11/11
```

Same candles found in both engines (the base detection logic is deliberately unchanged) — the difference is entirely in what the new engine now knows *about* each one. The OLD engine's mitigation check never once reported a mitigated OB across the whole replay (a live-price snapshot is rarely exactly inside a historical zone); the NEW engine correctly identified all 11 as having been tested at some point, which is the real answer. Zero of the 11 OBs in this particular synthetic series happened to cause a tracked BOS/CHoCH within the 20-candle lookahead window — a real, disclosed finding about this dataset's shape, not a defect (confirmed separately in the regression suite that causation tagging does fire correctly when a genuine matching break exists). FVG confluence was found on all 11 — plausible but likely influenced by this synthetic series' small, consistent candle wicks producing frequent 3-candle FVGs; not claimed as representative of real market data.

### 7.5 AI impact analysis
**Disclosure:** the real `AIScorer.assess()` could not be executed in this sandbox for the same reason documented in `MARKET_STRUCTURE_ENGINE_IMPLEMENTATION_REPORT.md` (no `sqlalchemy`, no network to install it). The exact `order_block_quality` scoring block was copied verbatim from `app/ai/scorer.py` and applied read-only:

```
Average score delta (NEW - OLD) across 10 sampled ticks: +1.6
Ticks where NEW score differs from OLD: 1/10
```

The single tick that differed did so because the corrected `mitigated` field changed from `False` (old snapshot check) to `True` (new lifecycle scan) for the same OB — moving that tick's `order_block_quality` score from the "unmitigated" base (58) to the "mitigated" base (74) in the unmodified `scorer.py` formula. This confirms the migration's behavior change is exactly where it should be — the correctness fix — not an unrelated drift in scoring.

### 7.6 Performance comparison
200 repeated calls on the same 120-candle window:

```
OLD engine: 12.102ms/run
NEW engine: 12.133ms/run
NEW/OLD ratio: 1.00x
```

No meaningful performance regression. The added work (displacement measurement, structure-break matching, FVG matching, lifecycle scan) is small relative to the shared backward candle-scan both engines perform.

## 8. Confirmation: no production module uses the old logic, no duplicate implementation exists

- `app/smc/order_blocks.py`: **deleted.**
- Project-wide search (`app/`, `scripts/`, excluding `.venv`/`.git`/`__pycache__`) for `from app.smc.order_blocks import` or equivalent: **zero matches** outside historical comments (in `app/ai/scorer.py`, untouched, and `app/strategy/signal_generator.py`'s own bug-fix-history comment) and the already-established inert `setup_module2.sh` scaffold (same status as documented in `MARKET_STRUCTURE_CUTOVER_REPORT.md` — not executed by the live application).
- `grep` for `from app.smc.order_block_engine import` confirms exactly the 5 expected files use it: `signal_generator.py`, `market_scorer.py`, `token_scorer.py`, `ta_dashboard.py`, `analyze_smc_frequency.py`.
- `ta_dashboard.py`'s previously-duplicate breaker-block detector is gone — confirmed by the same grep sweep finding no local `_find_broken_order_block`/`_is_momentum_candle` definitions remaining in that file.

## 9. What was NOT modified (explicit confirmation)

Per the standing instruction, checked and confirmed:
- **Liquidity** (`app/smc/liquidity.py`) — byte-identical to before this task (checksum verified); it consumes `SwingPoint` from Market Structure, has no coupling to Order Blocks at all.
- **Supply & Demand** (`app/smc/supply_demand.py`) — byte-identical, zero coupling.
- **AI Scoring** (`app/ai/scorer.py`) — byte-identical (checksum matches the value recorded in the prior Market Structure cutover report). It already only reads `.get("type")`, `.get("high")`, `.get("low")`, `.get("mitigated")` off a plain dict, all of which the new engine's callers still supply in exactly that shape.
- **Calibration** (`app/ai/calibration.py`, `calibration_profiles.py`) — untouched.
- **Trading Agent, Database, API** — untouched.

No dependency was found requiring Liquidity or Supply & Demand to be redesigned — this report is not a stop/escalation notice.

## 10. Known limitations

- **Displacement measurement is single-candle.** `displacement_ratio` looks at the confirming candle's move past the OB edge, not the full impulsive leg (which can span several candles). A fuller treatment would sum displacement across the whole leg until the next retracement; deferred as a possible future refinement, not required by the objectives as stated.
- **BOS/CHoCH causation uses a fixed 20-candle lookahead window**, a plain documented constant (not wired to `CalibrationProfile`, which is out of scope). A structure break more than 20 candles after the OB formed is not linked, even if it's genuinely the same leg on a slower-moving symbol. This threshold is disclosed and easily adjustable if real outcome data later suggests a different value.
- **FVG confluence checks for a matching-direction FVG anywhere in the same lookahead window**, not strictly "the exact 3 candles of the displacement leg." This is a reasonable proxy given how `FVGDetector` and `OrderBlockEngine` are deliberately decoupled (neither imports the other), but is looser than a hand-verified "same leg" check would be.
- **No hard gate ties Order Block validity to BOS/CHoCH causation** (see section 6) — by design, to avoid an unauthorized cascading change to `SignalGenerator`'s gating behavior. This means a caller wanting "only ICT-strict, structurally-confirmed Order Blocks" must filter on `caused_bos`/`caused_choch` itself; the engine returns displacement-validated candidates either way.
- **Multi-Timeframe compatibility is structural, not orchestrated** — the engine works on any timeframe's data and tags results with an optional `timeframe` label, but no caller currently runs it across multiple timeframes and merges results. Building that orchestration wasn't requested by any existing caller and was left out to avoid scope creep beyond "upgrade Order Blocks."
- **AI impact analysis (7.5) and historical validation (7.4) both use disclosed synthetic data**, not real Binance history, for the same sandbox reason established throughout this project (no network egress, no pip install access).

---

**Files in this delivery:**
- `app/smc/order_block_engine.py` (new, production, 452 lines)
- 5 files modified (section 4)
- 1 file removed: `app/smc/order_blocks.py`
- `ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md` (this report)
