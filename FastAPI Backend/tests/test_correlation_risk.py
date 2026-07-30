from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services.correlation_risk import attach_correlation_warnings


class _FakeDataManager:
    def __init__(self, series):
        self._series = series

    def get_dataframe(self, symbol, interval, limit=200):
        return pd.DataFrame({"close": self._series[symbol]})


def _signal(status, direction, symbol):
    return SimpleNamespace(status=status, direction=direction, coin_symbol=symbol, correlation_warning=None)


@pytest.fixture
def price_series():
    rng = np.random.default_rng(42)
    n = 250
    base = np.cumsum(rng.normal(0, 1, n)) + 100
    noise = rng.normal(0, 0.05, n)
    return {
        "BTCUSDT": base,
        "ETHUSDT": base + noise,  # near-perfect correlation with BTC
        "DOGEUSDT": np.cumsum(rng.normal(0, 1, n)) + 50,  # independent
    }


def test_flags_correlated_same_direction_signals(price_series):
    dm = _FakeDataManager(price_series)
    s1 = _signal("ACTIVE", "LONG", "BTCUSDT")
    s2 = _signal("ACTIVE", "LONG", "ETHUSDT")

    attach_correlation_warnings([s1, s2], dm)

    assert s1.correlation_warning is not None
    assert "ETHUSDT" in s1.correlation_warning
    assert s2.correlation_warning is not None


def test_does_not_flag_independent_symbols(price_series):
    dm = _FakeDataManager(price_series)
    s1 = _signal("ACTIVE", "LONG", "BTCUSDT")
    s3 = _signal("ACTIVE", "LONG", "DOGEUSDT")

    attach_correlation_warnings([s1, s3], dm)

    assert s3.correlation_warning is None


def test_does_not_flag_opposite_direction(price_series):
    dm = _FakeDataManager(price_series)
    s1 = _signal("ACTIVE", "LONG", "BTCUSDT")
    s4 = _signal("ACTIVE", "SHORT", "ETHUSDT")

    attach_correlation_warnings([s1, s4], dm)

    assert s4.correlation_warning is None


def test_ignores_non_active_signals(price_series):
    dm = _FakeDataManager(price_series)
    s1 = _signal("ACTIVE", "LONG", "BTCUSDT")
    s5 = _signal("STOPPED", "LONG", "ETHUSDT")

    attach_correlation_warnings([s1, s5], dm)

    assert s5.correlation_warning is None
