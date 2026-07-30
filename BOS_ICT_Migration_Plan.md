# BOS ICT Migration Plan

**Status: design only. No code has been written or modified for this plan.**
**Scope:** `app/smc/market_structure.py` (`MarketStructure.detect_bos_choch()`), and its downstream consumers.
**Precedent:** the completed FVG migration (`FVG_ICT_Promotion_Report.md`) — a single, additive, backward-compatible promotion of one method. This plan follows the same philosophy but the BOS case is more involved, so it is broken into ordered, independently-reversible steps rather than one migration.
**Evidence base:** `BOS_ICT_Audit_Report.md` (ICT compliance audit) and `BOS_Duplicate_Investigation_Report.md` (execution-proven root cause of duplicate BOS counting), both re-verified against the current codebase while writing this plan.
**Date:** 2026-07-28

---

## 1. Review of the current BOS implementation

`MarketStructure.detect_bos_choch()` (`app/smc/market_structure.py`, re-read in full for this plan, unchanged since the two prior audits):

- Detects swing highs/lows with a symmetric pivot test (`pivot_window`, default 5).
- Labels each swing HH/LH (highs) or HL/LL (lows) relative to only the immediately preceding swing of the same type.
- **BOS:** scans the last 3 swing highs/lows; the first one found (oldest-to-newest) that is classified as the "wrong-way" type (LH for bullish, HL for bearish) AND has already been closed beyond by the current candle is reported, then the loop stops (`break`).
- **CHoCH:** requires the last 2 highs AND last 2 lows to jointly confirm an established trend (both HH or both LH, both HL or both LL) before flagging a break of the most recent one.
- Every `StructureBreak` returned carries `timestamp = self.df.index[-1]` — the CURRENT candle being analyzed, never the candle where the swing itself formed or was first broken. The swing's own real identity does exist (`StructureBreak.broken_swing.timestamp`), but nothing in this file or any caller currently uses it for deduplication.
- The whole class is fully stateless: every call to `MarketStructure(df)` recomputes everything from scratch; nothing persists between calls.

This is unchanged from the prior audits — re-verification confirms the code is exactly as previously analyzed.

---

## 2. Comparison against ICT methodology (recap, verified current)

| Present and correct | Present but simplified | Missing entirely |
|---|---|---|
| Swing Highs/Lows | BOS swing selection (picks first-in-last-3, not necessarily the most recent/relevant) | Protected High/Low |
| HH/HL/LH/LL classification | No persistence/event-identity (proven root cause of duplicate counting) | Formal Internal/External Structure |
| Bullish/Bearish BOS distinction | | Strong/Weak Swing |
| CHoCH (correctly weighted higher than BOS downstream) | | Displacement Confirmation (exists downstream in AI scoring only, not at detection time) |
| Body-close confirmation (compares real close, not wick, to the level) | | Liquidity-sweep-before-BOS as a precondition |
| | | Market Structure Shift as a distinct label from CHoCH |
| | | Inducement |
| | | Premium/Discount context |
| | | Multi-leg confirmation |

---

## 3. Every difference identified, and its evidence source

1. **No event identity / duplicate re-firing.** Proven by execution in `BOS_Duplicate_Investigation_Report.md`: the same swing was reported as "broken" on 35 consecutive candles in a controlled test, and 147 counted "events" in a realistic 700-candle run traced back to only 11 real distinct swings.
2. **Swing-selection order.** `BOS_ICT_Audit_Report.md` Section 3: the last-3 scan with an early `break` on the first (oldest) qualifying match can report an older, already-superseded swing instead of the nearest relevant one when more than one of the last 3 qualifies.
3. **No displacement gate at detection time.** A break of any size (even a single tick beyond the level) currently qualifies; displacement is only used afterward, as a scoring bonus in `app/ai/scorer.py`.
4. **No Protected High/Low tracking.** No code anywhere marks a swing as the standing reference level a bias depends on.
5. **No formal Internal/External Structure.** `app/services/ta_dashboard.py` approximates this informally by instantiating `MarketStructure` three times with different `pivot_window` values (5/3/8) — but nothing relates the three results to each other; it's parameter reuse, not a real internal/external model.
6. **No Strong/Weak Swing classification.** Every geometrically-qualifying pivot is treated identically regardless of the move that created it.
7. **No Premium/Discount context, no Inducement modeling, no multi-leg confirmation, no explicit MSS label** — confirmed absent by a full-project search in the prior audit.
8. **Diagnostic tooling still measures the inflated rate.** Re-verified while writing this plan: `scripts/analyze_smc_frequency.py`'s `measure_smc_frequency()` (Part 1, untouched by the FVG migration since that work was scoped to FVG only) still contains the same `latest_break.timestamp != prev_break_ts` check proven unable to deduplicate (line 144, current file). Its `bos_per_100_steps`/`choch_per_100_steps` output remains inflated by the same mechanism proven in the forensic investigation. This was in scope for the FVG migration only for its FVG-specific parts (which were removed); it was never in scope to fix the BOS-side counting, so it is still broken today and is explicitly addressed by this plan.

---

## 4. What must change to reach ICT standard

Each gap in Section 3 maps to a specific, scoped change:

| Gap | What must change |
|---|---|
| No event identity (item 1) | A caller-level check using the already-existing `broken_swing.timestamp` field to distinguish a genuinely new break from a still-active old one |
| Swing-selection order (item 2) | Change the last-3 scan to prefer the MOST RECENT qualifying swing rather than stopping at the first (oldest) match |
| No displacement gate (item 3) | An optional, profile-driven minimum-displacement threshold (in ATR terms) applied at detection time, not just scoring time |
| No Protected High/Low (item 4) | A new, explicitly-tracked "standing reference level" concept — this requires genuine persistent state, a bigger architectural step (see Section 7, Phase 5) |
| No formal Internal/External Structure (item 5) | Promote `ta_dashboard.py`'s informal 3-pivot-window pattern into an explicit, first-class distinction other consumers (AI Scoring, Evidence Engine) can also reference |
| No Strong/Weak Swing (item 6) | A swing-quality score, likely reusing the same displacement-ratio concept already validated in AI scoring |
| Diagnostic tooling still inflated (item 8) | Change `prev_break_ts` comparisons in `scripts/analyze_smc_frequency.py` to compare `broken_swing.timestamp` instead of `StructureBreak.timestamp` |
| Premium/Discount, Inducement, multi-leg confirmation | Not designed in this plan — see Section 7's explicit deferral |

---

## 5. Compatibility with every dependent system (verified by reading each one, not assumed)

- **AI Scoring (`app/ai/scorer.py`).** Reads `features["market_structure"]["bos_choch"][-1]`, then `.type` and `.level` only. As long as `StructureBreak` keeps these two fields with the same meaning (a string type and a numeric price level), zero code changes are required here for Steps 1–4 below. The score's numeric OUTPUT will shift once fewer, more accurate breaks are detected — this is a data-distribution effect, not a compatibility break (see Section 6).
- **Signal Generator (`app/strategy/signal_generator.py`).** Calls `ms.detect_bos_choch()`, hard-gates on `if not breaks: return None`, then reads `latest_break.direction`. This is the ONE consumer that actually needs new logic (the event-identity check, item 1) — everything else about its usage is compatible unchanged. `SignalGenerator` already holds one persistent instance per symbol via `scanner.py`'s `self.generators = {s: SignalGenerator(s) for s in symbols}`, which is a natural, already-existing place to hold "last reported break" state — no new architecture required to add it.
- **Order Blocks (`app/smc/order_blocks.py`).** Verified: `OrderBlockDetector` takes only `df` and a `direction` string supplied by the caller — it has zero direct dependency on `MarketStructure` or `StructureBreak`. Fully unaffected by any BOS-side change, as long as `.direction` keeps returning `"bullish"`/`"bearish"`.
- **Liquidity (`app/smc/liquidity.py`).** Verified: `LiquidityDetector` takes `swing_highs`/`swing_lows` (the raw `SwingPoint` lists) directly, and only reads `.price` from them. It never touches `StructureBreak` at all. Fully unaffected, as long as `MarketStructure.swing_highs`/`.swing_lows` keep being populated the same way.
- **CHoCH.** Shares `_classify_swings()` and the same swing lists as BOS — any change to swing detection/classification affects both. Every step in Section 7 must include an explicit CHoCH regression check, not just a BOS one.
- **Technical Dashboard (`app/services/ta_dashboard.py`).** Calls `detect_bos_choch()` three times (pivot_window 5/3/8) for a single current-state read each time — this is the correct usage pattern per the forensic investigation (a one-shot "what does structure look like right now" query, not an event stream), and is unaffected by the event-identity fix in Step 1, since that fix belongs in `SignalGenerator`, not in `MarketStructure` itself.
- **Backtesting (`app/backtest/engine.py`).** Imports and runs the same `SignalGenerator` as live, so it inherits Step 1's fix identically — live and backtest cannot diverge, by construction. Backtested calibration history was generated under the OLD behavior, so it becomes a different (more accurate, but different) baseline after any change — flagged in Section 6.

**Design conclusion: `detect_bos_choch()` itself should stay a stateless, pure "what is the current structure" function** (this is what makes it safely reusable by Dashboard/Market Scan/Token Scan/ta_dashboard). The event-identity problem should be fixed at the `SignalGenerator` layer, which is the only consumer that actually needs to distinguish "new" from "still active." This keeps the fix small, isolated, and consistent with the project's existing "single source of truth, many readers" architecture (documented in `02_System_Architecture.md`).

---

## 6. Estimated impact

| Area | Estimate | Basis |
|---|---|---|
| **Signal frequency** | Likely decrease, magnitude unknown without measurement | The event-identity fix (Step 1) stops re-building candidate signals for a break already reported — `BOS_Duplicate_Investigation_Report.md`'s realistic test showed ~13x over-counting on average; if live behavior is similar, most of the reduction is duplicate suppression, not fewer real opportunities |
| **AI confidence** | Score distribution shifts; no formula change | `market_structure` score already uses `displacement_ratio`, which grows the longer a break persists (Section 9 of `BOS_ICT_Audit_Report.md`) — after Step 1, scores will reflect the break's true age instead of quietly climbing on a stale, repeatedly-re-detected one |
| **Existing calibration** | Recalibration likely needed | `app/ai/calibration.py`/`calibration_profiles.py` tuned weights against historical outcomes produced under the CURRENT (inflated, imprecise) BOS behavior; a materially different break rate/quality changes the statistical basis calibration was built on |
| **Performance** | Negligible | Step 1 adds one timestamp comparison per signal-generation call; Step 2 (swing-selection order) is the same O(1) last-3 scan, just preferring a different match; no new O(n) or worse work introduced by Steps 1–4 |

This section deliberately does not claim precise percentages — none of the current evidence measures live production behavior (both prior audits used synthetic or historical-replay data), and the diagnostic tooling itself needs the Step-0 fix (Section 3, item 8) before it can produce a trustworthy "before" baseline to compare against.

---

## 7. Migration plan — safe, backward-compatible, evidence-based, one step at a time

### Step 0 — Fix the measurement tool first (prerequisite, not yet done)
Change `scripts/analyze_smc_frequency.py`'s `measure_smc_frequency()` to deduplicate BOS/CHoCH counts using `latest_break.broken_swing.timestamp` instead of `latest_break.timestamp`. Zero production risk (script-only, read-only tool). This establishes an honest "before" baseline BOS/CHoCH frequency, which every later step needs in order to measure its own impact credibly.

### Step 1 — Fix event identity at the Signal Generator layer
Add a small piece of per-symbol session state to `SignalGenerator` (it already lives per-symbol for the life of the scanner) that remembers the `broken_swing.timestamp` of the last break it acted on, and skips building a new candidate signal if the current break's `broken_swing.timestamp` matches. `market_structure.py` itself is NOT changed — `detect_bos_choch()`'s signature, return type, and behavior stay identical, so Dashboard/Market Scan/Token Scan/ta_dashboard/backtesting all continue to work with zero changes. This is the direct fix for the proven duplicate-BOS problem and should ship first, alone, and be measured against Step 0's new honest baseline before anything else changes.

### Step 2 — Fix swing-selection order
Inside `detect_bos_choch()`, change the last-3 scan to prefer the most recent qualifying swing rather than stopping at the first (oldest) match. Signature and return type (`List[StructureBreak]`) stay identical — every consumer in Section 5 keeps working unchanged; only which specific swing gets reported changes when more than one of the last 3 qualifies. Requires an explicit CHoCH regression check (shares the same swing lists) before shipping.

### Step 3 — Optional, profile-driven displacement gate
Add a new field to `CalibrationProfile` (e.g., a minimum-displacement-in-ATR threshold), defaulting to a value that reproduces current behavior (no gate) until deliberately tuned per asset class — consistent with the existing crypto/gold/silver/oil profile architecture (`app/ai/calibration_profiles.py`), rather than one global constant. Fully backward compatible at the default value; any behavior change is opt-in and asset-class-scoped.

### Step 4 — Formalize Internal/External structure
Promote `ta_dashboard.py`'s existing informal 3-pivot-window pattern into an explicit, additive distinction (e.g., an optional new field alongside the existing `StructureBreak` fields, not a replacement of any existing one) so AI Scoring and the Evidence Engine could reference both views explicitly in the future. Purely additive — no existing field removed or renamed.

### Step 5 — Protected High/Low (explicitly deferred, needs its own design pass)
This is the first item that genuinely requires persistent state beyond what Step 1 introduces (a standing reference level that survives across many candles, not just "was this break already reported once"). Recommend treating this as a separate, later design investigation, only after Steps 0–4 have shipped and been measured — consistent with the audit's own conclusion ("Needs Major Improvements," not "rewrite everything at once").

### Step 6 — Premium/Discount context, Multi-leg confirmation, Inducement (explicitly deferred)
No design work done in this plan. Recommend deferring until real calibration data exists under Steps 0–4's corrected behavior — evaluating these against inflated, duplicate-laden historical data (today's situation) would not produce trustworthy conclusions.

**Every step above is independently shippable and independently reversible** (each touches a different, small surface: a script, then `SignalGenerator`, then `market_structure.py`'s internal selection order, then a new opt-in profile field, then an additive dashboard-facing field) — consistent with "one step at a time." No step in this plan has been implemented. Waiting for approval before writing any code, starting with Step 0.
