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

        if confidence < 60:
            logger.info(
        f"{self.symbol}: REJECTED | "
        f"Confidence={confidence} | "
        f"Reason={reason}"
    )
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
        logger.success(
         f"{self.symbol}: SIGNAL GENERATED | "
         f"{direction.value} | "
         f"Entry={round(current_price, 8)} | "
         f"Confidence={confidence}"
        )   
        return signal
# TODO: Multi-timeframe confirmation not yet implemented; all analysis uses 1m data.
        # A future version must incorporate 5m/15m/1h/4h structure alignment.