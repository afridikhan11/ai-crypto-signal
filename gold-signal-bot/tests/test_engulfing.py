"""The setup's rules, one at a time."""
from __future__ import annotations

import pytest

from gold_signals.candles import Candle
from gold_signals.engulfing import (
    LONG,
    SHORT,
    Bar,
    bars_from_candles,
    break_still_valid,
    entry_zone,
    find_engulfing_break,
    is_engulfing,
    stop_price,
    take_profit_price,
)

from conftest import (
    BEARISH_A,
    BEARISH_B,
    BREAK_UP,
    BULLISH_A,
    BULLISH_B,
    long_setup_bars,
    quiet_green_bars,
    short_setup_bars,
    to_candles,
)


class TestIsEngulfing:
    def test_green_swallowed_by_red_is_bearish(self):
        assert is_engulfing(BEARISH_A, BEARISH_B) == "bearish"

    def test_red_swallowed_by_green_is_bullish(self):
        assert is_engulfing(BULLISH_A, BULLISH_B) == "bullish"

    def test_same_colour_is_never_an_engulfing(self):
        green = Bar(100, 111, 99, 110)
        bigger_green = Bar(99, 120, 90, 115)
        assert is_engulfing(green, bigger_green) is None

    def test_a_body_engulfing_that_misses_the_wick_does_not_count(self):
        # The owner chose FULL RANGE. B's body swallows A's body, but B's high
        # stops short of A's high, so this is not a setup here.
        a = Bar(open=100.0, high=115.0, low=99.0, close=110.0)     # green, long upper wick
        b = Bar(open=112.0, high=113.0, low=97.0, close=98.0)      # red, high < a.high
        assert is_engulfing(a, b) is None

    def test_equal_high_and_low_still_engulf(self):
        # ">=" and "<=", not ">" and "<": a candle that exactly matches the
        # extremes has still covered the whole range.
        a = Bar(open=100.0, high=110.0, low=100.0, close=109.0)
        b = Bar(open=109.5, high=110.0, low=100.0, close=100.5)
        assert is_engulfing(a, b) == "bearish"

    def test_a_doji_neither_engulfs_nor_is_engulfed(self):
        doji = Bar(open=105.0, high=111.0, low=99.0, close=105.0)
        assert is_engulfing(doji, BEARISH_B) is None
        assert is_engulfing(BEARISH_A, Bar(open=105.0, high=120.0, low=90.0, close=105.0)) is None


class TestFindEngulfingBreak:
    def test_a_bearish_engulfing_broken_upward_is_a_long(self):
        setup = find_engulfing_break(long_setup_bars())
        assert setup is not None
        assert setup.direction == LONG
        assert setup.engulfed == BEARISH_A       # the entry anchor is candle A
        assert setup.engulfing == BEARISH_B
        assert setup.break_close == 115.0
        assert setup.bars_since_break == 0

    def test_a_bullish_engulfing_broken_downward_is_a_short(self):
        setup = find_engulfing_break(short_setup_bars())
        assert setup is not None
        assert setup.direction == SHORT
        assert setup.engulfed == BULLISH_A

    def test_the_break_must_close_beyond_BOTH_candles(self):
        # 112 is above A.high (111) but not above B.high (113): the pattern is
        # not broken until price closes past the whole thing.
        short_break = Bar(open=99.0, high=116.0, low=98.0, close=112.0)
        assert find_engulfing_break([BEARISH_A, BEARISH_B, short_break]) is None

    def test_a_wick_through_is_not_a_break(self):
        # High 120 pokes above everything, but the CLOSE is what counts.
        wick = Bar(open=99.0, high=120.0, low=98.0, close=105.0)
        assert find_engulfing_break([BEARISH_A, BEARISH_B, wick]) is None

    def test_a_stale_break_is_not_a_setup(self):
        bars = long_setup_bars() + quiet_green_bars(5, start=115.0)
        assert find_engulfing_break(bars, max_bars_since_break=3) is None
        # ...but widening the window finds the same break again.
        setup = find_engulfing_break(bars, max_bars_since_break=10)
        assert setup is not None and setup.bars_since_break == 5

    def test_bars_since_break_counts_closed_candles_after_the_break(self):
        bars = long_setup_bars() + quiet_green_bars(2, start=115.0)
        setup = find_engulfing_break(bars, max_bars_since_break=3)
        assert setup is not None
        assert setup.bars_since_break == 2

    def test_too_few_candles_is_not_an_error(self):
        assert find_engulfing_break([]) is None
        assert find_engulfing_break([BEARISH_A, BEARISH_B]) is None

    def test_lookback_bounds_the_search(self):
        bars = long_setup_bars() + quiet_green_bars(1, start=115.0)
        # A lookback of 2 cannot reach back to the pair.
        assert find_engulfing_break(bars, max_bars_since_break=3, lookback=2) is None

    def test_only_the_FIRST_candle_beyond_the_pair_is_the_break(self):
        """Found while writing these tests, and the reason the search was
        rewritten.

        Every candle that closes beyond the pair used to re-qualify as "the
        break". Price holding above the level is not a second confirmation -
        and treating it as one had two consequences: the setup never aged out
        of `max_bars_since_break`, and its break price changed every 30
        minutes, so the same trade would have been announced again and again
        under a new identity.
        """
        holding = [
            Bar(open=115.0, high=117.0, low=114.5, close=116.0),   # still above 113
            Bar(open=116.0, high=118.0, low=115.5, close=117.0),   # still above 113
        ]
        setup = find_engulfing_break(long_setup_bars() + holding, max_bars_since_break=3)
        assert setup is not None
        assert setup.break_close == 115.0        # the original break, not 117.0
        assert setup.bars_since_break == 2       # and it is ageing

    def test_a_pattern_price_has_not_broken_yet_is_not_a_setup(self):
        unbroken = [BEARISH_A, BEARISH_B, Bar(open=99.0, high=112.0, low=98.0, close=100.0)]
        assert find_engulfing_break(unbroken) is None

    def test_the_most_recent_break_wins(self):
        first = long_setup_bars()
        second = [
            Bar(open=200.0, high=211.0, low=199.0, close=210.0),   # green
            Bar(open=212.0, high=213.0, low=197.0, close=198.0),   # red, covers
            Bar(open=199.0, high=216.0, low=198.0, close=215.0),   # break up
        ]
        setup = find_engulfing_break(first + second, max_bars_since_break=3)
        assert setup is not None
        assert setup.break_close == 215.0


class TestPrices:
    """The owner's numbers: 1 pip = $0.10, entry 30 pips into the engulfed
    candle, stop 90 pips beyond its extreme, take-profit at 1:2."""

    PIP = 0.10

    def test_long_entry_sits_30_pips_above_the_engulfed_low(self):
        setup = find_engulfing_break(long_setup_bars())
        near, far = entry_zone(setup, self.PIP, 30.0)
        assert near == pytest.approx(102.0)      # 99.0 + $3.00
        assert far == pytest.approx(99.0)        # the candle's own low

    def test_long_stop_sits_90_pips_below_the_engulfed_low(self):
        setup = find_engulfing_break(long_setup_bars())
        assert stop_price(setup, self.PIP, 90.0) == pytest.approx(90.0)   # 99.0 - $9.00

    def test_short_mirrors_the_long_off_the_engulfed_HIGH(self):
        setup = find_engulfing_break(short_setup_bars())
        near, far = entry_zone(setup, self.PIP, 30.0)
        assert near == pytest.approx(108.0)      # 111.0 - $3.00
        assert far == pytest.approx(111.0)
        assert stop_price(setup, self.PIP, 90.0) == pytest.approx(120.0)  # 111.0 + $9.00

    def test_risk_is_always_entry_pips_plus_stop_pips(self):
        setup = find_engulfing_break(long_setup_bars())
        near, _ = entry_zone(setup, self.PIP, 30.0)
        stop = stop_price(setup, self.PIP, 90.0)
        assert abs(near - stop) == pytest.approx(120 * self.PIP)   # $12.00

    def test_take_profit_is_two_R_in_the_trade_direction(self):
        assert take_profit_price(102.0, 90.0, LONG, 2.0) == pytest.approx(126.0)
        assert take_profit_price(108.0, 120.0, SHORT, 2.0) == pytest.approx(84.0)

    def test_a_bigger_pip_size_scales_everything(self):
        # Gold pips differ by broker; getting this wrong moves the trade by a
        # factor of ten, so it must be a setting that actually flows through.
        setup = find_engulfing_break(long_setup_bars())
        near, _ = entry_zone(setup, 1.0, 30.0)
        assert near == pytest.approx(129.0)      # 99.0 + $30.00


class TestBreakStillValid:
    def test_a_long_dies_when_price_trades_back_below_the_engulfed_low(self):
        setup = find_engulfing_break(long_setup_bars())
        assert break_still_valid(setup, 105.0) is True
        assert break_still_valid(setup, 99.0) is False      # exactly at the low
        assert break_still_valid(setup, 95.0) is False

    def test_a_short_dies_when_price_trades_back_above_the_engulfed_high(self):
        setup = find_engulfing_break(short_setup_bars())
        assert break_still_valid(setup, 105.0) is True
        assert break_still_valid(setup, 112.0) is False


class TestBarsFromCandles:
    def test_the_forming_candle_is_dropped(self):
        """Detection must never see an unclosed candle - acting on a close
        that has not happened is the difference between a backtest that works
        and one that lies."""
        candles = to_candles(long_setup_bars(), forming=Bar(115, 118, 114, 117))
        bars = bars_from_candles(candles)
        assert len(bars) == 3
        assert bars[-1].close == 115.0            # the break, not the forming candle

    def test_a_candle_with_no_complete_flag_is_treated_as_forming(self):
        # Absent means "might still be open"; the safe reading is to ignore it.
        raw = to_candles(long_setup_bars())
        assert len(bars_from_candles(raw)) == 3

        incomplete = Candle(raw[-1].time, 115, 118, 114, 117, complete=False)
        assert len(bars_from_candles(raw + [incomplete])) == 3
