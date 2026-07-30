"""
Free, no-API-key fundamental/macro context signals for the AI scorer.

Crypto "fundamentals" aren't earnings reports - they're derivatives-market
positioning and market-wide sentiment:
  - Open Interest trend (Binance public futures data): rising OI alongside
    a price move means fresh positions are fueling it (real conviction);
    falling OI means positions are being unwound.
  - Long/short account ratio (Binance public futures data): a mild
    contrarian signal - a heavily one-sided crowd tends to get squeezed.
  - Crypto Fear & Greed Index (alternative.me): market-wide sentiment,
    extreme readings often mark tops/bottoms.
  - BTC Dominance (CoinGecko): matters specifically for altcoins - a high
    dominance regime means capital has rotated into BTC and out of alts.

All four are genuinely free and require no account or API key. Market-wide
values (Fear & Greed, BTC dominance) are cached and shared across every
symbol; per-symbol values (OI trend, long/short ratio) are cached per
symbol so a 50-symbol scan doesn't hammer these endpoints every 15m.
"""
import time
from typing import Dict, Optional, Tuple

import httpx
from loguru import logger


class FundamentalsService:
    BINANCE_FUTURES_URL = "https://fapi.binance.com"
    FNG_URL = "https://api.alternative.me/fng/"
    COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

    OI_CACHE_TTL = 300.0          # 5 min
    LONG_SHORT_CACHE_TTL = 300.0  # 5 min
    FNG_CACHE_TTL = 3600.0        # 1 hour - the index itself only updates ~daily
    DOMINANCE_CACHE_TTL = 1800.0  # 30 min

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._oi_cache: Dict[str, Tuple[dict, float]] = {}
        self._ls_cache: Dict[str, Tuple[float, float]] = {}
        self._fng_cache: Optional[Tuple[dict, float]] = None
        self._dominance_cache: Optional[Tuple[float, float]] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def get_open_interest_trend(self, symbol: str) -> Dict[str, float]:
        """
        {"oi_trend": "rising"|"falling"|"flat", "oi_change_pct": float}
        comparing the latest 15m OI snapshot to ~2 hours ago.
        """
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._oi_cache.get(symbol)
        if cached and (now - cached[1]) < self.OI_CACHE_TTL:
            return cached[0]

        result = {"oi_trend": "flat", "oi_change_pct": 0.0}
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.BINANCE_FUTURES_URL}/futures/data/openInterestHist",
                params={"symbol": symbol, "period": "15m", "limit": 8},
            )
            resp.raise_for_status()
            data = resp.json()
            if len(data) >= 2:
                oldest = float(data[0]["sumOpenInterest"])
                latest = float(data[-1]["sumOpenInterest"])
                if oldest > 0:
                    change_pct = (latest - oldest) / oldest * 100
                    result["oi_change_pct"] = round(change_pct, 2)
                    if change_pct > 2:
                        result["oi_trend"] = "rising"
                    elif change_pct < -2:
                        result["oi_trend"] = "falling"
        except Exception as e:
            logger.warning(f"Could not fetch open interest for {symbol}: {e}")
            if cached:
                return cached[0]

        self._oi_cache[symbol] = (result, now)
        return result

    async def get_long_short_ratio(self, symbol: str) -> float:
        """
        Global long/short ACCOUNT ratio (not position size). >1 means more
        accounts are long than short. Neutral default of 1.0 on failure.
        """
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._ls_cache.get(symbol)
        if cached and (now - cached[1]) < self.LONG_SHORT_CACHE_TTL:
            return cached[0]

        ratio = 1.0
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.BINANCE_FUTURES_URL}/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": "15m", "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                ratio = float(data[-1]["longShortRatio"])
        except Exception as e:
            logger.warning(f"Could not fetch long/short ratio for {symbol}: {e}")
            if cached:
                return cached[0]

        self._ls_cache[symbol] = (ratio, now)
        return ratio

    async def get_fear_greed_index(self) -> Dict[str, object]:
        """Market-wide Fear & Greed Index (0-100), shared across all symbols."""
        now = time.monotonic()
        if self._fng_cache and (now - self._fng_cache[1]) < self.FNG_CACHE_TTL:
            return self._fng_cache[0]

        result = {"value": 50, "classification": "Neutral"}
        try:
            client = await self._get_client()
            resp = await client.get(self.FNG_URL, params={"limit": 1})
            resp.raise_for_status()
            data = resp.json()["data"][0]
            result = {"value": int(data["value"]), "classification": data["value_classification"]}
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed index: {e}")
            if self._fng_cache:
                return self._fng_cache[0]

        self._fng_cache = (result, now)
        return result

    async def get_btc_dominance(self) -> float:
        """BTC's % share of total crypto market cap, shared across all symbols."""
        now = time.monotonic()
        if self._dominance_cache and (now - self._dominance_cache[1]) < self.DOMINANCE_CACHE_TTL:
            return self._dominance_cache[0]

        dominance = 50.0
        try:
            client = await self._get_client()
            resp = await client.get(self.COINGECKO_GLOBAL_URL)
            resp.raise_for_status()
            dominance = float(resp.json()["data"]["market_cap_percentage"]["btc"])
        except Exception as e:
            logger.warning(f"Could not fetch BTC dominance: {e}")
            if self._dominance_cache:
                return self._dominance_cache[0]

        self._dominance_cache = (dominance, now)
        return dominance

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
