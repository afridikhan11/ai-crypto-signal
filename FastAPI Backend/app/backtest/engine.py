"""
Backtesting engine.

Replays historical candles through the EXACT SAME SignalGenerator/AIScorer/
SMC pipeline used live (app/strategy/signal_generator.py), simulating SL/TP
outcomes candle-by-candle. This exists so the strategy can be validated -
win rate, average realized R, how it behaves across confidence buckets -
against weeks/months of past price action immediately, instead of waiting
for enough live closed trades to accumulate.

Known, documented simplifications vs. live trading:
  - funding_rate is passed as 0.0 (neutral) for every step. Binance has no
    free "funding rate as of historical timestamp T" series that's
    practical to paginate for every candle of a multi-week backtest.
  - liquidation_pressure and fundamentals (OI trend, long/short ratio,
    fear/greed, BTC dominance) are passed as empty/None for every step -
    these are live-only free data sources with no historical REST
    endpoint. AIScorer already treats missing values as neutral (see
    institutional_filters and fundamental_context in app/ai/scorer.py), so
    this never crashes or silently biases direction - it just means a
    backtest run leans on the SMC structure/liquidity-sweep/order-block/
    FVG/zone/confirmation categories more heavily than a live signal would.
  - btc_trend and htf_trend_1h/4h/1d/entry_trend_5m ARE real, computed from
    real historical 1h/4h/1d/5m candles using the exact same EMA20/50 logic
    as live (see app/analysis/trend.py), evaluated as-of each historical
    timestamp so there is no lookahead bias.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import replace as _dataclass_replace
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd
from loguru import logger

from app.legacy.trend import ema_trend_from_df
from app.services.binance_service import BinanceDataManager, Candle
from app.strategy.signal_generator import SignalGenerator

# Same "how far apart do EMA20/EMA50 need to be to call a real trend"
# threshold used live (CryptoScanner.BTC_TREND_MIN_SEPARATION).
TREND_MIN_SEPARATION = 0.001

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

WIN_OUTCOMES = ("TP_HIT",)

# ICT pending-entry fill window - imported rather than redeclared so the
# backtest's fill simulation and the live monitor's expiry can never drift.
from app.core.constants import PENDING_ENTRY_EXPIRY_CANDLES  # noqa: E402


async def fetch_klines_range(
    client: httpx.AsyncClient, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1500
) -> List[Candle]:
    """
    Paginates Binance's public /fapi/v1/klines endpoint across [start_ms,
    end_ms) since a single request caps out at 1500 candles - a multi-week
    backtest on a low timeframe needs several requests. No API key needed
    (same public endpoint used by fetch_liquid_symbols).
    """
    candles: List[Candle] = []
    cursor = start_ms
    interval_ms = INTERVAL_MS.get(interval, 60_000)

    while cursor < end_ms:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": limit,
        }
        resp = await client.get(f"{BinanceDataManager.REST_URL}/fapi/v1/klines", params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break

        for row in rows:
            candles.append(Candle(
                timestamp=row[0],
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                taker_buy_volume=float(row[9]) if len(row) > 9 else 0.0,
            ))

        next_cursor = rows[-1][0] + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if len(rows) < limit:
            break

        await asyncio.sleep(0.15)  # stay well clear of rate limits

    return candles


def _candles_to_df(candles: List[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "taker_buy_volume"])
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
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    return df.set_index("timestamp")


class BacktestEngine:
    # Matches production's CryptoScanner.analyze_symbol, which always reads
    # a trailing 500-candle window (real EMA200 warm-up, not just its first
    # non-NaN value) - the backtest mirrors that exactly at every step.
    WARMUP_CANDLES = 500

    # Same "as of now" trailing window live's _get_ema_trend uses (limit=100).
    HTF_TREND_WINDOW = 100

    # How many forward candles to search for SL/TP resolution before giving
    # up and marking a trade UNRESOLVED (roughly 5 days on a 15m chart).
    MAX_RESOLUTION_CANDLES = 500

    def __init__(self, symbol: str, timeframe: str = "15m",
                 pending_expiry_candles: int = PENDING_ENTRY_EXPIRY_CANDLES):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        # How many candles a pending limit entry has to fill before it EXPIRES.
        # Defaults to the live constant (PENDING_ENTRY_EXPIRY_CANDLES) so a
        # normal backtest mirrors live exactly; a sweep can override it to
        # measure the fill-rate vs win-rate trade-off before any live change.
        self.pending_expiry_candles = pending_expiry_candles

    async def _load_all_series(
        self, days: int
    ) -> Tuple[List[Candle], List[Candle], List[Candle], List[Candle], List[Candle], List[Candle]]:
        interval_ms = INTERVAL_MS.get(self.timeframe, 900_000)
        end_ms = int(time.time() * 1000)
        # Extra warmup so the very first scan point already has a full
        # WARMUP_CANDLES-candle lookback, matching production.
        warmup_ms = self.WARMUP_CANDLES * interval_ms
        start_ms = end_ms - days * 86_400_000 - warmup_ms

        # 1D needs its own, much longer lookback than the other series - a
        # HTF_TREND_WINDOW-candle (100) trailing EMA20/50 read on 1D means
        # ~100 days of history BEFORE the backtest's own start, which the
        # shared `start_ms` above (sized off the PRIMARY timeframe's warmup,
        # e.g. ~5 days for a 15m primary) doesn't come close to covering.
        daily_warmup_ms = 100 * INTERVAL_MS["1d"]
        daily_start_ms = start_ms - daily_warmup_ms

        async with httpx.AsyncClient(timeout=20.0) as client:
            primary = await fetch_klines_range(client, self.symbol, self.timeframe, start_ms, end_ms)
            htf1h = await fetch_klines_range(client, self.symbol, "1h", start_ms, end_ms)
            htf4h = await fetch_klines_range(client, self.symbol, "4h", start_ms, end_ms)
            htf1d = await fetch_klines_range(client, self.symbol, "1d", daily_start_ms, end_ms)
            # "5M precision entry" - matches CryptoScanner's entry_trend_5m
            # (see AIScorer's multi-timeframe alignment). Same start_ms as
            # the other short series is plenty of buffer: HTF_TREND_WINDOW
            # (100 candles) on 5m is only ~8.3h, far inside the primary
            # timeframe's own warmup window in every realistic case.
            entry5m = await fetch_klines_range(client, self.symbol, "5m", start_ms, end_ms)
            btc1h = htf1h if self.symbol == "BTCUSDT" else await fetch_klines_range(
                client, "BTCUSDT", "1h", start_ms, end_ms
            )

        return primary, htf1h, htf4h, btc1h, htf1d, entry5m

    @staticmethod
    def _trend_asof(htf_df: pd.DataFrame, ts, window: int) -> str:
        """Real EMA20/50 trend using only candles closed at-or-before `ts`."""
        if htf_df.empty:
            return "neutral"
        pos = htf_df.index.searchsorted(ts, side="right")
        if pos <= 0:
            return "neutral"
        start = max(0, pos - window)
        return ema_trend_from_df(htf_df.iloc[start:pos], TREND_MIN_SEPARATION)

    def _simulate_fill(
        self, df: pd.DataFrame, start_index: int, signal_data: Dict[str, Any]
    ) -> Optional[int]:
        """
        The index of the candle on which this entry actually filled, or None
        if it never did.

        Mirrors `SignalMonitor._pending_outcome()` exactly, so a backtested
        fill and a live fill mean the same thing:
          - invalidation is checked BEFORE the fill on each candle, so a
            candle that spans both the entry zone and the stop counts as
            "never entered" rather than as an instantly-stopped trade;
          - a fill is price trading anywhere INSIDE the entry zone, not only
            an exact touch of the midpoint;
          - the window is PENDING_ENTRY_EXPIRY_CANDLES, the same constant
            the live monitor uses.

        Returns `start_index` unchanged for a market-type entry, where the
        trade is filled at the live price by definition.
        """
        if signal_data.get("entry_type") in (None, "market"):
            return start_index

        entry = signal_data["entry"]
        sl = signal_data["stop_loss"]
        zone_top = signal_data.get("entry_zone_top")
        zone_bottom = signal_data.get("entry_zone_bottom")
        if zone_top is None or zone_bottom is None or zone_top < zone_bottom:
            zone_top = zone_bottom = entry

        is_long = signal_data["direction"] == "LONG"
        end_index = min(len(df), start_index + self.pending_expiry_candles)
        for j in range(start_index, end_index):
            high = float(df["high"].iloc[j])
            low = float(df["low"].iloc[j])
            if is_long:
                if low <= sl:
                    return None          # invalidated before entry
                if low <= zone_top:
                    return j             # traded into the zone
            else:
                if high >= sl:
                    return None
                if high >= zone_bottom:
                    return j
        return None                       # window elapsed unfilled

    def _resolve_trade(
        self, df: pd.DataFrame, start_index: int, signal_data: Dict[str, Any]
    ) -> Tuple[str, Optional[Any], Optional[int], float]:
        """
        Walks forward from `start_index` checking each candle's high/low
        against SL/TP, matching app/scheduler/signal_monitor.py's
        SignalMonitor._resolve_status so live and backtested outcomes are
        judged the same way.
        """
        direction = signal_data["direction"]
        sl = signal_data["stop_loss"]
        tp = signal_data["take_profit"]
        entry = signal_data["entry"]
        risk = abs(entry - sl)

        # ------------------------------------------------------------------
        # ICT Pending Limit Entry (2026-07-30) - FILL SIMULATION.
        #
        # Under entry_mode "ict_pending" the entry is a retracement level
        # price has NOT reached yet, so the trade cannot be assumed filled.
        # Walking straight to TP/SL from here (which is what this method did
        # before) would credit fills that never happened and inflate every
        # result. `_simulate_fill()` finds the candle where price actually
        # traded into the entry zone - or reports that it never did.
        #
        # Under entry_mode "market" the entry IS the live price, so the
        # helper returns `start_index` immediately and resolution proceeds
        # exactly as it always has. Same code path, both modes, no drift
        # between live and backtested behaviour.
        # ------------------------------------------------------------------
        fill_index = self._simulate_fill(df, start_index, signal_data)
        if fill_index is None:
            return "EXPIRED", None, None, 0.0
        start_index = fill_index

        end_index = min(len(df), start_index + self.MAX_RESOLUTION_CANDLES)
        for j in range(start_index, end_index):
            high = float(df["high"].iloc[j])
            low = float(df["low"].iloc[j])
            ts = df.index[j]

            if direction == "LONG":
                if high >= tp:
                    return "TP_HIT", ts, j, round((tp - entry) / risk, 2) if risk else 0.0
                if low <= sl:
                    return "STOPPED", ts, j, -1.0
            else:
                if low <= tp:
                    return "TP_HIT", ts, j, round((entry - tp) / risk, 2) if risk else 0.0
                if high >= sl:
                    return "STOPPED", ts, j, -1.0

        return "UNRESOLVED", None, None, 0.0

    @staticmethod
    def _summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(trades)
        wins = sum(1 for t in trades if t["outcome"] in WIN_OUTCOMES)
        losses = sum(1 for t in trades if t["outcome"] == "STOPPED")
        unresolved = sum(1 for t in trades if t["outcome"] == "UNRESOLVED")
        # ICT pending entries that price never reached. Reported separately
        # and EXCLUDED from win_rate and realized RR - a setup that was never
        # entered is neither a win nor a loss, and folding it into either
        # would misrepresent the strategy's real performance.
        expired = sum(1 for t in trades if t["outcome"] == "EXPIRED")
        resolved = wins + losses
        win_rate = round(wins / resolved * 100, 2) if resolved else 0.0
        fill_rate = round((total - expired) / total * 100, 2) if total else 0.0

        realized = [
            t["realized_rr"] for t in trades
            if t["outcome"] not in ("UNRESOLVED", "EXPIRED")
        ]
        avg_rr = round(sum(realized) / len(realized), 2) if realized else 0.0

        by_bucket: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "wins": 0})
        for t in trades:
            floor = (t["confidence"] // 5) * 5
            bucket = f"{floor}-{floor + 4}"
            by_bucket[bucket]["total"] += 1
            if t["outcome"] in WIN_OUTCOMES:
                by_bucket[bucket]["wins"] += 1

        return {
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "unresolved": unresolved,
            "expired": expired,
            "win_rate": win_rate,
            "fill_rate": fill_rate,
            "avg_realized_rr": avg_rr,
            "by_confidence_bucket": dict(by_bucket),
        }

    async def run(self, days: int = 30, min_confidence: Optional[int] = None) -> Dict[str, Any]:
        primary_candles, htf1h_candles, htf4h_candles, btc1h_candles, htf1d_candles, entry5m_candles = (
            await self._load_all_series(days)
        )

        primary_df = _candles_to_df(primary_candles)
        htf1h_df = _candles_to_df(htf1h_candles)
        htf4h_df = _candles_to_df(htf4h_candles)
        btc1h_df = _candles_to_df(btc1h_candles)
        htf1d_df = _candles_to_df(htf1d_candles)
        entry5m_df = _candles_to_df(entry5m_candles)

        if len(primary_df) < self.WARMUP_CANDLES + 10:
            return {
                "error": (
                    f"Not enough historical {self.timeframe} candles returned for "
                    f"{self.symbol} ({len(primary_df)}) - try a shorter lookback "
                    f"or double check the symbol."
                )
            }

        generator = SignalGenerator(self.symbol)
        if min_confidence is not None:
            # BUG FIX (2026-07-26): SignalGenerator no longer has a
            # MIN_CONFIDENCE class attribute - the threshold now lives on
            # generator.profile.min_confidence (see CalibrationProfile /
            # get_profile_for_symbol), auto-selected per asset class. The
            # old `generator.MIN_CONFIDENCE = min_confidence` line here
            # would have silently done nothing once that refactor landed
            # (generate() reads self.profile.min_confidence, never
            # self.MIN_CONFIDENCE) - this sweep override would have quietly
            # stopped working. dataclasses.replace() builds a new profile
            # with just this field overridden (CalibrationProfile is
            # frozen/immutable) and swaps it onto this ONE generator
            # instance only - does NOT touch the shared profile object used
            # by the live scanner's generators for this or any other symbol.
            generator.profile = _dataclass_replace(generator.profile, min_confidence=min_confidence)

        trades: List[Dict[str, Any]] = []
        n = len(primary_df)
        i = self.WARMUP_CANDLES - 1
        start_i = i
        total_steps = max(n - start_i, 1)
        # Progress logging every ~10% - a multi-symbol sweep can take
        # several minutes per symbol even after optimization, and a long
        # silent gap in the logs is easy to mistake for a hang.
        log_every = max(total_steps // 10, 200)

        while i < n:
            if (i - start_i) % log_every == 0:
                pct = round((i - start_i) / total_steps * 100)
                logger.info(f"[backtest {self.symbol}] {pct}% ({i - start_i}/{total_steps} candles, {len(trades)} signal(s) so far)")

            window = primary_df.iloc[max(0, i - self.WARMUP_CANDLES + 1): i + 1]
            current_ts = window.index[-1]
            current_close = float(window["close"].iloc[-1])

            btc_trend = self._trend_asof(btc1h_df, current_ts, self.HTF_TREND_WINDOW)
            htf_trend_1h = self._trend_asof(htf1h_df, current_ts, self.HTF_TREND_WINDOW)
            htf_trend_4h = self._trend_asof(htf4h_df, current_ts, self.HTF_TREND_WINDOW)
            htf_trend_1d = self._trend_asof(htf1d_df, current_ts, self.HTF_TREND_WINDOW)
            entry_trend_5m = self._trend_asof(entry5m_df, current_ts, self.HTF_TREND_WINDOW)

            try:
                signal_data = generator.generate(
                    window,
                    btc_trend,
                    0.0,  # funding_rate: no free historical series, see module docstring
                    htf_trend_1h,
                    htf_trend_4h,
                    entry_price=current_close,
                    liquidation_pressure=None,
                    fundamentals=None,
                    htf_trend_1d=htf_trend_1d,
                    entry_trend_5m=entry_trend_5m,
                )
            except Exception as e:
                logger.warning(f"[backtest {self.symbol}] step failed at {current_ts}: {e}")
                i += 1
                continue

            if signal_data is None:
                i += 1
                continue

            outcome, exit_ts, exit_index, realized_rr = self._resolve_trade(primary_df, i + 1, signal_data)

            trades.append({
                "symbol": self.symbol,
                "direction": signal_data["direction"],
                "entry": signal_data["entry"],
                "entry_type": signal_data.get("entry_type"),
                "stop_loss": signal_data["stop_loss"],
                "take_profit": signal_data["take_profit"],
                "confidence": signal_data["confidence"],
                "score_breakdown": signal_data.get("score_breakdown"),
                "outcome": outcome,
                "realized_rr": realized_rr,
                "entry_time": current_ts.to_pydatetime(),
                "exit_time": exit_ts.to_pydatetime() if exit_ts is not None else None,
            })

            # Mirrors production's "one active signal at a time per symbol"
            # guard (CryptoScanner.save_signal) - jump past this trade's
            # resolution instead of generating overlapping/correlated
            # signals on every subsequent candle while it's still "open".
            i = exit_index if exit_index is not None else i + 1

        return {"trades": trades, "summary": self._summarize(trades)}
