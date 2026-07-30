# ICT Pending Limit Entry — Migration Plan (FOR APPROVAL, NOT IMPLEMENTED)

**Date:** 2026-07-30
**Status:** **PLAN ONLY. No code has been written or modified.**
**Scope of the eventual change:** convert entry from "market fill at the live price" to "ICT pending limit entry at the already-computed `entry_plan` price," consistently across every subsystem.

---

## 0. The Insight That Makes This Safe

The reason the naive version was dangerous is that a MARKET order fills at the live price while the signal would have recorded the OTE price — and `quantity = risk_usd / abs(entry_price - stop_loss)` would then under-state risk distance and over-size the position.

**A LIMIT order fills *at* its limit price.** So once the entry becomes a resting limit order at the OTE level, `entry_price` and the actual fill price are the same number, and every downstream calculation — position sizing, breakeven at 1R, trailing, backtest RR — becomes correct *by construction* rather than by coincidence. That is why Option 2 is the safe path and Option 3 was not.

The cost is that a limit order **may never fill**, which is exactly what the new state machine has to manage.

---

## 1. Target Lifecycle

```
   scanner generates signal
            |
            v
   [PENDING_ENTRY]  entry_price = OTE / OB / FVG zone price
            |         SL + TP already computed (methodology unchanged)
            |
      +-----+---------------------------+--------------------------+
      |                                 |                          |
 price touches                   price hits SL              N candles pass
 entry zone                       before entry              with no touch
      |                                 |                          |
      v                                 v                          v
  [ACTIVE]                        [EXPIRED]                  [EXPIRED]
  filled_at, actual_fill_price    never entered              never entered
      |
      +--> existing behaviour from here on, unchanged:
           TP_HIT / STOPPED / CANCELLED (structure failure)
```

Auto Trading (exchange side) mirrors it in two stages, because reduceOnly protective orders cannot exist before a position does:

```
Execute clicked on a PENDING_ENTRY signal
   -> stage 1: place LIMIT entry order, store entry_order_id       (no SL/TP yet)
   -> monitor detects the LIMIT filled
   -> stage 2: place reduceOnly STOP_MARKET (SL) + reduceOnly LIMIT (TP)
   -> expiry / kill switch: cancel the resting LIMIT, mark EXPIRED
```

---

## 2. Every File That Will Change

### 2.1 Backend — core state machine

| # | File | Change |
|---|---|---|
| 1 | `app/models/signal.py` | Add `PENDING_ENTRY` and `EXPIRED` to `SignalStatus`. Add columns: `entry_type`, `entry_zone_top`, `entry_zone_bottom`, `filled_at`, `actual_fill_price`, `entry_order_id`, `entry_expires_at`. |
| 2 | `app/main.py` | Startup schema bootstrap. **This repo has no Alembic migrations** (alembic 1.14.1 is in `requirements.txt` but unused; `main.py` lines 143–170 evolve the schema with idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). New columns follow that established pattern; the new **enum values additionally need `ALTER TYPE ... ADD VALUE IF NOT EXISTS`**, which is a different and riskier operation — see Risk R8. |
| 3 | `app/strategy/signal_generator.py` | **The core change.** Move the existing `self.entry_engine.build(...)` call from after the decision (line ~751) to before the SL/TP/RR block (line ~453) — every input it needs (`ote_zone`, `ob`, `relevant_fvgs`, `zone`, `current_price`) is already computed by then. Use `entry_plan.entry_price` as the entry for SL/TP/RR, guarded (see §4). Emit `entry_type` / zone bounds in the signal dict. **SL construction, TP selection, ATR multiples, gates, AI scoring all untouched.** |
| 4 | `app/scheduler/universal_scanner.py` | `save_signal()`: persist `status=PENDING_ENTRY` + the new fields. Extend the duplicate-signal guard (currently `status == ACTIVE` only, line ~398) to also count `PENDING_ENTRY`, or a symbol could hold one pending *and* one active simultaneously. |
| 5 | `app/scheduler/signal_monitor.py` | Largest behavioural addition. New pending pass: fill detection (price traded into the zone → `ACTIVE`, set `filled_at`/`actual_fill_price`), pre-entry invalidation (SL touched before entry → `EXPIRED`), expiry (`entry_expires_at` passed → `EXPIRED` + cancel any resting LIMIT). Trade management must **skip** `PENDING_ENTRY` signals entirely. For executed pending signals, place SL/TP after fill (stage 2). |

### 2.2 Backend — execution

| # | File | Change |
|---|---|---|
| 6 | `app/api/v1/endpoints/trading.py` | `execute_signal()` currently requires `status == ACTIVE` (line 70). Must accept `PENDING_ENTRY`, place a LIMIT entry only, and record `entry_order_id`. The Auto-Trading-OFF gate, RiskEngine gate, and double-execution guard stay exactly as they are. |
| 7 | `app/services/binance_trading_service.py` | Add `place_limit_entry()` (LIMIT, not reduceOnly) and `place_protective_orders()` (the SL+TP half of today's bracket). Reuse the existing `_client_order_id()` idempotency helpers and `_round_price`/`_round_to_step` filters unchanged. `place_signal_bracket()` stays for the market path. |
| 8 | `app/services/trading_control_service.py` | Cancel All Orders / Kill Switch must also cancel resting LIMIT **entry** orders and mark those signals `EXPIRED` — otherwise the kill switch leaves live entry orders on the exchange. |
| 9 | `app/services/execution_risk.py` | **No logic change.** It already sizes from `signal.entry_price`; that value simply becomes correct. Verified, not assumed. |

### 2.3 Backend — read paths that will silently break if missed

| # | File | Why it must change |
|---|---|---|
| 10 | `app/services/signal_service.py` | `get_stats()` computes `closed = status != ACTIVE` (line 463) — `PENDING_ENTRY` would be counted as **closed**, inflating the win-rate denominator and corrupting every stat on the Dashboard and Statistics screens. Also `get_signal_counts_for_control_panel()`: its existing `pending_signals` means "ACTIVE but not executed" and now **collides semantically** with the new pending-entry concept — needs disambiguation. `build_portfolio_risk_context()` needs a decision on whether a resting limit counts as committed risk. |
| 11 | `app/repositories/signal_repository.py` | `get_active_signals()` (ACTIVE only) drives portfolio risk aggregation; `get_closed_signals_since()` excludes ACTIVE and CANCELLED and would wrongly treat `EXPIRED` as a realised loss in daily-loss tracking. |
| 12 | `app/repositories/history_repository.py` | `HISTORY_STATUSES` must include `EXPIRED` so expired signals are visible in History. `WIN_STATUSES`/`COUNTED_STATUSES` stay unchanged (TP_HIT / TP_HIT+STOPPED) so win-rate maths is untouched. |
| 13 | `app/ai/calibration.py` | `WIN_STATUSES = [TP_HIT]`, `LOSS_STATUSES = [STOPPED]` — **verified: no change needed.** Expired signals must never feed calibration, and with these lists they can't. |
| 14 | `app/schemas/signal.py` | Expose the new fields; allow the new statuses in list filters. |
| 15 | `app/diagnostics/signal_pipeline_diagnostics.py` | Add pending-created / filled / expired counters so the 5-minute summary keeps telling the truth. |

### 2.4 Backtest

| # | File | Change |
|---|---|---|
| 16 | `app/backtest/engine.py` | `_resolve_trade()` (lines 196–228) currently assumes an **instant fill at `entry`** and immediately walks SL/TP. It must first simulate the fill: walk forward and find whether price touches `entry` before `stop_loss`, within an expiry window; only then resolve TP/SL from the fill candle. **Without this the backtest reports fills it never got and inflates results.** |

### 2.5 WPF

| # | File | Change |
|---|---|---|
| 17 | `Models/SignalDto.cs` | New fields: `entry_type`, `entry_zone_top/bottom`, `filled_at`, `actual_fill_price`, `entry_order_id`. |
| 18 | `Models/SignalModel.cs` | Status/entry display; the Execute button's label and enablement for a pending signal. |
| 19 | `ViewModels/LiveSignalsViewModel.cs`, `GoldSignalsViewModel.cs`, `AutoTradingViewModel.cs` | Render the new statuses and the revised counts. |
| 20 | `Views/LiveSignalsView.xaml`, `GoldSignalsView.xaml`, `AutoTradingView.xaml` | Status chips for `PENDING_ENTRY` / `EXPIRED`. |
| 21 | `Models/TradingControlDtos.cs` | Any new control-panel counts. |

### 2.6 Tests

| # | File | Change |
|---|---|---|
| 22 | `tests/test_signal_generator_entry.py` *(new)* | Entry activation, the validity guard, and market fallback. |
| 23 | `tests/test_signal_monitor.py` | Fill detection, pre-entry invalidation, expiry, and that management skips pending. |
| 24 | `tests/test_backtest_engine.py` | Fill simulation and expired-trade accounting. |
| 25 | `tests/test_trading_control_service.py` | Kill switch cancels resting entry orders. |
| 26 | `tests/test_execution_risk.py` | Sizing correctness at the limit price. |

**Totals: 21 production files (16 backend, 5 WPF) + 5 test files.**

---

## 3. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | **Signals never fill.** ICT retracement entries legitimately get missed; signal count could drop sharply. | High (expected, not a bug) | Explicit `entry_expires_at`; diagnostics counters for fill-rate vs expiry-rate; validate on Testnet before Mainnet. |
| R2 | **Partial fills.** A LIMIT can partially fill, so position size ≠ intended and SL/TP quantities mismatch. | High (money) | Read the real `executedQty` before placing protective orders; size SL/TP to what actually filled; log any partial loudly. |
| R3 | **Orphaned exchange orders after restart.** A resting LIMIT survives an app restart; the DB may disagree. | High (money) | Startup reconciliation: for every `PENDING_ENTRY` with an `entry_order_id`, query Binance and re-sync (filled → ACTIVE, gone → EXPIRED). |
| R4 | **Unprotected window.** Between fill and SL/TP placement the position has no stop — a longer window than today's market bracket. | High (money) | Place protective orders in the very next monitor pass; on failure, reuse the existing loud-warning path and surface it in the UI. Consider tightening `POLL_INTERVAL_SECONDS` for pending-filled signals only. |
| R5 | **Stats corruption.** `closed = status != ACTIVE` counts pending/expired as closed → wrong win rate everywhere. | High (silent) | Item 10; explicit regression tests on `get_stats()`. |
| R6 | **Semantic collision** on the existing `pending_signals` counter. | Medium (confusing) | Rename/disambiguate in item 10 + WPF item 21. |
| R7 | **Backtest results change.** Fewer trades, better RR, and prior reports become non-comparable. | Medium | Publish a fresh baseline and state plainly that pre-migration reports are not comparable. |
| R8 | **Postgres enum migration.** `create_all()` never alters an existing type; adding an enum value needs `ALTER TYPE ... ADD VALUE`, which historically cannot be used in the same transaction that then references it. A bad bootstrap can fail startup. | High (availability) | Do the `ALTER TYPE` in its own committed transaction ahead of `create_all()`, idempotently, with the same defensive try/except the existing bootstraps use. Test against a real Postgres before deploying. **Alternative worth your consideration: store status as `VARCHAR` and drop the DB-level enum**, which removes this class of problem permanently. |
| R9 | **Thin Testnet liquidity** makes fills unrepresentative of Mainnet. | Medium | Treat Testnet as a correctness test, not a fill-rate test. |
| R10 | **Mixed behaviour during rollout** — the exact thing you told me to avoid. | High | The feature flag in §5: every subsystem reads one switch, so the platform is either wholly market-entry or wholly pending-entry, never half. |

---

## 4. The Entry Validity Guard

`EntryPlan.entry_price` is the **midpoint of the zone** (`(zone_top + zone_bottom) / 2` for OTE/OB/FVG/S&D). It is not guaranteed to sit between SL and TP, so it must be validated before use. Fallback to today's market price whenever it fails — exactly the "only then" fallback you specified:

```
accept entry_plan.entry_price only if ALL hold:
    LONG :  stop_loss < entry < take_profit
    SHORT:  take_profit < entry < stop_loss
    abs(entry - stop_loss) > 0
    resulting RR is finite
otherwise -> entry = current_price   (existing behaviour, unchanged)
```

Note the ordering consequence: RR is computed *after* the entry is chosen, so the entry must be resolved before the SL/TP/RR block. That is the one structural reordering in `signal_generator.py`; no formula inside it changes.

---

## 5. Rollout Strategy — Single Feature Flag

To guarantee no mixed behaviour, add one setting to `app/services/trading_settings.py` (the same pattern as the existing `auto_trading_enabled` / `engine_run_state` switches):

```
entry_mode = "market" | "ict_pending"      # default: "market"
```

Every one of the 21 files reads that single flag. Default `"market"` means **merging the code changes nothing** until you explicitly switch it — the same backward-compatibility discipline used for the Auto Trading control plane. Existing in-flight ACTIVE signals always continue under market semantics regardless of the flag.

**Suggested sequence, with a checkpoint after each:**

1. Schema + enum migration, verified against real Postgres. Nothing reads the new fields yet.
2. `signal_generator.py` entry activation behind the flag + tests. Verify with the RR harness that RR distribution moves above 2.0.
3. Monitor state machine (fill / invalidate / expire) + tests. Still no execution change.
4. Backtest fill simulation + fresh baseline.
5. Execution two-stage LIMIT + reconciliation + kill-switch coverage.
6. Read paths, diagnostics, stats.
7. WPF.
8. Full regression, then enable the flag on **Testnet only** and observe fill-rate before any Mainnet consideration.

---

## 6. What Does NOT Change

Stop-loss methodology (the `min()`/`max()` structure-vs-ATR construction), take-profit selection (nearest liquidity, ATR fallback), all ICT detection (Market Structure, BOS/CHoCH, Order Blocks, FVG, Liquidity, OTE, Institutional Bias), AI scoring and its weights, confidence thresholds, the Risk Engine's rules, position-sizing *formula*, Trade Management rules, and every signal filter/gate. The only behavioural change is **which price the trade is entered at**, plus the state machine required to make that price real rather than notional.

---

## 7. Open Decisions I Need From You

1. **Expiry window** — how long may a pending entry wait before expiring? (Suggestion: a profile-driven candle count, e.g. 8–12 × 15m ≈ 2–3 hours, so it dies with the setup that produced it.)
2. **Do resting limit entries count toward portfolio open risk?** (Suggestion: yes once the limit is actually placed on the exchange — it is committed capital.)
3. **Enum vs VARCHAR for `status`** (Risk R8) — keep the Postgres enum and do `ALTER TYPE`, or migrate to `VARCHAR` and remove the whole failure mode?
4. **`EXPIRED` as a new status, or reuse `CANCELLED`?** (Suggestion: a distinct `EXPIRED` — "never entered" and "entered then closed by structure failure" are very different facts for performance analysis.)

Answer those four and I'll implement in the sequence above, one checkpoint at a time.
