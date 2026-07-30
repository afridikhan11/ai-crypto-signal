# Project Health Report — Phase 4 (Evidence & Reasoning Engine)

Date: 2026-07-27
Scope: new `app/agent/evidence_engine.py`; edits to `app/services/market_scorer.py` (additive), `app/agent/orchestrator.py`, `app/agent/explainer.py`, `app/schemas/agent.py`, `app/api/v1/endpoints/agent.py`

## 1. What was built

**Evidence & Reasoning Engine** (`app/agent/evidence_engine.py`, new, 436 lines) - a pure explanation layer, per Requirement 12. It fetches nothing itself: `EvidenceEngine.build(decision, historical=...)` takes an already-fully-computed `AgentDecision` (from the unmodified Decision Engine) plus an optional, already-fetched `HistoricalEvidence`, and formats them into an `EvidenceReport` covering every requirement in the brief:

- **Positive/negative factors** (Requirements 2/3) - drawn from the decision's own `reasons`/`warnings` plus the Technical/Smart Money/Security dashboard rows, tagged with a disclosed, best-effort keyword heuristic (checked negative-phrases-first so a negation like "NOT renounced" isn't misread as positive). A wrong label never hides the underlying text - the verbatim `detail` is always shown regardless of its polarity tag.
- **Technical / Smart Money / Security evidence shown separately** (Requirement 7) - each dashboard's rows are kept in their own list, reusing the exact same `name`/`status`/`strength`/`confidence`/`detail` rows those dashboards already produce. Rows with `confidence == 0` ("Not Available") are excluded rather than shown as a false signal.
- **Confidence explained, including why it was reduced** (Requirements 4/5) - this is the one place this phase found genuinely wasted existing data: `AIScorer.assess()` has always computed a per-category score breakdown and used it in a weighted sum to produce the final confidence number, but `market_scorer.py` was discarding that breakdown (`_score_breakdown`) after only keeping the total. This phase's only change to that file is keeping it (`score_breakdown`/`score_weights`, two new dict keys, same values that already existed) instead of throwing it away. The Evidence Engine then does the one new arithmetic operation in this phase - `shortfall = weight × (100 − score)` - to rank which categories cost the confidence score the most points, which is the complement of the exact multiplication AIScorer's own sum already performs, not a new indicator. For on-chain token scans (no AIScorer breakdown), it falls back to the Phase 3 Decision Engine's own Technical/Security/Liquidity/Smart Money/Holder/Risk sub-scores instead, clearly labeled as a coarser breakdown.
- **Historical evidence whenever available** (Requirement 6) - the Orchestrator does one new real read, via the existing `SignalRepository.get_signals()` (already used by `GET /signals`), for the asset's own past signals; the Evidence Engine formats real win/loss counts and recent outcomes. When there's no history (a first-time symbol, a contract address, or the lookup wasn't attempted), it says so honestly rather than guessing.
- **Data gaps disclosed** - the report's own `unavailable_data` list (already produced by both scan pipelines) is surfaced directly.
- **Never invents evidence** (Requirement 8) - every `EvidenceItem.detail` string is copied verbatim from data the platform already produced; nothing is rephrased or fabricated.

**Wiring**: `TradingAgentOrchestrator._build_evidence()` does the one new DB read described above and calls the Evidence Engine; `_handle_single_asset` attaches `evidence` and `_handle_compare` attaches `evidence_by_symbol` to `AgentResponse`. `Explainer.explain()`/`explain_comparison()` take an optional `evidence` argument and append a formatted "Evidence:" section to the narrative text (Requirement 1 - every recommendation's narrative now includes its evidence, not just the structured object). The API schema and `/agent/query` response were extended to expose the full structured `EvidenceReport` as well.

## 2. Compile check

`python3 -m py_compile` across the entire `app/` tree: **PASSED**, zero syntax errors.

## 3. Regression / functional testing (sandbox)

Same documented sandbox limitation as Phases 2-3 (no network, heavily-stubbed environment). Within that constraint:

- All 10 `app/agent/*.py` modules (including the new `evidence_engine.py`) plus `app/schemas/agent.py` import cleanly individually.
- `app/api/v1/endpoints/agent.py` imports cleanly, isolated from the 10 pre-existing sibling routers (same technique as Phases 2-3).
- All Phase 2/3 functional assertions (asset resolution, all 15 intent commands, style rescaling, provider health, Decision Engine merge + Phase 3 enrichment fields, conversation follow-ups including "compare it with ETH") were re-run and still pass unchanged - confirming this phase didn't regress anything.
- New functional checks, executed end-to-end against two synthetic reports (a market-scan shape with a real `score_breakdown`/`score_weights`, and an on-chain token-scan shape without one):
  - **Historical evidence formatting**: all three honest "not available" cases (no symbol, no lookup attempted, lookup found nothing) plus a real populated case (2 wins/1 loss/1 active from 4 fake signals, 66.7% win rate) all formatted correctly.
  - **Evidence extraction**: positive and negative factors correctly separated; a `confidence=0` "Not Available" dashboard row was correctly excluded rather than shown as evidence; Technical and Smart Money evidence correctly kept in separate lists.
  - **Confidence explanation, market-scan pipeline**: `granularity="category_breakdown"`, all 13 real AIScorer categories present. After finding my first ranking approach (sorting by raw weighted contribution) would have missed a low-score/high-weight category, I corrected it to rank by points *shortfall* instead - re-verified `Trend Filters` (the lowest-scoring, high-weight category in the test data) now correctly ranks as the #1 reducer, and `Multi-Timeframe Alignment` (highest weight, strong score) correctly ranks as the #1 contributor.
  - **Confidence explanation, token-scan pipeline**: correctly fell back to `granularity="sub_score_breakdown"` using the Phase 3 Decision Engine sub-scores, since no AIScorer breakdown exists for on-chain tokens.
  - **Security evidence + legacy fallback**: dashboard rows ("LP Lock" → negative, "Ownership" → positive) classified correctly; the legacy `token_security` fallback path is only used when the newer Contract Security dashboard is empty.
  - **Explainer narrative**: confirmed the full "Evidence:" section (positive/negative factors, confidence explanation with reducers/contributors, per-category evidence blocks, historical evidence line) renders correctly in the plain-text response every recommendation returns.

**Not verified in this sandbox** (needs your Docker environment): a real `POST /api/v1/agent/query` call against live Binance data to confirm `score_breakdown`/`score_weights` actually populate end-to-end from a real `AIScorer.assess()` run, and the existing pytest suite.

Recommended verification on your side:
```
docker compose exec app python -m pytest
docker compose up -d --build
docker compose exec app curl -s -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"message": "Analyze BTCUSDT"}'
```
Check the response's `evidence.confidence_explanation.granularity` is `"category_breakdown"` with 13 real contributions, and `evidence.historical_evidence` reflects BTCUSDT's real signal history (or an honest "no past signals yet" if none exist).

## 4. Architecture compliance

- **Explanation layer, not another analysis engine** (Requirement 12): `evidence_engine.py` makes zero HTTP/DB calls; every value it touches was already computed by the Decision Engine or the underlying dashboards before this module ever sees it.
- **No new indicators** (Requirement 10): the only new arithmetic in this whole phase is `score × weight` (already what AIScorer's own sum does) and its complement `weight × (100 − score)` - never a new market read.
- **Reuses Technical/Smart Money/Security dashboards and the Decision Engine unchanged** (Requirement 11): confirmed by construction - `evidence_engine.py` only reads fields off `decision.raw_report`, never recomputes them.
- **market_scorer.py change is genuinely additive**: two new dict keys only; verified the existing `/token-scan` HTTP endpoint's `TokenScanReport(**report)` construction is unaffected (pydantic ignores unrecognized extra keys by default, confirmed no `extra="forbid"` is set anywhere on that schema).
- **No existing calculation modified, no feature removed**: Decision Engine's Phase 2/3 fields are untouched; nothing was deleted.

## 5. What's next

Compile ✅, regression tests (sandbox-limited, disclosed above) ✅, health report ✅. Waiting for your approval before the next phase.
