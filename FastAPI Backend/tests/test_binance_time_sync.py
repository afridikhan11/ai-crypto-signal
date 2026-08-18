"""
Clock-skew handling for signed Binance requests (fixes intermittent -1021).

Three things are proven here:
  1. `BinanceTimeSync` measures the server<->local offset and applies it in
     `now_ms()`, syncs ONCE under contention (no thundering herd), degrades to
     the host clock on failure, and force-resyncs on demand.
  2. A -1021 on a signed request forces EXACTLY ONE re-sync + ONE retry, in
     both the trading service (GET/POST/DELETE) and the read-only account
     service.
  3. The order-placing POST path never double-submits: success sends once, a
     -1021 retries the SAME order exactly once (idempotent), and any OTHER
     error is surfaced without a retry.

No real HTTP is made - the server-time fetch and the httpx client are
replaced with fakes, but every line of the offset/retry logic runs for real.
"""
import asyncio
import time

import httpx
import pytest

from app.services import binance_time_sync as bts
from app.services.binance_account_service import BinanceAccountError, BinanceAccountService
from app.services.binance_time_sync import BinanceTimeSync, get_time_sync
from app.services.binance_trading_service import BinanceTradingError, BinanceTradingService


def _run(coro):
    return asyncio.run(coro)


def _resp(status, body):
    return httpx.Response(status_code=status, json=body)


# ======================================================================
# 1. BinanceTimeSync - offset measurement, herd control, degradation
# ======================================================================
class TestBinanceTimeSync:
    def _sync(self, offset_ms):
        ts = BinanceTimeSync("http://binance.test")
        calls = {"n": 0}

        async def fake_fetch():
            calls["n"] += 1
            # Server is `offset_ms` ahead of / behind our local clock.
            return int(time.time() * 1000) + offset_ms

        ts._fetch_server_time_ms = fake_fetch
        return ts, calls

    def test_offset_is_measured_and_applied(self):
        ts, _ = self._sync(offset_ms=5000)  # server 5s ahead of us
        now = _run(ts.now_ms())
        # now_ms == local + offset, i.e. it reproduces the server clock.
        assert abs(now - (int(time.time() * 1000) + 5000)) < 150
        assert abs(ts.offset_ms - 5000) < 150
        assert ts.synced is True

    def test_negative_offset_when_host_is_ahead(self):
        ts, _ = self._sync(offset_ms=-3000)  # our clock runs 3s fast
        _run(ts.now_ms())
        assert abs(ts.offset_ms + 3000) < 150

    def test_single_sync_under_concurrency_no_thundering_herd(self):
        ts, calls = self._sync(offset_ms=2000)

        # Make the fetch yield so 10 concurrent callers genuinely overlap; the
        # async lock + double-check must still collapse them to ONE fetch.
        inner = ts._fetch_server_time_ms

        async def slow_fetch():
            await asyncio.sleep(0)
            return await inner()

        ts._fetch_server_time_ms = slow_fetch

        async def burst():
            return await asyncio.gather(*[ts.now_ms() for _ in range(10)])

        _run(burst())
        assert calls["n"] == 1

    def test_cached_offset_reused_within_interval(self):
        ts, calls = self._sync(offset_ms=1000)
        _run(ts.now_ms())
        _run(ts.now_ms())
        _run(ts.now_ms())
        assert calls["n"] == 1  # still fresh -> no refetch

    def test_stale_offset_triggers_refetch(self):
        ts, calls = self._sync(offset_ms=1000)
        ts._resync_interval = 0.0  # everything is immediately stale
        _run(ts.now_ms())
        _run(ts.now_ms())
        assert calls["n"] == 2

    def test_failure_degrades_to_host_clock_without_raising(self):
        ts = BinanceTimeSync("http://binance.test")

        async def boom():
            raise RuntimeError("time endpoint down")

        ts._fetch_server_time_ms = boom
        now = _run(ts.now_ms())          # must NOT raise
        assert abs(now - int(time.time() * 1000)) < 1000  # ~ host clock
        assert ts.synced is False
        assert ts.offset_ms == 0.0

    def test_resync_collapses_a_burst_but_refetches_when_stale(self):
        ts, calls = self._sync(offset_ms=4000)
        _run(ts.now_ms())               # fetch #1
        _run(ts.resync())               # within the 2s gap -> collapsed
        assert calls["n"] == 1
        ts._last_sync -= (bts._MIN_FORCED_GAP + 1.0)  # pretend time passed
        _run(ts.resync())               # now a real forced refetch
        assert calls["n"] == 2

    def test_registry_shares_one_instance_per_host(self):
        a = get_time_sync("http://shared.test")
        b = get_time_sync("http://shared.test")
        c = get_time_sync("http://other.test")
        assert a is b
        assert a is not c


# ======================================================================
# Fakes for the service-level tests
# ======================================================================
class FakeTimeSync:
    """Counts resyncs and hands out strictly increasing timestamps, so a
    retry is visibly a *different* timestamp than the first attempt."""

    def __init__(self):
        self.resyncs = 0
        self._n = 0

    async def now_ms(self):
        self._n += 1
        return 1_700_000_000_000 + self._n

    async def resync(self):
        self.resyncs += 1


class FakeClient:
    """Records every request and replays a queued list of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def request(self, method, url, params=None, headers=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {})})
        return self._responses.pop(0)

    async def get(self, url, params=None, headers=None):
        self.calls.append({"method": "GET", "url": url, "params": dict(params or {})})
        return self._responses.pop(0)


def _trading(responses):
    svc = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
    svc._time_sync = FakeTimeSync()
    svc._client = FakeClient(responses)
    return svc


def _account(responses):
    svc = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
    svc._time_sync = FakeTimeSync()
    svc._client = FakeClient(responses)
    return svc


# ======================================================================
# 2. -1021 -> exactly one re-sync + one retry
# ======================================================================
class TestTradingSkewRetry:
    def test_get_retries_once_on_1021_then_succeeds(self):
        svc = _trading([
            _resp(400, {"code": -1021, "msg": "Timestamp outside recvWindow"}),
            _resp(200, {"ok": True}),
        ])
        out = _run(svc._signed_get("/fapi/v1/openOrders", {"symbol": "BTCUSDT"}))
        assert out == {"ok": True}
        assert svc._time_sync.resyncs == 1          # re-synced exactly once
        assert len(svc._client.calls) == 2          # retried exactly once

    def test_no_retry_when_first_attempt_succeeds(self):
        svc = _trading([_resp(200, {"ok": True})])
        out = _run(svc._signed_get("/fapi/v1/openOrders", {"symbol": "BTCUSDT"}))
        assert out == {"ok": True}
        assert svc._time_sync.resyncs == 0
        assert len(svc._client.calls) == 1

    def test_non_1021_error_is_not_retried(self):
        svc = _trading([_resp(400, {"code": -2010, "msg": "insufficient balance"})])
        with pytest.raises(BinanceTradingError):
            _run(svc._signed_get("/fapi/v1/openOrders", {"symbol": "BTCUSDT"}))
        assert svc._time_sync.resyncs == 0
        assert len(svc._client.calls) == 1

    def test_retry_that_still_fails_is_surfaced_not_retried_again(self):
        svc = _trading([
            _resp(400, {"code": -1021, "msg": "skew"}),
            _resp(400, {"code": -1021, "msg": "skew again"}),
        ])
        with pytest.raises(BinanceTradingError):
            _run(svc._signed_get("/fapi/v1/openOrders", {"symbol": "BTCUSDT"}))
        assert svc._time_sync.resyncs == 1          # only ONE re-sync
        assert len(svc._client.calls) == 2          # only ONE retry (no loop)


class TestAccountSkewRetry:
    def test_signed_get_retries_once_on_1021(self):
        svc = _account([
            _resp(400, {"code": -1021, "msg": "skew"}),
            _resp(200, {"balances": []}),
        ])
        resp = _run(svc._signed_get(svc.spot_base_url, "/api/v3/account"))
        assert resp.status_code == 200
        assert svc._time_sync.resyncs == 1
        assert len(svc._client.calls) == 2

    def test_signed_get_no_retry_on_success(self):
        svc = _account([_resp(200, {"balances": []})])
        resp = _run(svc._signed_get(svc.spot_base_url, "/api/v3/account"))
        assert resp.status_code == 200
        assert svc._time_sync.resyncs == 0
        assert len(svc._client.calls) == 1

    def test_signed_get_no_retry_on_other_error(self):
        svc = _account([_resp(401, {"code": -2015, "msg": "bad key"})])
        resp = _run(svc._signed_get(svc.spot_base_url, "/api/v3/account"))
        assert resp.status_code == 401           # returned as-is for the caller
        assert svc._time_sync.resyncs == 0
        assert len(svc._client.calls) == 1


# ======================================================================
# 3. POST order path never double-submits
# ======================================================================
class TestPostDoesNotDoubleSubmit:
    ORDER = {"symbol": "BTCUSDT", "side": "SELL", "type": "MARKET",
             "quantity": "0.01", "newClientOrderId": "sig-abc-E"}

    def test_success_submits_exactly_once(self):
        svc = _trading([_resp(200, {"orderId": 1, "status": "NEW"})])
        out = _run(svc._signed_post("/fapi/v1/order", dict(self.ORDER)))
        assert out["orderId"] == 1
        assert len(svc._client.calls) == 1          # one order, one submit
        assert svc._time_sync.resyncs == 0

    def test_1021_retries_the_same_order_exactly_once(self):
        svc = _trading([
            _resp(400, {"code": -1021, "msg": "skew"}),
            _resp(200, {"orderId": 2, "status": "NEW"}),
        ])
        out = _run(svc._signed_post("/fapi/v1/order", dict(self.ORDER)))
        assert out["orderId"] == 2
        assert len(svc._client.calls) == 2          # exactly one retry
        # Both submissions are the SAME order: same idempotency key, only the
        # timestamp differs - so Binance would reject a true duplicate (-2010).
        c0, c1 = svc._client.calls
        assert c0["params"]["newClientOrderId"] == c1["params"]["newClientOrderId"] == "sig-abc-E"
        assert c0["params"]["timestamp"] != c1["params"]["timestamp"]

    def test_ambiguous_post_error_is_not_retried(self):
        # A non-1021 failure (which might mean the order WAS accepted) must not
        # be retried - that is what prevents a duplicate position.
        svc = _trading([_resp(400, {"code": -1001, "msg": "internal error"})])
        with pytest.raises(BinanceTradingError):
            _run(svc._signed_post("/fapi/v1/order", dict(self.ORDER)))
        assert len(svc._client.calls) == 1          # NOT resubmitted
        assert svc._time_sync.resyncs == 0
