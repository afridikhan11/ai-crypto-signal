import pandas as pd
import numpy as np
from typing import Optional
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from ta.volume import volume_weighted_average_price as vwap_fn


class ConfirmationIndicators:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._compute()

    def _compute(self):
        # EMAs
        self.ema20 = EMAIndicator(close=self.df["close"], window=20).ema_indicator()
        self.ema50 = EMAIndicator(close=self.df["close"], window=50).ema_indicator()
        self.ema200 = EMAIndicator(close=self.df["close"], window=200).ema_indicator()

        # VWAP
        self.vwap = vwap_fn(self.df["high"], self.df["low"], self.df["close"], self.df["volume"])

        # Volume
        self.volume_sma = self.df["volume"].rolling(window=20).mean()
        self.volume_spike = self.df["volume"] > (self.volume_sma * 1.5)

        # ATR
        atr_ind = AverageTrueRange(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=14)
        self.atr = atr_ind.average_true_range()

        # RSI
        self.rsi = RSIIndicator(close=self.df["close"], window=14).rsi()

        # MACD
        macd = MACD(close=self.df["close"], window_slow=26, window_fast=12, window_sign=9)
        self.macd_line = macd.macd()
        self.macd_signal = macd.macd_signal()
        self.macd_hist = macd.macd_diff()

        # ADX
        adx_ind = ADXIndicator(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=14)
        self.adx = adx_ind.adx()
        self.plus_di = adx_ind.adx_pos()
        self.minus_di = adx_ind.adx_neg()

        # Supertrend (custom implementation)
        self.supertrend, self.supertrend_direction = self._calculate_supertrend()

    def _calculate_supertrend(self, period: int = 7, multiplier: float = 3.0):
        """Return (supertrend_line, direction_series) where direction = 1 for uptrend, -1 for downtrend."""
        high = self.df["high"]
        low = self.df["low"]
        close = self.df["close"]
        atr = self.atr

        # Basic Upper/Lower bands
        hl_avg = (high + low) / 2
        upper = hl_avg + multiplier * atr
        lower = hl_avg - multiplier * atr

        # Initialize SuperTrend arrays
        st = pd.Series(np.nan, index=self.df.index)
        direction = pd.Series(1, index=self.df.index)  # 1 = up, -1 = down

        for i in range(1, len(self.df)):
            if close.iloc[i] <= upper.iloc[i - 1]:
                upper.iloc[i] = min(upper.iloc[i], upper.iloc[i - 1])
            else:
                upper.iloc[i] = upper.iloc[i]
            if close.iloc[i] >= lower.iloc[i - 1]:
                lower.iloc[i] = max(lower.iloc[i], lower.iloc[i - 1])
            else:
                lower.iloc[i] = lower.iloc[i]

            # Determine trend direction
            if close.iloc[i] > upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif close.iloc[i] < lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            # Assign SuperTrend value
            if direction.iloc[i] == 1:
                st.iloc[i] = lower.iloc[i]
            else:
                st.iloc[i] = upper.iloc[i]

        # Fill the first value
        st.iloc[0] = lower.iloc[0]
        return st, direction

    def get_latest(self) -> Optional[dict]:
        """Return the most recent indicator values, or None if any NaN (except volume_spike)."""
        idx = -1
        vals = {
            "ema20": self.ema20.iloc[idx],
            "ema50": self.ema50.iloc[idx],
            "ema200": self.ema200.iloc[idx],
            "vwap": self.vwap.iloc[idx],
            "atr": self.atr.iloc[idx],
            "rsi": self.rsi.iloc[idx],
            "macd_line": self.macd_line.iloc[idx],
            "macd_signal": self.macd_signal.iloc[idx],
            "macd_hist": self.macd_hist.iloc[idx],
            "adx": self.adx.iloc[idx],
            "plus_di": self.plus_di.iloc[idx],
            "minus_di": self.minus_di.iloc[idx],
            "supertrend": self.supertrend.iloc[idx],
            "supertrend_dir": self.supertrend_direction.iloc[idx],
            "volume_spike": bool(self.volume_spike.iloc[idx]) if not pd.isna(self.volume_spike.iloc[idx]) else False,
        }
        # Check for NaN in all except volume_spike
        for k, v in vals.items():
            if k == "volume_spike":
                continue
            if pd.isna(v):
                return None
        return vals