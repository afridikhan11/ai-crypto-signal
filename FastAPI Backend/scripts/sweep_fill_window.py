"""
Fill-window sweep — find the pending-entry expiry that best trades off fill-rate
against win-rate / R:R.

WHY THIS EXISTS
---------------
The existing-pipeline backtest showed ~96% of signals EXPIRE: a pending limit
entry has only PENDING_ENTRY_EXPIRY_CANDLES (=12) candles to fill before it is
abandoned, and in ICT price often doesn't retrace into the entry zone that fast.
A high expiry rate is not automatically bad (an unfilled entry is simply no
trade, no loss) - the real question is whether GIVING setups more time to fill
captures profitable trades or just adds losers.

This script answers that empirically: it replays the real pipeline at several
expiry windows and reports, for each, how many signals filled and how the filled
trades actually did. Only after seeing this table should the live constant
(app/core/constants.PENDING_ENTRY_EXPIRY_CANDLES) be changed - and only to a
window the data supports.

    window   signals  fill%   win%   avg_rr   (filled/resolved)
    12          ...    4.2     34.3    0.78     ...
    24          ...    ...     ...     ...      ...
    48          ...    ...     ...     ...      ...

You want the window where fill% rises AND win%/avg_rr stay healthy (ideally the
point of diminishing returns). If win%/RR collapse as the window widens, the
extra fills are junk and 12 was right.

RUN WHERE BINANCE IS REACHABLE (a cloud VM / Cloud Shell, not the CI sandbox):

    cd "FastAPI Backend"
    python scripts/sweep_fill_window.py --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --days 180 --timeframe 15m --min-confidence 45 --windows 12,24,48,96

NOTE ON COST: each window re-runs the full pipeline per symbol (re-downloading
per cell), so keep the symbol/duration list modest. Results also stream to
--out after each window.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

# Make `app` importable however this is launched, and quiet per-candle logging.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.backtest.engine import BacktestEngine  # noqa: E402


def _combine(summaries: List[Dict]) -> Dict:
    total = sum(s.get("total_signals", 0) for s in summaries)
    wins = sum(s.get("wins", 0) for s in summaries)
    losses = sum(s.get("losses", 0) for s in summaries)
    expired = sum(s.get("expired", 0) for s in summaries)
    resolved = wins + losses
    rr_num = sum(s.get("avg_realized_rr", 0.0) * (s.get("wins", 0) + s.get("losses", 0))
                 for s in summaries)
    return {
        "total_signals": total,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "resolved": resolved,
        "fill_rate": round((total - expired) / total * 100, 2) if total else 0.0,
        "win_rate": round(wins / resolved * 100, 2) if resolved else 0.0,
        "avg_realized_rr": round(rr_num / resolved, 2) if resolved else 0.0,
    }


async def _run_window(symbols, days, timeframe, min_confidence, window) -> Dict:
    summaries = []
    for sym in symbols:
        print(f"  [window={window}] {sym} {timeframe} ({days}d)...")
        try:
            res = await BacktestEngine(
                sym, timeframe=timeframe, pending_expiry_candles=window
            ).run(days=days, min_confidence=min_confidence)
        except Exception as e:  # noqa: BLE001
            print(f"    {sym} failed: {e}")
            continue
        if "error" in res:
            print(f"    {sym}: {res['error']}")
            continue
        summaries.append(res.get("summary", {}))
    return _combine(summaries) if summaries else {}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--min-confidence", type=int, default=45,
                    help="lower gate so enough signals exist to measure fills")
    ap.add_argument("--windows", default="12,24,48,96",
                    help="comma-separated pending-entry expiry candle counts")
    ap.add_argument("--out", default="data/fill_window_sweep.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    windows = [int(w) for w in args.windows.split(",") if w.strip()]

    result: dict = {"params": vars(args), "symbols": symbols, "by_window": {}}
    print(f"Fill-window sweep: {len(symbols)} symbols × {len(windows)} windows "
          f"| {args.timeframe} {args.days}d min_conf={args.min_confidence}")

    for w in windows:
        combined = await _run_window(symbols, args.days, args.timeframe,
                                     args.min_confidence, w)
        result["by_window"][str(w)] = combined
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)   # stream after each window

    print("\n===== Fill-window sweep (current live default = 12) =====")
    print(f"{'window':>7} {'signals':>8} {'fill%':>7} {'win%':>7} "
          f"{'avg_rr':>7} {'resolved':>9}")
    for w in windows:
        c = result["by_window"].get(str(w)) or {}
        if not c:
            print(f"{w:>7} {'-':>8} {'-':>7} {'-':>7} {'-':>7} {'-':>9}")
            continue
        print(f"{w:>7} {c.get('total_signals', 0):>8} {c.get('fill_rate', 0):>7} "
              f"{c.get('win_rate', 0):>7} {c.get('avg_realized_rr', 0):>7} "
              f"{c.get('resolved', 0):>9}")
    print("\nPick the window where fill% climbs while win%/avg_rr stay healthy. "
          "If win%/RR fall as the window widens, the extra fills are junk and 12 "
          "was right. Then set PENDING_ENTRY_EXPIRY_CANDLES accordingly.")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
