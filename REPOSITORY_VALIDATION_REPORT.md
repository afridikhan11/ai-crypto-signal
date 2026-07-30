# Repository Validation Report

**Phase 2, Objective 1 — Full Repository Audit**
Date: 2026-07-30 | Method: AST-parsed (not text/grep-matched) class definitions and import edges across every `.py` file under `app/`, cross-checked against the mechanically-enforced tests in `tests/test_legacy_isolation.py`, plus a fresh, independent audit script run for this report.

## 1. Exactly-one-of-each core component

AST audit of every `ClassDef` in `app/` for names containing `Scanner`, `SignalGenerator`, `RiskEngine`, `EvidenceEngine`, `DecisionEngine`, `Scorer`:

| Component | Class found | Location | Count |
|---|---|---|---|
| Scanner | `UniversalScanner` | `app/scheduler/universal_scanner.py` | **1** |
| Signal Generator | `SignalGenerator` | `app/strategy/signal_generator.py` | **1** |
| Risk Engine | `RiskEngine` | `app/risk/risk_engine.py` | **1** |
| AI Confidence Scorer | `AIScorer` | `app/ai/scorer.py` | **1** |
| ICT Evidence/Decision Engine | `ICTEvidenceEngine`, `ICTDecisionEngine` | `app/ai/evidence_engine.py`, `app/ai/ict_decision_engine.py` | **1 each** |

No class named `CryptoScanner`, `GoldScanner`, `ForexScanner`, `CryptoAI`, `GoldAI`, `ForexAI`, `CryptoScorer`, or `GoldScorer` exists anywhere in `app/`. `app/scheduler/scanner.py` (the old `CryptoScanner`) was deleted in Phase 1 and does not exist.

**Result: PASS.** Exactly one of each required core ICT component exists, and no forbidden per-asset class name exists.

## 2. A disclosed, non-duplicate exception: the Agent/Coach subsystem

The audit also found `EvidenceEngine` in `app/agent/evidence_engine.py` and `DecisionEngine` in `app/agent/decision_engine.py` — names that overlap the ICT components above. These are **not** duplicates of the ICT Evidence/Decision Engine and do not violate "exactly one." Verified by reading both files in full:

- `app/agent/decision_engine.py::DecisionEngine` performs **no indicator/SMC/security recalculation** — it only reshapes an already-computed report dict (`market_scorer.py`/`token_scorer.py` output) plus a Risk Manager read into one `AgentDecision`, for the conversational Trading Agent/Coach feature (`POST /agent/query`). Its own docstring states this explicitly.
- `app/agent/evidence_engine.py::EvidenceEngine` is a **pure formatter** over an already-built `AgentDecision` — it never fetches data and never computes a new indicator (the one arithmetic op, `score * weight`, is disclosed as the same term `AIScorer.assess()` already sums).
- Both files carry explicit "why this is not the same as" cross-references to `app/ai/evidence_engine.py` and `app/ai/ict_decision_engine.py`, and both of those ICT-side files carry the matching reverse cross-reference — this separation was a deliberate, already-documented design decision from an earlier phase, not something discovered here.
- Reverse-import check: `app/agent/` never imports `app.smc`, `app.strategy`, `app.assets`, or `app.risk` (grep across `app/agent/*.py` for those prefixes returns nothing). It only reads pre-computed dicts from `market_scorer.py`/`token_scorer.py`.
- Forward-import check: no production ICT package (`app/smc`, `app/ai`, `app/assets`, `app/risk`, `app/strategy`, `app/scheduler`) imports anything from `app/agent`.

**Result: PASS, with disclosure.** Two conversational/explanation classes share a generic name with the ICT engines but are architecturally isolated, non-duplicating, and this was already documented before this audit — not a new finding, but re-verified here since Objective 1 explicitly asks to check for duplicated Evidence/Decision Engines.

## 3. No production code imports legacy modules

`app/legacy/` (confirmation.py, candlestick_patterns.py, chart_patterns.py, trend.py) has exactly four approved consumers, confirmed by AST-parsed import edges:

| File | Imports from `app.legacy` |
|---|---|
| `app/services/market_scorer.py` | trend, candlestick_patterns, chart_patterns, confirmation |
| `app/services/ta_dashboard.py` | trend, confirmation |
| `app/services/token_scorer.py` | confirmation |
| `app/backtest/engine.py` | trend |

No file under `app/smc`, `app/ai`, `app/assets`, `app/risk`, `app/strategy`, or `app/scheduler/universal_scanner.py` / `app/scheduler/signal_monitor.py` imports `app.legacy` or `ta`. This matches (and re-confirms) `tests/test_legacy_isolation.py`'s enforced allowlist — unchanged since Phase 1.

**Result: PASS.**

## 4. No duplicated scanner / AI / risk engine

Already covered by Section 1's class count. Additionally confirmed: `app/main.py` constructs exactly one `UniversalScanner(ALL_SYMBOLS)` at startup (`app.state.scanner`); no second scheduler/scanner instantiation exists anywhere in `app/`.

## 5. Full regression suite

Ran the complete offline test suite (`tests/`, 33 files) against real project code (no third-party network access; `loguru`/`sqlalchemy`/`pydantic`/`ta`/`httpx`/`websockets`/`redis` stubbed at the package boundary only — every project `.py` file runs unmodified):

```
TOTAL: 536 passed, 0 failed
```

(491 carried over from the Phase 1 Universal Platform report + 45 new tests added during this Phase 2 validation: `test_dataset_validator.py` (20), `test_performance_report.py` (15), `test_walk_forward.py` (10) — see `BACKTEST_VALIDATION_REPORT.md`. `test_stress_scenarios.py`'s 7 tests are counted separately in `STRESS_AND_PERFORMANCE_REPORT.md` and also included in the 536 total above.)

## Verdict

**Repository Validation: PASS.** Exactly one Scanner, one Signal Generator, one AI Scorer, one ICT Evidence Engine, one ICT Decision Engine, one Risk Engine. No production ICT path imports legacy/retail code. The only name overlap (`EvidenceEngine`/`DecisionEngine` in `app/agent/`) is a pre-existing, disclosed, non-duplicating conversational subsystem with no import-path connection to the ICT pipeline. Zero regressions across 536 tests.
