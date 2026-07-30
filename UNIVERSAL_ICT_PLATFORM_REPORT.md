# Universal Institutional ICT Platform — Phase 1 Implementation Report

**Date:** 2026-07-30
**Scope:** One scanner, one AI, one signal generator, one risk engine, one evidence engine, one ICT pipeline — for every supported asset. Legacy retail strategy code isolated. Commodity foundation delivered as an Asset Profile.

---

## 1. Architecture Diagram

```
                          ┌──────────────────────────┐
                          │   UniversalScanner       │  ← the ONLY scanner
                          │ app/scheduler/           │
                          │   universal_scanner.py   │
                          └────────────┬─────────────┘
                                       │ per symbol
                    ┌──────────────────▼───────────────────┐
                    │ AssetProfile  (app/assets/)          │  describes the MARKET
                    │  sessions · kill zones · liquidity   │  never the strategy
                    │  volatility · hours · events         │
                    │  tick/contract · ICT filters         │
                    │  └─ .calibration → CalibrationProfile│  (referenced, not copied)
                    └──────────────────┬───────────────────┘
                                       │
        ┌──────────────────────────────▼──────────────────────────────┐
        │       THE Universal ICT Pipeline — SignalGenerator          │
        │              app/strategy/signal_generator.py               │
        ├─────────────────────────────────────────────────────────────┤
        │ Market Structure → Liquidity → Order Blocks → FVG →         │
        │ Supply/Demand → Premium/Discount → Session/Kill Zones →     │
        │ Inducement → OTE → HTF Structure → Institutional Bias       │
        │                          │                                  │
        │   market data: app/smc/order_flow.py (ATR·CVD·POC·RelVol)    │
        └──────────────────────────┬──────────────────────────────────┘
                                   ▼
                  ┌────────────────────────────────┐
                  │ ICTEvidenceEngine              │  what the facts mean
                  │ app/ai/evidence_engine.py      │
                  └───────────────┬────────────────┘
                                  ▼
                  ┌────────────────────────────────┐
                  │ AIScorer  (Confidence Engine)  │  how much confidence
                  │ app/ai/scorer.py — 13 ICT cats │
                  └───────────────┬────────────────┘
                                  ▼
                  ┌────────────────────────────────┐
                  │ ICTDecisionEngine              │  what we do, and WHY
                  │ app/ai/ict_decision_engine.py  │
                  └───────────────┬────────────────┘
                                  ▼
              LONG  ·  SHORT  ·  NO TRADE  (always fully explained)
                                  │
              ┌───────────────────┴────────────────────┐
              ▼                                        ▼
   ┌────────────────────┐                  ┌──────────────────────┐
   │ RiskEngine         │                  │ Entry / Exit /       │
   │ app/risk/          │                  │ TradeManagement      │
   └────────────────────┘                  └──────────────────────┘

   ╔═══════════════════════════════════════════════════════════════╗
   ║  app/legacy/  — QUARANTINED retail TA. Zero production imports ║
   ║  enforced by tests/test_legacy_isolation.py                    ║
   ╚═══════════════════════════════════════════════════════════════╝
```

## 2. Scanner Flow

```
Load Symbols          UniversalScanner(ALL_SYMBOLS) from app/main.py
      ↓
Determine Asset Type  app.core.constants.asset_type_for_symbol
      ↓
Load Asset Profile    app.assets.get_asset_profile  (resolved ONCE per symbol,
      ↓                                              at construction)
Run Universal ICT     SignalGenerator.evaluate() — ONE pass over the candle
Pipeline              (11 ICT engines, profile-driven sessions/kill zones)
      ↓
Generate Evidence     ICTEvidenceEngine → ICTEvidenceReport
      ↓
AI Decision           AIScorer (confidence) → ICTDecisionEngine (decision + why)
      ↓
Signal                persisted + published, or a fully-explained NO_TRADE
```

Every symbol — BTCUSDT and XAUUSDT alike — takes that exact path. `analyze_symbol()` contains **no branch on asset type**; a test asserts this by parsing its AST.

## 3. Asset Profile Diagram

```
AssetProfile
├── asset_type / asset_class / display_name
├── session_windows        {Session: SessionWindow}   → drives SessionEngine
├── kill_zone_windows      {KillZone: SessionWindow}  → drives SessionEngine
├── london_ny_overlap      SessionWindow
├── liquidity              primary sessions, equal-H/L behaviour, description
├── volatility             gaps_across_breaks, session_clustered, description
├── trading_hours          is_24_7, trades_weekends, daily_break, is_open()
├── economic_events        high/medium impact tuples, priority_of()
├── ict_filters            require_kill_zone, reject_off_session,
│                          min_confluence_signals  (can ONLY tighten)
├── tick_size / contract_size / tick_size_by_symbol
└── calibration ──────────► CalibrationProfile (existing, referenced)

Registry:  crypto → CRYPTO_PROFILE
           gold ┐
           silver ├──────► COMMODITY_PROFILE   (one market shape…)
           oil  ┘                               …three separate calibrations
           forex → FOREX_PROFILE      ┐
           indices → INDICES_PROFILE  ├─ future-ready, unreachable today
           stocks → STOCKS_PROFILE    ┘
```

**Crypto vs Commodity, side by side:**

| | Crypto | Commodity (Gold/Silver/Oil) |
|---|---|---|
| Sessions | Asian + London + New York | **London + New York** |
| Kill zones | Asian, London, NY, London Close | London, **COMEX open (13:00–15:00)**, NY, London Close |
| Primary liquidity | all three sessions | London + NY only |
| Trading hours | 24/7, weekends | settlement break 22:00–23:00 UTC, no weekends |
| Gaps | no | yes |
| Volatility | spread across day | session-clustered |
| ICT filters | none (unchanged) | `reject_off_session=True` |
| Calibration | crypto weights | gold / silver / oil weights |

Gold's entire commodity behaviour comes from this table — **there is no Gold strategy, no Gold scanner, no Gold AI.**

## 4. Legacy Inventory

| Module | Retail content | Still used by |
|---|---|---|
| `app/legacy/confirmation.py` | EMA20/50/200, RSI, MACD, Stoch RSI, CCI, Williams %R, ADX, Supertrend, Bollinger, Keltner, Donchian, OBV, VWAP | `ta_dashboard.py`, `market_scorer.py`, `token_scorer.py` |
| `app/legacy/candlestick_patterns.py` | 6 classical reversal patterns | `market_scorer.py` |
| `app/legacy/chart_patterns.py` | Double Top/Bottom, Triangles, Flags | `market_scorer.py` |
| `app/legacy/trend.py` | EMA20/50 trend classification | `market_scorer.py`, `ta_dashboard.py`, `backtest/engine.py` |

Nothing was deleted. Four modules were **moved** into quarantine and their four legitimate consumers repointed. The allowlist in the isolation test is exactly those four files, and a test fails if a consumer stops using legacy code (so the allowlist can only shrink) or if a new module starts using it.

## 5. Files Added

| File | Purpose |
|---|---|
| `app/assets/__init__.py`, `app/assets/asset_profile.py` | Asset Profile system + registry + 6 profiles |
| `app/smc/order_flow.py` | ICT-native ATR / CVD / POC / relative volume — removes `ta` from production |
| `app/scheduler/universal_scanner.py` | The one scanner |
| `app/legacy/__init__.py` | Quarantine boundary, documented |
| `tests/test_asset_profile.py` (38) | Profiles + commodity foundation |
| `tests/test_universal_scanner.py` (24) | Scanner flow, single-pass, routing |
| `tests/test_legacy_isolation.py` (14) | Mechanical boundary enforcement |
| `tests/test_order_flow.py` (25) | Order-flow primitives + `ta` equivalence |

## 6. Files Modified

| File | Change |
|---|---|
| `app/strategy/signal_generator.py` | Profile-driven; `OrderFlowMetrics` replaces retail suite; EMA HTF gate replaced with ICT-native institutional-bias gate; profile ICT filters; new `evaluate()` single-pass entry point |
| `app/smc/session_engine.py` | Added `COMEX_OPEN_KILL_ZONE` (additive — not in the default map) |
| `app/ai/ict_decision_engine.py` | Added `OFF_SESSION`, `KILL_ZONE_REQUIRED` rejection gates |
| `app/main.py` | Wires `UniversalScanner` |
| `app/services/market_scorer.py`, `ta_dashboard.py`, `token_scorer.py`, `backtest/engine.py` | Imports repointed to `app/legacy/` (import lines only — **no logic touched**, backtest engine logic untouched per constraint) |
| `app/services/binance_service.py`, `app/legacy/trend.py` | Stale references to the deleted scanner corrected |
| `tests/test_order_block_engine.py`, `tests/test_signal_generator.py` | Updated for moved modules / new internals |

## 7. Files Archived / Removed

**Archived (moved to `app/legacy/`):** `confirmation.py`, `candlestick_patterns.py`, `chart_patterns.py`, `trend.py`

**Removed:**
- `app/scheduler/scanner.py` — the old `CryptoScanner`, fully superseded
- `app/indicators/` and `app/analysis/` — empty package shells after the move, with zero importers

**Renamed:** `tests/test_confirmation_indicators.py` → `tests/test_legacy_confirmation.py`

## 8. Integration Report

- **One scanner.** `grep '^class .*Scanner'` returns exactly `UniversalScanner`. A test asserts no `CryptoScanner`/`GoldScanner`/`ForexScanner` class name exists anywhere.
- **One pipeline.** Exactly one `SignalGenerator` class. Crypto and gold generators share every engine type — verified attribute-by-attribute in a test.
- **One AI.** One `AIScorer`, one `ICTEvidenceEngine`, one `ICTDecisionEngine`. A test asserts no `CryptoAI`/`GoldAI`/`ForexAI`/per-asset scorer class exists.
- **One risk engine.** Exactly one `RiskEngine`.
- **Profile injection.** The scanner resolves each profile once and hands that same object to the pipeline — asserted by identity (`is`), not equality.
- **No production retail imports.** `app/smc`, `app/ai`, `app/assets`, `app/risk`, `app/strategy`, `app/scheduler` import neither `app.legacy` nor `ta`. Enforced by AST parsing, not grep.

## 9. Performance Report

Measured on a realistic 500-candle frame:

| | Before | After | Change |
|---|---|---|---|
| Market-data primitives | 136.0 ms (20-indicator retail suite) | **3.1 ms** (`OrderFlowMetrics`) | ~43x |
| Full pipeline `evaluate()` | 181.5 ms | **24.0 ms** | ~7.6x |
| `evaluate()` — BTCUSDT vs XAUUSDT | — | 23.98 ms / 24.23 ms | identical cost ⇒ genuinely one pipeline |

Cumulative across all three phases: `generate()` has gone **315.7 ms → 24.0 ms (~13x)**.

**Honest caveat:** the 136 ms legacy figure is partly inflated by the verification harness's `ta` stub (its CCI uses a slow `rolling.apply`). The real `ta` library would be faster, so the true speedup is smaller than 43x. The *structural* win is not in doubt: the pipeline no longer computes ~20 indicators of which it read 4, and the 24.0 ms end-to-end figure is measured against real project code.

Other performance work: the scanner calls `evaluate()` **once** per candle (a test fails if `generate()`/`generate_decision()` are called separately, which would scan twice); five EMA passes per symbol per scan are gone; HTF frames reuse existing websocket and long-TTL REST caches, adding no network round-trips.

## 10. Regression Report

**491 passed / 0 failed**, 29 test files — up from 390 at the end of the previous phase (**+101 tests**).

New this phase: 38 asset profile · 24 scanner · 25 order flow · 14 legacy isolation.

Every pre-existing ICT engine suite passes unchanged: Market Structure 17, Liquidity 20, Order Block 21, Supply/Demand 23, Session 26, Inducement 17, Evidence 42, Scorer 28, OTE 11, HTF 9, Institutional Bias 13, Decision 33, Monitor 26.

One behavioural change is deliberate and disclosed: `test_order_flow.py` documents a **bug fix** inherited from the old code — `cvd_rising` used `max(0, idx - 10)` with `idx = -1`, which collapses to index `0`, so it compared against the *first candle of the frame* rather than 10 candles back, contradicting its own comment. It now reads the intended short-term window, with a dedicated regression guard.

## 11. Universal Platform Readiness

| Success criterion | Status |
|---|---|
| Exactly ONE Universal Scanner | ✔ enforced by test |
| Exactly ONE Universal ICT Pipeline | ✔ enforced by test |
| Exactly ONE AI | ✔ enforced by test |
| Exactly ONE Risk Engine | ✔ enforced by test |
| Exactly ONE Signal Generator | ✔ enforced by test |
| Crypto uses the Universal ICT Pipeline | ✔ |
| Gold uses the Universal ICT Pipeline | ✔ same engine types, identical cost |
| Commodity behaviour ONLY from the Commodity Asset Profile | ✔ no asset-type branching in the scan path |
| No production code depends on retail strategies | ✔ AST-enforced |
| Legacy code isolated from production | ✔ allowlist can only shrink |
| Ready for Forex/Indices/Stocks via profiles alone | ✔ three profiles already declared |

### Open items (disclosed, not blockers)

1. **⚠️ No migration tool (unchanged, still needs your decision).** No Alembic in the repo, so the decision payload still can't be persisted and partial-close state can't be tracked. Carried forward from the previous report.
2. **`app/services/market_scorer.py` remains a second, legacy scan path** serving the Token Scanner and Agent features. It is fully isolated from the ICT path but is not itself ICT. Migrating those features onto the Universal Pipeline is the natural Phase 2 and was not in this phase's objectives.
3. **`BacktestEngine` still computes EMA trends** and passes them positionally. The pipeline now ignores them. Its logic was untouched per the explicit constraint; the parameters remain in `generate()`'s signature solely for that compatibility.
4. **Offline harness caveat.** `sqlalchemy`/`loguru`/`pydantic`/`ta`/`httpx`/`websockets`/`redis` are inert stubs in this sandbox. The `ta` ATR equivalence test skips here and runs in CI/Docker — it detects the stub explicitly rather than passing against a fake.

---

**Phase 1 objectives are complete.** All five deliverables — Universal Scanner, Asset Profile System, Universal ICT Pipeline, Legacy Strategy Isolation, Commodity Architecture Foundation — are implemented, integrated, and mechanically verified.
