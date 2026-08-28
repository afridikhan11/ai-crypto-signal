"""
app/services/binance_trading_service.py

TRADE-EXECUTION Binance integration - the ONLY place in this codebase
allowed to place, amend, or cancel a real order.

--------------------------------------------------------------------------
ARCHITECTURE / SEPARATION OF CONCERNS
--------------------------------------------------------------------------
This project now has THREE independent Binance integrations, each with a
hard, deliberate boundary:

    1. AI Module      -> app.services.binance_service.BinanceDataManager
                          Public market-data endpoints only. Powers signal
                          generation. Never touches an API key.

    2. Account Module  -> app.services.binance_account_service.BinanceAccountService
                          READ-ONLY authenticated endpoints (balances,
                          positions, orders, trade history). Every method
                          is a hard-coded HTTP GET - see that file's own
                          docstring. Its own docstring explicitly says any
                          order-placement feature must live in a separate,
                          clearly-labelled service instead of being added
                          there - THIS file is that separate service.

    3. Trading Module  -> app.services.binance_trading_service.BinanceTradingService (this file)
                          The only class in this codebase that sends a
                          signed POST to Binance. Used exclusively by the
                          Auto Trading feature (POST /trading/execute),
                          which itself only fires when the user clicks
                          "Execute" on a specific signal - never automatic,
                          never from the scanner/AI module.

--------------------------------------------------------------------------
SCOPE (deliberately minimal for the first version)
--------------------------------------------------------------------------
* USD-M Futures only, matching where every signal's price data already
  comes from (BinanceDataManager already points at fapi.binance.com).
* Places exactly 3 orders per executed signal: a MARKET entry, a
  STOP_MARKET stop-loss (reduceOnly), and a LIMIT take-profit (reduceOnly)
  at the signal's single take_profit level (see app/models/signal.py).
* Never changes leverage or margin type - whatever is already configured
  on the account for that symbol is what the order uses.
* Never a market order for exit - only reduceOnly stop/limit orders close
  a position, so a slow/failed response can never accidentally flip or
  double a position.

--------------------------------------------------------------------------
ENVIRONMENTS
--------------------------------------------------------------------------
Same testnet/mainnet switch as BinanceAccountService - `testnet=True`
points every request at the Binance Futures TESTNET
(testnet.binancefuture.com, a full API mirror where every order type -
including STOP_MARKET/TAKE_PROFIT - works) instead of mainnet
(fapi.binance.com). Overridable via BINANCE_FUTURES_TESTNET_URL. Whichever
environment is selected in Settings (saved alongside the API key) is what
every execution uses - there is no separate trading-only toggle.

NOTE: the previous default, demo-fapi.binance.com ("Demo/Mock Trading"),
rejects conditional orders on /fapi/v1/order with code -4120 ("use the Algo
Order API"), so stop-losses were never placed there. Testnet keys for the
real testnet come from https://testnet.binancefuture.com (separate from
demo keys).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import os
import time
from collections import namedtuple
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.binance_time_sync import get_time_sync


class BinanceTradingError(Exception):
    """Raised when a Binance order-placement request fails or returns an error payload.

    Carries the numeric Binance error `code` (e.g. -1021 for a timestamp
    outside the recvWindow) when the response body supplied one, so callers can
    react to a specific failure without string-matching the message."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------
# CONDITIONAL ORDERS (STOP_MARKET / TAKE_PROFIT_MARKET / ...)
#
# Binance migrated USDS-M conditional orders to the Algo Order service on
# 2025-12-09. Since then /fapi/v1/order answers -4120 for them:
#   "Order type not supported for this endpoint. Please use the Algo Order
#    API endpoints instead."
# That rejection is what left this platform's stop orders unplaced and pushed
# the monitor onto its SOFTWARE stop - it was NEVER a Demo-venue limitation,
# and it would have broken mainnet identically. Conditional orders now go to
# the paths below; a plain reduceOnly LIMIT take-profit is unaffected and
# still belongs on /fapi/v1/order.
# ----------------------------------------------------------------------
ALGO_ORDER_PATH = "/fapi/v1/algoOrder"
ALGO_OPEN_ORDERS_PATH = "/fapi/v1/openAlgoOrders"
ALGO_CANCEL_OPEN_ORDERS_PATH = "/fapi/v1/algoOpenOrders"

# Which endpoint a given host actually accepts conditional orders on, learned
# once at runtime and remembered per base_url: True = Algo API, False = the
# legacy /fapi/v1/order endpoint (older/self-hosted venues). Module-level
# because a service instance is short-lived (built per call).
_conditional_uses_algo: Dict[str, bool] = {}

# The Algo service's REQUEST spelling differs from the fields its RESPONSE
# carries, which is what made this hard to get right. Binance's own cURL
# example for POST /fapi/v1/algoOrder settles the two key fields:
#
#     --data algoType=CONDITIONAL      <- the ALGO type
#     --data type=STOP_MARKET          <- the ORDER type
#
# ...while the response for the same order comes back with `orderType`,
# `triggerPrice` and `clientAlgoId`. So request and response genuinely use
# different names, and only `algoType` + `type` are documented for the
# request; the trigger-price and client-id spellings are not, and demo-fapi
# rejected `stopPrice` with -1116.
#
# THERE IS EXACTLY ONE SHAPE, and it is the documented one. An earlier version
# of this module walked a ladder of four guessed spellings until one stopped
# being rejected. That was a mistake twice over:
#
#   1. Three of the four sent parameters that do not exist on the Algo API at
#      all (`stopPrice`, `orderType`, `newClientOrderId`), so they could never
#      have succeeded - they were dead weight sending three doomed orders.
#   2. Far worse, the ladder DESTROYED THE EVIDENCE. Each attempt overwrote
#      `last_error`, so the only thing that ever reached the log was the LAST
#      variant's complaint. On 2026-08-28 that surfaced as "Mandatory parameter
#      'algotype' was not sent" - which is simply what variant #4 deserved for
#      omitting `algoType`, and told us nothing whatsoever about why the real,
#      documented payload was refused.
#
# So: one shape, one request, and on failure the exact payload we sent is
# logged next to Binance's exact answer. A stop order is not a thing to guess
# at - when it is refused we need to be able to read why.
_AlgoShape = namedtuple("_AlgoShape", "algo_field order_field trigger_field client_field")
_ALGO_PARAMS = _AlgoShape("algoType", "type", "triggerPrice", "clientAlgoId")

# Parameters that must never reach a log line.
_SECRET_PARAMS = ("signature", "timestamp", "recvWindow")


def _protective_working_type() -> str:
    """Which price fires a protective stop - see Settings.stop_working_type.
    Read per call rather than captured at import so a venue change needs only
    an env edit and a restart, never a code change."""
    return get_settings().stop_working_type


def _is_true(value) -> bool:
    """Binance returns booleans as real bools on some paths and as the strings
    "true"/"True" on others."""
    return value in (True, "true", "True")


def _is_protective(order: dict) -> bool:
    """Whether a resting STOP_MARKET row is one of OURS - i.e. it closes rather
    than opens. A `closePosition` stop reports reduceOnly=FALSE, so filtering on
    reduceOnly alone would hide precisely the stops this service now places and
    make every resting one look like a stray entry trigger."""
    return _is_true(order.get("reduceOnly")) or _is_true(order.get("closePosition"))


class OrderResult(BaseModel):
    order_id: int
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: float
    price: Optional[float] = None
    # The REAL average fill price as reported by Binance in the order
    # response (`avgPrice`), when the order has actually (partially)
    # filled. None otherwise - NEVER defaulted to the requested/planned
    # price: those are different facts, and conflating them is exactly the
    # G6 defect (a MARKET sell that filled at ~97 was recorded as filled
    # at the signal's 100.445). A5-lite, 2026-08-01. Additive and optional,
    # so every existing constructor call and consumer is unaffected.
    avg_fill_price: Optional[float] = None


class ExecutionResult(BaseModel):
    environment: str
    symbol: str
    entry_order: OrderResult
    stop_loss_order: Optional[OrderResult] = None
    take_profit_order: Optional[OrderResult] = None
    quantity: float
    warnings: list[str] = []


class StopReplacementResult(BaseModel):
    """Outcome of `BinanceTradingService.replace_stop_loss()` - see that
    method's docstring for the exact ordering guarantee (new stop placed
    BEFORE the old one is cancelled) that makes `success=False` here mean
    "the position's existing protection is untouched", never "unprotected"."""

    success: bool
    new_order: Optional[OrderResult] = None
    cancelled_order_ids: list[int] = []
    warning: Optional[str] = None


class BinanceTradingService:
    FUTURES_MAINNET_URL = "https://fapi.binance.com"
    # The REAL Binance Futures Testnet - a full mirror of the mainnet API, so
    # every order type works (STOP_MARKET / TAKE_PROFIT included). The older
    # default, demo-fapi.binance.com ("Demo/Mock Trading"), does NOT support
    # conditional orders on /fapi/v1/order (returns code -4120 "use the Algo
    # Order API"), which left stop-losses unplaced. Get testnet keys from
    # https://testnet.binancefuture.com. Overridable via BINANCE_FUTURES_TESTNET_URL
    # for anyone who still wants the demo endpoint.
    FUTURES_TESTNET_URL = os.getenv(
        "BINANCE_FUTURES_TESTNET_URL", "https://testnet.binancefuture.com"
    )

    # Widened from 5000 -> 10000 (Binance max is 60000). On testnet the host
    # clock was ~2000ms off Binance, eating 40% of a 5s window; 10s leaves
    # ample slack once binance_time_sync corrects the timestamp too. Kept
    # modest deliberately: a larger window widens the replay-attack surface.
    RECV_WINDOW = 10000
    HTTP_TIMEOUT = 15.0
    EXCHANGE_INFO_CACHE_TTL = 3600.0  # symbol precision rarely changes

    # Retry policy for placing a REPLACEMENT stop order only (see
    # replace_stop_loss) - a transient network failure here is retried
    # before giving up, since the old stop stays active and untouched
    # throughout, so trying harder costs nothing and can only help.
    STOP_PLACEMENT_MAX_ATTEMPTS = 3
    STOP_PLACEMENT_RETRY_DELAY_SECONDS = 1.0

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        if not api_key or not api_secret:
            raise ValueError("api_key and api_secret are required.")

        self._api_key = api_key
        self._api_secret = api_secret
        self.testnet = testnet
        self.environment = "testnet" if testnet else "mainnet"
        self.base_url = self.FUTURES_TESTNET_URL if testnet else self.FUTURES_MAINNET_URL
        self._client = httpx.AsyncClient(timeout=self.HTTP_TIMEOUT)
        self._exchange_info_cache: Optional[tuple[dict, float]] = None
        # Binance-aligned timestamps for every signed request (fixes -1021
        # clock-skew rejections). Shared per host, so all service instances
        # pointing at this base_url reuse one offset.
        self._time_sync = get_time_sync(self.base_url)

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self) -> "BinanceTradingService":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------
    def _sign(self, params: dict) -> dict:
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _send_signed(self, method: str, path: str, params: dict) -> httpx.Response:
        """Stamp a fresh Binance-aligned timestamp, sign, and send ONE signed
        request. Kept side-effect-free on `params` (signs a copy) so a caller
        can re-stamp and resend the SAME params for a retry."""
        body = dict(params)
        body["timestamp"] = await self._time_sync.now_ms()
        body.setdefault("recvWindow", self.RECV_WINDOW)
        signed = self._sign(dict(body))
        headers = {"X-MBX-APIKEY": self._api_key}
        return await self._client.request(
            method, f"{self.base_url}{path}", params=signed, headers=headers
        )

    async def _signed_request(self, method: str, path: str, params: dict) -> dict:
        """Send a signed request, and on a -1021 (timestamp outside recvWindow)
        force a clock re-sync and retry EXACTLY ONCE.

        Retrying an order-placing POST on -1021 is safe: -1021 is a
        timestamp-validation rejection that Binance performs BEFORE the order
        is ever accepted or matched, so no order can have been placed. As a
        second backstop, order legs carry a deterministic newClientOrderId
        (see `_client_order_id` / `_stop_replacement_client_order_id`), so even
        a spurious duplicate would be rejected by Binance with -2010 rather than
        opening a second position. Any OTHER error is surfaced immediately -
        never retried - so an ambiguous failure can't double-submit."""
        resp = await self._send_signed(method, path, params)
        try:
            return self._parse_or_raise(resp, f"{method} {path}")
        except BinanceTradingError as exc:
            if exc.code != -1021:
                raise
            logger.warning(
                "Binance -1021 (clock skew) on {} {}; re-syncing time and retrying once.",
                method,
                path,
            )
            await self._time_sync.resync()
            resp = await self._send_signed(method, path, params)
            return self._parse_or_raise(resp, f"{method} {path}")

    async def _signed_post(self, path: str, params: dict) -> dict:
        """The ONLY method in this codebase that sends an order-placing POST to Binance."""
        return await self._signed_request("POST", path, params)

    async def _public_get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = await self._client.get(f"{self.base_url}{path}", params=params or {})
        return self._parse_or_raise(resp, f"GET {path}")

    async def _signed_get(self, path: str, params: Optional[dict] = None):
        """Signed, read-only GET (e.g. open orders) - never places, amends,
        or cancels anything by itself. Returns whatever shape Binance sends
        back (a dict for most endpoints, a list for `/fapi/v1/openOrders`) -
        `_parse_or_raise` only inspects the HTTP status, not the payload
        shape, so it's safe for both."""
        return await self._signed_request("GET", path, dict(params or {}))

    async def _signed_delete(self, path: str, params: dict) -> dict:
        """The ONLY method in this codebase that sends an order-cancelling
        DELETE to Binance."""
        return await self._signed_request("DELETE", path, params)

    # ------------------------------------------------------------------
    # Conditional orders - see the ALGO_* constants' comment above.
    # ------------------------------------------------------------------
    @staticmethod
    def _to_algo_params(params: dict, shape: Optional["_AlgoShape"] = None) -> dict:
        """Build the Algo API payload for a conditional order, in `shape`'s
        spelling (see _ALGO_PARAMS). The ORDER itself is untouched - only how
        the algo type, order type, trigger price and client id are named
        changes."""
        shape = shape or _ALGO_PARAMS
        algo = dict(params)
        order_type = algo.pop("type")
        algo[shape.algo_field] = "CONDITIONAL"
        algo[shape.order_field] = order_type
        if shape.trigger_field != "stopPrice" and "stopPrice" in algo:
            algo[shape.trigger_field] = algo.pop("stopPrice")
        if shape.client_field != "newClientOrderId" and "newClientOrderId" in algo:
            algo[shape.client_field] = algo.pop("newClientOrderId")
        return algo

    @staticmethod
    def _normalise_algo_order(raw: dict) -> dict:
        """Give an Algo order the same `orderId` / `stopPrice` keys the rest of
        this module already reads, and tag it `_is_algo` so a later cancel is
        routed to the algo endpoint rather than /fapi/v1/order."""
        out = dict(raw)
        if out.get("orderId") is None and out.get("algoId") is not None:
            out["orderId"] = out["algoId"]
        if out.get("stopPrice") is None and out.get("triggerPrice") is not None:
            out["stopPrice"] = out["triggerPrice"]
        if out.get("type") is None and out.get("orderType") is not None:
            out["type"] = out["orderType"]
        out["_is_algo"] = True
        return out

    async def _place_conditional_order(self, params: dict) -> dict:
        """Place ONE conditional order (STOP_MARKET etc.), on whichever
        endpoint this host accepts.

        Tries the Algo Order API first - mandatory on Binance since 2025-12-09
        - and falls back ONCE to the legacy /fapi/v1/order endpoint for venues
        still on the old API. The answer is remembered per host, so the probe
        happens once, not on every order, and after that a failure is a REAL
        failure and is raised rather than silently re-routed.

        Returns a dict carrying `orderId` whichever endpoint served it."""
        prefers_algo = _conditional_uses_algo.get(self.base_url)

        if prefers_algo is not False:
            proven = prefers_algo is True
            algo_params = self._to_algo_params(params)
            try:
                raw = await self._signed_post(ALGO_ORDER_PATH, algo_params)
                if not proven:
                    _conditional_uses_algo[self.base_url] = True
                    logger.success(
                        f"Conditional orders on {self.base_url} routed to the Algo Order API "
                        f"({ALGO_ORDER_PATH}) - exchange-native stops are active."
                    )
                return self._normalise_algo_order(raw)
            except BinanceTradingError as exc:
                if proven:
                    # This host has already served an algo order, so this is a
                    # genuine order failure - surface it, never silently
                    # re-route to an endpoint that would answer -4120.
                    raise
                # First refusal on this host. Log EXACTLY what we sent beside
                # exactly what Binance answered: without both halves the next
                # step is guesswork, and this payload carries a stop-loss.
                logger.warning(
                    f"Algo Order API refused the documented payload on {self.base_url} "
                    f"(code={exc.code}): {exc}\n"
                    f"  sent to {ALGO_ORDER_PATH}: {self._loggable(algo_params)}\n"
                    f"  falling back to the legacy /fapi/v1/order endpoint once."
                )

        raw = await self._signed_post("/fapi/v1/order", params)
        _conditional_uses_algo[self.base_url] = False
        return raw

    @staticmethod
    def _loggable(params: dict) -> dict:
        """The request payload with the credential-bearing fields dropped, so a
        failed order can be diagnosed from the log without leaking anything."""
        return {k: v for k, v in params.items() if k not in _SECRET_PARAMS}

    async def cancel_conditional_order(self, symbol: str, order_id, is_algo: bool = False) -> None:
        """Cancel one conditional order on the endpoint that owns it."""
        if is_algo:
            await self._signed_delete(ALGO_ORDER_PATH, {"symbol": symbol.upper(), "algoId": order_id})
        else:
            await self._signed_delete("/fapi/v1/order", {"symbol": symbol.upper(), "orderId": order_id})

    @staticmethod
    def _protective_stop_params(
        symbol: str, exit_side: str, stop_price: float, quantity: float
    ) -> dict:
        """The ONE place a protective STOP_MARKET payload is built, so every
        stop this service places - initial, re-attached, or replaced by
        breakeven/trailing - is shaped identically.

        See Settings.stop_close_position / .stop_working_type for why it
        closes the whole position and triggers on the mark price."""
        params = {
            "symbol": symbol,
            "side": exit_side,
            "type": "STOP_MARKET",
            "stopPrice": stop_price,
            "workingType": _protective_working_type(),
        }
        if get_settings().stop_close_position:
            # Mutually exclusive with quantity/reduceOnly - sending either
            # alongside it is rejected.
            params["closePosition"] = "true"
        else:
            params["quantity"] = quantity
            params["reduceOnly"] = "true"
        return params

    async def cancel_any_order(self, symbol: str, order: dict) -> None:
        """Cancel one order row from `get_all_open_orders()` on whichever
        service owns it. Rows read from the Algo endpoint carry `_is_algo`;
        cancelling those on /fapi/v1/order fails, so every caller that sweeps
        a mixed list must route rather than assume."""
        await self.cancel_conditional_order(
            symbol, order.get("orderId"), is_algo=bool(order.get("_is_algo"))
        )

    async def cancel_all_conditional_orders(self, symbol: str) -> None:
        """Cancel every resting ALGO (conditional) order for `symbol`. Used by
        the monitor's post-resolution cleanup, which otherwise only sees plain
        orders on /fapi/v1/openOrders and would leave a stray stop behind."""
        await self._signed_delete(ALGO_CANCEL_OPEN_ORDERS_PATH, {"symbol": symbol.upper()})

    @staticmethod
    def _parse_or_raise(resp: httpx.Response, context: str):
        if resp.status_code >= 400:
            code, msg = None, resp.text
            try:
                body = resp.json()
                msg = body.get("msg", resp.text)
                code = body.get("code")
            except Exception:
                pass
            raise BinanceTradingError(
                f"{context} failed ({resp.status_code}, code={code}): {msg}", code=code
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Symbol precision (quantity/price rounding) - required or Binance
    # rejects orders with -1111 (precision) / -4014 (tick size) errors.
    # ------------------------------------------------------------------
    async def _get_symbol_filters(self, symbol: str) -> dict:
        now = time.monotonic()
        if self._exchange_info_cache and (now - self._exchange_info_cache[1]) < self.EXCHANGE_INFO_CACHE_TTL:
            info = self._exchange_info_cache[0]
        else:
            info = await self._public_get("/fapi/v1/exchangeInfo")
            self._exchange_info_cache = (info, now)

        for s in info.get("symbols", []):
            if s.get("symbol") == symbol.upper():
                step_size = 1.0
                tick_size = 0.01
                min_notional = 0.0
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step_size = float(f["stepSize"])
                    elif f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                    elif f.get("filterType") == "MIN_NOTIONAL":
                        # USD-M Futures uses "notional"; spot-style schemas use
                        # "minNotional" - accept either so this doesn't silently
                        # fall back to 0 if Binance ever changes the key.
                        min_notional = float(f.get("notional") or f.get("minNotional") or 0.0)
                return {"step_size": step_size, "tick_size": tick_size, "min_notional": min_notional}

        raise BinanceTradingError(f"Symbol {symbol} not found in exchangeInfo - cannot size order safely.")

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        precision = max(0, round(-math.log10(step)))
        rounded = math.floor(value / step) * step
        return round(rounded, precision)

    @staticmethod
    def _round_price(value: float, tick: float) -> float:
        if tick <= 0:
            return value
        precision = max(0, round(-math.log10(tick)))
        rounded = round(value / tick) * tick
        return round(rounded, precision)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_avg_price(raw: dict) -> Optional[float]:
        """The real average fill price from a Binance order response, or
        None when the response carries no fill yet ("avgPrice": "0" /
        missing / unparseable). Returning None instead of 0.0 or a planned
        price keeps 'unknown' distinguishable from 'filled at' - spec
        invariant 5. (G6, A5-lite 2026-08-01.)"""
        try:
            value = float(raw.get("avgPrice", 0) or 0)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _client_order_id(signal_id: Optional[str], leg: str) -> Optional[dict]:
        """
        Deterministic newClientOrderId per order leg, derived from the
        signal's own id - so if POST /trading/execute is ever retried for
        the same signal (double-click, client timeout-and-retry, proxy
        retry), Binance itself rejects the duplicate (error -2010,
        surfaced as a normal BinanceTradingError) instead of silently
        opening a second real position. The DB-level `signal.executed`
        flag (see trading.py) already blocks a second call from reaching
        here at all in the normal case - this is a second, exchange-level
        backstop for the race/retry window before that flag is committed.
        Returns None (no override, Binance auto-generates one) if no
        signal_id was supplied, so this stays fully optional/backward
        compatible.
        """
        if not signal_id:
            return None
        # clientOrderId is capped at 36 chars on Binance Futures - a bare
        # uuid4 hex is already 32, so keep the leg suffix to 1 char.
        short_id = signal_id.replace("-", "")[:32]
        return {"newClientOrderId": f"{short_id}{leg}"}

    @staticmethod
    def _stop_replacement_client_order_id(signal_id: Optional[str], new_stop_price: float) -> Optional[dict]:
        """
        Idempotency key for `replace_stop_loss()` - see that method's
        docstring. Deterministic from (signal_id, new_stop_price) via a
        stable hash (hashlib, NOT the builtin `hash()`, whose per-string
        salt is randomized per process and would silently stop matching
        after any process restart): the SAME trade-management decision
        ("move this signal's stop to exactly this price") always produces
        the SAME clientOrderId, so a retried/duplicated call for that exact
        decision is rejected by Binance itself (-2010) instead of opening a
        second resting stop order. A genuinely NEW decision (a different
        price) always gets a genuinely new id, so real stop improvements
        are never blocked.
        """
        if not signal_id:
            return None
        digest = hashlib.sha256(f"{signal_id}:{round(new_stop_price, 8)}".encode("utf-8")).hexdigest()[:8]
        short_id = signal_id.replace("-", "")[:27]
        return {"newClientOrderId": f"{short_id}R{digest}"}

    async def place_signal_bracket(
        self,
        symbol: str,
        direction: str,  # "LONG" | "SHORT"
        quantity: float,
        stop_loss: float,
        take_profit: float,
        entry_price: Optional[float] = None,
        signal_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Places the MARKET entry, then the reduceOnly STOP_MARKET stop-loss
        and reduceOnly LIMIT take-profit. If the stop-loss or
        take-profit leg fails after the entry already filled, the entry is
        NOT rolled back (there is no real position "undo") - the failure is
        surfaced in `warnings` instead, so the caller/UI can tell the user
        their position is open without full protection and needs manual
        attention. This is the one case where "fail loudly, don't hide it"
        matters more than a clean abstraction.

        `entry_price` (the signal's recent reference price, not used as the
        order price since entry is MARKET) is optional but strongly
        recommended - it lets this reject a doomed order BEFORE sending it
        if qty * price is under Binance's MIN_NOTIONAL floor for the
        symbol, instead of letting Binance bounce it with an opaque -4164.

        `signal_id` is optional and, when supplied, makes all three legs
        idempotent via `newClientOrderId` (see `_client_order_id`).
        """
        symbol = symbol.upper()
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_to_step(quantity, filters["step_size"])
        if qty <= 0:
            raise BinanceTradingError(
                f"Computed quantity for {symbol} rounds down to 0 with this account size/risk% - "
                "position too small to place."
            )

        min_notional = filters.get("min_notional", 0.0)
        if min_notional > 0 and entry_price:
            notional = qty * entry_price
            if notional < min_notional:
                raise BinanceTradingError(
                    f"Order value ${notional:.2f} for {symbol} is below Binance's minimum notional "
                    f"(${min_notional:.2f}) - increase risk % or account size, or skip this signal."
                )

        entry_side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if direction == "LONG" else "BUY"

        entry_params = {"symbol": symbol, "side": entry_side, "type": "MARKET", "quantity": qty}
        entry_params.update(self._client_order_id(signal_id, "E") or {})
        entry_raw = await self._signed_post("/fapi/v1/order", entry_params)
        entry_order = OrderResult(
            order_id=entry_raw["orderId"],
            symbol=symbol,
            side=entry_side,
            order_type="MARKET",
            status=entry_raw.get("status", "NEW"),
            quantity=qty,
            # G6: the REAL fill price from Binance's own response. Futures
            # order responses carry `avgPrice` as a string, "0" until a
            # fill is reflected. >0 -> real fill price; otherwise None -
            # never a copy of the signal's planned entry.
            avg_fill_price=self._parse_avg_price(entry_raw),
        )

        warnings: list[str] = []
        sl_order: Optional[OrderResult] = None
        tp_order: Optional[OrderResult] = None

        stop_price = self._round_price(stop_loss, filters["tick_size"])
        try:
            sl_params = self._protective_stop_params(symbol, exit_side, stop_price, qty)
            sl_params.update(self._client_order_id(signal_id, "S") or {})
            sl_raw = await self._place_conditional_order(sl_params)
            sl_order = OrderResult(
                order_id=sl_raw["orderId"],
                symbol=symbol,
                side=exit_side,
                order_type="STOP_MARKET",
                status=sl_raw.get("status", "NEW"),
                quantity=qty,
                price=stop_price,
            )
        except BinanceTradingError as exc:
            warnings.append(f"Entry filled but stop-loss order FAILED: {exc}. Close/protect this position manually.")

        tp_price = self._round_price(take_profit, filters["tick_size"])
        try:
            tp_params = {
                "symbol": symbol,
                "side": exit_side,
                "type": "LIMIT",
                "price": tp_price,
                "quantity": qty,
                "timeInForce": "GTC",
                "reduceOnly": "true",
            }
            tp_params.update(self._client_order_id(signal_id, "T") or {})
            tp_raw = await self._signed_post("/fapi/v1/order", tp_params)
            tp_order = OrderResult(
                order_id=tp_raw["orderId"],
                symbol=symbol,
                side=exit_side,
                order_type="LIMIT",
                status=tp_raw.get("status", "NEW"),
                quantity=qty,
                price=tp_price,
            )
        except BinanceTradingError as exc:
            warnings.append(f"Entry filled but take-profit order FAILED: {exc}. Set TP manually.")

        return ExecutionResult(
            environment=self.environment,
            symbol=symbol,
            entry_order=entry_order,
            stop_loss_order=sl_order,
            take_profit_order=tp_order,
            quantity=qty,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # ICT Pending Limit Entry (2026-07-30) - two-stage execution.
    #
    # Stage 1 (`place_limit_entry`) rests a LIMIT order at the ICT entry
    # price. Stage 2 (`place_protective_orders`) attaches the stop-loss and
    # take-profit AFTER that entry has actually filled. The two stages
    # cannot be merged: a reduceOnly order is rejected by Binance when no
    # position exists yet, so protection genuinely cannot be placed until
    # the entry fills.
    #
    # Both reuse the SAME filters, rounding, idempotent client-order-id and
    # MIN_NOTIONAL checks as `place_signal_bracket()` above - this is the
    # same execution discipline split across two moments in time, not a
    # second implementation.
    # ------------------------------------------------------------------
    async def place_limit_entry(
        self,
        symbol: str,
        direction: str,  # "LONG" | "SHORT"
        quantity: float,
        entry_price: float,
        signal_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Rests a GTC LIMIT entry order at `entry_price` - the ICT anchor
        price (OTE / order block / FVG midpoint). Because a LIMIT order
        fills AT its limit price, the signal's recorded `entry_price` and
        the real fill price are the same number, which is what keeps
        position sizing (`quantity = risk_usd / |entry - stop|`), breakeven
        at 1R and backtested Risk:Reward correct by construction.

        No protective orders are placed here - see
        `place_protective_orders()`, called once the fill is observed.
        """
        symbol = symbol.upper()
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_to_step(quantity, filters["step_size"])
        if qty <= 0:
            raise BinanceTradingError(
                f"Computed quantity for {symbol} rounds down to 0 with this account size/risk% - "
                "position too small to place."
            )

        limit_price = self._round_price(entry_price, filters["tick_size"])
        min_notional = filters.get("min_notional", 0.0)
        if min_notional > 0 and limit_price:
            notional = qty * limit_price
            if notional < min_notional:
                raise BinanceTradingError(
                    f"Order value ${notional:.2f} for {symbol} is below Binance's minimum notional "
                    f"(${min_notional:.2f}) - increase risk % or account size, or skip this signal."
                )

        entry_side = "BUY" if direction == "LONG" else "SELL"
        params = {
            "symbol": symbol,
            "side": entry_side,
            "type": "LIMIT",
            "price": limit_price,
            "quantity": qty,
            "timeInForce": "GTC",
        }
        params.update(self._client_order_id(signal_id, "E") or {})
        # A plain GTC LIMIT is NOT a conditional order - it carries no trigger
        # price and belongs on /fapi/v1/order. Routing it through
        # `_place_conditional_order` was the reason exchange stops never got
        # placed: the Algo API rightly refused a LIMIT, the legacy fallback
        # succeeded, and that success recorded `_conditional_uses_algo=False`
        # for the whole host. Every REAL stop after it then skipped the Algo
        # endpoint entirely and went straight to /fapi/v1/order, where a
        # STOP_MARKET answers -4120. One pending entry per process was enough
        # to disarm every stop that followed it.
        raw = await self._signed_post("/fapi/v1/order", params)
        logger.info(
            f"{symbol}: ICT pending LIMIT entry placed | {direction} | qty={qty} @ {limit_price} "
            f"| order {raw['orderId']}"
        )
        return OrderResult(
            order_id=raw["orderId"],
            symbol=symbol,
            side=entry_side,
            order_type="LIMIT",
            status=raw.get("status", "NEW"),
            quantity=qty,
            price=limit_price,
        )

    async def get_price_and_tick(self, symbol: str) -> tuple[float, float]:
        """(last traded price, tick size) for `symbol` - the two inputs the
        §7 execution matrix needs. Public on purpose: the Manual Trading
        endpoints (A5-lite) must not reach into private members for this.
        Raises BinanceTradingError if either is unavailable - the matrix
        must never run on a guessed price or a zero tick."""
        symbol = symbol.upper()
        ticker = await self._public_get("/fapi/v1/ticker/price", {"symbol": symbol})
        try:
            last_price = float(ticker["price"])
        except (KeyError, TypeError, ValueError):
            raise BinanceTradingError(f"Could not read a live price for {symbol} from Binance.")
        if last_price <= 0:
            raise BinanceTradingError(f"Binance returned a non-positive price for {symbol}: {last_price}")

        filters = await self._get_symbol_filters(symbol)
        tick_size = float(filters.get("tick_size") or 0)
        if tick_size <= 0:
            raise BinanceTradingError(
                f"No usable tick size for {symbol} - cannot resolve an entry order type safely."
            )
        return last_price, tick_size

    async def place_stop_market_entry(
        self,
        symbol: str,
        direction: str,  # "LONG" | "SHORT"
        quantity: float,
        stop_price: float,
        signal_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Rests a STOP-MARKET ENTRY order (G3, A5-lite 2026-08-01 - spec §7):
        triggers when price reaches `stop_price` and then fills at market.
        This is the entry for the matrix's other half - a LONG whose entry
        is ABOVE the current price (breakout continuation) or a SHORT whose
        entry is BELOW it, which previously could not be represented at
        all: every existing STOP_MARKET in this service is reduceOnly
        protection. This one deliberately is NOT reduceOnly - it OPENS a
        position.

        `workingType=CONTRACT_PRICE` per spec §7 edge rule 2: trigger on
        the last traded price, not mark price, so the trigger corresponds
        to the chart the strategy analysed.

        Same execution discipline as `place_limit_entry`: same filters,
        rounding, MIN_NOTIONAL pre-check and idempotent client order id -
        this is the third entry shape, not a third implementation style.
        No protective orders are placed here; like the LIMIT entry, the
        stop-loss/take-profit can only exist after a position does
        (`place_protective_orders`, on observed fill).
        """
        symbol = symbol.upper()
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_to_step(quantity, filters["step_size"])
        if qty <= 0:
            raise BinanceTradingError(
                f"Computed quantity for {symbol} rounds down to 0 with this account size/risk% - "
                "position too small to place."
            )

        trigger_price = self._round_price(stop_price, filters["tick_size"])
        if trigger_price <= 0:
            raise BinanceTradingError(
                f"Stop-entry trigger price for {symbol} rounds to {trigger_price} - refusing to place."
            )
        min_notional = filters.get("min_notional", 0.0)
        if min_notional > 0:
            notional = qty * trigger_price
            if notional < min_notional:
                raise BinanceTradingError(
                    f"Order value ${notional:.2f} for {symbol} is below Binance's minimum notional "
                    f"(${min_notional:.2f}) - increase risk % or account size, or skip this signal."
                )

        entry_side = "BUY" if direction == "LONG" else "SELL"
        params = {
            "symbol": symbol,
            "side": entry_side,
            "type": "STOP_MARKET",
            "stopPrice": trigger_price,
            "quantity": qty,
            "workingType": "CONTRACT_PRICE",
            # NOT reduceOnly - this order OPENS the position.
        }
        params.update(self._client_order_id(signal_id, "E") or {})
        # A STOP_MARKET genuinely IS conditional, even though this one OPENS a
        # position rather than closing one, so it belongs on the Algo API like
        # every other trigger order - /fapi/v1/order answers -4120 for it.
        raw = await self._place_conditional_order(params)
        logger.info(
            f"{symbol}: STOP-MARKET entry placed | {direction} | qty={qty} trigger @ {trigger_price} "
            f"| order {raw['orderId']}"
        )
        return OrderResult(
            order_id=raw["orderId"],
            symbol=symbol,
            side=entry_side,
            order_type="STOP_MARKET",
            status=raw.get("status", "NEW"),
            quantity=qty,
            price=trigger_price,
        )

    async def place_protective_orders(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        signal_id: Optional[str] = None,
    ) -> tuple[Optional[OrderResult], Optional[OrderResult], list[str]]:
        """
        Attaches the reduceOnly STOP_MARKET stop-loss and reduceOnly LIMIT
        take-profit to an ALREADY-FILLED position. `quantity` must be the
        real filled quantity (see `get_order()`), not the originally
        requested size - a partial fill protected at the full requested size
        would leave a reduceOnly order larger than the position.

        Returns `(sl_order, tp_order, warnings)`. A failure on either leg is
        reported in `warnings` rather than raised, matching
        `place_signal_bracket()`'s existing "the position is open, say so
        loudly rather than pretend it isn't" contract.
        """
        symbol = symbol.upper()
        filters = await self._get_symbol_filters(symbol)
        qty = self._round_to_step(quantity, filters["step_size"])
        exit_side = "SELL" if direction == "LONG" else "BUY"

        warnings: list[str] = []
        sl_order: Optional[OrderResult] = None
        tp_order: Optional[OrderResult] = None

        if qty <= 0:
            warnings.append(
                f"Filled quantity for {symbol} rounds to 0 - no protective orders placed. "
                "Check this position manually."
            )
            return None, None, warnings

        stop_price = self._round_price(stop_loss, filters["tick_size"])
        try:
            sl_params = self._protective_stop_params(symbol, exit_side, stop_price, qty)
            sl_params.update(self._client_order_id(signal_id, "S") or {})
            sl_raw = await self._place_conditional_order(sl_params)
            sl_order = OrderResult(
                order_id=sl_raw["orderId"], symbol=symbol, side=exit_side,
                order_type="STOP_MARKET", status=sl_raw.get("status", "NEW"),
                quantity=qty, price=stop_price,
            )
        except BinanceTradingError as exc:
            warnings.append(
                f"Pending entry FILLED but stop-loss order FAILED: {exc}. "
                "This position is open WITHOUT a stop - protect it manually."
            )

        tp_price = self._round_price(take_profit, filters["tick_size"])
        try:
            tp_params = {
                "symbol": symbol,
                "side": exit_side,
                "type": "LIMIT",
                "price": tp_price,
                "quantity": qty,
                "timeInForce": "GTC",
                "reduceOnly": "true",
            }
            tp_params.update(self._client_order_id(signal_id, "T") or {})
            tp_raw = await self._signed_post("/fapi/v1/order", tp_params)
            tp_order = OrderResult(
                order_id=tp_raw["orderId"], symbol=symbol, side=exit_side,
                order_type="LIMIT", status=tp_raw.get("status", "NEW"),
                quantity=qty, price=tp_price,
            )
        except BinanceTradingError as exc:
            warnings.append(f"Pending entry filled but take-profit order FAILED: {exc}. Set TP manually.")

        return sl_order, tp_order, warnings

    async def get_order(self, symbol: str, order_id: int) -> dict:
        """
        Read-only: the live state of a single order, fetched fresh from
        Binance. Used by the pending-entry reconciliation pass to decide
        whether a resting LIMIT entry has FILLED (and with what
        `executedQty`/`avgPrice`), is still NEW/PARTIALLY_FILLED, or is gone
        (CANCELED/EXPIRED/REJECTED). Never trusts any locally cached value.
        """
        return await self._signed_get(
            "/fapi/v1/order", {"symbol": symbol.upper(), "orderId": order_id}
        )

    async def close_position(self, symbol: str, position_amt: float) -> OrderResult:
        """
        Fully closes an existing position with a single reduceOnly MARKET
        order. `position_amt` must be the LIVE signed position size fetched
        fresh from Binance right before calling this (positive = long,
        negative = short) - the caller (POST /trading/close-position) is
        responsible for that freshness, this method never trusts a
        client-supplied quantity for anything except the sign/magnitude of
        an already-open position.
        """
        symbol = symbol.upper()
        if position_amt == 0:
            raise BinanceTradingError(f"No open position on {symbol} to close.")

        filters = await self._get_symbol_filters(symbol)
        qty = self._round_to_step(abs(position_amt), filters["step_size"])
        if qty <= 0:
            raise BinanceTradingError(f"Position quantity for {symbol} rounds down to 0 - cannot close.")

        close_side = "SELL" if position_amt > 0 else "BUY"
        raw = await self._signed_post(
            "/fapi/v1/order",
            {"symbol": symbol, "side": close_side, "type": "MARKET", "quantity": qty, "reduceOnly": "true"},
        )
        return OrderResult(
            order_id=raw["orderId"],
            symbol=symbol,
            side=close_side,
            order_type="MARKET",
            status=raw.get("status", "NEW"),
            quantity=qty,
        )

    # ------------------------------------------------------------------
    # Exchange stop synchronization (Live Execution Safety, 2026-07-30)
    # ------------------------------------------------------------------
    # WHY THIS EXISTS: TradeManagementEngine's breakeven/trailing decisions
    # (app/scheduler/signal_monitor.py) used to update only the `Signal`
    # row's `stop_loss` column - the real STOP_MARKET order resting on
    # Binance for an EXECUTED signal was never touched, so the database,
    # dashboard, and exchange could silently disagree about how a live
    # position was actually protected. `replace_stop_loss()` closes that
    # gap by finding the resting stop order(s) and safely swapping them for
    # a new one at the tightened price.
    #
    # NO NEW DATABASE COLUMN: the exchange order id of an executed signal's
    # stop-loss is intentionally NOT persisted anywhere. This repository has
    # no schema-migration tool configured (no Alembic - a standing,
    # previously-disclosed architectural gap), so adding a column would risk
    # breaking an already-deployed database. Instead, the CURRENT resting
    # stop order is looked up live via `get_open_stop_orders()` every time -
    # schema-free, and self-correcting if a previous sync attempt partially
    # failed (see below).
    async def cancel_order(self, symbol: str, order_id: int) -> None:
        """Cancels a single existing order by id. Public wrapper over
        `_signed_delete` so other modules (e.g. `signal_monitor.py`'s
        stray-stop cleanup after a structure-failure close) never need to
        reach into a private, underscore-prefixed method directly."""
        await self._signed_delete("/fapi/v1/order", {"symbol": symbol.upper(), "orderId": order_id})

    async def get_open_stop_orders(self, symbol: str) -> list[dict]:
        """
        Read-only: the currently-resting reduceOnly STOP_MARKET order(s)
        for `symbol`, freshly fetched from Binance (never trusted from any
        cached/stored value). Ordinarily exactly one, for this platform's
        one-order-per-signal execution model - a list is still returned
        (rather than the single most-recent one) so a caller can safely
        cancel every stray stop it finds, not just the newest.

        Reads BOTH services: conditional orders live on the Algo endpoint
        since Binance's 2025-12-09 migration, while a stop placed before it
        (or on a venue still using the old API) rests on /fapi/v1/openOrders.
        Every row is normalised to carry `orderId` / `stopPrice` plus an
        `_is_algo` tag, so a caller can cancel it on the endpoint that owns it
        without knowing which service it came from.
        """
        symbol = symbol.upper()
        found: list[dict] = []

        try:
            raw = await self._signed_get(ALGO_OPEN_ORDERS_PATH, {"symbol": symbol})
            rows = raw.get("orders", []) if isinstance(raw, dict) else raw
            for r in rows if isinstance(rows, list) else []:
                if r.get("orderType") == "STOP_MARKET" and _is_protective(r):
                    found.append(self._normalise_algo_order(r))
        except BinanceTradingError as exc:
            # A venue without the algo service is not an error here - the
            # legacy read below still finds its stops.
            logger.debug(f"{symbol}: algo open-orders read unavailable: {exc}")

        try:
            rows = await self._signed_get("/fapi/v1/openOrders", {"symbol": symbol})
            found += [
                r for r in (rows if isinstance(rows, list) else [])
                if r.get("type") == "STOP_MARKET" and _is_protective(r)
            ]
        except BinanceTradingError as exc:
            logger.debug(f"{symbol}: legacy open-orders read failed: {exc}")

        return found

    async def get_all_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """
        Read-only: every currently-open order, any type (MARKET fills never
        stay open, so in practice this is STOP_MARKET/LIMIT/etc. resting
        orders) - unlike `get_open_stop_orders()`, this is NOT filtered to
        reduceOnly stop orders, since the Auto Trading Control Panel's
        Cancel Pending Orders action (2026-07-30) must be able to clear any
        stray resting order, not just protective stops. `symbol=None` asks
        Binance for every open order account-wide in a single call (real
        `/fapi/v1/openOrders` behavior - a heavier signed request than the
        per-symbol form, so only used here and not on the hot per-signal
        stop-sync path, which already knows its own symbol).

        Reads BOTH services. Conditional orders (every stop) live on the Algo
        endpoint since Binance's 2025-12-09 migration, so a legacy-only read
        made the Control Panel's "Cancel Pending Orders" blind to exactly the
        orders an operator reaches for that button to clear - and worse, able
        to answer "No pending orders to cancel" while a stop was still resting.
        Algo rows are normalised and tagged `_is_algo` so the caller can cancel
        each on the endpoint that owns it.
        """
        params = {"symbol": symbol.upper()} if symbol else {}
        rows: list[dict] = []

        try:
            raw = await self._signed_get(ALGO_OPEN_ORDERS_PATH, params)
            algo_rows = raw.get("orders", []) if isinstance(raw, dict) else raw
            rows += [
                self._normalise_algo_order(r)
                for r in (algo_rows if isinstance(algo_rows, list) else [])
            ]
        except BinanceTradingError as exc:
            # A venue with no algo service is not an error - the legacy read
            # below still returns its orders.
            logger.debug(f"Algo open-orders read unavailable: {exc}")

        legacy = await self._signed_get("/fapi/v1/openOrders", params)
        rows += legacy if isinstance(legacy, list) else []
        return rows

    async def replace_stop_loss(
        self,
        symbol: str,
        direction: str,  # "LONG" | "SHORT"
        quantity: float,
        new_stop_price: float,
        signal_id: Optional[str] = None,
    ) -> StopReplacementResult:
        """
        Safely moves an EXECUTED signal's live protective stop to
        `new_stop_price`.

        ORDERING GUARANTEE (the whole point of this method): the NEW stop is
        placed FIRST. Only once it is confirmed resting on the exchange are
        any OLD stop order(s) for this symbol cancelled. If placing the new
        stop fails for any reason, this method returns immediately with
        `success=False` and touches nothing else - the old stop, whatever it
        was, is still exactly where it was. Protection can therefore only
        ever be ADDED before it is removed, never removed first - it is
        never allowed to become weaker or disappear, even for an instant.

        If the new stop places successfully but cancelling an old one fails,
        that is logged as a warning, not a hard failure: having two resting
        reduceOnly stop orders is redundant but harmless (whichever price
        the market reaches first fills; Binance auto-adjusts/cancels a
        reduceOnly order that would exceed the now-smaller position), and
        the next successful sync will find and clean up the stray order.
        `success=True` is only ever returned once real, confirmed
        protection is in place at the new price - a partial cancel failure
        never downgrades that to `success=False`.
        """
        symbol = symbol.upper()
        exit_side = "SELL" if direction == "LONG" else "BUY"
        filters = await self._get_symbol_filters(symbol)
        stop_price = self._round_price(new_stop_price, filters["tick_size"])
        qty = self._round_to_step(quantity, filters["step_size"])

        # A closePosition stop names no quantity, so a residual too small to
        # round to a step is exactly the case it exists to cover - refusing
        # here would strand the dust it is meant to flatten.
        if qty <= 0 and not get_settings().stop_close_position:
            return StopReplacementResult(
                success=False,
                warning=f"Computed quantity for {symbol} rounds down to 0 - refusing to replace the stop; "
                        f"the existing stop order (if any) is untouched.",
            )

        # 1. Look up what is currently resting BEFORE changing anything, so
        #    we know what (if anything) needs cancelling afterward.
        try:
            existing_stops = await self.get_open_stop_orders(symbol)
        except BinanceTradingError as exc:
            return StopReplacementResult(
                success=False,
                warning=f"Could not read existing stop orders for {symbol} - refusing to proceed "
                        f"without knowing current protection state: {exc}",
            )

        # 2. Place the NEW stop. Nothing existing is touched yet.
        #
        # RETRY: a transient network failure (timeout/connection error, NOT
        # a definitive Binance rejection like bad params) is retried a
        # bounded number of times before giving up - the old stop stays
        # untouched throughout, so a retry never risks a moment of no
        # protection, and trying harder here directly serves "never leave
        # position unprotected" better than failing on the first hiccup.
        # A `BinanceTradingError` (Binance itself rejected the request) is
        # NOT retried - retrying an invalid request would just fail the
        # same way again.
        new_params = self._protective_stop_params(symbol, exit_side, stop_price, qty)
        new_params.update(self._stop_replacement_client_order_id(signal_id, stop_price) or {})

        new_raw = None
        last_network_error: Optional[Exception] = None
        for attempt in range(1, self.STOP_PLACEMENT_MAX_ATTEMPTS + 1):
            try:
                new_raw = await self._place_conditional_order(new_params)
                last_network_error = None
                break
            except BinanceTradingError as exc:
                logger.warning(
                    f"{symbol}: new stop-loss order at {stop_price} FAILED to place - "
                    f"existing protection is untouched: {exc}"
                )
                return StopReplacementResult(
                    success=False,
                    warning=f"New stop-loss order at {stop_price} failed to place - existing protection "
                            f"is untouched and remains active: {exc}",
                )
            except httpx.RequestError as exc:
                last_network_error = exc
                logger.warning(
                    f"{symbol}: network error placing new stop at {stop_price} "
                    f"(attempt {attempt}/{self.STOP_PLACEMENT_MAX_ATTEMPTS}): {exc}"
                )
                if attempt < self.STOP_PLACEMENT_MAX_ATTEMPTS:
                    await asyncio.sleep(self.STOP_PLACEMENT_RETRY_DELAY_SECONDS)

        if new_raw is None:
            return StopReplacementResult(
                success=False,
                warning=f"New stop-loss order at {stop_price} failed to place after "
                        f"{self.STOP_PLACEMENT_MAX_ATTEMPTS} attempts (network errors) - existing "
                        f"protection is untouched and remains active: {last_network_error}",
            )

        new_order = OrderResult(
            order_id=new_raw["orderId"], symbol=symbol, side=exit_side, order_type="STOP_MARKET",
            status=new_raw.get("status", "NEW"), quantity=qty, price=stop_price,
        )
        logger.success(f"{symbol}: new stop-loss order placed at {stop_price} (order {new_raw['orderId']}).")

        # 3. Only now cancel the OLD stop(s) - the new one is already
        #    confirmed resting, so the position is never briefly unprotected.
        cancelled_ids: list[int] = []
        cancel_warnings: list[str] = []
        for old in existing_stops:
            old_id = old.get("orderId")
            if old_id is None or old_id == new_raw["orderId"]:
                continue
            try:
                # Route the cancel to the service that owns the order - an
                # algo (conditional) stop is not cancellable on /fapi/v1/order.
                await self.cancel_conditional_order(
                    symbol, old_id, is_algo=old.get("_is_algo", False)
                )
                cancelled_ids.append(old_id)
                logger.info(f"{symbol}: old stop-loss order {old_id} cancelled.")
            except BinanceTradingError as exc:
                msg = (
                    f"Old stop order {old_id} could not be cancelled (will retry on the next sync) - "
                    f"two stop orders may be resting simultaneously, which is redundant but not unsafe: {exc}"
                )
                logger.warning(f"{symbol}: {msg}")
                cancel_warnings.append(msg)

        return StopReplacementResult(
            success=True,
            new_order=new_order,
            cancelled_order_ids=cancelled_ids,
            warning="; ".join(cancel_warnings) or None,
        )
