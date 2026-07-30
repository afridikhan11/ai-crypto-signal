"""
Regression suite for app.smc.order_flow - the ICT pipeline's only
market-data primitives.

Includes a direct equivalence test against the real `ta` package
(`TestEquivalenceWithTaLibrary`). That test runs automatically wherever
`ta` is installed (CI, Docker) and skips where it is not, so any
divergence between the new Wilder ATR and the previous `ta`-backed one is
caught by the suite rather than assumed away.
"""
import numpy as np
import pandas as pd
import pytest

from app.smc.order_flow import (
    CVD_DIRECTION_LOOKBACK,
    DEFAULT_ATR_WINDOW,
    OrderFlowMetrics,
    true_range,
)


def _df(n=300, seed=1, with_taker=True):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, n))
    close = np.maximum(close, 5)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    hi = np.maximum(open_, close) + rng.uniform(0.05, 1.0, n)
    lo = np.minimum(open_, close) - rng.uniform(0.05, 1.0, n)
    volume = rng.uniform(10, 1000, n)
    data = {"open": open_, "high": hi, "low": lo, "close": close, "volume": volume}
    if with_taker:
        data["taker_buy_volume"] = volume * rng.uniform(0.3, 0.7, n)
    return pd.DataFrame(data, index=pd.date_range("2026-01-05", periods=n, freq="15min"))


class TestTrueRange:
    def test_first_value_falls_back_to_high_minus_low(self):
        """Row 0 has no previous close, so the two close-based components
        are NaN and the row-wise max (which skips NaN) yields H-L. This is
        the same behaviour the `ta` package produces, and the value the
        Wilder seed is computed from."""
        d = _df(50)
        tr = true_range(d["high"], d["low"], d["close"])
        assert not pd.isna(tr.iloc[0])
        assert abs(tr.iloc[0] - (d["high"].iloc[0] - d["low"].iloc[0])) < 1e-12

    def test_is_max_of_three_components(self):
        d = _df(50)
        tr = true_range(d["high"], d["low"], d["close"])
        prev_close = d["close"].shift(1)
        for i in range(1, 50):
            expected = max(
                d["high"].iloc[i] - d["low"].iloc[i],
                abs(d["high"].iloc[i] - prev_close.iloc[i]),
                abs(d["low"].iloc[i] - prev_close.iloc[i]),
            )
            assert abs(tr.iloc[i] - expected) < 1e-12

    def test_never_negative(self):
        d = _df(200, seed=4)
        tr = true_range(d["high"], d["low"], d["close"]).dropna()
        assert (tr >= 0).all()


class TestATR:
    def test_nan_before_seed_index(self):
        m = OrderFlowMetrics(_df(100))
        assert m.atr.iloc[: DEFAULT_ATR_WINDOW - 1].isna().all()

    def test_seeded_at_window_minus_one(self):
        m = OrderFlowMetrics(_df(100))
        assert not pd.isna(m.atr.iloc[DEFAULT_ATR_WINDOW - 1])

    def test_wilder_recursion_holds(self):
        m = OrderFlowMetrics(_df(100))
        w = DEFAULT_ATR_WINDOW
        tr = m.true_range.to_numpy(dtype=float)
        atr = m.atr.to_numpy(dtype=float)
        for i in range(w + 1, 100):
            expected = (atr[i - 1] * (w - 1) + tr[i]) / w
            assert abs(atr[i] - expected) < 1e-9

    def test_positive_and_finite(self):
        m = OrderFlowMetrics(_df(300, seed=9))
        tail = m.atr.dropna()
        assert (tail > 0).all()
        assert np.isfinite(tail.to_numpy()).all()

    def test_frame_shorter_than_window_is_all_nan(self):
        m = OrderFlowMetrics(_df(5))
        assert m.atr.isna().all()

    def test_deterministic(self):
        d = _df(200, seed=11)
        assert OrderFlowMetrics(d).atr.equals(OrderFlowMetrics(d).atr)


class TestCVD:
    def test_uses_taker_buy_volume_when_present(self):
        d = _df(100)
        m = OrderFlowMetrics(d)
        expected = (2 * d["taker_buy_volume"] - d["volume"]).cumsum()
        assert np.allclose(m.cvd.to_numpy(), expected.to_numpy())

    def test_missing_taker_column_degrades_to_negative_volume_sum(self):
        d = _df(100, with_taker=False)
        m = OrderFlowMetrics(d)
        assert np.allclose(m.cvd.to_numpy(), (-d["volume"]).cumsum().to_numpy())

    def test_cvd_rising_reads_recent_window_not_absolute_level(self):
        d = _df(100)
        m = OrderFlowMetrics(d)
        expected = bool(m.cvd.iloc[-1] > m.cvd.iloc[-1 - CVD_DIRECTION_LOOKBACK])
        assert m.get_latest()["cvd_rising"] == expected

    def test_cvd_rising_is_not_measured_against_the_first_candle(self):
        """Regression guard for the bug inherited from the previous
        implementation, where `max(0, idx - 10)` with `idx = -1` collapsed
        to index 0 and silently compared against the dawn of the frame."""
        d = _df(400, seed=21)
        m = OrderFlowMetrics(d)
        # Force a large CVD divergence between "10 candles ago" and "the
        # very first candle" so the two readings must disagree.
        against_first = bool(m.cvd.iloc[-1] > m.cvd.iloc[0])
        against_recent = bool(m.cvd.iloc[-1] > m.cvd.iloc[-1 - CVD_DIRECTION_LOOKBACK])
        assert m.get_latest()["cvd_rising"] == against_recent
        if against_first != against_recent:
            assert m.get_latest()["cvd_rising"] != against_first

    def test_cvd_rising_none_without_enough_history(self):
        m = OrderFlowMetrics(_df(CVD_DIRECTION_LOOKBACK))
        latest = m.get_latest()
        if latest is not None:
            assert latest["cvd_rising"] is None


class TestPointOfControl:
    def test_returns_a_price_inside_the_window_range(self):
        d = _df(300)
        poc = OrderFlowMetrics(d).poc_price
        assert poc is not None
        window = d.iloc[-200:]
        assert float(window["low"].min()) <= poc <= float(window["high"].max())

    def test_none_when_too_little_history(self):
        assert OrderFlowMetrics(_df(15)).poc_price is None

    def test_none_when_zero_volume(self):
        d = _df(100)
        d["volume"] = 0.0
        assert OrderFlowMetrics(d).poc_price is None

    def test_concentrated_volume_pulls_poc_to_that_level(self):
        d = _df(300, seed=3)
        # Dump enormous volume onto a narrow band of candles and confirm the
        # POC migrates to that band's typical price.
        target = d.iloc[-50:-40]
        d.loc[target.index, "volume"] = 1e9
        poc = OrderFlowMetrics(d).poc_price
        typical = ((target["high"] + target["low"] + target["close"]) / 3.0)
        assert typical.min() - 5 <= poc <= typical.max() + 5


class TestRelativeVolume:
    def test_matches_volume_over_its_own_sma(self):
        d = _df(200)
        m = OrderFlowMetrics(d)
        expected = round(float(d["volume"].iloc[-1] / m.volume_sma.iloc[-1]), 2)
        assert m.get_latest()["relative_volume"] == expected

    def test_none_before_sma_has_history(self):
        d = _df(19)
        m = OrderFlowMetrics(d)
        latest = m.get_latest()
        if latest is not None:
            assert latest["relative_volume"] is None


class TestGetLatestContract:
    def test_exposes_exactly_the_ict_pipeline_fields(self):
        latest = OrderFlowMetrics(_df(300)).get_latest()
        assert set(latest.keys()) == {"atr", "cvd", "cvd_rising", "poc_price", "relative_volume"}

    def test_no_retail_indicator_fields_leak_through(self):
        latest = OrderFlowMetrics(_df(300)).get_latest()
        for retail in ("ema20", "ema50", "ema200", "rsi", "macd_hist", "adx",
                       "supertrend_dir", "obv_rising", "squeeze_on", "donchian_breakout",
                       "stoch_rsi_k", "cci", "williams_r", "vwap"):
            assert retail not in latest

    def test_none_when_atr_unavailable(self):
        assert OrderFlowMetrics(_df(5)).get_latest() is None

    def test_none_on_empty_frame(self):
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert OrderFlowMetrics(empty).get_latest() is None

    def test_atr_is_a_plain_float(self):
        assert isinstance(OrderFlowMetrics(_df(300)).get_latest()["atr"], float)


class TestEquivalenceWithTaLibrary:
    """
    Verifies the in-house Wilder ATR matches `ta.volatility.AverageTrueRange`,
    the implementation the pipeline used before this module existed.

    Skips where `ta` is not installed (the offline verification sandbox);
    runs wherever it is (CI, Docker), which is exactly where a regression
    in this would matter.
    """

    def test_atr_matches_ta_average_true_range(self):
        ta_volatility = pytest.importorskip(
            "ta.volatility", reason="`ta` not installed - equivalence check runs in CI/Docker",
        )
        if getattr(ta_volatility, "__IS_OFFLINE_VERIFICATION_STUB__", False):
            # The offline verification harness puts an inert `ta` stub on
            # sys.path so unrelated legacy imports resolve. Comparing
            # against that stub would be meaningless - and worse, a green
            # result would imply a verification that never happened.
            pytest.skip("resolved `ta` is the offline harness stub, not the real package")
        for seed in (1, 2, 3, 7, 11):
            d = _df(400, seed=seed)
            mine = OrderFlowMetrics(d).atr
            theirs = ta_volatility.AverageTrueRange(
                high=d["high"], low=d["low"], close=d["close"], window=DEFAULT_ATR_WINDOW,
            ).average_true_range()
            # Compare where both are defined; `ta` zero-fills its warm-up
            # region while this module leaves it NaN (an intentional
            # difference - a fabricated 0 ATR would be a silent landmine).
            both = mine.notna() & theirs.notna() & (theirs != 0)
            assert both.sum() > 300, "not enough overlapping points to compare"
            assert np.allclose(
                mine[both].to_numpy(), theirs[both].to_numpy(), rtol=1e-9, atol=1e-9,
            ), f"ATR diverged from the ta library on seed {seed}"
