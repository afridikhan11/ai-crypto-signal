"""
Evaluate the predictive edge of the fourteen latest-ICT engines.

WHY THIS EXISTS
---------------
The new engines (app/smc/latest_ict_confluence.py) are wired into the pipeline
as *advisory* evidence - they do not move the confidence number yet. Before
giving any of them real scoring weight you must know which ones actually
predicted direction on real history. This script measures exactly that.

WHAT IT DOES
------------
For each symbol it downloads historical klines from Binance's public endpoint
(no API key needed), then walks forward candle-by-candle. At each step it runs
every latest-ICT engine over the trailing window; whenever an engine emits a
directional signal (bullish/bearish) it records the *forward return* over the
next `horizon` candles. It then aggregates, per engine:

    signals      how many directional calls it made
    hit_rate     % of calls where price moved the predicted way
    expectancy   average signed forward return (the real edge)
    suggested_w  a normalised weight proposal (edge-proportional)

An engine with hit_rate > ~55% and positive expectancy has genuine edge and
deserves scoring weight; one near 50% / negative does not.

RUN IT WHERE BINANCE IS REACHABLE (your laptop, not the CI sandbox):

    cd "FastAPI Backend"
    python scripts/evaluate_latest_ict.py --days 90 --horizon 16 \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,XAUUSDT

Results print as a table and are written to data/latest_ict_backtest.json.
Paste that file back and the engines can be integrated with data-driven weights.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List

import httpx
import pandas as pd

# Make `app` importable no matter how the script is launched (python
# scripts/x.py adds scripts/ to sys.path, not the project root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Quiet the app's per-candle INFO logging (app.core.logging adds a stdout
# handler at settings.log_level) BEFORE any app import; explicit LOG_LEVEL wins.
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.backtest.engine import fetch_klines_range
from app.smc.fvg import FVGDetector
from app.smc.latest_ict_confluence import LatestICTConfluenceEngine


def _candles_to_df(candles) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "timestamp": c.timestamp, "open": c.open, "high": c.high,
        "low": c.low, "close": c.close, "volume": c.volume,
    } for c in candles])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")


async def _load(symbol: str, interval: str, days: int) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    async with httpx.AsyncClient(timeout=30) as client:
        candles = await fetch_klines_range(client, symbol, interval, start_ms, end_ms)
    return _candles_to_df(candles)


def _evaluate_symbol(df: pd.DataFrame, window: int, horizon: int, step: int,
                     stats: Dict[str, dict]) -> None:
    engine = LatestICTConfluenceEngine()
    closes = df["close"].to_numpy()
    n = len(df)
    for i in range(window, n - horizon, step):
        w = df.iloc[i - window:i]
        try:
            fvgs = [f for f in FVGDetector(w).detect_fvg() if not f.filled]
            report = engine.analyze(w, direction=None, fvgs=fvgs)
        except Exception:
            continue
        if not report.signals:
            continue
        fwd = (closes[i + horizon] - closes[i]) / closes[i]
        for s in report.signals:
            if s.bias not in ("bullish", "bearish"):
                continue
            signed = fwd if s.bias == "bullish" else -fwd
            st = stats[s.name]
            st["n"] += 1
            st["sum_signed"] += signed
            st["hits"] += 1 if signed > 0 else 0


def _report(stats: Dict[str, dict]) -> List[dict]:
    rows = []
    for name, st in stats.items():
        if st["n"] == 0:
            continue
        expectancy = st["sum_signed"] / st["n"]
        rows.append({
            "engine": name,
            "signals": st["n"],
            "hit_rate": round(100 * st["hits"] / st["n"], 1),
            "expectancy_pct": round(100 * expectancy, 4),
        })
    rows.sort(key=lambda r: r["expectancy_pct"], reverse=True)
    # Edge-proportional weight proposal from positive expectancies only.
    edge = {r["engine"]: max(0.0, r["expectancy_pct"]) for r in rows}
    total = sum(edge.values()) or 1.0
    for r in rows:
        r["suggested_weight"] = round(edge[r["engine"]] / total, 4)
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--horizon", type=int, default=16, help="forward candles to measure the move over")
    ap.add_argument("--step", type=int, default=3, help="sample every Nth candle (speed vs coverage)")
    ap.add_argument("--out", default="data/latest_ict_backtest.json",
                    help="output JSON path (use a distinct name to run several in parallel)")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    stats: Dict[str, dict] = defaultdict(lambda: {"n": 0, "sum_signed": 0.0, "hits": 0})
    per_symbol_bars = {}

    for sym in symbols:
        print(f"Loading {sym} {args.interval} (requested {args.days}d)...")
        df = await _load(sym, args.interval, args.days)
        covered_days = (
            round((df.index[-1] - df.index[0]).total_seconds() / 86_400, 1)
            if len(df) > 1 else 0.0
        )
        per_symbol_bars[sym] = {"bars": len(df), "covered_days": covered_days}
        if len(df) < args.window + args.horizon + 10:
            # Newer listings (e.g. XAUUSDT/commodities) simply don't have a
            # long history on Binance — use a shorter --days for those.
            print(f"  skipped {sym}: only {len(df)} candles "
                  f"({covered_days}d available — try a shorter --days)")
            continue
        first, last = df.index[0].date(), df.index[-1].date()
        print(f"  {len(df)} candles, {covered_days}d covered ({first} -> {last}) -> evaluating")
        _evaluate_symbol(df, args.window, args.horizon, args.step, stats)

    rows = _report(stats)

    print("\n=== Latest-ICT engine edge (higher expectancy = better) ===")
    print(f"{'engine':30} {'signals':>8} {'hit%':>6} {'exp%':>8} {'weight':>8}")
    for r in rows:
        print(f"{r['engine']:30} {r['signals']:>8} {r['hit_rate']:>6} "
              f"{r['expectancy_pct']:>8} {r['suggested_weight']:>8}")

    out = {
        "params": vars(args),
        "symbols": symbols,
        "bars_per_symbol": per_symbol_bars,
        "results": rows,
    }
    path = args.out
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path} — paste it back for data-driven weighting.")


if __name__ == "__main__":
    asyncio.run(main())
