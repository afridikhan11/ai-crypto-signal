"""
Regression suite for the QUARANTINED retail indicator suite
(app/legacy/confirmation.py).

This module is no longer part of the production ICT path - the Universal
ICT Pipeline gets its market-data primitives from app/smc/order_flow.py
(see tests/test_order_flow.py). These tests are retained because
app/services/ta_dashboard.py, market_scorer.py and token_scorer.py still
depend on this code, so it must keep working even though it is isolated.

Renamed from test_confirmation_indicators.py when the module moved to
app/legacy/ in the Universal ICT Platform phase.
"""
import numpy as np
import pandas as pd

from app.legacy.confirmation import ConfirmationIndicators


def _make_indicators(df, atr_values):
    """
    Builds a ConfirmationIndicators instance without running the full
    _compute() pipeline (which needs the `ta` library's EMA/RSI/MACD/ADX
    indicators) - _calculate_supertrend only needs self.df and self.atr,
    so those are set directly.
    """
    obj = object.__new__(ConfirmationIndicators)
    obj.df = df
    obj.atr = pd.Series(atr_values, index=df.index)
    return obj


def test_supertrend_matches_reference_loop_implementation():
    """
    Regression test for the numpy-array rewrite of _calculate_supertrend
    (previously a pandas .iloc loop, ~100x slower and the dominant cost of
    a multi-symbol backtest sweep - see the fix's commit/comment in
    app/indicators/confirmation.py). This reference implementation is the
    ORIGINAL .iloc-based logic, kept here only to prove the rewrite
    produces identical output, not as something to run in production.
    """
    rng = np.random.default_rng(7)
    n = 100
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0.5, 0.3, n))
    low = close - np.abs(rng.normal(0.5, 0.3, n))
    atr = np.abs(rng.normal(1.0, 0.2, n))
    df = pd.DataFrame({"high": high, "low": low, "close": close})

    def reference_supertrend(df, atr, multiplier=3.0):
        high_s, low_s, close_s = df["high"], df["low"], df["close"]
        hl_avg = (high_s + low_s) / 2
        upper = hl_avg + multiplier * atr
        lower = hl_avg - multiplier * atr
        st = pd.Series(np.nan, index=df.index)
        direction = pd.Series(1, index=df.index)
        for i in range(1, len(df)):
            if close_s.iloc[i] <= upper.iloc[i - 1]:
                upper.iloc[i] = min(upper.iloc[i], upper.iloc[i - 1])
            if close_s.iloc[i] >= lower.iloc[i - 1]:
                lower.iloc[i] = max(lower.iloc[i], lower.iloc[i - 1])
            if close_s.iloc[i] > upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif close_s.iloc[i] < lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]
            st.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]
        st.iloc[0] = lower.iloc[0]
        return st, direction

    atr_series = pd.Series(atr, index=df.index)
    expected_st, expected_dir = reference_supertrend(df, atr_series)

    indicators = _make_indicators(df, atr)
    actual_st, actual_dir = indicators._calculate_supertrend()

    assert np.allclose(actual_st.to_numpy(), expected_st.to_numpy(), equal_nan=True)
    assert (actual_dir.to_numpy() == expected_dir.to_numpy()).all()


def test_supertrend_handles_single_row():
    df = pd.DataFrame({"high": [101.0], "low": [99.0], "close": [100.0]})
    indicators = _make_indicators(df, [1.0])
    st, direction = indicators._calculate_supertrend()
    assert len(st) == 1
    assert not pd.isna(st.iloc[0])
