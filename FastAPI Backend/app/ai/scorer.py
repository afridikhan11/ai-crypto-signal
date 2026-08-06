"""ICT confluence scorer.

Turns the structured features produced by :class:`SignalGenerator` into a
0-100 confidence score plus a human-readable reason string. The weights encode
ICT priorities: trade *with* the higher-timeframe bias, off a liquidity sweep,
into an imbalance left by displacement, at an optimal (OTE) price, inside a
kill zone.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


class AIScorer:
    VERSION = "2.0.0"

    def __init__(self):
        self.weights = {
            "htf_alignment": 0.22,
            "liquidity_sweep": 0.15,
            "displacement_fvg": 0.15,
            "ote_entry": 0.12,
            "order_block": 0.10,
            "market_structure_ltf": 0.08,
            "confirmation_alignment": 0.08,
            "killzone": 0.05,
            "institutional_filters": 0.05,
        }

    def assess(self, features: Dict[str, Any]) -> Tuple[int, str]:
        scores: Dict[str, float] = {}
        reasons = []
        direction = features.get("direction", "LONG")

        # --- Higher-timeframe alignment (the dominant factor) ---------------
        htf = features.get("htf_bias", {})
        if htf.get("direction") == direction:
            strength = float(htf.get("strength", 0.5))
            scores["htf_alignment"] = 60 + 40 * strength
            reasons.append(f"HTF bias {direction} ({htf.get('per_tf', {})})")
        else:
            scores["htf_alignment"] = 20  # counter-trend penalty

        # HTF premium/discount context reinforces or dampens.
        zone = htf.get("htf_zone", "unknown")
        if (direction == "LONG" and zone == "discount") or (
            direction == "SHORT" and zone == "premium"
        ):
            scores["htf_alignment"] = min(scores["htf_alignment"] + 10, 100)
            reasons.append(f"HTF {zone}")

        # --- Liquidity sweep (draw on liquidity engineered) -----------------
        sweep = features.get("liquidity_sweep", {})
        if sweep.get("swept"):
            scores["liquidity_sweep"] = 90
            reasons.append(f"Liquidity swept ({sweep.get('level_type', 'level')})")
        else:
            scores["liquidity_sweep"] = 45

        # --- Displacement + FVG (Change in State of Delivery) ---------------
        disp = features.get("displacement", {})
        if disp.get("present") and disp.get("has_fvg"):
            body = float(disp.get("body_atr", 0))
            scores["displacement_fvg"] = min(70 + body * 12, 100)
            reasons.append(f"Displacement+FVG ({body:.1f} ATR)")
        elif disp.get("present"):
            scores["displacement_fvg"] = 60
            reasons.append("Displacement")
        else:
            scores["displacement_fvg"] = 30

        # --- Optimal Trade Entry --------------------------------------------
        ote = features.get("ote", {})
        if ote.get("in_zone"):
            scores["ote_entry"] = 95 if ote.get("at_sweet_spot") else 80
            reasons.append("OTE entry" + (" (sweet spot)" if ote.get("at_sweet_spot") else ""))
        else:
            scores["ote_entry"] = 40

        # --- Order block ----------------------------------------------------
        ob = features.get("order_block", {})
        if ob and ob.get("present"):
            scores["order_block"] = 85 if ob.get("mitigated") else 65
            reasons.append("OB mitigation" if ob.get("mitigated") else "OB present")
        else:
            scores["order_block"] = 30

        # --- LTF structure confirmation -------------------------------------
        ms = features.get("market_structure_ltf", {})
        if ms.get("confirmed"):
            scores["market_structure_ltf"] = 85
            reasons.append(f"LTF {ms.get('break_type', 'BOS')} {direction}")
        else:
            scores["market_structure_ltf"] = 45

        # --- Indicator confirmation -----------------------------------------
        conf = features.get("confirmation", {})
        scores["confirmation_alignment"] = self._score_confirmation(direction, conf, reasons)

        # --- Kill zone timing -----------------------------------------------
        kz = features.get("killzone", {})
        scores["killzone"] = float(kz.get("weight", 0.3)) * 100
        if kz.get("active"):
            reasons.append(f"{kz.get('name')} KZ")

        # --- Institutional filters (BTC / funding / volatility) -------------
        scores["institutional_filters"] = self._score_institutional(direction, features, reasons)

        total = sum(self.weights[k] * scores.get(k, 0) for k in self.weights)
        return int(round(total)), "; ".join(reasons)

    # ------------------------------------------------------------------
    @staticmethod
    def _score_confirmation(direction: str, conf: Dict[str, Any], reasons: list) -> float:
        if not conf:
            return 50.0
        score = 50.0
        ema20, ema50 = conf.get("ema20", 0), conf.get("ema50", 0)
        rsi, adx = conf.get("rsi", 50), conf.get("adx", 0)
        st_dir = conf.get("supertrend_dir", 0)
        if direction == "LONG":
            if ema20 > ema50 and rsi > 50 and adx > 20:
                score = 85
                reasons.append("Bullish EMA/RSI/ADX")
            if st_dir == 1:
                score += 5
        else:
            if ema20 < ema50 and rsi < 50 and adx > 20:
                score = 85
                reasons.append("Bearish EMA/RSI/ADX")
            if st_dir == -1:
                score += 5
        if conf.get("volume_spike"):
            score += 5
        return min(score, 100.0)

    # ------------------------------------------------------------------
    @staticmethod
    def _score_institutional(direction: str, features: Dict[str, Any], reasons: list) -> float:
        inst = features.get("institutional", {})
        score = 70.0
        btc_trend = inst.get("btc_trend", "neutral")
        if (direction == "LONG" and btc_trend == "up") or (
            direction == "SHORT" and btc_trend == "down"
        ):
            score = 88
            reasons.append("BTC aligned")
        elif btc_trend == "neutral":
            score = 65
        else:
            score = 35
            reasons.append("BTC opposing")

        funding = inst.get("funding_rate", 0.0)
        # Contrarian: crowded longs (high +funding) hurt a long, help a short.
        if direction == "LONG" and funding > 0.001:
            score -= 10
        elif direction == "SHORT" and funding < -0.001:
            score -= 10

        if inst.get("volatility") == "high":
            score -= 20
            reasons.append("High volatility")
        return max(score, 0.0)
