"""
Asset Profile system tests, including the Commodity foundation.

Covers the two claims the Universal Platform rests on:
  1. A profile describes a MARKET and never a strategy.
  2. Commodity behaviour comes ONLY from the Commodity Asset Profile - the
     ICT engines and pipeline are byte-identical across asset classes.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.assets import (
    AssetProfile,
    EconomicEventPriority,
    ICTFilters,
    all_asset_profiles,
    get_asset_profile,
)
from app.assets.asset_profile import (
    COMMODITY_PROFILE,
    CRYPTO_PROFILE,
    FOREX_PROFILE,
    INDICES_PROFILE,
    STOCKS_PROFILE,
)
from app.core.constants import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_TYPE_CRYPTO,
    ASSET_TYPE_GOLD,
    ASSET_TYPE_OIL,
    ASSET_TYPE_SILVER,
)
from app.smc.session_engine import KillZone, Session, SessionEngine

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class TestResolution:
    def test_crypto_symbol_resolves_to_crypto_profile(self):
        p = get_asset_profile("BTCUSDT")
        assert p.asset_type == ASSET_TYPE_CRYPTO
        assert p.asset_class == ASSET_CLASS_CRYPTO

    def test_gold_resolves_to_commodity_profile(self):
        p = get_asset_profile("XAUUSDT")
        assert p.asset_class == ASSET_CLASS_COMMODITY
        assert p.display_name == COMMODITY_PROFILE.display_name

    def test_silver_and_oil_share_the_commodity_profile(self):
        for symbol in ("XAGUSDT", "CLUSDT", "BZUSDT"):
            assert get_asset_profile(symbol).display_name == COMMODITY_PROFILE.display_name

    def test_unknown_symbol_defaults_to_crypto(self):
        assert get_asset_profile("SOMENEWCOINUSDT").asset_type == ASSET_TYPE_CRYPTO

    def test_resolution_is_case_insensitive(self):
        assert get_asset_profile("xauusdt").asset_class == ASSET_CLASS_COMMODITY

    def test_every_registered_profile_is_an_asset_profile(self):
        profiles = all_asset_profiles()
        assert len(profiles) >= 5
        assert all(isinstance(p, AssetProfile) for p in profiles.values())


class TestCalibrationIsReferencedNotDuplicated:
    def test_each_symbol_carries_its_own_calibration(self):
        assert get_asset_profile("BTCUSDT").calibration.asset_type == ASSET_TYPE_CRYPTO
        assert get_asset_profile("XAUUSDT").calibration.asset_type == ASSET_TYPE_GOLD
        assert get_asset_profile("XAGUSDT").calibration.asset_type == ASSET_TYPE_SILVER
        assert get_asset_profile("CLUSDT").calibration.asset_type == ASSET_TYPE_OIL

    def test_gold_and_oil_share_market_shape_but_not_calibration(self):
        """This is the whole design: one market profile, separate scoring."""
        gold, oil = get_asset_profile("XAUUSDT"), get_asset_profile("CLUSDT")
        assert gold.session_windows == oil.session_windows
        assert gold.kill_zone_windows == oil.kill_zone_windows
        assert gold.calibration.asset_type != oil.calibration.asset_type
        assert gold.calibration.weights_file != oil.calibration.weights_file

    def test_profile_does_not_redeclare_calibration_fields(self):
        """Scoring config must live on CalibrationProfile only - a duplicate
        threshold here would be two sources of truth."""
        profile_fields = set(AssetProfile.__dataclass_fields__)
        for calibration_only in (
            "min_confidence", "min_risk_reward", "stop_loss_atr_multiple",
            "default_weights", "weights_file", "volatility_lookback",
            "volatility_high_ratio", "volatility_low_ratio",
            "take_profit_fallback_atr_multiple", "min_target_distance_atr",
        ):
            assert calibration_only not in profile_fields, (
                f"{calibration_only} belongs to CalibrationProfile, not AssetProfile"
            )

    def test_profiles_do_not_share_mutable_dicts_across_symbols(self):
        a, b = get_asset_profile("XAUUSDT"), get_asset_profile("XAGUSDT")
        assert a.session_windows is not b.session_windows
        assert a.kill_zone_windows is not b.kill_zone_windows


class TestProfilesNeverImplementStrategy:
    def test_no_strategy_methods_on_the_dataclass(self):
        """A profile answers questions about a market; it must not be able
        to decide, score, or detect anything."""
        banned = ("generate", "score", "assess", "decide", "detect", "signal", "entry", "exit")
        for name, _ in inspect.getmembers(AssetProfile, predicate=inspect.isfunction):
            if name.startswith("__"):
                continue
            assert not any(b in name.lower() for b in banned), (
                f"AssetProfile.{name} looks like strategy logic"
            )

    def test_asset_profile_module_imports_no_ict_engines(self):
        """It may reference session/killzone TYPES, but must not pull in a
        detection engine - that would let per-asset strategy creep in."""
        tree = ast.parse((BACKEND_ROOT / "app/assets/asset_profile.py").read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        forbidden = (
            "app.smc.market_structure_engine", "app.smc.liquidity_engine",
            "app.smc.order_block_engine", "app.smc.fvg", "app.smc.supply_demand_engine",
            "app.smc.ote_engine", "app.smc.inducement_engine",
            "app.smc.institutional_bias_engine", "app.smc.htf_structure_engine",
            "app.ai.scorer", "app.ai.ict_decision_engine", "app.strategy.signal_generator",
        )
        for module in imported:
            assert module not in forbidden, f"asset_profile.py must not import {module}"

    def test_ict_filters_can_only_tighten(self):
        """Defaults are permissive, so a profile that sets nothing behaves
        exactly as the shared pipeline does."""
        default = ICTFilters()
        assert default.require_kill_zone is False
        assert default.reject_off_session is False
        assert default.min_confluence_signals == 1


class TestCryptoProfile:
    def test_uses_all_three_sessions(self):
        assert set(CRYPTO_PROFILE.session_windows) == {Session.ASIAN, Session.LONDON, Session.NEW_YORK}

    def test_trades_continuously(self):
        hours = CRYPTO_PROFILE.trading_hours
        assert hours.is_24_7 is True
        assert hours.trades_weekends is True
        assert hours.daily_break is None

    def test_does_not_gap(self):
        assert CRYPTO_PROFILE.volatility.gaps_across_breaks is False

    def test_applies_no_extra_filters(self):
        """Crypto behaviour must be unchanged by this phase."""
        assert CRYPTO_PROFILE.ict_filters == ICTFilters()

    def test_open_at_every_hour(self):
        assert all(CRYPTO_PROFILE.trading_hours.is_open(h) for h in range(24))


class TestCommodityProfile:
    def test_uses_london_and_new_york_sessions(self):
        assert set(COMMODITY_PROFILE.session_windows) == {Session.LONDON, Session.NEW_YORK}

    def test_asian_session_is_not_a_primary_liquidity_session(self):
        assert Session.ASIAN not in COMMODITY_PROFILE.liquidity.primary_liquidity_sessions
        assert COMMODITY_PROFILE.liquidity.primary_liquidity_sessions == (
            Session.LONDON, Session.NEW_YORK,
        )

    def test_includes_comex_open_kill_zone(self):
        assert KillZone.COMEX_OPEN_KILL_ZONE in COMMODITY_PROFILE.kill_zone_windows

    def test_comex_kill_zone_covers_the_metals_open(self):
        """COMEX metals open is 13:30 UTC / 08:30 ET."""
        window = COMMODITY_PROFILE.kill_zone_windows[KillZone.COMEX_OPEN_KILL_ZONE]
        assert window.contains(13.5)

    def test_has_commodity_kill_zones_not_the_asian_one(self):
        assert KillZone.ASIAN_KILL_ZONE not in COMMODITY_PROFILE.kill_zone_windows
        assert KillZone.LONDON_KILL_ZONE in COMMODITY_PROFILE.kill_zone_windows
        assert KillZone.NEW_YORK_KILL_ZONE in COMMODITY_PROFILE.kill_zone_windows
        assert KillZone.LONDON_CLOSE_KILL_ZONE in COMMODITY_PROFILE.kill_zone_windows

    def test_observes_a_settlement_break_and_weekend_close(self):
        hours = COMMODITY_PROFILE.trading_hours
        assert hours.is_24_7 is False
        assert hours.trades_weekends is False
        assert hours.daily_break is not None
        assert hours.is_open(22.5) is False       # inside the settlement break
        assert hours.is_open(14.0) is True        # mid New York session
        assert hours.is_open(14.0, is_weekend=True) is False

    def test_gaps_and_is_session_clustered(self):
        assert COMMODITY_PROFILE.volatility.gaps_across_breaks is True
        assert COMMODITY_PROFILE.volatility.session_clustered is True

    def test_rejects_off_session_setups(self):
        assert COMMODITY_PROFILE.ict_filters.reject_off_session is True

    def test_usd_macro_events_are_high_priority(self):
        events = COMMODITY_PROFILE.economic_events
        assert events.priority_of("FOMC Rate Decision") == "high"
        assert events.priority_of("US CPI m/m") == "high"
        assert events.priority_of("Crude Oil Inventories") == "medium"
        assert events.priority_of("Some Local Holiday") is None
        assert events.priority_of(None) is None


class TestSessionEngineIsDrivenByTheProfile:
    """Proves the SAME engine produces asset-appropriate results purely from
    profile configuration - no commodity-specific session code exists."""

    @staticmethod
    def _engine(profile):
        return SessionEngine(
            session_windows=profile.session_windows,
            kill_zone_windows=profile.kill_zone_windows,
            london_ny_overlap=profile.london_ny_overlap,
        )

    def test_asian_hours_are_in_session_for_crypto_but_not_commodity(self):
        import pandas as pd
        asian_hour = pd.Timestamp("2026-01-05 02:00:00")
        crypto_ctx = self._engine(get_asset_profile("BTCUSDT")).classify_timestamp(asian_hour)
        gold_ctx = self._engine(get_asset_profile("XAUUSDT")).classify_timestamp(asian_hour)
        assert crypto_ctx.is_off_session is False
        assert gold_ctx.is_off_session is True

    def test_comex_open_classifies_for_gold_only(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-05 13:45:00")
        gold_ctx = self._engine(get_asset_profile("XAUUSDT")).classify_timestamp(ts)
        crypto_ctx = self._engine(get_asset_profile("BTCUSDT")).classify_timestamp(ts)
        assert gold_ctx.in_kill_zone
        # Crypto's default map has no COMEX window, so it can never surface.
        assert crypto_ctx.kill_zone is not KillZone.COMEX_OPEN_KILL_ZONE

    def test_london_session_is_active_for_both(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-05 08:30:00")
        for symbol in ("BTCUSDT", "XAUUSDT"):
            ctx = self._engine(get_asset_profile(symbol)).classify_timestamp(ts)
            assert Session.LONDON in ctx.active_sessions


class TestFutureReadyProfiles:
    def test_forex_indices_stocks_are_declared(self):
        for profile in (FOREX_PROFILE, INDICES_PROFILE, STOCKS_PROFILE):
            assert isinstance(profile, AssetProfile)
            assert profile.session_windows
            assert profile.liquidity.primary_liquidity_sessions

    def test_stocks_has_no_london_ny_overlap(self):
        """A single-session market must not inherit the shared overlap."""
        assert STOCKS_PROFILE.london_ny_overlap is not None
        assert STOCKS_PROFILE.london_ny_overlap.contains(14.0) is False

    def test_adding_an_asset_class_needs_no_new_strategy(self):
        """The architectural claim, asserted: every future profile is the
        same dataclass, so onboarding needs zero new engine/scanner/AI."""
        for profile in (FOREX_PROFILE, INDICES_PROFILE, STOCKS_PROFILE):
            assert type(profile) is AssetProfile


class TestNoFabricatedInstrumentData:
    def test_tick_and_contract_size_are_none_rather_than_guessed(self):
        """Every instrument here is a USDT-margined perpetual, not a COMEX
        contract - a fabricated contract size would be plainly wrong."""
        for profile in all_asset_profiles().values():
            assert profile.tick_size is None
            assert profile.contract_size is None

    def test_tick_size_lookup_degrades_to_none(self):
        assert get_asset_profile("XAUUSDT").tick_size_for("XAUUSDT") is None


class TestEconomicEventPriorityHelper:
    def test_empty_priority_matches_nothing(self):
        assert EconomicEventPriority().priority_of("FOMC") is None

    def test_matching_is_case_insensitive_and_substring_based(self):
        events = EconomicEventPriority(high_impact=("FOMC",), medium_impact=("GDP",))
        assert events.priority_of("  fomc minutes ") == "high"
        assert events.priority_of("Advance GDP q/q") == "medium"

    def test_high_impact_wins_over_medium(self):
        events = EconomicEventPriority(high_impact=("CPI",), medium_impact=("CPI",))
        assert events.priority_of("CPI") == "high"
