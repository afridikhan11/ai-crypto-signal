# Entry Validation & Risk/Reward — Root Cause Analysis

**Date:** 2026-07-30
**Scope:** Diagnostic only. **No code was modified.** No ICT logic, signal generation, AI scoring, confidence threshold, Risk Engine, Entry Validation rule, RR minimum, Trade Management, or position sizing was changed. No filter loosened, no calculation altered, no gate bypassed. All findings below were produced by running the **real, unmodified `SignalGenerator` pipeline** in a read-only harness.

---

## 1. Files Inspected

| File | What was verified |
|---|---|
| `app/strategy/signal_generator.py` (lines 444–500, 664–698) | Entry/SL/TP construction, RR formula, gate evaluation |
| `app/strategy/entry_validation_engine.py` (full) | All six validation checks and their failure conditions |
| `app/strategy/exit_engine.py` (lines 1–90) | Whether the managed TP differs from the gated TP |
| `app/smc/market_structure_engine.py` (lines 151–170, 386–410) | `swing_highs`/`swing_lows`, `protected_high`/`protected_low` semantics |
| `app/smc/order_flow.py` (via `OrderFlowMetrics.get_latest`) | ATR validity before use |
| `app/ai/calibration_profiles.py` | The four profile parameters that drive SL/TP/RR |
| `app/assets/asset_profile.py` | `tick_size` availability for rounding checks |

---

## 2. Formula Used for RR

Verbatim from `signal_generator.py` (lines 490–492):

```python
risk_distance   = abs(current_price - stop_loss)
reward_distance = abs(take_profit - current_price)
rr = round(reward_distance / risk_distance, 2) if risk_distance > 0 else 0
```

**This formula is mathematically correct.** Verified by independent recomputation on every test case — the pipeline's `rr` matched a separately-calculated `reward/risk` to within rounding on 100% of runs. Division-by-zero is guarded.

The inputs are built as (lines 458–488):

```python
# LONG
swing_low  = min(sw.price for sw in snapshot.swing_lows[-3:])
stop_loss  = min([swing_low, current_price - 1.5*atr, (ob_low - 0.1*atr)])   # FARTHEST
take_profit = <nearest BUYSIDE liquidity beyond current_price + 1.5*atr>      # NEAREST
              or current_price + 3.0*atr                                       # fallback

# SHORT — mirrored with max()/max() and SELLSIDE
```

---

## 3. Formula Used for Entry Validation

Six independent checks (`entry_validation_engine.py`); the overall result is `all(passed)`. Five of the six degrade honestly — they **pass** when data is absent. The `risk` check is:

```python
risk_fail = risk_reward is not None and risk_reward < self.min_risk_reward
```

`min_risk_reward` is injected in `SignalGenerator.__init__` as `self.profile.min_risk_reward` — **the same 2.0 used by the standalone `min_risk_reward` gate, applied to the same `rr` value.**

**Therefore `entry_validation` is not a second, independent failure.** Across every run, its *only* failing sub-check was `risk`:

```
[entry_validation] Entry validation failed on risk. risk: Risk:Reward 0.26 below minimum 2.0.
```

The two rejection reasons you're seeing in the live logs are **one root cause counted twice.** `institutional_bias`, `htf_alignment`, `confluence`, `session_killzone` and `invalidation` all passed.

---

## 4. Example Calculations Using Real Pipeline Values

Real output of the unmodified pipeline (synthetic OHLCV — no live Binance data is reachable from this environment — but every number below is computed by the production code, not by hand):

**BTCUSDT — reproduces your reported RR range exactly**

| # | Field | Value |
|---|---|---|
| 1 | Symbol | BTCUSDT |
| 2 | Direction | SHORT (latest break: BOS bearish) |
| 3 | Entry Price | 98,490.80 |
| 4 | Stop Loss | 99,789.92 |
| 5 | Take Profit | 98,152.85 |
| 6 | Risk Distance | 1,299.12 → **6.62 × ATR** |
| 7 | Reward Distance | 337.94 → **1.72 × ATR** |
| 8 | Calculated RR | **0.26** (independent recompute: 337.94/1299.12 = 0.26 ✓ **MATCHES**) |
| 9 | Minimum Required RR | 2.0 |
| 10 | Entry Validation Result | **FAILED** |
| 11 | Exact reason | `risk` sub-check only — "Risk:Reward 0.26 below minimum 2.0" |
| 12 | Which calculation produced it | **Stop-loss selection**: `max()` chose the swing high at 99,789.92 (6.62 ATR away) over the ATR stop at 98,687.16 (1.5 ATR away) |

ATR = 196.36. Stop candidates were `swing_high(max of last 3) = 99,789.92` vs `atr_stop = 98,687.16`; `max()` picked the swing — **4.4× farther**. Meanwhile TP took the *nearest* sellside level at 1.72 ATR.

**Distribution across 18 independently generated market regimes** (trending up and down, swing-leg length 8–24 candles, amplitude 0.6%–3.0%):

```
RR: min=0.18  median=0.33  max=0.95
RR >= 2.0 (the gate):                    0 / 18   (0%)
risk distance:   median=7.49 ATR   max=13.23 ATR
reward distance: median=3.00 ATR   max=4.47 ATR
stop wider than its 1.5 ATR floor:      18 / 18   (100%)
min_risk_reward gate hit:                18 / 18
entry_validation gate hit:               18 / 18
Tradeable signals:                        0
```

The stop anchors to a swing point a **median of 70 candles back (17.5 hours of 15m data)**, max 115.

**Counterfactual, same series, same stop:** selecting the *furthest* qualifying liquidity target instead of the nearest yields **RR = 2.98 — which passes.** Anchoring the stop to `protected_high` instead only moves RR 0.26 → 0.32.

---

## 5. Root Cause

**Every named bug class was checked and none is present.** Entry, stop, and target are on the correct sides in 12/12 setups (no entry-above-TP, no entry-below-SL, no LONG/SHORT inversion — bullish break → LONG with stop below, bearish → SHORT with stop above). ATR is validated non-NaN and > 0 before use, 0 invalid values reached the calculation. Entry price is the **live 1m close**, deliberately fresher than the 15m structure close — not stale. Rounding is `round(..., 8)` on prices and `round(rr, 2)` computed from unrounded floats (error bound ~1e-8 on a 1e5 price — cannot explain 0.26 vs 2.0). `tick_size` is `None` for crypto by documented design, so no tick-rounding is applied or skewing anything. `ExitEngine` also selects the *nearest* target, so the gated RR is consistent with the plan the trade would be managed to — **"RR calculated before final TP adjustment" is not occurring.**

**The root cause is a structural incompatibility between three individually-defensible rules:**

1. **The stop always takes the farthest candidate.** `min()` for LONG / `max()` for SHORT over `{most extreme of the last 3 external swings, 1.5×ATR, order-block boundary}`. This guarantees the stop is never inverted and never closer than 1.5 ATR — genuinely good safety — but in practice the distant swing wins **100% of the time**, producing a median risk of **7.5 ATR**.
2. **The target always takes the nearest candidate.** The nearest liquidity level beyond `min_target_distance_atr` (1.5 ATR), else a 3.0 ATR fallback — producing a median reward of **3.0 ATR**, max 4.5 ATR.
3. **The gate demands `reward ≥ 2.0 × risk`.** With risk at 7.5 ATR that requires a reward of **≥ 15 ATR** — a distance rule 2 structurally never reaches, because it stops at the *first* qualifying level.

These rules pull in opposite directions, and the stop rule dominates. There is also a **provable parameter-level inconsistency**, independent of any market data:

```
risk is ALWAYS >= stop_loss_atr_multiple            = 1.5 ATR   (min()/max() guarantees it)
so passing min_risk_reward = 2.0 REQUIRES reward   >= 3.0 ATR
but TP selection admits any target beyond
    min_target_distance_atr                         = 1.5 ATR
```

Any nearest liquidity target in **[1.5, 3.0) ATR cannot satisfy the RR gate** — yet TP selection *prefers* exactly that target over the 3.0 ATR fallback that would have met the gate precisely. **Finding a real liquidity target can therefore make RR worse than finding none at all.**

**On the mandated conclusion:** the calculations *are* mathematically correct — the RR formula, the distances, the sides, and the arithmetic all verify. But I cannot honestly assert the second half of "the system is behaving correctly; current market conditions simply do not produce setups satisfying the configured institutional requirements," because the evidence contradicts it: **0 of 18 deliberately varied market regimes produced a passing RR, and the parameter proof above is pure arithmetic with no market input at all.** A genuinely market-dependent gate would pass intermittently. This is a configuration/construction issue that market conditions cannot fix — reporting it as "just the market" would be inaccurate.

---

## 6. Recommended Fix

**Nothing was changed** — per your instruction, no code is modified unless an arithmetic bug is proven, and none is. These are proposals for your approval; option 2 is the highest-leverage and smallest.

1. **Make TP selection RR-aware** *(recommended — smallest change, largest effect)*. Instead of the nearest level beyond a fixed 1.5 ATR, select the nearest level that is at least `min_risk_reward × risk_distance` away, keeping the 3.0 ATR fallback. This aligns the target filter with the gate it must satisfy rather than fighting it. Measured effect on the BTCUSDT case: **RR 0.26 → 2.98 (passes).** Requires computing the stop before the target (already the case) and no threshold change.
2. **Anchor the stop to `protected_high`/`protected_low`.** These are already computed by `MarketStructureEngine` and already consumed by the Evidence Engine, but *not* used for stop placement — the code instead takes the most extreme of the last three swings. `protected_*` is the ICT-correct "standing reference level the current bias depends on." Effect alone is modest (0.26 → 0.32), so this is a correctness/methodology improvement rather than a fix on its own.
3. **Use the OTE / order-block retracement as the entry.** `signal_generator.py` already builds an `entry_plan` with `entry_type` (e.g. `"ote"`) and then explicitly discards it for the numerics — a limitation the code itself discloses at lines 739–750. Entering on the retracement instead of at market is the textbook ICT answer to poor RR, since it shrinks risk without moving the target. This is the most faithful fix but the largest: it changes what `signal_monitor.py` and `backtest/engine.py` resolve trades against, so it warrants its own phase with backtest validation.

A note on what I'd advise *against*: lowering `min_risk_reward` or raising `min_target_distance_atr` would make the numbers agree, but both are threshold changes you've explicitly ruled out, and they'd mask the construction issue rather than resolve it.

**Suggested next step:** approve option 1 alone, then re-run this same diagnostic harness to confirm the RR distribution shifts above 2.0 before touching anything else.
