# BOS Duplicate-Detection Forensic Investigation Report

**Scope:** `app/smc/market_structure.py` (unmodified) and `scripts/analyze_smc_frequency.py` (unmodified)
**Method:** Full code read, PLUS actual execution of the real, unmodified `MarketStructure` class (imported directly from its real file path, no copy, no edit) against controlled synthetic OHLC data, using the exact same rolling-window replay and counting logic found in `scripts/analyze_smc_frequency.py`'s `measure_smc_frequency()`. No production code was changed, refactored, or optimized. No file inside `app/` or `scripts/` was modified — all execution happened in a separate, throwaway test script outside the project.
**Date:** 2026-07-28

This report answers the assigned question with **execution evidence**, not just code reading, because reading alone can only prove a mechanism is *possible* — running the real code against controlled data proves it *happens*.

---

## 1. Files read in full

- `app/smc/market_structure.py` — re-read in full (already fully read in the prior BOS ICT audit; re-verified line-by-line for this investigation, particularly `detect_bos_choch()` lines 78–130).
- `scripts/analyze_smc_frequency.py` — the `measure_smc_frequency()` function (lines 118–235) was read in full; this is the exact function that produced `smc_frequency_report_20260727_230446.json`.

---

## 2. Complete execution flow, traced exactly

```
For each step i (one per candle, starting once WARMUP candles exist):
    window = df.iloc[max(0, i - WARMUP + 1) : i + 1]        (scripts/analyze_smc_frequency.py:144)
        ↓
    ms = MarketStructure(window)                              (line 147)
        ↓
    ms._detect_swings() runs automatically inside __init__     (market_structure.py:37)
        - finds swing highs/lows using a centered pivot test, LOCAL to this window's own indices
        ↓
    ms._classify_swings() runs automatically right after        (market_structure.py:39, 60)
        - labels each swing HH/LH or HL/LL relative to the PREVIOUS swing visible in THIS window
        ↓
    breaks = ms.detect_bos_choch()                              (line 148)
        - compares window["close"].iloc[-1] (the CURRENT candle in this window) against the
          last 3 swing highs/lows
        - if a match is found, builds a StructureBreak with timestamp = window.index[-1]
          (market_structure.py lines 88, 98, 114, 124 — ALWAYS the current candle)
        ↓
    latest_break = breaks[-1] if breaks else None               (line 149)
        ↓
    if latest_break is not None and latest_break.timestamp != prev_break_ts:   (line 151)
        count as bos_events or choch_events                     (lines 152-156)
        prev_break_ts = latest_break.timestamp
        ↓
    (loop continues to next i)
        ↓
    per_100() converts raw counts to a rate                      (line 222)
        ↓
    JSON output (smc_frequency_report_*.json)
```

This is the exact flow that was then re-run, line for line, against synthetic data in Sections 3–7 below.

---

## 3. Does the detector return the SAME structural break again and again while price remains beyond the broken level?

**Fact, proven by execution.** A 700-candle synthetic series (deterministic seed, mild upward drift with realistic noise — see Section 8 for full setup) was replayed through the exact `WARMUP=500` rolling-window loop used by the real script, calling the real, unmodified `MarketStructure(window).detect_bos_choch()` at every step. The `broken_swing` object attached to each `StructureBreak` (`market_structure.py`'s `broken_swing` field, which carries the swing's own real timestamp — separate from the `StructureBreak.timestamp` field) was inspected directly:

```
i=516  level=89.986...  broken_swing.index=457 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=517  level=89.986...  broken_swing.index=456 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=518  level=89.986...  broken_swing.index=455 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=519  level=89.986...  broken_swing.index=454 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=520  level=89.986...  broken_swing.index=453 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=521  level=89.986...  broken_swing.index=452 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=522  level=89.986...  broken_swing.index=451 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=523  level=89.986...  broken_swing.index=450 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
i=524  level=89.986...  broken_swing.index=449 (local)  broken_swing.timestamp=2026-01-05 22:30:00  type=HL
   ... continues, 35 consecutive steps in total (i=514 to i=548) ...
```

The `broken_swing.index` value changes every step (it's a LOCAL index into that step's own window slice, which shifts by one as the window rolls forward), but **`broken_swing.timestamp` — the swing's real, absolute identity — is identical across all 35 steps: `2026-01-05 22:30:00`.** This is conclusive: it is verifiably the same physical swing point being reported as broken, 35 times in a row, once per candle, for 8 hours 45 minutes of continuous price action (35 × 15m), while price kept moving further away from it (close fell from 89.03 to 83.99 over that span).

**Conclusion for Task 3: Yes, proven by direct execution, not inferred.**

---

## 4. Does `StructureBreak.timestamp` represent (A) the candle where BOS first occurred, or (B) the current candle being analyzed?

**Fact, proven by both code and execution.** In `market_structure.py`, all four `StructureBreak(...)` constructions (2 for BOS, 2 for CHoCH) set `timestamp=self.df.index[-1]` — literally "the last row of whatever dataframe was passed to this call," never a reference to when the swing itself formed or was first exceeded.

Execution confirms this exactly: across every single step in the 201-step, 700-candle test run that produced a break, comparing `latest_break.timestamp` to that step's own current-candle timestamp showed equality 100% of the time (this was logged as a boolean column, `ts_equals_break_ts`, and was `True` on every row where a break existed — no exceptions found).

**Answer: (B) — the current candle being analyzed. Never (A).**

**Downstream effect:** any code that uses `StructureBreak.timestamp` to judge "is this a new event" (as `scripts/analyze_smc_frequency.py` does — Section 5) receives a value that is mathematically guaranteed to differ from one step to the next, regardless of whether the underlying structural condition is new or 35 candles old. The field cannot serve as an event-identity marker; only `broken_swing.timestamp` (not currently used for this purpose anywhere) actually carries that identity, as shown in Section 3.

---

## 5. Does `scripts/analyze_smc_frequency.py` count new BOS events, or repeated active BOS conditions?

**Fact, proven by both code and execution.** The exact counting code:

```python
# scripts/analyze_smc_frequency.py, lines 151-156
if latest_break is not None and latest_break.timestamp != prev_break_ts:
    if latest_break.type == "CHoCH":
        choch_events += 1
    else:
        bos_events += 1
    prev_break_ts = latest_break.timestamp
```

This increments `bos_events` every time `latest_break.timestamp != prev_break_ts`. Per Section 4, `latest_break.timestamp` is always the CURRENT candle's timestamp. Since the loop advances to a new candle every step, `prev_break_ts` (set from the previous step) can never equal the current step's candle timestamp. The result, proven by the 700-candle execution run: **147 `bos_events` were counted, but they trace back to only 11 distinct underlying broken-swing levels** (identified via `broken_swing.timestamp`/`level` grouping). One single level (89.99, the swing examined in Section 3) alone accounted for 35 of the 147 counted events — nearly a quarter of the total count from one physical swing.

**Answer: the script counts repeated ACTIVE BOS conditions, not new BOS events. This is proven, not hypothesized.**

---

## 6. Does the `prev_break_ts` logic actually prevent duplicates, or does it always pass? Why?

**Fact, proven mathematically and by execution: it always passes, whenever two consecutive evaluated steps both have a break.**

Why: `prev_break_ts` is only ever assigned the value of a previous `latest_break.timestamp`, and per Section 4, that value is always equal to whatever candle was "current" at the time it was set. Because the loop's `i` strictly increases by one candle every iteration, the "current candle" timestamp is different at every step by construction (there are no duplicate timestamps in an ordered candle series). Therefore:

- If step N has a break, `prev_break_ts` becomes step N's own candle timestamp.
- If step N+1 also has a break, its `latest_break.timestamp` is step N+1's own candle timestamp — which is, by definition, a different timestamp than step N's.
- `latest_break.timestamp != prev_break_ts` therefore evaluates `True` every single time two consecutive steps both produce a break — there is no code path by which it can evaluate `False` in that situation.

The check can only ever suppress a count in one narrow scenario: if `breaks` is empty for one or more intermediate steps and then a break reappears bearing the exact same timestamp as a break from BEFORE that gap — which cannot happen either, since a re-appearing break's timestamp would still be set to that later step's own (later, different) current candle. **There is no scenario in which this check successfully deduplicates two genuinely-repeated detections of the same underlying swing.** It is not a partially-working safeguard; it is inert by construction.

---

## 7. Manual candle-by-candle walkthrough (most important section)

Using the same 700-candle synthetic series and the same `WARMUP=500` rolling window as the real script, here is the exact sequence around the level=89.99 event from Section 3, shown candle by candle:

| Step (row) | Candle index `i` | Timestamp | Close price | Detected `type` | Direction | Broken level | Same swing as before? (`broken_swing.timestamp`) | Counted as "NEW" by the script? |
|---|---|---|---|---|---|---|---|---|
| 14 | 513 | 2026-01-06 08:15 | 87.382 | CHoCH | bearish | 91.22 | (different swing — a genuine new CHoCH) | Yes |
| **15** | **514** | **2026-01-06 08:30** | **89.032** | **BOS** | **bearish** | **89.99** | **First appearance of this swing** | **Yes** |
| 16 | 515 | 2026-01-06 08:45 | 88.832 | BOS | bearish | 89.99 | **SAME swing (2026-01-05 22:30:00)** | **Yes — but it's not new** |
| 17 | 516 | 2026-01-06 09:00 | 87.837 | BOS | bearish | 89.99 | **SAME swing** | **Yes — but it's not new** |
| 18 | 517 | 2026-01-06 09:15 | 86.589 | BOS | bearish | 89.99 | **SAME swing** | **Yes — but it's not new** |
| … | … | … | … | … | … | … | *(31 more consecutive candles, same swing, same story)* | … |
| 47 | 546 | 2026-01-06 16:30 | 83.284 | BOS | bearish | 89.99 | **SAME swing** | **Yes — but it's not new** |
| 48 | 547 | 2026-01-06 16:45 | 83.497 | BOS | bearish | 89.99 | **SAME swing** | **Yes — but it's not new** |
| **49** | **548** | **2026-01-06 17:00** | **83.992** | **BOS** | **bearish** | **89.99** | **SAME swing — 35th and final consecutive report** | **Yes — but it's not new** |

**Reading this walkthrough against the requested example format:**

```
Candle 1 (row 14)   → CHoCH detected against a DIFFERENT, earlier swing (91.22) — a genuinely new event
Candle 2 (row 15)   → FIRST BOS against the 89.99 swing — this one IS genuinely new
Candle 3 (row 16)   → Should the detector report again? By ICT convention: NO — the break already happened at row 15.
                       What does it actually report? → The SAME BOS against the SAME 89.99 swing, again.
Candles 4-48 (rows 17-48) → Continues reporting the SAME 89.99 BOS on every single subsequent candle,
                              for 33 more candles, regardless of price moving progressively further away.
Candle 49 (row 49)  → Still the same 89.99 BOS. Only stops once the underlying swing configuration in the
                       rolling window itself changes (a new swing forms or an old one ages out of the window).
```

This is the direct, observed behavior of the real, unmodified detector — not a theoretical projection.

---

## 8. Can the observed ~50 BOS / 100 candles be fully explained by duplicate counting, or does the detector genuinely create that many independent events?

**Facts from execution (700-candle synthetic series, same methodology as the real diagnostic script):**

- Total evaluated steps: 201
- `bos_events` counted by the script's own logic: 147 (**73.13 per 100 steps** — the same order of magnitude as the real report's 47–62 per 100 range across the 9 real symbols)
- Distinct underlying broken-swing levels among those 147 counted events: **11**
- Longest single unbroken run of the identical level being re-counted: **35 consecutive steps**
- Second-longest run: 32 consecutive steps (a different level, 85.09)
- Average run length across all identified runs: 9.63 steps

**Conclusion: fully explained by duplicate counting, in this test.** 147 "events" from 11 real, distinct swings is not evidence of 147 independent structural breaks — it is evidence of roughly 11 real breaks, each counted an average of ~13 times. This synthetic series used a simple constant-drift-plus-noise model, not real market data, so the exact ratio (11 levels / 147 counts) should not be read as a precise prediction for any specific real symbol — but the *mechanism* generating the inflation is the same mechanism proven in Sections 3–6 against the real, unmodified code, and produces a per-100-steps rate in the same range actually observed in the real 9-symbol report. This is strong, mechanism-level evidence that the real report's ~54 BOS/100 rate is not 54 genuine independent structural breaks per 100 candles.

**What this does NOT prove:** this does not establish the exact true underlying BOS rate for any real traded symbol (BTCUSDT, gold, etc.) — that would require re-running this same instrumentation against the real historical candle data for each symbol, which was not done here (out of scope — would require touching/running the real data-fetch pipeline, not just the detector in isolation).

---

## 9. Risk Assessment — downstream systems affected if the duplicate issue is real

| System | Effect, based on evidence already gathered | Confirmed by |
|---|---|---|
| **AI Scoring** (`app/ai/scorer.py`) | Reads `bos_choch[-1]` and scores it with a `displacement_ratio` bonus that grows the further price has traveled past the level. Since Section 3 shows the SAME level being reported for many consecutive candles while price moves further away, the score for what is really one aging break would tend to *increase* over repeated re-detections rather than reflect a fresh, independent event | Direct code read (prior BOS ICT audit, Section 9), consistent with this investigation's execution evidence |
| **Signal Generator** (`app/strategy/signal_generator.py`) | Hard-gates on `if not breaks: return None`. Per Section 3's proof, this gate would repeatedly evaluate "pass" for the same underlying break across many consecutive scan cycles, not just once | Code read + this investigation's execution proof of persistence |
| **Dashboard** (`app/api/v1/endpoints/dashboard.py`) | Displays "the latest break" as a single current-state field (e.g., "BOS (bullish)"). This usage is a single point-in-time READ, not a counted series — it is not affected by the counting flaw described in Sections 5-6, since it never compares timestamps across calls. This is a case where the detector's "current state" behavior is actually being used correctly | Code read (prior dependency report) |
| **Backtesting** (`app/backtest/engine.py`) | Runs the exact same `SignalGenerator` as live, so it inherits the same repeated-gate-pass behavior as the Signal Generator row above — live and backtest are affected identically and consistently, not differently | Code read (prior dependency report, Chain F) |
| **Technical Dashboard** (`app/services/ta_dashboard.py`) | Same single-point-in-time "current structure read" usage pattern as the Dashboard row — not affected by the counting flaw itself, since it doesn't compare across calls either | Code read (prior BOS ICT audit) |

The counting flaw specifically and materially affects: (1) any diagnostic/statistical tool that counts "how many BOS happened" over a period (proven in this report), and (2) potentially the true novelty of consecutive live scan-cycle signal candidates (proven the underlying mechanism exists; the DB-level "active signal already exists" guard in `scanner.py`'s `save_signal()` — noted in the prior BOS ICT audit — prevents this from creating duplicate database rows, but does not prevent the scanner from repeatedly re-building a full candidate signal and AI score for the same aging break before that guard discards it).

---

## 10. Decision — root cause classification

☑ **Both**

Evidence-based justification:

- **The detector (`market_structure.py`) is the root enabling condition.** `detect_bos_choch()` has no concept of "already reported" and stamps every result with the current candle's timestamp rather than the swing's own timestamp (which does exist, correctly, on `broken_swing.timestamp`, but is never used for this purpose). This is a real, proven characteristic of the detector itself (Sections 3, 4), not a misreading of it. Note, however, that this behavior is not unconditionally "wrong" — for a single, one-off "what is the current structure right now" read (the Dashboard and Technical Dashboard use cases), returning the current active state is reasonable and correct. The detector is not designed to answer "did something NEW just happen," and nothing about it claims to.
- **The frequency analysis script (`scripts/analyze_smc_frequency.py`) is where the actual counting error occurs.** It uses the detector in a way that requires event-novelty detection ("count how many NEW BOS happened"), attempts to implement that via a timestamp comparison, and that comparison is proven (Section 6) to be incapable of ever succeeding, given what the detector actually provides. The script's own stated intent (variable names `bos_events`, the `per_100_steps` framing) is unambiguously "count distinct events," which the current logic cannot deliver.
- Neither component alone fully explains the observed field data: the detector alone (used as a single-shot "what's the current structure" query, as the Dashboard uses it) would not produce an inflated *count* at all, since nothing there is counting anything. The script alone, if it had a correctly-designed detector to call (one that either tracked "already reported" state or exposed a stable break-identity), would not have this bug, since its comparison logic would then have something valid to compare against.

**This is not a "Detector bug" alone or an "Analysis script bug" alone — it is a mismatch between what the detector provides (a stateless current-condition read) and what the script assumes it provides (a stream of discrete, self-deduplicating events). Both files would need to change in coordination to fix this, which is exactly why this is flagged as "Both" rather than attributing fault to a single file.**

No implementation is recommended in this report. Waiting for approval before any code is changed.
