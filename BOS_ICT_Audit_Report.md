# BOS / ICT Compliance Audit Report

**Scope:** `app/smc/market_structure.py` (class `MarketStructure`, methods `_detect_swings`, `_classify_swings`, `detect_bos_choch`)
**Method:** Full read of the target file, plus every real call site found in the prior dependency trace, plus the existing `smc_frequency_report_20260727_230446.json` diagnostic data (9 symbols, 5,761 steps each). No code was modified, refactored, or optimized.
**Date:** 2026-07-28
**Related prior work:** `Market_Structure_Dependency_Report.md` (confirms this is the sole, production structure-detection engine) and the earlier FVG audit (confirmed the live FVG detector implements a 2-candle gap, not the ICT 3-candle definition).

---

## 1. Complete read of `app/smc/market_structure.py`

The file defines three things:

- `SwingType` (enum: HH, HL, LH, LL)
- `SwingPoint` (a detected pivot: timestamp, price, type, index)
- `StructureBreak` (a detected break: timestamp, type "BOS"/"CHoCH", direction, the swing it broke, the level)
- `MarketStructure` (the class), with methods `_detect_swings()`, `_classify_swings()`, and `detect_bos_choch()`

Every line was read and is accounted for in Sections 2–7 below.

---

## 2. How BOS is currently detected — plain-English trading logic

**Step 1 — Find pivots.** The detector scans every candle (except the first and last `pivot_window` candles, default 5) and marks a candle as a "swing high" if its high is the highest high in the surrounding 11-candle window (5 before, 5 after), and a "swing low" if its low is the lowest low in that same surrounding window. This is a standard fractal/centered-pivot method — nothing unusual here.

**Step 2 — Label each pivot.** Going through the swing highs in time order, each one is labeled Higher High (HH) if it's above the previous swing high, otherwise Lower High (LH). Swing lows are labeled the same way: Higher Low (HL) if above the previous swing low, otherwise Lower Low (LL). The very first swing high is always labeled HH and the very first swing low is always labeled HL by default (there's nothing earlier to compare to).

**Step 3 — Check for a break, using only the last 3 swings on each side.** The detector looks at the 3 most recent swing highs and the 3 most recent swing lows, and compares them to the CURRENT candle's closing price (not the wick):

- **Bullish BOS:** if any of the last 3 swing highs is a Lower High (LH) and the current close is above that LH's price, a bullish BOS is flagged, using the price level of that LH.
- **Bearish BOS:** if any of the last 3 swing lows is a Higher Low (HL) and the current close is below that HL's price, a bearish BOS is flagged.

**Step 4 — Check for a CHoCH (trend change), using only the last 2 swings on each side.** This looks for a very specific two-swing pattern immediately before the break:
- **Bearish CHoCH:** the last 2 swing lows were both HL AND the last 2 swing highs were both HH (i.e., price was in a clean uptrend) — but now the close has dropped below the most recent HL. This says "the uptrend's last higher-low just failed."
- **Bullish CHoCH:** the mirror image — the last 2 swing highs were both LH AND the last 2 swing lows were both LL (a clean downtrend), but now the close has risen above the most recent LH.

**In one sentence:** BOS means "price closed beyond a swing that was going against the current short-term structure"; CHoCH means "price closed beyond a swing after two consecutive prior swings confirmed a clean, established trend" — i.e., CHoCH requires stronger prior evidence of trend than BOS does.

---

## 3. Comparison against ICT concepts

| ICT concept | Present? | Evidence |
|---|---|---|
| Swing Highs | Yes | `_detect_swings()`, centered-pivot method |
| Swing Lows | Yes | Same method, `low` side |
| HH (Higher High) | Yes | `_classify_swings()`, compares each swing high to the previous one |
| HL (Higher Low) | Yes | Same, `low` side |
| LH (Lower High) | Yes | Same |
| LL (Lower Low) | Yes | Same |
| Break of Structure (general concept) | Yes, but simplified (see below) | `detect_bos_choch()` |
| Bullish BOS | Yes | Close > a recent LH |
| Bearish BOS | Yes | Close < a recent HL |

All 9 of the explicitly-requested checks are implemented in some form. The implementation is not absent or fake — swings, HH/HL/LH/LL classification, and both directions of BOS and CHoCH all exist and run on real price data.

However, several details diverge from the strict ICT definition of BOS:

- **Standard ICT BOS is defined relative to the immediately preceding relevant swing**, i.e., the most recent swing that defines the current leg. This implementation checks the **last 3** swing highs/lows and reports a break on the **first one found (oldest-to-newest order)** that satisfies the condition, via a `for` loop with an early `break` (Section 2's "recent_highs"/"recent_lows" iterate chronologically, oldest first). This means it can flag a break of an older, already-superseded LH/HL rather than the most recent, most structurally relevant one, if more than one of the last 3 qualifies.
- **Standard ICT BOS is a discrete, one-time event** — the moment structure is broken. This implementation has **no memory between calls**: `detect_bos_choch()` recomputes everything from scratch every time it's called, and nothing marks a swing as "already broken." As long as price remains beyond that swing's level and the swing stays within the "last 3," the function will keep reporting a BOS on every subsequent call — see Section 6 for the evidence this produces in practice.

---

## 4. Classification: (A) Pure ICT / (B) Simplified ICT / (C) Generic Market Structure / (D) Incorrect

**Conclusion: (B) Simplified ICT.**

Evidence for "not incorrect" (rules out D): the core logic — swing detection, HH/HL/LH/LL labeling, break-of-a-counter-trend-swing as BOS, break-after-a-confirmed-trend as CHoCH — is directionally and conceptually correct ICT vocabulary and logic, not arbitrary or mislabeled. CHoCH is deliberately given a higher base confidence score than BOS downstream in `app/ai/scorer.py` ("CHoCH...represents a bigger structural shift" — see Section 9), which is the correct ICT hierarchy (CHoCH > BOS in significance).

Evidence for "not Pure ICT" (rules out A): Section 5 lists specific, real ICT refinements that are absent. Most importantly, Section 6 shows the break-detection has no persistence/state tracking, which is a simplification with a measurable real-world consequence (Section 8's frequency data).

Evidence for "more than Generic Market Structure" (rules out C): a truly generic swing-structure detector would not distinguish BOS from CHoCH at all, or would not require the specific two-consecutive-same-type-swing pattern CHoCH currently requires. This implementation does make that distinction with real, if simplified, logic — that puts it a step above generic structure-breaks into "simplified ICT" territory.

---

## 5. Missing ICT concepts (only what is actually absent, verified by absence of any matching code)

Searched `market_structure.py` and every file that consumes its output for each of the following. None of these terms, or equivalent logic, were found anywhere in the codebase:

| Missing concept | What it means in ICT | Evidence of absence |
|---|---|---|
| **Protected High / Protected Low** | A swing that must not be violated for a bias to remain valid, tracked explicitly as a standing reference level | No field, attribute, or variable anywhere named or resembling this; `SwingPoint`/`StructureBreak` have no "protected" flag |
| **Internal Structure vs. External Structure (as a labeled concept)** | ICT distinguishes minor (internal) swing structure from major (external) swing structure explicitly, usually with separate tracked states | Not present as a concept in `market_structure.py` itself. `app/services/ta_dashboard.py` *does* instantiate `MarketStructure` three times with different `pivot_window` values (5/3/8) and informally calls two of them "internal"/"external" (see prior dependency report, Section 5) — but this is parameter reuse of the same undifferentiated class, not a structural concept the detector itself understands (no code relates the three results to each other, e.g., no check that internal structure aligns with external bias) |
| **Strong Swing / Weak Swing** | ICT classifies swing points by the quality of the move that created them (displacement vs. no displacement) | No swing-quality scoring exists in `_detect_swings()` or `_classify_swings()` — every pivot that geometrically qualifies is treated identically regardless of the move behind it |
| **Displacement Confirmation** | Requiring a strong, momentum-driven candle (or run of candles) to validate that a break is real institutional intent, not noise | Absent from `market_structure.py` itself. A related but distinct concept — "displacement_ratio" (how far price has traveled past the broken level in ATR terms) — exists, but only downstream in `app/ai/scorer.py`, as a scoring bonus, not as a detection-time confirmation gate. The detector itself accepts a break with a close of any distance beyond the level, however small |
| **Body Close Confirmation** | Requiring the candle's real body (not just a wick) to close beyond the level | **Partially present.** The break check does compare `self.df["close"]` (a real close price) against the swing's wick-based `price`, so a mere wick poke does not trigger a break — this specific ICT nuance is implemented correctly |
| **Liquidity Sweep before BOS (as a required precondition)** | Many ICT models require a stop-hunt/liquidity grab to occur before treating a subsequent break as high-probability | Liquidity sweep detection exists elsewhere (`app/smc/liquidity.py`) and is consumed downstream by `AIScorer` and `SignalGenerator` as a separate, independent score/gate — but `detect_bos_choch()` itself has no awareness of liquidity and does not require or check for a prior sweep before flagging a break |
| **Market Structure Shift (MSS) as a distinct labeled event from CHoCH** | Some ICT teaching treats MSS as a specific sub-case or synonym of CHoCH with its own confirmation rules | No separate "MSS" label, field, or logic exists — only "BOS" and "CHoCH" strings are produced |
| **Inducement** | A deliberate minor swing designed to trap traders before the real move, tracked as its own concept | No code anywhere models inducement |
| **Premium / Discount context** | Whether the current price sits in the premium (upper) or discount (lower) half of the most recent significant range, used to filter/qualify entries | Not present in `market_structure.py`. Not found in `app/smc/` at all |
| **Multi-leg confirmation** | Requiring more than one consecutive impulse leg in the same direction before treating structure as confirmed | Not present — a single close beyond a single swing is sufficient to flag BOS; no leg-counting logic exists |

Nothing above is invented — each row is either a documented absence (no matching code found after a full-project search) or, where something related exists elsewhere in the codebase (displacement ratio, ta_dashboard's informal internal/external naming), that is stated explicitly rather than conflated with a true implementation of the concept.

---

## 6. Can this implementation generate False BOS, Missed BOS, Late BOS, or Early BOS?

### False BOS — **Yes, mechanism identified with direct code evidence.**

`StructureBreak.timestamp` is always set to `self.df.index[-1]` — the timestamp of the **current last candle in whatever window was passed in**, not the timestamp of the swing being broken and not the timestamp of when the break first became true (`app/smc/market_structure.py` lines 88, 98, 114, 124 — all four `StructureBreak(...)` constructions use `timestamp=self.df.index[-1]`).

Consequence: because nothing marks a swing as "already broken," and the reported timestamp always advances with the calling window regardless of whether anything new happened, **the exact same underlying structural break will be reported again on every subsequent call for as long as price remains beyond that level and the broken swing stays within the "last 3."** This was independently confirmed against the diagnostic data: `scripts/analyze_smc_frequency.py`'s own de-duplication check (`if latest_break.timestamp != prev_break_ts`) cannot function as intended, because `latest_break.timestamp` is the *current step's* candle timestamp, which is by definition always different from the previous step's — so the check always passes and every step with an active break condition is counted as a distinct BOS event. This is the most likely explanation for the frequency data in Section 8 (BOS present on roughly half of all candles, uniformly across 9 unrelated symbols) — a rate far too high for BOS to be a rare, discrete structural event as ICT defines it. Whether this constitutes a "false" BOS is a matter of definition — the underlying condition (price is beyond that swing) is factually true — but it is not a *new* BOS each time it is reported, and any consumer treating each occurrence as a fresh, independent event would be misled.

### Missed BOS — **Possible, mechanism identified, not directly measured.**

Because the break check only looks at the **last 3** swing highs/lows, and because `_classify_swings()` labels every swing relative only to its immediately preceding swing of the same type, a genuine structural break could fail to register if the relevant swing has already aged out of the "last 3" by the time price reaches it (e.g., during a long consolidation followed by a delayed breakout). This is a plausible mechanism based on the code's fixed window size, not something measured directly in this audit.

### Late BOS — **Yes, by design, and this is expected/acceptable.**

Swing pivots require `pivot_window` candles (default 5) *after* the pivot candle to confirm it as a real swing (`_detect_swings()`'s loop range explicitly excludes the last `pivot_window` candles: `range(self.pivot_window, n - self.pivot_window)`). This means a swing is only known/usable 5 candles after it actually formed. This is standard, expected behavior for any fractal/pivot-based swing method (avoiding lookahead bias) — noted as a fact, not a flaw.

### Early BOS — **Not identified as a distinct additional risk beyond the False BOS mechanism above.** No code path was found that would flag a break *before* a genuine close beyond the level occurs (the close comparison is a hard `>`/`<` check on real, already-closed price data).

---

## 7. Swing detection review

**How pivots are created:** a symmetric/centered window comparison — a candle is a swing high if its high equals the maximum high in a window of `pivot_window` candles on each side (11 candles total by default), and correspondingly for swing lows.

**Is `pivot_window` (default 5) appropriate?** No single value is objectively "correct" — it is a real, meaningful trade-off:

- **Strengths:** a fixed, simple, well-understood method (a standard fractal definition). Deterministic and cheap to compute (`O(n · pivot_window)` via the `max()`/`min()` calls in a sliding loop). Used consistently across every call site found (`signal_generator.py`, `market_scorer.py`, `token_scorer.py`, `dashboard.py` all use the default 5; `ta_dashboard.py` additionally uses 3 and 8 for finer/coarser reads).
- **Weaknesses:** a fixed window doesn't adapt to volatility — the same `pivot_window=5` is applied identically to BTCUSDT and to a much choppier or calmer instrument, with no ATR-relative or volatility-adjusted sizing. A single outlier wick can also prevent a nearby, more "real" swing from qualifying, since the check uses strict `==` against the window max/min.
- **Edge cases confirmed in code:** if `n < 2 * pivot_window + 1` (too few candles), `_detect_swings()` returns immediately with zero swings (line 43-44) — `detect_bos_choch()` then correctly returns an empty list rather than erroring, since it explicitly checks `if not self.swing_highs or not self.swing_lows: return breaks`. Ties (two candles with the exact same high) are not specially handled — both would independently satisfy the `==` check and both would be appended as separate swing points, which could produce two adjacent swing points of the same type in an unusual price environment (not observed directly, inferred from the code's lack of a tie-breaker).

---

## 8. Historical behavior (from the existing `smc_frequency_report_20260727_230446.json`, 9 symbols, 5,761 steps each, no code executed for this audit)

| Symbol | BOS / 100 steps | CHoCH / 100 steps | Bullish/Bearish split available? |
|---|---|---|---|
| BTCUSDT | 54.54 | 1.61 | No |
| ETHUSDT | 55.86 | 2.27 | No |
| SOLUSDT | 58.38 | 2.15 | No |
| BNBUSDT | 57.56 | 2.50 | No |
| XRPUSDT | 61.88 | 1.96 | No |
| XAUUSDT | 51.97 | 2.97 | No |
| XAGUSDT | 47.13 | 3.84 | No |
| CLUSDT | 56.83 | 2.05 | No |
| BZUSDT | 60.22 | 1.65 | No |

**Facts:**
- BOS fires on roughly half of all candle-steps (47–62 per 100) across every one of the 9 symbols tested, spanning crypto, gold, silver, and oil.
- CHoCH is 15–35x rarer than BOS in every symbol (1.6–3.8 per 100 steps).
- The report's underlying fields (`frequency` block) do not separately record bullish vs. bearish counts for BOS or CHoCH — only combined totals. This is a limitation of the diagnostic script's current output, not something this audit can compute without re-running or modifying that script (out of scope here — no code was changed or executed).

**Observations (interpretation, not directly re-verified by execution):**
- A ~50%+ per-candle BOS rate, uniform across 9 unrelated instruments with different volatility profiles, is not consistent with BOS being a rare, discrete structural event as ICT defines it. This is consistent with, and plausibly explained by, the stateless/no-persistence mechanism identified in Section 6 (False BOS) — the detector is very likely re-reporting the same still-active break repeatedly rather than detecting ~1 new break every 2 candles.
- CHoCH's much lower and more varied rate is consistent with its stricter precondition (two consecutive same-classified swings on both sides) being naturally more self-invalidating — once a real reversal occurs, new swings typically stop matching the old "both HH/both HL" (or LH/LL) pattern within a few pivots, which would organically suppress repeat-firing more than the single-swing BOS check does. This is a plausible explanation grounded in the code's logic, not a directly measured fact.

**Future Investigation:** confirming the False-BOS mechanism definitively (rather than as the most plausible explanation) would require instrumenting `detect_bos_choch()` to log which specific swing was matched on each call and observing whether the same swing is matched repeatedly across consecutive steps — not done in this audit, since it would require running/modifying code.

---

## 9. Interaction with other SMC modules (traced from real imports/calls, per the prior dependency report)

| Module | How it uses BOS/CHoCH output |
|---|---|
| **Order Blocks** (`app/smc/order_blocks.py`) | Does not import `MarketStructure` or take a `StructureBreak` directly. Callers (e.g. `signal_generator.py`) pass a `direction` string (derived from `latest_break.direction`) into `OrderBlockDetector.find_bullish_order_block()` / `find_bearish_order_block()` — so BOS/CHoCH's directional conclusion steers *which* order block is searched for, but the Order Block module does its own independent candle-pattern detection |
| **Liquidity** (`app/smc/liquidity.py`) | Directly consumes `MarketStructure`'s `swing_highs`/`swing_lows` as constructor arguments (`LiquidityDetector(df, ms.swing_highs, ms.swing_lows)`) — equal-high/low levels and sweeps are built directly from the same swing points BOS/CHoCH uses |
| **Supply & Demand** (`app/smc/supply_demand.py`) | No import of `MarketStructure` found — builds zones independently of swing/BOS data |
| **FVG** (`app/smc/fvg.py`) | No import of `MarketStructure` found — FVG detection is independent; the two modules' outputs are only combined later, by the callers (e.g. `market_scorer.py`, `ta_dashboard.py`), not inside either module |
| **AI Scoring** (`app/ai/scorer.py`) | Reads `features["market_structure"]["bos_choch"]`, takes the **last entry** (`bos_choch[-1]`), and computes a score: base 72 for CHoCH vs. 58 for plain BOS, plus a bonus of up to 26 points scaled by `displacement_ratio` (how many ATRs price has traveled past the broken level). If no break exists, a flat neutral-ish score of 35 is used. This is the one place a magnitude/quality adjustment is applied to BOS/CHoCH — but see the Possible Risk below |
| **Technical Dashboard** (`app/services/ta_dashboard.py`) | Instantiates `MarketStructure` three separate times (`pivot_window` 5, 3, 8) to report default/"internal"/"external" structure reads side by side in the dashboard UI |
| **Signal Generator** (`app/strategy/signal_generator.py`) | Hard-gates on BOS/CHoCH: `if not breaks: return None` — no structure break at all means no signal is generated for that scan cycle. If a break exists, its `direction` sets the trade direction (LONG/SHORT) for everything that follows (order blocks, liquidity, entry/stop/target construction) |

**Possible Risk:** because `displacement_ratio` (Section 9's AI Scoring row) generally *increases* the longer price continues beyond an already-broken level, and because the same already-broken level can keep being reported as "the latest break" across many scan cycles (Section 6), the AI Scoring module could assign an *increasing* confidence to what is structurally the same, aging breakout each time it re-fires — rather than the confidence naturally decaying as the "event" becomes less new. This has not been measured directly (would require live/backtest execution) and is flagged as a risk, not a confirmed outcome.

---

## 10. Performance review

- **Complexity:** `_detect_swings()` is `O(n · pivot_window)` (a `max()`/`min()` over a small fixed-size window per candle) — cheap. `detect_bos_choch()` only looks at the last 3 highs/2-3 lows — constant-time relative to `n`. No evidence of quadratic or worse behavior.
- **Memory:** `swing_highs`/`swing_lows` lists grow linearly with the number of candles in `df`; for the window sizes actually used in production (500 candles per the scanner, or `WARMUP=500` in the diagnostic script) this is a small, bounded list — not a concern.
- **Scalability:** every real call site (Section-3-of-the-prior-report chains) constructs a brand-new `MarketStructure(df)` per call, recomputing all swings from scratch each time, rather than incrementally updating from a previous state. This is simple and correct but not the most CPU-efficient approach for very frequent re-scans — not currently a measured problem (the diagnostic script's own reported `detector_time_us_per_step` figures, from the earlier FVG audit, are all well under 100ms per step), but worth noting as a design characteristic.
- **Live scanning suitability:** adequate — the live scanner calls this once per symbol per closed 15-minute candle (`on_new_candle`, filtered to `interval == "15m"`), not on every tick, so the recompute-from-scratch cost is paid infrequently.
- **Backtesting suitability:** the diagnostic/backtest scripts recompute a full `MarketStructure(window)` at every single step of a walk-forward replay (Section 8's methodology) — functionally correct, and confirmed fast enough in practice, but this is the same recompute-from-scratch pattern repeated thousands of times per symbol.

---

## 11. Risk Assessment — could changing BOS affect other systems?

| Area | Downstream impact if BOS logic changes | Evidence basis |
|---|---|---|
| **AI confidence** | Direct — `app/ai/scorer.py`'s `market_structure` score (up to 35% of the SMC scoring bucket per its own comment) is computed directly from `bos_choch[-1]`; any change to what counts as a break, or how often, changes this score on every single scan | Section 9 |
| **Signal frequency** | Direct and significant — `SignalGenerator.generate()` hard-gates on `if not breaks: return None`; fixing the persistence/re-fire issue in Section 6 would very likely *reduce* the raw rate of candidate signals (fewer redundant re-detections of the same break), which is a real, material behavior change, not a cosmetic one | Section 6, 9 |
| **Backtest results** | Direct — `app/backtest/engine.py` imports and runs the exact same `SignalGenerator`, so any BOS logic change changes backtest outcomes identically to live behavior (this is actually a strength: live and backtest cannot silently diverge, since they share one code path) | Prior dependency report, Chain F |
| **Existing calibration** | Direct — `app/ai/calibration.py` / `app/ai/calibration_profiles.py` tune weights (including the SMC bucket) against historical outcomes produced under the *current* BOS behavior; changing BOS detection would make prior calibration data reflect a different underlying signal distribution than what the new detector produces, likely requiring recalibration | Inference from calibration's stated purpose (tunes weights to historical outcomes) — not independently re-verified in this audit |
| **Technical Dashboard** | Direct — the Dashboard's "Market Structure" panel field and `ta_dashboard.py`'s three (default/internal/external) structure reads all display whatever `detect_bos_choch()` currently returns; a change would change what's shown to the user immediately | Section 9, prior dependency report |

No change has been made. This section identifies *where* the blast radius would reach if a change were made in the future — it does not evaluate whether making a change is currently advisable.

---

## 12. Decision

Based only on the evidence gathered above:

**Classification: Needs Major Improvements.**

Justification:
- The core ICT vocabulary (swings, HH/HL/LH/LL, BOS vs. CHoCH distinction, close-based confirmation) is genuinely present and conceptually sound — this rules out "Should Be Rewritten" as an evidence-based conclusion; there is a real, working foundation here, not something fundamentally broken.
- However, Section 6 identified a concrete, code-verified mechanism (the always-current-candle `timestamp` field, combined with no persistence/consumed-state tracking) that produces repeated re-detection of the same structural break — not a cosmetic gap, but a mechanism directly supported by both the code and the independently-gathered frequency data in Section 8 (uniformly ~50%+ per-100-step BOS rate across 9 unrelated instruments). This is more significant than a "minor improvement" because it affects the fundamental meaning of what a "BOS event" is to every downstream consumer (Section 9, 11), not just a fine-tuning detail.
- Section 5's list of absent ICT refinements (Protected High/Low, Strong/Weak Swing, Displacement Confirmation at detection time, Premium/Discount context, Multi-leg confirmation, explicit Internal/External structure tracking) represents real gaps versus a complete ICT implementation, but each of these is an *enhancement* on top of a working foundation, not evidence the foundation itself is broken — consistent with "Needs Major Improvements" rather than "Should Be Rewritten."

This classification is a research conclusion only. No implementation is recommended or should be inferred from this report. Waiting for approval before reviewing the next SMC module.
