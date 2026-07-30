# Stress Testing & Performance Audit Report

**Phase 2, Objectives 7 & 8**
Date: 2026-07-30

## Objective 7: Stress Testing

Built `tests/test_stress_scenarios.py` (new, test-only — no production module added) running the real, unmodified `SignalGenerator.evaluate()` (the same single-pass entry point `UniversalScanner` uses live) against six synthetic regimes. "Pass" is defined as: the pipeline never raises, every decision is fully self-explaining (JSON-serializable `TradeDecision.to_dict()`, and every `NO_TRADE` carries at least one blocking gate or missing-evidence reason — never a silent, unexplained rejection), and any regime-specific claim this project has already made is verified to actually hold. **No test asserts a specific win rate, confidence value, or trade count under stress** — this project has no historical basis for such a number in this environment, and asserting one would be fabrication.

| Scenario | What was injected | Result |
|---|---|---|
| High volatility | ~8x baseline noise, positive drift | PASS — no exception across 3 seeds; whenever a trade was produced, stop ≠ target and RR > 0 |
| Low volatility | ~0.017x baseline noise (near-flat) | PASS — degrades honestly to a fully-explained NO_TRADE across 3 seeds, never crashes on a starved ATR/structure read |
| Sideways | zero drift, moderate noise | PASS — no exception across 3 seeds regardless of whether a structure break formed |
| News spike | one candle forced to ~15x local ATR + 20x volume mid-series | PASS — no exception across 2 seeds |
| Weekend crypto | series deliberately spans a Saturday/Sunday | PASS — confirmed `CRYPTO_PROFILE.ict_filters.reject_off_session is False`, and confirmed the `OFF_SESSION` gate never fires purely from weekend timestamps |
| Commodity market close | last candle forced into Gold's declared 22:00–23:00 UTC daily settlement break, and separately into a weekend timestamp | PASS — confirmed `COMMODITY_PROFILE.ict_filters.reject_off_session is True`, and when `SessionEngine` itself reports `is_off_session`, the `OFF_SESSION` `BlockingReason` is confirmed present in the decision — the profile-level filter genuinely fires, not just declared |

All 7 test methods pass. This proves robustness (no crash, no silent rejection, gates fire when they should) across regimes — it does not and cannot prove profitability under stress without a real historical dataset (see `BACKTEST_VALIDATION_REPORT.md`'s environment disclosure).

## Objective 8: Performance Audit

Measured directly against real project code in this sandbox (Python 3.10, pandas 2.3.3, numpy 2.2.6; no GPU/special hardware — figures are relative/comparative, not a production-hardware SLA).

**Pipeline speed** (`SignalGenerator.evaluate()`, 600-candle window, 200 warmed-up calls):

| Symbol | ms/call |
|---|---|
| BTCUSDT (crypto) | 27.17 |
| XAUUSDT (commodity) | 28.93 |
| Cost ratio | 1.065x |

Crypto and commodity cost essentially the same per evaluation (within 6.5%) — re-confirms the Phase 1 finding that this is genuinely one pipeline, not two, since the only difference between the two calls is which `AssetProfile`/`CalibrationProfile` is attached.

**AI Scorer speed (isolated):** captured a real `features` dict from one live pipeline pass, then timed `AIScorer.assess(features)` alone, 500 calls: **0.092 ms/call** — roughly 0.3% of total pipeline time. Confirms (again) that ICT structural detection (Market Structure, Liquidity, Order Blocks, FVGs, Supply/Demand, Order Flow) dominates cost, not the scoring/confidence layer.

**Scanner speed (end-to-end):** `UniversalScanner.analyze_symbol("BTCUSDT")` against the existing test suite's stubbed data manager (network/DB boundaries stubbed, everything else real), 10 runs after warmup: **avg 25.68 ms, min 24.00 ms, max 28.93 ms.** This is in the same range as `evaluate()` alone (27.17 ms) — confirms no duplicate pipeline execution at the scanner layer, consistent with `tests/test_universal_scanner.py::test_scan_runs_the_pipeline_exactly_once`, which fails loudly if `generate()`/`generate_decision()` are ever called during a scan instead of `evaluate()`.

**Duplicate-computation check:** instrumented `OrderFlowMetrics.__init__` and `MarketStructureEngine.__init__` with call counters across one `evaluate()` call: **both constructed exactly once per call** (expected: 1). No duplicate ATR/structure computation.

**Memory:** `tracemalloc` across one `evaluate()` call: **~43.0 KB current / ~249.3 KB peak** allocated. Trivial — no memory leak indication, no unexpectedly large allocation.

**CPU / RSS:** a realistic 4-symbol scan cycle (BTCUSDT, ETHUSDT, XAUUSDT, CLUSDT — 2 crypto + 2 commodity) — **183.23 ms wall time, 169.04 ms CPU user time, 0 ms CPU sys time, 76.7 MB max RSS** (cumulative for the whole Python process, not just this cycle — RSS is monotonic and reflects the interpreter + pandas/numpy + all loaded modules, not a per-scan delta).

## Verdict

**No performance regression, no duplicate computation, no crash under any tested stress regime.** Per-symbol pipeline cost (~25–29 ms) is small enough that a multi-symbol scan cycle (the platform currently tracks a handful of symbols) completes in well under 200 ms; this leaves ample headroom before scan latency could become the bottleneck at the platform's current symbol count. Real production-hardware CPU/memory figures should still be measured in the actual Docker deployment before treating these sandbox numbers as an SLA — they are directionally reliable (relative costs, ratios, "no duplication") but not a substitute for a production-environment measurement.
