"""Tests for the Latest-ICT confluence aggregation layer."""

import numpy as np
import pandas as pd

from app.smc.fvg import FairValueGap, FVGType
from app.smc.latest_ict_confluence import (
    LatestICTConfluenceEngine, LatestICTConfluenceReport,
)


def _df(days=3, start="2026-01-05 00:00", base=100.0):
    n = days * 24
    idx = pd.date_range(start=start, periods=n, freq="1h")
    t = np.arange(n)
    close = base + 4 * np.sin(t / 8.0) + t * 0.05
    return pd.DataFrame(
        {"open": close, "high": close + 0.6, "low": close - 0.6, "close": close, "volume": 1000.0},
        index=idx,
    )


def test_empty_df_returns_empty_report():
    report = LatestICTConfluenceEngine().analyze(pd.DataFrame())
    assert isinstance(report, LatestICTConfluenceReport)
    assert report.signals == [] and report.notes == []
    assert report.net_bias is None


def test_runs_and_returns_report_with_key_level_notes():
    report = LatestICTConfluenceEngine().analyze(_df(), direction="bullish")
    assert isinstance(report, LatestICTConfluenceReport)
    # Key-level draw notes should be present with 3+ days of history.
    assert any("Draw" in n for n in report.notes)


def test_bias_counting_and_alignment():
    report = LatestICTConfluenceEngine().analyze(_df(), direction="bullish")
    assert report.bullish_count >= 0 and report.bearish_count >= 0
    assert report.aligned_count("bullish") == report.bullish_count
    assert report.aligned_count(None) == 0
    if report.bullish_count > report.bearish_count:
        assert report.net_bias == "bullish"


def test_accepts_fvgs_and_order_blocks_without_error():
    df = _df()
    price = float(df["close"].iloc[-1])
    fvgs = [
        FairValueGap(timestamp=df.index[-3], top=price + 0.5, bottom=price + 0.1, type=FVGType.BULLISH, filled=False),
        FairValueGap(timestamp=df.index[-2], top=price + 0.4, bottom=price - 0.1, type=FVGType.BEARISH, filled=False),
    ]
    report = LatestICTConfluenceEngine().analyze(df, direction="bullish", fvgs=fvgs, order_blocks=[])
    assert isinstance(report, LatestICTConfluenceReport)
