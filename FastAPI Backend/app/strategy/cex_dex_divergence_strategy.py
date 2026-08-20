"""
CEX-DEX Divergence strategy (Smart AI module).

The idea
--------
A perpetual future on a CEX and the underlying spot token on a DEX are the
same asset priced in two different venues. The perp can drift away from spot
because perp price is driven by *positioning* (leverage) rather than by spot
supply/demand: when leveraged longs crowd in, the perp trades at a PREMIUM to
the real (spot) price with nothing backing it; convergence back toward spot is
the mean-reversion edge. The reverse - a perp DISCOUNT to spot - is crowded
shorts and a mean-reversion long.

    divergence_pct = (cex_perp_price - dex_spot_price) / dex_spot_price * 100

  * divergence_pct > 0  -> perp PREMIUM  -> fade with a SHORT
  * divergence_pct < 0  -> perp DISCOUNT -> fade with a LONG

Funding as a WEIGHTED CONFIRMATION (not a second trigger)
---------------------------------------------------------
Funding rate and the CEX-DEX divergence are *correlated by construction*: both
are downstream of the same thing - crowded perp positioning. Positive funding
(longs pay shorts) and a perp premium are two readings of the same crowded-long
state; negative funding and a perp discount, of the same crowded-short state.
Treating funding as an INDEPENDENT trigger would therefore double-count one
signal and fabricate confidence that is not really there.

So funding is used only to nudge `confidence`, never to open or block a trade:

  * funding AGREES with the divergence direction (positive funding + premium/
    short, or negative funding + discount/long)  ->  confidence *= (1 + w)
  * funding DISAGREES                             ->  confidence *= (1 - w)
  * funding unknown/neutral                        ->  no adjustment

where ``w = settings.divergence_funding_weight``. The divergence itself is the
only thing that can make the strategy fire.

Purity
------
Per the Smart AI contract this strategy performs NO I/O. It receives the DEX
quote pre-fetched by the runner (which uses `app/services/dexscreener_service.py`)
in ``market_data.extra["dex"]`` as ``{"price", "liquidity_usd", "timestamp_ms"}``.
The only state it holds is an in-memory per-symbol counter of consecutive
divergent polls - no external calls, which keeps it deterministic and testable.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from app.models.signal import Direction
from app.strategy.base_strategy import (
    BaseStrategy,
    ConditionResult,
    MarketData,
    StrategySignal,
    register_strategy,
)

# The stop is derived from the divergence magnitude itself (there is no
# separate stop-distance knob): the target is FULL convergence to spot, and we
# abandon the mean-reversion thesis only if the dislocation instead extends by
# another half of its current size. Reward = full dislocation `d`, risk =
# `0.5 * d`, so risk_reward is a constant, honest 2.0 by construction.
_STOP_EXTENSION_FRACTION = 0.5


@register_strategy
class CexDexDivergenceStrategy(BaseStrategy):
    """Fade a sustained CEX-perp vs DEX-spot dislocation back toward spot."""

    strategy_id = "cex_dex_divergence"
    display_name = "CEX-DEX Divergence"

    def __init__(self, settings: Any, now_ms: Optional[Callable[[], int]] = None) -> None:
        super().__init__(settings)
        # Injectable clock so tests control "now" for the staleness guard; the
        # default reads wall-clock in ms. Never call time.time() directly in
        # evaluate() - always via self._now_ms().
        self._now_ms_fn: Callable[[], int] = now_ms or (lambda: int(time.time() * 1000))
        # Per-symbol consecutive-divergent-poll state:
        #   symbol -> {"count": int, "direction": Optional[Direction]}
        # The streak is keyed by symbol and only ever advances while the SAME
        # direction keeps printing on usable (liquid + fresh) quotes; it resets
        # to 0 on a non-divergent poll, an unusable quote, or a direction flip.
        self._streaks: Dict[str, Dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.smartai_enabled
            and self.settings.strategy_cex_dex_divergence_enabled
        )

    def _now_ms(self) -> int:
        return self._now_ms_fn()

    def _reset_streak(self, symbol: str) -> int:
        self._streaks[symbol] = {"count": 0, "direction": None}
        return 0

    def _advance_streak(self, symbol: str, direction: Direction) -> int:
        prev = self._streaks.get(symbol)
        if prev and prev["direction"] == direction:
            count = prev["count"] + 1
        else:
            # First divergent poll, or the direction just flipped.
            count = 1
        self._streaks[symbol] = {"count": count, "direction": direction}
        return count

    async def evaluate(self, market_data: MarketData) -> Optional[StrategySignal]:
        symbol = market_data.symbol
        dex = market_data.extra.get("dex") if market_data.extra else None
        cex_price = market_data.current_price

        # Defensive: the runner always populates these, but a strategy must not
        # crash on a missing/malformed quote - it just declines to trade.
        if not dex or cex_price is None:
            self._reset_streak(symbol)
            logger.debug(f"[{self.strategy_id}] {symbol}: no DEX quote / price, skipping")
            return None

        dex_spot = dex.get("price")
        liquidity_usd = dex.get("liquidity_usd")
        ts_ms = dex.get("timestamp_ms")
        if not dex_spot or dex_spot <= 0 or liquidity_usd is None or ts_ms is None:
            self._reset_streak(symbol)
            logger.debug(f"[{self.strategy_id}] {symbol}: incomplete DEX quote {dex}, skipping")
            return None

        divergence_pct = (cex_price - dex_spot) / dex_spot * 100.0
        direction = Direction.SHORT if divergence_pct > 0 else Direction.LONG

        # ---- Hard guards (each a ConditionResult) --------------------------
        liquidity_ok = liquidity_usd >= self.settings.dexscreener_min_liquidity_usd
        age_s = (self._now_ms() - ts_ms) / 1000.0
        quote_fresh = age_s <= self.settings.dexscreener_staleness_seconds
        is_divergent = abs(divergence_pct) >= self.settings.divergence_threshold_pct

        # The streak only advances on a usable (liquid + fresh) AND divergent
        # poll; any break - thin pool, stale quote, sub-threshold print, or a
        # direction flip - resets the "consecutive" chain to 0.
        if liquidity_ok and quote_fresh and is_divergent:
            streak = self._advance_streak(symbol, direction)
        else:
            streak = self._reset_streak(symbol)

        min_polls = self.settings.divergence_min_consecutive_polls
        consecutive_ok = streak >= min_polls

        # Funding is confirmation-only (see module docstring): it never gates.
        funding = market_data.funding_rate
        if funding is None or funding == 0:
            funding_agrees: Optional[bool] = None
        else:
            funding_agrees = (funding > 0 and direction == Direction.SHORT) or (
                funding < 0 and direction == Direction.LONG
            )

        conditions: List[ConditionResult] = [
            ConditionResult(
                "liquidity_ok",
                liquidity_ok,
                f"pool ${liquidity_usd:,.0f} vs min ${self.settings.dexscreener_min_liquidity_usd:,.0f}",
            ),
            ConditionResult(
                "quote_fresh",
                quote_fresh,
                f"age {age_s:.1f}s vs max {self.settings.dexscreener_staleness_seconds:.0f}s",
            ),
            ConditionResult(
                "divergence_threshold",
                is_divergent,
                f"|{divergence_pct:+.2f}%| vs min {self.settings.divergence_threshold_pct:.2f}%",
            ),
            ConditionResult(
                "consecutive_polls",
                consecutive_ok,
                f"{streak}/{min_polls} consecutive divergent polls",
            ),
            ConditionResult(
                "funding_confirmation",
                bool(funding_agrees),
                (
                    "no funding data"
                    if funding_agrees is None
                    else f"funding {funding:+.4%} {'confirms' if funding_agrees else 'contradicts'} {direction.value}"
                ),
            ),
        ]

        # Any hard guard failing => no trade. Funding is NOT a guard.
        guards_pass = liquidity_ok and quote_fresh and is_divergent and consecutive_ok
        if not guards_pass:
            failed = [c.name for c in conditions[:4] if not c.passed]
            logger.debug(
                f"[{self.strategy_id}] {symbol}: no signal "
                f"(div={divergence_pct:+.2f}%, failed={failed})"
            )
            return None

        # ---- Build the signal ---------------------------------------------
        # Mean-reversion: take profit at full convergence to spot; stop at a
        # further adverse dislocation of half the current gap (see
        # _STOP_EXTENSION_FRACTION) -> risk_reward == 2.0 by construction.
        dislocation = abs(cex_price - dex_spot)
        entry_price = cex_price
        take_profit = dex_spot
        stop_distance = dislocation * _STOP_EXTENSION_FRACTION
        if direction == Direction.SHORT:
            stop_loss = entry_price + stop_distance
        else:
            stop_loss = entry_price - stop_distance
        risk_reward = dislocation / stop_distance if stop_distance > 0 else 0.0

        # Confidence from divergence magnitude (how many multiples of the
        # threshold), then nudged by the funding confirmation weight.
        threshold = self.settings.divergence_threshold_pct
        multiples = abs(divergence_pct) / threshold if threshold > 0 else 1.0
        base_conf = min(90.0, 40.0 + (multiples - 1.0) * 25.0)
        w = self.settings.divergence_funding_weight
        if funding_agrees is True:
            confidence = base_conf * (1.0 + w)
        elif funding_agrees is False:
            confidence = base_conf * (1.0 - w)
        else:
            confidence = base_conf
        confidence_int = int(max(0.0, min(100.0, round(confidence))))

        max_notional_usd = liquidity_usd * self.settings.divergence_max_pool_depth_fraction

        reason = (
            f"Perp {direction.value} fade: {divergence_pct:+.2f}% "
            f"{'premium' if direction == Direction.SHORT else 'discount'} to DEX spot "
            f"sustained {streak} polls; target convergence to {dex_spot:.6g}"
        )

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=round(risk_reward, 4),
            confidence=confidence_int,
            conditions=conditions,
            targets=[take_profit],
            reason=reason,
            metadata={
                "divergence_pct": round(divergence_pct, 4),
                "dex_spot_price": dex_spot,
                "cex_perp_price": cex_price,
                "liquidity_usd": liquidity_usd,
                "max_notional_usd": max_notional_usd,
                "consecutive_polls": streak,
                "funding_rate": funding,
                "funding_confirms": funding_agrees,
            },
        )
        logger.info(
            f"[{self.strategy_id}] {symbol}: {direction.value} signal "
            f"div={divergence_pct:+.2f}% conf={confidence_int} rr={signal.risk_reward} "
            f"max_notional=${max_notional_usd:,.0f}"
        )
        return signal
