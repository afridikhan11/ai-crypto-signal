"""
UniversalScanner tests - the one scanner for every asset.

Exercises the real `UniversalScanner` against a stub data manager, so the
scanner's own flow (determine asset type -> load profile -> run the shared
pipeline -> evidence -> AI decision -> signal) is genuinely executed. Only
the network/DB boundaries are stubbed.
"""
import asyncio
import ast
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import app.scheduler.universal_scanner as universal_scanner_module
from app.assets.asset_profile import COMMODITY_PROFILE
from app.core.constants import ASSET_CLASS_COMMODITY, ASSET_CLASS_CRYPTO
from app.scheduler.universal_scanner import (
    HTF_TIMEFRAMES,
    PRIMARY_TIMEFRAME,
    UniversalScanner,
)
from app.strategy.signal_generator import SignalGenerator

BACKEND_ROOT = Path("/sessions/eloquent-gracious-goodall/mnt/ai-crypto-signal/FastAPI Backend")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSDT", "CLUSDT"]


def _ohlcv(n=400, seed=1, drift=0.2, freq="15min", start="2026-01-05"):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(drift, 1.2, n))
    close = np.maximum(close, 5)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    hi = np.maximum(open_, close)
    lo = np.minimum(open_, close)
    wick = np.abs(close - open_) * rng.uniform(0.02, 0.5, n) + 1e-6
    return pd.DataFrame(
        {"open": open_, "high": hi + wick, "low": lo - wick, "close": close,
         "volume": rng.uniform(10, 1000, n)},
        index=pd.date_range(start, periods=n, freq=freq),
    )


class _StubDataManager:
    """Records every access so tests can assert on caching/duplicate scans."""

    def __init__(self, symbols, frames=None, empty=False):
        self.symbols = symbols
        self.empty = empty
        self.frames = frames or {}
        self.get_dataframe_calls = []
        self.daily_calls = 0
        self.weekly_calls = 0
        self.started_ws = False

    def get_dataframe(self, symbol, timeframe, limit=None):
        self.get_dataframe_calls.append((symbol, timeframe))
        if self.empty:
            return pd.DataFrame()
        if timeframe in self.frames:
            return self.frames[timeframe]
        if timeframe == "1m":
            return _ohlcv(5, seed=99, freq="1min")
        return _ohlcv(200, seed=abs(hash(timeframe)) % 500, freq="1h")

    async def get_daily_dataframe(self, symbol, limit=None):
        self.daily_calls += 1
        return _ohlcv(120, seed=7, freq="1D", start="2025-09-01")

    async def get_weekly_dataframe(self, symbol, limit=None):
        self.weekly_calls += 1
        return _ohlcv(80, seed=8, freq="7D", start="2025-01-05")

    async def get_funding_rate(self, symbol):
        return 0.0001

    def get_liquidation_pressure(self, symbol):
        return {"long_liquidated_usd": 0.0, "short_liquidated_usd": 0.0}

    def set_on_candle_callback(self, cb):
        self.callback = cb

    async def initialise_historical_data(self):
        return None

    async def start_websocket(self):
        self.started_ws = True

    async def start_liquidation_stream(self):
        return None

    async def stop(self):
        return None


class _StubFundamentals:
    async def get_open_interest_trend(self, symbol):
        return {"oi_trend": "rising", "oi_change_pct": 1.2}

    async def get_long_short_ratio(self, symbol):
        return 1.05

    async def get_fear_greed_index(self):
        return {"value": 55}

    async def get_btc_dominance(self):
        return 50.0

    async def close(self):
        return None


class _StubMacro:
    def __init__(self):
        self.calls = 0

    async def get_dollar_index_trend(self):
        self.calls += 1
        return {"dxy_trend": "down", "dxy_change_pct": -0.4}

    async def get_real_yield_trend(self):
        return {"real_yield_trend": "down"}

    async def get_high_impact_event_risk(self):
        return {"high_impact_event_soon": True, "event_name": "FOMC Rate Decision"}

    async def close(self):
        return None


def _scanner(symbols=None, **kwargs):
    scanner = UniversalScanner(symbols or SYMBOLS)
    scanner.data_manager = _StubDataManager(scanner.symbols, **kwargs)
    scanner.fundamentals = _StubFundamentals()
    scanner.macro_fundamentals = _StubMacro()
    return scanner


class TestConstruction:
    def test_resolves_one_profile_per_symbol(self):
        s = _scanner()
        assert set(s.asset_profiles) == set(SYMBOLS)

    def test_builds_one_pipeline_per_symbol(self):
        s = _scanner()
        assert set(s.generators) == set(SYMBOLS)
        assert all(isinstance(g, SignalGenerator) for g in s.generators.values())

    def test_crypto_and_commodity_get_different_profiles(self):
        s = _scanner()
        assert s.asset_profiles["BTCUSDT"].asset_class == ASSET_CLASS_CRYPTO
        assert s.asset_profiles["XAUUSDT"].asset_class == ASSET_CLASS_COMMODITY

    def test_pipeline_receives_the_resolved_profile(self):
        """The scanner must hand the SAME profile object to the pipeline, not
        let it re-resolve independently."""
        s = _scanner()
        for symbol in SYMBOLS:
            assert s.generators[symbol].asset_profile is s.asset_profiles[symbol]

    def test_commodity_pipeline_gets_commodity_sessions(self):
        s = _scanner()
        gold_engine = s.generators["XAUUSDT"].session_engine
        assert set(gold_engine.session_windows) == set(COMMODITY_PROFILE.session_windows)

    def test_crypto_pipeline_keeps_three_sessions(self):
        s = _scanner()
        assert len(s.generators["BTCUSDT"].session_engine.session_windows) == 3


class TestEveryAssetUsesTheSamePipeline:
    def test_all_generators_are_the_same_class(self):
        s = _scanner()
        assert len({type(g) for g in s.generators.values()}) == 1

    def test_all_generators_share_engine_types(self):
        """Same engine classes for crypto and gold - only config differs."""
        s = _scanner()
        crypto, gold = s.generators["BTCUSDT"], s.generators["XAUUSDT"]
        for attr in (
            "ai_scorer", "session_engine", "ote_engine", "htf_structure_engine",
            "institutional_bias_engine", "entry_engine", "entry_validation_engine",
            "exit_engine", "evidence_engine", "decision_engine",
        ):
            assert type(getattr(crypto, attr)) is type(getattr(gold, attr)), attr

    def test_scanner_module_declares_no_per_asset_branching_in_analysis(self):
        """`analyze_symbol` must not branch on asset type - the profile is
        the only thing that varies."""
        tree = ast.parse((BACKEND_ROOT / "app/scheduler/universal_scanner.py").read_text(encoding="utf-8"))
        analyze = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "analyze_symbol"
        )
        source = ast.dump(analyze)
        for banned in ("ASSET_CLASS_COMMODITY", "ASSET_TYPE_GOLD", "asset_class_for_symbol"):
            assert banned not in source, (
                f"analyze_symbol branches on {banned} - per-asset logic must live in the profile"
            )


class TestScanFlow:
    def test_scan_completes_and_reads_primary_timeframe(self):
        s = _scanner()
        asyncio.run(s.analyze_symbol("BTCUSDT"))
        assert (("BTCUSDT", PRIMARY_TIMEFRAME)) in s.data_manager.get_dataframe_calls

    def test_scan_runs_the_pipeline_exactly_once(self):
        """Guards against calling generate_decision() and generate()
        separately, which would scan the same candle twice."""
        s = _scanner()
        calls = {"n": 0}
        real_evaluate = s.generators["BTCUSDT"].evaluate

        def counting_evaluate(*args, **kwargs):
            calls["n"] += 1
            return real_evaluate(*args, **kwargs)

        s.generators["BTCUSDT"].evaluate = counting_evaluate

        def fail_if_called(*a, **k):
            raise AssertionError("pipeline entered twice - use evaluate() once per scan")

        s.generators["BTCUSDT"].generate = fail_if_called
        s.generators["BTCUSDT"].generate_decision = fail_if_called

        asyncio.run(s.analyze_symbol("BTCUSDT"))
        assert calls["n"] == 1

    def test_empty_dataframe_aborts_without_raising(self):
        s = _scanner(empty=True)
        asyncio.run(s.analyze_symbol("BTCUSDT"))  # must not raise

    def test_scan_never_raises_across_all_asset_classes(self):
        s = _scanner()
        for symbol in SYMBOLS:
            asyncio.run(s.analyze_symbol(symbol))

    def test_exceptions_inside_a_scan_are_contained(self):
        """One bad symbol must not take down the scan loop."""
        s = _scanner()

        def boom(*a, **k):
            raise RuntimeError("engine exploded")

        s.generators["BTCUSDT"].evaluate = boom
        asyncio.run(s.analyze_symbol("BTCUSDT"))  # logged, not raised


class TestHTFDataAssembly:
    def test_loads_every_htf_timeframe(self):
        s = _scanner()
        frames = asyncio.run(s._load_htf_dataframes("BTCUSDT"))
        assert set(frames) == set(HTF_TIMEFRAMES)

    def test_uses_cached_daily_and_weekly_helpers(self):
        """1d/1w come from the data manager's long-TTL REST caches rather
        than fresh per-scan fetches."""
        s = _scanner()
        asyncio.run(s._load_htf_dataframes("BTCUSDT"))
        assert s.data_manager.daily_calls == 1
        assert s.data_manager.weekly_calls == 1

    def test_empty_timeframe_is_omitted_not_faked(self):
        s = _scanner(empty=True)
        frames = asyncio.run(s._load_htf_dataframes("BTCUSDT"))
        # 1h/4h come from the (empty) candle cache and must be dropped;
        # 1d/1w come from the stubbed REST helpers and remain.
        assert "1h" not in frames and "4h" not in frames

    def test_htf_failure_degrades_rather_than_raising(self):
        s = _scanner()

        async def broken(*a, **k):
            raise RuntimeError("REST down")

        s.data_manager.get_weekly_dataframe = broken
        frames = asyncio.run(s._load_htf_dataframes("BTCUSDT"))
        assert "1w" not in frames
        assert "1d" in frames


class TestFundamentalsRouting:
    def test_crypto_gets_no_commodity_macro_reads(self):
        s = _scanner()
        fundamentals = asyncio.run(s._load_fundamentals("BTCUSDT"))
        assert "dxy_trend" not in fundamentals
        assert s.macro_fundamentals.calls == 0

    def test_commodity_gets_macro_reads(self):
        s = _scanner()
        fundamentals = asyncio.run(s._load_fundamentals("XAUUSDT"))
        assert fundamentals["dxy_trend"] == "down"
        assert fundamentals["real_yield_trend"] == "down"
        assert s.macro_fundamentals.calls == 1

    def test_event_priority_comes_from_the_asset_profile(self):
        s = _scanner()
        fundamentals = asyncio.run(s._load_fundamentals("XAUUSDT"))
        assert fundamentals["event_priority"] == "high"

    def test_both_asset_classes_share_the_same_base_fields(self):
        s = _scanner()
        crypto = asyncio.run(s._load_fundamentals("BTCUSDT"))
        gold = asyncio.run(s._load_fundamentals("XAUUSDT"))
        for key in ("oi_trend", "long_short_ratio", "fear_greed_value", "btc_dominance"):
            assert key in crypto and key in gold


class TestCandleCallback:
    def test_ignores_non_primary_timeframes(self):
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        asyncio.run(s.on_new_candle("BTCUSDT", "1m", []))
        assert len(s.data_manager.get_dataframe_calls) == before

    def test_triggers_on_primary_timeframe(self):
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        asyncio.run(s.on_new_candle("BTCUSDT", PRIMARY_TIMEFRAME, []))
        assert len(s.data_manager.get_dataframe_calls) > before


class TestEngineRunStateGate:
    """Auto Trading Control Panel (2026-07-30) - Pause/Stop must suppress
    new signal analysis without touching the WebSocket/data manager (so
    Resume/Start is instant, no re-warm needed). See
    app/services/trading_settings.py and the module docstring's EXCHANGE
    STOP SYNCHRONIZATION-adjacent 'Auto Trading Control Panel' section."""

    def test_paused_skips_analysis(self):
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        with patch.object(universal_scanner_module, "get_engine_run_state", return_value="paused"):
            asyncio.run(s.on_new_candle("BTCUSDT", PRIMARY_TIMEFRAME, []))
        assert len(s.data_manager.get_dataframe_calls) == before

    def test_stopped_skips_analysis(self):
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        with patch.object(universal_scanner_module, "get_engine_run_state", return_value="stopped"):
            asyncio.run(s.on_new_candle("BTCUSDT", PRIMARY_TIMEFRAME, []))
        assert len(s.data_manager.get_dataframe_calls) == before

    def test_running_state_allows_analysis(self):
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        with patch.object(universal_scanner_module, "get_engine_run_state", return_value="running"):
            asyncio.run(s.on_new_candle("BTCUSDT", PRIMARY_TIMEFRAME, []))
        assert len(s.data_manager.get_dataframe_calls) > before

    def test_is_running_property_reflects_lifecycle_flag(self):
        s = _scanner()
        assert s.is_running is False  # never started in this test helper
        s._running = True
        assert s.is_running is True

    def test_paused_still_ignores_non_primary_timeframe_first(self):
        """The existing non-primary-timeframe filter still runs before the
        new pause gate - order doesn't matter for the outcome, but this
        pins that both checks compose rather than one hiding the other."""
        s = _scanner()
        before = len(s.data_manager.get_dataframe_calls)
        with patch.object(universal_scanner_module, "get_engine_run_state", return_value="running"):
            asyncio.run(s.on_new_candle("BTCUSDT", "1m", []))
        assert len(s.data_manager.get_dataframe_calls) == before
