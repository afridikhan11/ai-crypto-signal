"""
Classical chart pattern detection - the "high-value" subset from the
product roadmap (Double Top/Bottom, Ascending/Descending Triangle, Bull/Bear
Flag). Head & Shoulders and wedge detection are deliberately deferred (the
roadmap itself calls these out as lower-priority/addable later).

Built on top of the SAME swing highs/lows the ICT Market Structure Engine
already computes for BOS/CHoCH (see app/smc/market_structure_engine.py)
rather than a second, possibly-inconsistent pivot detector - two different
"what's a swing point" definitions in the same pipeline would be a real
source of drift.

Consumed by AIScorer.assess()'s market_structure section as a same-
direction bonus / opposite-direction penalty on top of the BOS/CHoCH read,
not as a standalone category - a classical pattern is, structurally,
another way of describing the same "has price structure actually shifted"
question BOS/CHoCH already asks.
"""
from typing import List, Optional, Tuple
import pandas as pd

from app.smc.market_structure_engine import SwingPoint


def detect_chart_pattern(
    df: pd.DataFrame,
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    atr: float,
) -> Optional[Tuple[str, str]]:
    """Returns (pattern_name, "bullish"|"bearish") for the first pattern
    that matches, checking double-top/bottom, then triangles, then flags -
    or None."""
    if atr <= 0 or pd.isna(atr):
        return None

    for detector in (_double_top_bottom, _triangle, _flag):
        result = detector(df, swing_highs, swing_lows, atr)
        if result:
            return result
    return None


def _double_top_bottom(
    df: pd.DataFrame, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint], atr: float
) -> Optional[Tuple[str, str]]:
    tolerance = atr * 0.5
    close = df["close"].iloc[-1]

    if len(swing_highs) >= 2:
        h1, h2 = swing_highs[-2], swing_highs[-1]
        if abs(h1.price - h2.price) <= tolerance:
            trough = [sw for sw in swing_lows if h1.index < sw.index < h2.index]
            if trough:
                neckline = min(sw.price for sw in trough)
                if (h1.price - neckline) >= atr * 1.5 and close < neckline:
                    return ("Double Top", "bearish")

    if len(swing_lows) >= 2:
        l1, l2 = swing_lows[-2], swing_lows[-1]
        if abs(l1.price - l2.price) <= tolerance:
            peak = [sw for sw in swing_highs if l1.index < sw.index < l2.index]
            if peak:
                neckline = max(sw.price for sw in peak)
                if (neckline - l1.price) >= atr * 1.5 and close > neckline:
                    return ("Double Bottom", "bullish")

    return None


def _triangle(
    df: pd.DataFrame, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint], atr: float
) -> Optional[Tuple[str, str]]:
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return None

    flat_tolerance = atr * 0.4
    close = df["close"].iloc[-1]

    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]

    highs_flat = (max(sw.price for sw in recent_highs) - min(sw.price for sw in recent_highs)) <= flat_tolerance
    lows_rising = recent_lows[0].price < recent_lows[1].price < recent_lows[2].price
    if highs_flat and lows_rising:
        resistance = sum(sw.price for sw in recent_highs) / 3
        if close >= resistance - flat_tolerance:
            return ("Ascending Triangle", "bullish")

    lows_flat = (max(sw.price for sw in recent_lows) - min(sw.price for sw in recent_lows)) <= flat_tolerance
    highs_falling = recent_highs[0].price > recent_highs[1].price > recent_highs[2].price
    if lows_flat and highs_falling:
        support = sum(sw.price for sw in recent_lows) / 3
        if close <= support + flat_tolerance:
            return ("Descending Triangle", "bearish")

    return None


def _flag(
    df: pd.DataFrame, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint], atr: float
) -> Optional[Tuple[str, str]]:
    """
    Impulse (a strong directional move over the last ~15 candles, excluding
    the most recent ~5) followed by a tight, lower-volatility consolidation
    (the last ~5 candles) that drifts flat-to-counter-trend - the "flagpole
    then flag" shape. Confirmed only if the impulse and the signal direction
    agree (a flag argues for CONTINUATION, not the consolidation's own
    drift direction).
    """
    lookback = 20
    if len(df) < lookback + 5:
        return None

    window = df.iloc[-lookback:]
    pole = window.iloc[:-5]
    flag_part = window.iloc[-5:]

    pole_move = pole["close"].iloc[-1] - pole["close"].iloc[0]
    if abs(pole_move) < atr * 3:
        # Not a real impulse - too small a move to call it a flagpole.
        return None

    pole_range = (pole["high"].max() - pole["low"].min()) or 1e-9
    flag_range = flag_part["high"].max() - flag_part["low"].min()
    if flag_range > pole_range * 0.5:
        # Consolidation isn't actually tight relative to the impulse.
        return None

    flag_drift = flag_part["close"].iloc[-1] - flag_part["close"].iloc[0]

    if pole_move > 0 and flag_drift <= atr * 0.5:
        return ("Bull Flag", "bullish")
    if pole_move < 0 and flag_drift >= -atr * 0.5:
        return ("Bear Flag", "bearish")
    return None
