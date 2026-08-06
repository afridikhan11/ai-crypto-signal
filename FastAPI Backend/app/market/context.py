"""Real-time market context.

Replaces the previously hard-coded ``btc_trend`` / ``funding_rate`` /
``volatility`` values with live, data-driven readings:

* **BTC trend** — HTF bias of the market leader (BTCUSDT). The whole market
  tends to follow BTC, so this is a broad risk-on/risk-off filter.
* **Funding rate** — pulled from Binance; extreme funding warns of crowded
  positioning (contrarian filter).
* **Volatility** — ATR% of the entry timeframe classified against its own
  recent distribution (avoids trading chop / news spikes blindly).
"""

from __future__ import annotations

from typing import Dict

import pandas as pd
from ta.volatility import AverageTrueRange

from app.market.universe import MARKET_LEADER
from app.strategy.bias import compute_bias


class MarketContext:
    def __init__(self, data_manager, htf_order):
        self.data_manager = data_manager
        self.htf_order = htf_order

    # ------------------------------------------------------------------
    def btc_trend(self) -> str:
        """Return 'up' | 'down' | 'neutral' from BTC higher-timeframe bias."""
        frames: Dict[str, pd.DataFrame] = {}
        for tf in self.htf_order:
            frames[tf] = self.data_manager.get_dataframe(MARKET_LEADER, tf, limit=300)
        if all(df.empty for df in frames.values()):
            return "neutral"
        bias = compute_bias(frames, self.htf_order)
        return {"LONG": "up", "SHORT": "down"}.get(bias.direction, "neutral")

    # ------------------------------------------------------------------
    async def funding_rate(self, symbol: str) -> float:
        return await self.data_manager.fetch_funding_rate(symbol)

    # ------------------------------------------------------------------
    @staticmethod
    def classify_volatility(df: pd.DataFrame, window: int = 14) -> str:
        """Classify current volatility as 'low' | 'normal' | 'high'.

        Uses ATR% (ATR / price) compared against its own recent percentiles so
        the reading is symbol-agnostic.
        """
        if df is None or df.empty or len(df) < window + 20:
            return "normal"
        atr = AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"], window=window
        ).average_true_range()
        atr_pct = (atr / df["close"]).dropna()
        if atr_pct.empty:
            return "normal"
        current = atr_pct.iloc[-1]
        recent = atr_pct.iloc[-100:]
        high_thr = recent.quantile(0.80)
        low_thr = recent.quantile(0.30)
        if current >= high_thr:
            return "high"
        if current <= low_thr:
            return "low"
        return "normal"
