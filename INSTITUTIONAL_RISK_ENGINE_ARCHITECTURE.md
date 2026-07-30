# Institutional Risk Engine — Architecture Design

**Date:** 2026-07-30
**Status:** DESIGN ONLY. No code written or modified. For approval before implementation.

---

## 0. Design Principles

1. **One concept, one control.** Two checks that differ only by a constant factor are one check with two names. §4 proves which of the eight are genuinely independent and dissolves the overlaps.
2. **Each metric from its authoritative source.** Binance knows margin, leverage and liquidation. It does *not* know our stops. The engine reads each fact from whoever actually owns it — never re-derives one from the other.
3. **Evaluate everything, short-circuit nothing.** The existing engine already collects every failing reason rather than stopping at the first (`risk_engine.py` "GATE EVALUATION"). Keep that; order affects *reporting priority*, not whether a check runs.
4. **Blocking is for solvency and mandate; warning is for judgement.** A check blocks only if proceeding is either impossible, or violates a limit the user set as policy.
5. **Never fabricate.** Any metric whose inputs are unavailable reports `UNKNOWN` and is excluded from the verdict — it never silently passes as zero.

---

## 1. The Equity Base (fixes a real inconsistency)

Every ratio needs one denominator. Today the codebase uses `wallet_balance`, which **excludes unrealized PnL** — so a portfolio deep in profit understates its risk ratios, and one deep in loss overstates them.

```
equity = margin_balance          # = wallet_balance + unrealized_pnl
```

`FuturesAccountInfo.margin_balance` is already parsed (`binance_account_service.py:481`, from `totalMarginBalance`). This is the same base Binance itself uses for margin ratio, so our numbers and the exchange's become directly comparable.

**Impact:** `wallet_balance` remains what the Account screen displays; `equity` becomes the risk denominator. This is a deliberate, documented change of meaning, not a silent one.

---

## 2. The Eight Metrics

Notation: `n` = open positions, `E` = equity, `Rᵢ` = per-position risk, `Nᵢ` = notional, `Lᵢ` = that symbol's exchange leverage.

| # | Metric | Formula | Question it answers |
|---|---|---|---|
| 1 | **Maximum Open Risk** | `Σ Rᵢ / E` where `Rᵢ = qtyᵢ × abs(entryᵢ − initial_stopᵢ)` | If every open stop is hit, how much of the account is gone? |
| 2 | **Capital Margin Usage** | `Σ (Nᵢ / Lᵢ) / E` | How much capital is locked as collateral — can I even open this? |
| 3 | **Effective Portfolio Leverage** | `Σ Nᵢ / E` | How large is total position size relative to the account? |
| 4 | **Liquidation Margin Ratio** | `total_maintenance_margin / margin_balance` | How close is the account to forced liquidation? |
| 5 | **Correlation Risk** | Pearson ρ on 15m returns, existing `CORRELATION_THRESHOLD = 0.7` | Are these positions secretly the same bet? |
| 6 | **Maximum Drawdown** | `(peak_equity − E) / peak_equity` | How far below the high-water mark is the account? |
| 7 | **Daily Loss** | `realized_pnl_today / E` | Has today already been bad enough to stop? |
| 8 | **Portfolio Concentration** | `max(cluster_risk) / Σ Rᵢ`, clusters from #5 | Is the portfolio one bet wearing several hats? |

---

## 3. Binance Data Mapping — every metric is already satisfiable

| Metric | Field | Where it already exists |
|---|---|---|
| Equity | `margin_balance` | `FuturesAccountInfo.margin_balance` |
| 1. Open Risk | `Signal.initial_stop_loss`, `entry_price`, qty | DB — Binance does not know our stops |
| 2. Margin Usage | `FuturesPosition.margin` (`positionInitialMargin`/`isolatedWallet`) | `binance_account_service.py:449` — **real, not derived** |
| 2 (fallback) | `FuturesPosition.leverage` | `:122`, `:460` — for a position not yet open |
| 3. Leverage | `position_amt × mark_price` | `:117`, `:126` |
| 4. Liquidation | `total_maintenance_margin`, `margin_balance` | `:481-482` |
| 4 (per-position) | `liquidation_price`, `mark_price` | `:126-127` — **already fetched from `/fapi/v2/positionRisk`** |
| 5. Correlation | 15m candle returns | `correlation_risk.py` + `data_manager` |
| 6. Drawdown | equity peak | **THE ONE GAP** — see §7 |
| 7. Daily Loss | `REALIZED_PNL` income rows | `get_income_history(income_type="REALIZED_PNL")` — exists, currently unused for risk |
| 8. Concentration | `Rᵢ` per symbol + #5 clusters | derived from the above |

**Two corrections to today's behaviour this implies.** Daily loss is currently *inferred* from DB signal outcomes; Binance's realized-PnL income stream is authoritative and already fetchable. And margin/leverage/liquidation must come from **real Binance positions**, not from DB signals — the current engine has no idea what is actually open on the exchange.

---

## 4. Overlap Analysis — dissolving the duplicates

This is where the current engine went wrong, so it is proven rather than asserted.

### 4.1 Open Risk vs Leverage — related, and that relationship is the bug

Since `Nᵢ = Rᵢ × (entryᵢ / stop_distᵢ)`:

```
Leverage = Open_Risk / stop_fraction
```

They are **not independent**. This is precisely why `max_open_risk = 6%` became unreachable: with a 1.33% stop, leverage runs 75× larger than open risk, so a 50% leverage cap binds ~9× tighter than the 6% risk cap.

**Resolution — keep both, but bind their limits explicitly:**

```
leverage_limit  ≥  open_risk_limit / expected_stop_fraction
```

With `open_risk_limit = 6%` and ICT stops of ~0.5–2%, a coherent leverage ceiling is **300–1200%** (3×–12×). Set below that and the risk-based control is dead code. **A startup assertion should fail loudly if the configured limits make either control unreachable** — that single guard would have caught this class of bug immediately.

### 4.2 Margin Usage vs Leverage — independent *only because* leverage varies per symbol

```
Margin_Usage = Σ (Nᵢ / Lᵢ) / E        Leverage = Σ Nᵢ / E
```

If every symbol shared one leverage, `Margin_Usage = Leverage / L` — pure redundancy. They differ only because `Lᵢ` is per-symbol and set outside this platform. **Both are kept, with distinct roles:** Margin Usage is a *solvency* constraint (the exchange will reject the order if collateral is insufficient); Leverage is a *policy* constraint (how much size the mandate permits).

### 4.3 Liquidation Ratio vs Margin Usage — different failure modes

Both scale with notional, but: Margin Usage asks *"can I open this?"* (uses `wallet`/`available_balance`, pre-trade), Liquidation Ratio asks *"am I about to be force-closed?"* (uses `margin_balance`, which moves with unrealized PnL, continuously). A portfolio can sit at 20% margin usage and still approach liquidation after adverse movement. **Not redundant — different tiers.**

### 4.4 Drawdown vs Daily Loss — separated by scope and by realization

Overlapping if both measured the same thing over the same window. Separate them by definition:

- **Daily Loss** — **realized** only, resets at UTC midnight. Tactical circuit breaker.
- **Drawdown** — **equity** (realized + unrealized) vs all-time peak, persists. Strategic.

A single bad day counts once against each, measuring different facts (booked losses vs high-water erosion). Making Daily Loss realized-only is what removes the double-count.

### 4.5 Correlation vs Concentration — **merge them**

This is the real duplicate. Concentration measured on raw symbols is wrong precisely *because* of correlation: three 20% positions in perfectly correlated alts is one 60% bet, and a naive per-symbol check waves it through.

**Correlation becomes an input to Concentration, not a parallel check:**

```
1. Compute pairwise ρ across open + candidate symbols   (existing engine)
2. Cluster symbols with ρ ≥ 0.7                          (single-linkage)
3. cluster_risk = Σ Rᵢ over the cluster
4. Concentration = max(cluster_risk) / Σ Rᵢ
```

Correlation still surfaces its own advisory message (preserving `correlation_risk.py`'s documented "advisory only, never blocks" contract), but the *limit* lives on the correlation-adjusted concentration. **Eight named metrics; seven independent controls.**

---

## 5. Block vs Warn

| # | Metric | Verdict | Why |
|---|---|---|---|
| 7 | Daily Loss | **BLOCK** | Explicit user mandate; a circuit breaker that only warns isn't one |
| 6 | Max Drawdown | **BLOCK** | Capital preservation mandate |
| 4 | Liquidation Margin Ratio | **BLOCK** | Existential — nothing else matters if the account is liquidated |
| 2 | Capital Margin Usage | **BLOCK** | Physical: insufficient collateral means the exchange rejects it anyway |
| 1 | Maximum Open Risk | **BLOCK** | The core stop-based portfolio control |
| 3 | Effective Leverage | **BLOCK** | Policy ceiling — but only meaningful once §4.1 is respected |
| 8 | Concentration | **BLOCK** above hard cap, **WARN** in the band below | A single cluster holding most of the risk is a mandate breach |
| 5 | Correlation | **WARN only** | Preserves the module's existing explicit design decision; its teeth are in #8 |

**Sizing failure** (`position_size is None`) stays blocking, but must report the *actual* cause — the current combined "unknown balance **or** degenerate pair" message is what made the breakeven bug so hard to find. Split into two distinct reasons.

---

## 6. Execution Order

Two orderings, deliberately distinct.

**6.1 Computation order** (dependency-driven — each step needs the previous):

```
0. Account snapshot   -> equity, margin_balance, maintenance, real positions
1. Candidate sizing   -> qty, notional, risk_usd   (from initial_stop_loss)
2. Per-position facts -> notional, initial margin, per-symbol leverage
3. Correlation matrix -> clusters
4. Portfolio aggregates -> Σ risk, Σ notional, Σ margin
5. Evaluate all eight
```

**6.2 Reporting order** (severity-driven — evaluate all, but report most-actionable first):

```
Tier 1  HALT       Daily Loss, Max Drawdown         "stop trading entirely today"
Tier 2  SOLVENCY   Liquidation Ratio, Margin Usage  "the account cannot support this"
Tier 3  MANDATE    Open Risk, Leverage              "policy limit"
Tier 4  QUALITY    Concentration (hard cap)         "portfolio shape"
Tier 5  ADVISORY   Concentration (soft), Correlation "judgement call"
```

Rationale: if a daily-loss halt is active, that is the *only* thing worth telling the user — that leverage would also be exceeded is noise. The current flat list buries the actionable reason among consequences.

---

## 7. The One Genuine Gap: Equity Peak

Drawdown needs a high-water mark, and this project has **no equity-curve table**. Today `signal_service.py` reconstructs it as *"balance before today's realized PnL, when today was negative"* — honestly documented as a lower bound, but it cannot see a peak from last week.

**Proposed:** a small `equity_snapshots` table (`timestamp`, `equity`, `wallet_balance`, `unrealized_pnl`), written once per monitor poll (30s) — a real time series, not a guess. Cheap: 2,880 rows/day, trivially prunable.

**Until it exists, Drawdown must report `UNKNOWN` and not participate in the verdict.** It must never silently pass as 0% — that would claim "no drawdown" when the truth is "we don't know", which is exactly the fabrication this project's conventions forbid.

---

## 8. Proposed Structure

```
app/risk/
  engine.py        RiskEngine.assess()  - orchestration only, no arithmetic
  metrics/
    open_risk.py         margin_usage.py      leverage.py
    liquidation.py       correlation.py       drawdown.py
    daily_loss.py        concentration.py
  limits.py        RiskLimits + the §4.1 coherence assertion
  context.py       RiskContext - everything fetched, nothing computed
  result.py        MetricResult / RiskAssessment
```

Each metric is a pure function `(RiskContext) -> MetricResult{value, limit, status, detail}` with `status ∈ {PASS, WARN, BLOCK, UNKNOWN}`. Independently unit-testable, no I/O, matching the "services fetch, engines compute" separation the codebase already uses.

**Backward compatibility:** `assess_new_trade()` keeps its current signature and return shape so `execution_risk.py` and `signal_service.py` need no changes on day one; the new metrics populate additional fields.

---

## 9. Limits to Configure (values are yours to set)

| Limit | Current | Design note |
|---|---|---|
| `max_open_risk_percent` | 6% | Sound. Becomes the *primary* control once §4.1 is fixed |
| `max_leverage_ratio` | (50% as "exposure") | **Must be ≥ 300% to avoid masking open risk** — see §4.1 |
| `max_margin_usage_percent` | — new | Suggest 50–70%; leaves headroom for adverse moves |
| `max_margin_ratio_percent` | — new | Suggest 50%; Binance auto-deleverages well before 100% |
| `max_daily_loss_percent` | 5% | Sound; switch input to realized income |
| `max_drawdown_percent` | 20% | Sound; needs §7 |
| `max_cluster_concentration` | — new | Suggest 50% hard / 35% warn |
| `correlation_threshold` | 0.7 | Reuse existing |

---

## 10. What This Fixes

- Open Risk becomes reachable instead of dead code (§4.1), with a startup assertion preventing recurrence.
- Margin, leverage and liquidation are measured against **real exchange positions** rather than inferred from DB signals.
- Equity includes unrealized PnL, so ratios stop drifting from Binance's own view.
- Correlation gains teeth via concentration instead of being an ignorable string.
- Rejections name the binding constraint first rather than listing consequences.
- Drawdown becomes honest — `UNKNOWN` until real equity history exists.

**Open questions for you:** (a) the eight limit values in §9; (b) approval for the `equity_snapshots` table in §7; (c) whether Margin Usage should measure against `equity` or the stricter `available_balance`; (d) whether this lands incrementally behind the existing signature (my recommendation) or as one cutover.
