"""
Institutional Order Flow & Volatility metrics - the ONLY market-data
primitives the Universal ICT Pipeline consumes outside the ICT engines
themselves.

STATUS (2026-07-30): PRODUCTION. Created for the Universal Institutional
ICT Platform phase to remove the production pipeline's last dependency on
retail technical-analysis code.

WHY THIS EXISTS
--------------------------------------------------------------------------
`SignalGenerator` previously built an `app.legacy.confirmation.
ConfirmationIndicators`, which computes ~20 retail indicators (EMA, RSI,
MACD, Stochastic RSI, CCI, Williams %R, ADX, Supertrend, Bollinger,
Keltner, Donchian, OBV, VWAP) via the third-party `ta` package. The
ICT-only AI Scorer stopped reading all but four of those fields in the
previous migration phase, so the production path was paying for a full
retail indicator suite - and, more importantly, still IMPORTED retail
strategy code, which the Universal ICT mandate forbids.

This module computes exactly the four values the ICT pipeline actually
needs, from raw OHLCV, with pure pandas/numpy and no third-party TA
dependency:

  - ATR                - volatility scale. NOT a retail "signal": ICT
                          itself measures displacement in ATR terms, and
                          this project's Order Block, Supply/Demand,
                          Inducement and OTE engines all express
                          displacement as a multiple of it. Standard
                          Wilder smoothing (see `_wilder_atr`).
  - CVD / cvd_rising   - Cumulative Volume Delta from Binance's own
                          per-candle taker-buy-volume field. Real
                          aggressor-side order flow, not an oscillator.
  - Point of Control   - the price level that absorbed the most volume
                          over the recent window (simplified Volume
                          Profile). Real institutional activity proxy.
  - relative_volume    - current candle's volume vs its own 20-period
                          average. Plain participation measure.

Nothing here is directional and nothing here is a strategy. Every value is
a raw market-microstructure measurement that the ICT engines and the
Evidence Engine interpret.

BEHAVIOURAL CONTINUITY WITH THE PREVIOUS IMPLEMENTATION
--------------------------------------------------------------------------
CVD, `cvd_rising`, Point of Control and `relative_volume` are ported
verbatim from `ConfirmationIndicators` - same formulas, same windows, same
None-vs-value semantics - so those four are unchanged.

ATR is now computed here with textbook Wilder smoothing (SMA seed over the
first `window` true ranges, then `atr[i] = (atr[i-1]*(n-1) + tr[i]) / n`),
which is the same algorithm `ta.volatility.AverageTrueRange` implements.
`tests/test_order_flow.py` contains a direct equivalence test against the
real `ta` package that runs automatically wherever `ta` is installed (CI,
Docker) and skips in environments without it - so any divergence is caught
by the test suite rather than assumed away.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Windows are the same values `ConfirmationIndicators` used, so the
# pipeline's volatility scale and participation baseline are unchanged.
DEFAULT_ATR_WINDOW = 14
DEFAULT_VOLUME_SMA_WINDOW = 20
DEFAULT_POC_BINS = 24
DEFAULT_POC_LOOKBACK = 200
# Short-term window over which CVD direction is read. The CVD level itself
# is an arbitrary running total (only meaningful relative to itself), so
# the pipeline uses "is order flow net-buying over the last N candles",
# not the absolute value.
CVD_DIRECTION_LOOKBACK = 10


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Classic True Range: the greatest of (H-L), |H-Cprev|, |L-Cprev|."""
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def _wilder_atr(tr: pd.Series, window: int) -> pd.Series:
    """
    Wilder-smoothed ATR: seeded with the simple mean of the first `window`
    true ranges, then recursively smoothed. Values before the seed index
    are NaN, matching the convention every consumer already expects (the
    pipeline explicitly rejects a candle whose ATR is NaN).
    """
    values = tr.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    if n < window:
        return pd.Series(out, index=tr.index)

    # tr[0] is NaN by construction (no previous close), so the seed uses
    # true ranges 1..window inclusive - the same slice `ta` seeds from.
    seed = np.nanmean(values[:window])
    out[window - 1] = seed
    for i in range(window, n):
        out[i] = (out[i - 1] * (window - 1) + values[i]) / window
    return pd.Series(out, index=tr.index)


class OrderFlowMetrics:
    """
    Stateless per-instance: constructing it computes everything once from
    the supplied dataframe; two instances built on the same frame produce
    identical output.

    Exposes the same surface `SignalGenerator` previously used from
    `ConfirmationIndicators` - an `.atr` Series and a `.get_latest()` dict -
    so it is a drop-in for the ICT pipeline without any caller-side
    special-casing.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        atr_window: int = DEFAULT_ATR_WINDOW,
        volume_sma_window: int = DEFAULT_VOLUME_SMA_WINDOW,
        poc_bins: int = DEFAULT_POC_BINS,
        poc_lookback: int = DEFAULT_POC_LOOKBACK,
    ) -> None:
        self.df = df
        self.atr_window = atr_window
        self.volume_sma_window = volume_sma_window
        self.poc_bins = poc_bins
        self.poc_lookback = poc_lookback

        self.true_range = true_range(df["high"], df["low"], df["close"])
        self.atr = _wilder_atr(self.true_range, atr_window)
        self.volume_sma = df["volume"].rolling(window=volume_sma_window).mean()

        # CVD - real aggressor-side imbalance from Binance's own per-candle
        # taker-buy-volume field (see Candle.taker_buy_volume). Each
        # candle's delta = taker_buy - taker_sell, where taker_sell =
        # volume - taker_buy, hence delta = 2*taker_buy - volume. Falls
        # back to a flat zero series when the column isn't present (e.g.
        # candles cached before that field existed) rather than raising.
        if "taker_buy_volume" in df.columns:
            taker_buy = df["taker_buy_volume"].fillna(0.0)
        else:
            taker_buy = pd.Series(0.0, index=df.index)
        self.cvd = (2 * taker_buy - df["volume"]).cumsum()

        self.poc_price = self._calculate_poc()

    # ------------------------------------------------------------------
    # Volume Profile Point of Control
    # ------------------------------------------------------------------
    def _calculate_poc(self) -> Optional[float]:
        """
        Simplified Volume Profile: bins the recent window's typical price
        (HLC/3 per candle - the standard proxy when only OHLCV, not raw
        trades, is available) by volume and returns the bin center holding
        the most volume. None when there's too little history or the window
        has zero volume/range - never a fabricated number.
        """
        bins, lookback = self.poc_bins, self.poc_lookback
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

        # np.bincount is the vectorized equivalent of the previous
        # per-candle accumulation loop - same totals, no Python loop.
        bin_volumes = np.bincount(
            bin_idx, weights=window["volume"].to_numpy(dtype=float), minlength=bins,
        )
        poc_bin = int(np.argmax(bin_volumes))
        return round((bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2.0, 8)

    # ------------------------------------------------------------------
    # Latest values
    # ------------------------------------------------------------------
    def get_latest(self) -> Optional[dict]:
        """
        The latest metrics, or None when ATR is unavailable.

        ATR is the one REQUIRED field - the pipeline cannot anchor a stop,
        a target, or a displacement ratio without a valid volatility read,
        so a NaN ATR means the candle is genuinely unusable and the caller
        must skip it. Every other field is Optional by design and degrades
        to None (never to a fabricated 0) until it has enough history.
        """
        if len(self.df) == 0:
            return None

        idx = -1
        atr_value = self.atr.iloc[idx]
        if pd.isna(atr_value):
            return None

        cvd_value = self.cvd.iloc[idx]
        volume_sma_value = self.volume_sma.iloc[idx]
        current_volume = self.df["volume"].iloc[idx]

        return {
            "atr": float(atr_value),
            "cvd": float(cvd_value) if not pd.isna(cvd_value) else 0.0,
            # BUG FIX (2026-07-30, carried forward from the previous
            # implementation rather than replicated): the old code in
            # `ConfirmationIndicators.get_latest()` read
            # `self.cvd.iloc[max(0, idx - 10)]` with `idx = -1`. Because
            # `max(0, -11)` evaluates to 0, that always compared the latest
            # CVD against candle ZERO - i.e. "is order flow net-buying
            # since the dawn of this dataframe", which is precisely what
            # its own comment said it was NOT doing ("not since the dawn of
            # this dataframe"). Negative indexing is used directly here so
            # the comparison is genuinely against `CVD_DIRECTION_LOOKBACK`
            # candles back, as documented and intended.
            "cvd_rising": (
                bool(self.cvd.iloc[idx] > self.cvd.iloc[idx - CVD_DIRECTION_LOOKBACK])
                if len(self.cvd) > CVD_DIRECTION_LOOKBACK
                else None
            ),
            "poc_price": self.poc_price,
            "relative_volume": (
                round(float(current_volume / volume_sma_value), 2)
                if not pd.isna(volume_sma_value) and volume_sma_value > 0
                else None
            ),
        }
