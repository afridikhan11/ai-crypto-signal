"""Displacement detection.

Displacement is an aggressive, impulsive move that reprices the market and
leaves an imbalance (FVG) behind. In ICT terms it signals a *Change in State
of Delivery* (CISD) — the moment institutional order-flow flips direction. A
valid ICT entry is a retracement into the imbalance left by displacement.

A displacement leg gives us the swing (origin → extreme) from which the
Optimal Trade Entry (OTE) retracement is measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class DisplacementType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class DisplacementLeg:
    type: DisplacementType
    index: int              # index of the displacement candle
    origin: float           # start of the impulse leg (swing before the move)
    extreme: float          # furthest point reached by the impulse
    body_atr: float         # displacement candle body measured in ATR units
    has_fvg: bool
    fvg_top: Optional[float]
    fvg_bottom: Optional[float]


class DisplacementDetector:
    def __init__(
        self,
        df: pd.DataFrame,
        atr: pd.Series,
        body_atr_mult: float = 1.5,
        origin_window: int = 5,
    ):
        self.df = df
        self.atr = atr
        self.body_atr_mult = body_atr_mult
        self.origin_window = origin_window

    def _body_atr(self, i: int) -> float:
        atr_val = self.atr.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            return 0.0
        row = self.df.iloc[i]
        return abs(row["close"] - row["open"]) / atr_val

    def find_latest(self, direction: DisplacementType) -> Optional[DisplacementLeg]:
        """Return the most recent displacement leg in ``direction`` (or None).

        We scan from the most recent *closed* candle backwards so the freshest
        institutional footprint wins.
        """
        n = len(self.df)
        if n < self.origin_window + 3:
            return None

        highs = self.df["high"].values
        lows = self.df["low"].values
        opens = self.df["open"].values
        closes = self.df["close"].values

        # Leave one candle on each side so an FVG (i-1, i, i+1) can form.
        for i in range(n - 2, self.origin_window, -1):
            body_atr = self._body_atr(i)
            if body_atr < self.body_atr_mult:
                continue

            if direction == DisplacementType.BULLISH:
                if closes[i] <= opens[i]:
                    continue
                has_fvg = highs[i - 1] < lows[i + 1]
                fvg_top = lows[i + 1] if has_fvg else None
                fvg_bottom = highs[i - 1] if has_fvg else None
                origin = float(np.min(lows[i - self.origin_window:i + 1]))
                extreme = float(np.max(highs[i:n]))
            else:
                if closes[i] >= opens[i]:
                    continue
                has_fvg = lows[i - 1] > highs[i + 1]
                fvg_top = lows[i - 1] if has_fvg else None
                fvg_bottom = highs[i + 1] if has_fvg else None
                origin = float(np.max(highs[i - self.origin_window:i + 1]))
                extreme = float(np.min(lows[i:n]))

            return DisplacementLeg(
                type=direction,
                index=i,
                origin=origin,
                extreme=extreme,
                body_atr=round(body_atr, 2),
                has_fvg=has_fvg,
                fvg_top=fvg_top,
                fvg_bottom=fvg_bottom,
            )
        return None
