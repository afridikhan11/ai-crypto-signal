"""Higher-timeframe (HTF) directional bias engine.

ICT is top-down: the higher timeframes decide *direction*, the lower timeframe
only decides *entry*. This module reads the configured HTF frames (default
4h + 1h) and produces a single bias the entry logic must align with.

Bias per timeframe is derived from market structure (sequence of swing
highs/lows) with an EMA50/EMA200 tie-breaker, then combined across timeframes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd
from ta.trend import EMAIndicator

from app.smc.market_structure import MarketStructure, SwingType
from app.smc.supply_demand import SupplyDemandZones


@dataclass
class Bias:
    direction: str                 # "LONG" | "SHORT" | "NEUTRAL"
    strength: float                # 0.0 - 1.0
    htf_zone: str = "unknown"      # premium | discount | equilibrium
    per_tf: Dict[str, str] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("LONG", "SHORT")


# Minimum EMA separation (as a fraction of price) to call a trend; inside this
# deadband the market is treated as ranging/neutral.
_EMA_DEADBAND = 0.001  # 0.1%


def _ema_trend(df: pd.DataFrame) -> str:
    if len(df) < 200:
        window = max(10, len(df) // 2)
        fast = EMAIndicator(df["close"], window=window).ema_indicator()
        slow = EMAIndicator(df["close"], window=min(len(df) - 1, window * 2)).ema_indicator()
    else:
        fast = EMAIndicator(df["close"], window=50).ema_indicator()
        slow = EMAIndicator(df["close"], window=200).ema_indicator()
    f, s = fast.iloc[-1], slow.iloc[-1]
    if pd.isna(f) or pd.isna(s) or s == 0:
        return "neutral"
    diff = (f - s) / abs(s)
    if abs(diff) < _EMA_DEADBAND:
        return "neutral"
    return "bullish" if diff > 0 else "bearish"


def _structure_trend(df: pd.DataFrame) -> str:
    """Classify trend from the most recent swing-point structure."""
    ms = MarketStructure(df)
    highs, lows = ms.swing_highs, ms.swing_lows
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"

    last_high, last_low = highs[-1], lows[-1]
    bullish = last_high.type == SwingType.HH and last_low.type == SwingType.HL
    bearish = last_high.type == SwingType.LH and last_low.type == SwingType.LL
    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "neutral"


def _tf_bias(df: pd.DataFrame) -> str:
    """Per-timeframe bias.

    EMA(50/200) is the primary, low-noise trend measure; market structure is a
    confirmation used to break ties when the EMAs are flat. Structure is not
    allowed to veto a clear EMA trend to neutral (that made the bias far too
    fragile on real, noisy data).
    """
    if df is None or df.empty or len(df) < 30:
        return "neutral"
    ema = _ema_trend(df)
    if ema != "neutral":
        return ema
    return _structure_trend(df)


def compute_bias(htf_frames: Dict[str, pd.DataFrame], htf_order: List[str]) -> Bias:
    """Combine per-timeframe bias into a single directional bias.

    ``htf_order`` lists timeframes from highest to lowest priority. The highest
    timeframe is the primary driver; agreement across frames strengthens the
    bias, disagreement neutralises it.
    """
    per_tf: Dict[str, str] = {}
    weights: Dict[str, float] = {}
    # Highest timeframe gets the largest weight.
    n = len(htf_order)
    for rank, tf in enumerate(htf_order):
        df = htf_frames.get(tf)
        per_tf[tf] = _tf_bias(df) if df is not None else "neutral"
        weights[tf] = (n - rank) / sum(range(1, n + 1))

    score = 0.0
    for tf, b in per_tf.items():
        if b == "bullish":
            score += weights[tf]
        elif b == "bearish":
            score -= weights[tf]

    reasons = [f"{tf}:{b}" for tf, b in per_tf.items()]

    # Premium/discount context from the primary (highest) timeframe.
    primary_df = htf_frames.get(htf_order[0]) if htf_order else None
    htf_zone = "unknown"
    if primary_df is not None and not primary_df.empty:
        sd = SupplyDemandZones(primary_df)
        sd.calculate_recent_range(lookback=min(50, len(primary_df)))
        htf_zone = sd.get_zone(float(primary_df["close"].iloc[-1]))

    if score >= 0.5:
        direction = "LONG"
    elif score <= -0.5:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return Bias(
        direction=direction,
        strength=round(min(abs(score), 1.0), 2),
        htf_zone=htf_zone,
        per_tf=per_tf,
        reasons=reasons,
    )
