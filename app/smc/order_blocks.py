import pandas as pd
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class BlockType(Enum):
    BULLISH_OB = "bullish_ob"
    BEARISH_OB = "bearish_ob"
    BREAKER = "breaker"
    MITIGATION = "mitigation"


@dataclass
class OrderBlock:
    timestamp: pd.Timestamp
    high: float
    low: float
    type: BlockType
    mitigated: bool = False


class OrderBlockDetector:
    def __init__(self, df: pd.DataFrame, body_ratio_threshold: float = 0.6):
        self.df = df
        self.body_ratio_threshold = body_ratio_threshold

    def _is_momentum_candle(self, idx: int, direction: str) -> bool:
        if idx < 0 or idx >= len(self.df):
            return False
        row = self.df.iloc[idx]
        body = abs(row["close"] - row["open"])
        total_range = row["high"] - row["low"]
        if total_range == 0:
            return False
        if body / total_range < self.body_ratio_threshold:
            return False
        if direction == "up":
            return row["close"] > row["open"]
        else:
            return row["close"] < row["open"]

    def _is_ob_broken(self, ob: OrderBlock) -> bool:
        """FIXED: Check if subsequent price action has invalidated the OB."""
        ob_idx = self.df.index.get_loc(ob.timestamp)
        if ob_idx >= len(self.df) - 1:
            return False
        subsequent_closes = self.df.iloc[ob_idx+1:]["close"]
        if ob.type == BlockType.BULLISH_OB:
            # If price ever closed below the OB low, it's broken
            return any(subsequent_closes < ob.low)
        else:  # BEARISH_OB
            return any(subsequent_closes > ob.high)

    def find_bullish_order_block(self) -> Optional[OrderBlock]:
        for i in range(len(self.df)-2, 1, -1):
            if self._is_momentum_candle(i, "down"):
                if self._is_momentum_candle(i+1, "up"):
                    ob = OrderBlock(
                        timestamp=self.df.index[i],
                        high=self.df.iloc[i]["high"],
                        low=self.df.iloc[i]["low"],
                        type=BlockType.BULLISH_OB
                    )
                    if not self._is_ob_broken(ob):
                        return ob
        return None

    def find_bearish_order_block(self) -> Optional[OrderBlock]:
        for i in range(len(self.df)-2, 1, -1):
            if self._is_momentum_candle(i, "up"):
                if self._is_momentum_candle(i+1, "down"):
                    ob = OrderBlock(
                        timestamp=self.df.index[i],
                        high=self.df.iloc[i]["high"],
                        low=self.df.iloc[i]["low"],
                        type=BlockType.BEARISH_OB
                    )
                    if not self._is_ob_broken(ob):
                        return ob
        return None

    def check_mitigation(self, ob: OrderBlock, current_price: float) -> bool:
        return ob.low <= current_price <= ob.high

    def detect_breaker_block(self, ob: OrderBlock, current_price: float) -> bool:
        if ob.type == BlockType.BULLISH_OB:
            return current_price < ob.low
        else:
            return current_price > ob.high
