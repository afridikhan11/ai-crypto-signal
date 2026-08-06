"""Live multi-timeframe scanner.

Streams candles for the whole symbol universe, and on every close of the entry
timeframe runs the ICT signal generator with *real* market context (BTC bias,
funding, volatility). New signals are persisted and published to Redis.
"""

import asyncio
import json
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.market.context import MarketContext
from app.market.universe import get_scan_symbols
from app.models.coin import Coin
from app.models.signal import Direction, Signal, SignalStatus
from app.services.binance_service import BinanceDataManager
from app.strategy.signal_generator import SignalGenerator


class CryptoScanner:
    def __init__(self, symbols: List[str] | None = None):
        settings = get_settings()
        self.settings = settings
        self.symbols = symbols or get_scan_symbols()
        self.ltf = settings.ltf_timeframe
        self.htf_order = settings.htf_list

        self.data_manager = BinanceDataManager(
            self.symbols, timeframes=settings.stream_tf_list
        )
        self.context = MarketContext(self.data_manager, self.htf_order)
        self.generators = {
            s: SignalGenerator(
                s,
                min_confidence=settings.min_confidence,
                min_rr=settings.min_risk_reward,
            )
            for s in self.symbols
        }
        self._running = False
        self._symbol_locks = {s: asyncio.Lock() for s in self.symbols}

    # ------------------------------------------------------------------
    async def start(self):
        logger.info("Initializing historical market data...")
        await self.data_manager.initialise_historical_data()
        logger.success("Historical data loaded successfully.")

        for symbol in self.symbols:
            try:
                await self.analyze_symbol(symbol)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Initial scan failed for {symbol}: {e}")
        logger.success("Initial market scan completed.")

        self.data_manager.set_on_candle_callback(self.on_new_candle)
        self._running = True
        asyncio.create_task(self.data_manager.start_websocket())
        logger.info(f"Crypto scanner started (entry TF={self.ltf}, HTF={self.htf_order}).")

    # ------------------------------------------------------------------
    async def on_new_candle(self, symbol: str, interval: str, candles):
        # Only re-evaluate on entry-timeframe closes; HTF frames stay cached.
        if interval != self.ltf:
            return
        async with self._symbol_locks[symbol]:
            await self.analyze_symbol(symbol)

    # ------------------------------------------------------------------
    async def analyze_symbol(self, symbol: str):
        try:
            ltf_df = self.data_manager.get_dataframe(symbol, self.ltf, limit=300)
            if ltf_df.empty:
                return

            htf_frames = {
                tf: self.data_manager.get_dataframe(symbol, tf, limit=300)
                for tf in self.htf_order
            }

            btc_trend = self.context.btc_trend()
            funding_rate = await self.context.funding_rate(symbol)
            volatility = MarketContext.classify_volatility(ltf_df)

            signal_data = self.generators[symbol].generate(
                ltf_df,
                htf_frames,
                self.htf_order,
                btc_trend=btc_trend,
                funding_rate=funding_rate,
                volatility=volatility,
                enforce_killzones=self.settings.enforce_killzones,
                ltf_timeframe=self.ltf,
            )

            if signal_data is None:
                return

            await self.save_signal(signal_data)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"Error analyzing {symbol}: {e}")

    # ------------------------------------------------------------------
    async def save_signal(self, data: Dict[str, Any]):
        async with AsyncSessionLocal() as session:
            coin = (
                await session.execute(
                    select(Coin).where(Coin.symbol == data["symbol"].upper())
                )
            ).scalar_one_or_none()

            if coin is None:
                coin = Coin(symbol=data["symbol"].upper())
                session.add(coin)
                await session.flush()

            existing = (
                await session.execute(
                    select(Signal).where(
                        Signal.coin_id == coin.id,
                        Signal.status == SignalStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                logger.info(f"{data['symbol']}: active signal already exists.")
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
                timeframe=data.get("timeframe", self.ltf),
                ai_model_version=data.get("ai_model_version"),
                session=data.get("session"),
                htf_bias=data.get("htf_bias"),
                bias_strength=data.get("bias_strength"),
            )
            session.add(signal)
            await session.commit()
            logger.success(
                f"Saved signal: {data['symbol']} {data['direction']} "
                f"conf={data['confidence']} rr={data['risk_reward']}"
            )

            await redis_client.publish("new_signal", json.dumps(data, default=str))

    # ------------------------------------------------------------------
    async def stop(self):
        self._running = False
        await self.data_manager.stop()
