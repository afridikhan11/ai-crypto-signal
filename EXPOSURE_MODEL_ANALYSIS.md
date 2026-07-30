# Exposure Model Analysis — Spot Notional vs Futures Margin

**Date:** 2026-07-30
**Question:** Is the 50% exposure limit mathematically incompatible with ICT trading, or does the exposure calculation model *spot* exposure instead of *Binance Futures margin* exposure?

**Answer: the second.** The calculation is internally consistent with its own documented intent, but that intent is a gross-notional (spot-style) measure. It is not a margin model, and I could not find evidence that the 50% value was ever a considered policy decision. **No code was changed.**

---

## 1. Every Variable, Traced to Its Line

Real numbers, BTCUSDT, wallet 4694 USDT, risk 1%:

| Variable | Value | Source |
|---|---|---|
| `wallet_balance` | 4,694.00 USDT | `signal_service._get_account_balance()` → `futures.wallet_balance` (`totalWalletBalance`) |
| `risk_percent` | 1.00 % | `trading_settings.get_risk_percent()` |
| `stop_distance` | 1,306.00 | `abs(entry − initial_stop)` |
| `stop_distance_percent` | 1.326 % | derived |
| `risk_usd` | 46.94 USDT | `position_sizing.py:45` — `balance × risk%/100` |
| `quantity` | 0.035942 | `position_sizing.py:46` — `risk_usd / stop_distance` |
| `position_notional` | 3,539.94 USDT | `position_sizing.py:47` — `quantity × entry_price` |
| `effective_exposure` | **75.41 %** | `risk_engine.py:118` — `notional / wallet_balance × 100` |
| `max_exposure_percent` | 50.00 % | `RiskLimits` default |
| `exchange_leverage` | — | **never referenced in RiskEngine** |
| `initial_margin` | — | **never referenced in RiskEngine** |
| `maintenance_margin` | — | **never referenced in RiskEngine** |
| `margin_ratio` | — | **never referenced in RiskEngine** |

The bottom four are not omissions of *available* data. `grep` proves the platform **already parses all of them** from the same Binance response:

- `binance_account_service.py:122` — `leverage: int`
- `binance_account_service.py:449` — `positionInitialMargin` / `isolatedWallet`
- `binance_account_service.py:481-482` — `totalMarginBalance`, `totalMaintenanceMargin`
- `dashboard.py:38-40` — already computes `margin_ratio_pct = maint / margin_balance × 100`

`grep -n "margin\|leverage" app/risk/risk_engine.py` returns **nothing**. The correct futures metric is computed for the Dashboard and ignored by the engine that gates execution.

Also proven: this platform **never sets** leverage (`binance_trading_service.py:42` — *"Never changes leverage or margin type"*), so whatever the account is configured at is what applies.

---

## 2. RiskEngine vs the Binance Margin Model

Same position, 3,539.94 USDT notional:

| Leverage | Initial margin | Margin / wallet | Maint. margin (0.4%) | Margin ratio |
|---|---|---|---|---|
| 1× | 3,539.94 | 75.41 % | 14.16 | 0.30 % |
| 3× | 1,179.98 | 25.14 % | 14.16 | 0.30 % |
| 5× | 707.99 | 15.08 % | 14.16 | 0.30 % |
| 10× | 353.99 | 7.54 % | 14.16 | 0.30 % |
| 20× | 177.00 | 3.77 % | 14.16 | 0.30 % |

Binance liquidates when `margin_balance < maintenance_margin`. Here maintenance margin is **14.16 USDT — 0.30% of the wallet**. The account sits roughly **332×** above its liquidation requirement.

So RiskEngine says *"exposure 75.4%, rejected"* for a position that, at the platform's likely default leverage, ties up **3.77%** of the wallet and is nowhere near liquidation. `notional / wallet_balance` is only equal to margin usage at **1× leverage** — i.e. the formula is exactly right for **spot**, and wrong by a factor of `leverage` for futures.

**The 75.4% figure is not a margin measurement. It is literally leverage × 100.** `notional / wallet = 0.754` means the account is running 0.754× leverage. The 50% cap therefore permits at most **0.5× leverage** — it forbids using any leverage at all on a leveraged-futures product.

---

## 3. Is the Gross-Notional Cap a Deliberate Policy?

**The formula is deliberate; the limit value does not appear to be.**

*For deliberate:* `risk_engine.py`'s module docstring states plainly — *"Portfolio Risk / Maximum Exposure → NEW: total notional exposure (existing open positions + the position being sized) as a percentage of account balance."* The code does exactly what it says. This is not a coding error.

*Against it being a considered policy:*

1. **No rationale is recorded.** `RiskLimits` is a bare dataclass with five bare defaults and zero explanatory comments. That is conspicuous in *this* codebase, where comparable constants all carry detailed justifications (`PENDING_ENTRY_EXPIRY_CANDLES`, `STRONG_SCORE_THRESHOLD`, `RR_LOW_RISK_THRESHOLD`, the volatility ratios). Every other threshold explains itself; this one does not.

2. **It makes `max_open_risk_percent` dead code.** Derived from the two formulas:

   ```
   open_risk% = n × risk%          exposure% = n × risk% / stop_fraction
   exposure binds first  ⟺  stop_fraction < max_open_risk / max_exposure = 6/50 = 12%
   ```

   | stop % | positions allowed by 6% open-risk | by 50% exposure | binds |
   |---|---|---|---|
   | 0.50 % | 6.0 | 0.25 | exposure |
   | 1.33 % | 6.0 | 0.66 | exposure |
   | 2.00 % | 6.0 | 1.00 | exposure |
   | 5.00 % | 6.0 | 2.50 | exposure |
   | 12.00 % | 6.0 | 6.00 | crossover |

   For **any** stop closer than 12% of price, the notional cap binds first. ICT structural stops are 0.3–1.5%. So `max_open_risk_percent = 6%` — the leverage-independent, theoretically correct portfolio risk control — **can never be reached**. Two limits where one silently renders the other unreachable is not a coherent policy; it is an unexamined interaction.

3. **The engine's own scope claims otherwise.** Its docstring says it merges *"Position Sizing, Correlation Risk, Portfolio Risk, Maximum Exposure, Maximum Drawdown, Daily Risk, Open Risk"* for a USD-M futures platform — while never referencing leverage, initial margin or maintenance margin, all of which a sibling module already extracts from the same API call.

**Conclusion: this is a remaining design issue, not a proven policy.** The answer to your original question is that the limit is *not* mathematically incompatible with ICT in principle — it is incompatible because it measures the wrong quantity at a value calibrated for a metric it isn't computing.

---

## 4. Which Metric Should Exposure Be?

They measure genuinely different things, and a futures risk engine wants more than one:

| Metric | Formula | What it controls | Status here |
|---|---|---|---|
| **Gross notional / wallet** | `notional / wallet` | Effective **leverage**. Legitimate — but it *is* leverage, so a cap below 100% forbids leverage entirely. | Currently used, capped at 50% (= 0.5×) |
| **Required margin / wallet** | `notional / leverage / wallet` | Capital actually **committed** — how much of the account is tied up and unavailable. | Not computed; `leverage` is already available |
| **Margin ratio** | `maint_margin / margin_balance` | **Liquidation proximity** — the metric Binance itself uses. | Already computed in `dashboard.py`, ignored by RiskEngine |
| **Open risk** | `Σ risk_usd / wallet` | Capital genuinely **at risk** given the stops. Leverage-independent; correct primary control for a stop-based strategy. | Present as `max_open_risk_percent=6%`, currently unreachable |

**My assessment:** for a stop-based ICT strategy, `max_open_risk_percent` is the theoretically correct primary control and it is already implemented — it is simply being shadowed. Gross notional is worth keeping as an explicit **leverage** cap, but it should be named as such and set to a leverage-appropriate value (300% = 3×, etc.), not 50%. Margin ratio is the right *safety* backstop and the data is already on hand.

---

## 5. What I Did Not Do

I changed no code and no limit. You previously instructed me not to alter risk limits, and the correct resolution here is a policy decision that is yours:

- **(a)** Reinterpret the existing cap as leverage and raise it (e.g. `max_exposure_percent = 300` for 3×), keeping the formula.
- **(b)** Switch exposure to `required_margin / wallet` using the `leverage` field Binance already returns — a real code change, and the value 50% would then be meaningful roughly as-is.
- **(c)** Rely on `max_open_risk_percent` as the primary gate and demote the notional cap to a loose leverage ceiling.

**Remaining risk:** whichever you choose, execution stays blocked until then — a single ICT trade at 1% risk with a 1.33% stop reports 75.4% against a 50% cap. And note that under **(b)**, the platform does not control leverage (it never calls `/fapi/v1/leverage`), so the margin figure would depend on whatever leverage you have set per-symbol in Binance — that dependency should be surfaced in the UI rather than left implicit.
