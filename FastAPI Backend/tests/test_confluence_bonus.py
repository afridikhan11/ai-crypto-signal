"""
Tests for the data-driven latest-ICT confluence bonus.

Two independent surfaces are covered:

  1. The per-asset weights on CalibrationProfile - only engines whose edge was
     proven on that asset class in the matrix backtest carry weight, and every
     other asset carries an EMPTY map (so its live behavior is unchanged).
  2. aligned_confluence_bonus() - the pure, bounded arithmetic that turns a
     confluence report + the profile's weights into confidence points.

The bonus arithmetic is deliberately a module-level pure function so it can be
tested exactly, without having to coax the full end-to-end SignalGenerator into
producing a specific setup.
"""
from dataclasses import dataclass
from typing import Optional

from app.ai.calibration_profiles import get_profile_for_symbol
from app.strategy.signal_generator import aligned_confluence_bonus


@dataclass
class _Sig:
    """Stand-in with the only two attributes the bonus reads (name, bias) -
    shape-compatible with app.smc.latest_ict_confluence.ConfluenceSignal."""
    name: str
    bias: Optional[str]


class TestPerAssetWeights:
    def test_crypto_weights_are_the_in_context_proven_engines(self):
        w = get_profile_for_symbol("BTCUSDT").confluence_weights
        assert w == {
            "IPDA Range": 5.0,
            "Judas Swing (new_york)": 3.0,
            "Turtle Soup": 3.0,
            "Mitigation Block": 2.0,
            "Rejection Block": 2.0,
            "Power of 3": 2.0,
        }
        # Judas Swing (London) stayed negative in context -> no weight on crypto.
        assert "Judas Swing (london)" not in w

    def test_gold_weights_are_the_in_context_proven_engines(self):
        w = get_profile_for_symbol("XAUUSDT").confluence_weights
        assert w == {
            "Power of 3": 5.0,
            "IPDA Range": 2.0,
            "Rejection Block": 2.0,
            "Mitigation Block": 1.0,
        }
        # Judas Swing / Turtle Soup do NOT work on gold (mirror image of crypto).
        assert "Judas Swing (new_york)" not in w
        assert "Turtle Soup" not in w

    def test_other_assets_carry_no_weight_so_behavior_is_unchanged(self):
        for sym in ("XAGUSDT", "CLUSDT", "BZUSDT"):
            assert get_profile_for_symbol(sym).confluence_weights == {}


class TestAlignedConfluenceBonus:
    CRYPTO = {"Judas Swing (new_york)": 5.0, "Turtle Soup": 2.0}

    def test_empty_weights_always_returns_zero(self):
        sigs = [_Sig("Judas Swing (new_york)", "bullish")]
        assert aligned_confluence_bonus(sigs, "bullish", {}, 6.0) == 0.0

    def test_no_signals_returns_zero(self):
        assert aligned_confluence_bonus([], "bullish", self.CRYPTO, 6.0) == 0.0

    def test_aligned_engine_awards_its_weight(self):
        sigs = [_Sig("Judas Swing (new_york)", "bullish")]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 5.0

    def test_opposed_engine_awards_nothing(self):
        sigs = [_Sig("Judas Swing (new_york)", "bearish")]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 0.0

    def test_unproven_engine_awards_nothing(self):
        # Power of 3 is not in the crypto map -> no contribution on crypto.
        sigs = [_Sig("Power of 3", "bullish")]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 0.0

    def test_multiple_aligned_engines_sum_then_cap(self):
        sigs = [
            _Sig("Judas Swing (new_york)", "bullish"),  # 5.0
            _Sig("Turtle Soup", "bullish"),             # 2.0 -> 7.0, capped
        ]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 6.0

    def test_mixed_alignment_counts_only_the_aligned(self):
        sigs = [
            _Sig("Judas Swing (new_york)", "bullish"),  # counts (5.0)
            _Sig("Turtle Soup", "bearish"),             # opposed, ignored
        ]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 5.0

    def test_none_bias_signal_is_ignored(self):
        sigs = [_Sig("Judas Swing (new_york)", None)]
        assert aligned_confluence_bonus(sigs, "bullish", self.CRYPTO, 6.0) == 0.0
