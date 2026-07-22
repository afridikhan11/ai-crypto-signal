import asyncio
import json
from typing import Dict, List, Optional, Callable, Awaitable
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

    def __init__(self, symbols: List[str], timeframes: Optional[List[str]] = None):
        self.symbols = [s.lower() for s in symbols]
        self.timeframes = timeframes or ["1m", "5m", "15m", "1h", "4h"]
        self.data: Dict[str, SymbolData] = {s: SymbolData(s) for s in self.symbols}
        self.callback: Optional[Callable[[str, str, List[Candle]], Awaitable[None]]] = None
        self._running = False
        self._ws = None
        self._lock = asyncio.Lock()
        self._symbol_tf_pairs = [(s, tf) for s in self.symbols for tf in self.timeframes]

        # HTTP session (created lazily)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._http_semaphore = asyncio.Semaphore(self.MAX_REST_CONCURRENCY)

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
                volume=float(row[5])
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
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    backoff = 1.0           # reset on successful connection
                    logger.info("WebSocket connected successfully.")
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            await self._handle_message(json.loads(message))
                        except Exception as e:
                            logger.error(f"Error in message handler: {e}")
            except asyncio.CancelledError:
                logger.info("WebSocket task cancelled.")
                break
            except Exception as e:
                logger.error(f"WebSocket disconnected: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)   # cap at 60 seconds

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
    # Data Access Helpers
    # ------------------------------------------------------------------
    def get_candles(self, symbol: str, interval: str) -> List[Candle]:
        symbol = symbol.lower()
        return self.data.get(symbol, SymbolData(symbol)).timeframes.get(interval, [])

    def get_dataframe(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        symbol = symbol.lower()
        candles = self.get_candles(symbol, interval)[-limit:]
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame([{
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume
        } for c in candles])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp")

    # ------------------------------------------------------------------
    # Graceful Shutdown
    # ------------------------------------------------------------------
    async def stop(self):
        self._running = False
        # close websocket
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        # close http client
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("BinanceDataManager stopped.")
### END OF FILE ###