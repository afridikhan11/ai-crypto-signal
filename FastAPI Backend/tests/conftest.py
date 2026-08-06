"""Synthetic OHLC builders for deterministic strategy tests."""

from __future__ import annotations

import math
from typing import List, Optional

import pandas as pd
import pytest


def build_df(closes: List[float], start: str = "2024-01-01 00:00:00",
             freq: str = "15min", wick: float = 0.05,
             volume: float = 1000.0) -> pd.DataFrame:
    """Build a UTC-indexed OHLC frame from a list of closes.

    Opens follow the previous close; highs/lows add a small symmetric wick so
    every candle is well-formed.
    """
    idx = pd.date_range(start=start, periods=len(closes), freq=freq, tz="UTC")
    rows = []
    prev = closes[0]
    for c in closes:
        o = prev
        hi = max(o, c) + wick
        lo = min(o, c) - wick
        rows.append({"open": o, "high": hi, "low": lo, "close": c, "volume": volume})
        prev = c
    return pd.DataFrame(rows, index=idx)


def uptrend_closes(n: int = 250, base: float = 100.0, slope: float = 0.15,
                   amp: float = 2.0, period: float = 6.0) -> List[float]:
    """Rising price with oscillation so swing highs/lows form (HH/HL)."""
    return [base + i * slope + amp * math.sin(i / period) for i in range(n)]


def downtrend_closes(n: int = 250, base: float = 400.0, slope: float = 0.15,
                     amp: float = 2.0, period: float = 6.0) -> List[float]:
    return [base - i * slope + amp * math.sin(i / period) for i in range(n)]


@pytest.fixture
def bullish_htf_frames():
    return {
        "4h": build_df(uptrend_closes(250), freq="4h"),
        "1h": build_df(uptrend_closes(250), freq="1h"),
    }


@pytest.fixture
def bearish_htf_frames():
    return {
        "4h": build_df(downtrend_closes(250), freq="4h"),
        "1h": build_df(downtrend_closes(250), freq="1h"),
    }


def bullish_ltf_with_setup() -> pd.DataFrame:
    """Uptrend → sweep of a swing low → bullish displacement → OTE retrace."""
    closes = uptrend_closes(230, base=100.0, slope=0.12, amp=1.5, period=5.0)
    # Sweep: sharp dip below recent lows then reclaim.
    swing_low_area = min(closes[-20:])
    closes.append(swing_low_area - 1.2)          # raid sell-side liquidity
    closes.append(swing_low_area + 0.5)          # reclaim
    # Displacement: strong impulse up (big body) leaving an FVG.
    base = closes[-1]
    closes.append(base + 6.0)                    # displacement candle close
    extreme = closes[-1]
    # Retrace into OTE (~0.70 of the leg from reclaim->extreme).
    leg_low = swing_low_area + 0.5
    ote = extreme - (extreme - leg_low) * 0.70
    closes.append(ote)
    df = build_df(closes, start="2024-01-01 12:00:00", freq="15min", wick=0.1)
    return df
