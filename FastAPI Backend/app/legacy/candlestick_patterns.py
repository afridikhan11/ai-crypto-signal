"""
Candlestick pattern detection - the 6 major reversal patterns from the
product roadmap (Bullish/Bearish Engulfing, Hammer, Shooting Star, Morning
Star, Evening Star), read from plain OHLC data.

Deliberately scoped down from "every candlestick pattern" to just these six
"high-value" ones, and deliberately NOT wired in as a standalone signal
category - per the product ask, a candlestick pattern only means something
when it forms INSIDE an existing SMC zone (order block / FVG). See
AIScorer.assess()'s "confluence" section, which is where this gets
consumed: a pattern whose direction agrees with the signal AND whose
timestamp is the most recent candle gets folded into the same
order-block/FVG/liquidity confluence bonus, not scored on its own.
"""
from typing import Optional, Tuple
import pandas as pd


def _body(candle: pd.Series) -> float:
    return abs(candle["close"] - candle["open"])


def _range(candle: pd.Series) -> float:
    return candle["high"] - candle["low"]


def _upper_wick(candle: pd.Series) -> float:
    return candle["high"] - max(candle["close"], candle["open"])


def _lower_wick(candle: pd.Series) -> float:
    return min(candle["close"], candle["open"]) - candle["low"]


def _is_bullish(candle: pd.Series) -> bool:
    return candle["close"] > candle["open"]


def _is_bearish(candle: pd.Series) -> bool:
    return candle["close"] < candle["open"]


def detect_latest_pattern(df: pd.DataFrame) -> Optional[Tuple[str, str]]:
    """
    Checks only the LAST candle in `df` (optionally using the 1-2 before it
    for the multi-candle patterns) - this is meant to answer "did a pattern
    just complete right now", not scan the whole history. Returns
    (pattern_name, "bullish"|"bearish") for the first match found, checking
    3-candle patterns before 2-candle before 1-candle (a Morning/Evening
    Star is a stronger, more specific claim than a plain Hammer that
    happens to sit in the same spot), or None if nothing qualifies.
    """
    if len(df) < 3:
        return None

    c0 = df.iloc[-1]  # most recent (completed) candle
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    avg_range = df["high"].sub(df["low"]).iloc[-14:].mean()
    if pd.isna(avg_range) or avg_range <= 0:
        return None

    # --- 3-candle patterns -------------------------------------------
    star = _three_candle_star(c2, c1, c0, avg_range)
    if star:
        return star

    # --- 2-candle patterns --------------------------------------------
    engulfing = _engulfing(c1, c0)
    if engulfing:
        return engulfing

    # --- 1-candle patterns ----------------------------------------------
    single = _hammer_or_star(c0, avg_range)
    if single:
        return single

    return None


def _engulfing(prev: pd.Series, curr: pd.Series) -> Optional[Tuple[str, str]]:
    prev_body_low = min(prev["open"], prev["close"])
    prev_body_high = max(prev["open"], prev["close"])
    curr_body_low = min(curr["open"], curr["close"])
    curr_body_high = max(curr["open"], curr["close"])

    if _body(prev) <= 0:
        return None

    engulfs = curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
    if not engulfs:
        return None

    if _is_bearish(prev) and _is_bullish(curr):
        return ("Bullish Engulfing", "bullish")
    if _is_bullish(prev) and _is_bearish(curr):
        return ("Bearish Engulfing", "bearish")
    return None


def _hammer_or_star(c: pd.Series, avg_range: float) -> Optional[Tuple[str, str]]:
    body = _body(c)
    rng = _range(c)
    if rng <= 0 or rng < avg_range * 0.3:
        # Too small a candle relative to recent volatility to mean anything.
        return None

    upper = _upper_wick(c)
    lower = _lower_wick(c)

    # Small body (top OR bottom third of the range), long opposite wick,
    # short same-side wick - classic hammer/shooting-star shape regardless
    # of whether the body itself closed green or red.
    if lower >= body * 2 and lower >= rng * 0.5 and upper <= body * 0.5:
        return ("Hammer", "bullish")
    if upper >= body * 2 and upper >= rng * 0.5 and lower <= body * 0.5:
        return ("Shooting Star", "bearish")
    return None


def _three_candle_star(
    first: pd.Series, middle: pd.Series, last: pd.Series, avg_range: float
) -> Optional[Tuple[str, str]]:
    first_body = _body(first)
    middle_body = _body(middle)
    last_body = _body(last)

    if first_body < avg_range * 0.5 or last_body < avg_range * 0.5:
        # First and third candles need to be real, decisive bodies - a
        # star's whole point is a big move, a tiny indecision candle, then
        # a big move back.
        return None
    if middle_body > first_body * 0.5:
        # Middle candle must be small/indecisive relative to the first.
        return None

    first_mid = (first["open"] + first["close"]) / 2

    if _is_bearish(first) and _is_bullish(last) and last["close"] > first_mid:
        return ("Morning Star", "bullish")
    if _is_bullish(first) and _is_bearish(last) and last["close"] < first_mid:
        return ("Evening Star", "bearish")
    return None
