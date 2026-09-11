"""
Engulfing Break - the owner's own setup, gold on 30m.

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
from its own extreme, which is what the owner specified. Take-profit is
2R, their standing 1:2 rule.

Gold pips: 1 pip = $0.10, so 30 pips = $3.00 and 90 pips = $9.00. That
convention is a setting, not a constant, because "a pip" on gold means
different things to different brokers and getting it wrong changes the trade
by a factor of ten.

DECISIONS MADE HERE, SO THEY ARE VISIBLE
--------------------------------------------------------------------------
* ENGULF MEANS FULL RANGE. The owner chose high/low engulfing over body
  engulfing: B.high >= A.high AND B.low <= A.low. Fewer setups, cleaner ones.

* THE ENTRY PRICE IS THE ZONE'S NEAR EDGE. A limit order rests at ONE price,
  and price returning to the zone touches its near edge first (A.low + 30
  pips for a long). Resting at the far edge would mean most fills never
  happen. The full zone is kept in `metadata` so the choice can be revisited
  against real fills rather than argued about.

* SHORTS ARE THE MIRROR IMAGE. The owner described the long only. The
  symmetric short is included because the setup is symmetric and it doubles
  the sample - which is the whole point of running this observe-only first.

* THE BREAK MUST BE RECENT and price must not have fallen back through the
  engulfed candle's extreme: a break that failed is not a setup. Both are
  checked and reported as conditions.

RISK, STATED PLAINLY
--------------------------------------------------------------------------
Entry-to-stop is a fixed 120 pips ($12.00). On gold near $4,500 that is
about 0.27% - well inside the 0.6% noise floor the ICT pipeline uses, and
close to the tight stops that were being taken out by noise there. That
floor was measured for crypto on 15m and is NOT applied to this strategy;
whether it should be is a question for the data this strategy is being run
to collect. It is said here so nobody is surprised by it later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from app.models.signal import Direction
from app.strategy.base_strategy import (
    BaseStrategy,
    ConditionResult,
    MarketData,
    StrategySignal,
    register_strategy,
)

STRATEGY_ID = "engulfing_break"


# ----------------------------------------------------------------------
# Pure detection - no pandas, no I/O, so every rule is directly testable.
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Candle:
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
    """A detected setup. `engulfed` is candle A - the one the entry and stop
    are both measured from."""

    direction: Direction
    engulfed: Candle
    engulfing: Candle
    break_close: float
    bars_since_break: int


def is_engulfing(a: Candle, b: Candle) -> Optional[str]:
    """"bearish" when a green candle is engulfed by a red one, "bullish" for
    the reverse, else None.

    FULL RANGE, not body: b must cover a's entire high-to-low span. A doji or
    any candle with no direction cannot engulf or be engulfed."""
    covers = b.high >= a.high and b.low <= a.low
    if not covers:
        return None
    if a.is_green and b.is_red:
        return "bearish"
    if a.is_red and b.is_green:
        return "bullish"
    return None


def _first_break(closed: List[Candle], pair_idx: int, kind: str) -> Optional[int]:
    """Index of the FIRST candle after the pair that closes beyond it.

    The first, emphatically not the latest. A candle closing beyond the pair
    is what CONFIRMS the setup; every candle after it that merely stays beyond
    is price holding, not a second confirmation. Taking the latest such candle
    instead meant the setup never aged out of `max_bars_since_break` and its
    reported break price changed on every candle - so the same setup was
    re-emitted indefinitely, each time looking new.
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
    candles: List[Candle], max_bars_since_break: int = 3, lookback: int = 40
) -> Optional[EngulfingBreak]:
    """The most recently CONFIRMED engulfing break in `candles` (oldest first),
    or None.

    A break is a candle closing beyond BOTH candles of the pair, in the
    direction opposite to the engulfing, and only the FIRST such candle counts.
    Breaks older than `max_bars_since_break` are history, not setups.

    The last candle is treated as still forming and is never used as the
    break: acting on an unclosed candle would fire on a close that has not
    happened yet, which is the difference between a backtest that works and
    one that lies.
    """
    if len(candles) < 4:
        return None

    closed = candles[:-1]                 # the forming candle is not tradeable
    if not closed:
        return None
    last_index = len(closed) - 1
    start = max(1, len(closed) - lookback)

    best: Optional[tuple] = None          # (break_idx, pair_idx, EngulfingBreak)
    for pair_idx in range(last_index, start - 1, -1):
        a, b = closed[pair_idx - 1], closed[pair_idx]
        kind = is_engulfing(a, b)
        if kind is None:
            continue

        break_idx = _first_break(closed, pair_idx, kind)
        if break_idx is None:
            continue                      # the pattern is there; price has not broken it
        bars_since = last_index - break_idx
        if bars_since > max_bars_since_break:
            continue                      # confirmed too long ago to trade now

        direction = Direction.LONG if kind == "bearish" else Direction.SHORT
        candidate = (
            break_idx, pair_idx,
            EngulfingBreak(direction, a, b, closed[break_idx].close, bars_since),
        )
        # A wider, older pair can be broken LATER than a newer one, so the most
        # recent break is found by comparing, not by stopping at the first pair
        # that qualifies.
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    return best[2] if best else None


def entry_zone(setup: EngulfingBreak, pip: float, entry_pips: float) -> tuple:
    """(near_edge, far_edge) of the entry zone, in the ENGULFED candle.

    Near edge is the price a retracement touches FIRST, and so the one a
    resting limit order should sit at."""
    span = pip * entry_pips
    if setup.direction == Direction.LONG:
        low = setup.engulfed.low
        return low + span, low            # touched from above
    high = setup.engulfed.high
    return high - span, high              # touched from below


def stop_price(setup: EngulfingBreak, pip: float, stop_pips: float) -> float:
    """Beyond the engulfed candle's own extreme - the same anchor the entry is
    measured from."""
    span = pip * stop_pips
    if setup.direction == Direction.LONG:
        return setup.engulfed.low - span
    return setup.engulfed.high + span


def take_profit_price(entry: float, stop: float, direction: Direction, rr: float) -> float:
    risk = abs(entry - stop)
    return entry + rr * risk if direction == Direction.LONG else entry - rr * risk


def break_still_valid(setup: EngulfingBreak, current_price: float) -> bool:
    """False once price has traded back THROUGH the engulfed candle's extreme -
    past that point the break has failed and the level is no longer support
    (or resistance); entering there would be trading a broken idea."""
    if setup.direction == Direction.LONG:
        return current_price > setup.engulfed.low
    return current_price < setup.engulfed.high


def candles_from_dataframe(df: Any, limit: int = 60) -> List[Candle]:
    """Tail of an OHLCV frame as plain Candles, oldest first."""
    if df is None or getattr(df, "empty", True):
        return []
    tail = df.tail(limit)
    return [
        Candle(float(r.open), float(r.high), float(r.low), float(r.close))
        for r in tail.itertuples()
    ]


def resample_ohlcv(df: Any, rule: str = "30min") -> Any:
    """15m -> 30m without another network call.

    Two 15m candles nest exactly inside one 30m candle, so this is an exact
    aggregation rather than an approximation. `label`/`closed` are left at
    pandas' defaults for a left-labelled bar, matching how the exchange
    stamps a candle by its OPEN time - the same convention `get_dataframe`
    already returns.
    """
    if df is None or getattr(df, "empty", True):
        return df
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    })
    return out.dropna(subset=["open", "high", "low", "close"])


# ----------------------------------------------------------------------
# The strategy
# ----------------------------------------------------------------------
@register_strategy
class EngulfingBreakStrategy(BaseStrategy):
    strategy_id = STRATEGY_ID
    display_name = "Engulfing Break (Gold 30m)"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.pip = float(getattr(settings, "engulfing_break_pip_size", 0.10))
        self.entry_pips = float(getattr(settings, "engulfing_break_entry_pips", 30.0))
        self.stop_pips = float(getattr(settings, "engulfing_break_stop_pips", 90.0))
        self.rr = float(getattr(settings, "engulfing_break_rr", 2.0))
        self.max_bars_since_break = int(getattr(settings, "engulfing_break_max_bars", 3))
        self.symbols = {
            s.strip().upper()
            for s in str(getattr(settings, "engulfing_break_symbols", "XAUUSDT")).split(",")
            if s.strip()
        }

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "engulfing_break_enabled", False))

    async def evaluate(self, market_data: MarketData) -> Optional[StrategySignal]:
        conditions: List[ConditionResult] = []
        symbol = (market_data.symbol or "").upper()

        in_scope = symbol in self.symbols
        conditions.append(ConditionResult(
            "symbol_in_scope", in_scope,
            f"{symbol} {'is' if in_scope else 'is not'} in {sorted(self.symbols)}",
        ))
        if not in_scope:
            return None

        candles = candles_from_dataframe(market_data.dataframe)
        enough = len(candles) >= 4
        conditions.append(ConditionResult(
            "enough_candles", enough, f"{len(candles)} candles (need 4+)",
        ))
        if not enough:
            return None

        setup = find_engulfing_break(candles, self.max_bars_since_break)
        conditions.append(ConditionResult(
            "engulfing_break", setup is not None,
            "no full-range engulfing broken the other way within "
            f"{self.max_bars_since_break} closed candles" if setup is None
            else f"{setup.direction.value} break, close {setup.break_close}, "
                 f"{setup.bars_since_break} bar(s) ago",
        ))
        if setup is None:
            return None

        price = market_data.current_price or candles[-1].close
        valid = break_still_valid(setup, price)
        conditions.append(ConditionResult(
            "break_still_valid", valid,
            f"price {price} vs engulfed "
            f"{'low ' + str(setup.engulfed.low) if setup.direction == Direction.LONG else 'high ' + str(setup.engulfed.high)}",
        ))
        if not valid:
            return None

        entry, far_edge = entry_zone(setup, self.pip, self.entry_pips)
        stop = stop_price(setup, self.pip, self.stop_pips)
        take_profit = take_profit_price(entry, stop, setup.direction, self.rr)
        risk = abs(entry - stop)

        # A degenerate pair (pip size or pip counts misconfigured to zero)
        # would divide by zero downstream in position sizing.
        if risk <= 0:
            conditions.append(ConditionResult(
                "risk_distance", False, "entry and stop are the same price - check pip settings",
            ))
            return None
        conditions.append(ConditionResult(
            "risk_distance", True,
            f"{risk / self.pip:.0f} pips (${risk:.2f}), {risk / entry * 100:.2f}% of entry",
        ))

        return StrategySignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction=setup.direction,
            entry_price=round(entry, 2),
            stop_loss=round(stop, 2),
            take_profit=round(take_profit, 2),
            risk_reward=self.rr,
            confidence=70,
            conditions=conditions,
            reason=(
                f"Engulfing break {setup.direction.value}: a full-range "
                f"{'bearish' if setup.direction == Direction.LONG else 'bullish'} engulfing was "
                f"closed through at {setup.break_close}; entry in the engulfed candle's last "
                f"{self.entry_pips:.0f} pips."
            ),
            metadata={
                "timeframe": getattr(self.settings, "engulfing_break_timeframe", "30m"),
                "entry_zone_near": round(entry, 2),
                "entry_zone_far": round(far_edge, 2),
                "engulfed_high": setup.engulfed.high,
                "engulfed_low": setup.engulfed.low,
                "engulfing_high": setup.engulfing.high,
                "engulfing_low": setup.engulfing.low,
                "break_close": setup.break_close,
                "bars_since_break": setup.bars_since_break,
                "pip_size": self.pip,
                "risk_pips": round(risk / self.pip),
            },
        )
