import pandas as pd
from ta.trend import EMAIndicator


def ema_trend_from_df(df: pd.DataFrame, min_separation: float) -> str:
    """
    Pure EMA20/EMA50 trend read from an OHLCV dataframe (last row = the
    candle to evaluate as-of). Returns "neutral" if there isn't enough data
    yet or the EMAs are too close to call a real trend, so an unready/
    ambiguous read never silently fakes a directional bias.

    LEGACY (2026-07-30): this is retail EMA trend classification and is no
    longer part of the production ICT path - the Universal ICT Pipeline
    derives higher-timeframe context from real Market Structure instead
    (see app/smc/htf_structure_engine.py and
    app/smc/institutional_bias_engine.py). It is retained here because
    app/services/market_scorer.py, app/services/ta_dashboard.py and
    app/backtest/engine.py still call it.

    Originally extracted out of the former CryptoScanner so it had one
    implementation
    shared by:
      - the live scanner (app/scheduler/scanner.py), which calls it on the
        trailing N live candles "as of now"
      - the BacktestEngine (app/backtest/engine.py), which calls it on a
        trailing N-candle slice ending at a historical timestamp

    Keeping this as a single pure function (no I/O, no live-data-manager
    dependency) means both callers can never silently drift apart in how
    "trend" is defined.
    """
    if len(df) < 50:
        return "neutral"

    ema20 = EMAIndicator(close=df["close"], window=20).ema_indicator().iloc[-1]
    ema50 = EMAIndicator(close=df["close"], window=50).ema_indicator().iloc[-1]
    if pd.isna(ema20) or pd.isna(ema50) or ema50 == 0:
        return "neutral"

    separation = (ema20 - ema50) / ema50
    if separation > min_separation:
        return "up"
    elif separation < -min_separation:
        return "down"
    return "neutral"
