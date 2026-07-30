# Initial Stop Loss Separation + Exposure Fix

**Date:** 2026-07-30
**Scope:** Issue 1 fixed in full. Issue 2 — two real bugs found and fixed, one structural component identified and **deliberately not changed** because fixing it would require altering a risk limit, which you forbade.

---

## 1. Files Changed

| File | Change |
|---|---|
| `app/models/signal.py` | New `initial_stop_loss` column (`Float`, `nullable=False`), documented as the frozen risk reference |
| `alembic.ini` | **New** — first Alembic setup in this project |
| `alembic/env.py` | **New** — async-aware, reads the URL from `app.core.config` so it can never target a different DB than the app |
| `alembic/script.py.mako` | **New** — migration template |
| `alembic/versions/20260730_01_add_initial_stop_loss.py` | **New migration** |
| `app/scheduler/universal_scanner.py` | `save_signal()` sets `initial_stop_loss = stop_loss` at creation |
| `app/services/execution_risk.py` | Gate reads `initial_stop_loss`; `committed_only=True`; full input instrumentation on both approve and reject |
| `app/services/signal_service.py` | All 4 sizing sites read `initial_stop_loss`; `committed_only` threaded through; realized-loss now measured against the stop actually hit; field exposed on responses |
| `app/repositories/signal_repository.py` | `get_active_signals(committed_only=False)` |
| `app/schemas/signal.py`, `app/schemas/history.py` | `initial_stop_loss` exposed |
| `app/main.py` | Guarded bootstrap so an un-migrated DB still boots (see Remaining Risks) |
| `tests/test_initial_stop_loss_risk.py` | **New**, 10 tests |
| `tests/test_execution_risk.py`, `tests/test_signal_service_risk.py` | Fixtures updated for the new required field |

**Deliberately NOT changed:** `app/strategy/trade_management_engine.py` and `app/scheduler/signal_monitor.py` still write **only** `signal.stop_loss`. Breakeven, trailing and exchange stop-sync are untouched. `app/api/v1/endpoints/trading.py:176` and `signal_monitor.py:303` still pass the **live** `stop_loss` when placing real protective orders — correct, because the exchange must protect at the current stop, not the original.

---

## 2. Migration Created

`alembic/versions/20260730_01_add_initial_stop_loss.py` — three deliberate steps:

1. `ADD COLUMN initial_stop_loss DOUBLE PRECISION NULL`
2. `UPDATE signals SET initial_stop_loss = stop_loss WHERE initial_stop_loss IS NULL`
3. `ALTER COLUMN ... SET NOT NULL`

This is **not** a nullable shortcut — the column ends up `NOT NULL` exactly as the model declares. It cannot be added as `NOT NULL` in one statement against a table that already holds rows; add/backfill/enforce is the standard sequence.

**Honest note on the backfill:** for a pre-existing signal whose stop has *already been trailed*, the true original is **unrecoverable** — this project has never recorded stop history. Using the current `stop_loss` is the closest correct value available and is never fabricated. New signals set both fields at creation, so the approximation applies only to rows that exist today.

Run with `alembic upgrade head`.

---

## 3. Root Cause of Issue 1

Your diagnosis was correct, and sharper than mine — I had previously guessed at an ICT-anchor rounding collision. The real mechanism:

`app/strategy/trade_management_engine.py:103` — at 1R the engine emits `ManagementAction(MOVE_TO_BREAKEVEN, new_stop_loss=entry_price)`. It sets the stop **exactly equal to entry**.

`signal_monitor.py` persists that to `signal.stop_loss`. Every later risk read then computed `abs(entry_price − stop_loss)` = **0**, so `calculate_position_size()` returned `None` at `position_sizing.py:42`, and `RiskEngine` reported *"unknown account balance, or a degenerate entry/stop pair."*

The message made it nearly undiagnosable: the balance clause is listed first, but the balance was perfectly well known. Proof — `risk_engine.py:118` computes `exposure_percent` as `0.0` whenever the balance is falsy, so the very fact that a **221.5%** exposure was reported *proves* the balance was known.

This is a design bug, exactly as you said: one field was serving two incompatible purposes — the live protective level and the fixed risk reference. A position's size is fixed the moment it is taken; it cannot change because the stop later moved.

**Verified fixed** — the identical scenario (stop trailed to breakeven) now sizes correctly instead of erroring.

---

## 4. Root Cause of Issue 2

Instrumented, not guessed. Same portfolio, computed both ways:

```
BEFORE (sized from the LIVE trailed stop)
symbol      entry       stop      dist   dist%          qty    notional   expo%
BTCUSDT   98491.00   98491.00      0.00  0.000%         None     DROPPED     --   <- sizing None
ETHUSDT    3524.93    3508.00     16.93  0.480%     2.772593     9773.20  208.2%
BNBUSDT     575.18     566.00      9.18  1.596%     5.113290     2941.06   62.7%
                                                        TOTAL    12714.26  270.9%

AFTER (sized from the frozen initial stop)
BTCUSDT   98491.00   97185.00   1306.00  1.326%     0.035942     3539.94   75.4%
ETHUSDT    3524.93    3466.00     58.93  1.672%     0.796538     2807.74   59.8%
BNBUSDT     575.18     566.00      9.18  1.596%     5.113290     2941.06   62.7%
                                                        TOTAL     9288.74  197.9%
```

**Cause 2a — trailed stops inflate notional.** `notional = risk_usd × (entry / stop_distance)`. As the stop trails toward entry the distance shrinks and the *measured* notional balloons, though nothing about the real position changed. ETHUSDT alone read 208% instead of 60%. Fixed by the same `initial_stop_loss` change.

**Cause 2b — un-executed signals consumed the real exposure budget.** `get_active_signals()` required `executed=True` for `PENDING_ENTRY` but **not** for `ACTIVE`. So a signal the scanner produced and the user never acted on contributed full notional to the gate that blocks real trades — while an un-armed pending signal correctly did not. That asymmetry was mine, introduced in the pending-entry migration. Fixed via `committed_only=True` on the execution path; the advisory display keeps its "if every signal were taken" meaning.

**Ruled out by inspection** (each was checked, none applies): existing positions are not double-counted (the tally reads DB signals only, and the executing signal is excluded via `exclude_signal_id`); closed positions are excluded by status; there is no hedge-mode duplication because Binance positions are never read here; mark price is not involved; quantity and entry price are correct; wallet balance is correct and identical to the UI's (`futures.wallet_balance` via `get_full_snapshot()` for both).

**Cause 2c — structural, NOT fixed, needs your decision.** After both fixes, a *single* BTCUSDT trade with zero other positions still reports **75.4%** exposure:

```
exposure% = risk% / stop_distance_fraction = 1% / 1.326% = 75.4%
```

`max_exposure_percent = 50` caps *total notional at 50% of wallet balance* — i.e. it permits at most **0.5× leverage**. An ICT trade at 1% risk with a sub-2% structural stop inherently needs more notional than that. To satisfy 50% at 1% risk, the stop would have to sit **≥2% away**; typical 15m ICT stops are 0.3–1.5%. The limit and the strategy are mathematically incompatible — the same class of finding as the earlier RR/TP mismatch.

I did not touch it: you explicitly said do not change risk limits and do not disable safety checks. This is a policy decision (raise the cap, lower `risk_percent`, or cap concurrent positions), not a bug I should silently resolve.

---

## 5. Why the Fixes Are Correct

- **Sizing is now time-invariant.** The quantity computed for a trade is identical on day 1 and after ten trailing adjustments, because it measures from a value nothing mutates. That is the correct semantics: size is fixed at entry.
- **No safety check was weakened.** All five limits are unchanged and test-pinned. `committed_only` makes exposure count *fewer, real* positions — a genuine position still blocks, verified by test.
- **Live stop management is provably untouched.** Trade Management still writes only `stop_loss`; real protective orders still use the live stop. Breakeven and trailing behave exactly as before.
- **Realized loss got more accurate as a side effect.** A trade trailed to breakeven then stopped now records ~0 loss rather than a full R, so the daily-loss limit is no longer overstated.
- **Regression: 677/679 passing** (+10 new). The single failure is the same pre-existing environment artifact (`test_universal_scanner::test_triggers_on_primary_timeframe` reads your real on-disk `engine_run_state: "stopped"`), unchanged by this work. Full `compileall` clean.

---

## 6. Remaining Risks

1. **Exposure still blocks execution** (Cause 2c). Nothing will execute until you decide on the leverage policy. This is the live blocker.
2. **Backfilled `initial_stop_loss` is approximate for already-trailed open trades** — unrecoverable by construction. New signals are exact. Consider closing or re-issuing currently-open positions if their sizing matters.
3. **Two schema mechanisms now coexist.** `main.py` retains guarded `ALTER TABLE` bootstraps *and* Alembic now exists. I kept a guarded bootstrap for this column so an un-migrated DB still boots rather than failing every query. It is idempotent and equivalent to the migration, so running `alembic upgrade head` afterwards is a no-op — but consolidating fully onto Alembic (`alembic stamp head`, then delete the bootstraps) is worth a follow-up.
4. **Alembic itself is unrun here** — no Postgres in this environment. The migration is written and syntactically valid but has not been executed against a real database.
5. **WPF still unbuilt** — no .NET SDK here. `initial_stop_loss` is exposed by the API but not yet surfaced in any WPF DTO/view; the app will ignore the extra JSON field harmlessly until you want it displayed.
