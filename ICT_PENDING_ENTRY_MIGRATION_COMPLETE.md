# ICT Pending Limit Entry — Migration Complete

**Date:** 2026-07-30
**Scope:** Entry model only. ICT methodology, AI scoring, confidence thresholds, Risk Engine rules, stop-loss methodology, take-profit methodology and the position-sizing formula are all unchanged.

---

## 1. Files Modified

**Backend (16)**

| File | Change |
|---|---|
| `app/models/signal.py` | `PENDING_ENTRY` + `EXPIRED` statuses; 7 new columns; `NON_TERMINAL_STATUSES` / `DECIDED_STATUSES` / `TERMINAL_STATUSES` groupings |
| `app/core/constants.py` | `PENDING_ENTRY_EXPIRY_CANDLES = 12` (3h) + minutes derivative — one definition shared by live monitor and backtest |
| `app/services/trading_settings.py` | `entry_mode` switch (`ict_pending` default / `market`), `get_entry_mode()`, `set_entry_mode()`, `is_ict_pending_entry()` |
| `app/main.py` | Idempotent bootstrap: 7 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, plus `ALTER TYPE signalstatus ADD VALUE IF NOT EXISTS` for both new enum values in their own autocommit connections |
| `app/strategy/signal_generator.py` | **Core change.** `entry_plan` built *before* SL/TP/RR; `_is_usable_entry()` guard; entry/zone bounds emitted on the signal |
| `app/scheduler/universal_scanner.py` | Signals born `PENDING_ENTRY` with zone + expiry; duplicate guard widened to `NON_TERMINAL_STATUSES` |
| `app/scheduler/signal_monitor.py` | Pending state machine: fill / pre-entry invalidation / expiry, exchange reconciliation, stage-2 protective orders, resting-order cancel |
| `app/services/binance_trading_service.py` | `place_limit_entry()`, `place_protective_orders()`, `get_order()` |
| `app/api/v1/endpoints/trading.py` | `execute_signal()` accepts `PENDING_ENTRY` and rests a LIMIT order; market path records `filled_at`/`actual_fill_price` |
| `app/services/trading_control_service.py` | `expire_armed_pending_entries()`; wired into Cancel All Orders + Kill Switch |
| `app/services/signal_service.py` | `closed` now means *decided* (not `!= ACTIVE`); new `pending_entries` count |
| `app/repositories/signal_repository.py` | Armed pending entries count toward portfolio risk; `EXPIRED` excluded from daily realised loss |
| `app/repositories/history_repository.py` | `EXPIRED` visible in History, excluded from win/loss totals |
| `app/schemas/signal.py` | 7 new response fields |
| `app/schemas/trading_control.py` + `app/api/v1/endpoints/trading_control.py` | `pending_entries`, `entry_mode` |
| `app/backtest/engine.py` | `_simulate_fill()`; `EXPIRED` outcome; `expired` + `fill_rate` in the summary |
| `app/diagnostics/signal_pipeline_diagnostics.py` | Pending fill/expiry stage + counters |

**WPF (7)** — `Models/SignalDto.cs`, `Models/SignalModel.cs`, `Models/TradingControlDtos.cs`, `ViewModels/AutoTradingViewModel.cs`, `ViewModels/LiveSignalsViewModel.cs`, `ViewModels/GoldSignalsViewModel.cs`, `Views/AutoTradingView.xaml`, `Views/LiveSignalsView.xaml`, `Views/GoldSignalsView.xaml`

**Tests (1 new)** — `tests/test_ict_pending_entry.py`, 42 tests.

---

## 2. Subsystem Summary

**Why the previous implementation ignored `entry_plan`:** the code said so explicitly. `entry_plan` was built *after* the decision and its own comment recorded that changing the numeric entry "would alter what `signal_monitor.py` and `backtest/engine.py` resolve trades against — a behavior change outside this phase's audited scope." It was a deliberate deferral, not an oversight.

**How it is used now:** `entry_engine.build()` moved to *before* the SL/TP/RR block — every input it needs was already computed there, so this is a reordering of an existing call, not new detection. Its price is accepted only when `_is_usable_entry()` confirms it sits strictly between stop and target with a positive risk distance; otherwise the live price is used, exactly as before.

**The insight that makes this safe:** a LIMIT order fills *at* its limit price, so `Signal.entry_price` and the real fill price are the same number. Position sizing (`quantity = risk_usd / |entry − stop|`), breakeven at 1R, trailing and backtest RR are therefore correct **by construction** — the over-sizing hazard of the naive version is structurally impossible.

**Two-stage execution:** stage 1 rests the LIMIT entry; stage 2 attaches reduceOnly SL/TP once the fill is observed. They cannot be merged — Binance rejects a reduceOnly order when no position exists. Protection is sized to the **real executed quantity**, so a partial fill is protected at what actually filled.

**Fill authority:** an armed signal is resolved from Binance's own view of the order (`get_order()`), never inferred from candles. Un-armed advisory signals resolve from candle extremes. Invalidation is checked **before** fill, so a candle spanning both the entry zone and the stop counts as *never entered* rather than an instantly-stopped trade — that ordering is directly test-pinned.

**Statistics integrity:** `EXPIRED` is a distinct terminal status meaning *never entered*. It is excluded from `DECIDED_STATUSES`, from win/loss totals, from average realised RR and from daily realised loss, but is visible in History. `get_stats()` no longer computes `closed = status != ACTIVE` — that would have counted pending and expired signals as closed and silently corrupted every win rate in the app.

---

## 3. Regression Results

```
files=39  import_errors=0  collected=669  passed=667  failed=1  error=0  skipped=1
```

- **+42 new tests, all passing.**
- **1 skip:** pre-existing by design (`test_order_flow.py` ATR-vs-real-`ta` comparison self-skips offline).
- **1 failure:** `test_universal_scanner.py::test_triggers_on_primary_timeframe` — pre-existing and unrelated, diagnosed two phases ago: it doesn't mock `get_engine_run_state()` and depends on your real on-disk `data/trading_settings.json`, which currently reads `engine_run_state: "stopped"`. Identical before and after this migration.

Two offline-harness stub gaps surfaced and were fixed in the harness only (`and_`/`or_`/`not_` and column operators like `.is_()`/`.notin_()`); real SQLAlchemy has always provided these. No production code was changed to satisfy a stub.

---

## 4. Build Results

- **Backend:** `python3 -m compileall app tests` — exit 0, zero errors across every file.
- **WPF:** **cannot be compiled here** — no .NET SDK exists in this environment (`dotnet` not on PATH, confirmed again this phase). In its place I verified statically that all 28 new/changed bindings resolve to real C# members across `SignalModel`, `SignalDto` and `TradingStatusDto` — all OK. This remains **compiler-unverified** and must be built on Windows.
- **Docker:** **cannot be verified here** — no Docker daemon in this environment. The startup bootstrap is written to the same defensive, idempotent pattern as the existing column migrations, but see Manual Steps.

---

## 5. Test Results

New coverage in `tests/test_ict_pending_entry.py` (42):

- **Entry guard (9):** valid long/short anchors accepted; entry below stop, above target, exactly on the stop, `None`, `NaN`, non-positive and non-numeric all rejected without raising.
- **RR activation (2):** ICT anchor clears the 2.0 gate where the market entry could not; stop and target provably untouched.
- **Zone bounds (3):** real bounds honoured; missing or inverted bounds fall back to the exact entry — never a guessed width.
- **Pending outcomes (6):** long/short fill, still-waiting, invalidation, and the candle-spanning-both case.
- **Expiry (4):** before/after window, naive-timestamp handling, and no expiry in market mode.
- **Backtest fill (7):** market fills immediately; pending fills on the correct candle index; never-reached and stop-first both return `None`; window matches the live constant; unfilled resolves `EXPIRED` with RR 0.
- **Summary integrity (2):** expired trades move neither win rate nor average RR.
- **Status contract (5)** and **entry-mode switch (4):** including that an invalid mode raises rather than silently blending.

---

## 6. Manual Steps Required

1. **Build the WPF app on Windows** (`dotnet build`) — the only unverified surface.
2. **Start the backend against your real Postgres and check the startup log** for `signalstatus enum value 'PENDING_ENTRY' present.` and `'EXPIRED' present.` The `ALTER TYPE` runs in its own autocommit connection (required: Postgres won't let a value added in a transaction be used by that same transaction) and needs **PostgreSQL 12+**. Both statements are idempotent and safe to re-run.
3. **Re-baseline your backtests.** Results are intentionally not comparable to pre-migration runs — the engine now simulates fills instead of assuming them, so expect fewer trades, a new `fill_rate`, and a truer RR.
4. **Testnet first.** Watch `Armed ICT Entries` on the Auto Trading panel and the diagnostics summary's filled-vs-expired counts to see the real fill rate before considering Mainnet.
5. **To revert atomically:** set `entry_mode` to `market` in `data/trading_settings.json`. Every subsystem reads that one switch.

**Four decisions I made on your behalf** (you approved without answering them): distinct `EXPIRED` status; 12-candle/3-hour expiry; armed pending entries count toward portfolio open risk (committed capital); kept the Postgres enum with a guarded `ALTER TYPE` rather than migrating `status` to `VARCHAR`. Any of the four is straightforward to change — say the word.

---

## 7. Confirmation: No Remaining MARKET-Entry Path Under ICT Pending Mode

Verified by execution, not inspection. The entry model is decided by **one** switch with exactly four call sites:

```
app/services/trading_settings.py   - defines get_entry_mode() / is_ict_pending_entry()
app/strategy/signal_generator.py:586 - gates the ICT anchor
app/scheduler/universal_scanner.py:422 - gates the birth status
app/api/v1/endpoints/trading_control.py:158 - reports it to the UI
```

Execution proof:

```
mode=ict_pending  is_ict_pending=True   birth_status=PENDING_ENTRY  uses_ICT_anchor=True
mode=market       is_ict_pending=False  birth_status=ACTIVE         uses_ICT_anchor=False
```

Under `ict_pending`: every signal is created `PENDING_ENTRY`, and `execute_signal()` routes on `signal.status is PENDING_ENTRY` to `place_limit_entry()` — `place_signal_bracket()` (the MARKET path) is unreachable, because no signal can be `ACTIVE`-and-unexecuted in that mode. The MARKET path survives solely to serve signals created under `market` mode, and `set_entry_mode()` raises on any value outside the two. **A mixed state is not representable.**
