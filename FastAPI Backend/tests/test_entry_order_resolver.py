"""
Locks the Final Architecture Specification §7 execution matrix (A5-lite,
G2 - 2026-08-01).

Every case here was first proven by direct execution before this file was
written; this suite keeps it proven on every run. The resolver is pure
stdlib, so these tests need no database, no network and no app fixtures.

The one non-obvious case is the incident that motivated the whole change:
a SHORT with entry=100.445 while the market is at 97 must resolve to a
resting LIMIT SELL (wait for price to come back up), never a MARKET sell
at 97 - which is what the status-based execution path did on 2026-07-31.
"""
import dataclasses

import pytest

from app.services.entry_order_resolver import (
    BAND_POLICY_IMMEDIATE_MARKET,
    DEFAULT_BAND_TICKS,
    EntryInsideBandError,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_MARKET,
    RECOMMEND_EXECUTE_MARKET,
    RECOMMEND_PLACE_LIMIT,
    RECOMMEND_PLACE_STOP,
    RECOMMEND_SIGNAL_EXPIRED,
    RECOMMEND_WAIT,
    recommend_execution,
    resolve_entry_order_type,
)

TICK = 0.001
BAND = DEFAULT_BAND_TICKS * TICK  # 0.005


class TestTheFourMatrixCells:
    """Spec §7, verbatim."""

    @pytest.mark.parametrize(
        "direction, entry, current, expected",
        [
            ("LONG", 97.0, 100.0, ORDER_TYPE_LIMIT),          # LONG below -> LIMIT BUY
            ("LONG", 103.0, 100.0, ORDER_TYPE_STOP_MARKET),   # LONG above -> STOP BUY
            ("SHORT", 103.0, 100.0, ORDER_TYPE_LIMIT),        # SHORT above -> LIMIT SELL
            ("SHORT", 97.0, 100.0, ORDER_TYPE_STOP_MARKET),   # SHORT below -> STOP SELL
        ],
    )
    def test_matrix(self, direction, entry, current, expected):
        resolved = resolve_entry_order_type(direction, entry, current, TICK)
        assert resolved.order_type == expected
        # Every decision must explain itself.
        assert resolved.reason
        assert str(entry) in resolved.reason or f"{entry}" in resolved.reason

    def test_the_aave_incident_case(self):
        """SHORT entry 100.445, market 97: must REST above the market and
        wait - the exact scenario that was market-sold at ~97 under the
        status-based path."""
        resolved = resolve_entry_order_type("SHORT", 100.445, 97.0, TICK)
        assert resolved.order_type == ORDER_TYPE_LIMIT

    def test_result_is_immutable(self):
        resolved = resolve_entry_order_type("LONG", 97.0, 100.0, TICK)
        with pytest.raises(dataclasses.FrozenInstanceError):
            resolved.order_type = ORDER_TYPE_MARKET  # type: ignore[misc]


class TestBandBehaviour:
    def test_inside_band_default_policy_rejects(self):
        with pytest.raises(EntryInsideBandError):
            resolve_entry_order_type("LONG", 100.003, 100.0, TICK)

    def test_exactly_on_the_band_edge_counts_as_inside(self):
        """abs(distance) <= band, not < : the edge is still a de-facto
        immediate fill, so it must not slip through as a resting order."""
        with pytest.raises(EntryInsideBandError):
            resolve_entry_order_type("LONG", 100.0 + BAND, 100.0, TICK)

    def test_one_tick_past_the_edge_resolves(self):
        resolved = resolve_entry_order_type("LONG", 100.0 + BAND + TICK, 100.0, TICK)
        assert resolved.order_type == ORDER_TYPE_STOP_MARKET

    def test_inside_band_immediate_market_policy(self):
        resolved = resolve_entry_order_type(
            "LONG", 100.003, 100.0, TICK, band_policy=BAND_POLICY_IMMEDIATE_MARKET
        )
        assert resolved.order_type == ORDER_TYPE_MARKET

    def test_band_error_carries_the_numbers(self):
        """The error is user-facing (HTTP 409 detail) - it must state the
        entry, the price and the band, not just 'rejected'."""
        with pytest.raises(EntryInsideBandError) as exc_info:
            resolve_entry_order_type("SHORT", 100.001, 100.0, TICK)
        message = str(exc_info.value)
        assert "100.001" in message and "100.0" in message


class TestInvalidInputsFailLoudly:
    """A wrong input must never resolve to a guessed order type."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(direction="UP", entry_price=1.0, current_price=2.0, tick_size=TICK),
            dict(direction="LONG", entry_price=0.0, current_price=2.0, tick_size=TICK),
            dict(direction="LONG", entry_price=1.0, current_price=0.0, tick_size=TICK),
            dict(direction="LONG", entry_price=1.0, current_price=2.0, tick_size=0.0),
            dict(direction="LONG", entry_price=1.0, current_price=2.0, tick_size=TICK,
                 band_ticks=-1),
            dict(direction="LONG", entry_price=1.0, current_price=2.0, tick_size=TICK,
                 band_policy="yolo"),
        ],
    )
    def test_rejects(self, kwargs):
        with pytest.raises(ValueError):
            resolve_entry_order_type(**kwargs)


class TestDisplayRecommendation:
    """Signals-screen text only - recommending executes nothing."""

    def test_expired_wins_over_everything(self):
        assert recommend_execution("SHORT", 100.445, 97.0, TICK, expired=True) \
            == RECOMMEND_SIGNAL_EXPIRED

    def test_already_executed_waits(self):
        assert recommend_execution("SHORT", 100.445, 97.0, TICK, already_executed=True) \
            == RECOMMEND_WAIT

    def test_missing_market_data_waits_instead_of_guessing(self):
        assert recommend_execution("SHORT", 100.445, None, TICK) == RECOMMEND_WAIT
        assert recommend_execution("SHORT", 100.445, 97.0, None) == RECOMMEND_WAIT
        assert recommend_execution("SHORT", 100.445, 0.0, TICK) == RECOMMEND_WAIT

    @pytest.mark.parametrize(
        "direction, entry, current, expected",
        [
            ("SHORT", 100.445, 97.0, RECOMMEND_PLACE_LIMIT),
            ("SHORT", 97.0, 100.0, RECOMMEND_PLACE_STOP),
            ("LONG", 97.0, 100.0, RECOMMEND_PLACE_LIMIT),
            ("LONG", 103.0, 100.0, RECOMMEND_PLACE_STOP),
            ("LONG", 100.001, 100.0, RECOMMEND_EXECUTE_MARKET),
        ],
    )
    def test_recommendations(self, direction, entry, current, expected):
        assert recommend_execution(direction, entry, current, TICK) == expected
