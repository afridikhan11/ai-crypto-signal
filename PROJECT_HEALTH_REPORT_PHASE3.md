# Project Health Report — Phase 3 (Conversation & Context Engine + Decision Engine Enrichment)

Date: 2026-07-27
Scope: new `app/agent/conversation_context.py`; edits to `app/agent/orchestrator.py`, `app/agent/decision_engine.py`, `app/agent/explainer.py`, `app/agent/strategy_profile_manager.py` (additive), `app/schemas/agent.py`, `app/api/v1/endpoints/agent.py`

## 1. What changed from the revised roadmap

Per your revision, Phase 3 became the **Conversation & Context Engine** (Natural Language Engine deferred) plus a **Decision Engine output enrichment**. Both are additive on top of the approved Phase 2 Trading Agent Core — no Phase 2 file's calculations were altered, only extended.

### Conversation & Context Engine (`app/agent/conversation_context.py`, new, 239 lines)

- **Session memory only, in-process, never persisted** (Requirements 5/6): `ConversationContextManager` holds sessions in a plain Python dict — no DB table, no file, no Redis key. It mirrors the existing `ProviderManager` singleton pattern already shipped in Phase 2. A process restart clears everything; idle sessions (1 hour default) are swept automatically so the dict can't grow unbounded either.
- **Remembers the last analyzed asset** (Requirement 4): after every turn, the Orchestrator records which asset was analyzed and the resulting decision.
- **Resolves follow-ups** (Requirement 2): "why did it fail?", "should I buy now?", a bare "what about it?" with no asset in the text — all resolve to the remembered asset. Context only fills gaps; it never overrides an asset explicitly named in the current message.
- **Comparison without repeating the asset name** (Requirement 3): "compare it with ETH" is paired with the remembered asset. This surfaced a real, disclosed edge case — the existing Intent Parser's `COMPARE_ASSETS` pattern only matches "compare ... vs/versus/against/and ...", so "compare it **with** ETH" alone gets classified as `analyze_asset` with one asset (ETH). Rather than touch the already-shipped, tested `intent_parser.py`, the Context Engine detects the bare word "compare" itself and promotes the intent — verified working in the test below.
- **Reuses the Trading Agent Core unchanged** (Requirement 7): this module never calls a scanner, provider, or the Decision Engine itself. It only edits the `AgentIntent` object *before* `TradingAgentOrchestrator._route()` runs — the exact same routing logic Phase 2 shipped, extracted verbatim into `_route()` with zero logic changes.
- **Backward compatible**: `TradingAgentOrchestrator.handle()` gained an optional `session_id` parameter. Omitting it (as any pre-Phase-3 caller would) reproduces Phase 2's behavior exactly — no context lookup at all.
- **API wiring**: `AgentQueryRequest.session_id` (optional — falls back to the authenticated user identity, consistent with this project's existing single-admin-user model), `AgentQueryResponse.context_used` / `context_note`, and a new `DELETE /agent/session` endpoint so a user can reset context on demand instead of waiting for the idle timeout.

### Decision Engine enrichment (presentation-only, per your instruction)

Five new fields on `AgentDecision`, all derived from **scores Phase 2 already computed** — no existing calculation was touched:

- **Confidence Breakdown** — re-exposes the existing technical/security/liquidity/smart-money/holder/risk sub-scores together under one field.
- **Recommendation Level** — combines the existing `final_decision` + `confidence_pct` into a readable line (e.g. "Strong Buy — High Conviction"), disclosed fixed threshold (confidence ≥ 80 = "High Conviction").
- **Expected Holding Time** — reused directly from the Strategy Profile Manager's existing style catalog (e.g. "hours to ~1 day" for the Intraday default) — zero new computation.
- **Risk Category** — disclosed fixed bands over the existing composite `risk_score` (Low / Moderate / Elevated / High Risk), honestly "Unknown" when no risk data was available rather than guessing.
- **Suitable Trading Style** — a disclosed heuristic over the existing `risk_reward` and the reused Technical Dashboard's own trend verdict, clearly labeled as a suggestion, not personalized financial advice — the trader still explicitly picks their own style via the existing `style` parameter, which is what actually rescales levels.

All five are surfaced in the `Explainer`'s narrative output and in the API response schema (`AgentDecisionResponse`).

## 2. Compile check

`python3 -m py_compile` across the entire `app/` tree: **PASSED**, zero syntax errors.

## 3. Regression / functional testing (sandbox)

Same documented sandbox limitation as Phase 2 (no network, missing sqlalchemy/pydantic/fastapi/redis/etc. — heavily stubbed for import-level testing). Within that constraint, the following was actually run, not just read:

- All 9 `app/agent/*.py` modules (including the new `conversation_context.py`) plus `app/schemas/agent.py` import cleanly individually — every cross-module reference is genuinely correct.
- `app/api/v1/endpoints/agent.py` imports cleanly, isolated from the 10 pre-existing sibling routers (same isolation technique used in Phase 2, for the same reason — those files need real JWT/Redis/Postgres unrelated to this work).
- Functional spot-checks, executed end-to-end:
  - **Decision Engine enrichment**: confirmed `confidence_breakdown` exactly matches the sub-scores already asserted, `recommendation_level` = "Strong Buy — High Conviction", `expected_holding_time` = "hours to ~1 day" (default Intraday), `risk_category` honestly "Unknown" (no risk data supplied), `suitable_trading_style` correctly picks the Swing/Position branch for a 3.0 risk-reward with a "STRONG" trend verdict. All five also confirmed present in the Explainer's narrative text.
  - **Conversation & Context Engine, turn by turn**:
    1. "Analyze BTCUSDT" — explicit, no context used. Session remembers BTCUSDT.
    2. "Why did it fail?" — no asset in the text at all; correctly resolved to BTCUSDT from context, intent correctly stayed `why_failed`.
    3. "Compare it with ETH" — resolved to a 2-asset `compare_assets` intent (BTCUSDT + ETHUSDT) without the user ever repeating "BTC" — the exact scenario Requirement 3 describes, including the Intent Parser edge case noted above.
    4. "Analyze ETHUSDT" — an explicit new asset was correctly NOT overridden by remembered context.
    5. Idle-session sweep — a session with a near-zero timeout was confirmed actually removed from memory, proving no retention beyond the configured window (Requirements 5/6).

**Not verified in this sandbox** (needs your Docker environment): the real `POST /api/v1/agent/query` HTTP round-trip with a real `session_id` across two separate requests, and the existing pytest suite.

Recommended verification on your side:
```
docker compose exec app python -m pytest
docker compose up -d --build
# Turn 1
docker compose exec app curl -s -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"message": "Analyze BTCUSDT", "session_id": "demo"}'
# Turn 2 - same session_id, no asset repeated
docker compose exec app curl -s -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"message": "Why did it fail?", "session_id": "demo"}'
```

## 4. Architecture compliance

- **No existing calculation modified**: Decision Engine's Phase 2 fields (scores, entry/SL/TP, final_decision) are untouched; the five new fields are additive and computed from those existing values only.
- **Trading Agent Core reused unchanged**: `_route()` is Phase 2's exact routing logic, unmodified — Phase 3 only wraps it.
- **No feature removed**: nothing was deleted or refactored away. Previously flagged, still-unapproved cleanup items remain untouched.
- **Disclosed limitation**: session context is genuinely single-session under this project's default single-admin-user auth model unless a caller passes an explicit `session_id` — documented directly in `conversation_context.py`'s module docstring rather than silently implied otherwise.

## 5. What's next

Compile ✅, regression tests (sandbox-limited, disclosed above) ✅, health report ✅. Waiting for your approval before Phase 4.
