"""
GeckoTerminal integration - free, public, no API key required.

This is what feeds REAL historical candles for a DEX token into the exact
same SMC/technical-analysis engine already used for Binance futures (see
app/smc/*, app/indicators/confirmation.py) - DexScreener gives current
stats but not a historical OHLCV series, so this fills that gap.

Docs: https://www.geckoterminal.com/dex-api
Rate limit: ~30 requests/minute per IP on the free tier - fine for
on-demand single-token scans, NOT meant for a live multi-symbol scanner
loop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from loguru import logger

# DexScreener chainId -> GeckoTerminal network slug. DexScreener's chainId
# strings are the more common/readable names; GeckoTerminal uses its own
# short slugs for the same networks - this is the only place that mapping
# needs to live. Extend this dict (not scattered elsewhere) if more chains
# are needed later.
CHAIN_TO_GECKOTERMINAL_NETWORK: Dict[str, str] = {
    "ethereum": "eth",
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
}


class GeckoTerminalError(Exception):
    """Raised when GeckoTerminal's API is unreachable or returns no usable data."""


class GeckoTerminalService:
    BASE_URL = "https://api.geckoterminal.com/api/v2"
    HTTP_TIMEOUT = 15.0

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # GeckoTerminal's API returns JSON:API content-type; a plain
            # Accept header avoids some proxies/CDNs serving a cached HTML
            # error page instead of the real JSON error body.
            self._client = httpx.AsyncClient(
                timeout=self.HTTP_TIMEOUT, headers={"Accept": "application/json"}
            )
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "GeckoTerminalService":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    async def get_top_pool_for_token(self, network: str, token_address: str) -> Optional[str]:
        """
        Fallback lookup for when the caller doesn't already have a pool
        address (e.g. from DexScreener) - returns the highest-liquidity
        pool address for this token on `network`, or None if nothing is
        indexed yet.
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/networks/{network}/tokens/{token_address}/pools"
        try:
            resp = await client.get(url, params={"page": 1})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise GeckoTerminalError(f"GeckoTerminal pool lookup failed: {e}") from e

        pools = data.get("data") or []
        if not pools:
            return None

        def _liquidity(p: Dict[str, Any]) -> float:
            attrs = p.get("attributes") or {}
            try:
                return float(attrs.get("reserve_in_usd") or 0)
            except (TypeError, ValueError):
                return 0.0

        best = max(pools, key=_liquidity)
        return best.get("attributes", {}).get("address")

    async def get_ohlcv(
        self,
        network: str,
        pool_address: str,
        timeframe: str = "hour",
        aggregate: int = 1,
        limit: int = 200,
    ) -> List[List[float]]:
        """
        Raw OHLCV rows: [timestamp_seconds, open, high, low, close, volume],
        newest last. `timeframe` is "day" | "hour" | "minute";  valid
        `aggregate` values depend on timeframe per GeckoTerminal's docs
        (hour: 1/4/12, minute: 1/5/15, day: 1) - an invalid combination
        raises rather than silently returning day-1 data.
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}"
        try:
            resp = await client.get(url, params={"aggregate": aggregate, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise GeckoTerminalError(f"GeckoTerminal OHLCV fetch failed: {e}") from e

        rows = (
            data.get("data", {})
            .get("attributes", {})
            .get("ohlcv_list", [])
        )
        # GeckoTerminal returns newest-first; every downstream consumer
        # (pandas DataFrame, the SMC swing detector) expects oldest-first.
        return list(reversed(rows))

    async def get_pool_trades(
        self, network: str, pool_address: str, min_volume_usd: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Individual trade prints for this pool - up to the most recent 300
        trades in the last 24h (GeckoTerminal's own cap on this endpoint,
        free tier, no API key). Each item has at least `block_timestamp`,
        `kind` ("buy"/"sell"), and `volume_in_usd` - real per-trade data,
        not an aggregate. Used by app/services/smart_money_service.py to
        build honest, trade-size-based Whale Activity / Net Flow /
        Accumulation-Distribution / Large Transactions reads WITHOUT wallet
        identity (that still requires a paid Arkham/Nansen subscription).
        `min_volume_usd` maps to GeckoTerminal's own server-side filter
        (`trade_volume_in_usd_greater_than`) so filtering for large trades
        doesn't require pulling and discarding small ones client-side.
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/networks/{network}/pools/{pool_address}/trades"
        params: Dict[str, Any] = {}
        if min_volume_usd > 0:
            params["trade_volume_in_usd_greater_than"] = min_volume_usd
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise GeckoTerminalError(f"GeckoTerminal trades fetch failed: {e}") from e

        return [row.get("attributes", {}) for row in (data.get("data") or [])]


def ohlcv_to_dataframe(rows: List[List[float]]) -> pd.DataFrame:
    """
    Converts GeckoTerminal's raw OHLCV rows into the same
    timestamp-indexed open/high/low/close/volume DataFrame shape every SMC
    module (MarketStructureEngine, LiquidityEngine, OrderBlockEngine,
    FVGDetector, SupplyDemandEngine) and ConfirmationIndicators already
    expect - so a DEX token's candles run through the EXACT SAME analysis
    code as a Binance futures symbol, not a separate reimplementation.
    """
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df.set_index("timestamp")


async def fetch_token_candles(
    network: str, pool_address: str, timeframe: str = "hour", aggregate: int = 1, limit: int = 200
) -> pd.DataFrame:
    """
    Convenience one-shot used by the Token Scanner: fetch + convert in one
    call. Returns an EMPTY dataframe (never raises) on failure, so the
    caller can check `.empty` and report "not enough chart history" as
    part of the scan result instead of the whole scan crashing.
    """
    async with GeckoTerminalService() as service:
        try:
            rows = await service.get_ohlcv(network, pool_address, timeframe, aggregate, limit)
        except GeckoTerminalError as e:
            logger.warning(f"[token-scan] GeckoTerminal OHLCV fetch failed for {pool_address}: {e}")
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        return ohlcv_to_dataframe(rows)


async def fetch_pool_trades(
    network: str, pool_address: str, min_volume_usd: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Convenience one-shot used by the Smart Money Dashboard: fetch this
    pool's recent individual trades in one call. Returns an EMPTY list
    (never raises) on failure, so the caller can treat "no trades" and
    "fetch failed" the same way - report the affected sections as
    unavailable instead of crashing the whole scan.
    """
    async with GeckoTerminalService() as service:
        try:
            return await service.get_pool_trades(network, pool_address, min_volume_usd)
        except GeckoTerminalError as e:
            logger.warning(f"[smart-money] GeckoTerminal trades fetch failed for {pool_address}: {e}")
            return []
