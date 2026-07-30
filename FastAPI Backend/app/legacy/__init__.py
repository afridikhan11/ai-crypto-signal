"""
LEGACY - retail technical-analysis code, quarantined from the production
ICT pipeline.

STATUS (2026-07-30): ISOLATED, NOT DELETED. Created by the Universal
Institutional ICT Platform phase.

WHY THIS PACKAGE EXISTS
--------------------------------------------------------------------------
This platform's production path is the Universal ICT Pipeline
(`app/strategy/signal_generator.py`) driven by the ICT engines in
`app/smc/`. That path must not depend on retail strategy code - EMA, RSI,
MACD, Stochastic RSI, CCI, Williams %R, ADX, Supertrend, Bollinger,
Keltner, Donchian, OBV, VWAP, candlestick patterns or classical chart
patterns.

The modules in this package implement exactly that retail toolkit. They
are NOT dead code and were NOT deleted: several user-facing features that
predate the ICT migration still legitimately use them, and silently
removing working features is not permitted in this project.

WHAT IS IN HERE, AND WHO STILL USES IT
--------------------------------------------------------------------------
  confirmation.py         - the ~20-indicator retail suite (`ta`-backed).
                            Used by: app/services/ta_dashboard.py,
                            app/services/market_scorer.py,
                            app/services/token_scorer.py
  candlestick_patterns.py - 6 classical reversal patterns.
                            Used by: app/services/market_scorer.py
  chart_patterns.py       - Double Top/Bottom, Triangles, Flags.
                            Used by: app/services/market_scorer.py
  trend.py                - EMA20/50 trend classification.
                            Used by: app/services/market_scorer.py,
                            app/services/ta_dashboard.py,
                            app/backtest/engine.py (explicitly out of
                            scope for modification this phase)

THE RULE
--------------------------------------------------------------------------
Nothing under `app/smc/`, `app/ai/`, `app/assets/`, `app/risk/`,
`app/strategy/` or `app/scheduler/universal_scanner.py` may import from
this package. `tests/test_legacy_isolation.py` enforces that mechanically
by walking the real import statements in the repository, so the boundary
cannot rot silently.

The ICT pipeline's own market-data needs (ATR, CVD, Volume Profile POC,
relative volume) are met by `app/smc/order_flow.py`, which computes them
from raw OHLCV with no third-party TA dependency.
"""
