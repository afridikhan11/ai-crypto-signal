from typing import List, Tuple, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum


class SwingType(Enum):
    HH = "Higher High"
    HL = "Higher Low"
    LH = "Lower High"
    LL = "Lower Low"


@dataclass
class SwingPoint:
    timestamp: pd.Timestamp
    price: float
    type: SwingType
    index: int


@dataclass
class StructureBreak:
    timestamp: pd.Timestamp
    type: str  # "BOS" or "CHoCH"
    direction: str  # "bullish" or "bearish"
    broken_swing: SwingPoint
    level: float


class MarketStructure:
    def __init__(self, df: pd.DataFrame, pivot_window: int = 5):
        self.df = df
        self.pivot_window = pivot_window
        self.swing_highs: List[SwingPoint] = []
        self.swing_lows: List[SwingPoint] = []
        self._detect_swings()

    def _detect_swings(self):
        highs = self.df["high"].values
        lows = self.df["low"].values
        n = len(self.df)
        if n < 2 * self.pivot_window + 1:
            return
        for i in range(self.pivot_window, n - self.pivot_window):
            if highs[i] == max(highs[i - self.pivot_window:i + self.pivot_window + 1]):
                self.swing_highs.append(SwingPoint(
                    timestamp=self.df.index[i],
                    price=highs[i],
                    type=SwingType.HH,
                    index=i
                ))
            if lows[i] == min(lows[i - self.pivot_window:i + self.pivot_window + 1]):
                self.swing_lows.append(SwingPoint(
                    timestamp=self.df.index[i],
                    price=lows[i],
                    type=SwingType.HL,
                    index=i
                ))
        self._classify_swings()

    def _classify_swings(self):
        prev_high = None
        for sw in self.swing_highs:
            if prev_high is not None:
                sw.type = SwingType.HH if sw.price > prev_high.price else SwingType.LH
            else:
                sw.type = SwingType.HH
            prev_high = sw
        prev_low = None
        for sw in self.swing_lows:
            if prev_low is not None:
                sw.type = SwingType.HL if sw.price > prev_low.price else SwingType.LL
            else:
                sw.type = SwingType.HL
            prev_low = sw

    def detect_bos_choch(self) -> List[StructureBreak]:
        breaks = []
        if not self.swing_highs or not self.swing_lows:
            return breaks
        recent_highs = self.swing_highs[-3:]
        recent_lows = self.swing_lows[-3:]
        close = self.df["close"].iloc[-1]
        for sw in recent_highs:
            if sw.type == SwingType.LH and close > sw.price:
                breaks.append(StructureBreak(
                    timestamp=self.df.index[-1],
                    type="BOS",
                    direction="bullish",
                    broken_swing=sw,
                    level=sw.price
                ))
                break
        for sw in recent_lows:
            if sw.type == SwingType.HL and close < sw.price:
                breaks.append(StructureBreak(
                    timestamp=self.df.index[-1],
                    type="BOS",
                    direction="bearish",
                    broken_swing=sw,
                    level=sw.price
                ))
                break
        if len(self.swing_lows) >= 2 and len(self.swing_highs) >= 2:
            last_low = self.swing_lows[-1]
            prev_low = self.swing_lows[-2]
            last_high = self.swing_highs[-1]
            prev_high = self.swing_highs[-2]
            if (prev_low.type == SwingType.HL and last_low.type == SwingType.HL and
                    prev_high.type == SwingType.HH and last_high.type == SwingType.HH):
                if close < last_low.price:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[-1],
                        type="CHoCH",
                        direction="bearish",
                        broken_swing=last_low,
                        level=last_low.price
                    ))
            if (prev_high.type == SwingType.LH and last_high.type == SwingType.LH and
                    prev_low.type == SwingType.LL and last_low.type == SwingType.LL):
                if close > last_high.price:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[-1],
                        type="CHoCH",
                        direction="bullish",
                        broken_swing=last_high,
                        level=last_high.price
                    ))
        return breaks
