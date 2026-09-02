"""
Live Execution Safety, Objectives 2/3/4/6 - tests for
`BinanceTradingService.replace_stop_loss()` and `get_open_stop_orders()`.

Exercises the REAL service class with its network-touching methods
(`_get_symbol_filters`, `get_open_stop_orders`, `_signed_post`,
`cancel_order`) monkeypatched to simulate Binance responses - no real HTTP
call is made, but every line of `replace_stop_loss`'s own ordering/retry/
idempotency logic runs for real.
"""
import asyncio

import httpx
import pytest

from app.services.binance_trading_service import (
    BinanceTradingError,
    BinanceTradingService,
)

FILTERS = {"step_size": 0.001, "tick_size": 0.01, "min_notional": 5.0}


@pytest.fixture(autouse=True)
def _sized_stops():
    """These tests describe the place-NEW-then-cancel-OLD ordering, which is
    the contract for SIZED reduceOnly stops.

    A closePosition stop cannot follow it: Binance permits only one per side
    and answers -4130 for a second, so that mode cancels first and restores
    the old stop if the placement fails. That path has its own tests in
    tests/test_algo_conditional_orders.py::TestClosePositionStopMove."""
    from app.services import binance_trading_service as bts

    class _Sized:
        stop_close_position = False
        stop_working_type = "MARK_PRICE"

    original = bts.get_settings
    bts.get_settings = lambda: _Sized()
    yield
    bts.get_settings = original


def _run(coro):
    return asyncio.run(coro)


def _service():
    return BinanceTradingService(api_key="k", api_secret="s", testnet=True)


async def _fake_filters(symbol):
    return FILTERS


class TestReplaceStopLossOrdering:
    """Objective 2/3 - new stop placed BEFORE old one is cancelled, and a
    failure at either step never leaves the position unprotected."""

    def test_places_new_stop_before_cancelling_old(self):
        svc = _service()
        call_order = []

        async def fake_get_open_stop_orders(symbol):
            return [{"orderId": 111, "type": "STOP_MARKET", "reduceOnly": True, "stopPrice": "90.0"}]

        async def fake_signed_post(path, params):
            call_order.append(("post", params.get("triggerPrice", params.get("stopPrice"))))
            return {"orderId": 222, "status": "NEW"}

        async def fake_cancel_order(symbol, order_id, is_algo=False):
            call_order.append(("cancel", order_id))

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = fake_signed_post
        svc.cancel_conditional_order = fake_cancel_order

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="11111111-1111-1111-1111-111111111111"))

        assert result.success is True
        assert result.new_order.order_id == 222
        assert result.cancelled_order_ids == [111]
        assert call_order[0][0] == "post"
        assert call_order[1] == ("cancel", 111)

    def test_new_stop_failure_leaves_old_stop_completely_untouched(self):
        svc = _service()
        cancel_called = []

        async def fake_get_open_stop_orders(symbol):
            return [{"orderId": 111, "type": "STOP_MARKET", "reduceOnly": True}]

        async def fake_signed_post(path, params):
            raise BinanceTradingError("simulated rejection")

        async def fake_cancel_order(symbol, order_id, is_algo=False):
            cancel_called.append(order_id)

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = fake_signed_post
        svc.cancel_conditional_order = fake_cancel_order

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is False
        assert cancel_called == []
        assert "untouched" in result.warning

    def test_cancel_failure_does_not_downgrade_success(self):
        """A stray, uncancelled old stop is redundant protection, not a
        safety failure - success must stay True, with a warning attached."""
        svc = _service()

        async def fake_get_open_stop_orders(symbol):
            return [{"orderId": 111, "type": "STOP_MARKET", "reduceOnly": True}]

        async def fake_signed_post(path, params):
            return {"orderId": 222, "status": "NEW"}

        async def fake_cancel_order(symbol, order_id, is_algo=False):
            raise BinanceTradingError("cancel failed")

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = fake_signed_post
        svc.cancel_conditional_order = fake_cancel_order

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is True
        assert result.cancelled_order_ids == []
        assert result.warning is not None

    def test_cannot_read_existing_stops_refuses_to_proceed(self):
        """If we don't know what's currently resting, we must not blindly
        place a new stop and risk a confusing double-protection state we
        can't reason about - refuse instead."""
        svc = _service()

        async def fake_get_open_stop_orders(symbol):
            raise BinanceTradingError("openOrders lookup failed")

        placed = {"called": False}

        async def fake_signed_post(path, params):
            placed["called"] = True
            return {"orderId": 222, "status": "NEW"}

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = fake_signed_post

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is False
        assert placed["called"] is False

    def test_zero_quantity_refuses_only_when_the_stop_is_a_SIZED_one(self):
        """A sized reduceOnly stop with no quantity would protect nothing, so
        it is refused. A closePosition stop names no quantity at all - and a
        residual too small to round to a step is exactly the dust it exists to
        flatten - so refusing there would strand it (task #12)."""
        from app.services import binance_trading_service as bts

        svc = _service()

        async def fake_filters_tiny_step(symbol):
            return {"step_size": 1.0, "tick_size": 0.01, "min_notional": 5.0}

        svc._get_symbol_filters = fake_filters_tiny_step

        class _Sized:
            stop_close_position = False
            stop_working_type = "MARK_PRICE"

        original = bts.get_settings
        bts.get_settings = lambda: _Sized()
        try:
            result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 0.0004, 95.0, signal_id="sig-1"))
        finally:
            bts.get_settings = original

        assert result.success is False
        assert "rounds down to 0" in (result.warning or "")


class TestExchangeTimeoutAndRetry:
    """Objective 6 - a transient network failure placing the NEW stop is
    retried a bounded number of times before giving up; a definitive
    Binance rejection is never retried."""

    def test_timeout_is_retried_and_eventually_succeeds(self):
        svc = _service()
        attempts = {"n": 0}

        async def fake_get_open_stop_orders(symbol):
            return []

        async def flaky_signed_post(path, params):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise httpx.TimeoutException("simulated timeout")
            return {"orderId": 999, "status": "NEW"}

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = flaky_signed_post
        svc.STOP_PLACEMENT_RETRY_DELAY_SECONDS = 0.0  # don't slow the test down

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is True
        assert attempts["n"] == 2

    def test_repeated_timeout_exhausts_retries_and_leaves_old_stop_active(self):
        svc = _service()
        attempts = {"n": 0}

        async def fake_get_open_stop_orders(symbol):
            return [{"orderId": 111, "type": "STOP_MARKET", "reduceOnly": True}]

        async def always_times_out(path, params):
            attempts["n"] += 1
            raise httpx.ConnectTimeout("simulated connect timeout")

        cancel_called = []

        async def fake_cancel_order(symbol, order_id, is_algo=False):
            cancel_called.append(order_id)

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = always_times_out
        svc.cancel_conditional_order = fake_cancel_order
        svc.STOP_PLACEMENT_RETRY_DELAY_SECONDS = 0.0

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is False
        assert attempts["n"] == svc.STOP_PLACEMENT_MAX_ATTEMPTS
        assert cancel_called == []  # old stop never touched

    def test_binance_rejection_is_not_retried(self):
        svc = _service()
        attempts = {"n": 0}

        async def fake_get_open_stop_orders(symbol):
            return []

        async def rejects_immediately(path, params):
            attempts["n"] += 1
            raise BinanceTradingError("invalid stop price")

        svc._get_symbol_filters = _fake_filters
        svc.get_open_stop_orders = fake_get_open_stop_orders
        svc._signed_post = rejects_immediately

        result = _run(svc.replace_stop_loss("BTCUSDT", "LONG", 1.0, 95.0, signal_id="sig-1"))

        assert result.success is False
        assert attempts["n"] == 1  # no retry for a definitive rejection


class TestIdempotency:
    """Objective 4 - a retried/duplicated request for the SAME trade-
    management decision (same signal, same target price) must use the same
    clientOrderId, so Binance itself rejects the duplicate rather than a
    second stop order being opened."""

    def test_same_signal_and_price_produces_the_same_client_order_id(self):
        id1 = BinanceTradingService._stop_replacement_client_order_id("sig-1", 95.0)
        id2 = BinanceTradingService._stop_replacement_client_order_id("sig-1", 95.0)
        assert id1 == id2

    def test_different_price_produces_a_different_client_order_id(self):
        id1 = BinanceTradingService._stop_replacement_client_order_id("sig-1", 95.0)
        id2 = BinanceTradingService._stop_replacement_client_order_id("sig-1", 96.0)
        assert id1 != id2

    def test_different_signal_produces_a_different_client_order_id(self):
        id1 = BinanceTradingService._stop_replacement_client_order_id("sig-1", 95.0)
        id2 = BinanceTradingService._stop_replacement_client_order_id("sig-2", 95.0)
        assert id1 != id2

    def test_client_order_id_stays_within_binance_length_cap(self):
        result = BinanceTradingService._stop_replacement_client_order_id(
            "11111111-1111-1111-1111-111111111111", 12345.6789,
        )
        assert len(result["newClientOrderId"]) <= 36

    def test_no_signal_id_means_no_override(self):
        assert BinanceTradingService._stop_replacement_client_order_id(None, 95.0) is None

    def test_client_order_id_is_stable_across_fresh_process_style_calls(self):
        """Uses hashlib (stable) rather than the builtin hash() (randomized
        per process via PYTHONHASHSEED) - re-deriving the id via a fresh,
        independent computation must match."""
        import hashlib
        signal_id, price = "sig-42", 101.25
        expected_digest = hashlib.sha256(f"{signal_id}:{round(price, 8)}".encode("utf-8")).hexdigest()[:8]
        result = BinanceTradingService._stop_replacement_client_order_id(signal_id, price)
        assert result["newClientOrderId"].endswith(expected_digest)


class TestGetOpenStopOrders:
    def test_filters_to_stop_market_reduce_only_only(self):
        svc = _service()

        async def fake_signed_get(path, params=None):
            return [
                {"orderId": 1, "type": "STOP_MARKET", "reduceOnly": True},
                {"orderId": 2, "type": "LIMIT", "reduceOnly": True},
                {"orderId": 3, "type": "STOP_MARKET", "reduceOnly": False},
                {"orderId": 4, "type": "STOP_MARKET", "reduceOnly": "true"},
            ]

        svc._signed_get = fake_signed_get
        orders = _run(svc.get_open_stop_orders("BTCUSDT"))
        ids = {o["orderId"] for o in orders}
        assert ids == {1, 4}

    def test_non_list_response_returns_empty(self):
        svc = _service()

        async def fake_signed_get(path, params=None):
            return {"unexpected": "shape"}

        svc._signed_get = fake_signed_get
        assert _run(svc.get_open_stop_orders("BTCUSDT")) == []
