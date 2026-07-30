# Signal Pipeline Diagnostics

**Date:** 2026-07-30
**Scope:** Diagnostics only. No ICT logic, AI scoring, Universal Scanner decision logic, Risk Engine, Trade Management, Auto Trading, position sizing, or confidence threshold was modified. No filter was loosened. No gate was bypassed. No signal is fabricated.

---

## How This Stays Read-Only

`SignalGenerator.evaluate()` already evaluates every hard gate on every run (never short-circuits) and returns one fully-explained `TradeDecision` object, carrying `missing_evidence` (which of the 12 ICT components were absent), `blocking_gates` (every named gate that blocked the trade), `confidence`, and `is_tradeable`. This diagnostics layer **only reads that already-computed object after `evaluate()` returns** — it never calls into an SMC/ICT engine, the AI Scorer, the Decision Engine, or the Risk Engine, and has no return value the live pipeline consumes. A bug in the diagnostics code cannot alter a decision that was already made and returned before the diagnostics code ever runs; every recording call is additionally wrapped so an exception inside it is logged and swallowed, never propagated.

---

## 1. Files Modified

**New files (zero coupling to any ICT/AI/Risk module):**
- `app/diagnostics/__init__.py`
- `app/diagnostics/signal_pipeline_diagnostics.py` — the diagnostics engine: per-stage counters, rejection-reason tally, and the 5-minute summary.

**Modified (additive only — every existing line is untouched; only new lines were inserted):**
- `app/scheduler/universal_scanner.py` — 8 new lines calling into the diagnostics module, at points where `analyze_symbol()` and `save_signal()` already had the relevant information available (candles fetched, decision returned, DB save outcome, Redis publish outcome).

**Not touched, confirmed:** `app/strategy/signal_generator.py`, `app/ai/ict_decision_engine.py`, `app/ai/scorer.py`, `app/risk/risk_engine.py`, `app/strategy/trade_management_engine.py`, every file under `app/smc/`, `app/api/v1/endpoints/trading.py`, `app/services/execution_risk.py`. Full regression suite re-run after this change: 625/627 passing, identical to before (the one failure is the same pre-existing, already-diagnosed environmental issue unrelated to this or any prior change — a test that depends on this sandbox's real, currently-stopped `engine_run_state` on disk).

---

## 2. Logging Added

Every scan cycle, per symbol, now feeds:

- **Market Data** — candles fetched vs. empty.
- **Scanner** — scan attempted.
- **ICT Detection / Institutional Bias / Order Block / Liquidity / FVG** — read from `decision.missing_evidence`; "found" unless the component appears in that list. Processing time for these five is reported as one bundled number (see the disclosed limitation below), not a fabricated per-engine split.
- **AI Scoring** — `decision.confidence`, filtered iff the `min_confidence` gate specifically fired.
- **Risk Engine** — always reported **N/A**. It genuinely does not run during signal generation in this codebase — `RiskEngine` is only invoked from `POST /trading/execute/{signal_id}`, after a signal already exists, when a user or Auto Trading chooses to execute it. This is itself a real, disclosed diagnostic finding, not an omission.
- **Signal Generator** — the final `is_tradeable` verdict, plus every gate in `decision.blocking_gates` (a run can hit more than one, since every gate is evaluated).
- **Database Save** — a real Signal row committed vs. skipped because an ACTIVE signal already exists for that coin ("Duplicate Signal").
- **WPF Update** — the Redis `new_signal` publish that follows a successful save.

A summary prints automatically every 5 minutes (checked cheaply on each scan, no new background task added), and can be triggered on demand via `diagnostics.force_log_summary()`. Because it logs through the existing `loguru` sink, it also flows into the Auto Trading Control Panel's existing Trading Logs panel in the WPF app automatically — no additional wiring needed.

**One disclosed limitation:** per-engine Processing Time (Order Block vs. Liquidity vs. FVG, individually) is not separately measurable from outside `signal_generator.py` without adding timing markers inside that file — which this phase deliberately does not do, to keep the ICT pipeline file completely untouched. The five ICT sub-stages currently share one bundled timing figure (the whole `evaluate()` call divided evenly). If true per-engine timing is wanted, that would need a small, explicitly-approved follow-up inside `signal_generator.py` itself (still purely additive, no logic change) — flagging this as a choice for you to make, not doing it unilaterally.

---

## 3. Sample Diagnostic Output

Verified against the real diagnostics engine with synthetic scan results (6 symbols: 5 rejected for a mix of reasons, 1 passing and saved/published), exercising every code path:

```
==============================================================================
SIGNAL PIPELINE DIAGNOSTICS SUMMARY  (on-demand)
==============================================================================
Symbols scanned:      7
ICT setups found:     6  (confirmed BOS/CHoCH present)
Passed AI:            4  (cleared min_confidence)
Passed Risk Engine:   N/A - Risk Engine does not run during signal generation
Signals generated:    1  (lifetime: 1)
Signals saved:        1  (lifetime: 1)
Signals displayed:    1  (lifetime: 1)
------------------------------------------------------------------------------
Per-stage counts (this interval):
  Market Data                                   in=7  out=6  filtered=1  filter%=14.3   avg_ms=0.0
  Scanner                                       in=7  out=7  filtered=0  filter%=0.0    avg_ms=0.0
  ICT Detection (Market Structure)              in=6  out=6  filtered=0  filter%=0.0    avg_ms=2.84
  Institutional Bias                            in=6  out=5  filtered=1  filter%=16.7   avg_ms=2.84
  Order Block Detection                         in=6  out=4  filtered=2  filter%=33.3   avg_ms=2.84
  Liquidity Detection                           in=6  out=5  filtered=1  filter%=16.7   avg_ms=2.84
  Fair Value Gap Detection                      in=6  out=5  filtered=1  filter%=16.7   avg_ms=2.84
  AI Scoring                                    in=6  out=4  filtered=2  filter%=33.3   avg_ms=14.2
  Signal Generator (final decision)             in=6  out=1  filtered=5  filter%=83.3   avg_ms=14.2
  Database Save                                 in=1  out=1  filtered=0  filter%=0.0    avg_ms=0.0
  WPF Update (Redis publish)                    in=1  out=1  filtered=0  filter%=0.0    avg_ms=0.0
------------------------------------------------------------------------------
Top rejection reasons (this interval):
  No Order Block                                2
  Confidence Too Low                            2
  Liquidity/Order Block/FVG Confluence Missing  2
  Liquidity Missing                             1
  Weak/Missing FVG                              1
  Risk/Reward Too Small                         1
  No Institutional Bias                         1
  No HTF Bias Alignment                         1
  No Market Data                                1
==============================================================================
```

---

## 4. Top Rejection Reasons (taxonomy now tracked)

Every reason below maps 1:1 to a real gate or component already computed by the existing pipeline — nothing here is invented:

No HTF Bias Alignment, No MSS/BOS, No Order Block, Liquidity Missing, Weak/Missing FVG, No Institutional Bias, Confidence Too Low, Risk/Reward Too Small, Liquidity/Order Block/FVG Confluence Missing, Entry Validation Failed, Trading Session Closed, Outside Required Kill Zone, Insufficient Candle Data, Order-Flow Indicator Unavailable, Invalid ATR, No Market Data, Duplicate Signal.

Portfolio Risk Exceeded / Daily Risk Exceeded / Exposure Limit / Cooldown Active are not in this list because they don't exist as generation-time gates in this codebase — they're `RiskEngine` checks (portfolio open risk, daily loss, exposure) that only apply at execution time, consistent with the "Risk Engine: N/A during generation" finding above.

---

## 5. Root Cause of Zero Signals

**Cannot be determined from this sandbox** — there is no live Binance market data or a running scanner process available here (no network egress, confirmed in earlier phases), so the diagnostics above are verified end-to-end against synthetic data, not a real scan. Once you run the app with this instrumentation live, the 5-minute summary log (or the WPF Trading Logs panel) will show the top rejection reasons directly and answer this precisely.

**One real, historical data point worth knowing while you wait for that:** `app/ai/calibration_profiles.py`'s own module docstring already records that before per-asset calibration existed, "the highest confidence ever recorded for XAUUSDT across all logs was 73, against an 85 minimum" — Gold/Silver/Oil have historically never cleared the confidence bar because they trade far less often than the ~50 crypto pairs this platform also scans. That's a documented fact about commodities specifically, not a diagnosis of crypto. If crypto is ALSO producing zero signals, the new instrumentation is what will tell you why — whether it's the confidence gate, the confluence requirement (needs a liquidity sweep, order block, or FVG present), HTF opposition, or something upstream like empty candle data.
