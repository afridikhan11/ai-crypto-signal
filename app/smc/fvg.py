import pandas as pd
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum


class FVGType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class FairValueGap:
    timestamp: pd.Timestamp
    top: float
    bottom: float
    type: FVGType
    filled: bool = False


class FVGDetector:
    def __init__(self, df: pd.DataFrame, min_gap_ratio: float = 0.0005):
        self.df = df
        self.min_gap_ratio = min_gap_ratio

    def detect_fvg(self) -> List[FairValueGap]:
        fvgs = []
        for i in range(1, len(self.df)):
            prev = self.df.iloc[i-1]
            curr = self.df.iloc[i]
            if curr["low"] > prev["high"]:
                gap = curr["low"] - prev["high"]
                avg_price = (curr["low"] + prev["high"]) / 2
                if avg_price > 0 and gap / avg_price >= self.min_gap_ratio:
                    fvgs.append(FairValueGap(
                        timestamp=self.df.index[i],
                        top=curr["low"],
                        bottom=prev["high"],
                        type=FVGType.BULLISH
                    ))
            elif curr["high"] < prev["low"]:
                gap = prev["low"] - curr["high"]
                avg_price = (prev["low"] + curr["high"]) / 2
                if avg_price > 0 and gap / avg_price >= self.min_gap_ratio:
                    fvgs.append(FairValueGap(
                        timestamp=self.df.index[i],
                        top=prev["low"],
                        bottom=curr["high"],
                        type=FVGType.BEARISH
                    ))
        return fvgs

    def is_fvg_filled(self, fvg: FairValueGap, current_price: float) -> bool:
        if fvg.type == FVGType.BULLISH:
            return current_price <= fvg.top and current_price >= fvg.bottom
        else:
            return current_price >= fvg.bottom and current_price <= fvg.top

    def detect_inverse_fvg(self) -> List[FairValueGap]:
        fvgs = self.detect_fvg()
        current = self.df["close"].iloc[-1]
        inverse = []
        for f in fvgs:
            if self.is_fvg_filled(f, current):
                inverse.append(FairValueGap(
                    timestamp=f.timestamp,
                    top=f.top,
                    bottom=f.bottom,
                    type=FVGType.BULLISH if f.type == FVGType.BEARISH else FVGType.BEARISH,
                    filled=True
                ))
        return inverse
