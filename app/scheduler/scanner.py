import asyncio
import json
from typing import List, Dict, Any

from loguru import logger
from sqlalchemy import select

from app.services.binance_service import BinanceDataManager
from app.strategy.signal_generator import SignalGenerator
from app.models.signal import Signal, Direction, SignalStatus
from app.models.coin import Coin
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client


class CryptoScanner:
    def __init__(self, symbols: List[str]):
        self.data_manager = BinanceDataManager(symbols)
        self.generators = {s: SignalGenerator(s) for s in symbols}
        self._running = False
        self._symbol_locks = {s: asyncio.Lock() for s in symbols}

    async def start(self):
        logger.info("Initializing historical market data...")

        await self.data_manager.initialise_historical_data()

        logger.success("Historical data loaded successfully.")

        # Initial scan so signals can be generated immediately
        for symbol in self.generators.keys():
            try:
                await self.analyze_symbol(symbol)
            except Exception as e:
                logger.exception(f"Initial scan failed for {symbol}: {e}")

        logger.success("Initial market scan completed.")

        self.data_manager.set_on_candle_callback(self.on_new_candle)

        self._running = True

        asyncio.create_task(self.data_manager.start_websocket())

        logger.info("Crypto scanner started.")

    async def on_new_candle(self, symbol: str, interval: str, candles):
        if interval != "1m":
            return

        async with self._symbol_locks[symbol]:
            await self.analyze_symbol(symbol)

    async def analyze_symbol(self, symbol: str):
        try:
            logger.info(f"[{symbol}] Analysis started")

            df = self.data_manager.get_dataframe(symbol, "1m", limit=200)

            logger.info(f"[{symbol}] Candles loaded: {len(df)}")

            if df.empty:
                logger.warning(f"[{symbol}] DataFrame is empty")
                return

            btc_trend = "up"
            funding_rate = 0.0001
            volatility = "normal"

            logger.info(f"[{symbol}] Creating signal generator")

            generator = self.generators[symbol]
            
            logger.info(f"[{symbol}] Calling generate()")
            
            signal_data = generator.generate(
                df,
                btc_trend,
                funding_rate,
                volatility,
            )

            logger.info(f"[{symbol}] generate() returned: {signal_data}")

            if signal_data is None:
                logger.info(f"{symbol}: No signal generated.")
                return

            logger.success(
                f"{symbol}: Signal generated "
                f"({signal_data['direction']}) "
                f"Confidence={signal_data['confidence']}"
            )

            await self.save_signal(signal_data)

            logger.success(f"[{symbol}] Signal saved successfully")
            
        except Exception as e:
            logger.exception(f"Error analyzing {symbol}: {e}")

    async def save_signal(self, data: Dict[str, Any]):
        async with AsyncSessionLocal() as session:

            stmt = select(Coin).where(
                Coin.symbol == data["symbol"].upper()
            )

            result = await session.execute(stmt)

            coin = result.scalar_one_or_none()

            if coin is None:
                coin = Coin(symbol=data["symbol"].upper())
                session.add(coin)
                await session.flush()

            stmt = select(Signal).where(
                Signal.coin_id == coin.id,
                Signal.status == SignalStatus.ACTIVE,
            )

            result = await session.execute(stmt)

            if result.scalar_one_or_none():
                logger.info(
                    f"{data['symbol']}: Active signal already exists."
                )
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

            logger.success(
                f"Saved signal: "
                f"{data['symbol']} "
                f"{data['direction']} "
                f"Confidence={data['confidence']}"
            )

            await redis_client.publish(
                "new_signal",
                json.dumps(data, default=str),
            )

    async def stop(self):
        self._running = False
        await self.data_manager.stop()