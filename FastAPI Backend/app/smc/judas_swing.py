"""
Judas Swing - the false move at a session open.

Near a major session open (London, New York) price frequently makes a first
push that RUNS liquidity in one direction - taking out the prior range's high
or low - only to reverse and deliver the session's real move the other way.
That first, deceptive push is the "Judas" (the betrayer): it traps breakout
traders and fuels the genuine move.

Detection, per session:
  * a pre-session reference range (e.g. the Asian range before London),
  * an opening window in which price sweeps that range's high or low,
  * and then closes back inside the range (rejecting the sweep).

A sweep of the range HIGH that is rejected is a *bearish* Judas (false up move
-> true bias down); a swept-and-rejected range LOW is a *bullish* Judas.

Pure and stateless-per-call. UTC timestamp convention as elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class JudasSession(str, Enum):
    LONDON = "london"
    NEW_YORK = "new_york"


@dataclass(frozen=True)
class _SessionSpec:
    range_start_hour: int
    range_end_hour: int      # == session open
    window_end_hour: int     # end of the opening window we look for the Judas in


# UTC windows, consistent with session_engine's fixed-UTC model.
_SPECS = {
    JudasSession.LONDON: _SessionSpec(0, 7, 10),      # Asian range -> London open
    JudasSession.NEW_YORK: _SessionSpec(7, 12, 15),   # London range -> NY open
}


@dataclass
class JudasSwing:
    session: JudasSession
    false_direction: str      # direction of the deceptive push ("bullish"/"bearish")
    bias_direction: str       # the real, opposite bias
    swept_level: float        # the range extreme that was raided
    reversal_price: float     # last close (back inside the range)


class JudasSwingEngine:
    def __init__(self, specs=None):
        self._specs = dict(_SPECS) if specs is None else dict(specs)

    @staticmethod
    def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(df.index)
        return idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")

    def detect(self, df: pd.DataFrame, session: JudasSession) -> Optional[JudasSwing]:
        if df is None or df.empty:
            return None
        spec = self._specs[session]

        idx = self._utc_index(df)
        today = idx[-1].normalize()
        hours = idx.hour + idx.minute / 60.0

        range_mask = (
            (idx >= today + pd.Timedelta(hours=spec.range_start_hour))
            & (idx < today + pd.Timedelta(hours=spec.range_end_hour))
        )
        window_mask = (
            (idx >= today + pd.Timedelta(hours=spec.range_end_hour))
            & (idx < today + pd.Timedelta(hours=spec.window_end_hour))
        )
        if not range_mask.any() or not window_mask.any():
            return None

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)

        range_high = highs[range_mask].max()
        range_low = lows[range_mask].min()

        window_high = highs[window_mask].max()
        window_low = lows[window_mask].min()
        last_close = float(closes[-1])

        # Bearish Judas: opening window swept the range HIGH, price now back
        # below it (the up-push was the betrayal; real bias is down).
        swept_high = window_high > range_high and last_close < range_high
        # Bullish Judas: swept the range LOW, price now back above it.
        swept_low = window_low < range_low and last_close > range_low

        # If both somehow trigger, prefer the more recent extreme by taking the
        # one whose sweep is larger relative to the range (rare; deterministic).
        if swept_high and swept_low:
            over = (window_high - range_high)
            under = (range_low - window_low)
            swept_high, swept_low = (over >= under, over < under)

        if swept_high:
            return JudasSwing(session, "bullish", "bearish", float(range_high), last_close)
        if swept_low:
            return JudasSwing(session, "bearish", "bullish", float(range_low), last_close)
        return None
