# ICT Migration — Final Phase Implementation Report

**Date:** 2026-07-29
**Scope:** Complete the entire remaining ICT migration (OTE, Institutional Bias, HTF Structure, Entry, Entry Validation, Exit, Trade Management, Unified Risk, AI Scoring rebuild, Evidence Engine integration, Signal Generator integration) — backtest-ready, backtesting itself not implemented or modified.

---

## 1. Architecture Decisions

Every new engine follows the same pattern already established in Phases A–C: stateless, pure-Python interpretation layers over already-computed objects from other engines — no engine re-detects what another engine already detected, and every constructor argument degrades honestly to `None`/empty/neutral rather than guessing when data is missing.

Three architectural decisions shaped this phase specifically:

- **SL/TP computation moved earlier in `SignalGenerator.generate()`.** It previously ran after the confidence gates; it has never depended on confidence, only on direction/ATR/order-block/swing structure, all already known earlier. Moving it up let the real Risk:Reward ratio feed into AIScorer's new `risk` category — previously impossible, since RR wasn't known until after scoring. This is a pure reordering: the same reject gates still fire, in the same logical conditions, just now grouped after scoring instead of split before/after it.
- **Entry Engine / Exit Engine were integrated as additive evidence, not as replacements for the live trade parameters.** `entry_type` and `exit_plan` are new informational fields on the returned signal dict; the numeric `entry`/`stop_loss`/`take_profit` used for the actual trade and downstream trade monitoring remain exactly the pre-existing structure-based construction. Switching the live entry price to a pending OTE/OB/FVG zone price would be a real, ICT-correct improvement, but it touches trade-monitoring code (`app/scheduler/signal_monitor.py`) that is adjacent to this phase's scope and was never audited here — an unaudited behavior change to that code was judged higher-risk than shipping the new engines as disclosed, additive evidence first.
- **Unified Risk Engine was built as a standalone, reusable engine (`app/risk/risk_engine.py`) but was *not* wired directly into `SignalGenerator`.** `SignalGenerator` is symbol-agnostic and has never held account balance, open positions, or today's closed trades — that state has always lived at the API/position-sizing layer (`app/services/position_sizing.py`, which `RiskEngine` reuses by import, not duplication). Forcing account-specific state into the symbol-agnostic signal-generation path would have been a real architecture regression. `RiskEngine` is positioned to be called from that same API layer in a follow-up; in the meantime, `SignalGenerator`'s own computed Risk:Reward ratio feeds AIScorer's `risk` category and `EntryValidationEngine`'s `risk` check.

---

## 2. Files Added

| File | Purpose |
|---|---|
| `app/smc/htf_structure_engine.py` | One reusable multi-timeframe (1W/1D/4H/1H) Market Structure snapshot — wraps `MarketStructureEngine`, detects nothing new. |
| `app/smc/institutional_bias_engine.py` | Weighted Weekly/Daily/4H structure + Premium/Discount → single `bullish`/`bearish`/`conflicted`/`neutral` bias. Never reads EMA/RSI/MACD/any retail indicator. |
| `app/smc/ote_engine.py` | Optimal Trade Entry zone detection — displacement + Order Block/FVG/Liquidity confluence + Premium/Discount alignment, strict AND-gated (not a bare Fibonacci calculator). |
| `app/strategy/entry_engine.py` | Entry type/price selection: OTE > Order Block > FVG > Supply/Demand > Limit > Market, in priority order. |
| `app/strategy/entry_validation_engine.py` | The "reject incomplete setups" gate — institutional bias, HTF alignment, confluence, session/kill zone, risk, invalidation, all checked together. |
| `app/strategy/exit_engine.py` | Liquidity/structure target selection, partial exits, breakeven trigger, trailing-structure flag, ATR fallback. |
| `app/strategy/trade_management_engine.py` | Open-position monitoring actions (partial close, breakeven, trail stop, structure/liquidity failure close, session-change note). Built and tested; not yet wired into the live monitoring loop — see §12. |
| `app/risk/__init__.py`, `app/risk/risk_engine.py` | Unified Risk Engine — merges Position Sizing (reused), Correlation Risk (advisory-only, preserved), Portfolio Exposure, Open Risk, Daily Risk, Drawdown into one engine. |
| `tests/test_htf_structure_engine.py` (9), `test_institutional_bias_engine.py` (13), `test_ote_engine.py` (11), `test_entry_engine.py` (8), `test_entry_validation_engine.py` (11), `test_exit_engine.py` (12), `test_trade_management_engine.py` (10), `test_risk_engine.py` (12), `test_scorer.py` (28) | New regression suites — 114 new tests, all passing. |

## 3. Files Modified

| File | Change |
|---|---|
| `app/ai/scorer.py` | Full internal rewrite (588 → 492 lines). 13 ICT-only categories, zero retail indicators. `assess()`'s signature is unchanged. Full disclosure of what was removed and where each piece went is in the file's own module docstring. |
| `app/ai/calibration.py` | `DEFAULT_WEIGHTS` replaced: old 13 retail-mixed category keys → new 13 ICT-only keys, sums to 1.00. `calibration_profiles.py` required zero changes (derives `default_weights` dynamically from `DEFAULT_WEIGHTS`). |
| `app/strategy/signal_generator.py` | Integration rewrite (344 → 562 lines). Every new engine wired in — see §8. `generate()`'s existing positional signature is 100% preserved; only one new trailing optional kwarg (`htf_dataframes`) was added. |
| `app/services/binance_service.py` | Added `get_weekly_dataframe()` (mirrors `get_daily_dataframe()`'s cached-REST pattern) + `_weekly_df_cache` + `WEEKLY_TREND_CACHE_TTL`. |
| `app/scheduler/scanner.py` | Wired real 1W/1D/4H/1H OHLCV fetching and passes it to `generate()` as `htf_dataframes`. |
| `tests/test_calibration.py` | Sample category names updated from the retired retail set to the new ICT set; `weights_from_samples()` itself is unmodified production code. |

**Retired:** `tests/test_ai_scorer.py` — every one of its assertions referenced category names (`liquidity_sweep`, `confluence`, `multi_tf_alignment`, `fundamental_context`, …) that no longer exist after the scorer rewrite. `tests/test_scorer.py` (28 tests) is its direct, more comprehensive replacement for the same class.

---

## 4. ICT Concepts Completed This Phase

OTE (Optimal Trade Entry), Institutional Bias, Higher-Timeframe Structure (Weekly/Daily/4H/1H), Entry Model (Market/Limit/OTE/Order Block/FVG/Supply-Demand), Entry Validation, Exit Model (liquidity/structure targets, partial exits, breakeven, trailing structure), Trade Management (position monitoring actions), Unified Risk Engine.

Combined with Phases A–C (already complete before this phase began): Market Structure, Liquidity (sweeps/grabs/engineered liquidity), Order Blocks, Fair Value Gaps, Supply & Demand, Premium/Discount, Session Analysis & Kill Zones, Inducement, and the ICT Evidence Engine foundation — every ICT concept named across this migration's four turns is now implemented.

## 5. AI Migration Summary

`AIScorer.assess()` now scores exactly these 13 categories, weights in `app/ai/calibration.py`:

`market_structure` (0.14) · `liquidity` (0.10) · `order_block` (0.12) · `fvg` (0.06) · `supply_demand` (0.06) · `session_killzone` (0.04) · `inducement` (0.08) · `ote` (0.08) · `institutional_bias` (0.10) · `htf_alignment` (0.12) · `volume_confirmation` (0.05) · `risk` (0.03) · `evidence_quality` (0.02)

Removed entirely: `pattern_confirmation`, `trend_filters`, `momentum`, `volume_order_flow`, `volatility_context`, `multi_tf_alignment` (old EMA-stack version), `fundamental_context`. No EMA, RSI, MACD, Stochastic RSI, CCI, Williams %R, Supertrend, Bollinger/Keltner/Donchian, ADX, VWAP, OBV, or chart-pattern code remains anywhere in `scorer.py`.

Two pieces of real (non-retail-indicator) signal were relocated rather than deleted: the old `confluence` check (Order Block + FVG + swept-liquidity zone overlap) now lives inside `evidence_quality`; real forced-liquidation pressure (Binance's public `!forceOrder` stream) moved from the old `liquidity_sweep` category into `volume_confirmation`, alongside CVD and Volume Profile POC.

Funding rate / Open Interest / long-short ratio / Fear&Greed / BTC dominance / DXY / real-yield / event-risk (`fundamental_context`) were **not** carried into the new 13-category weighted score — they weren't named in this phase's required list and aren't produced by any `app/smc/` ICT engine. This is a disclosed scope decision, not an oversight (see §12); the underlying data is still fetched and available to be surfaced as plain evidence text in a future pass.

## 6. Evidence Engine Integration

`ICTEvidenceEngine` (built in Phase C) is now wired into `SignalGenerator.generate()`: an `ICTEvidenceReport` is compiled from every fact already gathered (structure breaks, swept liquidity, inducement events, order blocks, FVGs, supply/demand zones, premium/discount, session context, volume confirmation, HTF bias, invalidation level) **before** scoring, so AIScorer's `evidence_quality` category can read it, and the report's flat label list (`["Bullish BOS", "Bullish CHoCH", "Liquidity Sweep", ...]`) is attached to every returned signal as `signal["evidence"]`. Every signal now carries its own explanation — no black-box confidence number.

## 7. Risk Engine Summary

`RiskEngine.assess_new_trade()` merges Position Sizing (reused via import from `position_sizing.py`, not duplicated), Correlation Risk (accepted as an advisory string, never blocking — matches `correlation_risk.py`'s own design), Portfolio Exposure, Open Risk, Daily Risk, and Maximum Drawdown into one `RiskAssessment`. 12/12 tests passing. Not yet wired into any live caller — see §1's architecture decision and §12.

## 8. Signal Generator Integration

`SignalGenerator.generate()` gained one new optional trailing parameter, `htf_dataframes: Optional[Dict[str, pd.DataFrame]] = None` — the *only* signature change, appended last, so `BacktestEngine`'s existing unmodified call (first 5 args positional, everything else by keyword) keeps working byte-for-byte unchanged.

Wired in, in execution order: Session/Kill Zone context (`df.index[-1]`, free in both live and backtest) → Inducement events → OTE zone → HTF Structure snapshot (from `htf_dataframes`, honestly empty if not supplied) → Institutional Bias → structure-based SL/TP/RR (moved earlier, see §1) → ICT Evidence Report → AIScorer confidence (13 categories) → existing `min_confidence` / `no_confirmation` / `htf_opposition` (pre-existing EMA gate, left untouched — see §12) / `min_risk_reward` gates → **new** Entry Validation gate → Entry Engine / Exit Engine (additive `entry_type`/`exit_plan` fields only).

Weak setups are now rejected on two independent axes: AIScorer's confidence score (all 13 categories must combine to clear `min_confidence`), and the new Entry Validation hard gate (institutional bias, HTF alignment, confluence, session/kill zone, risk, invalidation — all must pass).

## 9. Regression Results

**306 passed / 1 failed** across the complete suite (307 tests, 21 files) — every pre-existing ICT engine suite (Market Structure 17, Liquidity 20, Order Block 21, FVG-related, Supply/Demand 23, Session 26, Inducement 17, Evidence Engine 41/42) plus every new suite from this phase, unchanged and passing.

The one failure (`test_evidence_engine.py::TestDistinctFromAgentEvidenceEngine.test_classes_are_distinct`) is a pre-existing, unrelated offline-sandbox gap: it imports `app.agent.evidence_engine` (an untouched file from before this migration), which transitively needs the full Pydantic v2 API-schema stack (`app/schemas/*`) — disproportionate to stub for one class-distinctness assertion. Not caused by, and not fixed by, this phase's changes.

**Offline sandbox note:** this sandbox has no network egress, so `sqlalchemy`/`loguru`/`pydantic`/`pydantic_settings`/`ta`/`httpx`/`websockets` are not installed. A minimal, inert stub package for each was built under the verification harness (real production code runs unmodified; only the third-party libraries are stood in for — `ta`'s stub computes real pandas-derived indicator math, not the exact library algorithm). This is disclosed, not hidden, and does not substitute for running the real test suite in CI/Docker where the real packages are installed.

## 10. Performance Impact

No formal profiling was run this phase (disclosed, not fabricated). Qualitatively: each `generate()` call now additionally runs `MarketStructureEngine` up to 4 more times (once per supplied HTF timeframe, only when `htf_dataframes` is provided — live scanning only, not backtesting) plus `InducementEngine`, `OTEEngine`, `InstitutionalBiasEngine`, `EntryEngine`, `ExitEngine`, `EntryValidationEngine`, and `ICTEvidenceEngine` — all O(swings)/O(candles) pure-Python passes over data already in memory, the same order of cost as the existing Liquidity/Order Block/FVG passes. A load-test comparing pre/post-migration `generate()` latency under real live-scan volume is a reasonable follow-up before declaring performance parity, not assumed here.

## 11. Backtesting Readiness Checklist

- [x] `app/backtest/engine.py` — untouched (confirmed by re-reading it this phase; zero diff).
- [x] `SignalGenerator.generate()` — fully backward compatible; `BacktestEngine`'s exact positional call verified via a dedicated regression test.
- [x] Session/Kill Zone evidence — works in backtest today, no new parameter needed (`window.index[-1]`).
- [x] Every new engine degrades honestly (neutral/empty) when `htf_dataframes` is absent — verified via a dedicated "no `htf_dataframes`" test path.
- [ ] `BacktestEngine` does not yet fetch real HTF OHLCV for historical replay — `institutional_bias`/`htf_alignment` will score at their neutral baseline (50) during any backtest run today, same honest degradation as always, until a future phase feeds real HTF candles into the replay loop. **Not implemented this phase, per the explicit "do NOT modify the backtesting engine yet" instruction.**

## 12. Remaining Known Issues (disclosed scope decisions, not silent gaps)

1. **Trading Agent subsystem** (`app/agent/*`, `app/services/signal_service.py`, `app/repositories/signal_repository.py`) — not migrated to ICT-only; not named in this phase's component list.
2. **Retail indicator code / `ta` dependency** — `app/indicators/confirmation.py`, `candlestick_patterns.py`, `chart_patterns.py`, and the duplicate `market_scorer.py`/`token_scorer.py` scan paths are unchanged. AIScorer no longer reads most of them, but the files themselves remain (per the standing "reuse, never delete without approval" policy and this phase's own DO-NOT-TOUCH boundaries).
3. **Commodities architecture** — untouched, per explicit instruction.
4. **`htf_opposition` gate in `SignalGenerator`** — the pre-existing EMA-derived 1D/4H hard gate was left in place (see §1); the new ICT-structure-only equivalent (`institutional_bias`/`htf_alignment`) runs alongside it, not in place of it.
5. **Entry Engine / Exit Engine** — additive evidence only; the live trade's numeric entry/exit remain the pre-existing construction (see §1).
6. **Trade Management Engine** — built and tested, not yet wired into `app/scheduler/signal_monitor.py`'s live position-monitoring loop.
7. **Risk Engine** — built and tested, not yet wired into the API/position-sizing layer.
8. **Fundamental/macro context** (funding rate, OI, long/short ratio, Fear&Greed, BTC dominance, DXY, real yield, event risk) — no longer a weighted AIScorer category (see §5); still fetched, not yet surfaced as evidence text.
9. One test (`test_evidence_engine.py`'s cross-check against `app.agent.evidence_engine`) fails in this offline sandbox only, for reasons unrelated to this phase (see §9).

---

Stopping here per instruction. Awaiting approval before any historical-backtesting phase begins.
