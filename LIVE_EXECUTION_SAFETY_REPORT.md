# Live Execution Safety Report

**Mandate: Make Live Trading execution behavior match the platform's already-validated Risk Engine and Trade Management.**
Date: 2026-07-30 | Scope: execution-safety only — no ICT calculation, signal generation, confidence scoring, or Asset Profile logic was changed. This phase closes the two Critical Issues raised in `LIVE_TRADING_VALIDATION_REPORT.md` (Objectives 6 & 9): RiskEngine not enforced at execution, and trade management not synchronized to the live exchange order.

## 1. Files Modified

| File | Change |
|---|---|
| `app/services/execution_risk.py` | **New.** Framework-independent Risk Engine execution gate — `assess_execution_risk()` / `TradeRiskRejected`. |
| `app/api/v1/endpoints/trading.py` | `execute_signal()` now calls `assess_execution_risk()` before any position sizing or Binance call; a rejection returns HTTP 400 with every reason. The old bare `calculate_position_size()` fallback is gone. |
| `app/services/signal_service.py` | Added `build_portfolio_risk_context()`, a public wrapper around the existing private `_build_risk_context()` so the execution gate reads the exact same portfolio-risk inputs the signal list/detail endpoints already display. No existing method changed. |
| `app/services/binance_trading_service.py` | Added `replace_stop_loss()`, `get_open_stop_orders()`, `cancel_order()`, `StopReplacementResult`, and the idempotent client-order-id helper. `place_signal_bracket()` and `close_position()` — the two methods validated in the prior phase — are untouched. |
| `app/scheduler/signal_monitor.py` | Added `_build_trading_service_for()`, `_get_live_position_quantity()`, `_sync_exchange_stop()`, `_close_live_position_on_structure_failure()`. `_apply_management()` converted to `async` and now gates every DB stop-loss update and structure-failure closure on the exchange sync result. `_resolve_status`, `_breaks_after`, `_improves_stop`, `_evaluate_management` are unchanged. |
| `tests/test_execution_risk.py` | **New**, 11 tests. |
| `tests/test_binance_trading_service_stop_sync.py` | **New**, 16 tests. |
| `tests/test_signal_monitor.py` | Extended (26 → 42 tests): existing tests updated for the async signature change, 16 new tests for the exchange-sync gating logic. |

No file under `app/smc/`, `app/ai/`, `app/assets/`, `app/strategy/signal_generator.py`, or `app/scheduler/universal_scanner.py` was touched — confirmed both by review and by a file-modification-time sweep (Section 7).

## 2. Architecture — Before / After

**Before:**

```
POST /trading/execute/{signal_id}
    -> calculate_position_size(balance, entry, stop, risk_percent)   [bare, no risk gate]
    -> place_signal_bracket()                                        [entry + SL + TP orders]
    -> signal.executed = True

signal_monitor.check_active_signals() (every 30s)
    -> TradeManagementEngine.evaluate()
    -> _apply_management()
        -> signal.stop_loss = new_stop     [DB only — exchange order never touched]
```
RiskEngine was only ever called from `signal_service._signal_to_response()`, which feeds the signal list/detail display — not execution. A signal RiskEngine would reject could still be executed. Once executed, a breakeven/trailing move updated the database's `stop_loss` while the real STOP_MARKET order kept resting at its original price — dashboard, database, and exchange could silently disagree.

**After:**

```
POST /trading/execute/{signal_id}
    -> assess_execution_risk(signal_service, signal)
         -> build_portfolio_risk_context()
         -> RiskEngine.assess_new_trade()
         -> approved?  no  -> raise TradeRiskRejected -> HTTP 400 (all reasons), NO order placed
                        yes -> continue
    -> place_signal_bracket()
    -> signal.executed = True, executed_environment = <testnet|mainnet>

signal_monitor.check_active_signals() (every 30s)
    -> TradeManagementEngine.evaluate()
    -> _apply_management()   [async]
        -> structure failure?
              executed? -> _close_live_position_on_structure_failure()  [real reduceOnly MARKET close]
              signal.status = CANCELLED   (always, regardless of live-close outcome)
        -> stop improvement?
              _sync_exchange_stop(signal, symbol, new_stop)
                  not executed -> True (unchanged DB-only behavior)
                  executed     -> replace_stop_loss() [new stop placed, THEN old cancelled]
              synced? yes -> signal.stop_loss = new_stop   (DB advances only now)
                      no  -> DB untouched, warning logged, next poll retries
```

## 3. Execution Flow (Objective 1)

`execute_signal()` loads the signal, checks it is not already executed and is still `ACTIVE`, then calls `assess_execution_risk(signal_service, signal)` — the one and only place position size is computed for a real order. That function builds a `PortfolioRiskContext` (account balance, risk %, open positions, today's closed trades, equity peak) via the same `SignalService` method the display layer uses, and passes it to the unmodified `RiskEngine.assess_new_trade()`. If any limit is breached (open risk, exposure, daily loss, drawdown) or the account balance/risk % is unknown, `TradeRiskRejected` is raised with every reason attached, and the endpoint returns HTTP 400 — no Binance call is made. Only on approval does the endpoint proceed to `place_signal_bracket()` (unchanged) and mark the signal executed. There is no code path from this endpoint to a Binance order that does not pass through `assess_execution_risk()` first.

## 4. Risk Flow (Objective 1)

`assess_execution_risk()` lives in `app/services/execution_risk.py`, which imports nothing from FastAPI — it is a plain async function over `SignalService` and `RiskEngine`, both pre-existing and untouched. It either returns an approved `RiskAssessment` (carrying the computed position size) or raises `TradeRiskRejected` — there is no third outcome, so a caller cannot forget to check `.approved`. Because it reuses `build_portfolio_risk_context()`, the exact same inputs the signal-list endpoint uses to show `risk_approved`/`risk_reasons` to a user browsing signals are what execution enforces — verified directly in `test_execution_risk.py::TestDisplayAndExecutionNeverDisagree`, which asserts the display path and the execution path reach the identical verdict and reasons for the same context.

## 5. Stop Synchronization Flow (Objective 2)

For a signal with `executed = True`, every stop improvement (`MOVE_TO_BREAKEVEN` or `TRAIL_STOP`) now calls `_sync_exchange_stop()` before the database is touched. That method:

1. Resolves credentials and refuses to proceed if none are saved, or if `signal.executed_environment` (recorded at execution time) doesn't match the currently-saved environment — never guesses which account to touch.
2. Fetches the live, signed position size fresh from Binance (never a stored value).
3. Calls `BinanceTradingService.replace_stop_loss()`, which places the **new** reduceOnly STOP_MARKET order first, and only cancels the old one once the new one is confirmed resting on the exchange — so a failure at either step can never leave the position with zero protection.
4. Returns `True` only when the exchange genuinely now reflects the new stop (or when the signal was never executed, in which case behavior is unchanged from before this phase).

`_apply_management()` only assigns `signal.stop_loss = new_stop` when this returns `True`. A structure-failure verdict on an executed signal additionally calls `_close_live_position_on_structure_failure()`, which closes the real position with a single reduceOnly MARKET order (reusing the same `close_position()` used by `POST /trading/close-position`) and cleans up any stray resting stop.

## 6. Failure Recovery Flow / Rollback Strategy (Objective 3)

Every failure mode gates forward, never backward — there is nothing to "roll back" because the database is only ever advanced *after* the exchange confirms:

- **New stop placement fails** (Binance rejection or exhausted retries on a transient network error): the old stop is never cancelled, `replace_stop_loss()` returns `success=False`, `_sync_exchange_stop()` returns `False`, and `Signal.stop_loss` in the database is left at its previous value. The next 30-second poll re-evaluates and retries automatically.
- **New stop succeeds but the old-stop cancel fails**: treated as success (a redundant, still-protective stray order is not a safety failure) — `success=True` with a `warning` attached, logged as "exchange sync succeeded with a note."
- **Cannot read currently-resting orders at all**: `replace_stop_loss()` refuses to place anything rather than risk an unreasoned double-protection state.
- **Live position close on structure failure fails**: the signal is still marked `CANCELLED` in the database (that judgement is about the ICT trade thesis, not about execution), but a loud warning is logged and published so a human can close the manual position — the same "never silently continue" discipline `place_signal_bracket()` already used for a failed bracket leg.
- **Transient network errors** (`httpx.TimeoutException`, `ConnectTimeout`, `ConnectError`, etc.) placing the new stop are retried up to `STOP_PLACEMENT_MAX_ATTEMPTS` (3) with a fixed backoff; a definitive Binance rejection (`BinanceTradingError`) is never retried.
- **Idempotency**: the `newClientOrderId` sent with every stop replacement is a deterministic `sha256` of `(signal_id, new_stop_price)` — not Python's randomized `hash()` — so an identical retried request (e.g. the API endpoint called twice, or a poll re-running after a crash mid-cycle) is rejected by Binance itself (`-2010`) instead of opening a duplicate order.
- **Trade-management application errors of any kind** are caught in `check_active_signals()`'s existing containment `try/except` (unchanged) — a failure on one symbol never takes down the poller or partially commits other signals in the same batch.

## 7. Test Results (Objective 6)

Full offline regression suite: **586 passed, 0 failed** (up from the 543/543 baseline confirmed at the close of the prior validation-only phase — 43 net new tests: 11 + 16 + 16 across the three files below; the existing 26 in `test_signal_monitor.py` were updated for the `async` signature, not removed).

| Suite | Tests | Covers |
|---|---|---|
| `test_execution_risk.py` | 11 | Risk approved/rejected (exposure, open risk, daily loss, drawdown), unknown account balance, rejection carries every portfolio field, display path and execution path never disagree. |
| `test_binance_trading_service_stop_sync.py` | 16 | New-before-cancel ordering, new-stop failure leaves old stop untouched, cancel failure doesn't downgrade success, refusal when existing orders can't be read, exchange timeout + bounded retry, definitive-rejection-not-retried, idempotent client-order-id generation and stability. |
| `test_signal_monitor.py` (new classes) | 16 | `_build_trading_service_for` (non-executed/no-credentials/environment-mismatch/unrecorded-environment/match), `_sync_exchange_stop` short-circuits, `_apply_management`'s DB-update gating on sync success/failure, structure-failure live-close wiring, and one end-to-end wiring test through fake `BinanceAccountService`/`BinanceTradingService` responses. |

A file-modification-time sweep of `app/` and `tests/` (boundary: 2026-07-30 01:58, when this phase's first edit landed) confirms only the 8 files listed in Section 1 were touched — no file under `app/smc/`, `app/ai/`, `app/assets/`, `app/strategy/signal_generator.py`, or `app/scheduler/universal_scanner.py` carries a timestamp inside this phase.

## 8. Deployment Checklist

- [x] `RiskEngine.assess_new_trade()` is enforced before every real order — verified by code path (Section 3) and by `test_execution_risk.py`.
- [x] Orders cannot bypass risk validation — `execute_signal()` has no code path to `place_signal_bracket()` that skips `assess_execution_risk()`.
- [x] Stop-loss updates reach Binance for executed signals — `replace_stop_loss()`, new-before-cancel, verified.
- [x] Dashboard / Database / Exchange stay synchronized — DB `stop_loss` only advances after exchange confirmation.
- [x] Existing protection is never lost — old stop only cancelled after the new one is confirmed resting; a failed new-stop attempt never touches the old one.
- [x] Duplicate requests are safely ignored — deterministic `sha256`-derived `newClientOrderId`.
- [x] All tests pass — 586/586, zero regressions.
- [x] No ICT logic changed — confirmed by review and mtime sweep.
- [x] No Scanner changes — confirmed.
- [x] No AI/scoring changes — confirmed.
- [ ] **Open, pre-existing, out of scope for this phase**: this module assumes a single running `SignalMonitor` process (documented single-user deployment). Running multiple worker processes against the same database would need a real distributed lock before this exchange-sync logic could be considered multi-process-safe.
- [ ] **Recommended before going live with real capital**: run one full manual cycle against Binance testnet (execute a signal, force a breakeven trigger, force a structure-failure close) to observe the real API responses this test suite only simulates.
