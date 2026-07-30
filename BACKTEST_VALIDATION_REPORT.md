# Backtest Validation Report

**Phase 2, Objectives 2–5 — Backtest vs Live Diff, Historical Dataset Validation, Backtest Preparation Reports, Walk-Forward Framework**
Date: 2026-07-30

## Environment disclosure (read this first)

This validation was performed in a sandboxed environment with **no network egress** (confirmed: `curl https://fapi.binance.com/fapi/v1/ping` → no route) and **no stored historical OHLCV dataset** (`data/` contains only encrypted credentials and SMC frequency reports, no candle data). Per this project's no-fabrication policy, every number in this report is either: (a) measured directly against real project code using synthetic-but-labelled data, or (b) explicitly marked "Not Available in this environment" with the reason and what would produce it. Nothing here is a guessed or estimated trading statistic.

---

## Objective 2: Backtest Engine vs Live Pipeline — every difference found

`app/backtest/engine.py::BacktestEngine` and `app/scheduler/universal_scanner.py::UniversalScanner` were read in full and compared line by line. Both **do** use the same `SignalGenerator` class and the same `AssetProfile` resolution (`get_asset_profile(symbol)` — `BacktestEngine.__init__` passes no profile, so `SignalGenerator.__init__` resolves it independently, identically to how the live scanner resolves it). Gold/Oil backtests correctly get `COMMODITY_PROFILE` (same session windows, same `reject_off_session` gate) — confirmed Binance itself lists `XAUUSDT`/`XAGUSDT`/`CLUSDT`/`BZUSDT` as real USDT-margined perpetual futures (see `app/core/constants.py` comment, launched Jan/Apr 2026), so the same `/fapi/v1/klines` fetch path in `BacktestEngine.fetch_klines_range` works for commodities exactly as it does for crypto — no separate data source, no gap there.

The following are the **real, disclosed differences**:

1. **BacktestEngine calls `generator.generate()`, not `generator.evaluate()`.** `generate()` is the backward-compatible, multi-step entry point kept for this exact caller; it returns `Optional[Dict]` and discards the `TradeDecision` object on every `NO_TRADE` step. Practical effect: a live scan logs *why* every rejected candle was rejected (`decision.blocking_gates`, `decision.missing_evidence` — see `UniversalScanner.analyze_symbol`); a backtest run only sees `signal_data is None` and moves on. No trades are missed or different — this is a lost **observability** difference, not a lost **trade**, since `generate()` and `evaluate()` share the identical underlying `_evaluate()` call.

2. **BacktestEngine still computes retail EMA20/50 trend values every step, which the pipeline now ignores.** `_trend_asof()` calls `app.legacy.trend.ema_trend_from_df` (an approved, legacy-quarantine consumer — not an isolation violation, see `REPOSITORY_VALIDATION_REPORT.md` §3) to compute `btc_trend`, `htf_trend_1h/4h/1d`, `entry_trend_5m` on every candle, and fetches five extra candle series (BTC 1h, 1h, 4h, 1d, 5m) over the network to do it. `SignalGenerator._evaluate()`'s own comment confirms these parameters are "deliberately NOT passed on" to the AI Scorer — they exist on `generate()`'s signature purely for this caller's backward compatibility. **Effect: wasted network calls and wasted computation in every backtest run, not an incorrect result** — the values are computed and then genuinely discarded. Not a bug (documented, harmless), but real, disclosed dead work.

3. **`funding_rate`, `liquidation_pressure`, and `fundamentals` are always neutral/empty in a backtest.** `generate()` is called with `funding_rate=0.0`, `liquidation_pressure=None`, `fundamentals=None` unconditionally — including for Gold/Oil, where a **live** scan (`UniversalScanner._load_fundamentals`) fetches real DXY/real-yield/event-risk macro data for commodities specifically. This is disclosed in `BacktestEngine`'s own module docstring for crypto-only free data sources (funding rate, OI, fear/greed), but the docstring does not call out that commodity macro fundamentals are *also* always empty in a backtest even though they are real, live-fetched data for Gold/Oil in production. **This is a genuine backtest/live divergence specific to commodities** worth flagging even though it degrades gracefully (AIScorer treats missing fundamentals as neutral, never crashes).

4. **RiskEngine and TradeManagementEngine are not wired into BacktestEngine at all.** `grep` for `RiskEngine`/`TradeManagementEngine` in `app/backtest/engine.py` returns nothing. `BacktestEngine._resolve_trade()` walks forward from the entry candle checking only the **original, static** stop-loss/take-profit — it never simulates a breakeven move, a trailing-stop tightening, or a structure-failure early close, all of which `app/scheduler/signal_monitor.py` applies to every live/ACTIVE signal. **This means every backtest win-rate/RR statistic reflects a "set it and forget it" exit, not the trade-managed exit a live signal actually gets** — real trades are very likely to close earlier (via trailing stop lock-in or CANCELLED-on-structure-failure) than a backtest replay would show. This is the single largest backtest-vs-live behavioral gap found, and it directly affects how every number in the Objective 4 report below should be read.

5. **Intrabar TP/SL precedence is an assumption, not a certainty.** `_resolve_trade()` checks TP before SL on the same candle for a LONG (`if high >= tp: return TP_HIT ... if low <= sl: return STOPPED`), matching `signal_monitor.py::_resolve_status`'s own TP-first order — but live monitoring polls a single ticking price and never faces the ambiguity of "did price touch TP or SL first within one candle." A backtest candle can have touched both; the code cannot know which happened first intrabar, and resolves optimistically (TP first). This is a standard, disclosed backtesting limitation, not a bug, and is now explicitly recorded here since Objective 2 asks that no difference be hidden.

6. **No position sizing / dollar P&L in a backtest.** Trades are recorded and resolved purely in R-multiples (`realized_rr`); `app/services/position_sizing.py` is never invoked. This is appropriate (account balance isn't a backtest concept) but means "Profit Factor"/"Expectancy" below are R-multiple metrics, not dollar metrics.

**Verdict: the trade-finding logic (structure, liquidity, order blocks, FVGs, gates, calibration) is identical between backtest and live — confirmed, no alternative/simplified/retail-fallback strategy exists in BacktestEngine.** The real differences are all in what happens *around* a found trade: exit management (item 4, the significant one), commodity fundamentals (item 3), and observability (item 1). None of these were modified in this validation-only phase.

---

## Objective 3: Historical Dataset Validation

Built `app/backtest/dataset_validator.py` (new, additive) — a standalone quality gate that checks a historical OHLCV dataframe for exactly the eight things this objective lists, before it would be fed to `BacktestEngine`:

- **Missing candles** — infers expected spacing from the timeframe, flags any gap > 1.5x expected spacing with an exact missing-candle count.
- **Duplicate candles** — flags any duplicated timestamp with examples.
- **Timezone consistency** — hard-fails a tz-*aware* index (this project's documented convention, from `app/smc/session_engine.py`, is tz-naive UTC throughout; a tz-aware index would silently misclassify every session/kill-zone hour-of-day comparison) and any non-monotonic index.
- **OHLC consistency** — `high >= max(open,close,low)`, `low <= min(open,close,high)`, no non-positive or NaN prices.
- **Volume integrity** — no negative/NaN volume; zero-volume candles are a warning (thin liquidity/venue closure), not a failure.
- **Gap handling** — reports the largest gaps found, separately from the missing-candle count.
- **Session handling / commodity trading hours / crypto 24/7** — cross-checks timestamps against an `AssetProfile`'s `TradingHours` (weekend closure, daily settlement break). This check is diagnostic, not pass/fail on the data: this platform's commodity symbols are Binance-listed perpetual futures that trade continuously even though the `AssetProfile` models the traditional COMEX/CME session shape, so candles inside the profile's "closed" window are *expected data*, not corrupt data — it is the pipeline's `reject_off_session` ICT filter, not this validator, that is supposed to treat that window as blocked at the signal level. Crypto's 24/7 profile explicitly passes with zero violations regardless of weekend candle count.

**Verified correct** with 20 tests (`tests/test_dataset_validator.py`), each injecting exactly one defect into an otherwise-clean synthetic dataset and asserting the validator catches *that* defect while every other check still passes — including proving the validator correctly identifies a tz-aware index, an OHLC violation, negative/NaN volume, a missing-candle gap, and commodity weekend/settlement-break candles. All 20 pass.

**Cannot be run against real historical data in this environment** (no network, no stored dataset — see Environment disclosure above). This tool is ready to run the moment real historical candles are available (in the Docker/production environment, which does have Binance network access).

---

## Objective 4: Backtest Preparation Reports

Built `app/backtest/performance_report.py` (new, additive) — reads the `trades` list `BacktestEngine.run()` already returns and computes:

**Computable now, verified with 15 tests (`tests/test_performance_report.py`), each hand-calculating the expected number from a constructed trade list:** Win Rate, Profit Factor, Average Realized RR, Expectancy (R per trade), Max Drawdown (R-multiple equity curve), Average Hold Time, Long vs Short breakdown, Confidence-Bucket breakdown, Asset (per-symbol) Performance.

**Reported as `UNAVAILABLE`, not fabricated — Session Performance, Kill Zone Performance, Order Block Performance, FVG Performance, OTE Performance.** Reason: `BacktestEngine.run()`'s own `trades.append({...})` block (line ~347) only keeps `symbol, direction, entry, stop_loss, take_profit, confidence, score_breakdown, outcome, realized_rr, entry_time, exit_time` — it does not carry forward the richer `signal_data["session"]`, `signal_data["decision"]` (which holds the narrative evidence text), or `signal_data["institutional_bias"]` fields that `SignalGenerator` already computes per signal. The report module names the exact field each missing breakdown needs (e.g. `signal_data['session']` for Session Performance) so the one-line extension is documented, but that extension was **not applied** — it would modify `BacktestEngine.run()`, which is out of this validation-only phase's scope.

**Cannot be run against real trades in this environment** for the same reason as Objective 3 — no historical data to feed `BacktestEngine.run()`. The tool is proven correct against synthetic trade lists and ready to run the moment a real backtest is executed.

---

## Objective 5: Walk-Forward Framework Preparation

Built `app/backtest/walk_forward.py` (new, additive, **framework only — no optimization, no parameter fitting**) — `WalkForwardRunner` splits an already-loaded historical dataframe into `n` sequential, non-overlapping (train, test) windows and reuses `BacktestEngine._resolve_trade` (the real, unchanged method, not a duplicate) plus a fresh `SignalGenerator` per window to produce an independent `PerformanceReport` for each window.

**Why it doesn't call `BacktestEngine.run()` directly:** `run()` always fetches its own window ending at "now" (`end_ms = int(time.time() * 1000)`) with no parameter for an arbitrary historical `[start, end)` range — which a walk-forward split fundamentally requires. Modifying `run()`'s signature would touch Backtesting Engine logic, out of scope; instead `WalkForwardRunner` takes an already-loaded dataframe and reuses the engine's own trade-resolution rule.

**Verified with 10 tests (`tests/test_walk_forward.py`):** correct window count, strictly sequential non-overlapping boundaries, the last window extends to the end of the data, a too-small dataset raises rather than silently shrinking, `_resolve_trade` is confirmed (by identity check) to be the real `BacktestEngine` method rather than a reimplementation, every window produces a report even with zero trades, and — the explicit "no optimization" guarantee — a spy test confirms the exact same `CalibrationProfile.min_confidence` is used in every window (nothing is tuned or carried forward between windows). One additional test runs the real, unmodified ICT pipeline (not a stub) across a small window end-to-end without raising.

**No walk-forward run against real historical data was performed** — same network/data limitation as Objectives 3–4.

---

## Regression

All new modules are additive (no existing file modified). Full suite after adding all four new test files:

```
TOTAL: 536 passed, 0 failed
```
