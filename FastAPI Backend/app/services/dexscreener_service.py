"""
DexScreener integration - free, public, no API key required.

Used by the Token Scanner feature (see app/services/token_scorer.py) to
answer the "DexScreener" section of the report: liquidity, volume, price
action, how new the pair is, and short-term momentum.

Docs: https://docs.dexscreener.com/api/reference
Rate limit: DexScreener's public API allows ~300 requests/minute per IP,
generous enough for on-demand single-token lookups (this is NOT used by
the live 15m scanner - only when a user explicitly scans a token address).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger


class DexScreenerError(Exception):
    """Raised when DexScreener's API is unreachable or returns no usable data."""


class DexScreenerService:
    BASE_URL = "https://api.dexscreener.com"
    HTTP_TIMEOUT = 15.0

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.HTTP_TIMEOUT)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "DexScreenerService":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    async def get_pairs_for_token(self, chain_id: str, token_address: str) -> List[Dict[str, Any]]:
        """
        Every trading pair DexScreener has indexed for this token contract
        on `chain_id` (e.g. "ethereum", "bsc", "solana", "base", "arbitrum"
        - DexScreener's own chain-id strings, see their docs for the full
        list). Returns [] (not an error) if the token simply isn't indexed
        yet - a real, common state for a brand new token, not a failure.

        NOTE: DexScreener retired the old `/latest/dex/tokens/{address}`
        endpoint (as of mid-2026 it silently returns `{"pairs": null}` for
        every address, even extremely liquid ones like USDT - it's dead,
        not just returning empty). The current documented endpoint is
        `/token-pairs/v1/{chainId}/{tokenAddress}` (docs.dexscreener.com/
        api/reference), which requires the chain up front and returns a
        plain JSON array (not wrapped in a "pairs" key). Verified live
        against WETH on ethereum before switching.
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise DexScreenerError(f"DexScreener request failed: {e}") from e

        # This endpoint is already scoped to `chain_id` by the URL itself,
        # but keep a defensive filter in case DexScreener ever returns a
        # mixed-chain result for an edge case.
        pairs = data if isinstance(data, list) else (data.get("pairs") or [])
        return [p for p in pairs if p.get("chainId") == chain_id]

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Free-text search (symbol, name, or address) - used when the user
        doesn't have an exact contract address handy."""
        client = await self._get_client()
        url = f"{self.BASE_URL}/latest/dex/search"
        try:
            resp = await client.get(url, params={"q": query})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise DexScreenerError(f"DexScreener search failed: {e}") from e
        return data.get("pairs") or []

    @staticmethod
    def pick_primary_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        A token can have many pairs (different DEXes, different quote
        assets). The pair with the most USD liquidity is the most reliable
        one for pricing/analysis - a $200 liquidity pair on an obscure DEX
        is noise next to the $2M main pair.
        """
        if not pairs:
            return None
        return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)

    @staticmethod
    def summarize(pair: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turns one raw DexScreener pair object into the flat fields the
        Token Scanner report needs. Every field is read defensively
        (DexScreener doesn't guarantee every key is present for every
        pair, especially very new ones) - missing data becomes None, never
        a fabricated number.
        """
        liquidity = pair.get("liquidity") or {}
        volume = pair.get("volume") or {}
        price_change = pair.get("priceChange") or {}
        txns = pair.get("txns") or {}
        txns_24h = txns.get("h24") or {}
        base_token = pair.get("baseToken") or {}
        quote_token = pair.get("quoteToken") or {}

        created_at_ms = pair.get("pairCreatedAt")
        age_hours = None
        if created_at_ms:
            age_hours = round((time.time() * 1000 - created_at_ms) / 3_600_000, 1)

        liquidity_usd = liquidity.get("usd")
        volume_24h = volume.get("h24")

        # Simple, transparent momentum read (not a black box): compares
        # short-term (1h) vs medium-term (6h) price change direction and
        # magnitude, plus whether volume is buy-dominant over the last 24h.
        change_1h = price_change.get("h1")
        change_6h = price_change.get("h6")
        buys = txns_24h.get("buys")
        sells = txns_24h.get("sells")
        buy_sell_ratio = None
        if buys is not None and sells is not None and (buys + sells) > 0:
            buy_sell_ratio = round(buys / (buys + sells), 3)

        if change_1h is None or change_6h is None:
            momentum = "unknown"
        elif change_1h > 0 and change_6h > 0 and change_1h >= change_6h / 6:
            momentum = "accelerating_up"
        elif change_1h < 0 and change_6h < 0:
            momentum = "accelerating_down"
        elif change_1h > 0 and change_6h < 0:
            momentum = "reversing_up"
        elif change_1h < 0 and change_6h > 0:
            momentum = "reversing_down"
        else:
            momentum = "flat"

        return {
            "chain_id": pair.get("chainId"),
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
            "url": pair.get("url"),
            "base_token_symbol": base_token.get("symbol"),
            "base_token_name": base_token.get("name"),
            "base_token_address": base_token.get("address"),
            "quote_token_symbol": quote_token.get("symbol"),
            "price_usd": float(pair["priceUsd"]) if pair.get("priceUsd") else None,
            "liquidity_usd": liquidity_usd,
            "fdv_usd": pair.get("fdv"),
            "market_cap_usd": pair.get("marketCap"),
            "volume_24h_usd": volume_24h,
            "volume_6h_usd": volume.get("h6"),
            "volume_1h_usd": volume.get("h1"),
            "price_change_5m_pct": price_change.get("m5"),
            "price_change_1h_pct": change_1h,
            "price_change_6h_pct": change_6h,
            "price_change_24h_pct": price_change.get("h24"),
            "txns_24h_buys": buys,
            "txns_24h_sells": sells,
            "buy_sell_ratio_24h": buy_sell_ratio,
            "pair_age_hours": age_hours,
            "is_new_pair": bool(age_hours is not None and age_hours < 24),
            "momentum": momentum,
        }


async def fetch_dexscreener_summary(chain_id: str, token_address: str) -> Dict[str, Any]:
    """
    Convenience one-shot: fetch every pair for this token on `chain_id`,
    pick the most-liquid one, and return the flat summary dict - or a
    dict with an "error" key (never raises) if nothing was found, so
    callers building a partial report can just check for that key instead
    of wrapping every call in try/except.
    """
    async with DexScreenerService() as service:
        try:
            pairs = await service.get_pairs_for_token(chain_id, token_address)
        except DexScreenerError as e:
            logger.warning(f"[token-scan] DexScreener lookup failed for {token_address}: {e}")
            return {"error": str(e)}

        if not pairs:
            return {"error": f"No DexScreener pairs found for {token_address} on {chain_id}."}

        primary = service.pick_primary_pair(pairs)
        summary = service.summarize(primary)
        summary["all_pairs_count"] = len(pairs)
        return summary


async def search_tokens(query: str, allowed_chains: List[str]) -> List[Dict[str, Any]]:
    """
    Free-text symbol/name search (e.g. "PEPE") for the Token Scanner's
    search-and-pick flow: a ticker alone is ambiguous (many unrelated
    tokens share the same symbol across chains, and even the same chain
    can have multiple unrelated contracts using it), so this returns every
    distinct CONTRACT DexScreener found - not every pair - deduplicated by
    (chain, base token address), keeping each token's single most-liquid
    pair for display. Restricted to `allowed_chains` (the chains this
    scanner's technical-analysis/security engines actually support - see
    CHAIN_TO_GECKOTERMINAL_NETWORK) since a match on an unsupported chain
    can't be scanned anyway. Sorted by liquidity, most liquid first - the
    top results are almost always the "real" token; near-zero-liquidity
    entries further down are typically scam/impersonator contracts.
    """
    async with DexScreenerService() as service:
        try:
            pairs = await service.search(query)
        except DexScreenerError as e:
            logger.warning(f"[token-scan] DexScreener search failed for '{query}': {e}")
            return []

        best_per_token: Dict[tuple, Dict[str, Any]] = {}
        for pair in pairs:
            chain_id = pair.get("chainId")
            if chain_id not in allowed_chains:
                continue

            base_token = pair.get("baseToken") or {}
            token_address = base_token.get("address")
            if not token_address:
                continue

            key = (chain_id, token_address.lower())
            liquidity = (pair.get("liquidity") or {}).get("usd") or 0
            existing = best_per_token.get(key)
            existing_liquidity = (
                (existing.get("liquidity") or {}).get("usd") or 0 if existing else -1
            )
            if existing is None or liquidity > existing_liquidity:
                best_per_token[key] = pair

        results = sorted(
            best_per_token.values(),
            key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
            reverse=True,
        )
        return results
