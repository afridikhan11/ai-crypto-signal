import pandas as pd
import numpy as np
from typing import Optional
from ta.trend import EMAIndicator, MACD, ADXIndicator, CCIIndicator
from ta.momentum import RSIIndicator, StochRSIIndicator, WilliamsRIndicator
from ta.volatility import AverageTrueRange, BollingerBands, KeltnerChannel, DonchianChannel
from ta.volume import volume_weighted_average_price as vwap_fn, OnBalanceVolumeIndicator


class ConfirmationIndicators:
    def __init__(self, df: pd.DataFrame, volume_spike_multiplier: float = 1.5):
        """
        `volume_spike_multiplier` (default 1.5, unchanged from before
        2026-07-26) is how many times the 20-period average volume counts
        as a "spike" - now caller-supplied so each asset class's
        CalibrationProfile (see app/ai/calibration_profiles.py) can tune it
        independently instead of one hardcoded global value. Callers that
        don't pass it (ta_dashboard.py, market_scorer.py, token_scorer.py)
        keep getting the exact same 1.5x behavior as before.
        """
        self.df = df.copy()
        self.volume_spike_multiplier = volume_spike_multiplier
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
        self.volume_spike = self.df["volume"] > (self.volume_sma * self.volume_spike_multiplier)

        # ATR
        atr_ind = AverageTrueRange(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=14)
        self.atr = atr_ind.average_true_range()

        # RSI
        self.rsi = RSIIndicator(close=self.df["close"], window=14).rsi()

        # Momentum confirmation trio (Stochastic RSI, CCI, Williams %R) -
        # per the product ask, these are used ONLY as confirmation
        # (small additive bonuses in AIScorer, see confirmation_alignment)
        # alongside the RSI/ADX/EMA read above, never as a standalone
        # primary signal or a penalty.
        stoch_rsi = StochRSIIndicator(close=self.df["close"], window=14, smooth1=3, smooth2=3)
        self.stoch_rsi_k = stoch_rsi.stochrsi_k() * 100  # scale 0-1 -> 0-100 to match RSI's scale
        self.cci = CCIIndicator(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=20).cci()
        self.williams_r = WilliamsRIndicator(
            high=self.df["high"], low=self.df["low"], close=self.df["close"], lbp=14
        ).williams_r()

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

        # OBV (On-Balance Volume) - running total of volume signed by close
        # direction. Read as its trend (vs. its own short EMA) rather than
        # its raw level, same as every other trend read in this class -
        # OBV's absolute value is arbitrary/unbounded, only its slope means
        # anything.
        self.obv = OnBalanceVolumeIndicator(close=self.df["close"], volume=self.df["volume"]).on_balance_volume()
        self.obv_ema = self.obv.ewm(span=20, adjust=False).mean()

        # CVD (Cumulative Volume Delta) - real aggressor-side buy/sell
        # imbalance, built from Binance's own per-candle taker-buy-volume
        # field (see Candle.taker_buy_volume), not an estimate. Each
        # candle's delta = taker_buy_volume - taker_sell_volume, where
        # taker_sell_volume = volume - taker_buy_volume, so delta =
        # 2*taker_buy_volume - volume. Falls back to an all-zero (flat)
        # series if the caller's df doesn't carry the column yet (e.g. old
        # cached candles from before this field existed) instead of raising.
        if "taker_buy_volume" in self.df.columns:
            taker_buy = self.df["taker_buy_volume"].fillna(0.0)
        else:
            taker_buy = pd.Series(0.0, index=self.df.index)
        self.cvd = (2 * taker_buy - self.df["volume"]).cumsum()

        # Volume Profile - which price level absorbed the most volume over
        # the recent lookback (Point of Control). Read as "is price above
        # or below the level the market has spent the most volume at",
        # a lightweight institutional-activity proxy without needing raw
        # trade-level data.
        self.poc_price = self._calculate_poc()

        # Bollinger Bands / Keltner Channel / Donchian Channel - per the
        # product ask, these are deliberately NOT used for direction, only
        # for two specific context reads: "squeeze" (classic TTM-style
        # definition - Bollinger Bands compressed INSIDE the Keltner
        # Channel means volatility is coiled, a breakout is likely coming
        # but hasn't happened yet) and "breakout" (price closing beyond the
        # Donchian channel AS IT STOOD BEFORE this candle - i.e. a genuine
        # new N-period high/low, not just "near the edge of a rolling
        # window that includes itself").
        bb = BollingerBands(close=self.df["close"], window=20, window_dev=2)
        self.bb_upper = bb.bollinger_hband()
        self.bb_lower = bb.bollinger_lband()

        kc = KeltnerChannel(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=20)
        self.kc_upper = kc.keltner_channel_hband()
        self.kc_lower = kc.keltner_channel_lband()

        self.squeeze_on = (self.bb_upper < self.kc_upper) & (self.bb_lower > self.kc_lower)

        donchian = DonchianChannel(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=20)
        # Shifted by 1: the channel level AS OF THE PREVIOUS candle, so
        # "did today's close break it" is a real breakout, not a tautology
        # (today's own high/low is always inside a channel that includes
        # today's own high/low).
        self.donchian_upper_prev = donchian.donchian_channel_hband().shift(1)
        self.donchian_lower_prev = donchian.donchian_channel_lband().shift(1)

    def _calculate_supertrend(self, period: int = 7, multiplier: float = 3.0):
        """
        Return (supertrend_line, direction_series) where direction = 1 for
        uptrend, -1 for downtrend.

        Same math as before, but the sequential loop (each row depends on
        the previous one, so it can't be fully vectorized) now runs over
        raw numpy arrays instead of pandas Series with .iloc - .iloc has
        real per-call overhead, and this loop runs once per
        ConfirmationIndicators construction, i.e. once per candle during a
        backtest walk-forward (thousands of calls per symbol). That made
        this the dominant cost of a multi-symbol/multi-month backtest
        sweep - a 10-symbol/90-day calibration run was measured taking
        multiple hours before this change.
        """
        high = self.df["high"].to_numpy()
        low = self.df["low"].to_numpy()
        close = self.df["close"].to_numpy()
        atr = self.atr.to_numpy()
        n = len(self.df)

        hl_avg = (high + low) / 2
        upper = hl_avg + multiplier * atr
        lower = hl_avg - multiplier * atr

        st = np.full(n, np.nan)
        direction = np.ones(n, dtype=np.int64)

        for i in range(1, n):
            if close[i] <= upper[i - 1]:
                upper[i] = min(upper[i], upper[i - 1])
            if close[i] >= lower[i - 1]:
                lower[i] = max(lower[i], lower[i - 1])

            if close[i] > upper[i - 1]:
                direction[i] = 1
            elif close[i] < lower[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]

            st[i] = lower[i] if direction[i] == 1 else upper[i]

        if n > 0:
            st[0] = lower[0]

        return (
            pd.Series(st, index=self.df.index),
            pd.Series(direction, index=self.df.index),
        )

    def _calculate_poc(self, bins: int = 24, lookback: int = 200) -> Optional[float]:
        """
        Simplified Volume Profile: bins the recent window's typical price
        (HLC/3 per candle - a standard proxy when only OHLCV, not raw
        trades, is available) by volume and returns the bin center with the
        most volume (the Point of Control). None if there's too little
        history yet or the window has literally zero volume/range.
        """
        window = self.df.iloc[-lookback:] if len(self.df) > lookback else self.df
        if len(window) < 20 or window["volume"].sum() <= 0:
            return None

        price_min = float(window["low"].min())
        price_max = float(window["high"].max())
        if price_max <= price_min:
            return None

        bin_edges = np.linspace(price_min, price_max, bins + 1)
        typical_price = (window["high"] + window["low"] + window["close"]) / 3.0
        bin_idx = np.clip(np.digitize(typical_price, bin_edges) - 1, 0, bins - 1)

        bin_volumes = np.zeros(bins)
        for idx, vol in zip(bin_idx, window["volume"].to_numpy()):
            bin_volumes[idx] += vol

        poc_bin = int(np.argmax(bin_volumes))
        return round((bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2.0, 8)

    def _donchian_breakout_at(self, idx: int) -> Optional[str]:
        """"up" if close broke above the PRIOR-candle Donchian upper band,
        "down" if below the prior-candle lower band, None otherwise (or if
        not enough history yet for the shifted band to exist)."""
        upper = self.donchian_upper_prev.iloc[idx]
        lower = self.donchian_lower_prev.iloc[idx]
        close = self.df["close"].iloc[idx]
        if pd.isna(upper) or pd.isna(lower):
            return None
        if close > upper:
            return "up"
        if close < lower:
            return "down"
        return None

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
            "stoch_rsi_k": self.stoch_rsi_k.iloc[idx],
            "cci": self.cci.iloc[idx],
            "williams_r": self.williams_r.iloc[idx],
            "macd_line": self.macd_line.iloc[idx],
            "macd_signal": self.macd_signal.iloc[idx],
            "macd_hist": self.macd_hist.iloc[idx],
            "adx": self.adx.iloc[idx],
            "plus_di": self.plus_di.iloc[idx],
            "minus_di": self.minus_di.iloc[idx],
            "supertrend": self.supertrend.iloc[idx],
            "supertrend_dir": self.supertrend_direction.iloc[idx],
            "volume_spike": bool(self.volume_spike.iloc[idx]) if not pd.isna(self.volume_spike.iloc[idx]) else False,
            "obv_rising": (
                bool(self.obv.iloc[idx] > self.obv_ema.iloc[idx])
                if not pd.isna(self.obv.iloc[idx]) and not pd.isna(self.obv_ema.iloc[idx])
                else None
            ),
            "cvd": float(self.cvd.iloc[idx]) if not pd.isna(self.cvd.iloc[idx]) else 0.0,
            # Short-term (last 10 candles) net taker buy/sell imbalance -
            # the level of self.cvd is an arbitrary running total (only
            # meaningful relative to itself), so this is the piece actually
            # used for scoring: is order flow net-buying or net-selling
            # RIGHT NOW, not since the dawn of this dataframe.
            "cvd_rising": (
                bool(self.cvd.iloc[idx] > self.cvd.iloc[max(0, idx - 10)])
                if len(self.cvd) > 10
                else None
            ),
            "poc_price": self.poc_price,
            "squeeze_on": (
                bool(self.squeeze_on.iloc[idx]) if not pd.isna(self.squeeze_on.iloc[idx]) else None
            ),
            "donchian_breakout": self._donchian_breakout_at(idx),
            # Relative Volume - current candle's volume vs its own 20-period
            # average (self.volume_sma, already computed above for
            # volume_spike). >1 means above-average participation, <1 below.
            # None until the 20-period average itself has enough history.
            "relative_volume": (
                round(float(self.df["volume"].iloc[idx] / self.volume_sma.iloc[idx]), 2)
                if not pd.isna(self.volume_sma.iloc[idx]) and self.volume_sma.iloc[idx] > 0
                else None
            ),
        }
        # Check for NaN in all except the volume/order-flow/volatility-
        # context fields, which are Optional[bool/str] by design (e.g.
        # poc_price and squeeze_on are legitimately None until enough
        # history exists) rather than required numeric indicators -
        # treating None as "reject the whole candle" here would silently
        # block every signal until 200 candles of history exist, for
        # what's meant to be a soft confirmation.
        skip_nan_check = {
            "volume_spike", "obv_rising", "cvd_rising", "poc_price",
            "squeeze_on", "donchian_breakout", "relative_volume",
        }
        for k, v in vals.items():
            if k in skip_nan_check:
                continue
            if pd.isna(v):
                return None
        return vals