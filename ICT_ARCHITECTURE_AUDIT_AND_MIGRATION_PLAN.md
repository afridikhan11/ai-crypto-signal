# AI Crypto Signal Pro — Institutional ICT Architecture Audit & Migration Plan

**Date:** 2026-07-29
**Scope:** Complete read of the FastAPI Backend, the WPF frontend, Docker, CI, and tests, per the "Senior Quant Developer / ICT Expert / Python-FastAPI-PostgreSQL-Docker Architect" mandate.
**Status:** STEPS 1-9 (read, audit, find problems, plan). No code has been changed. This document is the deliverable for STOP-and-wait-for-approval. Nothing below has been implemented.

---

## STEP 1-2: Architecture Report

### 1.1 Two-project layout

```
ai-crypto-signal/
├── FastAPI Backend/        Python 3.12, FastAPI, async SQLAlchemy 2.0, PostgreSQL, Redis, Docker
└── AI_Crypto_Signal_Pro/   .NET WPF desktop client (C#), consumes the REST API + /ws/signals websocket
```

The backend is the entire trading engine: data ingestion, SMC/ICT detection, AI scoring, signal persistence, trade monitoring, portfolio/performance analytics, a conversational "Trading Agent," and Binance execution. The WPF app is a thin client — DTOs, ViewModels, Views, an `ApiService`/`WebSocketService` pair. It contains no trading logic of its own and is out of scope for the ICT-purity mandate (it renders whatever the backend computes); it is inventoried below for completeness but not targeted for removal/rewrite in this plan.

### 1.2 Backend module map (by responsibility)

| Area | Files | Responsibility |
|---|---|---|
| **Core ICT detection** | `app/smc/market_structure_engine.py`, `order_block_engine.py`, `liquidity_engine.py`, `supply_demand_engine.py`, `fvg.py` | The four ICT engines built in this project's own migration series (all single-implementation, no legacy duplicates remain — see Section 3.1). |
| **Confirmation indicators** | `app/indicators/confirmation.py`, `candlestick_patterns.py`, `chart_patterns.py`, `app/analysis/trend.py` | **Retail technical-analysis library** (`ta` package: EMA/RSI/MACD/ADX/CCI/Williams %R/Bollinger/Keltner/Donchian/OBV/VWAP) plus a hand-rolled Supertrend, classic candlestick patterns, and classical chart patterns. This is the primary target of the ICT-purity mandate — see Section 3.2. |
| **AI Scoring** | `app/ai/scorer.py`, `calibration.py`, `calibration_profiles.py` | `AIScorer.assess()` blends 13 weighted categories into a 0-100 confidence score. Six categories are genuine ICT/SMC evidence; seven are retail-indicator or mixed. Full weight breakdown in Section 3.2. |
| **Signal generation** | `app/strategy/signal_generator.py` | Orchestrates all four ICT engines + FVG + confirmation indicators into one signal per symbol; computes structure-based SL and liquidity-based TP. |
| **Duplicate scan paths** | `app/services/market_scorer.py`, `token_scorer.py`, `ta_dashboard.py` | Three near-identical reimplementations of `signal_generator.py`'s ICT-engine-orchestration block, one per surface (live Binance-pair scoring, on-chain token scoring, TA dashboard cards) — see Section 3.3/5.1. |
| **Live data & scheduling** | `app/scheduler/scanner.py`, `signal_monitor.py`, `app/services/binance_service.py` | `CryptoScanner` streams candles, triggers `analyze_symbol()` on every closed 15m candle, persists signals, publishes to Redis. `SignalMonitor` watches open signals for SL/TP hits. |
| **API layer** | `app/api/v1/endpoints/*.py` (13 routers) | dashboard, signals, history, stats, health, auth, account, agent, backtest, performance, portfolio, token_scan, trading. |
| **WebSocket** | `app/websocket/signal_ws.py`, the `/ws/signals` handler in `main.py` | Redis-pubsub-backed fan-out of new signals to connected WPF clients. |
| **Trading Agent** | `app/agent/*.py` (12 files, ~3,470 lines) | A conversational layer (`orchestrator.py`, `intent_parser.py`, `decision_engine.py`, `evidence_engine.py`, `research_engine.py`, `trading_coach.py`, `risk_manager.py`, `explainer.py`, `provider_manager.py`, `asset_resolver.py`, `strategy_profile_manager.py`, `conversation_context.py`) sitting on top of the same AIScorer/ICT-engine outputs — clean of retail-indicator terms by name, but inherits AIScorer's retail-weighted confidence wholesale. |
| **Backtesting** | `app/backtest/engine.py`, `calibration.py` | Replays historical candles through the same `SignalGenerator`/`AIScorer` and derives calibrated weights from simulated win/loss outcomes. |
| **Auto Trading (execution)** | `app/services/binance_trading_service.py`, `binance_account_service.py`, `binance_credentials.py`, `app/security/api_key_cipher.py` | Real order placement against Binance, encrypted-at-rest API credentials. |
| **Risk / sizing** | `app/services/position_sizing.py`, `correlation_risk.py`, `app/agent/risk_manager.py` | Three separate modules, no single "Risk Engine" — see Section 8. |
| **Token Scanner (on-chain)** | `app/services/dexscreener_service.py`, `geckoterminal_service.py`, `token_security_service.py`, `contract_security_service.py`, `smart_money_service.py` | A parallel, DEX-token-oriented scan path, reusing the same `ta_dashboard.py` SMC pipeline. |
| **Fundamentals/macro** | `app/services/fundamentals_service.py`, `macro_fundamentals_service.py` | Funding rate, OI, long/short ratio, Fear & Greed, BTC dominance (crypto) / DXY, real yield, event risk (commodities). |
| **Persistence** | `app/models/{signal,coin,user}.py`, `app/repositories/*.py` | 3 tables total: `signals`, `coins`, `users`. No Alembic migrations despite the dependency being installed — see Section 6.2. |
| **Core infra** | `app/core/{config,database,redis,security,logging,constants}.py` | Settings, async engine, Redis client, JWT primitives, loguru setup, symbol/asset-class constants. |

### 1.3 Data flow (live path)

```
Binance WS/REST (BinanceDataManager)
   → CryptoScanner.on_new_candle (15m close only)
      → SignalGenerator.generate(df)
         → MarketStructureEngine.analyze()          [ICT]
         → FVGDetector.detect_fvg()                 [ICT]
         → OrderBlockEngine.find_*_order_block()     [ICT]
         → LiquidityEngine.detect_*_liquidity()       [ICT]
         → SupplyDemandEngine.find_*_zones()          [ICT]
         → ConfirmationIndicators (RSI/MACD/EMA/…)   [RETAIL — see 3.2]
         → detect_latest_pattern / detect_chart_pattern [RETAIL — see 3.2]
         → AIScorer.assess(features) → confidence, reason, score_breakdown
      → hard gates (min_confidence, sweep-or-OB present, HTF opposition)
      → structure-based SL / liquidity-based TP
   → CryptoScanner.save_signal() → Postgres `signals` row + Redis publish("new_signal")
      → signal_ws_manager (Redis subscriber) → WPF clients over /ws/signals
SignalMonitor (separate loop) watches open signals against live price → TP_HIT/STOPPED → Redis publish
```

`market_scorer.py` (on-demand Binance-pair scoring for the dashboard/API) and `token_scorer.py` (on-chain token scoring) run the **same** ICT-engine block independently, on-demand, outside this scheduled loop — see Section 5.1 for the duplication/performance cost this creates.

### 1.4 API surface (13 routers, `/api/v1/*`)

`dashboard` (market/AI panels), `signals` (list/detail), `history`, `stats`, `health` (DB+Redis liveness), `auth` (single-admin JWT login), `account` (Binance balances/positions), `agent` (Trading Agent chat), `backtest`, `performance` (AI outcome analytics), `portfolio` (correlation/risk summary), `token_scan` (DEX tokens), `trading` (order execution). All mounted under one `APIRouter` in `app/api/v1/__init__.py`. Auth is enforced per-endpoint via a FastAPI dependency, globally togglable via `Settings.require_auth` (default **off** — see Section 7).

### 1.5 WebSocket flow

One endpoint, `/ws/signals`, in `main.py`. `SignalWsManager` (`app/websocket/signal_ws.py`) subscribes to two Redis pubsub channels (`new_signal`, presumably a monitor-status channel) on app startup and fans out to every connected WPF client; a 30s server-side ping keeps connections alive. Clean, single-purpose, no retail-indicator involvement.

### 1.6 Database models (3 tables, no migration framework in use)

- **`coins`**: symbol, name, `is_active`, `asset_class` (crypto/commodity), rank, market_cap, volume_24h.
- **`signals`**: direction, entry/SL/single-TP, risk_reward, confidence, reason, status (ACTIVE/TP_HIT/STOPPED/CANCELLED), `score_breakdown` (JSON — the full AIScorer category breakdown, used for calibration), Auto-Trading execution tracking fields (executed, order id, environment).
- **`users`**: email, hashed_password, is_active, is_superuser, full_name — **defined but not wired into `auth.py`'s single-admin-env-var login flow** (see Section 6.1 and Section 8).

`alembic==1.14.1` is a listed dependency but **no `alembic/` directory or `alembic.ini` exists anywhere in the repository.** Schema evolution instead happens via a growing block of idempotent, hand-written `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / one-time-data-migration `text()` statements inside `main.py`'s `on_startup()` (visible: `score_breakdown`, `asset_class`, the four Auto-Trading columns, plus a full TP1/2/3→single-TP collapse with an enum-type rename). This works today but is a real architectural liability — see Section 6.2.

### 1.7 Docker / CI

- `docker-compose.yml` (dev): Postgres 16, Redis 7, app with `--reload`, DB/Redis ports exposed to host, credentials hardcoded directly in the compose file rather than interpolated from `.env`.
- `docker-compose.prod.yml`: no host port exposure for DB/Redis, `--workers 1` (explicitly and correctly justified in a comment — the scanner/monitor/websocket-listener are in-process singletons with no cross-worker coordination), log-driver caps, named volume for `logs/`. This file is well-designed.
- Single `Dockerfile` shared by both: `python:3.12-slim`, no multi-stage build, **no non-root `USER`**, **no `HEALTHCHECK`**, and its baked-in `CMD` still carries the dev `--reload` flag (prod compose overrides `command:`, but the image itself is not prod-safe if run without that override).
- CI (`.github/workflows/backend-tests.yml`): compiles every `.py` file, then runs `pytest -v`. Clean, minimal, works — but see Section 6.3 for what it doesn't cover.

### 1.8 Tests (520 lines total, 7 files)

`test_ai_scorer.py`, `test_backtest_engine.py`, `test_calibration.py`, `test_confirmation_indicators.py`, `test_correlation_risk.py`, `test_position_sizing.py`, `test_supply_demand.py`. See Section 6.3.

---

## STEP 3: Legacy Strategies Found

### 3.1 Legacy SMC engines — already fully removed (confirmed, not a finding requiring action)

This project already completed a rigorous, self-contained SMC→ICT migration series (documented in `MARKET_STRUCTURE_CUTOVER_REPORT.md`, `ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md`, `LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md`, `SUPPLY_DEMAND_ENGINE_IMPLEMENTATION_REPORT.md`). Verified during this audit:

- `app/smc/market_structure.py`, `order_blocks.py`, `liquidity.py`, `supply_demand.py` (the old, non-ICT implementations) **do not exist on disk.**
- A project-wide grep for the old class names (`LiquidityDetector`, `SupplyDemandZones`, and the deleted modules' import paths) returns **zero hits** outside historical docstring mentions inside the new engines themselves.
- `app/smc/` today contains exactly one file per concept: `market_structure_engine.py`, `order_block_engine.py`, `liquidity_engine.py`, `supply_demand_engine.py`, `fvg.py`. **No duplicate BOS/Liquidity/OB logic exists.** This part of Rule #2 is already satisfied.

### 3.2 The real legacy strategy: a retail indicator library wired directly into AI confidence

This is the central finding of this audit and the one Rule #2/the AI-Engine mandate is squarely about. `app/indicators/confirmation.py` wraps the third-party `ta` package (RSI, MACD, ADX, CCI, Stochastic RSI, Williams %R, Bollinger Bands, Keltner Channel, Donchian Channel, OBV, VWAP) plus a hand-rolled Supertrend, and every one of these values flows directly into `AIScorer.assess()`. `app/analysis/trend.py`'s `ema_trend_from_df()` — a bare EMA20/EMA50 crossover — is the **sole** mechanism used to compute BTC trend, the symbol's own 1h/4h/1D/5m trend, i.e. everything that currently stands in for "Institutional Bias" and "Multi-Timeframe Analysis."

**Exact weight breakdown** (from `app/ai/calibration.py`'s `DEFAULT_WEIGHTS`, which sum to 1.0 and are the confidence-score denominator every signal is gated on):

| Category | Weight | Real signal source | ICT-pure? |
|---|---:|---|:---:|
| `market_structure` | 7.7% | MarketStructureEngine BOS/CHoCH | ✅ ICT |
| `liquidity_sweep` | 5.9% | LiquidityEngine sweeps + real Binance liquidation stream | ✅ ICT |
| `order_block_quality` | 7.7% | OrderBlockEngine | ✅ ICT |
| `fvg_presence` | 3.6% | FVGDetector | ✅ ICT |
| `supply_demand_zone` | 3.6% | SupplyDemandEngine (Premium/Discount) | ✅ ICT |
| `confluence` | 3.7% | OB+FVG+Liquidity zone overlap | ✅ ICT |
| **ICT subtotal** | **32.2%** | | |
| `trend_filters` | 13.8% | EMA20/50 cross, ADX, volume spike, **EMA200, VWAP, Supertrend** | ❌ Retail |
| `momentum` | 4.6% | **RSI, MACD, Stochastic RSI, CCI, Williams %R** | ❌ Retail |
| `volatility_context` | 4.6% | ATR regime (✅) + **Bollinger/Keltner squeeze, Donchian breakout** (❌) | Mixed |
| `pattern_confirmation` | 4.6% | Candlestick patterns (Engulfing/Hammer/Star) + classical chart patterns (Double Top, Triangle, Flag) | ❌ Retail |
| `volume_order_flow` | 13.8% | **OBV** (❌ retail) + CVD, Volume Profile POC (✅ real taker-side data, institutional-flavored) | Mixed |
| `multi_tf_alignment` | 18.4% | BTC trend + weighted 1D/4H/1H/5M — **100% EMA20/50 crossover underneath** | ❌ Retail mechanism, ICT-correct *concept* |
| `fundamental_context` | 8.0% | Funding rate, OI, long/short ratio, Fear&Greed, BTC dominance, (commodities) DXY/real yield/event risk | Not technical-indicator-based; legitimate macro/institutional context |
| **Non-ICT-evidence subtotal** | **~59.8-67.8%** | | |

**Reading this table plainly: roughly 41% of every confidence score (`trend_filters` + `momentum` + Bollinger/Keltner/Donchian-within-`volatility_context` + `pattern_confirmation` + OBV-within-`volume_order_flow`) is driven by exactly the strategies Rule #2 explicitly lists for complete removal** — "EMA crossover strategies," "RSI strategies," "MACD strategies," "Supertrend strategies," "Bollinger strategies." These are not vestigial — they are live, weighted, and can move a signal across the confidence threshold today. The largest single category in the entire scorer, `multi_tf_alignment` at 18.4%, is conceptually a legitimate ICT idea (Institutional Bias / HTF alignment) but is currently **computed** by a retail EMA crossover, not by real ICT structure (e.g. `MarketStructureEngine.structure_alignment` run per timeframe, or protected-high/low position).

**Positive finding:** `ATR` (kept — explicitly an approved concept), real `CVD`/Volume Profile POC (built from Binance's genuine per-candle taker-buy-volume field, not an estimate — a legitimate institutional order-flow proxy even though it lives inside a category also polluted by OBV), and the entire `fundamental_context` bucket (funding rate, OI, long/short ratio, DXY, real yield, event risk) are **not** retail technical-analysis indicators at all — they are real market/macro data and should be preserved, likely reorganized under an explicit "Institutional Bias" bucket rather than removed.

### 3.3 Duplicate ICT-orchestration logic across four call sites

`signal_generator.py`, `market_scorer.py`, `token_scorer.py`, and `ta_dashboard.py` each independently:
1. Instantiate `MarketStructureEngine`, run `.analyze()`.
2. Instantiate `FVGDetector`, run `.detect_fvg()`.
3. Instantiate `OrderBlockEngine` with `structure_breaks=`/`fvgs=`, find OB(s).
4. Instantiate `LiquidityEngine` with `structure_breaks=`/`order_blocks=`/`fvgs=`, find levels, run sweep detection.
5. Instantiate `SupplyDemandEngine` with the same confluence params, find zones and/or read Premium/Discount.

This is not literally duplicated *code* (each file's block is slightly shaped for its own consumer, and the previous 4-phase migration series deliberately kept these as separate call sites rather than merging them mid-migration), but it is duplicated *computation*: the same 15m OHLCV window for the same symbol gets all five ICT detectors re-run from scratch in every one of up to four places that touch it (scheduled scan, on-demand dashboard, on-demand pair scoring, on-chain token scoring). `ta_dashboard.py` additionally runs `MarketStructureEngine` **twice per call** (`ms_default` at `external_pivot_window=5` for the BOS/CHoCH/Liquidity cards, `ms_tiers` at `internal_pivot_window=3, external_pivot_window=8` for the Internal/External BOS cards) — a real, already-justified-in-code, but real, duplicate computation. See Section 5.1 for the consolidation recommendation.

### 3.4 No dead/unreachable files found

Checked for: orphaned old-engine files (none — all deleted in prior migrations), unused service modules (all services grepped and traced to at least one real API-endpoint or scanner call site), unused DB tables (none — all 3 tables are actively written/read), commented-out legacy code blocks (none found — a full `TODO|FIXME|placeholder|mock|dummy|not implemented` sweep across `app/` returned only honest, intentional documentation of things the project deliberately does NOT implement, e.g. wallet-address analysis, MSS-as-separate-rule, contract-address research — consistent with this project's stated "never hallucinate" discipline, not policy violations). `setup_module1.sh`/`setup_module2.sh` at the FastAPI Backend root are historical one-time setup scaffolding, already confirmed inert in prior work this session (referenced nowhere in Docker, CI, or app startup).

---

## STEP 4: Duplicate Implementations

Beyond the ICT-orchestration duplication in Section 3.3:

- **HTF/trend computation** is duplicated in spirit across `app/analysis/trend.py` (the shared pure function — correctly deduplicated between live scanner and backtest engine, good practice) but its retail nature means the "duplicate" that actually matters is conceptual: the *same* EMA-crossover function is asked to answer BTC trend, symbol 1h/4h/1D/5m trend, and (implicitly, via `htf_trend_1d`/`htf_trend_4h` opposition gates) "Institutional Bias" — one retail tool wearing four different ICT-sounding hats.
- **Risk/position logic** is split three ways with no shared abstraction: `app/services/position_sizing.py` (account-risk→quantity), `app/services/correlation_risk.py` (portfolio correlation warnings), `app/agent/risk_manager.py` (Trading Agent's own risk read). Not literally duplicated code, but three separate "risk" surfaces with no single Risk Engine module owning the concept — see Section 8.
- **Candlestick + chart pattern detection** (`indicators/candlestick_patterns.py`, `indicators/chart_patterns.py`) each implement their own independent swing/shape logic that is conceptually redundant with what the ICT engines already compute (displacement, structure) — not duplicate code today (chart_patterns.py correctly reuses `MarketStructureEngine`'s own `SwingPoint` list rather than inventing a second pivot detector, which is good engineering), but duplicate *purpose*: both exist to answer "has price structure/momentum shifted," a question BOS/CHoCH/OB/FVG/displacement already answer natively and more precisely.

No other duplicate implementations found. Signal repositories, schemas, and endpoint routers are each single-purpose and non-overlapping.

---

## STEP 5: Performance Bottlenecks

### 5.1 Redundant ICT-engine computation across scan paths (the main one)

As detailed in 3.3: up to 4 independent full re-computations of Market Structure → FVG → Order Blocks → Liquidity → Supply & Demand for the same symbol/window, with no caching layer between them, plus `ta_dashboard.py`'s own internal double `MarketStructureEngine` call. For a single symbol requested via the dashboard seconds after the scheduled scanner already computed the identical 15m candles, this is pure duplicate work. `app/core/redis.py`'s Redis client is already wired into the app (used today only for pubsub) and is the natural place to cache a symbol's latest ICT snapshot keyed by its most recent closed-candle timestamp, invalidated automatically the next time a new 15m candle closes.

### 5.2 `SupplyDemandEngine.find_demand_zones()`/`find_supply_zones()` — O(n × base_len) pandas-slice cost, already measured

This project's own `SUPPLY_DEMAND_ENGINE_IMPLEMENTATION_REPORT.md` already measured and disclosed this: real zone detection costs ~203ms/call on a 60-candle window (vs ~0.2ms for the old flat-range read), because the scanner re-evaluates a local-ATR pandas slice at every `(candle, base_length)` combination. Currently only exercised by `ta_dashboard.py`. Confirmed still present; a real, disclosed, not-yet-optimized cost.

### 5.3 `ConfirmationIndicators._calculate_poc()` — O(bins × candles) Python loop

`_calculate_poc()`'s volume-binning step (`for idx, vol in zip(bin_idx, window["volume"].to_numpy())`) is a plain Python loop over up to 200 rows per call, once per `ConfirmationIndicators` construction — i.e. once per symbol per 15-minute scan cycle today, but the same class is also reconstructed inside every backtest replay step (per the class's own `_calculate_supertrend()` docstring, which documents that the Supertrend loop *was* the dominant cost of a multi-symbol backtest sweep before it was vectorized with numpy in an earlier pass — POC's loop was not given the same treatment). `np.bincount`/`np.add.at` would remove this loop entirely; low priority today, but relevant if `_calculate_poc()`'s retail-adjacent purpose (Volume Profile POC) survives the ICT-purity migration in some form.

### 5.4 No other unvectorized/duplicate-scan issues found

`OrderBlockEngine`, `LiquidityEngine`, `MarketStructureEngine` all use single forward-scan-per-call lifecycle tracking (documented, intentional, already performance-compared against their predecessors in this project's own implementation reports). `Supertrend`'s sequential loop is already numpy-vectorized-around (documented). No N+1 database query patterns found in the repositories reviewed (`history_repository.py`, `signal_repository.py`) — both use single `select()` statements with joins, not per-row queries in a loop.

---

## STEP 6: Architectural Problems

### 6.1 `users` table exists but is unused; auth is a single hardcoded admin

`app/models/user.py` defines a real `User` table (email, hashed_password, is_active, is_superuser, full_name), but `app/core/security.py` and `app/api/v1/endpoints/auth.py` implement a **single-admin, env-var-configured** login (`ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`), never querying the `users` table at all. This is honestly documented in `core/security.py`'s own docstring ("Single-admin-user model... there is no user table/multi-tenant data isolation anywhere else") — not a bug, but a real architectural inconsistency: a full ORM table exists for a feature (multi-user auth) that the rest of the system (global scanner instance, global signal set, no per-user credential scoping) isn't built to support anyway. Either the `User` table should be removed until real multi-tenancy is designed, or it should be documented as forward-scaffolding, not both left ambiguous.

### 6.2 No migration framework in active use despite being a dependency

Covered in 1.6. `alembic` is installed but unused; schema evolution is a hand-written, ever-growing sequence of idempotent `ALTER TABLE IF NOT EXISTS` statements plus one genuinely complex one-time data migration (the TP1/2/3→single-TP collapse, which renames a Postgres enum type live) inside `main.py`'s startup hook. This works, and is defensively written (each step wrapped in its own try/except with a warning log, so a failure doesn't crash startup) — but it has no rollback story, no migration history/versioning, and every new schema change means hand-editing `main.py` again rather than generating a reviewable migration file. For a project explicitly aiming to "look like it was built by a professional quantitative trading firm," this is the single highest-leverage infrastructure gap to close, independent of the ICT-purity work.

### 6.3 Test coverage does not include the ICT engines, signal generation, or the API layer at all

The 7 files in `tests/` (520 lines) cover: AIScorer, BacktestEngine, calibration, ConfirmationIndicators, correlation risk, position sizing, and Supply & Demand's Premium/Discount contract. **None of the four ICT engines' own detection logic (Market Structure, Order Blocks, Liquidity, the real zone-detection half of Supply & Demand) has a test in the committed `tests/` directory.** The rigorous 17/21/23/22-check regression suites this project's own prior migrations built and ran (per `MARKET_STRUCTURE_ENGINE_IMPLEMENTATION_REPORT.md` etc.) live only in ad hoc scratchpad scripts outside version control — they were never committed as permanent `tests/test_market_structure_engine.py` / `test_order_block_engine.py` / `test_liquidity_engine.py` / `test_supply_demand_engine.py` files. This means the CI pipeline (Section 1.7) currently provides **zero automated regression protection** for the actual core trading logic — the one part of this system where a silent regression is most costly. Similarly untested: `signal_generator.py`'s SL/TP and hard-gate logic, `market_scorer.py`/`token_scorer.py`/`ta_dashboard.py`, every API endpoint, the websocket manager, and all 12 Trading Agent modules (~3,470 lines, 0% test coverage).

### 6.4 No Core-ICT vs. Crypto-Module vs. future-Commodities-Module boundary yet

The four `app/smc/*_engine.py` files are already genuinely asset-agnostic (they take a plain OHLCV `DataFrame` and optional confluence lists — nothing Binance- or crypto-specific leaks into them; this is good, and is exactly the "Core ICT" the user wants reusable). The problem is everything **around** them: `signal_generator.py`'s `generate()` method, and `AIScorer.assess()`'s `fundamental_context` block, both contain crypto-specific logic (BTC dominance, Fear & Greed, BTC trend gate) and commodity-specific logic (DXY, real yield, event risk) as **inline if/else branches inside the same function**, gated by `asset_class_for_symbol()`/`is_commodity` checks scattered through the method body. `CalibrationProfile` (per-asset-type thresholds) is a genuinely good pattern already in place for *parameters*; there is no equivalent pattern yet for *data sourcing* — no `FundamentalsProvider` interface with a `CryptoFundamentalsProvider`/`CommodityFundamentalsProvider` implementation swapped by asset type. Today, adding a real future Commodities module (or Forex, per the placeholder `ASSET_TYPE_FOREX`) means adding more branches to these same shared methods rather than adding a new, isolated module — exactly the coupling the user's explicit "Commodity specific logic must remain separate from Crypto logic" instruction is asking to avoid.

### 6.5 No circular imports found; module boundaries are otherwise clean

Checked import graphs for `app/ai/`, `app/smc/`, `app/services/`, `app/strategy/`, `app/agent/` — one documented, deliberately-avoided near-cycle (`calibration_profiles.py` ↔ `calibration.py`, resolved with a local import, explicitly commented). No other circular import risk found. Each API endpoint file maps to exactly one concern; schemas are cleanly separated from ORM models; the `app/repositories/` layer is thin and correctly isolates the two endpoints that use it (`history`, and indirectly `signals`) from raw SQLAlchemy queries.

---

## STEP 7: Security Issues

| # | Finding | Severity | Detail |
|---|---|---|---|
| 1 | `REQUIRE_AUTH` defaults to `False` | High (if deployed as-is) | Every API endpoint (including `trading` — real Binance order execution — and `account`) is reachable with no login by default. Already loudly warned about at startup (`main.py`'s `_run_production_readiness_checks()`), but the default itself is the risky choice for a project whose stated goal is institutional-grade. |
| 2 | `SECRET_KEY` defaults to the literal string `"change_me_in_production"` | High (if unchanged) | This single key signs JWTs **and** derives the Fernet key that encrypts stored Binance API credentials (`app/security/api_key_cipher.py`). Already flagged at startup. Not itself a code defect (a safe default is impossible for a symmetric secret), but worth calling out because two unrelated security properties (auth + credential encryption) share one key with no rotation story. |
| 3 | No rate limiting anywhere | Medium | No throttling middleware on any endpoint, including `auth/login` (brute-forceable against the single admin account) or `trading/execute` (behind `require_auth`, but still unthrottled). |
| 4 | No CORS policy configured | Low/Informational | `main.py` adds no `CORSMiddleware`. Not currently exploitable (no browser-based client exists yet), but worth deciding deliberately before any web frontend is added, rather than having a future contributor bolt on `allow_origins=["*"]` without noticing `REQUIRE_AUTH` defaults off. |
| 5 | Dev `docker-compose.yml` hardcodes DB credentials in the compose file itself (not `.env`-interpolated) | Low | Inconsistent with `docker-compose.prod.yml`'s correct `${POSTGRES_USER}`/`${POSTGRES_PASSWORD}` interpolation. Dev-only, low real risk, easy normalize. |
| 6 | Dockerfile has no non-root `USER`, no `HEALTHCHECK`, and bakes `--reload` into the default `CMD` | Medium | Runs as root inside the container by default; a deploy that forgets to override `command:` (as `docker-compose.prod.yml` correctly does) ships a dev server. |
| 7 | `users` table + password hashing exist but aren't part of the active auth path | Informational | See 6.1 — not itself a vulnerability, but dead security-relevant code is worth resolving one way or the other. |
| 8 | SQL injection | **Clear** | Every query reviewed (`app/repositories/*.py`, `app/ai/calibration.py`, `main.py`'s startup `ALTER TABLE` statements) uses either SQLAlchemy's parameterized ORM query builder or static, hardcoded `text()` SQL strings with no user-input interpolation. No injection vector found. |
| 9 | Binance credential storage | **Good practice, confirmed** | `ApiKeyCipher` uses Fernet (AES-128-CBC + HMAC) with a PBKDF2-HMAC-SHA256-derived key (390,000 iterations) — appropriate, not a placeholder/toy implementation. |
| 10 | `.env` correctly gitignored | **Good practice, confirmed** | `.gitignore` excludes `.env`; the tracked `.env`/`.env.example`/`.env.production.example` files in the working tree contain only placeholder values, no real secrets. |

---

## STEP 8: Incomplete ICT Concepts

Cross-checked every concept in the user's explicit KEEP list against the codebase (grep + read):

| Concept | Status |
|---|---|
| Market Structure, Swing Structure, Internal Structure, BOS, CHoCH | ✅ Fully implemented (`MarketStructureEngine`) |
| MSS | ✅ Implemented as an honest label (`is_mss=True` on every CHoCH) with a documented rationale (no consistent distinct ICT rule set found in prior evidence search) — not a gap |
| Liquidity, Liquidity Pools, Liquidity Sweeps | ✅ Fully implemented (`LiquidityEngine`) |
| **Inducement** | ❌ **Not implemented.** No detection, naming, or scoring anywhere. `LiquidityEngine`'s existing sweep/grab/reversal-linkage machinery is the natural foundation to extend (an inducement is essentially a liquidity grab specifically engineered to trap retail before the real move — distinguishable from a genuine sweep by its relationship to the subsequent real displacement/BOS). |
| Order Blocks, Breaker Blocks, Mitigation Blocks | ✅ Fully implemented (`OrderBlockEngine`) |
| Fair Value Gaps, Inverse FVG | ✅ Fully implemented (`FVGDetector`) |
| Supply Zones, Demand Zones | ✅ Fully implemented (`SupplyDemandEngine`) |
| Premium, Discount, Equilibrium | ✅ Fully implemented (`SupplyDemandEngine`'s backward-compatible Premium/Discount read) |
| **OTE (Optimal Trade Entry)** | ❌ **Not implemented.** No 0.618-0.79 retracement-zone entry logic anywhere. Entries today are simply "current price at signal time," not refined against an OTE zone within the identified OB/FVG/zone. |
| **Session Analysis / Kill Zones** | ❌ **Not implemented at all.** No concept of London/New York/Asia sessions, no session-window filtering or weighting anywhere in the codebase (confirmed via grep — the only "Session" matches are Python's `AsyncSession`/generic variable names, not trading sessions). Signals fire identically at 3am and 3pm UTC today. |
| Multi Timeframe Analysis | ⚠️ **Present but not ICT-native.** Real 1D/4H/1H/5M multi-timeframe weighting exists (`multi_tf_alignment`, 18.4% weight — the single largest scoring category) but is computed via EMA20/50 crossover per timeframe, not via real ICT structure per timeframe. See Section 3.2. |
| Displacement | ✅ Implemented (`displacement_ratio` in `MarketStructureEngine`, `OrderBlockEngine`, `SupplyDemandEngine`) |
| Volume Confirmation | ⚠️ **Present but retail-mixed.** Real institutional-flavored data exists (CVD from genuine taker-buy-volume, Volume Profile POC) but is currently bundled in the same scoring category as OBV (retail). |
| ATR Confirmation | ✅ Implemented and explicitly correct to keep — used both as a real volatility-regime read and as the universal displacement-magnitude denominator across all four ICT engines. |
| **Institutional Bias** | ⚠️ **Conceptually present, not explicitly named or ICT-derived.** `fundamental_context` (funding/OI/long-short/DXY/real-yield) is real institutional-context data and the closest existing match, but there is no single, explicitly-named "Institutional Bias" bucket combining it with a true ICT-native HTF structure read. |
| **Entry Model** | ❌ **Not formalized.** Entry is "current live price," full stop — no OTE refinement, no session-timing gate, no explicit named Entry Model module. |
| **Exit Model** | ❌ **Not formalized.** Single fixed SL/TP per signal (already correctly upgraded from the old TP1/2/3 cascade to one real, structure-anchored target — good existing work), but no partial-exit, breakeven-move, or structure-trailing logic; `SignalMonitor` only checks "has price touched SL or TP yet." |
| **Risk Engine** | ⚠️ **Logic exists, not consolidated.** `position_sizing.py` + `correlation_risk.py` + `agent/risk_manager.py` are three separate modules; no single Risk Engine owns sizing + correlation + drawdown/exposure limits together. |
| Trade Management | ❌ Same gap as Exit Model — no post-entry management logic beyond the binary SL/TP watch. |

**Summary: 8 of the 27 explicitly-named KEEP concepts are either fully missing (Inducement, OTE, Session Analysis/Kill Zones, formal Entry Model, formal Exit Model, consolidated Risk Engine, Trade Management) or implemented with a retail mechanism standing in for what should be an ICT-native one (Multi-Timeframe Analysis, Institutional Bias, Volume Confirmation).** Everything else on the list is genuinely, verifiably implemented.

---

## STEP 9: Migration Plan

This plan is sequenced to minimize risk (verify-then-cut, exactly like the four prior successful engine migrations this project already completed) and to respect **"Never rewrite working code unnecessarily. Only replace code when there is a measurable improvement."** The four ICT engines themselves are NOT touched in this plan — they are correct, tested (in scratchpad form), and already the sole implementation of their concepts. Phase order below is a recommendation; I am stopping for your approval before any of it begins, per Rule #1's workflow.

### Phase A — Commit the existing regression suites (do first, zero risk, unlocks everything else safely)

Move the four scratchpad regression suites (`mse_verification/`, `ob_verification/`, `liq_verification/`, `sd_verification/` — currently outside version control) into `tests/test_market_structure_engine.py`, `test_order_block_engine.py`, `test_liquidity_engine.py`, `test_supply_demand_engine.py`, adapted to the project's real pytest fixtures/conventions. This closes the Section 6.3 gap and gives every subsequent phase a real safety net before anything else changes. **Justification for doing this first:** every later phase touches code these tests would catch regressions in; doing it last would mean cutting the AIScorer and confirmation pipeline with less protection than the ICT engines already deserve.

### Phase B — Rebuild `AIScorer` on ICT-only evidence (the core of Rule #2 and the AI Engine mandate)

1. Remove `momentum` (RSI/MACD/StochRSI/CCI/Williams %R) and the Bollinger/Keltner/Donchian portion of `volatility_context` as scoring categories entirely.
2. Remove `trend_filters` as a retail-EMA/ADX/Supertrend/VWAP category. Replace with a genuine **Institutional Bias** category derived from `MarketStructureEngine.structure_alignment` (already computed, already real ICT evidence) run at higher timeframes, not EMA crossover.
3. Replace `ema_trend_from_df()` as the mechanism behind `multi_tf_alignment` with the same real-structure-per-timeframe read, keeping the category's name, weight, and weighted-timeframe-stack architecture (1D > 4H > 1H > 5M) — that architecture is sound ICT methodology, only its data source needs replacing.
4. Split `volume_order_flow` into keeping CVD + Volume Profile POC (real institutional order-flow evidence) and dropping OBV.
5. Remove `pattern_confirmation` (candlestick + classical chart patterns) as a scoring category — these are retail pattern taxonomy, not ICT.
6. Reorganize `fundamental_context` under an explicit **Institutional Bias** umbrella alongside the rebuilt HTF-structure read from step 2/3, since both are genuinely the same underlying ICT concept (what is "smart money" actually doing / where is the market actually biased).
7. Every removed category's weight must be redistributed to real ICT categories (market_structure, liquidity_sweep, order_block_quality, fvg_presence, supply_demand_zone, confluence, the rebuilt Institutional Bias/HTF bucket) — **not silently dropped to zero**, per "Every confidence score must explain WHY" and this project's own "never fabricate, never silently delete signal" discipline already visible in `calibration.py`'s comments.
8. `ConfirmationIndicators` itself should be reduced to computing only what survives: ATR (kept), CVD/Volume Profile POC (kept), the rebuilt structure-based HTF bias inputs. The `ta` package dependency and the hand-rolled Supertrend can then be removed from `requirements.txt` entirely once nothing references RSI/MACD/StochRSI/CCI/Williams/Bollinger/Keltner/Donchian/OBV/EMA-crossover/Supertrend/VWAP.
9. Delete `indicators/candlestick_patterns.py` and `indicators/chart_patterns.py` (retail pattern taxonom(y), superseded by native ICT displacement/structure evidence) and their one call site each in `signal_generator.py`/`market_scorer.py`/`token_scorer.py`.
10. Re-run backtest-based calibration (`app/backtest/calibration.py`) against the rebuilt scorer before shipping, so the new weight distribution is evidence-derived, not hand-guessed — consistent with this project's existing calibration philosophy.

**This phase is the single largest, highest-risk phase — it changes the number every downstream gate/UI reads.** It should ship behind the exact same "historical replay + AI impact analysis + performance comparison + full regression suite" verification discipline used for all four prior engine migrations, and produce its own `AI_SCORER_ICT_MIGRATION_REPORT.md`.

### Phase C — Add the missing ICT concepts

1. **Session Analysis / Kill Zones**: a new, small, asset-agnostic `app/smc/session_engine.py` (or similar) tagging each candle/signal with its session (Asian/London/New York/London-NY overlap) in UTC, exposed as a new scoring input and as a hard/soft gate (many ICT traders explicitly avoid entries outside Kill Zones).
2. **Inducement**: extend `LiquidityEngine`'s existing sweep/reversal-linkage output to distinguish a genuine inducement (a sweep specifically preceding the real displacement leg) from an ordinary sweep — additive, no change to the engine's existing verified behavior.
3. **OTE**: a small, pure function (fits naturally as a `SignalGenerator` helper or a tiny new module) computing the 0.618-0.79 retracement zone of the most recent real displacement leg, used to refine entry price within an identified OB/FVG/zone rather than always using "current price."
4. **Formal Entry Model / Exit Model / Trade Management**: consolidate the currently-inline SL/TP logic in `signal_generator.py` into an explicit, testable `EntryModel`/`ExitModel` pair; extend `SignalMonitor` to support partial exits / breakeven moves / structure-trailing stops, not just a binary SL/TP watch.
5. **Consolidated Risk Engine**: merge `position_sizing.py` + `correlation_risk.py` + `agent/risk_manager.py`'s overlapping concerns into one `app/risk/risk_engine.py` module owning sizing, correlation exposure, and (new) portfolio-level drawdown/exposure limits, with the existing three modules' call sites updated to the single new entry point.

Each of these is additive/new capability, not a rewrite of working code, and should each get its own small regression suite + report, matching this project's established pattern.

### Phase D — Architecture cleanup (lower risk, can run in parallel with B/C)

1. Introduce Alembic properly: `alembic init`, generate a baseline migration matching current schema, replace `main.py`'s hand-written `ALTER TABLE` block with real migration files (one-time cutover, carefully sequenced so existing deployments don't re-run already-applied changes).
2. Resolve the `users`-table-vs-single-admin-auth inconsistency: either wire real per-user auth to it, or remove the unused table — a deliberate decision, not left ambiguous.
3. Introduce a `FundamentalsProvider` interface (crypto/commodity implementations) so `AIScorer`/`signal_generator.py` stop branching on `is_commodity` inline, cleanly separating Crypto-Module logic from Core-ICT and preparing the explicit Commodities-Module boundary the user asked for. Forex (`ASSET_TYPE_FOREX`) stays untouched/unimplemented per the explicit "ignore Forex completely" instruction — the interface should make adding it trivial later without being built now.
4. Add a Redis-backed cache for each symbol's latest ICT-engine snapshot (Section 5.1), keyed by symbol + most-recent-closed-candle timestamp, consumed by `market_scorer.py`/`token_scorer.py`/`ta_dashboard.py` instead of each recomputing from scratch.
5. Dockerfile: add a non-root `USER`, a `HEALTHCHECK`, and drop `--reload` from the baked-in `CMD` (dev compose already explicitly overrides `command:`, so this is a pure hardening change, not a dev-workflow regression).
6. Add rate limiting (e.g. `slowapi`) to `auth/login` at minimum, ideally globally.
7. Normalize dev `docker-compose.yml` to `.env`-interpolate DB credentials the same way `docker-compose.prod.yml` already correctly does.

### What is explicitly NOT in this plan

- No Forex implementation of any kind (per explicit instruction).
- No rewrite of the four ICT engines themselves — they are correct and already the sole implementation.
- No change to the WPF frontend's UI/UX — only its data contract may shift if `score_breakdown`'s category keys change in Phase B, which is a coordinated, additive DTO update, not a redesign.
- No change to Auto Trading execution logic (`binance_trading_service.py`) — out of scope for an ICT-purity audit.

---

## Every deletion this plan recommends, with justification

| To remove | Justification |
|---|---|
| `momentum` scoring category (RSI/MACD/StochRSI/CCI/Williams %R) | Explicitly named for removal by Rule #2 ("RSI strategies," "MACD strategies"); zero ICT content. |
| `trend_filters` scoring category (EMA20/50/200 cross, ADX, Supertrend, VWAP) | Explicitly named for removal ("EMA crossover strategies," "Supertrend strategies"); zero ICT content. |
| Bollinger/Keltner/Donchian portion of `volatility_context` | Explicitly named for removal ("Bollinger strategies"); ATR portion of the same category is kept. |
| `pattern_confirmation` (candlestick + classical chart patterns) | Retail pattern taxonomy; the "has structure shifted" question it answers is already answered natively and more precisely by BOS/CHoCH/OB/FVG/displacement. |
| OBV within `volume_order_flow` | Retail oscillator; CVD (real taker-side data) already serves the same "Volume Confirmation" purpose more accurately within the same category. |
| `indicators/candlestick_patterns.py`, `indicators/chart_patterns.py` | Only call site is the category being removed above; no other consumer. |
| `ema_trend_from_df()` as the HTF-bias mechanism | Retail EMA crossover standing in for what Rule/KEEP-list calls "Institutional Bias"/"Multi-Timeframe Analysis" — replaced by real ICT structure per timeframe, not deleted as a concept. |
| `ta` package + hand-rolled Supertrend from `requirements.txt`/`confirmation.py` | Once nothing above references them, this is a genuinely dead dependency, not just dead code. |

Every new module this plan proposes, with justification, is stated inline in Phase C/D above.

---

**STOP — awaiting your approval before any implementation begins**, per Rule #1's explicit workflow (read → report → find legacy → find duplicates → find bottlenecks → find architectural problems → find security issues → find incomplete concepts → migration plan → **stop**).
