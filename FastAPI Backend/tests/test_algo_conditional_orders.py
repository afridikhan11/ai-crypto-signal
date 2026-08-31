"""
Conditional orders on the Algo Order API.

Binance migrated USDS-M conditional orders (STOP_MARKET etc.) to the Algo
service on 2025-12-09; /fapi/v1/order has answered -4120 for them ever since.
That is what left this platform's stops unplaced and forced the monitor onto
its software stop - it was never a Demo-venue limitation. These tests cover
the param translation, the algo-first/legacy-fallback routing (learned once
per host), reading resting stops from BOTH services, and cancelling each on
the endpoint that owns it.
"""
import asyncio

import pytest
from loguru import logger

from app.services import binance_trading_service as bts
from app.services.binance_trading_service import (
    ALGO_CANCEL_OPEN_ORDERS_PATH,
    ALGO_OPEN_ORDERS_PATH,
    ALGO_ORDER_PATH,
    BinanceTradingError,
    BinanceTradingService,
)

STOP_PARAMS = {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "STOP_MARKET",
    "stopPrice": 102.5,
    "quantity": 0.01,
    "reduceOnly": "true",
    "newClientOrderId": "sig-abc-S",
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_learned_routing():
    """The algo/legacy choice is remembered per host - reset it between tests."""
    bts._conditional_uses_algo.clear()
    yield
    bts._conditional_uses_algo.clear()


def _service():
    return BinanceTradingService(api_key="k", api_secret="s", testnet=True)


async def _filters(symbol):
    return {"step_size": 0.001, "tick_size": 0.01, "min_notional": 5.0}


class _Recorder:
    """Stands in for the signed POST/GET/DELETE helpers."""

    def __init__(self, behavior):
        self.calls = []
        self._behavior = behavior

    async def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        result = self._behavior(path, params or {})
        if isinstance(result, Exception):
            raise result
        return result


# ======================================================================
# Param translation
# ======================================================================
class TestAlgoParamTranslation:
    def test_default_shape_matches_binances_own_curl_example(self):
        # Binance documents `--data algoType=CONDITIONAL --data type=STOP_MARKET`
        # for POST /fapi/v1/algoOrder, even though the RESPONSE calls the order
        # type `orderType`. The leading shape follows the documented request.
        algo = BinanceTradingService._to_algo_params(STOP_PARAMS)
        assert algo["algoType"] == "CONDITIONAL"
        assert algo["type"] == "STOP_MARKET"
        assert algo["triggerPrice"] == 102.5
        assert algo["clientAlgoId"] == "sig-abc-S"
        # The order itself is unchanged.
        assert algo["symbol"] == "BTCUSDT" and algo["side"] == "BUY"
        assert algo["quantity"] == 0.01 and algo["reduceOnly"] == "true"

    def test_there_is_exactly_one_shape_and_it_is_the_documented_one(self):
        # Binance's own cURL for POST /fapi/v1/algoOrder sends algoType=CONDITIONAL
        # alongside type=STOP_MARKET. Guessed alternatives used parameters the
        # endpoint does not define, and only served to bury the real error.
        assert bts._ALGO_PARAMS.algo_field == "algoType"
        assert bts._ALGO_PARAMS.order_field == "type"
        algo = BinanceTradingService._to_algo_params(STOP_PARAMS)
        assert algo["algoType"] == "CONDITIONAL"
        assert algo["type"] == "STOP_MARKET"
        # No legacy spelling survives the translation.
        assert "stopPrice" not in algo and "newClientOrderId" not in algo

    def test_does_not_mutate_the_caller_dict(self):
        before = dict(STOP_PARAMS)
        BinanceTradingService._to_algo_params(STOP_PARAMS)
        assert STOP_PARAMS == before

    def test_normalise_exposes_orderid_and_stopprice(self):
        out = BinanceTradingService._normalise_algo_order(
            {"algoId": 77, "triggerPrice": "102.5", "orderType": "STOP_MARKET"}
        )
        assert out["orderId"] == 77          # what the rest of the module reads
        assert out["stopPrice"] == "102.5"
        assert out["type"] == "STOP_MARKET"
        assert out["_is_algo"] is True


# ======================================================================
# Routing: algo first, legacy fallback, learned once
# ======================================================================
class TestConditionalRouting:
    def test_uses_algo_endpoint_and_returns_orderid(self):
        svc = _service()
        post = _Recorder(lambda path, p: {"algoId": 42, "algoStatus": "NEW"})
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 42 and raw["_is_algo"] is True
        assert post.calls[0][0] == ALGO_ORDER_PATH
        sent = post.calls[0][1]
        assert sent["algoType"] == "CONDITIONAL"    # documented request spelling
        assert sent["type"] == "STOP_MARKET"

    def test_falls_back_to_legacy_when_the_algo_ROUTE_does_not_exist(self):
        # 404 = this venue predates the Algo service. That, and only that, is
        # what the legacy fallback is for.
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Unknown endpoint", code=-1121, status=404)
            return {"orderId": 7, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 7
        assert [c[0] for c in post.calls] == [ALGO_ORDER_PATH, "/fapi/v1/order"]
        # The legacy answer is remembered - no repeat probe.
        assert bts._conditional_uses_algo[svc.base_url] is False

    def test_one_algo_attempt_per_order_never_a_ladder(self):
        # A refusal costs exactly ONE algo request - never a burst of doomed
        # spellings whose errors overwrite each other.
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Unknown endpoint", code=-1121, status=404)
            return {"orderId": 3, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert len([c for c in post.calls if c[0] == ALGO_ORDER_PATH]) == 1

    def test_a_BUSINESS_rejection_is_raised_never_re_routed(self):
        # The bug this prevents: on 2026-08-30 the algo service refused an
        # ETHUSDT stop with -2021 "order would immediately trigger" (the 0.15%
        # stop was already breached). The old code treated that as "no algo
        # service here", re-sent it to /fapi/v1/order, and reported the -4120
        # routing error instead - so the log blamed routing for a stop that was
        # simply too close, and the position was left open with no stop at all.
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Order would immediately trigger.", code=-2021, status=400)
            return {"orderId": 3, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        with pytest.raises(BinanceTradingError) as caught:
            _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert caught.value.code == -2021                      # the REAL reason
        assert [c[0] for c in post.calls] == [ALGO_ORDER_PATH]  # never re-sent

    def test_refusal_is_logged_with_the_payload_we_actually_sent(self):
        # The whole point of dropping the ladder: when a stop is refused, the
        # log must carry OUR payload beside BINANCE's answer - and no secrets.
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError(
                    "Mandatory parameter 'algoType' was not sent", code=-1102, status=400
                )
            return {"orderId": 3, "status": "NEW"}

        svc._signed_post = _Recorder(behavior)
        lines: list[str] = []
        sink = logger.add(lines.append, level="WARNING", format="{message}")
        try:
            with pytest.raises(BinanceTradingError):
                _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        finally:
            logger.remove(sink)

        text = "".join(lines)
        assert "algoType" in text and "STOP_MARKET" in text   # what we sent
        assert "triggerPrice" in text and "102.5" in text
        assert "-1102" in text                                 # what Binance said
        assert "signature" not in text                         # never a secret

    def test_loggable_drops_credential_fields(self):
        out = BinanceTradingService._loggable(
            {"symbol": "BTCUSDT", "signature": "deadbeef", "timestamp": 1, "recvWindow": 10000}
        )
        assert out == {"symbol": "BTCUSDT"}

    def test_proven_host_raises_a_real_error_without_probing(self):
        # Once a host's spelling is proven, an order failure is a REAL failure:
        # one attempt, raised, never re-routed to an endpoint that answers -4120.
        svc = _service()
        bts._conditional_uses_algo[svc.base_url] = True

        post = _Recorder(lambda path, p: BinanceTradingError("margin is insufficient", code=-2019))
        svc._signed_post = post

        with pytest.raises(BinanceTradingError):
            _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert len(post.calls) == 1

    def test_unknown_host_falls_back_after_exactly_one_attempt(self):
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Unknown endpoint", code=-1121, status=404)
            return {"orderId": 4, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 4
        assert len([c for c in post.calls if c[0] == ALGO_ORDER_PATH]) == 1
        assert bts._conditional_uses_algo[svc.base_url] is False

    def test_learned_legacy_host_skips_the_algo_probe(self):
        svc = _service()
        bts._conditional_uses_algo[svc.base_url] = False

        post = _Recorder(lambda path, p: {"orderId": 9, "status": "NEW"})
        svc._signed_post = post

        _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert [c[0] for c in post.calls] == ["/fapi/v1/order"]


# ======================================================================
# Only TRIGGER orders may touch the conditional router
#
# This is the regression that actually disarmed every stop in production.
# `place_limit_entry` sent a plain GTC LIMIT through
# `_place_conditional_order`. The Algo API refused it (a LIMIT has no
# trigger price), the legacy fallback succeeded, and that success recorded
# `_conditional_uses_algo[host] = False` - so every REAL stop afterwards
# skipped the Algo endpoint and went to /fapi/v1/order, which answers -4120.
# ONE pending entry per process was enough to leave every later position
# with no exchange stop at all.
# ======================================================================
class TestOnlyTriggerOrdersUseTheAlgoRouter:
    def _svc_recording(self):
        svc = _service()
        post = _Recorder(lambda path, p: {"orderId": 11, "status": "NEW"})
        svc._signed_post = post
        svc._get_symbol_filters = _filters
        return svc, post

    def test_limit_entry_never_touches_the_algo_endpoint(self):
        svc, post = self._svc_recording()
        _run(svc.place_limit_entry("BTCUSDT", "SHORT", 0.01, 50_000.0, signal_id="sig-abc"))
        assert [c[0] for c in post.calls] == ["/fapi/v1/order"]
        sent = post.calls[0][1]
        assert sent["type"] == "LIMIT" and "algoType" not in sent

    def test_limit_entry_does_not_poison_stop_routing(self):
        # The whole bug in one assertion: after a pending entry, a stop must
        # still be offered to the Algo API.
        svc, post = self._svc_recording()
        _run(svc.place_limit_entry("BTCUSDT", "SHORT", 0.01, 50_000.0, signal_id="sig-abc"))
        assert svc.base_url not in bts._conditional_uses_algo

        post.calls.clear()
        _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert post.calls[0][0] == ALGO_ORDER_PATH

    def test_stop_market_entry_does_use_the_algo_endpoint(self):
        # It opens a position rather than closing one, but it is still a
        # trigger order, so /fapi/v1/order would answer -4120.
        svc, post = self._svc_recording()
        _run(svc.place_stop_market_entry("BTCUSDT", "SHORT", 0.01, 50_000.0, signal_id="sig-abc"))
        assert post.calls[0][0] == ALGO_ORDER_PATH
        assert post.calls[0][1]["algoType"] == "CONDITIONAL"


# ======================================================================
# Reading resting stops from both services
# ======================================================================
class TestGetOpenStopOrders:
    def _svc_with(self, algo_rows, legacy_rows):
        svc = _service()

        def behavior(path, p):
            if path == ALGO_OPEN_ORDERS_PATH:
                return algo_rows if algo_rows is not None else BinanceTradingError("n/a")
            return legacy_rows

        svc._signed_get = _Recorder(behavior)
        return svc

    def test_collects_from_both_and_tags_algo_rows(self):
        svc = self._svc_with(
            algo_rows=[{"algoId": 1, "orderType": "STOP_MARKET", "reduceOnly": True,
                        "triggerPrice": "99"}],
            legacy_rows=[{"orderId": 2, "type": "STOP_MARKET", "reduceOnly": "true",
                          "stopPrice": "98"}],
        )
        stops = _run(svc.get_open_stop_orders("BTCUSDT"))
        assert [s["orderId"] for s in stops] == [1, 2]
        assert stops[0]["_is_algo"] is True
        assert stops[1].get("_is_algo") is None      # legacy row, untagged

    def test_ignores_non_stop_and_non_reduceonly_rows(self):
        svc = self._svc_with(
            algo_rows=[
                {"algoId": 1, "orderType": "TAKE_PROFIT_MARKET", "reduceOnly": True},
                {"algoId": 2, "orderType": "STOP_MARKET", "reduceOnly": False},
            ],
            legacy_rows=[{"orderId": 3, "type": "LIMIT", "reduceOnly": "true"}],
        )
        assert _run(svc.get_open_stop_orders("BTCUSDT")) == []

    def test_algo_read_failure_still_returns_legacy_stops(self):
        svc = self._svc_with(
            algo_rows=None,   # host has no algo service
            legacy_rows=[{"orderId": 5, "type": "STOP_MARKET", "reduceOnly": "true"}],
        )
        stops = _run(svc.get_open_stop_orders("BTCUSDT"))
        assert [s["orderId"] for s in stops] == [5]

    def test_accepts_a_wrapped_algo_payload(self):
        svc = self._svc_with(
            algo_rows={"orders": [{"algoId": 8, "orderType": "STOP_MARKET", "reduceOnly": True}]},
            legacy_rows=[],
        )
        assert [s["orderId"] for s in _run(svc.get_open_stop_orders("BTCUSDT"))] == [8]


# ======================================================================
# Cancelling on the right endpoint
# ======================================================================
class TestConditionalCancel:
    def test_algo_cancel_uses_algoid_on_the_algo_path(self):
        svc = _service()
        delete = _Recorder(lambda path, p: {})
        svc._signed_delete = delete

        _run(svc.cancel_conditional_order("btcusdt", 42, is_algo=True))
        path, params = delete.calls[0]
        assert path == ALGO_ORDER_PATH
        assert params == {"symbol": "BTCUSDT", "algoId": 42}

    def test_legacy_cancel_uses_orderid_on_the_order_path(self):
        svc = _service()
        delete = _Recorder(lambda path, p: {})
        svc._signed_delete = delete

        _run(svc.cancel_conditional_order("btcusdt", 7, is_algo=False))
        path, params = delete.calls[0]
        assert path == "/fapi/v1/order"
        assert params == {"symbol": "BTCUSDT", "orderId": 7}

    def test_cancel_all_conditional_sweeps_the_algo_service(self):
        svc = _service()
        delete = _Recorder(lambda path, p: {})
        svc._signed_delete = delete

        _run(svc.cancel_all_conditional_orders("btcusdt"))
        assert delete.calls[0] == (ALGO_CANCEL_OPEN_ORDERS_PATH, {"symbol": "BTCUSDT"})


# ======================================================================
# Protective stops trigger on MARK price, not the last trade
#
# Binance defaults an unspecified workingType to CONTRACT_PRICE (last traded
# price), which one thin print can move. On a shallow book that fires a stop
# the real market never reached and books the full planned loss for nothing.
# ======================================================================
class TestProtectiveStopWorkingType:
    def _svc(self):
        svc = _service()
        post = _Recorder(lambda path, p: {"orderId": 1, "algoId": 1, "status": "NEW", "avgPrice": "100"})
        svc._signed_post = post
        svc._get_symbol_filters = _filters
        return svc, post

    def _stop_payload(self, post):
        """The PROTECTIVE stop - it closes, so it carries closePosition or
        reduceOnly. An entry trigger carries neither."""
        return next(
            p for _, p in post.calls
            if p.get("type") == "STOP_MARKET"
            and (_is_true_str(p.get("closePosition")) or _is_true_str(p.get("reduceOnly")))
        )

    def test_bracket_stop_uses_mark_price(self):
        svc, post = self._svc()
        _run(svc.place_signal_bracket(
            symbol="BTCUSDT", direction="SHORT", quantity=1.0,
            stop_loss=105.0, take_profit=90.0, entry_price=100.0, signal_id="sig-abc",
        ))
        assert self._stop_payload(post)["workingType"] == "MARK_PRICE"

    def test_replacement_stop_uses_mark_price(self):
        svc, post = self._svc()
        svc.get_open_stop_orders = lambda symbol: _noop_list()
        svc.cancel_conditional_order = lambda *a, **k: _noop_none()
        _run(svc.replace_stop_loss(
            symbol="BTCUSDT", direction="SHORT", quantity=1.0,
            new_stop_price=104.0, signal_id="sig-abc",
        ))
        assert self._stop_payload(post)["workingType"] == "MARK_PRICE"

    def test_stop_market_ENTRY_still_uses_the_traded_price(self):
        # An entry trigger means "price genuinely traded through this level",
        # so CONTRACT_PRICE is correct there and must not be swept along.
        svc, post = self._svc()
        _run(svc.place_stop_market_entry("BTCUSDT", "SHORT", 1.0, 95.0, signal_id="sig-abc"))
        entry = next(p for _, p in post.calls if p.get("type") == "STOP_MARKET")
        assert entry["workingType"] == "CONTRACT_PRICE"
        assert "reduceOnly" not in entry


async def _noop_list():
    return []


async def _noop_none():
    return None


def _is_true_str(v):
    return v in (True, "true", "True")


# ======================================================================
# "Cancel Pending Orders" must be able to SEE a stop
#
# Stops live on the Algo service since 2025-12-09. A legacy-only read left
# the Control Panel's emergency button blind to exactly the orders an
# operator reaches for it to clear.
# ======================================================================
class TestGetAllOpenOrdersSpansBothServices:
    def _svc(self, algo_rows, legacy_rows):
        svc = _service()

        async def fake_get(path, params=None):
            if path == ALGO_OPEN_ORDERS_PATH:
                if isinstance(algo_rows, Exception):
                    raise algo_rows
                return algo_rows
            return legacy_rows

        svc._signed_get = fake_get
        return svc

    def test_returns_algo_and_legacy_rows_together(self):
        svc = self._svc(
            algo_rows={"orders": [{"algoId": 9, "orderType": "STOP_MARKET", "symbol": "BTCUSDT"}]},
            legacy_rows=[{"orderId": 4, "type": "LIMIT", "symbol": "BTCUSDT"}],
        )
        rows = _run(svc.get_all_open_orders())
        assert sorted(r["orderId"] for r in rows) == [4, 9]
        # The algo row is tagged so the caller can cancel it correctly.
        assert next(r for r in rows if r["orderId"] == 9)["_is_algo"] is True
        assert next(r for r in rows if r["orderId"] == 4).get("_is_algo") is None

    def test_a_venue_without_the_algo_service_still_returns_legacy_orders(self):
        svc = self._svc(
            algo_rows=BinanceTradingError("no algo service", code=-1121),
            legacy_rows=[{"orderId": 4, "type": "LIMIT", "symbol": "BTCUSDT"}],
        )
        assert [r["orderId"] for r in _run(svc.get_all_open_orders())] == [4]

    def test_cancel_any_order_routes_by_owner(self):
        svc = _service()
        delete = _Recorder(lambda path, p: {})
        svc._signed_delete = delete

        _run(svc.cancel_any_order("BTCUSDT", {"orderId": 9, "_is_algo": True}))
        _run(svc.cancel_any_order("BTCUSDT", {"orderId": 4}))

        assert delete.calls[0] == (ALGO_ORDER_PATH, {"symbol": "BTCUSDT", "algoId": 9})
        assert delete.calls[1] == ("/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": 4})


# ======================================================================
# Whole-position stops (closePosition=true)
#
# A quantity-carrying stop protects the size it was placed with, and that
# size drifts: the partial TP banks 50%, a TP LIMIT part-fills, a MARKET
# entry fills slightly short. Whatever the stop does not name survives the
# trigger as unprotected residual - the dust left open after AAVE's TP_HIT.
# ======================================================================
class TestClosePositionStops:
    def _svc(self):
        svc = _service()
        post = _Recorder(lambda path, p: {"orderId": 1, "algoId": 1, "status": "NEW", "avgPrice": "100"})
        svc._signed_post = post
        svc._get_symbol_filters = _filters
        return svc, post

    def test_stop_closes_the_whole_position_and_names_no_quantity(self):
        params = BinanceTradingService._protective_stop_params("BTCUSDT", "BUY", 105.0, 1.0)
        assert params["closePosition"] == "true"
        # Mutually exclusive with both - Binance rejects the combination.
        assert "quantity" not in params and "reduceOnly" not in params
        assert params["workingType"] == "MARK_PRICE"

    def test_sized_stop_when_the_setting_is_off(self, monkeypatch):
        monkeypatch.setattr(
            bts, "get_settings", lambda: _SettingsStub(close_position=False)
        )
        params = BinanceTradingService._protective_stop_params("BTCUSDT", "BUY", 105.0, 1.0)
        assert params["quantity"] == 1.0 and params["reduceOnly"] == "true"
        assert "closePosition" not in params

    def test_bracket_stop_uses_close_position(self):
        svc, post = self._svc()
        _run(svc.place_signal_bracket(
            symbol="BTCUSDT", direction="SHORT", quantity=1.0,
            stop_loss=105.0, take_profit=90.0, entry_price=100.0, signal_id="sig-abc",
        ))
        stop = next(p for _, p in post.calls if p.get("type") == "STOP_MARKET")
        assert stop["closePosition"] == "true"

    def test_a_close_position_stop_is_still_recognised_when_read_back(self):
        # It reports reduceOnly=FALSE, so a reduceOnly-only filter would hide
        # exactly the stops this service now places.
        assert bts._is_protective({"closePosition": True, "reduceOnly": False}) is True
        assert bts._is_protective({"reduceOnly": "true"}) is True
        # An ENTRY trigger opens a position and is neither.
        assert bts._is_protective({"reduceOnly": False}) is False

    def test_get_open_stop_orders_finds_a_close_position_stop(self):
        svc = _service()

        async def fake_get(path, params=None):
            if path == ALGO_OPEN_ORDERS_PATH:
                return {"orders": [{
                    "algoId": 5, "orderType": "STOP_MARKET",
                    "closePosition": True, "reduceOnly": False, "triggerPrice": "105",
                }]}
            return []

        svc._signed_get = fake_get
        assert [s["orderId"] for s in _run(svc.get_open_stop_orders("BTCUSDT"))] == [5]

    def test_replacement_is_not_refused_for_dust_too_small_to_round(self):
        # qty rounds to 0, but a closePosition stop names no quantity - the
        # residual is precisely what it should be protecting.
        svc, post = self._svc()
        svc.get_open_stop_orders = lambda symbol: _noop_list()
        result = _run(svc.replace_stop_loss(
            symbol="BTCUSDT", direction="SHORT", quantity=0.0000001,
            new_stop_price=104.0, signal_id="sig-abc",
        ))
        assert result.success is True
        assert next(p for _, p in post.calls if p.get("type") == "STOP_MARKET")["closePosition"] == "true"


class _SettingsStub:
    def __init__(self, close_position):
        self.stop_close_position = close_position
        self.stop_working_type = "MARK_PRICE"


# ======================================================================
# Algo response fields the rest of the module reads
# ======================================================================
class TestAlgoStatusIsMapped:
    def test_algo_status_becomes_status(self):
        # Without this an algo stop reports None -> callers default it to
        # "NEW" forever, even after it has TRIGGERED or been CANCELLED.
        out = BinanceTradingService._normalise_algo_order(
            {"algoId": 3358, "orderType": "STOP_MARKET", "algoStatus": "TRIGGERED"}
        )
        assert out["status"] == "TRIGGERED"

    def test_an_existing_status_is_never_overwritten(self):
        out = BinanceTradingService._normalise_algo_order(
            {"algoId": 1, "status": "CANCELED", "algoStatus": "NEW"}
        )
        assert out["status"] == "CANCELED"

    def test_maps_the_documented_algo_response_end_to_end(self):
        # Binance's own linear-swap conditional response shape.
        out = BinanceTradingService._normalise_algo_order({
            "algoId": 3358, "clientAlgoId": "yT58zmV3DSzMBQxc5tAJXU",
            "algoType": "CONDITIONAL", "orderType": "STOP_MARKET",
            "symbol": "BTCUSDT", "side": "BUY", "positionSide": "BOTH",
            "algoStatus": "NEW", "triggerPrice": "100000.00",
            "closePosition": False, "reduceOnly": False,
        })
        assert out["orderId"] == 3358
        assert out["type"] == "STOP_MARKET"
        assert out["stopPrice"] == "100000.00"
        assert out["status"] == "NEW"
        assert out["_is_algo"] is True


# ======================================================================
# Client order ids stay inside Binance's 36-char cap
# ======================================================================
class TestClientOrderIdLength:
    SIGNAL_ID = "314c0f10-b0fe-4ed8-95f1-276d08f7948f"

    def test_entry_leg_id_fits(self):
        oid = BinanceTradingService._client_order_id(self.SIGNAL_ID, "S")["newClientOrderId"]
        assert len(oid) <= 36 and oid.isalnum()

    def test_stop_replacement_id_has_headroom(self):
        # It used to be EXACTLY 36 - correct, but one character from breaking
        # the moment anything was appended.
        oid = BinanceTradingService._stop_replacement_client_order_id(
            self.SIGNAL_ID, 104.5
        )["newClientOrderId"]
        assert len(oid) == 33
        assert oid.isalnum()

    def test_stop_replacement_id_is_deterministic_per_decision(self):
        a = BinanceTradingService._stop_replacement_client_order_id(self.SIGNAL_ID, 104.5)
        b = BinanceTradingService._stop_replacement_client_order_id(self.SIGNAL_ID, 104.5)
        c = BinanceTradingService._stop_replacement_client_order_id(self.SIGNAL_ID, 104.6)
        assert a == b            # same decision -> same id
        assert a != c            # a genuinely new stop price -> new id

    def test_no_signal_id_means_no_override(self):
        assert BinanceTradingService._client_order_id(None, "S") is None
        assert BinanceTradingService._stop_replacement_client_order_id(None, 1.0) is None


# ======================================================================
# Hedge mode: refuse to trade rather than fail every protective order
# ======================================================================
class TestHedgeModeGuard:
    def setup_method(self):
        bts._hedge_mode_cache.clear()

    def teardown_method(self):
        bts._hedge_mode_cache.clear()

    def _svc(self, dual_side):
        svc = _service()

        async def fake_get(path, params=None):
            if path == bts._POSITION_MODE_PATH:
                if isinstance(dual_side, Exception):
                    raise dual_side
                return {"dualSidePosition": dual_side}
            return {}

        svc._signed_get = fake_get
        sent = []

        async def fake_request(method, path, params):
            sent.append((method, path, dict(params)))
            return {"orderId": 1, "status": "NEW"}

        svc._signed_request = fake_request
        svc.sent = sent
        return svc

    def test_hedge_mode_refuses_before_anything_is_placed(self):
        # The failure this prevents: in hedge mode the entry succeeds and then
        # EVERY protective order is rejected, so the problem only shows up once
        # a position already exists.
        svc = self._svc(True)
        with pytest.raises(BinanceTradingError) as caught:
            _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        assert "HEDGE" in str(caught.value)
        assert svc.sent == []                          # nothing was sent

    def test_one_way_mode_places_normally(self):
        svc = self._svc(False)
        _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        assert len(svc.sent) == 1

    def test_string_true_counts_as_hedge(self):
        # Binance returns booleans as real bools on some paths, strings on others.
        svc = self._svc("true")
        with pytest.raises(BinanceTradingError):
            _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))

    def test_the_answer_is_cached_not_read_per_order(self):
        svc = self._svc(False)
        reads = {"n": 0}
        inner = svc._signed_get

        async def counting_get(path, params=None):
            if path == bts._POSITION_MODE_PATH:
                reads["n"] += 1
            return await inner(path, params)

        svc._signed_get = counting_get
        _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        assert reads["n"] == 1

    def test_a_failed_mode_read_does_not_block_trading(self):
        # Refusing to trade because one auxiliary GET timed out would be its
        # own outage. One-way is what every prior release already assumed.
        svc = self._svc(BinanceTradingError("timeout", code=-1001, status=500))
        _run(svc._signed_post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        assert len(svc.sent) == 1
