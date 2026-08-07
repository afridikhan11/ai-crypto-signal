"""
Matrix backtest — every strategy × multiple timeframes × multiple durations.

One run produces a full grid:

    strategies : the 14 new ICT engines (individually) AND the existing live
                 pipeline (win-rate / RR)
    timeframes : e.g. 15m, 1h, 4h
    durations  : 1M, 3M, 6M, 1Y, 2Y, 3Y  (30/90/180/365/730/1095 days)

so you can see which strategy holds up across timeframes and across time —
the real robustness check before trusting any of them with money.

EFFICIENCY: for the new-engine grid it downloads each (symbol, timeframe) once
at the LONGEST duration and slices that frame for every shorter duration, so a
6-cell duration sweep costs one download, not six.

DATA REALITY: Binance simply doesn't have 2–3 years for newer listings
(commodities, recent alts). Those cells auto-shrink to whatever exists and the
report shows the real covered days, so a short sample is never mistaken for a
full one.

RUN WHERE BINANCE IS REACHABLE (a cloud VM / Cloud Shell, not the CI sandbox):

    python scripts/backtest_matrix.py --mode both \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --timeframes 15m,1h,4h \
        --durations 30,90,180,365,730,1095

Results stream to data/matrix_backtest.json as each timeframe finishes (so a
long run is never lost) and print as readable tables at the end.

NOTE ON COST: `--mode existing` (and `both`) replays the full SignalGenerator
per candle and RE-DOWNLOADS per cell — a 3-year × 3-timeframe existing sweep
can take a long time. Start with `--mode new` (fast), or fewer timeframes.
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

# Make `app` importable however this is launched, and quiet the per-candle
# INFO logging before any app import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOG_LEVEL", "WARNING")

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from app.backtest.engine import fetch_klines_range, BacktestEngine  # noqa: E402
from app.smc.fvg import FVGDetector  # noqa: E402
from app.smc.latest_ict_confluence import LatestICTConfluenceEngine  # noqa: E402

DURATION_LABELS = {30: "1M", 90: "3M", 180: "6M", 365: "1Y", 730: "2Y", 1095: "3Y"}


def _label(days: int) -> str:
    return DURATION_LABELS.get(days, f"{days}d")


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


def _slice_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = df.index[-1] - pd.Timedelta(days=days)
    return df[df.index >= cutoff]


# ------------------------------------------------------------------ new engines
def _eval_new(df: pd.DataFrame, horizon: int, step: int, window: int,
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


def _report_new(stats: Dict[str, dict]) -> List[dict]:
    rows = []
    for name, st in stats.items():
        if st["n"] == 0:
            continue
        exp = st["sum_signed"] / st["n"]
        rows.append({
            "engine": name, "signals": st["n"],
            "hit_rate": round(100 * st["hits"] / st["n"], 1),
            "expectancy_pct": round(100 * exp, 4),
        })
    rows.sort(key=lambda r: r["expectancy_pct"], reverse=True)
    return rows


async def run_new_matrix(symbols, timeframes, durations, horizon, step, window,
                         out_path, result) -> None:
    result["new"] = {}
    for tf in timeframes:
        result["new"][tf] = {}
        # stats[duration_days][engine] pooled across symbols
        stats = {d: defaultdict(lambda: {"n": 0, "sum_signed": 0.0, "hits": 0})
                 for d in durations}
        max_days = max(durations)
        for sym in symbols:
            print(f"[new] {sym} {tf}: downloading up to {max_days}d...")
            full = await _load(sym, tf, max_days)
            if full.empty:
                print(f"  {sym} {tf}: no data")
                continue
            covered = round((full.index[-1] - full.index[0]).total_seconds() / 86_400, 1)
            print(f"  {sym} {tf}: {len(full)} candles, {covered}d covered")
            for d in durations:
                sl = _slice_days(full, d)
                if len(sl) < window + horizon + 10:
                    continue
                _eval_new(sl, horizon, step, window, stats[d])
        for d in durations:
            result["new"][tf][_label(d)] = _report_new(stats[d])
        _save(out_path, result)   # stream after each timeframe
        print(f"[new] {tf} done.")


# ------------------------------------------------------------------ existing pipeline
def _combine(summaries: List[dict]) -> dict:
    wins = sum(s.get("wins", 0) for s in summaries)
    losses = sum(s.get("losses", 0) for s in summaries)
    total = sum(s.get("total_signals", 0) for s in summaries)
    resolved = wins + losses
    rr_num = sum(s.get("avg_realized_rr", 0.0) * (s.get("wins", 0) + s.get("losses", 0))
                 for s in summaries)
    return {
        "total_signals": total, "wins": wins, "losses": losses,
        "win_rate": round(wins / resolved * 100, 2) if resolved else 0.0,
        "avg_realized_rr": round(rr_num / resolved, 2) if resolved else 0.0,
    }


async def run_existing_matrix(symbols, timeframes, durations, out_path, result) -> None:
    result["existing"] = {}
    for tf in timeframes:
        result["existing"][tf] = {}
        for d in durations:
            summaries = []
            for sym in symbols:
                print(f"[existing] {sym} {tf} {_label(d)} ({d}d)...")
                try:
                    res = await BacktestEngine(sym, timeframe=tf).run(days=d)
                except Exception as e:  # noqa: BLE001
                    print(f"  {sym} failed: {e}")
                    continue
                if "error" in res:
                    print(f"  {sym}: {res['error']}")
                    continue
                summaries.append(res.get("summary", {}))
            result["existing"][tf][_label(d)] = _combine(summaries) if summaries else {}
            _save(out_path, result)   # stream after each cell
        print(f"[existing] {tf} done.")


# ------------------------------------------------------------------ io + tables
def _save(path: str, result: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2)


def _print_new(result: dict, top: int) -> None:
    if "new" not in result:
        return
    print("\n================ NEW ENGINES — edge by timeframe × duration ================")
    for tf, byd in result["new"].items():
        print(f"\n### Timeframe {tf}  (top {top} engines per duration, by expectancy) ###")
        header = f"{'duration':9} {'engine':26} {'signals':>8} {'hit%':>6} {'exp%':>9}"
        print(header)
        for dlabel, rows in byd.items():
            for r in rows[:top]:
                print(f"{dlabel:9} {r['engine']:26} {r['signals']:>8} "
                      f"{r['hit_rate']:>6} {r['expectancy_pct']:>9}")


def _print_existing(result: dict) -> None:
    if "existing" not in result:
        return
    print("\n================ EXISTING PIPELINE — win-rate by timeframe × duration ================")
    for tf, byd in result["existing"].items():
        print(f"\n### Timeframe {tf} ###")
        print(f"{'duration':9} {'signals':>8} {'win%':>7} {'avg_rr':>7}")
        for dlabel, s in byd.items():
            if not s:
                print(f"{dlabel:9} {'-':>8} {'-':>7} {'-':>7}")
                continue
            print(f"{dlabel:9} {s.get('total_signals', 0):>8} "
                  f"{s.get('win_rate', 0):>7} {s.get('avg_realized_rr', 0):>7}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--timeframes", default="15m,1h,4h")
    ap.add_argument("--durations", default="30,90,180,365,730,1095",
                    help="comma-separated days (1M,3M,6M,1Y,2Y,3Y)")
    ap.add_argument("--mode", choices=["new", "existing", "both"], default="both")
    ap.add_argument("--horizon", type=int, default=16, help="new-engine forward candles")
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--top", type=int, default=6, help="engines shown per cell in the printout")
    ap.add_argument("--out", default="data/matrix_backtest.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    durations = sorted(int(d) for d in args.durations.split(",") if d.strip())

    result: dict = {"params": vars(args), "symbols": symbols,
                    "timeframes": timeframes, "durations": durations}

    print(f"Matrix: {len(symbols)} symbols × {len(timeframes)} timeframes × "
          f"{len(durations)} durations | mode={args.mode}")

    if args.mode in ("new", "both"):
        await run_new_matrix(symbols, timeframes, durations,
                             args.horizon, args.step, args.window, args.out, result)
    if args.mode in ("existing", "both"):
        await run_existing_matrix(symbols, timeframes, durations, args.out, result)

    _save(args.out, result)
    _print_new(result, args.top)
    _print_existing(result)
    print(f"\nWrote {args.out} — paste it back for data-driven weighting.")


if __name__ == "__main__":
    asyncio.run(main())
