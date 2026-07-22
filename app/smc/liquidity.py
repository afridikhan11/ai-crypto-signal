from typing import List, Optional
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from .market_structure import SwingPoint, SwingType


class LiquidityType(Enum):
    BUYSIDE = "buyside"
    SELLSIDE = "sellside"


@dataclass
class LiquidityLevel:
    price: float
    type: LiquidityType
    swing_points: List[SwingPoint]
    swept: bool = False


class LiquidityDetector:
    def __init__(self, df: pd.DataFrame, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
                 tolerance: float = 0.0005):
        self.df = df
        self.swing_highs = swing_highs
        self.swing_lows = swing_lows
        self.tolerance = tolerance

    def detect_equal_highs(self) -> List[LiquidityLevel]:
        if not self.swing_highs:
            return []
        levels = []
        used = set()
        for i, sh1 in enumerate(self.swing_highs):
            if i in used:
                continue
            cluster = [sh1]
            for j, sh2 in enumerate(self.swing_highs[i+1:], start=i+1):
                if j in used:
                    continue
                avg_price = (sh1.price + sh2.price) / 2
                if abs(sh1.price - sh2.price) / avg_price <= self.tolerance:
                    cluster.append(sh2)
                    used.add(j)
            if len(cluster) >= 2:
                used.add(i)
                avg_price = sum(p.price for p in cluster) / len(cluster)
                levels.append(LiquidityLevel(
                    price=avg_price,
                    type=LiquidityType.BUYSIDE,
                    swing_points=cluster
                ))
        return levels

    def detect_equal_lows(self) -> List[LiquidityLevel]:
        if not self.swing_lows:
            return []
        levels = []
        used = set()
        for i, sl1 in enumerate(self.swing_lows):
            if i in used:
                continue
            cluster = [sl1]
            for j, sl2 in enumerate(self.swing_lows[i+1:], start=i+1):
                if j in used:
                    continue
                avg_price = (sl1.price + sl2.price) / 2
                if abs(sl1.price - sl2.price) / avg_price <= self.tolerance:
                    cluster.append(sl2)
                    used.add(j)
            if len(cluster) >= 2:
                used.add(i)
                avg_price = sum(p.price for p in cluster) / len(cluster)
                levels.append(LiquidityLevel(
                    price=avg_price,
                    type=LiquidityType.SELLSIDE,
                    swing_points=cluster
                ))
        return levels

    def detect_liquidity_sweeps(self, levels: List[LiquidityLevel]) -> List[LiquidityLevel]:
        for level in levels:
            if level.swept:
                continue
            recent_high = self.df["high"].iloc[-1]
            recent_low = self.df["low"].iloc[-1]
            if level.type == LiquidityType.BUYSIDE and recent_high > level.price:
                level.swept = True
            elif level.type == LiquidityType.SELLSIDE and recent_low < level.price:
                level.swept = True
        return levels
