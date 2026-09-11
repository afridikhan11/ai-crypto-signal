"""
Engulfing Break - the owner's own setup, gold on 30m.

Their definition, in their words (2026-09-11):

  "aik green candle ko red candle ny engulf kar lya ye aik engulfing ho gai ...
   market es engulfing ko opposite side pe break kr dy mtlb previous green or
   red candle dono k oper close kr dy to ye engulfing break kehlati hy"

  "jo candle engulf hui thi usk last 30 pips ko Entry k tor pe dy"
  "Stop loss Exact low k 90 pip nichy rakhna hy"

and, from the follow-up questions: engulf means FULL RANGE (high/low, not
body), the entry is measured UP from the engulfed candle's low, 1 pip on gold
is $0.10, and the target is 1:2.

Every number below is in that arithmetic: 30 pips = $3.00, 90 pips = $9.00.
"""
import pandas as pd
import pytest

from app.models.signal import Direction
from app.strategy.base_strategy import MarketData
from app.strategy.engulfing_break_strategy import (
    Candle,
    EngulfingBreakStrategy,
    break_still_valid,
    candles_from_dataframe,
    entry_zone,
    find_engulfing_break,
    is_engulfing,
    resample_ohlcv,
    stop_price,
    take_profit_price,
)

PIP = 0.10


def green(o, h, l, c):
    assert c > o
    return Candle(o, h, l, c)


def red(o, h, l, c):
    assert c < o
    return Candle(o, h, l, c)


class _Settings:
    engulfing_break_enabled = True
    engulfing_break_symbols = "XAUUSDT"
    engulfing_break_timeframe = "30m"
    engulfing_break_pip_size = PIP
    engulfing_break_entry_pips = 30.0
    engulfing_break_stop_pips = 90.0
    engulfing_break_rr = 2.0
    engulfing_break_max_bars = 3


# ======================================================================
# is_engulfing - FULL RANGE, not body
# ======================================================================
class TestIsEngulfing:
    def test_red_fully_engulfing_a_green_is_bearish(self):
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)      # covers a's whole high-low span
        assert is_engulfing(a, b) == "bearish"

    def test_green_fully_engulfing_a_red_is_bullish(self):
        a = red(4503, 4505, 4498, 4500)
        b = green(4497, 4507, 4496, 4506)
        assert is_engulfing(a, b) == "bullish"

    def test_body_engulfing_alone_is_not_enough(self):
        # b's body swallows a's body, but a's high pokes above b's. Under the
        # owner's full-range definition this is NOT an engulfing.
        a = green(4500, 4510, 4499, 4504)
        b = red(4505, 4506, 4498, 4499)
        assert is_engulfing(a, b) is None

    def test_same_colour_pair_is_not_an_engulfing(self):
        a = green(4500, 4505, 4498, 4503)
        b = green(4497, 4507, 4496, 4506)
        assert is_engulfing(a, b) is None

    def test_a_doji_neither_engulfs_nor_is_engulfed(self):
        doji = Candle(4500, 4505, 4495, 4500)
        other = red(4506, 4507, 4494, 4496)
        assert is_engulfing(doji, other) is None
        assert is_engulfing(other, doji) is None

    def test_equal_extremes_still_count_as_covering(self):
        # "engulf" is inclusive: matching the high/low exactly is not a miss.
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4505, 4498, 4497)
        assert is_engulfing(a, b) == "bearish"


# ======================================================================
# find_engulfing_break
# ======================================================================
def _long_setup_series(bars_after_break=0):
    """Bearish engulfing, then a close above BOTH candles -> LONG."""
    a = green(4500, 4505, 4498, 4503)        # the engulfed candle
    b = red(4506, 4507, 4496, 4497)          # engulfs a
    breaker = green(4498, 4510, 4497, 4508)  # closes above max(4505, 4507)
    filler = [Candle(4508, 4509, 4506, 4507) for _ in range(bars_after_break)]
    forming = Candle(4507, 4508, 4505, 4506)
    return [Candle(4495, 4501, 4494, 4500), a, b, breaker, *filler, forming]


class TestFindEngulfingBreak:
    def test_detects_a_long_break(self):
        setup = find_engulfing_break(_long_setup_series())
        assert setup is not None
        assert setup.direction is Direction.LONG
        assert setup.engulfed.low == 4498          # candle A, the engulfed one
        assert setup.break_close == 4508

    def test_detects_a_short_break(self):
        a = red(4503, 4505, 4498, 4500)            # engulfed
        b = green(4497, 4507, 4496, 4506)          # engulfs a
        breaker = red(4505, 4506, 4494, 4495)      # closes below min(4498, 4496)
        series = [Candle(4495, 4501, 4494, 4500), a, b, breaker, Candle(4495, 4496, 4493, 4494)]

        setup = find_engulfing_break(series)
        assert setup is not None
        assert setup.direction is Direction.SHORT
        assert setup.engulfed.high == 4505

    def test_a_close_inside_the_pattern_is_not_a_break(self):
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)
        not_a_break = green(4498, 4506, 4497, 4504)   # above A but NOT above B
        series = [Candle(4495, 4501, 4494, 4500), a, b, not_a_break, Candle(4504, 4505, 4503, 4504)]
        assert find_engulfing_break(series) is None

    def test_a_wick_through_without_a_close_is_not_a_break(self):
        # The owner said "close kr dy" - a close, not a touch.
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)
        wick = green(4498, 4515, 4497, 4505)          # high pierces, close does not
        series = [Candle(4495, 4501, 4494, 4500), a, b, wick, Candle(4505, 4506, 4504, 4505)]
        assert find_engulfing_break(series) is None

    def test_the_forming_candle_is_never_the_break(self):
        # Acting on an unclosed candle fires on a close that has not happened.
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)
        forming_break = green(4498, 4510, 4497, 4508)
        series = [Candle(4495, 4501, 4494, 4500), a, b, forming_break]
        assert find_engulfing_break(series) is None

    def test_a_stale_break_is_not_a_setup(self):
        fresh = find_engulfing_break(_long_setup_series(bars_after_break=2), max_bars_since_break=3)
        assert fresh is not None
        stale = find_engulfing_break(_long_setup_series(bars_after_break=10), max_bars_since_break=3)
        assert stale is None

    def test_only_the_FIRST_candle_beyond_the_pattern_is_the_break(self):
        """Price HOLDING above the level is not a second break.

        Every candle closing beyond the pair used to re-qualify as "the
        break", which had two consequences: the setup never aged out of
        `max_bars_since_break`, and its reported break price moved on every
        candle - so the same setup was emitted again and again, each time
        looking new. `_long_setup_series` happened to use filler that closed
        exactly AT the level rather than above it, so nothing caught this.
        """
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)
        breaker = green(4498, 4510, 4497, 4508)       # first close above 4507
        holding = [green(4508, 4512, 4507, 4511), green(4511, 4514, 4510, 4513)]
        series = [Candle(4495, 4501, 4494, 4500), a, b, breaker, *holding,
                  Candle(4513, 4514, 4512, 4513)]

        setup = find_engulfing_break(series, max_bars_since_break=3)
        assert setup is not None
        assert setup.break_close == 4508          # the original break, not 4513
        assert setup.bars_since_break == 2        # and it is ageing out

    def test_a_pattern_price_has_not_broken_yet_is_not_a_setup(self):
        a = green(4500, 4505, 4498, 4503)
        b = red(4506, 4507, 4496, 4497)
        series = [Candle(4495, 4501, 4494, 4500), a, b,
                  Candle(4498, 4502, 4497, 4500), Candle(4500, 4501, 4499, 4500)]
        assert find_engulfing_break(series) is None

    def test_too_few_candles_returns_none(self):
        assert find_engulfing_break([Candle(1, 2, 0.5, 1.5)]) is None
        assert find_engulfing_break([]) is None


# ======================================================================
# The prices - the owner's arithmetic, exactly
# ======================================================================
class TestPrices:
    def test_long_entry_is_the_engulfed_low_plus_30_pips(self):
        setup = find_engulfing_break(_long_setup_series())
        near, far = entry_zone(setup, PIP, 30.0)
        assert far == 4498.0                    # the low itself
        assert near == pytest.approx(4501.0)    # +$3.00, touched first from above

    def test_long_stop_is_90_pips_below_that_same_low(self):
        setup = find_engulfing_break(_long_setup_series())
        assert stop_price(setup, PIP, 90.0) == pytest.approx(4489.0)   # -$9.00

    def test_long_risk_is_120_pips_and_the_target_is_2R(self):
        setup = find_engulfing_break(_long_setup_series())
        entry, _ = entry_zone(setup, PIP, 30.0)
        stop = stop_price(setup, PIP, 90.0)
        assert entry - stop == pytest.approx(12.0)        # 120 pips
        tp = take_profit_price(entry, stop, Direction.LONG, 2.0)
        assert tp == pytest.approx(4525.0)                # entry + $24.00

    def test_short_is_the_mirror_image(self):
        a = red(4503, 4505, 4498, 4500)
        b = green(4497, 4507, 4496, 4506)
        breaker = red(4505, 4506, 4494, 4495)
        series = [Candle(4495, 4501, 4494, 4500), a, b, breaker, Candle(4495, 4496, 4493, 4494)]
        setup = find_engulfing_break(series)

        near, far = entry_zone(setup, PIP, 30.0)
        assert far == 4505.0                              # the engulfed high
        assert near == pytest.approx(4502.0)              # -$3.00
        assert stop_price(setup, PIP, 90.0) == pytest.approx(4514.0)   # +$9.00
        assert take_profit_price(near, 4514.0, Direction.SHORT, 2.0) == pytest.approx(4478.0)

    def test_pip_size_scales_everything(self):
        # The whole reason pip size is a setting: at $1.00 a pip the same
        # setup is a ten-times-wider trade.
        setup = find_engulfing_break(_long_setup_series())
        near, _ = entry_zone(setup, 1.0, 30.0)
        assert near == pytest.approx(4528.0)
        assert stop_price(setup, 1.0, 90.0) == pytest.approx(4408.0)


class TestBreakStillValid:
    def test_a_long_dies_once_price_trades_back_below_the_engulfed_low(self):
        setup = find_engulfing_break(_long_setup_series())
        assert break_still_valid(setup, 4499.0) is True     # inside the zone, fine
        assert break_still_valid(setup, 4497.0) is False    # through it - idea broken


# ======================================================================
# 15m -> 30m resampling
# ======================================================================
class TestResample:
    def test_two_15m_candles_become_one_30m_candle(self):
        idx = pd.to_datetime([
            "2026-09-11 10:00", "2026-09-11 10:15",
            "2026-09-11 10:30", "2026-09-11 10:45",
        ])
        df = pd.DataFrame(
            {"open": [10, 12, 20, 18], "high": [13, 15, 21, 19],
             "low": [9, 11, 17, 16], "close": [12, 14, 18, 17], "volume": [1, 2, 3, 4]},
            index=idx,
        )
        out = resample_ohlcv(df, "30min")

        assert len(out) == 2
        first = out.iloc[0]
        assert (first.open, first.high, first.low, first.close, first.volume) == (10, 15, 9, 14, 3)
        second = out.iloc[1]
        assert (second.open, second.high, second.low, second.close) == (20, 21, 16, 17)

    def test_an_empty_frame_survives(self):
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert resample_ohlcv(empty, "30min").empty


# ======================================================================
# The strategy end to end
# ======================================================================
def _frame(candles):
    idx = pd.date_range("2026-09-11 00:00", periods=len(candles), freq="30min")
    return pd.DataFrame(
        [{"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": 1.0} for c in candles],
        index=idx,
    )


@pytest.mark.asyncio
class TestStrategy:
    async def _run(self, candles, symbol="XAUUSDT", price=None, settings=None):
        strat = EngulfingBreakStrategy(settings or _Settings())
        df = _frame(candles)
        return await strat.evaluate(MarketData(
            symbol=symbol, dataframe=df,
            current_price=price if price is not None else float(df["close"].iloc[-1]),
        ))

    async def test_fires_a_long_on_the_owners_setup(self):
        sig = await self._run(_long_setup_series())
        assert sig is not None
        assert sig.direction is Direction.LONG
        assert sig.entry_price == pytest.approx(4501.0)
        assert sig.stop_loss == pytest.approx(4489.0)
        assert sig.take_profit == pytest.approx(4525.0)
        assert sig.risk_reward == 2.0
        assert sig.strategy_id == "engulfing_break"
        assert sig.metadata["risk_pips"] == 120

    async def test_gold_only(self):
        # The whole point of the symbol scope: this is the owner's gold setup,
        # not a rule for every symbol the scanner happens to sweep.
        assert await self._run(_long_setup_series(), symbol="BTCUSDT") is None

    async def test_no_setup_means_no_signal(self):
        flat = [Candle(4500 + i, 4502 + i, 4498 + i, 4501 + i) for i in range(10)]
        assert await self._run(flat) is None

    async def test_a_broken_break_produces_nothing(self):
        assert await self._run(_long_setup_series(), price=4490.0) is None

    async def test_every_checked_rule_is_reported_on_the_signal(self):
        sig = await self._run(_long_setup_series())
        names = {c.name for c in sig.conditions}
        assert {"symbol_in_scope", "enough_candles", "engulfing_break",
                "break_still_valid", "risk_distance"} <= names
        assert all(c.passed for c in sig.conditions)

    async def test_disabled_by_default(self):
        from app.core.config import Settings

        assert EngulfingBreakStrategy(Settings()).enabled is False
