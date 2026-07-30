# ICT Migration — Phase C Implementation Report

**Date:** 2026-07-29
**Scope:** Inducement Detection + Evidence Engine Foundation (per Phase C instructions). OTE and the AI Scoring rebuild were explicitly out of scope and were not touched.

---

## 1. Architecture decisions

**Inducement as a pure interpretation layer, not a new detector.** `InducementEngine` re-detects nothing: it consumes `LiquidityLevel` objects already produced by `LiquidityEngine.detect_liquidity_sweeps()` and looks up matching `StructureBreak` objects from the same `structure_breaks` list already passed to `LiquidityEngine`. The only genuinely new logic is the "continuation toward institutional objective" check (item 7), which is a new, disclosed measurement over the OHLCV window — not a duplicate of any existing BOS/CHoCH/sweep detector.

**Strict AND-gate, not a scored heuristic.** All 8 conditions from the Phase C brief (liquidity build-up, Equal Highs/Lows, engineered liquidity, retail trap, liquidity sweep, strong displacement, BOS/CHoCH confirmation, continuation) must hold simultaneously. Any single missing condition means no `InducementEvent` is emitted at all — no partial credit, no soft/maybe classification. This directly implements "If these conditions are missing, DO NOT generate inducement."

**Evidence Engine named `ICTEvidenceEngine`, deliberately not `EvidenceEngine`.** Before writing any code, I read the existing `app/agent/evidence_engine.py` in full (12KB, part of the Trading Agent subsystem) to check for a naming/responsibility collision. It already defines an `EvidenceEngine` class — but it is a **post-hoc chat-explanation formatter**: it runs *after* a decision already exists (`AgentDecision`, built from today's retail-mixed `AIScorer` output) and turns it into human-readable text for the conversational agent. That is a fundamentally different responsibility from what Phase C asked for — an evidence-**collection** layer that runs *before* any confidence/decision exists, generic enough for the future rebuilt AI Scorer to consume. Reusing the bare name `EvidenceEngine` here would have created exactly the confusing, duplicate-sounding module this whole migration is meant to eliminate. The new module is `app/ai/evidence_engine.py`, class `ICTEvidenceEngine`, with `ICTEvidenceItem`/`ICTEvidenceReport`/`ICTEvidenceInputs` — its own module docstring explains the distinction explicitly so a future maintainer (or a future phase of this same migration) never re-confuses the two.

**Evidence Engine is collection-only — confirmed no scoring anywhere.** Every `_*_evidence()` builder takes an already-computed object and returns `ICTEvidenceItem`s tagged with a `polarity` (`bullish`/`bearish`/`neutral`) — a plain directional label (e.g. a Bullish CHoCH is obviously bullish evidence), never a numeric weight or confidence contribution. There is no summation, no weighting table, and no aggregate score field anywhere in `ICTEvidenceReport`.

**Volume Confirmation is allow-listed, not block-listed.** `_ALLOWED_VOLUME_KEYS = ("cvd", "cvd_rising", "poc_price", "relative_volume")` — the module reads *only* these four keys from `ConfirmationIndicators.get_latest()`'s dict, even if a caller passes the full dict containing retail keys (rsi, macd, supertrend, etc.). This is enforced by construction (the code only ever calls `.get()` on these four names), not by a comment — verified directly by a test that passes a dict containing retail keys and asserts none of that text leaks into the produced evidence.

---

## 2. Files added

| File | Purpose |
|---|---|
| `app/smc/inducement_engine.py` | `InducementEngine`, `InducementEvent` — Inducement detection as an interpretation layer over `LiquidityEngine`/`MarketStructureEngine` output. |
| `app/ai/evidence_engine.py` | `ICTEvidenceEngine`, `ICTEvidenceItem`, `ICTEvidenceReport`, `ICTEvidenceInputs`, `ICTEvidenceCategory` — the Evidence Engine Foundation. |
| `tests/test_inducement_engine.py` | 17 tests: the full positive case, one dedicated rejection test per individual missing condition, the bullish mirror, determinism, non-mutation, mixed-batch isolation. |
| `tests/test_evidence_engine.py` | 42 tests: every builder method (honest degradation + correct polarity), the volume allow-list guarantee, the polarity validation guard, full `compile()` integration, and a direct distinctness check against `app.agent.evidence_engine.EvidenceEngine`. |

## 3. Files modified

**None.** No existing production file was edited — not `liquidity_engine.py`, not `market_structure_engine.py`, not `order_block_engine.py`, not `supply_demand_engine.py`, not `app/agent/evidence_engine.py`, not any file from Phase A or Phase B. Confirmed via `git status` — `app/smc/liquidity_engine.py` shows no modification marker, only "new/untracked," identical to its state before this phase began.

## 4. ICT logic implemented

**Inducement** — the 8-condition gate described in Section 1, mapped exactly onto already-computed fields:

| Required condition | Source (reused, not recomputed) |
|---|---|
| Liquidity build-up / Equal Highs-Lows | `LiquidityLevel.kind in (EQUAL_HIGHS, EQUAL_LOWS)` |
| Engineered liquidity | `LiquidityLevel.engineered` |
| Retail trap | `LiquidityLevel.sweep_outcome == GRAB` (not `BREAKOUT`) |
| Liquidity sweep | `LiquidityLevel.swept and .sweep_confirmed` |
| BOS or CHoCH confirmation | `LiquidityLevel.reversal_break_level is not None`, matched back to the real `StructureBreak` object |
| Strong displacement | matched `StructureBreak.displacement_ratio >= 1.2` (configurable) |
| Continuation toward institutional objective | new: price extends beyond the break level within 5 candles (configurable) **and** never closes back through the original swept price |

**Evidence Engine Foundation** — one builder method per evidence category from the Phase C example output, each reading only already-computed fields: Market Structure (Bullish/Bearish BOS/CHoCH), Liquidity Sweep, Valid Inducement, Order Block (strength-labeled), FVG (Filled/Present), Supply/Demand Zone, Premium/Discount, Session/Kill Zone, HTF Bias, Volume Confirmation (CVD/POC/Relative Volume only), Institutional Bias (caller-formatted notes), Risk Notes (caller-formatted), and Invalidation (from `protected_high`/`protected_low`). `compile()` orchestrates all of them from one optional-everything `ICTEvidenceInputs` bundle.

## 5. Test coverage

- Unit tests: every builder method in both new files, individually.
- Regression tests: all four pre-existing ICT engine suites (Market Structure 17, Order Blocks 21, Liquidity 20, Supply & Demand 23) plus Session Engine (26) re-run unchanged.
- Edge case tests: insufficient data for continuation, round-tripped continuation, no matching structure break supplied, unswept/unconfirmed levels, invalidated Order Blocks/broken zones excluded, `None`/empty inputs at every builder.
- False positive tests: one dedicated test per individual missing Inducement condition (10 rejection tests), plus a mixed-batch test proving only the genuinely valid level in a batch produces an event.
- Deterministic behaviour: `InducementEngine.detect()` called twice with identical inputs produces identical results; `LiquidityLevel` objects are proven unmutated after Inducement detection runs.
- Performance: not separately benchmarked — both new modules are bounded, small-constant-window scans (identical complexity class to the engines they build on), no new O(n²) or unbounded loops introduced.

## 6. Regression results

**165/166 checks passed** across all 7 committed test files (Market Structure 17/17, Order Blocks 21/21, Liquidity 20/20, Supply & Demand 23/23, Session 26/26, Inducement 17/17, Evidence Engine 41/42), verified against the real, unmodified production files using the same offline shim-based method established in Phase A (no network egress is available to install `pytest`/`sqlalchemy` in this environment).

The one non-passing check, `TestDistinctFromAgentEvidenceEngine.test_classes_are_distinct`, fails **only** because `app.agent.evidence_engine` transitively imports `sqlalchemy` (via `risk_manager.py`) and `loguru` (via `calibration.py`), neither of which is installed in this offline verification sandbox — traced the full import chain to confirm it never touches any file this phase added or modified. This is a disclosed sandbox limitation, the same category already documented in the Supply & Demand phase; the test will run for real the next time `pytest` executes with the project's actual dependencies installed (CI, or the local Windows venv).

**Confirmed: Inducement detection never changes `LiquidityEngine`'s own output.** `LiquidityEngine.detect_liquidity_sweeps()`'s 20/20 regression suite passed unchanged, `liquidity_engine.py` has zero modifications (confirmed via `git status`), and a dedicated non-mutation test proves `InducementEngine.detect()` does not alter any `LiquidityLevel` field it reads.

## 7. Performance impact

None on any existing code path — both new modules are standalone and are not yet called from `signal_generator.py`, `market_scorer.py`, `token_scorer.py`, `ta_dashboard.py`, or the live scanner. `InducementEngine.detect()` is O(levels × structure_lookahead) for the break lookup plus O(levels × continuation_lookahead) for the continuation check — both small, bounded constants (defaults 20 and 5), the same order of magnitude as the other engines' own confluence-lookup windows. `ICTEvidenceEngine.compile()` is a single linear pass over whatever lists are supplied — no new scans of the OHLCV dataframe at all.

## 8. Remaining work before Phase D

Per the Phase C instructions, OTE and the AI Scoring rebuild were explicitly not attempted. Neither new module is wired into any live caller yet — that wiring (and the still-pending: Institutional Bias data-sourcing formalization, HTF snapshot plumbing into `signal_generator.py`, and eventually feeding `ICTEvidenceReport` into the rebuilt AI Scorer) remains for later phases per the original migration plan.

---

**Stopping here per Phase C instructions — awaiting approval before Phase D.**
