"""Multi-timeframe ICT signal generator.

Top-down flow (ICT methodology):

    1. HTF bias      – 4h/1h decide LONG / SHORT / stand-aside.
    2. Kill zone     – only trade institutional windows (optional hard filter).
    3. Liquidity     – require a sweep of the opposite side (stop raid).
    4. Displacement  – require an impulsive CISD move leaving an FVG.
    5. OTE / OB      – enter on the 0.62-0.79 retracement or a mitigated OB.
    6. Confluence    – score everything; publish only high-probability setups.
    7. Risk model    – structure-based stop, liquidity-drawn targets.

The generator is pure (no I/O) so it is fully unit-testable with synthetic
OHLC frames.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from app.ai.scorer import AIScorer
from app.indicators.confirmation import ConfirmationIndicators
from app.models.signal import Direction
from app.smc.displacement import DisplacementDetector, DisplacementType
from app.smc.fvg import FVGDetector
from app.smc.key_levels import compute_key_levels
from app.smc.liquidity import LiquidityDetector
from app.smc.market_structure import MarketStructure, SwingType
from app.smc.order_blocks import OrderBlockDetector
from app.smc.ote import compute_ote
from app.smc.sessions import active_kill_zone
from app.smc.supply_demand import SupplyDemandZones
from app.strategy.bias import Bias, compute_bias


class SignalGenerator:
    def __init__(self, symbol: str, min_confidence: int = 65, min_rr: float = 1.5):
        self.symbol = symbol
        self.min_confidence = min_confidence
        self.min_rr = min_rr
        self.ai_scorer = AIScorer()

    # ------------------------------------------------------------------
    def generate(
        self,
        ltf_df: pd.DataFrame,
        htf_frames: Dict[str, pd.DataFrame],
        htf_order: List[str],
        btc_trend: str = "neutral",
        funding_rate: float = 0.0,
        volatility: str = "normal",
        now: Optional[datetime] = None,
        enforce_killzones: bool = False,
        ltf_timeframe: str = "15m",
    ) -> Optional[Dict[str, Any]]:
        if ltf_df is None or len(ltf_df) < 60:
            return None

        # --- 1. Higher-timeframe bias --------------------------------------
        bias = compute_bias(htf_frames, htf_order)
        if not bias.is_actionable:
            logger.debug(f"{self.symbol}: no HTF bias ({bias.reasons})")
            return None
        direction = Direction.LONG if bias.direction == "LONG" else Direction.SHORT

        # --- 2. Kill zone ---------------------------------------------------
        kz_time = now or ltf_df.index[-1].to_pydatetime()
        kz = active_kill_zone(kz_time)
        if enforce_killzones and kz is None:
            logger.debug(f"{self.symbol}: outside kill zone, skipping")
            return None

        # --- LTF analysis primitives ---------------------------------------
        conf = ConfirmationIndicators(ltf_df)
        conf_latest = conf.get_latest()
        if conf_latest is None:
            logger.warning(f"{self.symbol}: indicators NaN, skipping")
            return None
        atr = conf_latest["atr"]
        if pd.isna(atr) or atr <= 0:
            return None

        current_price = float(ltf_df["close"].iloc[-1])
        ms = MarketStructure(ltf_df)

        # --- 3. Liquidity sweep --------------------------------------------
        sweep = self._detect_sweep(ltf_df, ms, direction)

        # --- 4. Displacement + FVG (CISD) ----------------------------------
        disp_type = (
            DisplacementType.BULLISH if direction == Direction.LONG else DisplacementType.BEARISH
        )
        disp = DisplacementDetector(ltf_df, conf.atr).find_latest(disp_type)

        # --- 5. OTE / order block ------------------------------------------
        ote_info = {"in_zone": False, "at_sweet_spot": False}
        ote_zone = None
        if disp is not None:
            ote_zone = compute_ote(disp)
            tol = 0.15 * atr
            ote_info["in_zone"] = ote_zone.contains(current_price, tolerance=tol)
            ote_info["at_sweet_spot"] = abs(current_price - ote_zone.sweet_spot) <= tol

        ob_detector = OrderBlockDetector(ltf_df)
        ob = (
            ob_detector.find_bullish_order_block()
            if direction == Direction.LONG
            else ob_detector.find_bearish_order_block()
        )
        ob_info = {"present": False, "mitigated": False}
        if ob is not None:
            ob_info["present"] = True
            ob_info["mitigated"] = ob_detector.check_mitigation(ob, current_price)

        fvgs = [f for f in FVGDetector(ltf_df).detect_fvg() if not f.filled]

        # --- LTF structure confirmation ------------------------------------
        breaks = ms.detect_bos_choch()
        ltf_confirmed = any(
            (b.direction == "bullish") == (direction == Direction.LONG) for b in breaks
        )
        ms_info = {
            "confirmed": ltf_confirmed,
            "break_type": breaks[-1].type if breaks else None,
        }

        # --- Premium/discount of the LTF dealing range ---------------------
        sd = SupplyDemandZones(ltf_df)
        sd.calculate_recent_range(lookback=min(50, len(ltf_df)))
        ltf_zone = sd.get_zone(current_price)

        # --- 6. Score confluence -------------------------------------------
        features = {
            "direction": direction.value,
            "htf_bias": {
                "direction": bias.direction,
                "strength": bias.strength,
                "htf_zone": bias.htf_zone,
                "per_tf": bias.per_tf,
            },
            "liquidity_sweep": sweep,
            "displacement": {
                "present": disp is not None,
                "has_fvg": bool(disp and disp.has_fvg),
                "body_atr": disp.body_atr if disp else 0.0,
            },
            "ote": ote_info,
            "order_block": ob_info,
            "market_structure_ltf": ms_info,
            "confirmation": conf_latest,
            "killzone": {
                "active": kz is not None,
                "name": kz.name if kz else "Off-session",
                "weight": kz.weight if kz else 0.3,
            },
            "supply_demand_zone": ltf_zone,
            "institutional": {
                "btc_trend": btc_trend,
                "funding_rate": funding_rate,
                "volatility": volatility,
            },
        }
        confidence, reason = self.ai_scorer.assess(features)

        if confidence < self.min_confidence:
            logger.info(f"{self.symbol}: REJECTED conf={confidence} | {reason}")
            return None

        # --- 7. Risk model --------------------------------------------------
        risk_plan = self._build_risk(
            direction, current_price, atr, ms, disp, ote_zone, ltf_df
        )
        if risk_plan is None:
            return None
        stop_loss, tp1, tp2, tp3, rr = risk_plan

        if rr < self.min_rr:
            logger.info(f"{self.symbol}: REJECTED rr={rr} < {self.min_rr}")
            return None

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
            "timeframe": ltf_timeframe,
            "session": kz.name if kz else "Off-session",
            "htf_bias": bias.direction,
            "bias_strength": bias.strength,
            "ai_model_version": self.ai_scorer.VERSION,
        }
        logger.success(
            f"{self.symbol}: SIGNAL {direction.value} entry={signal['entry']} "
            f"conf={confidence} rr={rr} [{signal['session']}]"
        )
        return signal

    # ------------------------------------------------------------------
    def _detect_sweep(self, df: pd.DataFrame, ms: MarketStructure, direction: Direction,
                      lookback: int = 12) -> Dict[str, Any]:
        """Detect a recent stop-raid of the opposite side that price reclaimed."""
        levels = compute_key_levels(df)
        liq = LiquidityDetector(df, ms.swing_highs, ms.swing_lows)
        recent = df.iloc[-lookback:]
        close = float(df["close"].iloc[-1])

        if direction == Direction.LONG:
            # Sell-side pools below price that were pierced then reclaimed.
            candidates = [levels.prev_day_low, levels.asian_low]
            candidates += [lvl.price for lvl in liq.detect_equal_lows()]
            candidates += [sw.price for sw in ms.swing_lows[-3:]]
            for lvl in filter(None, candidates):
                if recent["low"].min() < lvl <= close:
                    return {"swept": True, "level_type": "sell-side", "level": float(lvl)}
        else:
            candidates = [levels.prev_day_high, levels.asian_high]
            candidates += [lvl.price for lvl in liq.detect_equal_highs()]
            candidates += [sw.price for sw in ms.swing_highs[-3:]]
            for lvl in filter(None, candidates):
                if recent["high"].max() > lvl >= close:
                    return {"swept": True, "level_type": "buy-side", "level": float(lvl)}
        return {"swept": False, "level_type": None, "level": None}

    # ------------------------------------------------------------------
    def _build_risk(self, direction, entry, atr, ms, disp, ote_zone, df):
        """Structure-based stop + liquidity-drawn targets. Returns tuple or None."""
        levels = compute_key_levels(df)
        buffer = 0.25 * atr

        if direction == Direction.LONG:
            anchors = [entry - 1.0 * atr]
            if disp is not None:
                anchors.append(disp.origin)
            if ms.swing_lows:
                anchors.append(min(sw.price for sw in ms.swing_lows[-3:]))
            if ote_zone is not None:
                anchors.append(ote_zone.leg_origin)
            stop_loss = min(anchors) - buffer
            risk = entry - stop_loss
            if risk <= 0:
                return None
            # Draw-on-liquidity targets above.
            pools = [p for p in [levels.prev_day_high, levels.curr_day_high] if p and p > entry]
            pools += [sw.price for sw in ms.swing_highs[-3:] if sw.price > entry]
            draw = min(pools) if pools else None
            tp1 = max(entry + 2 * risk, draw) if draw else entry + 2 * risk
            tp2 = tp1 + 1.5 * risk
            tp3 = tp1 + 3.0 * risk
        else:
            anchors = [entry + 1.0 * atr]
            if disp is not None:
                anchors.append(disp.origin)
            if ms.swing_highs:
                anchors.append(max(sw.price for sw in ms.swing_highs[-3:]))
            if ote_zone is not None:
                anchors.append(ote_zone.leg_origin)
            stop_loss = max(anchors) + buffer
            risk = stop_loss - entry
            if risk <= 0:
                return None
            pools = [p for p in [levels.prev_day_low, levels.curr_day_low] if p and p < entry]
            pools += [sw.price for sw in ms.swing_lows[-3:] if sw.price < entry]
            draw = max(pools) if pools else None
            tp1 = min(entry - 2 * risk, draw) if draw else entry - 2 * risk
            tp2 = tp1 - 1.5 * risk
            tp3 = tp1 - 3.0 * risk

        rr = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0
        return stop_loss, tp1, tp2, tp3, rr
