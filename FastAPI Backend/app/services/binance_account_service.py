"""
app/services/binance_account_service.py

READ-ONLY Binance account integration.

--------------------------------------------------------------------------
ARCHITECTURE / SEPARATION OF CONCERNS
--------------------------------------------------------------------------
This project has TWO independent Binance integrations:

    1. AI Module  -> app.services.binance_service.BinanceDataManager
                     Uses Binance's PUBLIC market-data endpoints only
                     (klines / websocket kline streams). No API key is
                     ever required or used there. Powers signal
                     generation. THIS FILE MUST NEVER BE IMPORTED HERE,
                     and that file must never be modified as part of
                     this feature.

    2. Account Module -> app.services.binance_account_service.BinanceAccountService (this file)
                     Uses Binance's PRIVATE/authenticated endpoints to
                     show the connected user their own account data
                     (balances, positions, orders, trade history).

These two are intentionally kept 100% separate so that a bug or outage
in one can never affect the other, and so the AI signal engine can
keep running even if no Binance API key has ever been configured.

--------------------------------------------------------------------------
SAFETY GUARANTEES (READ THIS BEFORE EDITING)
--------------------------------------------------------------------------
* Every request this service makes is an HTTP GET.
* `_signed_get` / `_public_get` are the ONLY two methods in this class
  that talk to the network, and neither accepts an HTTP verb argument.
* No method here places, amends, or cancels an order.
* No method here transfers, withdraws, or deposits funds.
* No method here changes leverage, margin type, or any account setting.

If a future requirement needs Binance to place/cancel orders, that is a
different, explicitly-scoped feature and must NOT be added to this
class. Create a distinct, clearly-labelled trading service instead so
this account-viewer service remains provably read-only.

--------------------------------------------------------------------------
ENVIRONMENTS
--------------------------------------------------------------------------
Pass `testnet=True` to point every request at Binance's current Demo
Trading / Testnet endpoints - Spot (https://demo-api.binance.com) and
USDS-M Futures (https://demo-fapi.binance.com) - instead of mainnet. The
exact same code path is used either way - only the base URLs change.

Note: Binance retired the older testnet.binance.vision / testnet.binancefuture.com
domains in favor of demo-api.binance.com / demo-fapi.binance.com (the domains
backing the "Demo Trading" mode reachable from demo.binance.com, where API keys
for this mode are created). Using the old domains causes authenticated Spot
requests to fail with error -2015 ("Invalid API-key, IP, or permissions for
action") even with a valid, fully-permissioned key.

--------------------------------------------------------------------------
ENCRYPTED KEY STORAGE
--------------------------------------------------------------------------
This service only talks to Binance - it never reads or writes credentials
to disk. Encrypting API key/secret pairs at rest is handled separately by
ApiKeyCipher in app.security.api_key_cipher, so this file stays focused
purely on Binance API communication.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional
from urllib.parse import urlencode

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from app.services.binance_time_sync import get_time_sync


# ==========================================================================
# Exceptions
# ==========================================================================
class BinanceAccountError(Exception):
    """Raised when a Binance authenticated request fails or returns an error payload."""


# ==========================================================================
# Response models
# ==========================================================================
class ConnectionTestResult(BaseModel):
    success: bool
    environment: str  # "mainnet" | "testnet"
    message: str
    account_type: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)


class AccountInfo(BaseModel):
    account_type: str
    account_status: str  # derived: "ACTIVE" | "RESTRICTED"
    can_trade: bool
    can_withdraw: bool
    can_deposit: bool
    buyer_commission: Optional[float] = None
    seller_commission: Optional[float] = None
    update_time: Optional[int] = None


class AssetBalance(BaseModel):
    asset: str
    free: float
    locked: float
    total: float
    value_usdt: Optional[float] = None  # None when no USDT trading pair exists to price it


class FuturesPosition(BaseModel):
    symbol: str
    position_amt: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    margin_type: Optional[str] = None
    position_side: Optional[str] = None
    # Enriched from the read-only /fapi/v2/positionRisk endpoint (merged by
    # symbol in get_futures_account) - still a GET, still no order-placing
    # capability added to this class.
    mark_price: Optional[float] = None
    liquidation_price: Optional[float] = None
    break_even_price: Optional[float] = None
    # positionInitialMargin (cross) or isolatedWallet (isolated) from
    # /fapi/v2/account - what Binance's own UI labels "Margin" per position.
    margin: Optional[float] = None
    # unrealized_pnl / margin * 100 - matches the "PNL(ROI%)" figure shown
    # next to each position in Binance's Positions table.
    roi_percent: Optional[float] = None


class FuturesAccountInfo(BaseModel):
    wallet_balance: float
    available_balance: float
    unrealized_pnl: float
    margin_balance: float
    total_maintenance_margin: Optional[float] = None
    open_positions: list[FuturesPosition] = Field(default_factory=list)


class TradeRecord(BaseModel):
    market: str  # "spot" | "futures"
    symbol: str
    side: str  # BUY | SELL
    quantity: float
    entry_price: float
    exit_price: Optional[float] = None  # Binance trade fills don't expose a matched exit price
    realized_pnl: Optional[float] = None  # only reported by Binance for futures fills
    commission: float
    commission_asset: Optional[str] = None
    time: int  # epoch ms


class OpenOrderInfo(BaseModel):
    market: str  # "spot" | "futures"
    symbol: str
    side: str
    price: float
    quantity: float
    status: str
    order_id: int
    time: int


class OrderHistoryItem(BaseModel):
    """A single Futures order (any terminal state - FILLED/CANCELED/EXPIRED/etc.), not just currently-open ones."""

    symbol: str
    side: str
    order_type: str
    price: float
    avg_price: float
    orig_qty: float
    executed_qty: float
    status: str
    order_id: int
    time: int


class IncomeHistoryItem(BaseModel):
    """A single Futures account ledger entry - funding fee, realized PnL, commission, transfer, etc."""

    symbol: Optional[str] = None
    income_type: str
    income: float
    asset: str
    time: int
    info: Optional[str] = None


class AccountSnapshot(BaseModel):
    """Everything the Account screen needs, fetched in one Refresh."""

    environment: str
    account: Optional[AccountInfo] = None
    balances: list[AssetBalance] = Field(default_factory=list)
    total_wallet_value_usdt: Optional[float] = None
    futures: Optional[FuturesAccountInfo] = None
    trade_history: list[TradeRecord] = Field(default_factory=list)
    open_orders: list[OpenOrderInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ==========================================================================
# Service
# ==========================================================================
class BinanceAccountService:
    """
    READ-ONLY client for Binance's authenticated account endpoints.
    See the module docstring for the safety guarantees this class upholds.

    Usage:
        async with BinanceAccountService(api_key, api_secret, testnet=True) as svc:
            result = await svc.test_connection()
            snapshot = await svc.get_full_snapshot(trade_history_symbols=["BTCUSDT"])
    """

    SPOT_MAINNET_URL = "https://api.binance.com"
    SPOT_TESTNET_URL = "https://demo-api.binance.com"
    FUTURES_MAINNET_URL = "https://fapi.binance.com"
    FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"

    # Widened from 5000 -> 10000 (Binance max is 60000) to tolerate host clock
    # skew; binance_time_sync corrects the timestamp itself, and a modest
    # window keeps the replay-attack surface small.
    RECV_WINDOW = 10000
    HTTP_TIMEOUT = 15.0

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        if not api_key or not api_secret:
            raise ValueError("api_key and api_secret are required.")

        self._api_key = api_key
        self._api_secret = api_secret
        self.testnet = testnet
        self.environment = "testnet" if testnet else "mainnet"
        self.spot_base_url = self.SPOT_TESTNET_URL if testnet else self.SPOT_MAINNET_URL
        self.futures_base_url = self.FUTURES_TESTNET_URL if testnet else self.FUTURES_MAINNET_URL
        self._client = httpx.AsyncClient(timeout=self.HTTP_TIMEOUT)
        # Binance-aligned timestamps (fixes -1021 clock-skew rejections). The
        # offset is host-clock-vs-Binance and identical across Binance hosts,
        # so one synchroniser against the futures time endpoint serves every
        # signed request this read-only client makes (spot and futures alike).
        self._time_sync = get_time_sync(self.futures_base_url)

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self) -> "BinanceAccountService":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    # ----------------------------------------------------------------
    # Low-level request helpers.
    # These are the ONLY two methods in this class that touch the
    # network, and both are hard-coded to HTTP GET.
    # ----------------------------------------------------------------
    def _sign(self, params: dict) -> dict:
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _send_signed_get(self, base_url: str, path: str, params: Optional[dict]) -> httpx.Response:
        """Stamp a fresh Binance-aligned timestamp, sign a copy, and send ONE
        GET. Leaves `params` untouched so the caller can resend for a retry."""
        body = dict(params or {})
        body["timestamp"] = await self._time_sync.now_ms()
        body.setdefault("recvWindow", self.RECV_WINDOW)
        signed = self._sign(dict(body))
        headers = {"X-MBX-APIKEY": self._api_key}
        return await self._client.get(f"{base_url}{path}", params=signed, headers=headers)

    async def _signed_get(self, base_url: str, path: str, params: Optional[dict] = None) -> httpx.Response:
        """SIGNED read-only GET request. Never used for order placement/cancellation.

        On a -1021 (timestamp outside recvWindow) it forces a clock re-sync and
        retries EXACTLY ONCE. Every call here is an idempotent read, so a retry
        can never cause a side effect."""
        resp = await self._send_signed_get(base_url, path, params)
        if resp.status_code >= 400 and self._error_code(resp) == -1021:
            logger.warning(
                "Binance -1021 (clock skew) on GET {}; re-syncing time and retrying once.",
                path,
            )
            await self._time_sync.resync()
            resp = await self._send_signed_get(base_url, path, params)
        return resp

    @staticmethod
    def _error_code(resp: httpx.Response) -> Optional[int]:
        """The numeric Binance error code from an error response body, or None."""
        try:
            return resp.json().get("code")
        except Exception:
            return None

    async def _public_get(self, base_url: str, path: str, params: Optional[dict] = None) -> httpx.Response:
        """Unsigned public GET (e.g. ticker prices for USDT valuation) - no credentials sent."""
        return await self._client.get(f"{base_url}{path}", params=params or {})

    @staticmethod
    def _raise_for_binance_error(resp: httpx.Response, context: str) -> None:
        if resp.status_code >= 400:
            code = None
            msg = resp.text
            try:
                body = resp.json()
                msg = body.get("msg", resp.text)
                code = body.get("code")
            except Exception:
                pass
            raise BinanceAccountError(f"{context} failed ({resp.status_code}, code={code}): {msg}")

    # ----------------------------------------------------------------
    # Test Connection
    # ----------------------------------------------------------------
    async def test_connection(self) -> ConnectionTestResult:
        """Verifies the API key/secret are valid for the selected environment."""
        try:
            resp = await self._signed_get(self.spot_base_url, "/api/v3/account")
            self._raise_for_binance_error(resp, "Test connection")
            data = resp.json()
            return ConnectionTestResult(
                success=True,
                environment=self.environment,
                message=f"Connected to Binance {self.environment} successfully.",
                account_type=data.get("accountType"),
                permissions=data.get("permissions", []),
            )
        except BinanceAccountError as exc:
            return ConnectionTestResult(success=False, environment=self.environment, message=str(exc))
        except httpx.RequestError as exc:
            return ConnectionTestResult(
                success=False, environment=self.environment, message=f"Network error: {exc}"
            )

    # ----------------------------------------------------------------
    # Account info (SPOT)
    # ----------------------------------------------------------------
    async def get_account_info(self) -> AccountInfo:
        resp = await self._signed_get(self.spot_base_url, "/api/v3/account")
        self._raise_for_binance_error(resp, "Get account info")
        data = resp.json()

        can_trade = bool(data.get("canTrade", False))
        # Binance's classic /api/v3/account response has no standalone "status" field;
        # we derive a simple, transparent status from the permission flags it does return.
        account_status = "ACTIVE" if can_trade else "RESTRICTED"

        return AccountInfo(
            account_type=data.get("accountType", "UNKNOWN"),
            account_status=account_status,
            can_trade=can_trade,
            can_withdraw=bool(data.get("canWithdraw", False)),
            can_deposit=bool(data.get("canDeposit", False)),
            buyer_commission=data.get("buyerCommission"),
            seller_commission=data.get("sellerCommission"),
            update_time=data.get("updateTime"),
        )

    # ----------------------------------------------------------------
    # Balances (SPOT) + best-effort USDT valuation
    # ----------------------------------------------------------------
    async def _get_usdt_prices(self) -> dict[str, float]:
        """Public ticker prices, used only to value non-USDT balances in USDT."""
        try:
            resp = await self._public_get(self.spot_base_url, "/api/v3/ticker/price")
            if resp.status_code >= 400:
                return {}
            rows = resp.json()
            return {
                row["symbol"]: float(row["price"])
                for row in rows
                if isinstance(row, dict) and str(row.get("symbol", "")).endswith("USDT")
            }
        except Exception as exc:
            logger.warning(f"Could not fetch USDT ticker prices for balance valuation: {exc}")
            return {}

    async def get_balances(self, min_total: float = 0.0) -> tuple[list[AssetBalance], Optional[float]]:
        """
        Returns (balances, total_wallet_value_usdt).
        total_wallet_value_usdt is None only if no assets could be priced at all.
        """
        resp = await self._signed_get(self.spot_base_url, "/api/v3/account")
        self._raise_for_binance_error(resp, "Get balances")
        raw_balances = resp.json().get("balances", [])

        prices = await self._get_usdt_prices()

        balances: list[AssetBalance] = []
        total_usdt = 0.0
        priced_any = False

        for row in raw_balances:
            free = float(row.get("free", 0))
            locked = float(row.get("locked", 0))
            total = free + locked
            if total <= min_total:
                continue

            asset = row.get("asset", "")
            value_usdt: Optional[float] = None
            if asset == "USDT":
                value_usdt = total
            else:
                price = prices.get(f"{asset}USDT")
                if price is not None:
                    value_usdt = total * price

            if value_usdt is not None:
                total_usdt += value_usdt
                priced_any = True

            balances.append(
                AssetBalance(asset=asset, free=free, locked=locked, total=total, value_usdt=value_usdt)
            )

        balances.sort(key=lambda b: (b.value_usdt or 0.0), reverse=True)
        return balances, (total_usdt if priced_any else None)

    # ----------------------------------------------------------------
    # Futures account (USDT-M) - optional, gracefully degrades if the
    # key has no futures permission or futures isn't enabled.
    # ----------------------------------------------------------------
    async def _get_position_risk_by_symbol(self) -> dict:
        """
        GET /fapi/v2/positionRisk (read-only) keyed by symbol - the only
        endpoint that exposes markPrice/liquidationPrice/breakEvenPrice per
        position. Best-effort: returns {} on any failure so a hiccup here
        never breaks the rest of the futures snapshot, just leaves the
        enrichment fields as None.
        """
        try:
            resp = await self._signed_get(self.futures_base_url, "/fapi/v2/positionRisk")
            if resp.status_code >= 400:
                logger.info(f"Position risk not available ({resp.status_code}): {resp.text}")
                return {}
            return {row["symbol"]: row for row in resp.json() if float(row.get("positionAmt", 0)) != 0}
        except Exception as exc:
            logger.warning(f"Position risk fetch failed: {exc}")
            return {}

    async def get_futures_account(self) -> Optional[FuturesAccountInfo]:
        try:
            resp = await self._signed_get(self.futures_base_url, "/fapi/v2/account")
        except httpx.RequestError as exc:
            logger.warning(f"Futures account fetch failed: {exc}")
            return None

        if resp.status_code >= 400:
            logger.info(f"Futures account not available ({resp.status_code}): {resp.text}")
            return None

        data = resp.json()
        risk_by_symbol = await self._get_position_risk_by_symbol()

        positions: list[FuturesPosition] = []
        for p in data.get("positions", []):
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue

            unrealized_pnl = float(p.get("unrealizedProfit", 0))
            # Binance's own "Margin" column: isolated wallet margin for an
            # ISOLATED position, position initial margin for a CROSS one.
            is_isolated = str(p.get("isolated", "false")).lower() == "true"
            margin = float(p.get("isolatedWallet", 0)) if is_isolated else float(p.get("positionInitialMargin", 0))
            roi_percent = round((unrealized_pnl / margin) * 100, 2) if margin > 0 else None

            risk = risk_by_symbol.get(p.get("symbol", ""), {})

            positions.append(
                FuturesPosition(
                    symbol=p.get("symbol", ""),
                    position_amt=amt,
                    entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=unrealized_pnl,
                    leverage=int(float(p.get("leverage", 0))),
                    margin_type=p.get("marginType"),
                    position_side=p.get("positionSide"),
                    mark_price=float(risk["markPrice"]) if risk.get("markPrice") not in (None, "") else None,
                    liquidation_price=(
                        float(risk["liquidationPrice"])
                        if risk.get("liquidationPrice") not in (None, "", "0")
                        else None
                    ),
                    break_even_price=(
                        float(p["breakEvenPrice"]) if p.get("breakEvenPrice") not in (None, "") else None
                    ),
                    margin=margin if margin > 0 else None,
                    roi_percent=roi_percent,
                )
            )

        return FuturesAccountInfo(
            wallet_balance=float(data.get("totalWalletBalance", 0)),
            available_balance=float(data.get("availableBalance", 0)),
            unrealized_pnl=float(data.get("totalUnrealizedProfit", 0)),
            margin_balance=float(data.get("totalMarginBalance", 0)),
            total_maintenance_margin=(
                float(data["totalMaintMargin"]) if "totalMaintMargin" in data else None
            ),
            open_positions=positions,
        )

    # ----------------------------------------------------------------
    # Open orders (SPOT + FUTURES) - account-wide, no symbol required
    # ----------------------------------------------------------------
    async def get_open_orders(self) -> list[OpenOrderInfo]:
        orders: list[OpenOrderInfo] = []

        try:
            resp = await self._signed_get(self.spot_base_url, "/api/v3/openOrders")
            if resp.status_code < 400:
                for o in resp.json():
                    orders.append(
                        OpenOrderInfo(
                            market="spot",
                            symbol=o.get("symbol", ""),
                            side=o.get("side", ""),
                            price=float(o.get("price", 0)),
                            quantity=float(o.get("origQty", 0)),
                            status=o.get("status", ""),
                            order_id=int(o.get("orderId", 0)),
                            time=int(o.get("time", 0)),
                        )
                    )
            else:
                logger.info(f"Spot open orders not available ({resp.status_code}): {resp.text}")
        except httpx.RequestError as exc:
            logger.warning(f"Spot open orders fetch failed: {exc}")

        try:
            resp = await self._signed_get(self.futures_base_url, "/fapi/v1/openOrders")
            if resp.status_code < 400:
                for o in resp.json():
                    orders.append(
                        OpenOrderInfo(
                            market="futures",
                            symbol=o.get("symbol", ""),
                            side=o.get("side", ""),
                            price=float(o.get("price", 0)),
                            quantity=float(o.get("origQty", 0)),
                            status=o.get("status", ""),
                            order_id=int(o.get("orderId", 0)),
                            time=int(o.get("time", 0)),
                        )
                    )
            else:
                logger.info(f"Futures open orders not available ({resp.status_code}): {resp.text}")
        except httpx.RequestError as exc:
            logger.warning(f"Futures open orders fetch failed: {exc}")

        orders.sort(key=lambda o: o.time, reverse=True)
        return orders

    # ----------------------------------------------------------------
    # Symbol auto-discovery - Binance requires an explicit symbol for
    # every trade-history request; there is no "all symbols" call. The
    # frontend should never have to know or supply symbols itself, so
    # this derives a candidate list automatically from live account
    # state, in order of reliability:
    #
    #   1. Symbols with an open order right now (spot + futures)
    #   2. Symbols with an open futures position right now
    #   3. Symbols implied by non-zero spot balances (e.g. holding BTC
    #      implies BTCUSDT is worth checking) - the closest available
    #      proxy for "recent trades", since Binance exposes no
    #      symbol-less recent-trades endpoint for spot accounts.
    #
    # Pass already-fetched `open_orders` / `futures` / `balances` to
    # avoid redundant network calls (get_full_snapshot does this).
    # ----------------------------------------------------------------
    async def discover_symbols(
        self,
        open_orders: Optional[list[OpenOrderInfo]] = None,
        futures: Optional[FuturesAccountInfo] = None,
        balances: Optional[list[AssetBalance]] = None,
    ) -> list[str]:
        symbols: set[str] = set()

        if open_orders is None:
            try:
                open_orders = await self.get_open_orders()
            except Exception as exc:
                logger.warning(f"Symbol discovery: open orders lookup failed: {exc}")
                open_orders = []
        symbols.update(o.symbol for o in open_orders if o.symbol)

        if futures is None:
            try:
                futures = await self.get_futures_account()
            except Exception as exc:
                logger.warning(f"Symbol discovery: futures account lookup failed: {exc}")
                futures = None
        if futures:
            symbols.update(p.symbol for p in futures.open_positions if p.symbol)

        if balances is None:
            try:
                balances, _ = await self.get_balances()
            except Exception as exc:
                logger.warning(f"Symbol discovery: balances lookup failed: {exc}")
                balances = []

        stable_assets = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI"}
        for b in balances:
            if b.asset in stable_assets:
                continue
            # value_usdt is only ever set when a "{asset}USDT" ticker was
            # found to exist on Binance, so this doubles as a validity check.
            if b.value_usdt is not None:
                symbols.add(f"{b.asset}USDT")

        return sorted(symbols)

    # ----------------------------------------------------------------
    # Trade history - Binance requires a symbol per request. If the
    # caller doesn't supply `symbols`, they are auto-discovered via
    # discover_symbols() so callers (including the frontend) never
    # need to know or manage symbols themselves.
    # ----------------------------------------------------------------
    async def get_trade_history(
        self, symbols: Optional[list[str]] = None, limit: int = 50, market: str = "both"
    ) -> list[TradeRecord]:
        if not symbols:
            symbols = await self.discover_symbols()
            if not symbols:
                logger.info("No symbols could be auto-discovered for trade history.")
                return []

        records: list[TradeRecord] = []

        for raw_symbol in symbols:
            symbol = raw_symbol.upper()

            if market in ("spot", "both"):
                try:
                    resp = await self._signed_get(
                        self.spot_base_url, "/api/v3/myTrades", {"symbol": symbol, "limit": limit}
                    )
                    if resp.status_code < 400:
                        for t in resp.json():
                            records.append(
                                TradeRecord(
                                    market="spot",
                                    symbol=symbol,
                                    side="BUY" if t.get("isBuyer") else "SELL",
                                    quantity=float(t.get("qty", 0)),
                                    entry_price=float(t.get("price", 0)),
                                    exit_price=None,
                                    realized_pnl=None,
                                    commission=float(t.get("commission", 0)),
                                    commission_asset=t.get("commissionAsset"),
                                    time=int(t.get("time", 0)),
                                )
                            )
                except httpx.RequestError as exc:
                    logger.warning(f"Spot trade history fetch failed for {symbol}: {exc}")

            if market in ("futures", "both"):
                try:
                    resp = await self._signed_get(
                        self.futures_base_url, "/fapi/v1/userTrades", {"symbol": symbol, "limit": limit}
                    )
                    if resp.status_code < 400:
                        for t in resp.json():
                            records.append(
                                TradeRecord(
                                    market="futures",
                                    symbol=symbol,
                                    side=t.get("side", ""),
                                    quantity=float(t.get("qty", 0)),
                                    entry_price=float(t.get("price", 0)),
                                    exit_price=None,
                                    realized_pnl=float(t.get("realizedPnl", 0)),
                                    commission=float(t.get("commission", 0)),
                                    commission_asset=t.get("commissionAsset"),
                                    time=int(t.get("time", 0)),
                                )
                            )
                except httpx.RequestError as exc:
                    logger.warning(f"Futures trade history fetch failed for {symbol}: {exc}")

        records.sort(key=lambda r: r.time, reverse=True)
        return records

    # ----------------------------------------------------------------
    # Order history (FUTURES only, any terminal status) - separate from
    # get_open_orders (currently-open only). Same symbol-required
    # constraint and auto-discovery fallback as trade history.
    # ----------------------------------------------------------------
    async def get_order_history(
        self, symbols: Optional[list[str]] = None, limit: int = 50
    ) -> list[OrderHistoryItem]:
        if not symbols:
            symbols = await self.discover_symbols()
            if not symbols:
                logger.info("No symbols could be auto-discovered for order history.")
                return []

        orders: list[OrderHistoryItem] = []
        for raw_symbol in symbols:
            symbol = raw_symbol.upper()
            try:
                resp = await self._signed_get(
                    self.futures_base_url, "/fapi/v1/allOrders", {"symbol": symbol, "limit": limit}
                )
                if resp.status_code < 400:
                    for o in resp.json():
                        orders.append(
                            OrderHistoryItem(
                                symbol=symbol,
                                side=o.get("side", ""),
                                order_type=o.get("type", ""),
                                price=float(o.get("price", 0)),
                                avg_price=float(o.get("avgPrice", 0)),
                                orig_qty=float(o.get("origQty", 0)),
                                executed_qty=float(o.get("executedQty", 0)),
                                status=o.get("status", ""),
                                order_id=int(o.get("orderId", 0)),
                                time=int(o.get("time", 0)),
                            )
                        )
                else:
                    logger.info(f"Order history not available for {symbol} ({resp.status_code}): {resp.text}")
            except httpx.RequestError as exc:
                logger.warning(f"Order history fetch failed for {symbol}: {exc}")

        orders.sort(key=lambda o: o.time, reverse=True)
        return orders

    # ----------------------------------------------------------------
    # Income/transaction history (FUTURES only) - funding fees, realized
    # PnL, commission, transfers. Account-wide, no symbol required.
    # ----------------------------------------------------------------
    async def get_income_history(
        self, limit: int = 50, income_type: Optional[str] = None
    ) -> list[IncomeHistoryItem]:
        params: dict = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type

        try:
            resp = await self._signed_get(self.futures_base_url, "/fapi/v1/income", params)
        except httpx.RequestError as exc:
            logger.warning(f"Income history fetch failed: {exc}")
            return []

        if resp.status_code >= 400:
            logger.info(f"Income history not available ({resp.status_code}): {resp.text}")
            return []

        items = [
            IncomeHistoryItem(
                symbol=row.get("symbol") or None,
                income_type=row.get("incomeType", ""),
                income=float(row.get("income", 0)),
                asset=row.get("asset", ""),
                time=int(row.get("time", 0)),
                info=row.get("info") or None,
            )
            for row in resp.json()
        ]
        items.sort(key=lambda i: i.time, reverse=True)
        return items

    # ----------------------------------------------------------------
    # Full snapshot - everything the Account screen needs, in one call
    # (used by the frontend's "Refresh" button). Individual sections
    # degrade gracefully (recorded in `warnings`) instead of failing
    # the whole snapshot if e.g. futures isn't enabled for this key.
    #
    # `trade_history_symbols` is optional: the frontend never needs to
    # supply it. When omitted, symbols are auto-discovered from the
    # account state already fetched above (open orders, open futures
    # positions, priced balances) via discover_symbols() - no extra
    # network calls beyond what this method makes anyway.
    # ----------------------------------------------------------------
    async def get_full_snapshot(self, trade_history_symbols: Optional[list[str]] = None) -> AccountSnapshot:
        snapshot = AccountSnapshot(environment=self.environment)
        warnings: list[str] = []

        try:
            snapshot.account = await self.get_account_info()
        except BinanceAccountError as exc:
            warnings.append(f"Account info unavailable: {exc}")

        balances: list[AssetBalance] = []
        try:
            balances, total_usdt = await self.get_balances()
            snapshot.balances = balances
            snapshot.total_wallet_value_usdt = total_usdt
        except BinanceAccountError as exc:
            warnings.append(f"Balances unavailable: {exc}")

        snapshot.futures = await self.get_futures_account()
        if snapshot.futures is None:
            warnings.append("Futures account not available (not enabled for this API key, or no futures permission).")

        try:
            snapshot.open_orders = await self.get_open_orders()
        except Exception as exc:  # defensive: never let orders fetch break the whole snapshot
            warnings.append(f"Open orders unavailable: {exc}")

        symbols = trade_history_symbols
        if not symbols:
            symbols = await self.discover_symbols(
                open_orders=snapshot.open_orders, futures=snapshot.futures, balances=balances
            )
            if not symbols:
                warnings.append(
                    "No symbols could be auto-discovered for trade history "
                    "(no open orders, open positions, or priced balances)."
                )

        if symbols:
            try:
                snapshot.trade_history = await self.get_trade_history(symbols)
            except Exception as exc:
                warnings.append(f"Trade history unavailable: {exc}")

        snapshot.warnings = warnings
        return snapshot
