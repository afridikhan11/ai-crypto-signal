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
