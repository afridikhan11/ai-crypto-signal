"""Shared fixtures. No network, no clock, no phone."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from gold_signals.candles import Candle
from gold_signals.config import Settings
from gold_signals.engulfing import Bar

BASE_TIME = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> Settings:
    """The owner's numbers, with the console channel so nothing is sent."""
    return Settings(
        instrument="XAU_USD",
        granularity="M30",
        pip_size=0.10,
        entry_pips=30.0,
        stop_pips=90.0,
        rr=2.0,
        max_bars_since_break=3,
        oanda_token="test-token",
        channel="console",
        db_path=":memory:",
    )


# ----------------------------------------------------------------------
# Bars. A green candle engulfed by a red one, broken upward = LONG.
# ----------------------------------------------------------------------
BEARISH_A = Bar(open=100.0, high=111.0, low=99.0, close=110.0)     # green
BEARISH_B = Bar(open=112.0, high=113.0, low=97.0, close=98.0)      # red, covers A
BREAK_UP = Bar(open=99.0, high=116.0, low=98.0, close=115.0)       # closes above both

BULLISH_A = Bar(open=110.0, high=111.0, low=99.0, close=100.0)     # red
BULLISH_B = Bar(open=98.0, high=113.0, low=97.0, close=112.0)      # green, covers A
BREAK_DOWN = Bar(open=112.0, high=113.0, low=94.0, close=95.0)     # closes below both


def long_setup_bars() -> List[Bar]:
    return [BEARISH_A, BEARISH_B, BREAK_UP]


def short_setup_bars() -> List[Bar]:
    return [BULLISH_A, BULLISH_B, BREAK_DOWN]


def quiet_green_bars(count: int, start: float) -> List[Bar]:
    """Candles that cannot form a pattern: all green, so no candle can engulf
    another (engulfing needs opposite colours), and each range sits above the
    last so nothing covers anything."""
    out = []
    price = start
    for _ in range(count):
        out.append(Bar(open=price, high=price + 1.0, low=price - 0.2, close=price + 0.5))
        price += 0.6
    return out


def to_candles(bars: List[Bar], forming: Optional[Bar] = None,
               start: datetime = BASE_TIME) -> List[Candle]:
    """Bars -> feed candles, 30 minutes apart, all complete. `forming` is
    appended as the still-open candle the feed would return last."""
    candles = [
        Candle(time=start + timedelta(minutes=30 * i), open=b.open, high=b.high,
               low=b.low, close=b.close, complete=True)
        for i, b in enumerate(bars)
    ]
    if forming is not None:
        candles.append(Candle(
            time=start + timedelta(minutes=30 * len(bars)), open=forming.open,
            high=forming.high, low=forming.low, close=forming.close, complete=False,
        ))
    return candles


class FakeFeed:
    """Stands in for OandaCandles. Records what it was asked for."""

    def __init__(self, candles: List[Candle], error: Optional[Exception] = None) -> None:
        self.candles = candles
        self.error = error
        self.calls: List[tuple] = []

    def fetch(self, instrument: str, granularity: str = "M30", count: int = 120) -> List[Candle]:
        self.calls.append((instrument, granularity, count))
        if self.error is not None:
            raise self.error
        return list(self.candles)

    def close(self) -> None:
        pass


class FakeSender:
    name = "fake"

    def __init__(self, error: Optional[Exception] = None) -> None:
        self.sent: List[str] = []
        self.error = error

    def send(self, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(text)


@pytest.fixture
def store():
    from gold_signals.store import SignalStore

    s = SignalStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def with_settings(settings):
    def _make(**overrides) -> Settings:
        return replace(settings, **overrides)

    return _make
