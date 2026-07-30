# SMC / ICT Engine — Master Migration Plan

**Status: design only. No code has been written or modified. No files have been changed.**
**Scope:** every SMC module in `app/smc/`, plus the modules that build on them (`app/services/ta_dashboard.py`, `app/strategy/signal_generator.py`'s SMC usage). AI Scoring, Calibration, and the Trading Agent are explicitly NOT modified — they are referenced only to describe *impact*, per the assignment's constraints.
**Evidence base:** direct reading of `app/smc/market_structure.py`, `fvg.py`, `liquidity.py`, `order_blocks.py`, `supply_demand.py`, `app/services/ta_dashboard.py`, `app/strategy/signal_generator.py`, `app/ai/scorer.py`, plus the three completed audits from this engagement: `BOS_ICT_Audit_Report.md`, `BOS_Duplicate_Investigation_Report.md`, `BOS_ICT_Migration_Plan.md`, and `FVG_ICT_Promotion_Report.md`. Nothing below is asserted without a code reference.
**Date:** 2026-07-28

---

## How to read this document

Section A covers all 22 requested modules, each with: Current implementation, ICT standard, Missing features, Risks, Impact on AI, Difficulty, Dependencies, and Now-or-later. Section B is the 4-phase roadmap. Modules are grouped by family (Structure, Order Blocks, Liquidity, FVG, Value/Zone, Multi-Timeframe) since several share the same underlying code and evidence.

---

## Section A — Module-by-module review

### 1. Market Structure (BOS)

| | |
|---|---|
| **Current implementation** | `app/smc/market_structure.py`'s `MarketStructure.detect_bos_choch()` — symmetric pivot swings (default `pivot_window=5`), HH/HL/LH/LL labeling vs. only the immediately preceding same-type swing, BOS = current close beyond the first (oldest) qualifying swing in the last 3 |
| **ICT standard** | A break of the most recent relevant opposing swing, confirmed by close, reported once as a discrete event |
| **Missing features** | Event identity/persistence (proven absent — see Risks), correct "most recent" swing selection (currently picks oldest-of-last-3 that qualifies) |
| **Risks** | **Proven, not theoretical**, via `BOS_Duplicate_Investigation_Report.md`: the same swing was reported as newly "broken" on 35 consecutive candles in a controlled execution test; a realistic 700-candle run showed 147 counted "events" tracing back to only 11 real distinct swings |
| **Impact on AI** | Direct — `app/ai/scorer.py` reads `bos_choch[-1]` every call; a stale, repeatedly-re-detected break can receive a rising `displacement_ratio` bonus over time, i.e. confidence can climb on an aging, non-new event |
| **Difficulty** | Low for the identity fix (isolated, caller-level); Low-Medium for swing-selection order (isolated, inside one function) |
| **Dependencies** | CHoCH shares the same swing lists and classification code; Liquidity/Order Blocks depend on `direction`/`swing_highs`/`swing_lows`, not on the break-selection logic itself |
| **Now or later** | **Now — Phase 1.** This is the single highest-evidence, highest-impact item in the entire SMC engine |

### 2. CHoCH (Change of Character)

| | |
|---|---|
| **Current implementation** | Same function, same file — requires the last 2 highs AND last 2 lows to jointly confirm an established trend before flagging a break of the most recent one |
| **ICT standard** | A break that ends an established trend, evidencing a probable reversal — correctly weighted as more significant than BOS |
| **Missing features** | Same event-identity gap as BOS (identical mechanism, same `StructureBreak.timestamp = current candle` field) |
| **Risks** | Lower magnitude than BOS in practice — the stricter 2-swing precondition is naturally more self-invalidating (confirmed via reasoning in `BOS_ICT_Audit_Report.md` Section 8, not directly re-measured) — but the same underlying bug applies |
| **Impact on AI** | `app/ai/scorer.py` gives CHoCH a higher base score (72) than BOS (58) — correct ICT hierarchy, already implemented correctly; only the event-identity issue affects it |
| **Difficulty** | Zero extra work — the Phase 1 BOS fix (item 1) fixes CHoCH identically, since it's the same function and the same `broken_swing.timestamp` field |
| **Dependencies** | Same swing lists as BOS — any Phase 1/2 change to swing classification must be regression-tested against CHoCH, not just BOS |
| **Now or later** | **Now — Phase 1**, bundled with item 1 |

### 3. Internal Structure

| | |
|---|---|
| **Current implementation** | Not a first-class concept anywhere in `app/smc/`. `app/services/ta_dashboard.py` approximates it informally: `ms_internal = MarketStructure(df, pivot_window=3)` — a finer pivot window, run independently |
| **ICT standard** | Minor/lower-timeframe swing structure, tracked and reconciled against external (major) structure |
| **Missing features** | No relationship is computed between the "internal" and "external" reads — they're three unrelated `MarketStructure` instances in `ta_dashboard.py`, not a model |
| **Risks** | Low — currently just an extra dashboard display field, not used in scoring or signal generation, so no live-behavior risk today |
| **Impact on AI** | None currently — not fed into `app/ai/scorer.py` at all |
| **Difficulty** | Medium — needs a real relationship (e.g., does internal structure agree/disagree with external), not just two more numbers |
| **Dependencies** | Depends on Phase 1/2's `MarketStructure` fixes being in place first, so "internal" readings aren't built on the same duplicate-firing/selection-order issues as "external" |
| **Now or later** | **Later — Phase 2**, after the base `MarketStructure` fixes |

### 4. External Structure

| | |
|---|---|
| **Current implementation** | Same as Internal Structure — `ms_default` (`pivot_window=5`) and `ms_external` (`pivot_window=8`) in `ta_dashboard.py`, run independently |
| **ICT standard** | Major/higher-degree swing structure, the primary bias-setting frame |
| **Missing features** | Same as item 3 — no formal relationship to Internal Structure |
| **Risks** | Low, same reasoning as item 3 |
| **Impact on AI** | None currently |
| **Difficulty** | Medium, shared design work with item 3 (they should be designed together, not separately) |
| **Dependencies** | Same as item 3 |
| **Now or later** | **Later — Phase 2**, together with item 3 |

### 5. Market Structure Shift (MSS)

| | |
|---|---|
| **Current implementation** | No distinct label exists. `detect_bos_choch()` only ever produces `"BOS"` or `"CHoCH"` strings — confirmed by a full-project search in `BOS_ICT_Audit_Report.md` |
| **ICT standard** | In some ICT teaching, MSS is a specific sub-case of CHoCH; in others it's used interchangeably. Either way, it's a *named* concept some traders expect to see |
| **Missing features** | The label itself; if it should carry different confirmation rules than plain CHoCH, that logic too |
| **Risks** | Low — this is largely a naming/taxonomy gap, not a correctness bug |
| **Impact on AI** | None — would only matter if MSS is given different scoring treatment than CHoCH, which has not been decided |
| **Difficulty** | Low if it's just a naming alias; Medium if it needs distinct confirmation logic |
| **Dependencies** | Depends on Phase 1's CHoCH fix being in place, and on a definitional decision (is MSS = CHoCH here, or a stricter subset?) |
| **Now or later** | **Later — Phase 4.** Genuinely optional; no evidence it changes any real trading outcome, only vocabulary |

### 6. Order Blocks

| | |
|---|---|
| **Current implementation** | `app/smc/order_blocks.py`'s `OrderBlockDetector` — scans backward for a momentum-candle-down-then-up (or mirrored) pattern, returns the first unbroken match. Direction is supplied externally (by `signal_generator.py`, `market_scorer.py`, `token_scorer.py`), not derived from `MarketStructure` |
| **ICT standard** | The last down-close candle before an up-move that broke structure (and vice versa) — a footprint of institutional order placement |
| **Missing features** | No explicit link to the BOS/CHoCH event that the OB is supposed to precede — direction is passed in from the caller's own BOS read, not verified against the specific structural break |
| **Risks** | Low-Medium — independent of the BOS duplicate-firing bug (verified: `OrderBlockDetector` takes only `df` and a direction string, zero coupling to `StructureBreak`), but inherits bad *direction* input if the caller's BOS read was wrong |
| **Impact on AI** | Direct — `app/ai/scorer.py`'s `order_block_quality` score, and the hard "no_confirmation" gate in `signal_generator.py` (`if not swept and not ob_dict: return None`) |
| **Difficulty** | Low to verify (no changes evidenced as required yet); Medium if an explicit OB-to-break linkage is added |
| **Dependencies** | Downstream of Market Structure's `direction` output only — benefits automatically once Phase 1 fixes BOS/CHoCH quality |
| **Now or later** | **Later — Phase 2.** No bug found in this module itself; re-verify behavior after Phase 1, no redesign needed now |

### 7. Breaker Blocks

| | |
|---|---|
| **Current implementation** | **Two separate, independent implementations exist.** `OrderBlockDetector.detect_breaker_block(ob, current_price)` in `order_blocks.py` (a simple "has price moved beyond this OB's boundary" check on an already-found OB) AND a completely separate `_find_broken_order_block(df, direction)` in `app/services/ta_dashboard.py`, which independently re-scans the candles for its own momentum-candle pattern from scratch, without reusing `OrderBlockDetector` at all |
| **ICT standard** | An order block price has since closed through, invalidating it as support/resistance and flipping its role |
| **Missing features** | A single, shared definition — the two implementations use different logic and could disagree with each other for the same candles |
| **Risks** | **Real, evidence-based duplicate-logic risk** — this is exactly the "avoid duplicate logic" pattern the project's own architecture (`02_System_Architecture.md`) and standing rules warn against. The two implementations can silently drift out of sync over time, similar in kind to the FVG situation before its migration |
| **Impact on AI** | Only `order_blocks.py`'s version is used in scoring paths reachable from `signal_generator.py`/`market_scorer.py`; `ta_dashboard.py`'s version only feeds a dashboard display field today — but if that ever changes, the disagreement becomes a real inconsistency |
| **Difficulty** | Medium — consolidating two working implementations into one requires deciding which definition is authoritative and re-verifying the dashboard's display output doesn't silently change |
| **Dependencies** | Depends on Order Blocks (item 6) being verified first |
| **Now or later** | **Later — Phase 2.** Not urgent (no proven live-behavior bug), but flagged clearly as a consolidation candidate, following the same "single source of truth" precedent as the FVG migration |

### 8. Mitigation Blocks

| | |
|---|---|
| **Current implementation** | `OrderBlockDetector.check_mitigation(ob, current_price)` — a one-line boolean: is the current price currently inside the OB's high/low range |
| **ICT standard** | A block that has been partially or fully revisited/filled by price, typically tracked as reducing that zone's future reliability |
| **Missing features** | No persistence (it's a point-in-time check, not a tracked history of whether/how much a block has been mitigated over time), no "reliability reduced" concept feeding back into scoring |
| **Risks** | Low — it's a simple, correct boolean for what it claims to check; the gap is depth of concept, not a bug |
| **Impact on AI** | Indirect — surfaced in `signal_generator.py`'s `ob_dict["mitigated"]` field, but not currently read by `app/ai/scorer.py`'s `order_block_quality` scoring (verified: that scoring block does not reference `mitigated`) |
| **Difficulty** | Low-Medium to add real tracking/scoring impact |
| **Dependencies** | Depends on Order Blocks (item 6) |
| **Now or later** | **Later — Phase 3.** A genuine enhancement, not a fix; no urgency |

### 9. Liquidity

| | |
|---|---|
| **Current implementation** | `app/smc/liquidity.py`'s `LiquidityDetector` — takes `swing_highs`/`swing_lows` directly from `MarketStructure` as constructor arguments |
| **ICT standard** | Pools of resting orders above/below significant swing points that price is drawn toward |
| **Missing features** | None identified as a correctness gap in this module itself |
| **Risks** | Inherits any quality issues from the `swing_highs`/`swing_lows` it's given — but has zero direct coupling to the BOS/CHoCH duplicate-firing bug (verified: this module never touches `StructureBreak`) |
| **Impact on AI** | Direct — `liquidity_sweep` score in `app/ai/scorer.py`, and the same `no_confirmation` hard gate as Order Blocks |
| **Difficulty** | Low — no changes evidenced as required |
| **Dependencies** | Downstream of `MarketStructure.swing_highs`/`.swing_lows` only |
| **Now or later** | **Later — Phase 2.** Re-verify after Phase 1, no redesign needed |

### 10. Equal Highs / Lows

| | |
|---|---|
| **Current implementation** | `LiquidityDetector.detect_equal_highs()`/`.detect_equal_lows()` — clusters swing highs/lows within a `tolerance` (default 0.05%) of each other, averages the cluster into one level |
| **ICT standard** | Multiple swing points at approximately the same price, marking an obvious resting-liquidity pool |
| **Missing features** | None identified — this is a reasonably faithful, working implementation of the concept |
| **Risks** | Low |
| **Impact on AI** | Feeds directly into `liquidity_sweep` scoring and confluence-zone construction |
| **Difficulty** | N/A — no change identified as needed |
| **Dependencies** | Same as item 9 |
| **Now or later** | **Later — Phase 2**, verification only, bundled with item 9 |

### 11. Buy-side / Sell-side Liquidity

| | |
|---|---|
| **Current implementation** | `LiquidityType.BUYSIDE`/`SELLSIDE` enum — buyside above equal highs, sellside below equal lows. Correctly modeled |
| **ICT standard** | Buyside liquidity rests above old highs (where short stops/breakout buys sit); sellside rests below old lows |
| **Missing features** | None identified |
| **Risks** | Low |
| **Impact on AI** | Same as items 9-10 |
| **Difficulty** | N/A |
| **Dependencies** | Same as item 9 |
| **Now or later** | **Later — Phase 2**, bundled with items 9-10 |

### 12. Liquidity Sweeps

| | |
|---|---|
| **Current implementation** | `LiquidityDetector.detect_liquidity_sweeps(levels)` — marks a level `.swept = True` if the CURRENT (last) candle's high/low has traded through it |
| **ICT standard** | Price briefly trades through a liquidity pool (triggering stops) before reversing — a stop-hunt |
| **Missing features** | No reversal confirmation — the current check is "did price trade through the level," not "did price trade through AND then reverse," which is the fuller ICT definition of a genuine sweep vs. a plain breakout |
| **Risks** | Medium — this could mean some flagged "sweeps" are actually just breakouts that never came back, which is a real definitional gap, though not proven to be firing incorrectly at scale (no execution-based measurement done for this module, unlike BOS) |
| **Impact on AI** | Direct — `liquidity_sweep` score and the `no_confirmation` hard gate both depend on this |
| **Difficulty** | Medium — needs a "did price close back on the other side" or similar reversal check added |
| **Dependencies** | Same swing/level dependencies as items 9-11 |
| **Now or later** | **Later — Phase 3.** A real, plausible gap, but unproven at the scale BOS's bug was proven — recommend measuring before changing, following the same evidence-first pattern used for BOS |

### 13. Fair Value Gaps — verification of the completed migration

| | |
|---|---|
| **Current implementation** | `app/smc/fvg.py`'s `FVGDetector.detect_fvg()` — confirmed, by direct re-reading, to be the standard ICT 3-candle definition (`range(2, len(self.df))`, compares candle `i-2` to candle `i`). The legacy 2-candle method no longer exists in the file |
| **ICT standard** | Met — this is the correct 3-candle definition |
| **Missing features** | None identified against the core definition |
| **Risks** | None new — `FVG_ICT_Promotion_Report.md`'s regression test (real, unmodified module, synthetic data) confirmed 450 detections on 700 candles where the old method always returned 0 |
| **Impact on AI** | Positive — `fvg_presence` scoring and confluence-zone construction now receive real data instead of a permanently-empty list |
| **Difficulty** | N/A — already done |
| **Dependencies** | None outstanding |
| **Now or later** | **Done.** Included here only for completeness/audit trail, per the assignment's explicit instruction to verify it |

### 14. Inverse FVG

| | |
|---|---|
| **Current implementation** | `FVGDetector.detect_inverse_fvg()` — takes all gaps from `self.detect_fvg()` (now the ICT method automatically, per item 13), and re-labels any FILLED gap as the opposite type, marked `filled=True` |
| **ICT standard** | A filled FVG can act as support/resistance in the opposite role it originally played |
| **Missing features** | None identified as a correctness gap; verified functional post-migration (regression test in `FVG_ICT_Promotion_Report.md` confirmed it runs and returns real inverse gaps) |
| **Risks** | Low — this method required zero code changes during the FVG migration since it calls `self.detect_fvg()` by name |
| **Impact on AI** | Not currently read by `app/ai/scorer.py` (verified: no reference to `detect_inverse_fvg` or an "inverse" key in the scoring feature dict) — currently unused output |
| **Difficulty** | N/A for correctness; Low-Medium if it should be wired into scoring |
| **Dependencies** | Depends on item 13 only (already resolved) |
| **Now or later** | **Later — Phase 3**, if/when wiring it into scoring is desired; no bug to fix now |

### 15. Supply & Demand

| | |
|---|---|
| **Current implementation** | `app/smc/supply_demand.py`'s `SupplyDemandZones` — a fixed 50-candle lookback range (`calculate_recent_range`), classifying the current price as premium/discount/equilibrium |
| **ICT standard** | Zones anchored to the specific dealing range defined by the structure currently in play (the swing high/low that define the current leg), not a fixed rolling lookback |
| **Missing features** | Structure-anchored range — currently uses a fixed 50-candle window regardless of where the actual relevant swing points are |
| **Risks** | Medium — a fixed lookback can mis-classify price relative to the *wrong* range during a strong trend or after a big structural shift, since it doesn't know where the current leg actually started |
| **Impact on AI** | Direct — `supply_demand_zone` score in `app/ai/scorer.py` |
| **Difficulty** | Medium — requires wiring `MarketStructure`'s actual swing points into the range calculation instead of a fixed lookback |
| **Dependencies** | Depends on Phase 1/2's `MarketStructure` improvements to have meaningful swing points to anchor to |
| **Now or later** | **Later — Phase 3** |

### 16. Premium / Discount

| | |
|---|---|
| **Current implementation** | **Already implemented**, inside `SupplyDemandZones.get_zone()` — a documented, deliberately-fixed 2026-07-26 bug fix already exists here (the discount/premium split was previously asymmetric; now correctly symmetric at the 0.382/0.618 retracement of the 50-candle range) |
| **ICT standard** | Same concept (price above 61.8% of a dealing range = premium/sell zone, below 38.2% = discount/buy zone) — met, EXCEPT the range itself should be structure-anchored per item 15, not a fixed lookback |
| **Missing features** | Structure-anchored range (same gap as item 15 — they share the same underlying `range_high`/`range_low`) |
| **Risks** | Same as item 15 — correct math, questionable range source |
| **Impact on AI** | Direct — same `supply_demand_zone` score |
| **Difficulty** | Zero extra work beyond item 15 — fixing the range source fixes both |
| **Dependencies** | Same as item 15 |
| **Now or later** | **Later — Phase 3**, bundled with item 15. **Correction to the earlier BOS-focused audit:** `BOS_ICT_Audit_Report.md` listed Premium/Discount as "absent" — that was accurate only within `market_structure.py`'s own direct dependents; a full-engine review (this document) finds it genuinely implemented elsewhere, in `supply_demand.py` |

### 17. Optimal Trade Entry (OTE)

| | |
|---|---|
| **Current implementation** | **Not implemented.** Confirmed by a full-project search for "OTE"/"Optimal Trade Entry" — zero matches. Entry price construction in `signal_generator.py` uses the live 1-minute close directly, not a Fibonacci retracement zone of an impulse leg |
| **ICT standard** | Enter within the 62%-79% retracement zone of the most recent displacement leg, rather than at market price immediately on signal |
| **Missing features** | The entire concept — no retracement-zone-based entry timing exists anywhere |
| **Risks** | Low to add (purely additive, wouldn't remove any existing entry logic) — but changes *when* a trade would actually fill, a real behavior change if made the default rather than an option |
| **Impact on AI** | None on scoring directly; would affect `signal_generator.py`'s entry price and therefore realized risk/reward and backtested win rate if enabled |
| **Difficulty** | Medium — needs a defined "displacement leg" to retrace from, which itself benefits from Phase 3's Displacement work (item 18) being in place first |
| **Dependencies** | Displacement (item 18), and ideally Phase 1/2's structure fixes, so the leg being retraced is measured from accurate swing points |
| **Now or later** | **Later — Phase 4.** A genuinely new capability, not a fix; sequenced after Displacement since OTE is defined in terms of it |

### 18. Displacement

| | |
|---|---|
| **Current implementation** | Exists only downstream, in `app/ai/scorer.py`'s `market_structure` scoring: `displacement_ratio = abs(current_price - latest.level) / atr`, used as a confidence bonus (up to +26 points) |
| **ICT standard** | A strong, momentum-driven move used to CONFIRM a break is real institutional intent, checked at the moment of detection, not just scored afterward |
| **Missing features** | Detection-time gating — today, a break of any size (even one tick) qualifies as BOS/CHoCH; displacement only affects the score afterward, never whether a break is accepted at all |
| **Risks** | Contributes to the BOS over-firing problem (item 1) — tiny, insignificant breaks are treated identically to strong ones at detection time |
| **Impact on AI** | Currently already a scoring input; adding a detection-time gate would change signal frequency (see `BOS_ICT_Migration_Plan.md` Step 3 for the exact design: an opt-in, profile-driven minimum threshold, defaulting to no gate for full backward compatibility) |
| **Difficulty** | Low-Medium — the ratio calculation already exists and is proven; wiring it as an optional gate (not just a score) is the only new work |
| **Dependencies** | Should follow Phase 1 (event-identity fix) so the "before/after" frequency impact of adding a displacement gate can be measured against an honest baseline, not one still inflated by duplicate counting |
| **Now or later** | **Later — Phase 3**, exactly as designed in `BOS_ICT_Migration_Plan.md` Step 3 |

### 19. Strong vs Weak Swing

| | |
|---|---|
| **Current implementation** | Not implemented. Every geometrically-qualifying pivot in `_detect_swings()` is treated identically — no quality/strength score exists on `SwingPoint` |
| **ICT standard** | Classify swings by the quality of the move that created them (displacement vs. no displacement behind the swing) |
| **Missing features** | The entire concept |
| **Risks** | Low to add (purely additive); the current lack of it means weak, noise-driven swings are weighted the same as strong, momentum-driven ones everywhere a swing is used (BOS, CHoCH, Liquidity, Order Block direction) |
| **Impact on AI** | Would be a NEW scoring input if wired in — not currently touching `app/ai/scorer.py` at all |
| **Difficulty** | Medium — likely reuses the same displacement-ratio concept as item 18, applied to swings instead of breaks |
| **Dependencies** | Natural to build alongside Displacement (item 18), since both use the same underlying "how much real movement was behind this" measurement |
| **Now or later** | **Later — Phase 3**, bundled conceptually with item 18 |

### 20. Protected High / Low

| | |
|---|---|
| **Current implementation** | Not implemented. No field, attribute, or persisted concept anywhere marks a swing as a standing reference level |
| **ICT standard** | A swing that must not be violated for the current bias to remain valid, tracked as a standing reference across many candles |
| **Missing features** | The entire concept, AND the underlying architecture to support it — `MarketStructure` is currently fully stateless (recomputed from scratch on every call), while Protected High/Low inherently requires state that persists across time, not just within one `df` window |
| **Risks** | This is the most architecturally invasive item in the entire list — done carelessly, it risks re-introducing a version of the same "no persistence" class of bug that caused the BOS duplicate-firing problem, just in a new form |
| **Impact on AI** | Would be a new, potentially high-value scoring input, but only once designed correctly |
| **Difficulty** | High — the only item in this document assessed as needing its own dedicated design pass before implementation is even scoped, exactly as `BOS_ICT_Migration_Plan.md` Step 5 already concluded |
| **Dependencies** | Phase 1 (event-identity fix, which establishes the precedent for where state should live — at the `SignalGenerator` layer, not inside `MarketStructure` itself) |
| **Now or later** | **Later — Phase 4, explicitly deferred**, consistent with the existing migration plan's own conclusion |

### 21. Inducement

| | |
|---|---|
| **Current implementation** | Not implemented. No code anywhere models a deliberate minor swing designed to trap traders before the real move |
| **ICT standard** | A liquidity grab at a minor, low-significance swing that precedes the real structural move — distinguishing it from a legitimate sweep at a major level requires judgment about swing *significance*, not just price crossing a level |
| **Missing features** | The entire concept, and it depends on Strong/Weak Swing (item 19) being solved first — you cannot identify "a minor swing used as a trap" without first being able to distinguish minor from major swings at all |
| **Risks** | High risk of false positives if implemented without Strong/Weak Swing and Protected High/Low first — inducement is one of the more subjective, pattern-judgment-heavy ICT concepts, and coding it against noisy swing data (today's state) would likely produce unreliable signals |
| **Impact on AI** | Speculative — no evidence-based estimate possible without the prerequisite work |
| **Difficulty** | High |
| **Dependencies** | Item 19 (Strong/Weak Swing) and item 20 (Protected High/Low) |
| **Now or later** | **Later — Phase 4, explicitly deferred**, same conclusion as `BOS_ICT_Migration_Plan.md` Step 6 |

### 22. Multi-Timeframe Structure

| | |
|---|---|
| **Current implementation** | Two different things exist today, and they are NOT the same as this concept: (1) EMA20/50 TREND direction agreement across 1h/4h/1d/5m timeframes (`htf_trend_1h`, `htf_trend_4h`, `htf_trend_1d`, `entry_trend_5m` in `signal_generator.py`/`scanner.py`) — a trend-direction check, not structure detection; (2) `ta_dashboard.py`'s three `MarketStructure` instances with different `pivot_window` values — all computed on the SAME 15m timeframe, not on actually-different timeframes |
| **ICT standard** | Recompute BOS/CHoCH/swing structure independently on multiple real timeframes (e.g., 15m, 1H, 4H), and reconcile bias across them |
| **Missing features** | Genuine multi-timeframe STRUCTURE detection — today's "multi-timeframe" signals are all trend-direction checks (EMA slope), not re-run SMC structure analysis on higher timeframes |
| **Risks** | Low to add (purely additive), but real design complexity in reconciling disagreements between timeframes (e.g., bullish 15m BOS against a bearish 4H structure) |
| **Impact on AI** | Would be a significant new scoring dimension — today's `institutional` feature block already has 4 timeframe-trend fields feeding `multi_tf_alignment`-style scoring; adding real structure (not just trend) per timeframe would meaningfully deepen this, not duplicate it |
| **Difficulty** | High — needs fetching/maintaining candle data for additional timeframes (partially already done via `mtf_dfs` in `ta_dashboard.py` and the scanner's existing multi-timeframe candle caching), then running the full (post-Phase-1/2) `MarketStructure` pipeline per timeframe |
| **Dependencies** | Depends on Phase 1/2's `MarketStructure` fixes — running a flawed algorithm on more timeframes would just multiply the existing bug's surface area, not fix anything |
| **Now or later** | **Later — Phase 4.** Highest implementation cost of any item in this document; sequence last, after every timeframe's underlying structure detection is trustworthy |

---

## Section B — Phase roadmap

### Phase 1 (Critical)

**Contents:** Market Structure/BOS event-identity fix, CHoCH (same fix, bundled), FVG verification (already complete, included for audit trail).

**Why it comes first:** this is the only phase addressing a PROVEN correctness bug, not a missing feature. `BOS_Duplicate_Investigation_Report.md` established via direct execution of the real, unmodified code that the same structural break can be reported dozens of times as if new. Every other module in this document either reads `MarketStructure`'s output directly (Order Blocks' direction, Liquidity's swing points) or is scored using it (`app/ai/scorer.py`'s `market_structure` category). Building anything else on top of this before fixing it means building on a foundation known to be wrong.

**What depends on it:** items 3, 4 (Internal/External Structure), 6 (Order Blocks, indirectly via direction quality), 9-12 (Liquidity family, indirectly via swing quality), 15-16 (Supply & Demand/Premium-Discount, once structure-anchored), 18 (Displacement gating needs an honest baseline), 22 (Multi-Timeframe Structure would just multiply the bug across timeframes if built first).

**Expected impact:** likely reduction in signal frequency (duplicate suppression, not fewer real opportunities — magnitude unmeasured, see `BOS_ICT_Migration_Plan.md` Section 6), AI confidence distribution shift (scores will reflect a break's true age instead of climbing on a stale one), and a need to recalibrate (`app/ai/calibration.py`'s weights were tuned against the current, inflated break rate).

**Estimated implementation complexity:** Low. Per `BOS_ICT_Migration_Plan.md`'s design, the fix lives at the `SignalGenerator` layer (a small, additive piece of per-symbol state, since `SignalGenerator` already persists per-symbol via the scanner's `self.generators` dict) — `market_structure.py` itself is not modified for this specific fix, keeping the change small and isolated.

**Required regression tests:** re-run the corrected diagnostic script (Step 0 of `BOS_ICT_Migration_Plan.md`) to get an honest before/after BOS and CHoCH frequency; confirm Order Blocks/Liquidity/Dashboard/Token Scan continue to function unchanged (they read `MarketStructure` directly and are not touched by this fix); confirm backtesting (`app/backtest/engine.py`, which shares `SignalGenerator` with live) reflects the same corrected behavior, not a divergent one.

---

### Phase 2 (High)

**Contents:** Market Structure swing-selection order fix, Internal/External Structure formalization, Order Blocks (verification only), Breaker Blocks (consolidate the two independent implementations), Liquidity/Equal Highs-Lows/Buy-Sell-side (verification only).

**Why it comes first (after Phase 1):** these are the modules that directly consume `MarketStructure`'s corrected output. None of them have a proven bug at Phase 1's severity, but Breaker Blocks has a real, evidence-based duplicate-logic problem (two independent implementations that can disagree), and Internal/External Structure currently exists only as three unrelated numbers rather than a model — both are "do it right" items, not "fix a measured bug" items, which is why they rank below Phase 1 but above net-new features.

**What depends on it:** item 15/16 (Supply & Demand/Premium-Discount's structure-anchoring, Phase 3), item 22 (Multi-Timeframe Structure, Phase 4) — both need Internal/External Structure's model decided first.

**Expected impact:** more consistent breaker-block reporting between the scoring path and the dashboard path; Internal/External Structure becoming a usable signal rather than decorative dashboard text; no expected change to signal frequency (Order Blocks/Liquidity verification is not expected to change behavior, only confirm it).

**Estimated implementation complexity:** Medium. Swing-selection order is a small, isolated change; Breaker Block consolidation requires picking one authoritative definition and re-verifying the dashboard's display output; Internal/External Structure requires new design work (a real relationship between the two reads), not just wiring.

**Required regression tests:** CHoCH-specific regression (shares swing-selection logic with BOS), dashboard visual/data regression for the "Market Structure"/"Breaker Blocks" panel fields, and confirmation that `order_blocks.py`'s and `ta_dashboard.py`'s breaker-block outputs agree after consolidation.

---

### Phase 3 (Medium)

**Contents:** Displacement (detection-time gate), Strong vs Weak Swing, Mitigation Blocks (real tracking), Liquidity Sweeps (reversal confirmation), Supply & Demand + Premium/Discount (structure-anchored range), Inverse FVG (wire into scoring, optional).

**Why it comes first (after Phase 2):** these are genuine ICT depth enhancements with no proven live-behavior bug behind them (unlike Phase 1) and no cross-module duplicate-logic risk (unlike Phase 2's Breaker Blocks) — they improve accuracy and nuance rather than fix something broken or inconsistent. Liquidity Sweeps' reversal-confirmation gap is the closest thing to a "bug" in this phase, but it is unproven at the scale BOS's bug was proven, so it is sequenced here rather than Phase 1, pending its own measurement.

**What depends on it:** item 17 (OTE, Phase 4) is explicitly defined in terms of Displacement (item 18), so OTE cannot be meaningfully designed before this phase ships.

**Expected impact:** each item is independently additive and mostly opt-in (Displacement gating is explicitly designed as opt-in/profile-driven per `BOS_ICT_Migration_Plan.md`) — expected to change AI Scoring's input quality and, if displacement gating is tuned to be more than a no-op, signal frequency and calibration, similar in kind (smaller in scope) to Phase 1's impact.

**Estimated implementation complexity:** Medium across the board — each item is a bounded, single-module change, but there are six of them, so total phase effort is larger than Phase 1 or 2 individually.

**Required regression tests:** AI Scorer output comparison (since `market_structure`, `supply_demand_zone`, and potentially new Strong/Weak Swing scoring inputs all change), a dedicated before/after liquidity-sweep frequency measurement (following the same evidence-first pattern as the BOS investigation), and calibration re-validation once real trade outcomes exist under the new behavior.

---

### Phase 4 (Advanced ICT)

**Contents:** Optimal Trade Entry, Protected High/Low, Inducement, Market Structure Shift (as a distinct label), Multi-Timeframe Structure.

**Why it comes first (i.e., why it comes LAST):** every item here either requires genuine new architecture (Protected High/Low needs persistent state beyond anything built in Phases 1-3; Multi-Timeframe Structure needs the full structure pipeline re-run per timeframe), or depends on a chain of earlier prerequisites (Inducement needs Strong/Weak Swing AND Protected High/Low; OTE needs Displacement), or is purely definitional with no measured impact (MSS as a distinct label). None of these are corrections to existing behavior — they are net-new capability, and the audit's own conclusion (`BOS_ICT_Audit_Report.md`: "Needs Major Improvements," explicitly not "Should Be Rewritten") supports building incrementally rather than attempting all of this at once.

**What depends on it:** nothing else in this document depends on Phase 4 — it is the leaf level of the dependency tree.

**Expected impact:** unknown/speculative for Inducement and Protected High/Low without their own dedicated design passes (explicitly deferred, not designed here); OTE would change realized entry price and therefore risk/reward math if enabled; Multi-Timeframe Structure would be the single largest scoring-model change in this entire roadmap if implemented, given how central "does the higher timeframe agree" already is to the existing hard gates in `signal_generator.py`.

**Estimated implementation complexity:** High across the board — this phase contains every item in the document rated "High" difficulty, plus the two items (Protected High/Low, Inducement) explicitly called out as needing their own separate design investigations before implementation is even scoped.

**Required regression tests:** full end-to-end regression (AI Scoring, Signal Generator, Backtesting, Technical Dashboard) for each item individually — given the complexity and the compounding dependency chain, these should not be batched together; each item needs its own before/after evidence, following the same standard set by the BOS investigation, before the next one begins.

---

## Summary table

| Phase | Modules | Complexity | Prerequisite for |
|---|---|---|---|
| 1 (Critical) | Market Structure/BOS, CHoCH, FVG (verify) | Low | Everything else |
| 2 (High) | Swing-selection order, Internal/External Structure, Order Blocks (verify), Breaker Blocks (consolidate), Liquidity family (verify) | Medium | Phase 3's structure-anchored items, Phase 4's Multi-Timeframe Structure |
| 3 (Medium) | Displacement (gate), Strong/Weak Swing, Mitigation Blocks, Liquidity Sweeps (reversal), Supply & Demand + Premium/Discount, Inverse FVG (wire in) | Medium | Phase 4's OTE |
| 4 (Advanced ICT) | OTE, Protected High/Low, Inducement, MSS (label), Multi-Timeframe Structure | High | Nothing (leaf level) |

No code has been written. No files have been modified. Waiting for approval before implementing Phase 1.
