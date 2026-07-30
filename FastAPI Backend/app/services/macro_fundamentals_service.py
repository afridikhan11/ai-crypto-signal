"""
Free, no-API-key macro context signals for commodity (Gold/Silver/Oil)
signals - the crypto fundamentals in fundamentals_service.py (OI trend,
long/short ratio, Fear & Greed, BTC dominance) don't mean anything for
XAUUSDT/XAGUSDT/CLUSDT/BZUSDT, so those get real macro drivers instead:

  - US Dollar Index (DXY) trend: gold/silver are priced in USD and move
    inversely to dollar strength most of the time - a strengthening dollar
    is a headwind for a LONG, a weakening dollar is a tailwind.
  - US 10Y real yield (DFII10) trend: the classic opportunity-cost driver
    for a non-yielding asset like gold - rising real yields make holding
    gold relatively less attractive, falling real yields make it more so.
  - High-impact USD economic calendar risk (FOMC/CPI/NFP etc.): these
    releases can move gold sharply and unpredictably in either direction,
    so this is used as a confidence penalty (regardless of direction)
    rather than a directional signal, the same way "High volatility" is
    penalized in AIScorer.institutional_filters.

All three sources are public and require no account/API key:
  - Yahoo Finance's public chart endpoint for DX-Y.NYB (DXY index)
  - FRED's public CSV export for DFII10 (10-Year TIPS constant maturity)
  - ForexFactory's public weekly calendar JSON feed

Every fetch is cache-first with stale-cache fallback on error, matching
the pattern in fundamentals_service.py - a failed macro fetch must never
block signal generation, it should just fall back to "neutral"/last-known.
"""
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx
from loguru import logger


class MacroFundamentalsService:
    DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
    FRED_REAL_YIELD_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
    CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    DXY_CACHE_TTL = 1800.0          # 30 min
    REAL_YIELD_CACHE_TTL = 21600.0  # 6 hours - DFII10 only updates once/day
    CALENDAR_CACHE_TTL = 3600.0     # 1 hour

    DXY_TREND_THRESHOLD_PCT = 0.15       # % move over the lookback window to call a trend
    REAL_YIELD_TREND_THRESHOLD_BPS = 3.0  # basis points

    EVENT_LOOKAHEAD_HOURS = 24.0

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._dxy_cache: Optional[Tuple[dict, float]] = None
        self._yield_cache: Optional[Tuple[dict, float]] = None
        self._calendar_cache: Optional[Tuple[dict, float]] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Yahoo's public endpoint 403s without a browser-like UA.
            self._client = httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": "Mozilla/5.0"}
            )
        return self._client

    async def get_dollar_index_trend(self) -> Dict[str, object]:
        """{"dxy_trend": "up"|"down"|"flat", "dxy_change_pct": float}"""
        now = time.monotonic()
        if self._dxy_cache and (now - self._dxy_cache[1]) < self.DXY_CACHE_TTL:
            return self._dxy_cache[0]

        result = {"dxy_trend": "flat", "dxy_change_pct": 0.0}
        try:
            client = await self._get_client()
            resp = await client.get(self.DXY_URL, params={"range": "5d", "interval": "1h"})
            resp.raise_for_status()
            data = resp.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                first, last = closes[0], closes[-1]
                if first:
                    change_pct = (last - first) / first * 100
                    result["dxy_change_pct"] = round(change_pct, 3)
                    if change_pct > self.DXY_TREND_THRESHOLD_PCT:
                        result["dxy_trend"] = "up"
                    elif change_pct < -self.DXY_TREND_THRESHOLD_PCT:
                        result["dxy_trend"] = "down"
        except Exception as e:
            logger.warning(f"Could not fetch DXY trend: {e}")
            if self._dxy_cache:
                return self._dxy_cache[0]

        self._dxy_cache = (result, now)
        return result

    async def get_real_yield_trend(self) -> Dict[str, object]:
        """{"real_yield_trend": "up"|"down"|"flat", "real_yield_change_bps": float}"""
        now = time.monotonic()
        if self._yield_cache and (now - self._yield_cache[1]) < self.REAL_YIELD_CACHE_TTL:
            return self._yield_cache[0]

        result = {"real_yield_trend": "flat", "real_yield_change_bps": 0.0}
        try:
            client = await self._get_client()
            resp = await client.get(self.FRED_REAL_YIELD_URL)
            resp.raise_for_status()
            lines = [l for l in resp.text.strip().splitlines() if l and "," in l]
            rows = []
            for line in lines[1:]:  # skip header
                _, value = line.split(",", 1)
                value = value.strip()
                if value and value != ".":
                    try:
                        rows.append(float(value))
                    except ValueError:
                        continue
            if len(rows) >= 2:
                lookback = rows[-10] if len(rows) >= 10 else rows[0]
                latest = rows[-1]
                change_bps = (latest - lookback) * 100
                result["real_yield_change_bps"] = round(change_bps, 1)
                if change_bps > self.REAL_YIELD_TREND_THRESHOLD_BPS:
                    result["real_yield_trend"] = "up"
                elif change_bps < -self.REAL_YIELD_TREND_THRESHOLD_BPS:
                    result["real_yield_trend"] = "down"
        except Exception as e:
            logger.warning(f"Could not fetch real yield trend: {e}")
            if self._yield_cache:
                return self._yield_cache[0]

        self._yield_cache = (result, now)
        return result

    async def get_high_impact_event_risk(self) -> Dict[str, object]:
        """
        {"high_impact_event_soon": bool, "event_name": str|None,
         "hours_until_event": float|None} for the next USD high-impact
        release (FOMC/CPI/NFP/etc.) within EVENT_LOOKAHEAD_HOURS.
        """
        now = time.monotonic()
        if self._calendar_cache and (now - self._calendar_cache[1]) < self.CALENDAR_CACHE_TTL:
            events = self._calendar_cache[0]
        else:
            events = None
            try:
                client = await self._get_client()
                resp = await client.get(self.CALENDAR_URL)
                resp.raise_for_status()
                events = resp.json()
                self._calendar_cache = (events, now)
            except Exception as e:
                logger.warning(f"Could not fetch economic calendar: {e}")
                if self._calendar_cache:
                    events = self._calendar_cache[0]

        result = {"high_impact_event_soon": False, "event_name": None, "hours_until_event": None}
        if not events:
            return result

        try:
            now_utc = datetime.now(timezone.utc)
            best: Optional[Tuple[float, str]] = None
            for ev in events:
                if ev.get("country") != "USD" or ev.get("impact") != "High":
                    continue
                date_str = ev.get("date")
                if not date_str:
                    continue
                try:
                    ev_time = datetime.fromisoformat(date_str)
                except ValueError:
                    continue
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
                hours_until = (ev_time - now_utc).total_seconds() / 3600.0
                if 0 <= hours_until <= self.EVENT_LOOKAHEAD_HOURS:
                    if best is None or hours_until < best[0]:
                        best = (hours_until, ev.get("title", "High-impact USD event"))
            if best is not None:
                result = {
                    "high_impact_event_soon": True,
                    "event_name": best[1],
                    "hours_until_event": round(best[0], 1),
                }
        except Exception as e:
            logger.warning(f"Could not evaluate economic calendar: {e}")

        return result

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
