#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------------------
# AI Crypto Signal System – Module 2 Setup Script
# Smart Money Concept Engine, Indicators, AI Scorer, Live Scanner
# (All reported bugs corrected)
# -------------------------------------------------------------------

echo "📦 Setting up Module 2: SMC Engine, AI Scorer, and Live Scanner (FIXED)..."

# 1. Create required directories
mkdir -p app/smc
mkdir -p app/indicators
mkdir -p app/ai
mkdir -p app/strategy
mkdir -p app/services
mkdir -p app/scheduler

# 2. Write __init__.py files
touch app/smc/__init__.py
touch app/indicators/__init__.py
touch app/ai/__init__.py
touch app/strategy/__init__.py
touch app/services/__init__.py
touch app/scheduler/__init__.py

# -------------------------------------------------------------------
# 3. Binance Live Data Service (FIXED: retry logic, gather exceptions, exponential backoff)
cat > app/services/binance_service.py << 'PYEOF'
import asyncio
import json
from typing import Dict, List, Optional, Set, Callable, Awaitable
from collections import defaultdict
from dataclasses import dataclass, field
import websockets
import httpx
from loguru import logger
import pandas as pd


@dataclass
class Candle:
    timestamp: int  # ms
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
            if len(self.timeframes[tf]) > 500:
                self.timeframes[tf].pop(0)
        self.last_update[tf] = candle.timestamp


class BinanceDataManager:
    BASE_WS_URL = "wss://fstream.binance.com/ws"
    REST_URL = "https://fapi.binance.com"

    def __init__(self, symbols: List[str], timeframes: List[str] = None):
        self.symbols = [s.lower() for s in symbols]
        self.timeframes = timeframes or ["1m", "5m", "15m", "1h", "4h"]
        self.data: Dict[str, SymbolData] = {s: SymbolData(s) for s in self.symbols}
        self.callback: Optional[Callable[[str, str, List[Candle]], Awaitable[None]]] = None
        self._running = False
        self._ws = None
        self._lock = asyncio.Lock()
        self._symbol_tf_pairs = [(s, tf) for s in self.symbols for tf in self.timeframes]

    async def fetch_historical_klines(self, symbol: str, interval: str, limit: int = 500) -> List[Candle]:
        """FIXED: Added retry logic for transient errors."""
        url = f"{self.REST_URL}/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        for attempt in range(3):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            except httpx.RequestError:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                raise
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

    async def initialise_historical_data(self):
        """FIXED: gather with return_exceptions to avoid crash on a single symbol failure."""
        logger.info("Fetching historical data for symbols...")
        tasks = []
        for symbol in self.symbols:
            for tf in self.timeframes:
                tasks.append(self._init_symbol_tf(symbol, tf))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result, (symbol, tf) in zip(results, self._symbol_tf_pairs):
            if isinstance(result, Exception):
                logger.error(f"Failed loading {symbol} {tf}: {result}")

    async def _init_symbol_tf(self, symbol: str, tf: str):
        candles = await self.fetch_historical_klines(symbol, tf)
        async with self._lock:
            self.data[symbol].timeframes[tf] = candles[-500:]
            if candles:
                self.data[symbol].last_update[tf] = candles[-1].timestamp

    def set_on_candle_callback(self, cb: Callable[[str, str, List[Candle]], Awaitable[None]]):
        self.callback = cb

    async def start_websocket(self):
        """FIXED: Exponential backoff on reconnect."""
        if self._running:
            return
        self._running = True
        streams = []
        for s in self.symbols:
            for tf in self.timeframes:
                streams.append(f"{s}@kline_{tf}")
        url = f"{self.BASE_WS_URL}/stream?streams={'/'.join(streams)}"
        logger.info(f"Connecting to Binance WebSocket: {len(streams)} streams")
        backoff = 1
        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    backoff = 1  # reset on successful connect
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(json.loads(message))
            except Exception as e:
                logger.error(f"WebSocket error: {e}, reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle_message(self, msg: dict):
        try:
            stream = msg.get("stream", "")
            if "@kline_" not in stream:
                return
            symbol, _, interval_raw = stream.partition("@kline_")
            interval = interval_raw
            kline = msg["data"]["k"]
            if not kline["x"]:
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
                await self.callback(symbol, interval, candles)
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")

    def get_candles(self, symbol: str, interval: str) -> List[Candle]:
        return self.data.get(symbol, SymbolData(symbol)).timeframes.get(interval, [])

    def get_dataframe(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
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

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
PYEOF

# -------------------------------------------------------------------
# 4. Market Structure
cat > app/smc/market_structure.py << 'PYEOF'
from typing import List, Tuple, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum


class SwingType(Enum):
    HH = "Higher High"
    HL = "Higher Low"
    LH = "Lower High"
    LL = "Lower Low"


@dataclass
class SwingPoint:
    timestamp: pd.Timestamp
    price: float
    type: SwingType
    index: int


@dataclass
class StructureBreak:
    timestamp: pd.Timestamp
    type: str  # "BOS" or "CHoCH"
    direction: str  # "bullish" or "bearish"
    broken_swing: SwingPoint
    level: float


class MarketStructure:
    def __init__(self, df: pd.DataFrame, pivot_window: int = 5):
        self.df = df
        self.pivot_window = pivot_window
        self.swing_highs: List[SwingPoint] = []
        self.swing_lows: List[SwingPoint] = []
        self._detect_swings()

    def _detect_swings(self):
        highs = self.df["high"].values
        lows = self.df["low"].values
        n = len(self.df)
        if n < 2 * self.pivot_window + 1:
            return
        for i in range(self.pivot_window, n - self.pivot_window):
            if highs[i] == max(highs[i - self.pivot_window:i + self.pivot_window + 1]):
                self.swing_highs.append(SwingPoint(
                    timestamp=self.df.index[i],
                    price=highs[i],
                    type=SwingType.HH,
                    index=i
                ))
            if lows[i] == min(lows[i - self.pivot_window:i + self.pivot_window + 1]):
                self.swing_lows.append(SwingPoint(
                    timestamp=self.df.index[i],
                    price=lows[i],
                    type=SwingType.HL,
                    index=i
                ))
        self._classify_swings()

    def _classify_swings(self):
        prev_high = None
        for sw in self.swing_highs:
            if prev_high is not None:
                sw.type = SwingType.HH if sw.price > prev_high.price else SwingType.LH
            else:
                sw.type = SwingType.HH
            prev_high = sw
        prev_low = None
        for sw in self.swing_lows:
            if prev_low is not None:
                sw.type = SwingType.HL if sw.price > prev_low.price else SwingType.LL
            else:
                sw.type = SwingType.HL
            prev_low = sw

    def detect_bos_choch(self) -> List[StructureBreak]:
        breaks = []
        if not self.swing_highs or not self.swing_lows:
            return breaks
        recent_highs = self.swing_highs[-3:]
        recent_lows = self.swing_lows[-3:]
        close = self.df["close"].iloc[-1]
        for sw in recent_highs:
            if sw.type == SwingType.LH and close > sw.price:
                breaks.append(StructureBreak(
                    timestamp=self.df.index[-1],
                    type="BOS",
                    direction="bullish",
                    broken_swing=sw,
                    level=sw.price
                ))
                break
        for sw in recent_lows:
            if sw.type == SwingType.HL and close < sw.price:
                breaks.append(StructureBreak(
                    timestamp=self.df.index[-1],
                    type="BOS",
                    direction="bearish",
                    broken_swing=sw,
                    level=sw.price
                ))
                break
        if len(self.swing_lows) >= 2 and len(self.swing_highs) >= 2:
            last_low = self.swing_lows[-1]
            prev_low = self.swing_lows[-2]
            last_high = self.swing_highs[-1]
            prev_high = self.swing_highs[-2]
            if (prev_low.type == SwingType.HL and last_low.type == SwingType.HL and
                    prev_high.type == SwingType.HH and last_high.type == SwingType.HH):
                if close < last_low.price:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[-1],
                        type="CHoCH",
                        direction="bearish",
                        broken_swing=last_low,
                        level=last_low.price
                    ))
            if (prev_high.type == SwingType.LH and last_high.type == SwingType.LH and
                    prev_low.type == SwingType.LL and last_low.type == SwingType.LL):
                if close > last_high.price:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[-1],
                        type="CHoCH",
                        direction="bullish",
                        broken_swing=last_high,
                        level=last_high.price
                    ))
        return breaks
PYEOF

# -------------------------------------------------------------------
# 5. Liquidity
cat > app/smc/liquidity.py << 'PYEOF'
from typing import List, Optional
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from .market_structure import SwingPoint, SwingType


class LiquidityType(Enum):
    BUYSIDE = "buyside"
    SELLSIDE = "sellside"


@dataclass
class LiquidityLevel:
    price: float
    type: LiquidityType
    swing_points: List[SwingPoint]
    swept: bool = False


class LiquidityDetector:
    def __init__(self, df: pd.DataFrame, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
                 tolerance: float = 0.0005):
        self.df = df
        self.swing_highs = swing_highs
        self.swing_lows = swing_lows
        self.tolerance = tolerance

    def detect_equal_highs(self) -> List[LiquidityLevel]:
        if not self.swing_highs:
            return []
        levels = []
        used = set()
        for i, sh1 in enumerate(self.swing_highs):
            if i in used:
                continue
            cluster = [sh1]
            for j, sh2 in enumerate(self.swing_highs[i+1:], start=i+1):
                if j in used:
                    continue
                avg_price = (sh1.price + sh2.price) / 2
                if abs(sh1.price - sh2.price) / avg_price <= self.tolerance:
                    cluster.append(sh2)
                    used.add(j)
            if len(cluster) >= 2:
                used.add(i)
                avg_price = sum(p.price for p in cluster) / len(cluster)
                levels.append(LiquidityLevel(
                    price=avg_price,
                    type=LiquidityType.BUYSIDE,
                    swing_points=cluster
                ))
        return levels

    def detect_equal_lows(self) -> List[LiquidityLevel]:
        if not self.swing_lows:
            return []
        levels = []
        used = set()
        for i, sl1 in enumerate(self.swing_lows):
            if i in used:
                continue
            cluster = [sl1]
            for j, sl2 in enumerate(self.swing_lows[i+1:], start=i+1):
                if j in used:
                    continue
                avg_price = (sl1.price + sl2.price) / 2
                if abs(sl1.price - sl2.price) / avg_price <= self.tolerance:
                    cluster.append(sl2)
                    used.add(j)
            if len(cluster) >= 2:
                used.add(i)
                avg_price = sum(p.price for p in cluster) / len(cluster)
                levels.append(LiquidityLevel(
                    price=avg_price,
                    type=LiquidityType.SELLSIDE,
                    swing_points=cluster
                ))
        return levels

    def detect_liquidity_sweeps(self, levels: List[LiquidityLevel]) -> List[LiquidityLevel]:
        for level in levels:
            if level.swept:
                continue
            recent_high = self.df["high"].iloc[-1]
            recent_low = self.df["low"].iloc[-1]
            if level.type == LiquidityType.BUYSIDE and recent_high > level.price:
                level.swept = True
            elif level.type == LiquidityType.SELLSIDE and recent_low < level.price:
                level.swept = True
        return levels
PYEOF

# -------------------------------------------------------------------
# 6. Order Blocks (FIXED: Check for broken OB)
cat > app/smc/order_blocks.py << 'PYEOF'
import pandas as pd
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class BlockType(Enum):
    BULLISH_OB = "bullish_ob"
    BEARISH_OB = "bearish_ob"
    BREAKER = "breaker"
    MITIGATION = "mitigation"


@dataclass
class OrderBlock:
    timestamp: pd.Timestamp
    high: float
    low: float
    type: BlockType
    mitigated: bool = False


class OrderBlockDetector:
    def __init__(self, df: pd.DataFrame, body_ratio_threshold: float = 0.6):
        self.df = df
        self.body_ratio_threshold = body_ratio_threshold

    def _is_momentum_candle(self, idx: int, direction: str) -> bool:
        if idx < 0 or idx >= len(self.df):
            return False
        row = self.df.iloc[idx]
        body = abs(row["close"] - row["open"])
        total_range = row["high"] - row["low"]
        if total_range == 0:
            return False
        if body / total_range < self.body_ratio_threshold:
            return False
        if direction == "up":
            return row["close"] > row["open"]
        else:
            return row["close"] < row["open"]

    def _is_ob_broken(self, ob: OrderBlock) -> bool:
        """FIXED: Check if subsequent price action has invalidated the OB."""
        ob_idx = self.df.index.get_loc(ob.timestamp)
        if ob_idx >= len(self.df) - 1:
            return False
        subsequent_closes = self.df.iloc[ob_idx+1:]["close"]
        if ob.type == BlockType.BULLISH_OB:
            # If price ever closed below the OB low, it's broken
            return any(subsequent_closes < ob.low)
        else:  # BEARISH_OB
            return any(subsequent_closes > ob.high)

    def find_bullish_order_block(self) -> Optional[OrderBlock]:
        for i in range(len(self.df)-2, 1, -1):
            if self._is_momentum_candle(i, "down"):
                if self._is_momentum_candle(i+1, "up"):
                    ob = OrderBlock(
                        timestamp=self.df.index[i],
                        high=self.df.iloc[i]["high"],
                        low=self.df.iloc[i]["low"],
                        type=BlockType.BULLISH_OB
                    )
                    if not self._is_ob_broken(ob):
                        return ob
        return None

    def find_bearish_order_block(self) -> Optional[OrderBlock]:
        for i in range(len(self.df)-2, 1, -1):
            if self._is_momentum_candle(i, "up"):
                if self._is_momentum_candle(i+1, "down"):
                    ob = OrderBlock(
                        timestamp=self.df.index[i],
                        high=self.df.iloc[i]["high"],
                        low=self.df.iloc[i]["low"],
                        type=BlockType.BEARISH_OB
                    )
                    if not self._is_ob_broken(ob):
                        return ob
        return None

    def check_mitigation(self, ob: OrderBlock, current_price: float) -> bool:
        return ob.low <= current_price <= ob.high

    def detect_breaker_block(self, ob: OrderBlock, current_price: float) -> bool:
        if ob.type == BlockType.BULLISH_OB:
            return current_price < ob.low
        else:
            return current_price > ob.high
PYEOF

# -------------------------------------------------------------------
# 7. Fair Value Gaps
cat > app/smc/fvg.py << 'PYEOF'
import pandas as pd
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum


class FVGType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class FairValueGap:
    timestamp: pd.Timestamp
    top: float
    bottom: float
    type: FVGType
    filled: bool = False


class FVGDetector:
    def __init__(self, df: pd.DataFrame, min_gap_ratio: float = 0.0005):
        self.df = df
        self.min_gap_ratio = min_gap_ratio

    def detect_fvg(self) -> List[FairValueGap]:
        fvgs = []
        for i in range(1, len(self.df)):
            prev = self.df.iloc[i-1]
            curr = self.df.iloc[i]
            if curr["low"] > prev["high"]:
                gap = curr["low"] - prev["high"]
                avg_price = (curr["low"] + prev["high"]) / 2
                if avg_price > 0 and gap / avg_price >= self.min_gap_ratio:
                    fvgs.append(FairValueGap(
                        timestamp=self.df.index[i],
                        top=curr["low"],
                        bottom=prev["high"],
                        type=FVGType.BULLISH
                    ))
            elif curr["high"] < prev["low"]:
                gap = prev["low"] - curr["high"]
                avg_price = (prev["low"] + curr["high"]) / 2
                if avg_price > 0 and gap / avg_price >= self.min_gap_ratio:
                    fvgs.append(FairValueGap(
                        timestamp=self.df.index[i],
                        top=prev["low"],
                        bottom=curr["high"],
                        type=FVGType.BEARISH
                    ))
        return fvgs

    def is_fvg_filled(self, fvg: FairValueGap, current_price: float) -> bool:
        if fvg.type == FVGType.BULLISH:
            return current_price <= fvg.top and current_price >= fvg.bottom
        else:
            return current_price >= fvg.bottom and current_price <= fvg.top

    def detect_inverse_fvg(self) -> List[FairValueGap]:
        fvgs = self.detect_fvg()
        current = self.df["close"].iloc[-1]
        inverse = []
        for f in fvgs:
            if self.is_fvg_filled(f, current):
                inverse.append(FairValueGap(
                    timestamp=f.timestamp,
                    top=f.top,
                    bottom=f.bottom,
                    type=FVGType.BULLISH if f.type == FVGType.BEARISH else FVGType.BEARISH,
                    filled=True
                ))
        return inverse
PYEOF

# -------------------------------------------------------------------
# 8. Supply & Demand
cat > app/smc/supply_demand.py << 'PYEOF'
import pandas as pd
from typing import Optional, Tuple


class SupplyDemandZones:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.range_high: Optional[float] = None
        self.range_low: Optional[float] = None

    def calculate_recent_range(self, lookback: int = 50):
        if len(self.df) < lookback:
            return
        recent = self.df.iloc[-lookback:]
        self.range_high = recent["high"].max()
        self.range_low = recent["low"].min()

    def get_zone(self, price: float) -> str:
        if self.range_high is None or self.range_low is None:
            return "unknown"
        range_size = self.range_high - self.range_low
        if range_size == 0:
            return "equilibrium"
        fib_0_5 = self.range_low + 0.5 * range_size
        fib_0_618 = self.range_low + 0.618 * range_size
        fib_0_79 = self.range_low + 0.79 * range_size
        if price >= fib_0_79:
            return "premium"
        elif price <= fib_0_5:
            return "discount"
        else:
            return "equilibrium"
PYEOF

# -------------------------------------------------------------------
# 9. Confirmation Indicators (FIXED: NaN handling in get_latest)
cat > app/indicators/confirmation.py << 'PYEOF'
import pandas as pd
import numpy as np
from typing import Tuple, Optional
from ta.trend import EMAIndicator, MACD, ADXIndicator, SupertrendIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from ta.volume import volume_weighted_average_price as vwap_fn


class ConfirmationIndicators:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._compute()

    def _compute(self):
        self.ema20 = EMAIndicator(close=self.df["close"], window=20).ema_indicator()
        self.ema50 = EMAIndicator(close=self.df["close"], window=50).ema_indicator()
        self.ema200 = EMAIndicator(close=self.df["close"], window=200).ema_indicator()

        self.vwap = vwap_fn(self.df["high"], self.df["low"], self.df["close"], self.df["volume"])

        self.volume_sma = self.df["volume"].rolling(window=20).mean()
        self.volume_spike = self.df["volume"] > (self.volume_sma * 1.5)

        atr_ind = AverageTrueRange(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=14)
        self.atr = atr_ind.average_true_range()

        self.rsi = RSIIndicator(close=self.df["close"], window=14).rsi()

        macd = MACD(close=self.df["close"], window_slow=26, window_fast=12, window_sign=9)
        self.macd_line = macd.macd()
        self.macd_signal = macd.macd_signal()
        self.macd_hist = macd.macd_diff()

        adx_ind = ADXIndicator(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=14)
        self.adx = adx_ind.adx()
        self.plus_di = adx_ind.adx_pos()
        self.minus_di = adx_ind.adx_neg()

        st = SupertrendIndicator(high=self.df["high"], low=self.df["low"], close=self.df["close"], window=7, multiplier=3.0)
        self.supertrend = st.supertrend()
        self.supertrend_direction = st.supertrend_direction()

    def get_latest(self) -> Optional[dict]:
        """FIXED: Return None if any indicator value is NaN."""
        idx = -1
        vals = {
            "ema20": self.ema20.iloc[idx],
            "ema50": self.ema50.iloc[idx],
            "ema200": self.ema200.iloc[idx],
            "vwap": self.vwap.iloc[idx],
            "atr": self.atr.iloc[idx],
            "rsi": self.rsi.iloc[idx],
            "macd_line": self.macd_line.iloc[idx],
            "macd_signal": self.macd_signal.iloc[idx],
            "macd_hist": self.macd_hist.iloc[idx],
            "adx": self.adx.iloc[idx],
            "plus_di": self.plus_di.iloc[idx],
            "minus_di": self.minus_di.iloc[idx],
            "supertrend": self.supertrend.iloc[idx],
            "supertrend_dir": self.supertrend_direction.iloc[idx],
            "volume_spike": bool(self.volume_spike.iloc[idx]) if not pd.isna(self.volume_spike.iloc[idx]) else False,
        }
        if any(pd.isna(v) for k, v in vals.items() if k != "volume_spike"):
            return None
        return vals
PYEOF

# -------------------------------------------------------------------
# 10. AI Scorer
cat > app/ai/scorer.py << 'PYEOF'
from typing import Dict, Any, List, Optional, Tuple
import numpy as np


class AIScorer:
    def __init__(self):
        self.weights = {
            "market_structure": 0.2,
            "liquidity_sweep": 0.15,
            "order_block_quality": 0.2,
            "fvg_presence": 0.1,
            "supply_demand_zone": 0.1,
            "confirmation_alignment": 0.15,
            "institutional_filters": 0.1,
        }

    def assess(self, features: Dict[str, Any]) -> Tuple[int, str]:
        scores = {}
        reasons = []

        ms = features.get("market_structure", {})
        bos_choch = ms.get("bos_choch", [])
        if bos_choch:
            scores["market_structure"] = 90
            reasons.append(f"Structure break ({bos_choch[0].type})")
        else:
            scores["market_structure"] = 40
            reasons.append("No clear BOS/CHoCH")

        liq = features.get("liquidity", {})
        swept_levels = [l for l in liq.get("levels", []) if l.swept]
        if swept_levels:
            scores["liquidity_sweep"] = 85
            reasons.append("Liquidity swept")
        else:
            scores["liquidity_sweep"] = 50

        ob = features.get("order_block", {})
        if ob:
            if ob.get("mitigated", False):
                scores["order_block_quality"] = 80
                reasons.append("OB mitigation")
            elif ob.get("type") in ["BULLISH_OB", "BEARISH_OB"]:
                scores["order_block_quality"] = 70
                reasons.append("OB present")
            else:
                scores["order_block_quality"] = 30
        else:
            scores["order_block_quality"] = 20

        fvg = features.get("fvg", [])
        if fvg and any(not f.filled for f in fvg):
            scores["fvg_presence"] = 85
            reasons.append("Unfilled FVG")
        else:
            scores["fvg_presence"] = 40

        zone = features.get("supply_demand_zone", "unknown")
        if zone == "discount" and features.get("direction") == "LONG":
            scores["supply_demand_zone"] = 90
            reasons.append("In discount zone")
        elif zone == "premium" and features.get("direction") == "SHORT":
            scores["supply_demand_zone"] = 90
            reasons.append("In premium zone")
        elif zone == "equilibrium":
            scores["supply_demand_zone"] = 60
        else:
            scores["supply_demand_zone"] = 30

        conf = features.get("confirmation", {})
        alignment_score = 50
        if features["direction"] == "LONG":
            if conf.get("ema20", 0) > conf.get("ema50", 0) and conf.get("rsi", 50) > 50 and conf.get("adx", 0) > 20:
                alignment_score = 85
                reasons.append("Bullish EMA/RSI/ADX")
            elif conf.get("rsi", 50) < 30:
                alignment_score += 10
        else:
            if conf.get("ema20", 0) < conf.get("ema50", 0) and conf.get("rsi", 50) < 50 and conf.get("adx", 0) > 20:
                alignment_score = 85
                reasons.append("Bearish EMA/RSI/ADX")
            elif conf.get("rsi", 50) > 70:
                alignment_score += 10
        if conf.get("volume_spike", False):
            alignment_score += 5
        scores["confirmation_alignment"] = min(alignment_score, 100)

        inst = features.get("institutional", {})
        inst_score = 70
        btc_trend = inst.get("btc_trend", "neutral")
        if (features["direction"] == "LONG" and btc_trend == "up") or (features["direction"] == "SHORT" and btc_trend == "down"):
            inst_score = 85
            reasons.append("BTC trend aligned")
        elif btc_trend == "neutral":
            inst_score = 70
        else:
            inst_score = 40
            reasons.append("BTC trend opposing")

        funding = inst.get("funding_rate", 0)
        if features["direction"] == "LONG" and funding < -0.001:
            inst_score -= 10
        elif features["direction"] == "SHORT" and funding > 0.001:
            inst_score -= 10

        volatility = inst.get("volatility", "normal")
        if volatility == "high":
            inst_score -= 20
            reasons.append("High volatility (choppy)")
        scores["institutional_filters"] = max(inst_score, 0)

        total = sum(self.weights[k] * scores[k] for k in self.weights)
        confidence = int(round(total))
        reason_str = "; ".join(reasons)
        return confidence, reason_str
PYEOF

# -------------------------------------------------------------------
# 11. Signal Generator (FIXED: ATR NaN guard; note MTF gap)
cat > app/strategy/signal_generator.py << 'PYEOF'
from typing import Dict, Any, Optional, Tuple, List
import pandas as pd
from loguru import logger

from app.smc.market_structure import MarketStructure, StructureBreak
from app.smc.liquidity import LiquidityDetector, LiquidityLevel
from app.smc.order_blocks import OrderBlockDetector, OrderBlock
from app.smc.fvg import FVGDetector, FairValueGap
from app.smc.supply_demand import SupplyDemandZones
from app.indicators.confirmation import ConfirmationIndicators
from app.ai.scorer import AIScorer
from app.models.signal import Direction


class SignalGenerator:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ai_scorer = AIScorer()

    def generate(self, df: pd.DataFrame, btc_trend: str, funding_rate: float,
                 volatility: str) -> Optional[Dict[str, Any]]:
        if len(df) < 50:
            return None

        ms = MarketStructure(df)
        breaks = ms.detect_bos_choch()
        if not breaks:
            return None

        latest_break = breaks[-1]
        direction = Direction.LONG if latest_break.direction == "bullish" else Direction.SHORT

        liq_detector = LiquidityDetector(df, ms.swing_highs, ms.swing_lows)
        equal_highs = liq_detector.detect_equal_highs()
        equal_lows = liq_detector.detect_equal_lows()
        all_levels = equal_highs + equal_lows
        swept = liq_detector.detect_liquidity_sweeps(all_levels)

        ob_detector = OrderBlockDetector(df)
        if direction == Direction.LONG:
            ob = ob_detector.find_bullish_order_block()
        else:
            ob = ob_detector.find_bearish_order_block()
        ob_dict = None
        if ob:
            current_price = df["close"].iloc[-1]
            mitigated = ob_detector.check_mitigation(ob, current_price)
            ob_dict = {
                "type": ob.type.value,
                "high": ob.high,
                "low": ob.low,
                "mitigated": mitigated,
            }

        fvg_detector = FVGDetector(df)
        fvgs = fvg_detector.detect_fvg()
        relevant_fvgs = [f for f in fvgs if not f.filled]

        sd = SupplyDemandZones(df)
        sd.calculate_recent_range()
        current_price = df["close"].iloc[-1]
        zone = sd.get_zone(current_price)

        conf = ConfirmationIndicators(df)
        conf_latest = conf.get_latest()
        if conf_latest is None:
            logger.warning(f"{self.symbol}: Confirmation indicators contain NaN, skipping.")
            return None

        inst = {
            "btc_trend": btc_trend,
            "funding_rate": funding_rate,
            "volatility": volatility,
        }

        features = {
            "direction": direction.value,
            "market_structure": {"bos_choch": breaks},
            "liquidity": {"levels": swept},
            "order_block": ob_dict,
            "fvg": relevant_fvgs,
            "supply_demand_zone": zone,
            "confirmation": conf_latest,
            "institutional": inst,
        }

        confidence, reason = self.ai_scorer.assess(features)

        if confidence < 85:
            logger.debug(f"{self.symbol}: Confidence {confidence} < 85, no signal.")
            return None

        atr = conf_latest["atr"]
        # FIXED: Guard against NaN ATR
        if pd.isna(atr) or atr <= 0:
            logger.warning(f"{self.symbol}: Invalid ATR value, skipping signal.")
            return None

        if direction == Direction.LONG:
            swing_low = min(sw.price for sw in ms.swing_lows[-3:]) if ms.swing_lows else current_price - 2*atr
            stop_loss = min(swing_low, current_price - 1.5 * atr)
            tp1 = current_price + 3 * atr
            tp2 = current_price + 5 * atr
            tp3 = current_price + 8 * atr
        else:
            swing_high = max(sw.price for sw in ms.swing_highs[-3:]) if ms.swing_highs else current_price + 2*atr
            stop_loss = max(swing_high, current_price + 1.5 * atr)
            tp1 = current_price - 3 * atr
            tp2 = current_price - 5 * atr
            tp3 = current_price - 8 * atr

        risk = abs(current_price - stop_loss)
        reward = abs(tp1 - current_price)
        rr = round(reward / risk, 2) if risk > 0 else 0

        signal = {
            "symbol": self.symbol,
            "direction": direction.value,
            "entry": round(current_price, 8),
            "stop_loss": round(stop_loss, 8),
            "tp1": round(tp1, 8),
            "tp2": round(tp2, 8),
            "tp3": round(tp3, 8),
            "risk_reward": rr,
            "confidence": confidence,
            "reason": reason,
        }
        # TODO: Multi-timeframe confirmation not yet implemented; all analysis uses 1m data.
        # A future version must incorporate 5m/15m/1h/4h structure alignment.
        return signal
PYEOF

# -------------------------------------------------------------------
# 12. Scanner (FIXED: Redis direct client, per-symbol lock, placeholder real data note)
cat > app/scheduler/scanner.py << 'PYEOF'
import asyncio
from typing import List, Dict, Any
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.binance_service import BinanceDataManager
from app.strategy.signal_generator import SignalGenerator
from app.models.signal import Signal, Direction, SignalStatus
from app.models.coin import Coin
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client  # FIXED: use global redis client directly
import json


class CryptoScanner:
    def __init__(self, symbols: List[str]):
        self.data_manager = BinanceDataManager(symbols)
        self.generators = {s: SignalGenerator(s) for s in symbols}
        self._running = False
        self._symbol_locks = {s: asyncio.Lock() for s in symbols}  # FIXED: per-symbol lock

    async def start(self):
        await self.data_manager.initialise_historical_data()
        self.data_manager.set_on_candle_callback(self.on_new_candle)
        self._running = True
        asyncio.create_task(self.data_manager.start_websocket())
        logger.info("Crypto scanner started.")

    async def on_new_candle(self, symbol: str, interval: str, candles):
        if interval != "1m":
            return
        # FIXED: serialize analysis per symbol to avoid duplicate signals
        async with self._symbol_locks[symbol]:
            await self.analyze_symbol(symbol)

    async def analyze_symbol(self, symbol: str):
        try:
            df = self.data_manager.get_dataframe(symbol, "1m", limit=200)
            if df.empty:
                return
            # TODO: Replace placeholders with real-time data from Binance endpoints
            # (BTC trend from BTCUSDT klines, funding rate from /fapi/v1/fundingRate, volatility from ATR)
            btc_trend = "up"   # placeholder
            funding_rate = 0.0001
            volatility = "normal"

            generator = self.generators[symbol]
            signal_data = generator.generate(df, btc_trend, funding_rate, volatility)
            if signal_data:
                await self.save_signal(signal_data)
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")

    async def save_signal(self, data: Dict[str, Any]):
        async with AsyncSessionLocal() as session:
            stmt = select(Coin).where(Coin.symbol == data["symbol"].upper())
            result = await session.execute(stmt)
            coin = result.scalar_one_or_none()
            if not coin:
                coin = Coin(symbol=data["symbol"].upper())
                session.add(coin)
                await session.flush()

            stmt_active = select(Signal).where(
                Signal.coin_id == coin.id,
                Signal.status == SignalStatus.ACTIVE
            )
            active_result = await session.execute(stmt_active)
            if active_result.scalar_one_or_none():
                logger.debug(f"Active signal already exists for {data['symbol']}, skipping.")
                return

            signal = Signal(
                coin_id=coin.id,
                direction=Direction[data["direction"]],
                entry_price=data["entry"],
                stop_loss=data["stop_loss"],
                take_profit_1=data["tp1"],
                take_profit_2=data["tp2"],
                take_profit_3=data["tp3"],
                risk_reward=data["risk_reward"],
                confidence=data["confidence"],
                reason=data["reason"],
                timeframe="1m",
                ai_model_version="1.0.0",
            )
            session.add(signal)
            await session.commit()
            logger.info(f"New signal for {data['symbol']}: {data['direction']} @ {data['entry']}")

            # FIXED: use redis_client directly
            await redis_client.publish("new_signal", json.dumps(data, default=str))

    async def stop(self):
        self._running = False
        await self.data_manager.stop()
PYEOF

# -------------------------------------------------------------------
# 13. Updated main.py (unchanged)
cat > app/main.py << 'PYEOF'
from fastapi import FastAPI
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import logger
from app.models.base import Base
from app.core.database import engine
import asyncio

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
async def on_startup():
    logger.info("Starting application...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Start crypto scanner for top symbols
    from app.scheduler.scanner import CryptoScanner
    TOP_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "LINKUSDT", "UNIUSDT",
        "ATOMUSDT", "LTCUSDT", "ETCUSDT", "FILUSDT", "OPUSDT", "ARBUSDT",
        "APTUSDT", "NEARUSDT", "VETUSDT", "ICPUSDT", "GRTUSDT", "SANDUSDT",
        "MANAUSDT", "EGLDUSDT", "AAVEUSDT", "ALGOUSDT", "THETAUSDT", "FTMUSDT",
        "FLOWUSDT", "XTZUSDT", "ENJUSDT", "CHZUSDT", "BATUSDT", "ZILUSDT",
        "ONEUSDT", "KSMUSDT", "COMPUSDT", "CRVUSDT", "SNXUSDT", "SUSHIUSDT",
        "YFIUSDT", "ZRXUSDT", "LRCUSDT", "RENUSDT", "BALUSDT", "OCEANUSDT",
        "COTIUSDT", "CELOUSDT"
    ]
    app.state.scanner = CryptoScanner(TOP_SYMBOLS)
    asyncio.create_task(app.state.scanner.start())
    logger.info("Scanner started.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down...")
    if hasattr(app.state, "scanner"):
        await app.state.scanner.stop()
    await engine.dispose()
PYEOF

echo ""
echo "✅ Corrected Module 2 setup complete!"
echo "   - Fixed: HTTP retry, WS backoff, NaN handling, broken OB check"
echo "   - Fixed: Redis publisher, per-symbol lock, ATR NaN guard"
echo "   - Multi-timeframe confirmation still pending (TODO in signal generator)"
echo ""
echo "ℹ️  Run 'docker compose restart app' to apply changes."