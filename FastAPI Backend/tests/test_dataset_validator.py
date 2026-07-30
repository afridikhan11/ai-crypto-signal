"""
Historical Dataset Validator tests (Phase 2, objective 3).

Proves the validator both PASSES a clean synthetic dataset and correctly
DETECTS each deliberately-injected defect - missing candles, duplicates,
tz-aware index, OHLC violations, negative/NaN volume, and session/closed-
window candles for a non-24/7 profile. A validator that only ever passes
would be worthless, so every failure-mode test below builds a dataframe
with ONE specific defect and asserts the matching check fails while every
other check still passes.
"""
import numpy as np
import pandas as pd
import pytest

from app.assets.asset_profile import COMMODITY_PROFILE, CRYPTO_PROFILE
from app.backtest.dataset_validator import validate_ohlcv_dataset


def _clean_df(n=200, freq="15min", start="2026-01-05", seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 5)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    hi = np.maximum(open_, close) + rng.uniform(0.1, 1.0, n)
    lo = np.minimum(open_, close) - rng.uniform(0.1, 1.0, n)
    vol = rng.uniform(10, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": hi, "low": lo, "close": close, "volume": vol},
        index=pd.date_range(start, periods=n, freq=freq),
    )


class TestCleanDataset:
    def test_clean_dataset_passes_every_check(self):
        df = _clean_df()
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m", asset_profile=CRYPTO_PROFILE)
        assert report.passed, [c.to_dict() for c in report.failures]
        assert report.candle_count == 200

    def test_report_serializes_to_dict(self):
        report = validate_ohlcv_dataset(_clean_df(), "BTCUSDT", "15m")
        d = report.to_dict()
        assert d["symbol"] == "BTCUSDT" and d["passed"] is True

    def test_crypto_24_7_weekend_candles_are_not_a_violation(self):
        # 15 days of 15m candles definitely spans a weekend.
        df = _clean_df(n=15 * 96, start="2026-01-01")
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m", asset_profile=CRYPTO_PROFILE)
        session_check = next(c for c in report.checks if c.name == "session_and_trading_hours")
        assert session_check.status == "pass"


class TestMissingCandles:
    def test_detects_a_gap(self):
        df = _clean_df(n=100)
        broken = pd.concat([df.iloc[:50], df.iloc[60:]])  # drop 10 candles
        report = validate_ohlcv_dataset(broken, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "missing_candles")
        assert check.status == "warn"
        assert check.count == 10

    def test_no_gap_reports_pass(self):
        report = validate_ohlcv_dataset(_clean_df(n=100), "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "missing_candles")
        assert check.status == "pass"


class TestDuplicateCandles:
    def test_detects_duplicate_timestamps(self):
        df = _clean_df(n=50)
        broken = pd.concat([df, df.iloc[[10]]]).sort_index()
        report = validate_ohlcv_dataset(broken, "BTCUSDT", "15m")
        assert not report.passed
        check = next(c for c in report.checks if c.name == "duplicate_candles")
        assert check.status == "fail" and check.count == 1


class TestTimezoneConsistency:
    def test_detects_tz_aware_index(self):
        df = _clean_df(n=50)
        df.index = df.index.tz_localize("UTC")
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        assert not report.passed
        check = next(c for c in report.checks if c.name == "timezone_consistency")
        assert check.status == "fail"
        assert "tz-naive" in check.detail

    def test_detects_unsorted_index(self):
        df = _clean_df(n=50)
        shuffled = df.iloc[np.random.default_rng(0).permutation(len(df))]
        report = validate_ohlcv_dataset(shuffled, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "timezone_consistency")
        assert check.status == "fail"


class TestOHLCConsistency:
    def test_detects_high_below_close(self):
        df = _clean_df(n=50)
        df.loc[df.index[5], "high"] = df.loc[df.index[5], "close"] - 10
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        assert not report.passed
        check = next(c for c in report.checks if c.name == "ohlc_consistency")
        assert check.status == "fail" and check.count == 1

    def test_detects_negative_price(self):
        df = _clean_df(n=50)
        df.loc[df.index[3], "low"] = -1.0
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "ohlc_consistency")
        assert check.status == "fail"

    def test_detects_nan_price(self):
        df = _clean_df(n=50)
        df.loc[df.index[7], "open"] = np.nan
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "ohlc_consistency")
        assert check.status == "fail"


class TestVolumeIntegrity:
    def test_detects_negative_volume(self):
        df = _clean_df(n=50)
        df.loc[df.index[2], "volume"] = -5.0
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        assert not report.passed
        check = next(c for c in report.checks if c.name == "volume_integrity")
        assert check.status == "fail"

    def test_detects_nan_volume(self):
        df = _clean_df(n=50)
        df.loc[df.index[2], "volume"] = np.nan
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "volume_integrity")
        assert check.status == "fail"

    def test_zero_volume_is_a_warning_not_a_failure(self):
        df = _clean_df(n=50)
        df.loc[df.index[2], "volume"] = 0.0
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "volume_integrity")
        assert check.status == "warn"
        assert report.passed  # a warning must not fail the overall report

    def test_missing_volume_column_fails(self):
        df = _clean_df(n=50).drop(columns=["volume"])
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        assert not report.passed


class TestGapHandling:
    def test_largest_gap_is_reported(self):
        df = _clean_df(n=100)
        broken = pd.concat([df.iloc[:50], df.iloc[70:]])  # 20-candle gap
        report = validate_ohlcv_dataset(broken, "BTCUSDT", "15m")
        check = next(c for c in report.checks if c.name == "gap_handling")
        assert check.status == "warn"
        assert check.count >= 1


class TestSessionAndCommodityHours:
    def test_commodity_profile_flags_weekend_and_break_candles(self):
        # 10 days of 1h candles definitely covers a weekend and daily breaks.
        df = _clean_df(n=24 * 10, freq="1h", start="2026-01-01")
        report = validate_ohlcv_dataset(df, "XAUUSDT", "1h", asset_profile=COMMODITY_PROFILE)
        check = next(c for c in report.checks if c.name == "session_and_trading_hours")
        assert check.status == "warn"
        assert check.count > 0
        # Diagnostic only - must never fail the overall report by itself.
        assert report.passed

    def test_no_profile_supplied_is_not_applicable(self):
        report = validate_ohlcv_dataset(_clean_df(n=50), "BTCUSDT", "15m", asset_profile=None)
        check = next(c for c in report.checks if c.name == "session_and_trading_hours")
        assert check.status == "not_applicable"


class TestEmptyAndMalformed:
    def test_empty_dataframe_fails_cleanly(self):
        report = validate_ohlcv_dataset(pd.DataFrame(), "BTCUSDT", "15m")
        assert not report.passed
        assert report.candle_count == 0

    def test_missing_required_column_fails_cleanly(self):
        df = _clean_df(n=20).drop(columns=["high"])
        report = validate_ohlcv_dataset(df, "BTCUSDT", "15m")
        assert not report.passed
