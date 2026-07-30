# AI Crypto Signal Pro → Enterprise AI Trading Platform
## Architecture Review (Phase 1) — 2026-07-27

No code was changed to produce this review. It is based on a fresh scan of the live folder structure, `requirements.txt`, the real API router wiring, and file sizes, cross-checked against the full build history of this project.

---

## 1. Current Architecture (as it exists today)

**Backend** — FastAPI, layered structure under `FastAPI Backend/app/`:

- `api/v1/endpoints/` — 10 route groups: health, auth, signals, stats, history, account, dashboard, backtest, trading, token_scan.
- `services/` — 19 files. Data providers (Binance market/account/trading, DexScreener, GeckoTerminal, GoPlus/contract security, macro/fundamentals) and scoring aggregators (`market_scorer.py` for trading-pair tokens, `token_scorer.py` for on-chain tokens, `ta_dashboard.py` shared technical-dashboard builder, `smart_money_service.py`, `contract_security_service.py`).
- `ai/` — `scorer.py` (the weighted confidence engine), `calibration.py` / `calibration_profiles.py` (per-asset-type profiles: Crypto/Gold/Silver/Oil/Forex).
- `smc/` — Smart Money Concepts primitives: `market_structure.py` (BOS/CHoCH), `liquidity.py` (sweeps), `order_blocks.py`, `fvg.py`, `supply_demand.py`.
- `strategy/signal_generator.py` — orchestrates SMC + AIScorer + a symbol's CalibrationProfile into a tradeable signal (SL/TP/RR/confidence).
- `backtest/engine.py` — replays the exact live pipeline against historical candles.
- `scheduler/` — `scanner.py` (the always-on symbol scanner) and `signal_monitor.py` (outcome tracking).
- `models/` — `Signal`, `Coin`, `User` (SQLAlchemy, small and single-purpose).
- `core/` — config, database, security (JWT), logging, Redis.

**Frontend** — WPF, MVVM, under `AI_Crypto_Signal_Pro/`:

- 10 active Views/ViewModels wired into `MainWindowViewModel`: Dashboard, Live Signals, Gold Signals, History, Account, Auto Trading, Statistics, Token Scanner, Settings, plus 2 unwired legacy ones (Market, Portfolio — see §4).
- `Services/ApiService.cs` — single typed HTTP client wrapping every backend endpoint; `WebSocketService.cs` for live push.
- `Models/*Dto.cs` — one DTO family per backend schema.

**Two parallel scoring pipelines exist today, and this is the central fact the new Trading Agent has to bridge:**

1. **Signal pipeline** (crypto pairs + Gold/Silver/Oil futures): `SignalGenerator` → `AIScorer` → `CalibrationProfile`. Produces BUY/SELL signals with SL/TP/RR/confidence and a `reason` + `score_breakdown`.
2. **On-chain token pipeline** (Token Scanner): `token_scorer.py` / `market_scorer.py` → `ta_dashboard.py` + `smart_money_service.py` + `contract_security_service.py`. Produces a dashboard (Technical/Smart Money/Security/AI Decision tabs), not a formal Signal row.

These do not share a common output schema or a common entry point today. A user (or the new Trading Agent) has to already know "is this a Binance pair or a token contract" and go to the right tab/endpoint manually.

---

## 2. Reusable Components (the Trading Agent must sit on top of these, never reimplement them)

- **Asset classification**: `app/core/constants.py` — `asset_type_for_symbol()` / `asset_class_for_symbol()` already distinguish Crypto / Gold / Silver / Oil / Forex by symbol. This is the natural base for "detect asset type," extended only with wallet-address and contract-address pattern detection (not currently classified anywhere).
- **Crypto/commodity signal scoring**: `AIScorer`, `SignalGenerator`, all 5 `smc/` detectors, `CalibrationProfile` per asset type.
- **On-chain token scoring**: `token_scorer.py`, `market_scorer.py`, `ta_dashboard.py`, `smart_money_service.py`, `contract_security_service.py`.
- **Backtesting/historical replay**: `BacktestEngine` — already proven able to replay any symbol's real history through the live pipeline; directly reusable for "why did X fail" / "show evidence" style questions.
- **Explanation material that already exists**: `AIScorer.assess()` already returns a human-readable `reason` string and a full `score_breakdown` dict per category. This is *not* natural language yet, but it is the structured input the Explanation Engine (§3) needs — should be consumed, not recomputed.
- **Auth/session plumbing**: JWT (`app/core/security.py`), already wired through `api/dependencies.py`.

---

## 3. Missing Components (net-new work required for the Trading Agent vision)

1. **Intent parser** — nothing today turns "Why is Bitcoin bullish?" or "Find today's safest trade" into a structured call. Net new: `app/agent/intent_parser.py`.
2. **Asset resolver** — nothing today detects a raw wallet address or contract address from free text and routes it to Token Scanner vs a wallet-holdings lookup. A **wallet-holdings analysis path does not exist at all** — Token Scanner only handles *contract* addresses (DexScreener/GeckoTerminal/GoPlus), not wallet PNL/holdings. This is a genuinely missing feature, not just missing orchestration.
3. **Unified Decision Engine** — nothing today merges Technical + Smart Money + Security + SMC into one Overall Score / BUY-HOLD-SELL / STRONG BUY-STRONG SELL scale. The two existing pipelines (§1) produce differently-shaped outputs (`Signal` row vs dashboard DTO) that need a common merge schema.
4. **Natural-language Explanation Engine** — `reason`/`score_breakdown` exist as data; nothing converts them into the flowing "Bullish BOS, Strong Order Block, Liquidity Sweep completed..." narrative the spec shows. Net new, but should be a thin templating layer over existing data, not a new scoring system.
5. **Comparison logic** ("Compare BTC vs ETH") — no existing endpoint runs two assets and diffs them.
6. **Conversation memory** — the backend is fully stateless request/response today. No session or message history table exists. This needs a new, small schema addition (a `ConversationSession`/`Message` table or equivalent) — flagged now so it isn't a surprise at Phase 5.
7. **"Why did it fail" against real history** — partially possible today by reading `Signal.score_breakdown` for a past rejection, but there's no endpoint that reruns `AIScorer` on-demand for an arbitrary symbol *right now* and explains a live near-miss the way `scripts/analyze_smc_frequency.py` does offline. The Trading Agent needs an on-demand, single-symbol version of that logic as a real API path, not a batch script.

---

## 4. Weak Points / Technical Debt (found this pass + carried over from memory)

- **New this pass**: `app/api/v1/endpoints/__init__.py` defines its own orphaned `api_router` (only health/signals/stats) that is never imported anywhere — the real router wiring is one level up in `app/api/v1/__init__.py` (all 10 routers). Confirmed via grep: nothing references the nested one. Dead code, safe to remove, but per standing policy I'm flagging it rather than deleting it.
- **Carried over, still pending your approval** (already in memory from earlier reviews): WPF `MarketView`/`MarketViewModel` and `PortfolioView`/`PortfolioViewModel` are unwired/dead (still present in the codebase, `ShowMarket()` still exists on `MainWindowViewModel` but doesn't lead anywhere users navigate to), and a legacy Security card still exists in `token_security_service.py` alongside the newer Contract Security Dashboard, kept only for backward compatibility per your earlier explicit instruction.
- **Two asset-classification systems, both real and both needed** (not duplicate — flagging so the Trading Agent uses the right one): the coarse `ASSET_CLASS_CRYPTO/COMMODITY` split and the fine `ASSET_TYPE_CRYPTO/GOLD/SILVER/OIL/FOREX` split coexist by design (calibration profiles need the fine one, some older code paths still use the coarse one). The Trading Agent's asset resolver should call the fine one and extend it, not invent a third classifier.
- **Two scoring pipelines with no shared contract** (§1) — the single biggest architectural gap the Trading Agent exists to close. Building the Decision Engine will require defining one merged output schema both pipelines can be mapped into, without changing either pipeline's own internals.
- **Performance note, not urgent**: this week's SMC-frequency diagnostic work showed that a full structural recompute (`MarketStructure` + `LiquidityDetector` + `OrderBlockDetector` + `FVGDetector`) over a 500-candle window costs tens of milliseconds per call. Today this only runs on the scheduler's own cadence, never per-request, so it's not a live risk — but if the Trading Agent's "Analyze BTCUSDT" command is meant to recompute on-demand in real time rather than reading the scanner's last cached result, this cost needs a caching decision up front (reuse the scanner's last computed signal for that symbol vs. force a fresh recompute), otherwise a chat-style "analyze this" command could feel slow.

---

## 5. Recommended Placement for the New Trading Agent (no duplication, fits existing conventions)

Backend, mirroring the existing `app/ai/`, `app/smc/`, `app/strategy/` package style:

```
app/agent/
    intent_parser.py      # free text -> structured intent
    asset_resolver.py      # extends asset_type_for_symbol() + wallet/contract detection
    orchestrator.py         # calls existing SignalGenerator / token_scorer / market_scorer / smart_money_service / contract_security_service - never recomputes their logic
    decision_engine.py      # merges outputs into one Overall Score / BUY-HOLD-SELL schema
    explainer.py            # turns existing reason/score_breakdown into natural language
```

New API surface: `app/api/v1/endpoints/agent.py` (e.g. `POST /api/v1/agent/query`), wired into the real `app/api/v1/__init__.py` router — and a good natural moment to also remove the dead nested router from §4, with your approval.

Frontend: one new `TradingAgentView.xaml` / `TradingAgentViewModel.cs`, reusing the existing `ApiService.cs` HTTP pattern and `WebSocketService.cs` for streaming responses — not a new communication layer.

Database: no changes needed for Phases 1-4. Phase 5 (conversation memory) will need one small new table + an Alembic migration — flagged now, not before.

---

## 6. What I Am NOT Proposing

- Not touching either existing scoring pipeline's internals.
- Not removing Market/Portfolio views or the legacy Security card — still awaiting your separate approval on those, unrelated to this initiative.
- Not writing any Trading Agent code yet.

Waiting for your go-ahead on this review before Phase 2 (Trading Agent Core).
