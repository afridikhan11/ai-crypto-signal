# Live / Paper Trading Validation Report

**Phase 2, Objectives 6 & 9 — Paper Trading Preparation, Risk Validation**
Date: 2026-07-30 | Method: full read of the execution/risk/trade-management code paths, cross-referenced against the existing test suite (37 risk-related tests: `test_risk_engine.py` 12, `test_position_sizing.py` 6, `test_signal_service_risk.py` 15, `test_correlation_risk.py` 4 — all passing).

## Architecture found

Three independent Binance integrations, by design (`app/services/binance_trading_service.py`'s own docstring):

1. **AI Module** (`binance_service.py`) — public market data only, powers signal generation, never touches an API key.
2. **Account Module** (`binance_account_service.py`) — read-only authenticated endpoints (balances, positions, history).
3. **Trading Module** (`binance_trading_service.py`) — the only class that sends a signed POST to Binance. Fires exclusively from `POST /trading/execute/{signal_id}` — a user clicking "Execute" on a specific signal. Never automatic, never from the scanner.

**Paper trading = Binance Demo Trading (testnet), not a separate simulator.** There is no dedicated paper-trading engine; `testnet=True` on `BinanceTradingService`/`BinanceAccountService` points every request at `demo-fapi.binance.com` instead of `fapi.binance.com`. Whichever environment is selected in Settings is what every execution uses. `Signal.executed_environment` records which one was used, specifically so a testnet fill is never confused with a mainnet one.

## Objective 6: Paper/Live Execution Readiness

**Order management (verified, working as documented):** `place_signal_bracket()` places exactly 3 orders per executed signal — MARKET entry, reduceOnly STOP_MARKET stop-loss, reduceOnly LIMIT take-profit — with deterministic `newClientOrderId`s derived from the signal's own id, so a retried request is rejected by Binance itself (-2010) instead of silently opening a second position. If the SL or TP leg fails after entry fills, the entry is **not** rolled back (there is no real "undo" for a filled market order); the failure surfaces in `warnings` for the caller/UI, which is the correct choice for a system that must never hide a partially-protected position.

**Stop loss / take profit (verified):** both are placed as real, resting reduceOnly orders on the exchange at signal-execution time — not simulated.

**Trailing logic / trade management (verified, with a real gap found):** `TradeManagementEngine` (via `signal_monitor.py::check_active_signals`) evaluates breakeven/trailing-stop/structure-failure conditions on every poll and, when it decides to move a stop, updates **only** `Signal.stop_loss` in the database. `signal_monitor.py` never imports or calls `BinanceTradingService` — confirmed by grep, zero matches for `BinanceTradingService`/`amend`/`cancel_order`/`modify_order` in that file.

> **⚠️ Critical gap — the DB's stop-loss and the exchange's resting stop-loss order can silently diverge.** Once a signal has been executed (a real `STOP_MARKET` order is resting on Binance at the original price), a later breakeven/trailing move updates the `Signal` row's `stop_loss` field but never cancels/replaces the real order on the exchange. The UI/API would then show a protective stop that does not actually exist on the position — the real position is still protected at the *original*, wider stop. This is a genuine, unaudited safety gap between "signal-level trade management" (which this project has built and tested thoroughly) and "executed-position order management" (which does not yet exist). **Not fixed in this validation-only phase** — closing it means teaching `signal_monitor.py` to cancel-and-replace a real resting order, which is new execution logic touching real money and needs explicit approval, not a unilateral "bug fix."

**Partial exits (verified, working as documented, but advisory-only):** `TradeManagementEngine` can emit a `PARTIAL_CLOSE` action; `signal_monitor.py::_apply_management` explicitly does not execute or persist it — it only logs `"{symbol}: Partial-close opportunity (advisory)"`. This is a disclosed design choice (the module docstring states partial closes "are not executable or persistable in this execution model"), not a bug, but means a user who expects the system to actually scale out of a position will not have that happen automatically.

## Objective 9: Risk Validation

**RiskEngine itself (verified correct, 12 tests passing):** `app/risk/risk_engine.py::RiskEngine.assess_new_trade()` correctly merges position sizing, open risk %, exposure %, daily P&L %, and drawdown % against `RiskLimits` (2%/6%/5%/50%/20% defaults), reusing `calculate_position_size()` rather than duplicating it, and treats a correlation warning as advisory-only (never blocks), matching `correlation_risk.py`'s own design.

> **⚠️ Critical gap — RiskEngine is never called before a real order is placed.** `app/api/v1/endpoints/trading.py::execute_signal()` (the only endpoint that places a real order) computes position size with a **bare** `calculate_position_size(account_balance, signal.entry_price, signal.stop_loss, risk_percent)` call — confirmed by reading the full endpoint — and never imports or calls `RiskEngine`. `RiskEngine.assess_new_trade()` **is** called, but only inside `app/services/signal_service.py::_signal_to_response()`, which powers the signal *listing/detail* endpoints (`GET /signals`, etc.) — it populates the `risk_approved`/`risk_reasons`/`portfolio_open_risk_percent`/`portfolio_exposure_percent` fields a user sees when *browsing* signals. **There is no server-side enforcement**: a signal that `RiskEngine` would flag as `risk_approved: False` (daily loss limit reached, max exposure exceeded, max drawdown reached) can still be sent to `POST /trading/execute/{signal_id}` and will be executed — the execution endpoint has no risk gate at all beyond "can we compute a nonzero position size." This is the single highest-priority finding in this validation phase: the risk display layer and the risk enforcement layer are not the same code path, and only the display layer currently calls `RiskEngine`.

**Daily loss limits (verified as a check that EXISTS in RiskEngine, but per the gap above, is not enforced at execution time):** `RiskLimits.max_daily_loss_percent` (default 5%) is correctly computed from `closed_trades_today` and blocks *display-layer* approval; not checked by `execute_signal()`.

**Maximum exposure (same status):** `RiskLimits.max_exposure_percent` (default 50%) — computed and checked in the display layer only.

**Partial exits:** see Objective 6 above — advisory-only, not executed or persisted, consistently in both the risk and trade-management layers.

**Breakeven / trailing stop (verified working, DB-side):** `TradeManagementEngine`'s breakeven/trailing logic is real and tested (10 tests in `test_trade_management_engine.py`, 26 in `test_signal_monitor.py`), enforces a documented "stop can only tighten, never widen" invariant (`_improves_stop`), and correctly ignores a structure break that occurred at-or-before the signal's own creation (`_breaks_after`) so the break that created the signal is never misread as a structure failure against it. The only gap is the one already named above: these decisions update the DB, not the live exchange order.

## Verdict

**Paper trading order placement, SL/TP bracket construction, and idempotent retry-safety: PASS, production-quality.** **Trade-management-to-live-order synchronization: GAP** (DB and exchange can diverge once a signal is executed). **Risk Engine correctness: PASS.** **Risk Engine enforcement at the point of real execution: FAIL** — it is wired into the read/display path only, not the write/execution path. Both gaps are pre-existing (not introduced by this validation phase) and are flagged, not fixed, per this phase's validation-only mandate; both are carried into the Final Validation Report as Critical Issues.
