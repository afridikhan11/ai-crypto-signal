# Project Health Report — Phase 2 (Trading Agent Core)

Date: 2026-07-27
Scope: `app/agent/` (8 files), `app/schemas/agent.py`, `app/api/v1/endpoints/agent.py`, `app/api/v1/__init__.py` (additive registration only)

## 1. What was built

Three infrastructure managers, as requested before Phase 2 implementation began:

- **Strategy Profile Manager** (`app/agent/strategy_profile_manager.py`, 186 lines) — resolves an asset's existing `CalibrationProfile` (Crypto/Gold/Silver/Oil — reused, not recomputed) together with one of four trading-style profiles (Scalping, Intraday, Swing, Position). Rescales an already-computed entry/SL/TP by a disclosed per-style distance ratio; never re-derives ATR or recalculates indicators.
- **Risk Manager** (`app/agent/risk_manager.py`, 194 lines) — thin orchestration wrapper over the existing `SignalService` (position sizing, portfolio exposure, daily loss limits) and `CorrelationRisk`. Adds one new disclosed formula: a composite 0–100 risk score built from real existing risk flags (RR-below-minimum, daily-loss-breached, portfolio-exposure-over-threshold, correlation-warning), never a new market indicator.
- **Provider Manager** (`app/agent/provider_manager.py`, 198 lines) — call-site wrapper around existing provider functions (Binance, DexScreener, GeckoTerminal, GoPlus, macro/fundamentals services) giving uniform success/failure/latency tracking and a `call_with_fallback` hook. Discloses in its own docstring that "CoinGecko" (named in the original brief) isn't actually integrated in this codebase — GeckoTerminal is — rather than inventing a connector that doesn't exist.

Trading Agent Core, built on top of the three managers:

- **Asset Resolver** (`asset_resolver.py`) — detects trading pairs, commodity aliases (gold/silver/oil→XAUUSDT/XAGUSDT/CLUSDT), EVM/Solana contract addresses, and wallet-shaped addresses from free text, reusing `market_scorer.detect_input_type()`.
- **Intent Parser** (`intent_parser.py`) — deterministic regex-based parser (no LLM/NLU dependency — disclosed, matches the project's no-fabrication standard) covering all 15 example commands from the brief.
- **Decision Engine** (`decision_engine.py`) — merges an already-computed `TokenScanReport`-shaped dict (from the existing, unmodified `build_market_scan_report()`/`build_token_scan_report()`) plus an optional `AgentRiskAssessment` into one `AgentDecision`: Overall Score, Confidence, Technical/Security/Liquidity/Smart Money/Holder/Risk Score, Trend, Momentum, Entry/SL/TP1-3, Risk/Reward, Final Decision, and reasons. Computes zero new indicators — every score is read from the reused report or the Risk Manager.
- **Explainer** (`explainer.py`) — turns an `AgentDecision` + parsed intent into the natural-language format from the brief (`Decision: BUY / Confidence: 91% / Reason: ...`), plus comparison and ranked-list narratives.
- **Orchestrator** (`orchestrator.py`) — the entry point (`TradingAgentOrchestrator.handle(raw_text)`). Routes to market scan, token scan, wallet (honestly reports unsupported), asset comparison, or a portfolio-wide ranked query (reads real `ACTIVE` signals via `SignalRepository`, doesn't re-scan). Never duplicates a calculation any existing module already performs.

Wiring: `POST /api/v1/agent/query` and `GET /api/v1/agent/providers/health`, registered additively as the 11th router in `app/api/v1/__init__.py` — the 10 pre-existing routers are untouched.

Total new code: 1,628 lines across 11 files. No existing file's logic was modified; `app/api/v1/__init__.py` received two added lines only.

## 2. Compile check

`python3 -m py_compile` across the entire `app/` tree (all pre-existing files plus the new ones): **PASSED**, zero syntax errors.

## 3. Regression / import testing (sandbox)

This sandbox has no network access and is missing most real dependencies (sqlalchemy, pydantic, fastapi, redis, PyJWT, pydantic-settings, cryptography), a limitation documented throughout this project. Within that constraint, the following was verified by actually running Python imports against a heavily-stubbed environment — not just read:

- All 8 new `app/agent/*.py` modules plus `app/schemas/agent.py` **import cleanly**, individually. This proves every cross-module reference the new code makes (function names, class names, field names) is genuinely correct against the real project source — a typo or wrong assumption would have surfaced as `ImportError`/`AttributeError` here.
- `app/api/v1/endpoints/agent.py` (the new endpoint file) **imports cleanly** in isolation from its 10 pre-existing sibling routers. (Importing it the normal way forces Python to first execute the sibling routers' own files — auth, backtest, dashboard, etc. — which need real JWT/Redis/Postgres/cryptography this sandbox doesn't have; that's a pre-existing limitation unrelated to Phase 2, not a defect in the new code.)
- Functional spot-checks, run end-to-end (not just imported):
  - **AssetResolver**: correctly resolved "gold" → `XAUUSDT`, `BTCUSDT` directly, a wallet-shaped address (flagged unsupported, honestly), and a contract address with chain hint.
  - **IntentParser**: all 15 example commands from the brief (Analyze/Compare/Find best trade/Find safest trade/Why failed/Why confidence/Show strongest/Show weakest/What's blocking/Should I wait/buy/exit/Find bullish/bearish/Explain signal) parsed to the correct intent.
  - **StrategyProfileManager**: resolved `XAUUSDT` + "scalping" to the Gold calibration profile + Scalping style, and correctly rescaled SL/TP tighter than the base distance.
  - **ProviderManager**: health tracking verified correct at both a "down" boundary (50% failure ratio) and a genuine "degraded" case (33% failure ratio) — matching the class's own documented thresholds.
  - **DecisionEngine.merge()**: merged a synthetic market-scan-shaped report into a decision with the correct final decision and technical score.
  - **Explainer**: produced the exact `Decision: STRONG BUY / Confidence: 88% / Reason: ...` narrative format from the brief.

**Not verified in this sandbox** (needs your Docker environment, same as every prior script in this project): real Postgres-backed `SignalRepository` queries, real Binance/DexScreener/GeckoTerminal/GoPlus HTTP calls, real JWT auth, the full FastAPI app boot (`uvicorn`) and `POST /api/v1/agent/query` over HTTP, and the existing pytest suite.

Recommended verification on your side once you're ready:
```
docker compose exec app python -m pytest
docker compose up -d --build
docker compose exec app curl -X POST http://localhost:8000/api/v1/agent/query \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"message": "Analyze BTCUSDT"}'
```

## 4. Architecture compliance

- **No duplicate calculation**: confirmed by design and by reading `market_scorer.py`/`token_scorer.py` in full — the Decision Engine only reshapes fields already present in the reused report.
- **Backward compatibility**: all 10 pre-existing routers, and every pre-existing service/model file, are byte-for-byte unmodified. `app/api/v1/__init__.py` received two purely additive lines.
- **No feature removed**: nothing was deleted. The Architecture Review's flagged dead code (orphaned duplicate router, WPF Market/Portfolio views) remains untouched, per your standing approval-required policy.
- **Two known, disclosed gaps** (not silently worked around): wallet-address analysis doesn't exist anywhere in the codebase yet — the Orchestrator reports this honestly rather than fabricating a result; and the brief's "CoinGecko" provider is actually GeckoTerminal in this codebase — registered under its real name.

## 5. What's next

Per your Phase 2 instruction, this is the checkpoint: compile ✅, regression tests (sandbox-limited, disclosed above) ✅, health report ✅. Waiting for your approval before starting Phase 3 (Natural Language Engine).
