"""
Backtest-driven weight calibration.

app.ai.calibration.calibrate_weights() only learns from LIVE closed
trades, which take weeks/months to accumulate the 15+ wins and 15+ losses
it requires. This sweeps BacktestEngine across multiple symbols/days to
generate a much larger pool of (score_breakdown, outcome) samples
immediately, then feeds them through the exact same win/loss-spread
formula used for live calibration (app.ai.calibration.weights_from_samples)
and writes to the same weights file AIScorer loads on startup.

This is still real evidence, not synthetic - every sample comes from
actually replaying the live SignalGenerator/AIScorer pipeline against real
historical candles. See app/backtest/engine.py's module docstring for the
documented simplifications (no free historical funding rate/liquidation/
fundamentals data), which apply here too since this reuses that engine.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.ai.calibration import MIN_SAMPLES_PER_GROUP, weights_from_samples, write_weights_file
from app.backtest.engine import BacktestEngine, WIN_OUTCOMES

# A reasonably liquid, diverse default sweep if the caller doesn't specify
# symbols - kept short enough that a full run finishes in a few minutes.
DEFAULT_CALIBRATION_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]


async def calibrate_weights_from_backtest(
    symbols: Optional[List[str]] = None,
    timeframe: str = "15m",
    days: int = 90,
    min_confidence: Optional[int] = None,
) -> Dict[str, Any]:
    symbols = symbols or DEFAULT_CALIBRATION_SYMBOLS

    all_wins: List[Dict[str, float]] = []
    all_losses: List[Dict[str, float]] = []
    per_symbol: Dict[str, Any] = {}
    total_unresolved = 0

    for symbol in symbols:
        try:
            engine = BacktestEngine(symbol, timeframe)
            result = await engine.run(days=days, min_confidence=min_confidence)
        except Exception as e:
            logger.warning(f"[calibration] backtest failed for {symbol}: {e}")
            per_symbol[symbol] = {"error": str(e)}
            continue

        if "error" in result:
            per_symbol[symbol] = {"error": result["error"]}
            continue

        trades = result["trades"]
        wins = [
            t["score_breakdown"] for t in trades
            if t["outcome"] in WIN_OUTCOMES and t.get("score_breakdown")
        ]
        losses = [
            t["score_breakdown"] for t in trades
            if t["outcome"] == "STOPPED" and t.get("score_breakdown")
        ]
        unresolved = sum(1 for t in trades if t["outcome"] == "UNRESOLVED")

        all_wins.extend(wins)
        all_losses.extend(losses)
        total_unresolved += unresolved
        per_symbol[symbol] = {
            "total_signals": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "unresolved": unresolved,
        }

        logger.info(f"[calibration] {symbol}: {len(trades)} signal(s) ({len(wins)} win / {len(losses)} loss)")

    if len(all_wins) < MIN_SAMPLES_PER_GROUP or len(all_losses) < MIN_SAMPLES_PER_GROUP:
        message = (
            f"Not enough combined samples yet: {len(all_wins)} wins / {len(all_losses)} losses "
            f"(need >= {MIN_SAMPLES_PER_GROUP} of each). Try more symbols and/or a longer `days` "
            f"window. Existing weights (calibrated or default) were left untouched."
        )
        logger.info(f"[calibration] {message}")
        return {
            "symbols_used": symbols,
            "total_wins": len(all_wins),
            "total_losses": len(all_losses),
            "total_unresolved": total_unresolved,
            "per_symbol_summary": per_symbol,
            "weights": None,
            "calibrated": False,
            "message": message,
        }

    weights = weights_from_samples(all_wins, all_losses)
    write_weights_file(weights)

    message = (
        f"Calibrated from {len(all_wins)} wins / {len(all_losses)} losses across "
        f"{len(symbols)} symbol(s). Run docker-compose restart app for the live "
        f"scanner to pick up the new weights."
    )
    logger.success(f"[calibration] {message} Weights: {weights}")

    return {
        "symbols_used": symbols,
        "total_wins": len(all_wins),
        "total_losses": len(all_losses),
        "total_unresolved": total_unresolved,
        "per_symbol_summary": per_symbol,
        "weights": weights,
        "calibrated": True,
        "message": message,
    }
