# Project Health Report — Phase 5 (AI Research & Market Intelligence)

Date: 2026-07-27
Scope: new `app/agent/research_engine.py`; edits to `app/agent/orchestrator.py`, `app/agent/explainer.py`, `app/schemas/agent.py`, `app/api/v1/endpoints/agent.py`. No edits to `app/services/fundamentals_service.py`, `app/services/macro_fundamentals_service.py`, or any scoring/calibration module.

## 1. What was built

**Research & Market Intelligence Engine** (`app/agent/research_engine.py`, new) — a supporting-context layer that explains market moves using data the platform already has access to, without ever computing a score, confidence, or decision of its own.

- **Explains market moves using existing data sources** (Goal 1): `ResearchEngine.explain_market_move(asset, decision)` reuses the two already-shipped, free, no-API-key services from earlier work — `FundamentalsService` (crypto: open interest trend, long/short account ratio, Fear & Greed index, BTC dominance for altcoins) and `MacroFundamentalsService` (commodities/gold: DXY trend, US 10Y real yield trend, high-impact USD event risk). Both services already existed; this phase's only "new" code is a second, independent caller of their existing public methods — no new market computation was introduced.
- **Never replaces or overrides the Decision Engine** (Goal 2): `ResearchNote` has no score, confidence, or decision field. It is attached to the response and narrative strictly as an additional, clearly separate section — the Explainer only renders it after the Decision and Evidence sections, and only when `research.applicable` is true.
- **Treated as supporting evidence only** (Goal 3): every `ResearchNote` carries a fixed disclaimer ("correlative market context, not a causal explanation and not a trading signal by itself — it never overrides the Decision Engine's output above") and the narrative places the research block last.
- **Keeps the layered architecture** (Goal 4): Orchestrator → Research Engine → Provider Manager → existing services, mirroring the exact call shape already used for Technical/Smart Money/Security data.
- **Reuses Provider Manager for all external access** (Goal 5): every fetch is wrapped in `provider_manager.call(PROVIDER_FUNDAMENTALS, ...)` or `provider_manager.call(PROVIDER_MACRO_FUNDAMENTALS, ...)` — these two constants existed unused since Phase 2 and are now exercised for the first time. This means `GET /agent/providers/health` now also reports on these two providers.
- **Honest on missing data, never guesses** (Goal 6): three layers of honesty are disclosed rather than hidden — (a) if the asset isn't a Binance trading pair (e.g. a contract address), `ResearchNote.applicable=False` with a plain reason; (b) if a fetch throws unexpectedly, the same honest fallback fires with the exception message; (c) the module's own docstring discloses that `FundamentalsService`/`MacroFundamentalsService` already swallow their own fetch errors internally and fall back to neutral defaults — meaning a "neutral" reading can mean a genuinely quiet market *or* an unreachable data source, and this layer cannot tell the two apart from the outside. That caveat is surfaced directly in the narrative text, not just in a code comment.

**Wiring**: `TradingAgentOrchestrator._build_research()` calls the engine with its own try/except safety net; `_handle_single_asset` attaches `research`, `_handle_compare` attaches `research_by_symbol`. `handle()` was refactored into a thin wrapper around the existing logic (now `_handle()`) with a `finally: await self.research_engine.close()` to release the two services' HTTP clients every call, matching the "no leaked resources across requests" standard the rest of the agent already holds. `Explainer.explain()`/`explain_comparison()` take an optional `research` argument and append a "Market Intelligence (supporting context, not a trading signal):" section when applicable. The API schema (`ResearchSignalResponse`, `ResearchNoteResponse`) and `/agent/query` response were extended to expose the structured note.

## 2. Compile check

`python3 -m py_compile` across the entire `app/` tree: **PASSED**, zero syntax errors.

## 3. Regression / functional testing (sandbox)

Same documented sandbox limitation as Phases 2–4 (no network, heavily-stubbed environment — `httpx` is stubbed to always raise on `.get()`, which is actually useful here: it proves the "honest fallback on failure" path for real). Within that constraint:

- All 12 `app/agent/*.py` modules (including the new `research_engine.py`) plus `app/schemas/agent.py` import cleanly individually, and `app/api/v1/endpoints/agent.py` imports cleanly isolated from the 10 pre-existing sibling routers (same technique as prior phases).
- All Phase 2–4 functional assertions (asset resolution, all 15 intent commands, provider health, Decision Engine + Phase 3 enrichment, Evidence Engine for both market-scan and token-scan shapes, conversation follow-ups) were re-run unchanged and still pass — no regression.
- Fixed one latent bug found while extending the test harness: the sandbox's `httpx` stub was missing an `aclose()` method, which would have made `ResearchEngine.close()` crash the first time it ran (real code calls `self._client.aclose()` inside `FundamentalsService.close()`/`MacroFundamentalsService.close()`, and `self._client` is already assigned before the network call fails). Adding a no-op `aclose()` to the test stub resolved it — this was a test-harness gap, not an application bug.
- New Phase 5 functional checks, executed end-to-end via `asyncio.run(...)`:
  - **Contract address asset** → `applicable=False`, reason mentions "on-chain tokens" — correctly declines rather than fetching irrelevant data.
  - **BTCUSDT** → `applicable=True`, `asset_type="crypto"`, exactly the 3 expected signals (Open Interest, Long/Short Account Ratio, Crypto Fear & Greed Index), **no** BTC Dominance signal (correctly skipped for BTC itself), and the fixed disclaimer text present. Under the stubbed always-fails network, `FundamentalsService`'s own internal fallbacks correctly produced neutral defaults (flat OI, balanced ratio, neutral 50/100 F&G) — proving the full call chain survives a real network failure without crashing the agent response.
  - **ETHUSDT** → correctly includes the BTC Dominance signal (only skipped for BTC itself).
  - **XAUUSDT** → `applicable=True`, `asset_type="commodity"`, routes to the macro path with DXY and real-yield signals present.
  - **Provider health** → confirmed both `fundamentals` and `macro_fundamentals` now show `total_calls > 0` in `ProviderManager.get_all_health()`, proving Goal 5 (all external access routed through Provider Manager) is actually true, not just asserted in a docstring.
  - **Explainer narrative** → re-run with both `evidence=` and `research=` attached: confirmed the "Market Intelligence" section renders after the "Decision:" and "Evidence:" sections (ordering asserted explicitly), includes each signal's label/value/interpretation, and ends with the disclaimer.

**Not verified in this sandbox** (needs your Docker environment, with real network access): a real `POST /api/v1/agent/query` call to confirm `FundamentalsService`/`MacroFundamentalsService` return genuinely live data (not just their neutral fallback) through the new Research Engine path, and the existing pytest suite.

Recommended verification on your side:
```
docker compose exec app python -m pytest
docker compose up -d --build
docker compose exec app curl -s -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"message": "Analyze BTCUSDT"}'
docker compose exec app curl -s http://localhost:8000/api/v1/agent/providers/health \
  -H "Authorization: Bearer <token>"
```
Check the query response's `research.signals` for real (non-flat/non-50) values reflecting current market conditions, and confirm `fundamentals`/`macro_fundamentals` appear in the providers/health list with a healthy `status`.

## 4. Architecture compliance

- **Research as supporting evidence only, never overriding the Decision Engine** (Goals 2/3): confirmed by construction — `ResearchNote` has no score/confidence/decision field, and the narrative places it strictly after and below the Decision/Evidence sections.
- **Reuses Provider Manager for all external access** (Goal 5): confirmed via the functional test — both provider constants (`PROVIDER_FUNDAMENTALS`, `PROVIDER_MACRO_FUNDAMENTALS`) that sat unused since Phase 2 now show real call counts.
- **Do not modify the existing scoring system**: `app/ai/scorer.py`, `app/ai/calibration.py`, and `app/services/market_scorer.py` were **not touched** this phase (unlike Phase 4, which made a small additive edit to `market_scorer.py` — deliberately not repeated here per your "do not redesign any existing modules" instruction; `research_engine.py`'s docstring explicitly discloses this tradeoff).
- **Do not redesign existing modules**: `fundamentals_service.py` and `macro_fundamentals_service.py` were read but not edited; the Research Engine calls their existing public methods a second, independent time rather than modifying either file.
- **Honest on unavailable data** (Goal 6): both the "wrong asset type" and "fetch failed" paths return a plain-language reason instead of a guess; the narrative also discloses the deeper caveat that a neutral reading may mean quiet markets or an unreachable source.
- **No existing calculation modified, no feature removed**: Decision Engine, Evidence Engine, and Phase 2/3 fields are untouched; nothing was deleted.

## 5. What's next

Compile ✅, regression tests (sandbox-limited, disclosed above) ✅, health report ✅. Waiting for your approval before the next phase.
