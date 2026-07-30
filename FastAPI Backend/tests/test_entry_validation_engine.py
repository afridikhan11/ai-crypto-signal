"""Regression suite for app.strategy.entry_validation_engine."""
import pytest

from app.strategy.entry_validation_engine import EntryValidationEngine
from app.smc.institutional_bias_engine import InstitutionalBias
from app.smc.htf_structure_engine import HTFSnapshot, TimeframeStructure
from app.smc.market_structure_engine import MarketStructureSnapshot


def _bias(direction):
    return InstitutionalBias(direction=direction, confluence_count=0, supporting_evidence=[])


def _htf(alignment, tf="1d"):
    snap = MarketStructureSnapshot(swing_highs=[], swing_lows=[], internal_breaks=[], external_breaks=[],
                                    protected_high=None, protected_low=None, structure_alignment=alignment)
    ts = TimeframeStructure(timeframe=tf, structure_alignment=alignment, protected_high=None, protected_low=None,
                             latest_break_direction=None, latest_break_type=None, snapshot=snap)
    return HTFSnapshot(timeframes={tf: ts})


@pytest.fixture(scope="module")
def engine():
    return EntryValidationEngine()


class TestFullyValidSetup:
    def test_all_checks_pass(self, engine):
        result = engine.validate(
            "LONG", institutional_bias=_bias("bullish"), htf_snapshot=_htf("aligned_bullish"),
            liquidity_present=True, order_block_present=True, fvg_present=False,
            session_context=None, risk_reward=2.5, invalidation_level=95.0,
        )
        assert result.valid is True
        assert result.failed_checks == []


class TestIndividualRejections:
    def test_opposing_institutional_bias_rejects(self, engine):
        result = engine.validate("LONG", institutional_bias=_bias("bearish"), liquidity_present=True, invalidation_level=95.0)
        assert result.valid is False
        assert "institutional_bias" in result.failed_checks

    def test_neutral_bias_does_not_reject(self, engine):
        result = engine.validate("LONG", institutional_bias=_bias("neutral"), liquidity_present=True, invalidation_level=95.0)
        assert "institutional_bias" not in result.failed_checks

    def test_opposing_htf_alignment_rejects(self, engine):
        result = engine.validate("LONG", htf_snapshot=_htf("aligned_bearish"), liquidity_present=True, invalidation_level=95.0)
        assert result.valid is False
        assert "htf_alignment" in result.failed_checks

    def test_missing_htf_does_not_reject(self, engine):
        result = engine.validate("LONG", htf_snapshot=None, liquidity_present=True, invalidation_level=95.0)
        assert "htf_alignment" not in result.failed_checks

    def test_no_confluence_rejects(self, engine):
        result = engine.validate("LONG", liquidity_present=False, order_block_present=False, fvg_present=False, invalidation_level=95.0)
        assert result.valid is False
        assert "confluence" in result.failed_checks

    def test_poor_risk_reward_rejects(self, engine):
        result = engine.validate("LONG", liquidity_present=True, risk_reward=1.0, invalidation_level=95.0)
        assert result.valid is False
        assert "risk" in result.failed_checks

    def test_missing_invalidation_rejects(self, engine):
        result = engine.validate("LONG", liquidity_present=True, invalidation_level=None)
        assert result.valid is False
        assert "invalidation" in result.failed_checks

    def test_off_session_with_zero_confluence_rejects(self, engine):
        class _Ctx:
            is_off_session = True
        result = engine.validate("LONG", liquidity_present=False, order_block_present=False, fvg_present=False,
                                  session_context=_Ctx(), invalidation_level=95.0)
        assert "session_killzone" in result.failed_checks

    def test_off_session_with_confluence_does_not_reject_session_check(self, engine):
        class _Ctx:
            is_off_session = True
        result = engine.validate("LONG", liquidity_present=True, session_context=_Ctx(), invalidation_level=95.0)
        assert "session_killzone" not in result.failed_checks


class TestConfigurableRequiredChecks:
    def test_only_required_checks_gate_validity(self):
        engine = EntryValidationEngine(required_checks=["invalidation"])
        result = engine.validate("LONG", liquidity_present=False, invalidation_level=95.0)
        # confluence would fail, but it's not in required_checks, so overall still valid
        assert result.valid is True
        assert "confluence" in result.failed_checks
