"""
Historical Dataset Validator - Phase 2 Validation (2026-07-30).

STATUS: PRODUCTION (validation tooling). Additive-only: this module does
not modify BacktestEngine, SignalGenerator, or any ICT engine. It is a
standalone quality gate a caller runs BEFORE feeding a historical OHLCV
dataframe into `BacktestEngine`/`WalkForwardRunner`, so a bad dataset is
caught as a labelled, honest report instead of silently producing a
misleading backtest.

WHY THIS EXISTS
--------------------------------------------------------------------------
Phase 2 objective 3 ("Prepare Historical Dataset Validation") asks for
validation of: missing candles, duplicate candles, timezone consistency,
OHLC consistency, volume integrity, gap handling, session handling,
commodity trading hours, and crypto 24/7 trading. Nothing upstream of this
module currently checks any of these - `BacktestEngine._candles_to_df()`
only drops exact duplicate timestamps and sorts; it does not check for
missing candles, OHLC integrity, or session/gap plausibility.

NO FABRICATION
--------------------------------------------------------------------------
Every check below either passes, fails with a concrete count/example, or
is explicitly marked `not_applicable` (e.g. session-hours checks on a
dataframe with no `AssetProfile` supplied). Nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Expected spacing for each Binance kline interval this platform uses.
INTERVAL_MINUTES: Dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
    "1d": 1440, "7d": 10080,
}


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "warn" | "not_applicable"
    detail: str
    count: Optional[int] = None
    examples: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status, "detail": self.detail,
            "count": self.count, "examples": [str(e) for e in self.examples[:10]],
        }


@dataclass
class DatasetValidationReport:
    symbol: str
    timeframe: str
    candle_count: int
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == "warn"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "timeframe": self.timeframe,
            "candle_count": self.candle_count, "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }

    def summary(self) -> str:
        lines = [f"Dataset validation for {self.symbol} ({self.timeframe}, {self.candle_count} candles):"]
        for c in self.checks:
            lines.append(f"  [{c.status.upper():>15}] {c.name}: {c.detail}")
        return "\n".join(lines)


def _check_duplicates(df: pd.DataFrame) -> CheckResult:
    dupes = df.index[df.index.duplicated(keep=False)]
    n = len(set(dupes))
    if n == 0:
        return CheckResult("duplicate_candles", "pass", "No duplicate timestamps.")
    return CheckResult(
        "duplicate_candles", "fail", f"{n} duplicated timestamp(s) found.",
        count=n, examples=list(pd.unique(dupes))[:10],
    )


def _check_missing_candles(df: pd.DataFrame, timeframe: str) -> CheckResult:
    minutes = INTERVAL_MINUTES.get(timeframe)
    if minutes is None:
        return CheckResult(
            "missing_candles", "not_applicable",
            f"No known expected spacing for timeframe '{timeframe}'.",
        )
    if len(df) < 2:
        return CheckResult("missing_candles", "not_applicable", "Fewer than 2 candles - cannot infer spacing.")

    expected = pd.Timedelta(minutes=minutes)
    deltas = df.index.to_series().diff().dropna()
    missing_gaps = deltas[deltas > expected * 1.5]
    if missing_gaps.empty:
        return CheckResult("missing_candles", "pass", "No gaps larger than one expected interval.")

    total_missing_candles = int(sum(round(g / expected) - 1 for g in missing_gaps))
    return CheckResult(
        "missing_candles", "warn",
        f"{len(missing_gaps)} gap(s) totalling ~{total_missing_candles} missing candle(s).",
        count=total_missing_candles,
        examples=[f"{ts}: gap of {g}" for ts, g in missing_gaps.items()][:10],
    )


def _check_timezone_consistency(df: pd.DataFrame) -> CheckResult:
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return CheckResult("timezone_consistency", "fail", "Index is not a DatetimeIndex.")
    # This project's documented convention (see app/smc/session_engine.py)
    # is tz-naive UTC timestamps throughout the pipeline. A tz-aware index
    # would silently break every hour-of-day session/kill-zone comparison
    # SessionEngine performs, so this is a hard fail, not a warning.
    if idx.tz is not None:
        return CheckResult(
            "timezone_consistency", "fail",
            f"Index is tz-aware ({idx.tz}) - project convention is tz-naive UTC "
            "(see app/smc/session_engine.py); SessionEngine's hour-of-day comparisons "
            "would silently misclassify sessions/kill zones against this data.",
        )
    if not idx.is_monotonic_increasing:
        return CheckResult("timezone_consistency", "fail", "Index is not sorted ascending.")
    return CheckResult("timezone_consistency", "pass", "tz-naive, monotonically increasing UTC index.")


def _check_ohlc_consistency(df: pd.DataFrame) -> CheckResult:
    bad = df[
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
        | (df["high"] < df["low"])
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | df[["open", "high", "low", "close"]].isna().any(axis=1)
    ]
    if bad.empty:
        return CheckResult("ohlc_consistency", "pass", "All candles satisfy high >= max(o,c,l), low <= min(o,c,h), positive, non-NaN.")
    return CheckResult(
        "ohlc_consistency", "fail", f"{len(bad)} candle(s) violate OHLC consistency.",
        count=len(bad), examples=list(bad.index[:10]),
    )


def _check_volume_integrity(df: pd.DataFrame) -> CheckResult:
    if "volume" not in df.columns:
        return CheckResult("volume_integrity", "fail", "No 'volume' column present.")
    bad = df[df["volume"].isna() | (df["volume"] < 0)]
    zero_volume = int((df["volume"] == 0).sum())
    if bad.empty and zero_volume == 0:
        return CheckResult("volume_integrity", "pass", "All volume values are non-negative and non-NaN, no zero-volume candles.")
    if bad.empty and zero_volume > 0:
        return CheckResult(
            "volume_integrity", "warn",
            f"{zero_volume} candle(s) have exactly zero volume (thin liquidity or venue closure, not necessarily corrupt).",
            count=zero_volume,
        )
    return CheckResult(
        "volume_integrity", "fail", f"{len(bad)} candle(s) have negative or NaN volume.",
        count=len(bad), examples=list(bad.index[:10]),
    )


def _check_largest_gaps(df: pd.DataFrame, timeframe: str, top_n: int = 5) -> CheckResult:
    minutes = INTERVAL_MINUTES.get(timeframe)
    if minutes is None or len(df) < 2:
        return CheckResult("gap_handling", "not_applicable", "No known expected spacing or too few candles.")
    deltas = df.index.to_series().diff().dropna().sort_values(ascending=False)
    top = deltas.head(top_n)
    expected = pd.Timedelta(minutes=minutes)
    real_gaps = top[top > expected]
    if real_gaps.empty:
        return CheckResult("gap_handling", "pass", "No gap exceeds the expected candle spacing.")
    return CheckResult(
        "gap_handling", "warn",
        f"Largest gap is {real_gaps.iloc[0]} (expected spacing {expected}). "
        f"See examples for the top {len(real_gaps)}.",
        count=len(real_gaps),
        examples=[f"{ts}: {g}" for ts, g in real_gaps.items()],
    )


def _check_session_and_hours(df: pd.DataFrame, asset_profile) -> CheckResult:
    """
    Cross-checks the dataframe's own timestamps against the AssetProfile's
    declared TradingHours (daily_break / weekend closure). This is
    DIAGNOSTIC, not a pass/fail on the data itself: this platform's
    commodity symbols (XAUUSDT/XAGUSDT/CLUSDT/BZUSDT) are USDT-margined
    perpetual futures on Binance, which trade continuously even though the
    AssetProfile models the traditional COMEX/CME session shape (see
    app/assets/asset_profile.py). Candles existing inside the profile's
    declared "closed" window are therefore EXPECTED, not corrupt - the
    reject_off_session ICT filter (not this validator) is what makes the
    pipeline itself treat that window as low-conviction/blocked.
    """
    if asset_profile is None:
        return CheckResult("session_and_trading_hours", "not_applicable", "No AssetProfile supplied.")

    hours = asset_profile.trading_hours
    if hours.is_24_7 and hours.trades_weekends and hours.daily_break is None:
        weekend_count = int((df.index.dayofweek >= 5).sum())
        return CheckResult(
            "session_and_trading_hours", "pass",
            f"Crypto 24/7 profile: {weekend_count} weekend candle(s) present, none is a violation "
            "(is_24_7=True, trades_weekends=True, no daily_break).",
        )

    in_weekend = df.index.dayofweek >= 5
    weekend_candles = int(in_weekend.sum()) if not hours.trades_weekends else 0

    in_break = pd.Series(False, index=df.index)
    if hours.daily_break is not None:
        hour_of_day = df.index.hour + df.index.minute / 60.0
        in_break = hour_of_day.to_series(index=df.index).apply(hours.daily_break.contains)
    break_candles = int(in_break.sum())

    detail = (
        f"Data source trades through this profile's declared closed windows: "
        f"{weekend_candles} candle(s) fall on a weekend the profile marks closed, "
        f"{break_candles} candle(s) fall inside the {hours.daily_break} daily break. "
        "This is expected for a Binance-listed perpetual future and is exactly what the "
        "pipeline's `reject_off_session` ICT filter is designed to gate at the signal level, "
        "not at the data level - see this function's docstring."
    )
    return CheckResult("session_and_trading_hours", "warn" if (weekend_candles or break_candles) else "pass", detail,
                        count=weekend_candles + break_candles)


def validate_ohlcv_dataset(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    asset_profile: Optional[Any] = None,
) -> DatasetValidationReport:
    """Runs every Phase 2 objective-3 check against one OHLCV dataframe."""
    report = DatasetValidationReport(symbol=symbol, timeframe=timeframe, candle_count=len(df))

    if len(df) == 0:
        report.checks.append(CheckResult("empty_dataset", "fail", "Dataframe has zero rows."))
        return report

    required_cols = {"open", "high", "low", "close", "volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        report.checks.append(CheckResult(
            "required_columns", "fail", f"Missing required column(s): {sorted(missing_cols)}.",
        ))
        return report

    report.checks.append(_check_timezone_consistency(df))
    report.checks.append(_check_duplicates(df))
    report.checks.append(_check_missing_candles(df, timeframe))
    report.checks.append(_check_largest_gaps(df, timeframe))
    report.checks.append(_check_ohlc_consistency(df))
    report.checks.append(_check_volume_integrity(df))
    report.checks.append(_check_session_and_hours(df, asset_profile))

    return report
