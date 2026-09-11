"""Engulfing Break detection - pure, no I/O, no network, no database.

THE SETUP (owner's words, 2026-09-11)
--------------------------------------------------------------------------
An ENGULFING is one candle swallowed by the next: a green candle that the
following red candle engulfs (bearish), or a red candle that the following
green one engulfs (bullish).

An ENGULFING BREAK is price then breaking that pattern the OTHER way - a
later candle CLOSING beyond both candles of the pair. A bearish engulfing
broken upward is a LONG; a bullish engulfing broken downward is a SHORT.

The trade is not taken at the break. It waits for price to come back to the
candle that WAS engulfed, and enters in its last 30 pips:

    LONG   entry zone [A.low,  A.low  + 30 pips]   stop A.low  - 90 pips
    SHORT  entry zone [A.high - 30 pips, A.high]   stop A.high + 90 pips

where A is the ENGULFED candle - both the entry and the stop are measured
from its own extreme. Take-profit is 2R, the owner's standing 1:2 rule.

DECISIONS MADE HERE, SO THEY ARE VISIBLE
--------------------------------------------------------------------------
* ENGULF MEANS FULL RANGE, not body: B.high >= A.high AND B.low <= A.low.
  The owner chose this. Fewer setups, cleaner ones.

* THIS MODULE ONLY EVER SEES CLOSED CANDLES. The caller strips the forming
  one, because only the feed knows which candle is still open. Acting on an
  unclosed candle fires on a close that has not happened yet - the difference
  between a backtest that works and one that lies.

* SHORTS ARE THE MIRROR IMAGE. The owner described the long only; the setup
  is symmetric and including the short doubles the sample.

* A BREAK GOES STALE. Only breaks within `max_bars_since_break` closed
  candles count - an old break is history, not a setup. And once price trades
  back THROUGH the engulfed candle's extreme the break has failed, so the
  level is no longer support (or resistance) and the idea is dead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class Bar:
    """The four prices detection needs. Time and volume live on the feed's
    Candle; this stays minimal so every rule is directly testable."""

    open: float
    high: float
    low: float
    close: float

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True)
class EngulfingBreak:
    """A detected setup. `engulfed` is candle A - the one the entry and the
    stop are both measured from."""

    direction: str
    engulfed: Bar
    engulfing: Bar
    break_close: float
    bars_since_break: int


def is_engulfing(a: Bar, b: Bar) -> Optional[str]:
    """"bearish" when a green candle is engulfed by a red one, "bullish" for
    the reverse, else None.

    FULL RANGE, not body: b must cover a's entire high-to-low span. A doji, or
    any candle with no direction, can neither engulf nor be engulfed.
    """
    covers = b.high >= a.high and b.low <= a.low
    if not covers:
        return None
    if a.is_green and b.is_red:
        return "bearish"
    if a.is_red and b.is_green:
        return "bullish"
    return None


def _first_break(closed: Sequence[Bar], pair_idx: int, kind: str) -> Optional[int]:
    """Index of the FIRST candle after the pair that closes beyond it.

    The first, emphatically not the latest. A candle closing beyond the pair
    is what CONFIRMS the setup; every candle after it that stays beyond is
    just price holding, not a second confirmation. Taking the latest such
    candle instead would mean the setup never aged out of
    `max_bars_since_break`, and - worse for a bot that must not repeat itself
    - its break price would change every 30 minutes, so the same trade would
    be announced again and again under a new identity.
    """
    a, b = closed[pair_idx - 1], closed[pair_idx]
    for k in range(pair_idx + 1, len(closed)):
        close = closed[k].close
        if kind == "bearish" and close > max(a.high, b.high):
            return k
        if kind == "bullish" and close < min(a.low, b.low):
            return k
    return None


def find_engulfing_break(
    closed: Sequence[Bar],
    max_bars_since_break: int = 3,
    lookback: int = 40,
) -> Optional[EngulfingBreak]:
    """The most recently CONFIRMED engulfing break in `closed` (oldest first),
    or None.

    `closed` must contain ONLY completed candles - see the module docstring.

    A break is a candle closing beyond BOTH candles of the pair, in the
    direction opposite to the engulfing, and only the first such candle
    counts. Breaks older than `max_bars_since_break` closed candles are
    history, not setups.
    """
    if len(closed) < 3:
        return None

    last_index = len(closed) - 1
    start = max(1, len(closed) - lookback)

    best: Optional[tuple] = None      # (break_idx, pair_idx, EngulfingBreak)
    for pair_idx in range(last_index, start - 1, -1):
        a, b = closed[pair_idx - 1], closed[pair_idx]
        kind = is_engulfing(a, b)
        if kind is None:
            continue

        break_idx = _first_break(closed, pair_idx, kind)
        if break_idx is None:
            continue                  # the pattern is there; price has not broken it
        bars_since = last_index - break_idx
        if bars_since > max_bars_since_break:
            continue                  # confirmed too long ago to trade now

        direction = LONG if kind == "bearish" else SHORT
        candidate = (
            break_idx, pair_idx,
            EngulfingBreak(direction, a, b, closed[break_idx].close, bars_since),
        )
        # A wider, older pair can be broken LATER than a newer one, so the
        # most recent break is found by comparing, not by stopping at the
        # first pair that qualifies.
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    return best[2] if best else None


def entry_zone(setup: EngulfingBreak, pip: float, entry_pips: float) -> Tuple[float, float]:
    """(near_edge, far_edge) of the entry zone, inside the ENGULFED candle.

    The near edge is the price a retracement touches FIRST, and so the one a
    resting limit order should sit at. The far edge is the candle's own
    extreme. Both are reported so the zone can be drawn, and so the choice of
    which edge to quote can be revisited against real fills.
    """
    span = pip * entry_pips
    if setup.direction == LONG:
        low = setup.engulfed.low
        return low + span, low            # touched from above
    high = setup.engulfed.high
    return high - span, high              # touched from below


def stop_price(setup: EngulfingBreak, pip: float, stop_pips: float) -> float:
    """Beyond the engulfed candle's own extreme - the same anchor the entry is
    measured from."""
    span = pip * stop_pips
    if setup.direction == LONG:
        return setup.engulfed.low - span
    return setup.engulfed.high + span


def take_profit_price(entry: float, stop: float, direction: str, rr: float) -> float:
    risk = abs(entry - stop)
    return entry + rr * risk if direction == LONG else entry - rr * risk


def break_still_valid(setup: EngulfingBreak, current_price: float) -> bool:
    """False once price has traded back THROUGH the engulfed candle's extreme.

    Past that point the break has failed; entering there would be trading a
    broken idea.
    """
    if setup.direction == LONG:
        return current_price > setup.engulfed.low
    return current_price < setup.engulfed.high


def bars_from_candles(candles: Sequence) -> List[Bar]:
    """Feed candles -> detection Bars, dropping anything not yet closed.

    The feed marks completeness; detection must never decide it for itself.
    """
    return [
        Bar(float(c.open), float(c.high), float(c.low), float(c.close))
        for c in candles
        if getattr(c, "complete", True)
    ]
