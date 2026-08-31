"""
The noise-floor gate: a stop too close to entry is not a tight stop.

Measured on the live venue, 2026-08-30, with every signal the pipeline
produced that day:

    ETH   entry 2432.70    stop 2429.04    0.15%
    DOT   entry 0.85215    stop 0.85359    0.17%
    BTC   entry 77514.55   stop 77353.00   0.21%
    AAVE  entry 120.735    stop 120.24     0.41%

At that distance three things go wrong at once, and the ETH trade hit all
three: a wick decides the trade before the idea does; risk-based sizing
(risk / stop distance) inflates until the notional cap stops it, leaving a
round trip's fees at roughly HALF the whole planned loss; and Binance can
refuse the stop outright with -2021 because price crossed the level between
fill and placement, which is how that position ended up open with no stop.

The gate REJECTS such a signal. It never moves a stop - structure decides
where the stop belongs (the PR #21 lesson: filter, never move a price).
"""
import pytest

from app.strategy.signal_generator import stop_distance_pct, stop_too_close

# The real numbers above.
ETH = (2432.70, 2429.04)
DOT = (0.85215, 0.85358961)
BTC = (77514.55, 77353.00)
AAVE = (120.735, 120.24)

DEFAULT_FLOOR = 0.6


class TestStopDistancePct:
    def test_measures_distance_as_a_percentage_of_entry(self):
        assert stop_distance_pct(100.0, 99.0) == pytest.approx(1.0)
        assert stop_distance_pct(100.0, 101.0) == pytest.approx(1.0)  # direction-agnostic

    def test_degenerate_entry_never_divides_by_zero(self):
        assert stop_distance_pct(0.0, 10.0) == 0.0

    def test_reproduces_the_measured_live_distances(self):
        assert stop_distance_pct(*ETH) == pytest.approx(0.150, abs=0.005)
        assert stop_distance_pct(*DOT) == pytest.approx(0.169, abs=0.005)
        assert stop_distance_pct(*BTC) == pytest.approx(0.208, abs=0.005)
        assert stop_distance_pct(*AAVE) == pytest.approx(0.410, abs=0.005)


class TestStopTooClose:
    @pytest.mark.parametrize("entry,stop", [ETH, DOT, BTC, AAVE])
    def test_every_signal_from_2026_08_30_is_rejected(self, entry, stop):
        assert stop_too_close(entry, stop, DEFAULT_FLOOR) is True

    def test_a_stop_beyond_the_floor_is_allowed(self):
        # 1% away - comfortably outside noise.
        assert stop_too_close(100.0, 99.0, DEFAULT_FLOOR) is False

    def test_exactly_at_the_floor_is_allowed(self):
        # The floor is a minimum, not an exclusive bound. Uses a pair whose
        # distance is exactly representable - 100/99.4 computes to
        # 0.5999999999999943 and would land on the wrong side of the
        # comparison for reasons that have nothing to do with this gate.
        assert stop_distance_pct(1000.0, 994.0) == 0.6
        assert stop_too_close(1000.0, 994.0, DEFAULT_FLOOR) is False

    def test_a_hair_inside_the_floor_is_rejected(self):
        assert stop_too_close(1000.0, 994.5, DEFAULT_FLOOR) is True

    def test_a_degenerate_stop_equal_to_entry_is_rejected(self):
        # Seen live: paper signals trailed to breakeven have zero risk
        # distance, which sizing cannot use at all.
        assert stop_too_close(100.0, 100.0, DEFAULT_FLOOR) is True

    def test_zero_disables_the_gate(self):
        assert stop_too_close(*ETH, 0.0) is False
        assert stop_too_close(100.0, 100.0, 0.0) is False

    def test_works_for_longs_and_shorts_alike(self):
        # The gate cares about distance, not side.
        assert stop_too_close(100.0, 99.9, DEFAULT_FLOOR) is True   # long
        assert stop_too_close(100.0, 100.1, DEFAULT_FLOOR) is True  # short


class TestGateIsWiredIn:
    def test_the_rejection_gate_exists_and_is_named(self):
        from app.ai.ict_decision_engine import RejectionGate

        assert RejectionGate.MIN_STOP_DISTANCE.value == "min_stop_distance"

    def test_the_default_floor_would_have_stopped_every_2026_08_30_signal(self):
        from app.core.config import Settings

        floor = Settings().min_stop_distance_pct
        assert floor > 0
        assert all(stop_too_close(e, s, floor) for e, s in (ETH, DOT, BTC, AAVE))
