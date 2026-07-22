import pandas as pd
from typing import Optional, Tuple


class SupplyDemandZones:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.range_high: Optional[float] = None
        self.range_low: Optional[float] = None

    def calculate_recent_range(self, lookback: int = 50):
        if len(self.df) < lookback:
            return
        recent = self.df.iloc[-lookback:]
        self.range_high = recent["high"].max()
        self.range_low = recent["low"].min()

    def get_zone(self, price: float) -> str:
        if self.range_high is None or self.range_low is None:
            return "unknown"
        range_size = self.range_high - self.range_low
        if range_size == 0:
            return "equilibrium"
        fib_0_5 = self.range_low + 0.5 * range_size
        fib_0_618 = self.range_low + 0.618 * range_size
        fib_0_79 = self.range_low + 0.79 * range_size
        if price >= fib_0_79:
            return "premium"
        elif price <= fib_0_5:
            return "discount"
        else:
            return "equilibrium"
