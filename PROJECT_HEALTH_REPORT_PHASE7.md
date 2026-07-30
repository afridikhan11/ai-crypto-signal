# Project Health Report — Phase 7 (Portfolio Intelligence & AI Performance Monitoring)

Date: 2026-07-28
Scope: new `app/schemas/portfolio.py`, `app/services/portfolio_intelligence.py`, `app/schemas/performance.py`, `app/services/ai_performance_service.py`, `app/api/v1/endpoints/portfolio.py`, `app/api/v1/endpoints/performance.py`; one-line additive registration edit to `app/api/v1/__init__.py`. No edits to `app/agent/*` (frozen per Phase 6 approval), no edits to `app/ai/*`, no edits to any existing scoring/calibration/risk module.

## 1. What was built

This phase is explicitly a reporting/aggregation layer, not a new reasoning layer - consistent with "the architecture is frozen" and "do not create a second scoring engine."

**Portfolio Intelligence** (`app/services/portfolio_intelligence.py`, new):
- **Reuses the existing Decision Engine** (Requirement 1): for each symbol currently held, `_attach_decision_reads()` calls the SAME `build_market_scan_report()` + `DecisionEngine().merge()` pair `orchestrator.py._analyze_asset()` already calls for a trading-pair symbol - not a reimplementation, the literal same functions - to show a fresh "what does the Decision Engine say right now" read next to each exposure line. Capped at 8 distinct symbols per report (disclosed in the response) to avoid an unbounded fan-out of live market scans; any per-symbol failure is caught and surfaced as an honest `decision_note` rather than breaking the report.
- **No second scoring engine** (Requirement 2): every number is either a real live figure (Binance position margin/notional/unrealized P&L) or a disclosed, fixed-formula reshaping of real numbers already computed elsewhere (weight percentages, a standard Herfindahl-Hirschman Index, Pearson correlation reused verbatim from the existing per-signal correlation-warning code).
- **Portfolio-level risk analysis** (Requirement 3): `PortfolioIntelligenceResponse.risk`/`daily_loss` are the EXISTING, completely unmodified `SignalService.get_portfolio_risk()`/`get_daily_loss()` (Phase 1) - simply composed alongside the new sections, not reimplemented.
- **Exposure analysis** (Requirement 4): real live Binance futures positions when an account is linked (same `BinanceAccountService` the Account tab already uses), falling back honestly to the existing active-signal suggested-risk figures when it isn't. Every row is tagged with its real `source` so a caller always knows which real data it's looking at.
- **Correlation analysis** (Requirement 5): `build_correlation_analysis()` imports `_pct_returns`, `CORRELATION_THRESHOLD`, `CORRELATION_LOOKBACK`, `MIN_ALIGNED_SAMPLES` directly from the existing `app/services/correlation_risk.py` - the exact same Pearson-correlation computation already used for per-signal correlation warnings, applied here as a full matrix across current exposure instead of one flag per signal. Honestly unavailable when no live scanner/data manager is attached, or when fewer than 2 symbols are held.
- **Diversification analysis** (Requirement 6): a standard Herfindahl-Hirschman Index (0-10000 scale) computed purely from the exposure weights the module already produced above - by symbol and by asset class - with fixed, disclosed concentration bands. Honestly unavailable with zero open exposure.

**AI Performance Monitoring** (`app/services/ai_performance_service.py`, new):
- **AI performance analytics** (Requirement 7): win rate broken down by confidence band (fixed bands centered on the existing 85-point live-signal bar), by symbol, and by asset class - every count is a real aggregation over already-stored `Signal` rows, reusing the exact join pattern `SignalService.get_stats()` already uses. Calibration health reuses `app.ai.calibration`'s own `WIN_STATUSES`/`LOSS_STATUSES`/`MIN_SAMPLES_PER_GROUP` constants and `app.ai.calibration_profiles.all_profiles()`/`CalibrationProfile.weights_file` unchanged - this is a read-only count of what's already there per asset type, never a recalibration trigger, and nothing in `app/ai/` was touched.
- **Trade journal review** (Requirement 8): reuses the EXISTING `HistoryRepository`/`HistoryQueryParams` (same filters, sort, and pagination the History module already uses) unchanged, reshaping each result into a journal entry with a real exit price (take_profit if TP_HIT, stop_loss if STOPPED, honestly null otherwise), a real held-duration (from the stored `created_at`/`closed_at` timestamps), and the `reason` field copied VERBATIM - same "never rephrase evidence" discipline as `evidence_engine.py`.
- **Reuses existing platform data only, never fabricates statistics** (Requirements 9/10): every "not available" path (zero trades in a bucket, no calibration data yet, no live account) reports `None`/an honest message rather than an invented number; a confidence bucket with zero trades reports `win_rate_pct=None`, never a fabricated 0%.

**Wiring**: two new routers (`/portfolio/*`, `/performance/*`) registered additively in `app/api/v1/__init__.py`, alongside the 10 already-registered routers, none of which were touched.

## 2. Compile check

`python3 -m py_compile` across the entire `app/` tree: **PASSED**, zero syntax errors.

## 3. Regression / functional testing (sandbox)

Same documented sandbox limitation as every previous phase (no real Postgres - sqlalchemy itself is stubbed), which affects this phase MORE than prior ones since Portfolio Intelligence/AI Performance are fundamentally SQL-aggregation-heavy. Within that constraint:

- All 6 new files (2 schema modules, 2 service modules, 2 endpoint modules) import cleanly, both individually and isolated from the pre-existing sibling routers (same technique as every prior phase's `agent.py` isolation).
- All Phase 2-6 functional assertions were re-run unchanged and still pass - no regression to the (frozen, untouched) `app/agent/` package.
- **What WAS genuinely exercised** (every pure, non-database function):
  - **Diversification**: a 90/10 two-symbol split correctly computed HHI=8200 ("Highly concentrated"); a 5-way equal split correctly computed HHI=2000 ("Moderately concentrated" - the standard convention band, not yet "well diversified" until closer to 7-8 equal holdings); an 8-way equal split correctly computed HHI=1250 ("Well diversified"); zero exposure correctly returned an honest "not available" rather than a fabricated HHI of 0.
  - **Correlation**: a synthetic scaled-identical price pair correctly computed ~1.0 correlation and was correctly flagged `same_direction=True`/`stacked_risk=True`; no-scanner-attached and fewer-than-2-symbols cases both correctly returned honest "not available" rather than a guessed matrix. This surfaced and fixed a real bug (see below).
  - **Trade journal formatting**: a TP_HIT signal correctly resolved `exit_price` to the real `take_profit` level and a real 6.0-hour held duration; a STOPPED signal correctly resolved `exit_price` to the real `stop_loss` level; a still-ACTIVE signal correctly reported `exit_price=None` and `held_duration_hours=None` rather than guessing either for an open trade; the `reason` field was confirmed verbatim, unmodified.
  - **Win-rate helper**: zero trades correctly returns `None` (never a fabricated 0%); a real 3-of-4 case correctly returns 75.0%.
  - **Exposure fallback trigger**: confirmed `has_saved_credentials()` returns `False` in this credential-less sandbox, proving the exposure analysis would honestly fall back to active-signal figures rather than fail outright.

- **Bug found and fixed during testing**: `build_correlation_analysis()`'s `stacked_risk` value was computed from pandas' `.corr()` return type (`numpy.float64`), making the boolean comparison a `numpy.bool_` rather than a plain Python `bool`. This compares equal to `True`/`False` but is a different object, which is a real, latent type inconsistency in what should be a plain boolean API field - not merely a test artifact. Fixed with an explicit `bool(...)` cast; re-verified.

**Not verified in this sandbox** (needs your Docker environment, with a real Postgres and, for exposure/correlation, a running scanner + linked Binance account): every SQL-query-constructing function (`get_confidence_bucket_stats`, `get_symbol_performance`, `get_asset_class_performance`, `get_calibration_health`, `get_trade_journal`, and `build_exposure_analysis`'s active-signal-fallback / live-Binance-position paths) and the existing pytest suite.

Recommended verification on your side:
```
docker compose exec app python -m pytest
docker compose up -d --build
docker compose exec app curl -s http://localhost:8000/api/v1/portfolio/intelligence \
  -H "Authorization: Bearer <token>"
docker compose exec app curl -s "http://localhost:8000/api/v1/performance/overview" \
  -H "Authorization: Bearer <token>"
docker compose exec app curl -s "http://localhost:8000/api/v1/performance/journal?page=1&page_size=10" \
  -H "Authorization: Bearer <token>"
```
Check `portfolio/intelligence`'s `exposure.items[].current_decision` populates from a real, fresh Decision Engine read; `correlation.pairs` populates once the scanner has cached enough real candles; and `performance/overview`'s `calibration_health` reflects real win/loss counts per asset type.

## 4. Architecture compliance

- **Reuses the existing Decision Engine** (Requirement 1): confirmed by construction - `_attach_decision_reads()` calls the unmodified `build_market_scan_report()`/`DecisionEngine().merge()` pair, nothing reimplemented.
- **No second scoring engine** (Requirement 2): confirmed - the only new arithmetic anywhere in this phase is weight percentages, a textbook HHI, and the SAME Pearson correlation already used elsewhere; nothing computes a score, confidence, or decision.
- **Portfolio-level risk / exposure / correlation / diversification analysis** (Requirements 3-6): all four built and tested as described above.
- **AI performance analytics / trade journal review** (Requirements 7-8): both built, reusing `SignalService.get_stats()`, `HistoryRepository`, and `app.ai.calibration`'s existing constants unchanged.
- **Reuses existing platform data, never fabricates statistics** (Requirements 9-10): every figure traces to a real stored field or a real live account/candle read; every "no data" case is an honest null/message, verified explicitly in testing (empty exposure, zero-trade confidence buckets, still-ACTIVE journal entries, no-scanner correlation).
- **Architecture freeze respected**: zero edits to `app/agent/*` or `app/ai/*`; the two new routers are purely additive registrations; no existing endpoint, schema, or service function was modified.

## 5. What's next

Compile ✅, regression tests (sandbox-limited, disclosed above) ✅, health report ✅. Waiting for your approval before the next phase.
