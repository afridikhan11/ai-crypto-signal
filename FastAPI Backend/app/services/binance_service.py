import asyncio
import json
import time
from typing import Dict, List, Optional, Callable, Awaitable, Tuple
from collections import defaultdict
from dataclasses import dataclass, field
import random
import websockets
import httpx
from loguru import logger
import pandas as pd

# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------
@dataclass
class Candle:
    timestamp: int       # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Taker BUY base-asset volume for this candle (Binance kline field
    # "V"/row[9]) - volume - taker_buy_volume is the taker SELL volume.
    # This is what CVD (Cumulative Volume Delta) is built from below; it's
    # a real aggressor-side split Binance already computes server-side per
    # candle, not an estimate. Defaults to 0.0 so any existing caller that
    # builds a Candle without it still works - CVD just reads as flat/0 in
    # that case rather than crashing.
    taker_buy_volume: float = 0.0


@dataclass
class SymbolData:
    symbol: str
    timeframes: Dict[str, List[Candle]] = field(default_factory=lambda: defaultdict(list))
    last_update: Dict[str, int] = field(default_factory=dict)

    def add_candle(self, tf: str, candle: Candle):
        if not self.timeframes[tf] or self.timeframes[tf][-1].timestamp != candle.timestamp:
            self.timeframes[tf].append(candle)
            # Keep a rolling window of at most 500 candles
            if len(self.timeframes[tf]) > 500:
                self.timeframes[tf].pop(0)
        self.last_update[tf] = candle.timestamp


# ---------------------------------------------------------------------------
# Binance Live Data Manager – Production Grade
# ---------------------------------------------------------------------------
class BinanceDataManager:
    """
    Manages live WebSocket streams for multiple symbols and timeframes,
    caches candles in memory, and provides OHLCV DataFrames for analysis.
    
    Features:
        - shared httpx.AsyncClient with connection pool
        - semaphore to limit concurrent REST requests
        - retry logic with exponential backoff + jitter
        - graceful shutdown
        - websocket auto-reconnect with exponential backoff
        - exception-safe user callbacks
    """
    BASE_WS_URL = "wss://fstream.binance.com/ws"
    REST_URL = "https://fapi.binance.com"
    MAX_REST_CONCURRENCY = 10       # max parallel kline fetches
    MAX_RETRIES = 3
    HTTP_TIMEOUT = 20.0

    # Binance funding rates only update every 8h, so there's no need to hit
    # the API on every 1-minute candle close - cache each symbol's rate for
    # a while between refetches.
    FUNDING_RATE_CACHE_TTL = 300.0  # seconds

    # 1D candles for the multi-timeframe "macro bias" filter (see
    # UniversalScanner._load_htf_dataframes) - fetched via a cached REST call
    # instead of one more live websocket kline stream per symbol. A daily
    # candle is unchanged for the entire scan cadence (every 15m) until it
    # closes, so polling it live would be 50+ extra streams for data that
    # moves at most once a day - a long-TTL REST cache is the right tool.
    DAILY_TREND_CACHE_TTL = 3600.0  # seconds

    # 1W candles (2026-07-29, ICT migration final phase) - the heaviest
    # ("macro bias") timeframe HTFStructureEngine/InstitutionalBiasEngine
    # consume (see app/smc/htf_structure_engine.py). Same reasoning as
    # DAILY_TREND_CACHE_TTL above, scaled up: a weekly candle is unchanged
    # for days at a time, so an even longer REST-cache TTL than 1D is
    # correct here, not a live stream.
    WEEKLY_TREND_CACHE_TTL = 21600.0  # seconds (6h)

    # Free, public, no-API-key-required stream of REAL liquidation events
    # across every USD-M futures symbol (Binance pushes at most the largest
    # liquidation per symbol per 1000ms). This is a genuine market signal -
    # not an estimated/predictive heatmap like paid services offer, but
    # actual forced closes as they happen - kept in a rolling window per
    # symbol so the scorer can react to real, recent liquidation pressure.
    LIQUIDATION_WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
    LIQUIDATION_WINDOW_SECONDS = 900.0  # 15 minutes

    # Max time to wait for ANY websocket message before treating the
    # connection as a silent zombie and forcing a reconnect. Binance pushes
    # kline updates several times a second across all subscribed streams, so a
    # gap this long only ever means the connection has stalled (TCP/keepalive
    # alive but no data flowing) - the failure mode that once froze the candle
    # stream, and therefore all scanning, silently for days because `async for`
    # blocks forever with no error to trigger reconnection.
    WS_STALE_TIMEOUT = 60.0

    def __init__(self, symbols: List[str], timeframes: Optional[List[str]] = None):
        self.symbols = [s.lower() for s in symbols]
        self.timeframes = timeframes or ["1m", "5m", "15m", "1h", "4h"]
        self.data: Dict[str, SymbolData] = {s: SymbolData(s) for s in self.symbols}
        self.callback: Optional[Callable[[str, str, List[Candle]], Awaitable[None]]] = None
        self._running = False
        self._ws = None
        self.is_connected = False
        self._lock = asyncio.Lock()
        self._symbol_tf_pairs = [(s, tf) for s in self.symbols for tf in self.timeframes]

        # HTTP session (created lazily)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._http_semaphore = asyncio.Semaphore(self.MAX_REST_CONCURRENCY)

        # symbol -> (last_funding_rate, fetched_at_monotonic_seconds)
        self._funding_rate_cache: Dict[str, Tuple[float, float]] = {}

        # symbol -> (1d candles dataframe, fetched_at_monotonic_seconds)
        self._daily_df_cache: Dict[str, Tuple[pd.DataFrame, float]] = {}
        # 2026-07-29, ICT migration final phase - see get_weekly_dataframe().
        self._weekly_df_cache: Dict[str, Tuple[pd.DataFrame, float]] = {}

        # symbol (lower) -> list of (event_time_ms, notional_usd, side)
        # side is "SELL" (a LONG got liquidated) or "BUY" (a SHORT got liquidated)
        self._liq_lock = asyncio.Lock()
        self.recent_liquidations: Dict[str, List[Tuple[int, float, str]]] = defaultdict(list)
        self._liq_running = False
        self._liq_ws = None

    # ------------------------------------------------------------------
    # HTTP Client Management
    # ------------------------------------------------------------------
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Returns a shared httpx.AsyncClient with connection pooling and timeouts."""
        if self._http_client is None:
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0
            )
            timeout = httpx.Timeout(self.HTTP_TIMEOUT, connect=10.0)
            self._http_client = httpx.AsyncClient(limits=limits, timeout=timeout)
        return self._http_client

    # ------------------------------------------------------------------
    # Historical Klines Fetch with Semaphore, Retry & Jitter
    # ------------------------------------------------------------------
    async def fetch_historical_klines(self, symbol: str, interval: str, limit: int = 500) -> List[Candle]:
        """Fetches klines with retry, rate‑limit handling, and concurrency control."""
        url = f"{self.REST_URL}/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}

        async with self._http_semaphore:
            client = await self._get_http_client()
            last_exc = None
            for attempt in range(self.MAX_RETRIES):
                try:
                    resp = await client.get(url, params=params)
                    # Handle rate limiting
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 2))
                        wait = min(retry_after, 30)
                        logger.warning(f"Rate limited for {symbol} {interval}, waiting {wait}s")
                        await asyncio.sleep(wait + random.uniform(0, 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (502, 503, 504):
                        last_exc = e
                        if attempt < self.MAX_RETRIES - 1:
                            wait = 2 ** attempt + random.uniform(0, 1)
                            logger.warning(f"Server error {e.response.status_code} for {symbol} {interval}, retrying in {wait:.1f}s")
                            await asyncio.sleep(wait)
                            continue
                    raise
                except httpx.RequestError as e:
                    last_exc = e
                    if attempt < self.MAX_RETRIES - 1:
                        wait = 2 ** attempt + random.uniform(0, 1)
                        logger.warning(f"Network error for {symbol} {interval}: {e}, retrying in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue
                    raise
            else:
                # exhausted retries
                raise last_exc if last_exc else Exception("Unknown error fetching klines")

        candles = []
        for row in data:
            candles.append(Candle(
                timestamp=row[0],
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                taker_buy_volume=float(row[9]) if len(row) > 9 else 0.0,
            ))
        return candles

    # ------------------------------------------------------------------
    # Initialise all symbols and timeframes (never fails completely)
    # ------------------------------------------------------------------
    async def initialise_historical_data(self):
        """Loads historical candles for all symbols and timeframes.
        Failures on individual symbols are logged, but the method
        always completes and logs success."""
        logger.info("Fetching historical data for symbols...")
        tasks = []
        for symbol in self.symbols:
            for tf in self.timeframes:
                tasks.append(self._init_symbol_tf(symbol, tf))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Log any errors per symbol/timeframe
        for (symbol, tf), result in zip(self._symbol_tf_pairs, results):
            if isinstance(result, Exception):
                logger.error(f"Failed loading {symbol} {tf}: {result}")

        logger.info("Historical data loaded successfully.")   # exactly as required

    async def _init_symbol_tf(self, symbol: str, tf: str):
        candles = await self.fetch_historical_klines(symbol, tf)
        async with self._lock:
            self.data[symbol].timeframes[tf] = candles[-500:]
            if candles:
                self.data[symbol].last_update[tf] = candles[-1].timestamp

    # ------------------------------------------------------------------
    # Callback Registration
    # ------------------------------------------------------------------
    def set_on_candle_callback(self, cb: Callable[[str, str, List[Candle]], Awaitable[None]]):
        self.callback = cb

    # ------------------------------------------------------------------
    # WebSocket with exponential backoff
    # ------------------------------------------------------------------
    async def start_websocket(self):
        if self._running:
            return
        self._running = True
        streams = [f"{s}@kline_{tf}" for s in self.symbols for tf in self.timeframes]
        url = f"{self.BASE_WS_URL}/stream?streams={'/'.join(streams)}"
        logger.info(f"Connecting to Binance WebSocket: {len(streams)} streams")
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self.is_connected = True
                    backoff = 1.0           # reset on successful connection
                    logger.info("WebSocket connected successfully.")
                    while self._running:
                        # Bound each receive so a stalled (zombie) connection
                        # becomes a timeout -> reconnect, instead of blocking
                        # forever with no error. See WS_STALE_TIMEOUT.
                        try:
                            message = await asyncio.wait_for(
                                ws.recv(), timeout=self.WS_STALE_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"No WebSocket data for {self.WS_STALE_TIMEOUT:.0f}s - "
                                f"connection stalled; forcing reconnect so candle data "
                                f"(and scanning) does not silently freeze."
                            )
                            break  # exit -> `async with` closes ws -> outer loop reconnects
                        try:
                            await self._handle_message(json.loads(message))
                        except Exception as e:
                            logger.error(f"Error in message handler: {e}")
            except asyncio.CancelledError:
                self.is_connected = False
                logger.info("WebSocket task cancelled.")
                break
            except Exception as e:
                self.is_connected = False
                logger.error(f"WebSocket disconnected: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)   # cap at 60 seconds
            else:
                self.is_connected = False

    async def _handle_message(self, msg: dict):
        try:
            stream = msg.get("stream", "")
            if "@kline_" not in stream:
                return
            symbol, _, interval_raw = stream.partition("@kline_")
            interval = interval_raw
            kline = msg["data"]["k"]
            if not kline["x"]:            # only closed candles
                return
            candle = Candle(
                timestamp=kline["t"],
                open=float(kline["o"]),
                high=float(kline["h"]),
                low=float(kline["l"]),
                close=float(kline["c"]),
                volume=float(kline["v"]),
                taker_buy_volume=float(kline.get("V", 0.0)),
            )
            async with self._lock:
                self.data[symbol].add_candle(interval, candle)
                candles = self.data[symbol].timeframes[interval].copy()
            if self.callback:
                try:
                    await self.callback(symbol, interval, candles)
                except Exception as e:
                    logger.error(f"Callback error for {symbol} {interval}: {e}")
        except Exception as e:
            logger.error(f"Unhandled error processing WebSocket message: {e}")

    # ------------------------------------------------------------------
    # Liquidation stream (public, free, no API key - real forced-close events)
    # ------------------------------------------------------------------
    async def start_liquidation_stream(self):
        """
        Connects to Binance's free public !forceOrder@arr stream and keeps a
        rolling LIQUIDATION_WINDOW_SECONDS window of real liquidation events
        per symbol. Same auto-reconnect-with-backoff shape as start_websocket.
        """
        if self._liq_running:
            return
        self._liq_running = True
        logger.info("Connecting to Binance liquidation stream (!forceOrder@arr)")
        backoff = 1.0
        while self._liq_running:
            try:
                async with websockets.connect(self.LIQUIDATION_WS_URL, ping_interval=20) as ws:
                    self._liq_ws = ws
                    backoff = 1.0
                    logger.info("Liquidation stream connected successfully.")
                    async for message in ws:
                        if not self._liq_running:
                            break
                        try:
                            await self._handle_liquidation_message(json.loads(message))
                        except Exception as e:
                            logger.error(f"Error in liquidation message handler: {e}")
            except asyncio.CancelledError:
                logger.info("Liquidation stream task cancelled.")
                break
            except Exception as e:
                logger.error(f"Liquidation stream disconnected: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _handle_liquidation_message(self, msg: dict):
        try:
            order = msg.get("o")
            if not order:
                return
            symbol = order["s"].lower()
            side = order["S"]  # "SELL" = a LONG was liquidated, "BUY" = a SHORT was liquidated
            qty = float(order.get("z") or order.get("q") or 0)
            price = float(order.get("ap") or order.get("p") or 0)
            notional = qty * price
            event_time = int(order.get("T") or msg.get("E") or 0)
            if notional <= 0:
                return

            async with self._liq_lock:
                self.recent_liquidations[symbol].append((event_time, notional, side))
                self._prune_liquidations(symbol)
        except Exception as e:
            logger.error(f"Unhandled error processing liquidation message: {e}")

    def _prune_liquidations(self, symbol: str):
        """Drop entries older than LIQUIDATION_WINDOW_SECONDS. Caller must hold _liq_lock."""
        cutoff = (time.time() - self.LIQUIDATION_WINDOW_SECONDS) * 1000
        entries = self.recent_liquidations.get(symbol, [])
        self.recent_liquidations[symbol] = [e for e in entries if e[0] >= cutoff]

    def get_liquidation_pressure(self, symbol: str) -> Dict[str, float]:
        """
        Real (not estimated) liquidation volume for `symbol` within the last
        LIQUIDATION_WINDOW_SECONDS: long_liquidated_usd is forced-sell
        pressure from longs getting stopped out, short_liquidated_usd is
        forced-buy pressure from shorts getting squeezed.
        """
        symbol = symbol.lower()
        cutoff = (time.time() - self.LIQUIDATION_WINDOW_SECONDS) * 1000
        entries = [e for e in self.recent_liquidations.get(symbol, []) if e[0] >= cutoff]
        long_liquidated = sum(notional for _, notional, side in entries if side == "SELL")
        short_liquidated = sum(notional for _, notional, side in entries if side == "BUY")
        return {
            "long_liquidated_usd": long_liquidated,
            "short_liquidated_usd": short_liquidated,
        }

    # ------------------------------------------------------------------
    # Funding Rate (public endpoint, no API key required)
    # ------------------------------------------------------------------
    async def get_funding_rate(self, symbol: str) -> float:
        """
        Current funding rate for `symbol`, used as an institutional-context
        signal in the strategy engine. Cached per FUNDING_RATE_CACHE_TTL
        since Binance only updates it every 8 hours. Returns 0.0 (neutral)
        if the fetch fails and nothing is cached yet - callers should treat
        that as "unknown" rather than a real reading of zero funding.
        """
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._funding_rate_cache.get(symbol)
        if cached and (now - cached[1]) < self.FUNDING_RATE_CACHE_TTL:
            return cached[0]

        try:
            client = await self._get_http_client()
            resp = await client.get(
                f"{self.REST_URL}/fapi/v1/premiumIndex", params={"symbol": symbol}
            )
            resp.raise_for_status()
            rate = float(resp.json()["lastFundingRate"])
            self._funding_rate_cache[symbol] = (rate, now)
            return rate
        except Exception as e:
            logger.warning(f"Could not fetch funding rate for {symbol}: {e}")
            return cached[0] if cached else 0.0

    async def get_daily_dataframe(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """
        Cached 1D OHLCV dataframe for `symbol`, used as the highest-timeframe
        "macro bias" input in UniversalScanner._load_htf_dataframes. See
        DAILY_TREND_CACHE_TTL for why this is a polled REST call instead of
        a live stream. Returns the last good cached frame (even if stale) on
        a fetch failure rather than an empty frame, so a transient API
        hiccup doesn't suddenly blank out the macro-bias signal for every
        symbol at once; returns an empty frame only if nothing has ever been
        fetched successfully yet.
        """
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._daily_df_cache.get(symbol)
        if cached and (now - cached[1]) < self.DAILY_TREND_CACHE_TTL:
            return cached[0]

        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            candles = await self.fetch_historical_klines(symbol, "1d", limit=limit)
            if not candles:
                return cached[0] if cached else empty
            df = pd.DataFrame([{
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            } for c in candles])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            self._daily_df_cache[symbol] = (df, now)
            return df
        except Exception as e:
            logger.warning(f"Could not fetch 1D candles for {symbol}: {e}")
            return cached[0] if cached else empty

    async def get_weekly_dataframe(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """
        Cached 1W OHLCV dataframe for `symbol` (2026-07-29, ICT migration
        final phase) - mirrors get_daily_dataframe() exactly (same cached-
        REST-call pattern, same stale-on-failure fallback, same empty-frame
        honest-degradation contract), just against Binance's generic "1w"
        kline interval via the already-generic fetch_historical_klines()
        instead of a new fetch path. Feeds HTFStructureEngine's "1w" slot
        (see app/smc/htf_structure_engine.py) - the heaviest weight in
        InstitutionalBiasEngine's top-down aggregation and in AIScorer's
        htf_alignment category.
        """
        symbol = symbol.upper()
        now = time.monotonic()
        cached = self._weekly_df_cache.get(symbol)
        if cached and (now - cached[1]) < self.WEEKLY_TREND_CACHE_TTL:
            return cached[0]

        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            candles = await self.fetch_historical_klines(symbol, "1w", limit=limit)
            if not candles:
                return cached[0] if cached else empty
            df = pd.DataFrame([{
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            } for c in candles])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            self._weekly_df_cache[symbol] = (df, now)
            return df
        except Exception as e:
            logger.warning(f"Could not fetch 1W candles for {symbol}: {e}")
            return cached[0] if cached else empty

    # ------------------------------------------------------------------
    # Data Access Helpers
    # ------------------------------------------------------------------
    def get_candles(self, symbol: str, interval: str) -> List[Candle]:
        symbol = symbol.lower()
        return self.data.get(symbol, SymbolData(symbol)).timeframes.get(interval, [])

    def get_dataframe(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        symbol = symbol.lower()
        candles = self.get_candles(symbol, interval)[-limit:]
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "taker_buy_volume"])
        df = pd.DataFrame([{
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "taker_buy_volume": c.taker_buy_volume,
        } for c in candles])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp")

    # ------------------------------------------------------------------
    # Graceful Shutdown
    # ------------------------------------------------------------------
    async def stop(self):
        self._running = False
        self.is_connected = False
        # close websocket
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        # close liquidation stream
        self._liq_running = False
        if self._liq_ws is not None:
            try:
                await self._liq_ws.close()
            except Exception:
                pass
        # close http client
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("BinanceDataManager stopped.")


# ---------------------------------------------------------------------------
# Liquidity filter (public endpoint, no API key required)
# ---------------------------------------------------------------------------
async def fetch_liquid_symbols(
    symbols: List[str], min_quote_volume_usdt: float = 20_000_000.0
) -> List[str]:
    """
    Drop symbols whose 24h USDT quote volume is below `min_quote_volume_usdt`
    from Binance's free public /fapi/v1/ticker/24hr endpoint. Thin/illiquid
    symbols carry real execution risk (spread, slippage) that a signal alone
    doesn't account for. Dynamic (checked at every startup) rather than a
    static hand-picked list, since liquidity conditions change over time.

    Falls back to the original, unfiltered list if the check itself fails
    (network issue, etc.) or would filter out everything - a broken filter
    should never silently stop the scanner from running.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{BinanceDataManager.REST_URL}/fapi/v1/ticker/24hr")
            resp.raise_for_status()
            data = resp.json()

        volumes = {row["symbol"]: float(row.get("quoteVolume", 0)) for row in data}
        liquid = [s for s in symbols if volumes.get(s, 0) >= min_quote_volume_usdt]
        dropped = [s for s in symbols if s not in liquid]

        if dropped:
            logger.info(
                f"Liquidity filter: dropped {len(dropped)} low-volume symbol(s) "
                f"(< ${min_quote_volume_usdt:,.0f} 24h quote volume): {dropped}"
            )

        return liquid or symbols
    except Exception as e:
        logger.warning(f"Liquidity filter skipped due to error, using unfiltered symbol list: {e}")
        return symbols
### END OF FILE ###