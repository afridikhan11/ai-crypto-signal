"""
Quantity rounding, and the dust it used to leave behind.

FOUND ON THE VENUE (2026-09-30)
---------------------------------------------------------------------------
A reconcile against the exchange turned up two positions the bot had no
signal for: 0.1 ARB and 0.1 FIL. Both were worth cents. Both were EXACTLY
one step - ARB and FIL each have a stepSize of 0.1 - and that is what gave
the cause away.

`value / step` is a binary float division. For a fifth of all exact
multiples it lands a hair BELOW the integer (0.3 / 0.1 is
2.9999999999999996), so flooring dropped a whole step. On an order that
only under-sizes by one lot. On a CLOSE it leaves a speck of position
behind - and a speck is still a position, counted against
MAX_OPEN_POSITIONS, quietly eating the bot's allowance.
"""
import math

import pytest

from app.services.binance_trading_service import BinanceTradingService as B

# Real Binance USD-M step sizes, from exchangeInfo.
REAL_STEPS = (1.0, 0.1, 0.01, 0.001)


class TestAnExactMultipleSurvives:
    """The bug, stated directly."""

    @pytest.mark.parametrize("value,step", [
        (0.3, 0.1), (0.6, 0.1), (0.7, 0.1), (1.2, 0.1), (1.4, 0.1), (1.9, 0.1),
        (0.29, 0.01), (0.57, 0.01), (1.13, 0.001),
    ])
    def test_a_value_already_on_the_step_is_returned_unchanged(self, value, step):
        assert B._round_to_step(value, step) == pytest.approx(value)

    def test_the_specific_case_that_left_dust_on_the_venue(self):
        # 0.3 of a coin whose step is 0.1 used to close as 0.2, orphaning 0.1.
        assert B._round_to_step(0.3, 0.1) == pytest.approx(0.3)

    def test_no_exact_multiple_loses_a_step_at_any_real_step_size(self):
        for step in REAL_STEPS:
            for i in range(1, 3000):
                value = round(i * step, 10)
                assert B._round_to_step(value, step) == pytest.approx(value, abs=1e-12), (
                    f"{value} at step {step} lost a step"
                )


class TestItStillFloors:
    """The snap must not become a round-up: over-ordering is the worse bug."""

    @pytest.mark.parametrize("value,step,expected", [
        (0.37, 0.1, 0.3),
        (0.29, 0.1, 0.2),
        (0.999, 0.1, 0.9),
        (1.0999, 0.1, 1.0),
        (5.678, 0.01, 5.67),
        (3.9, 1.0, 3.0),
    ])
    def test_a_genuine_remainder_is_dropped_not_rounded_up(self, value, step, expected):
        assert B._round_to_step(value, step) == pytest.approx(expected)

    def test_the_result_never_exceeds_the_input(self):
        import random

        random.seed(11)
        for step in REAL_STEPS:
            for _ in range(2000):
                value = random.uniform(0, 5000)
                assert B._round_to_step(value, step) <= value + 1e-9

    def test_the_result_is_always_a_multiple_of_the_step(self):
        import random

        random.seed(12)
        for step in REAL_STEPS:
            for _ in range(2000):
                value = random.uniform(0, 5000)
                out = B._round_to_step(value, step)
                assert abs(out / step - round(out / step)) < 1e-6


class TestEdges:
    def test_a_zero_or_negative_step_returns_the_value_untouched(self):
        assert B._round_to_step(1.234, 0.0) == 1.234
        assert B._round_to_step(1.234, -1.0) == 1.234

    def test_a_value_below_one_step_rounds_to_zero(self):
        # The caller refuses a zero quantity - this must stay detectable.
        assert B._round_to_step(0.05, 0.1) == 0.0

    def test_a_large_position_is_unharmed(self):
        # The live APTUSDT position at the time this was found.
        assert B._round_to_step(3338.8, 0.1) == pytest.approx(3338.8)

    def test_whole_number_steps_behave(self):
        assert B._round_to_step(7.0, 1.0) == 7.0
        assert B._round_to_step(7.9, 1.0) == 7.0


class TestWhyNothingCaughtThisSooner:
    """Random testing could never have found it, and that is the lesson.

    On 120,000 random values the old float version and the fixed one agree
    exactly. A random float is essentially never an exact multiple of the
    step, and exact multiples are the only inputs the bug touched.

    But the quantity handed to a CLOSE is the live position size - which came
    out of this very function, so it always IS an exact multiple. The one
    input class that mattered was the one class random testing never
    generates.
    """

    @staticmethod
    def _old(value, step):
        if step <= 0:
            return value
        precision = max(0, round(-math.log10(step)))
        return round(math.floor(value / step) * step, precision)

    def test_random_values_never_showed_the_bug(self):
        import random

        random.seed(7)
        for step in REAL_STEPS:
            for _ in range(3000):
                value = random.uniform(0.01, 5000)
                assert self._old(value, step) == pytest.approx(
                    B._round_to_step(value, step), abs=1e-12
                )

    def test_but_an_exact_multiple_did(self):
        assert self._old(0.3, 0.1) == pytest.approx(0.2)      # the bug
        assert B._round_to_step(0.3, 0.1) == pytest.approx(0.3)   # the fix

    def test_a_close_always_feeds_it_an_exact_multiple(self):
        """Which is why the dust appeared on closes and nowhere else: the
        quantity to close is the live position size, and that came out of
        this same function."""
        sized = B._round_to_step(0.37, 0.1)       # an order was sized
        assert sized == pytest.approx(0.3)
        # ...later, that exact position is closed.
        assert B._round_to_step(sized, 0.1) == pytest.approx(sized)
        assert self._old(sized, 0.1) == pytest.approx(0.2)     # used to orphan 0.1
