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
    bts._algo_param_variant.clear()
    yield
    bts._conditional_uses_algo.clear()
    bts._algo_param_variant.clear()


def _service():
    return BinanceTradingService(api_key="k", api_secret="s", testnet=True)


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
    def test_default_payload_keeps_legacy_spellings(self):
        # The live service demanded `type` (-1102) even though its responses
        # carry `orderType`, so the default payload changes nothing but adds
        # algoType.
        algo = BinanceTradingService._to_algo_params(STOP_PARAMS)
        assert algo["algoType"] == "CONDITIONAL"
        assert algo["type"] == "STOP_MARKET"
        assert algo["stopPrice"] == 102.5
        assert algo["newClientOrderId"] == "sig-abc-S"
        assert algo["symbol"] == "BTCUSDT" and algo["side"] == "BUY"
        assert algo["quantity"] == 0.01 and algo["reduceOnly"] == "true"

    def test_renames_apply_when_a_host_needs_them(self):
        algo = BinanceTradingService._to_algo_params(
            STOP_PARAMS, {"stopPrice": "triggerPrice", "newClientOrderId": "clientAlgoId"}
        )
        assert algo["triggerPrice"] == 102.5 and "stopPrice" not in algo
        assert algo["clientAlgoId"] == "sig-abc-S" and "newClientOrderId" not in algo
        assert algo["type"] == "STOP_MARKET"      # untouched by these renames

    def test_does_not_mutate_the_caller_dict(self):
        before = dict(STOP_PARAMS)
        BinanceTradingService._to_algo_params(STOP_PARAMS, {"stopPrice": "triggerPrice"})
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
        assert post.calls[0][1]["algoType"] == "CONDITIONAL"

    def test_falls_back_to_legacy_when_algo_rejects(self):
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("no algo service here", code=-1121)
            return {"orderId": 7, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 7
        assert [c[0] for c in post.calls] == [ALGO_ORDER_PATH, "/fapi/v1/order"]
        # The legacy answer is remembered - no repeat probe.
        assert bts._conditional_uses_algo[svc.base_url] is False

    def test_ladder_advances_past_a_malformed_payload_and_remembers(self):
        # Reproduces what demo-fapi actually did: reject one spelling with
        # -1102, accept the next. The winning variant is then reused, so the
        # ladder is walked once per host, not once per order.
        svc = _service()

        def behavior(path, p):
            if "triggerPrice" not in p:
                return BinanceTradingError(
                    "Mandatory parameter 'triggerPrice' was not sent", code=-1102
                )
            return {"algoId": 55, "algoStatus": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 55
        assert len(post.calls) == 2                       # variant 0 rejected, 1 accepted
        assert bts._algo_param_variant[svc.base_url] == 1

        post.calls.clear()
        _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert len(post.calls) == 1                       # straight to the proven spelling
        assert "triggerPrice" in post.calls[0][1]

    def test_proven_host_raises_a_real_error_without_probing(self):
        # Once a host's spelling is proven, an order failure is a REAL failure:
        # one attempt, raised, never re-routed to an endpoint that answers -4120.
        svc = _service()
        bts._conditional_uses_algo[svc.base_url] = True
        bts._algo_param_variant[svc.base_url] = 0

        post = _Recorder(lambda path, p: BinanceTradingError("margin is insufficient", code=-2019))
        svc._signed_post = post

        with pytest.raises(BinanceTradingError):
            _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert len(post.calls) == 1

    def test_unknown_host_stops_probing_on_a_non_spelling_error(self):
        # A host with no algo service at all answers something other than
        # -1102: stop walking the ladder immediately and use legacy.
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Unknown endpoint", code=-1121)
            return {"orderId": 4, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 4
        # Exactly ONE algo attempt - the other spellings are pointless here.
        assert len([c for c in post.calls if c[0] == ALGO_ORDER_PATH]) == 1

    def test_falls_back_to_legacy_only_after_every_spelling_fails(self):
        svc = _service()

        def behavior(path, p):
            if path == ALGO_ORDER_PATH:
                return BinanceTradingError("Mandatory parameter 'x' was not sent", code=-1102)
            return {"orderId": 3, "status": "NEW"}

        post = _Recorder(behavior)
        svc._signed_post = post

        raw = _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert raw["orderId"] == 3
        algo_attempts = [c for c in post.calls if c[0] == ALGO_ORDER_PATH]
        assert len(algo_attempts) == len(bts._ALGO_PARAM_VARIANTS)
        assert bts._conditional_uses_algo[svc.base_url] is False

    def test_learned_legacy_host_skips_the_algo_probe(self):
        svc = _service()
        bts._conditional_uses_algo[svc.base_url] = False

        post = _Recorder(lambda path, p: {"orderId": 9, "status": "NEW"})
        svc._signed_post = post

        _run(svc._place_conditional_order(dict(STOP_PARAMS)))
        assert [c[0] for c in post.calls] == ["/fapi/v1/order"]


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
