"""
Backtest the EXISTING live signal pipeline.

This is the companion to scripts/evaluate_latest_ict.py:

    backtest_existing.py     -> how the CURRENT strategy (the 13-category ICT
                                scorer + entry/exit engines, exactly what runs
                                live) performed on history.
    evaluate_latest_ict.py   -> the predictive edge of each of the 14 NEW,
                                advisory engines, individually.

Run both, side by side, to see whether the new engines actually beat (or would
improve) what the pipeline already does before giving them scoring weight.

This wraps the project's own app/backtest/engine.BacktestEngine, so it plays
back the REAL SignalGenerator: it generates signals candle-by-candle, simulates
each fill and its TP/SL resolution, and reports win-rate, realised R:R, fill
rate, and a win-rate-by-confidence breakdown.

RUN IT WHERE BINANCE IS REACHABLE (your laptop, not the CI sandbox):

    cd "FastAPI Backend"
    python scripts/backtest_existing.py --days 90 --min-confidence 65 \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,XAUUSDT

Prints a per-symbol + combined table and writes data/existing_backtest.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

# Make `app` importable no matter how the script is launched (python
# scripts/x.py adds scripts/ to sys.path, not the project root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backtest.engine import BacktestEngine


async def _run_symbol(symbol: str, timeframe: str, days: int,
                      min_confidence: Optional[int]) -> Dict:
    engine = BacktestEngine(symbol, timeframe=timeframe)
    result = await engine.run(days=days, min_confidence=min_confidence)
    return result


def _combine(summaries: List[Dict]) -> Dict:
    total = sum(s.get("total_signals", 0) for s in summaries)
    wins = sum(s.get("wins", 0) for s in summaries)
    losses = sum(s.get("losses", 0) for s in summaries)
    resolved = wins + losses
    # Weight avg RR by each symbol's resolved trades.
    rr_num = sum(s.get("avg_realized_rr", 0.0) * (s.get("wins", 0) + s.get("losses", 0))
                 for s in summaries)
    return {
        "total_signals": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / resolved * 100, 2) if resolved else 0.0,
        "avg_realized_rr": round(rr_num / resolved, 2) if resolved else 0.0,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--min-confidence", type=int, default=None,
                    help="override the per-asset default confidence gate")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    per_symbol: Dict[str, Dict] = {}
    summaries: List[Dict] = []

    for sym in symbols:
        print(f"Backtesting {sym} {args.timeframe} ({args.days}d)...")
        try:
            result = await _run_symbol(sym, args.timeframe, args.days, args.min_confidence)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym} failed: {e}")
            continue
        if "error" in result:
            print(f"  {sym}: {result['error']}")
            continue
        summary = result.get("summary", {})
        per_symbol[sym] = summary
        summaries.append(summary)
        print(f"  signals={summary.get('total_signals')} "
              f"win_rate={summary.get('win_rate')}% "
              f"avg_rr={summary.get('avg_realized_rr')} "
              f"fill_rate={summary.get('fill_rate')}%")

    combined = _combine(summaries) if summaries else {}

    print("\n=== Existing pipeline backtest ===")
    print(f"{'symbol':12} {'signals':>8} {'win%':>7} {'avg_rr':>7} {'fill%':>7}")
    for sym, s in per_symbol.items():
        print(f"{sym:12} {s.get('total_signals', 0):>8} {s.get('win_rate', 0):>7} "
              f"{s.get('avg_realized_rr', 0):>7} {s.get('fill_rate', 0):>7}")
    if combined:
        print(f"{'COMBINED':12} {combined['total_signals']:>8} {combined['win_rate']:>7} "
              f"{combined['avg_realized_rr']:>7} {'':>7}")

    out = {"params": vars(args), "per_symbol": per_symbol, "combined": combined}
    os.makedirs("data", exist_ok=True)
    path = "data/existing_backtest.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
