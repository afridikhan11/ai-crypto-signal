"""
Correlation risk warnings.

Two signals that are both LONG (or both SHORT) on symbols that move
together (e.g. most alts vs BTC) aren't really two independent bets - if
the shared underlying driver moves against you, both lose together. This
flags that so a trader can see it's concentrated/stacked risk, not
diversification, before acting on both.

Computed from REAL recent price correlation (Pearson correlation of 15m
pct-change returns over the last ~200 candles, already cached by the live
data manager - no extra API calls needed), not a hand-picked "these coins
are related" list, since which coins move together changes over time.

Deliberately advisory only - this never blocks or filters signal
generation (that's a settled decision from earlier in the project: no
daily quota / no silently dropping signals). It only adds a warning field
to signals that are already being shown.
"""
from __future__ import annotations

from itertools import combinations
from typing import Dict, Optional, Sequence

import pandas as pd
from loguru import logger

CORRELATION_THRESHOLD = 0.7
CORRELATION_LOOKBACK = 200
MIN_ALIGNED_SAMPLES = 30


def _pct_returns(data_manager, symbol: str) -> Optional[pd.Series]:
    try:
        df = data_manager.get_dataframe(symbol, "15m", limit=CORRELATION_LOOKBACK)
    except Exception as e:
        logger.warning(f"Correlation check: could not load candles for {symbol}: {e}")
        return None
    if len(df) < MIN_ALIGNED_SAMPLES:
        return None
    return df["close"].pct_change().dropna()


def attach_correlation_warnings(signals: Sequence, data_manager) -> None:
    """
    Mutates each ACTIVE signal's `correlation_warning` field in place.
    `signals` is a sequence of SignalResponse-like objects with
    `.status`, `.direction`, `.coin_symbol`, and a settable
    `.correlation_warning` attribute.
    """
    active = [s for s in signals if s.status == "ACTIVE"]
    if len(active) < 2 or data_manager is None:
        return

    returns_cache: Dict[str, Optional[pd.Series]] = {}
    for s in active:
        if s.coin_symbol not in returns_cache:
            returns_cache[s.coin_symbol] = _pct_returns(data_manager, s.coin_symbol)

    for a, b in combinations(active, 2):
        if a.coin_symbol == b.coin_symbol or a.direction != b.direction:
            continue  # opposite-direction exposure isn't "stacked" the same way

        ra, rb = returns_cache.get(a.coin_symbol), returns_cache.get(b.coin_symbol)
        if ra is None or rb is None:
            continue

        aligned = pd.concat([ra, rb], axis=1, join="inner")
        if len(aligned) < MIN_ALIGNED_SAMPLES:
            continue

        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if corr is None or pd.isna(corr) or corr < CORRELATION_THRESHOLD:
            continue

        a.correlation_warning = (
            f"High correlation with {b.coin_symbol} ({corr:.2f}) - same-direction stacked exposure"
        )
        b.correlation_warning = (
            f"High correlation with {a.coin_symbol} ({corr:.2f}) - same-direction stacked exposure"
        )
