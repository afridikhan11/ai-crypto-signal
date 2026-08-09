"""
Confluence-Lift test — do the "weak" latest-ICT engines get better when they
agree with Group A?

WHY THIS EXISTS
---------------
scripts/backtest_matrix.py (--mode new) measured each latest-ICT engine ALONE:
"engine fired -> where did price go?" But in the live pipeline these engines
never fire alone - they sit on TOP of the core Group A read (market structure,
premium/discount). An engine that is flat on its own can still be a good
CONFIRMATION: it may only add edge when it agrees with structure and fires from
the right half of the dealing range.

This script measures exactly that. For every directional signal an engine emits
it records the forward return in up to four buckets:

    standalone     every directional signal (same as --mode new)
    +structure     only when the signal agrees with Market Structure bias
                   (Group A: MarketStructureEngine.structure_alignment)
    +zone          only when it fires from the aligned half of the dealing
                   range (bullish in discount / bearish in premium)
    +both          structure AND zone agree

If an engine's expectancy JUMPS from `standalone` to `+both`, it is a genuine
team player and deserves a confirmation weight even if it looked flat alone.
The "lift" column is (+both expectancy - standalone expectancy).

This stays on the RELIABLE forward-return method (like --mode new). It does NOT
touch the confidence/fill path (scripts/backtest_matrix.py --mode existing),
which the offline backtest can't simulate faithfully.

RUN WHERE BINANCE IS REACHABLE (a cloud VM / Cloud Shell, not the CI sandbox):

    cd "FastAPI Backend"
    python scripts/evaluate_confluence_lift.py --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --interval 1h --days 365

Results print as a table and are written to data/confluence_lift.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional

# Make `app` importable however this is launched, and quiet the per-candle
# INFO logging before any app import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOG_LEVEL", "WARNING")

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from app.backtest.engine import fetch_klines_range  # noqa: E402
from app.smc.fvg import FVGDetector  # noqa: E402
from app.smc.latest_ict_confluence import LatestICTConfluenceEngine  # noqa: E402
from app.smc.market_structure_engine import MarketStructureEngine  # noqa: E402

BUCKETS = ("standalone", "+structure", "+zone", "+both")


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


def _structure_bias(alignment: str) -> Optional[str]:
    if alignment == "aligned_bullish":
        return "bullish"
    if alignment == "aligned_bearish":
        return "bearish"
    return None  # conflicted / insufficient_data -> no Group A directional read


def _zone(snapshot, w: pd.DataFrame, price: float) -> Optional[str]:
    """Premium/discount of the dealing range. Prefer the protected structural
    high/low (Group A); fall back to the window extremes. Equilibrium at 0.5:
    below -> discount (favors longs), above -> premium (favors shorts)."""
    hi = snapshot.protected_high if snapshot.protected_high is not None else float(w["high"].max())
    lo = snapshot.protected_low if snapshot.protected_low is not None else float(w["low"].min())
    if hi is None or lo is None or hi <= lo:
        return None
    pos = (price - lo) / (hi - lo)
    return "discount" if pos < 0.5 else "premium"


def _new_stat() -> dict:
    return {"n": 0, "sum_signed": 0.0, "hits": 0}


def _record(stats: Dict[str, dict], engine: str, bucket: str, signed: float) -> None:
    st = stats[f"{engine}|{bucket}"]
    st["n"] += 1
    st["sum_signed"] += signed
    st["hits"] += 1 if signed > 0 else 0


def _evaluate(df: pd.DataFrame, window: int, horizon: int, step: int,
              stats: Dict[str, dict]) -> None:
    engine = LatestICTConfluenceEngine()
    closes = df["close"].to_numpy()
    n = len(df)
    for i in range(window, n - horizon, step):
        w = df.iloc[i - window:i]
        price = float(closes[i])
        try:
            snapshot = MarketStructureEngine(w).analyze()
            bias_a = _structure_bias(snapshot.structure_alignment)
            zone = _zone(snapshot, w, price)
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
            struct_ok = bias_a is not None and s.bias == bias_a
            zone_ok = zone is not None and (
                (s.bias == "bullish" and zone == "discount")
                or (s.bias == "bearish" and zone == "premium")
            )
            _record(stats, s.name, "standalone", signed)
            if struct_ok:
                _record(stats, s.name, "+structure", signed)
            if zone_ok:
                _record(stats, s.name, "+zone", signed)
            if struct_ok and zone_ok:
                _record(stats, s.name, "+both", signed)


def _summary(stats: Dict[str, dict], min_signals: int) -> List[dict]:
    engines = sorted({k.split("|", 1)[0] for k in stats})
    rows = []
    for name in engines:
        base = stats.get(f"{name}|standalone")
        if not base or base["n"] < min_signals:
            continue
        row = {"engine": name}
        for b in BUCKETS:
            st = stats.get(f"{name}|{b}")
            if st and st["n"] > 0:
                row[b] = {
                    "signals": st["n"],
                    "hit_rate": round(100 * st["hits"] / st["n"], 1),
                    "expectancy_pct": round(100 * st["sum_signed"] / st["n"], 4),
                }
            else:
                row[b] = {"signals": 0, "hit_rate": 0.0, "expectancy_pct": 0.0}
        row["lift_pct"] = round(row["+both"]["expectancy_pct"]
                                - row["standalone"]["expectancy_pct"], 4)
        rows.append(row)
    # Biggest positive lift first - the engines confluence helps the most.
    rows.sort(key=lambda r: r["lift_pct"], reverse=True)
    return rows


def _print(rows: List[dict]) -> None:
    print("\n===== Confluence-Lift: standalone vs aligned with Group A =====")
    print("(exp% = avg signed forward return; lift = +both minus standalone)\n")
    hdr = (f"{'engine':26} {'stand n/exp':>16} {'+struct n/exp':>16} "
           f"{'+zone n/exp':>16} {'+both n/exp':>16} {'lift':>8}")
    print(hdr)
    for r in rows:
        def cell(b):
            c = r[b]
            return f"{c['signals']}/{c['expectancy_pct']}"
        print(f"{r['engine']:26} {cell('standalone'):>16} {cell('+structure'):>16} "
              f"{cell('+zone'):>16} {cell('+both'):>16} {r['lift_pct']:>8}")
    print("\nPositive 'lift' = the engine gets BETTER when it agrees with Group A "
          "(structure + premium/discount) -> earns a confirmation weight.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--horizon", type=int, default=16)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--min-signals", type=int, default=30,
                    help="skip engines with fewer than this many standalone signals")
    ap.add_argument("--out", default="data/confluence_lift.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    stats: Dict[str, dict] = defaultdict(_new_stat)
    per_symbol = {}

    for sym in symbols:
        print(f"Loading {sym} {args.interval} ({args.days}d)...")
        df = await _load(sym, args.interval, args.days)
        if len(df) < args.window + args.horizon + 10:
            print(f"  skipped {sym}: only {len(df)} candles")
            per_symbol[sym] = {"bars": len(df), "evaluated": False}
            continue
        covered = round((df.index[-1] - df.index[0]).total_seconds() / 86_400, 1)
        print(f"  {len(df)} candles, {covered}d -> evaluating")
        per_symbol[sym] = {"bars": len(df), "covered_days": covered, "evaluated": True}
        _evaluate(df, args.window, args.horizon, args.step, stats)

    rows = _summary(stats, args.min_signals)
    _print(rows)

    out = {"params": vars(args), "symbols": symbols,
           "per_symbol": per_symbol, "results": rows}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {args.out} — paste it back (or push it) for weighting.")


if __name__ == "__main__":
    asyncio.run(main())
