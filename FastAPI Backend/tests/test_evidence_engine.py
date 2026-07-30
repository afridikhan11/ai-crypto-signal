"""
Regression suite for app.ai.evidence_engine (Phase C, part 2, of the
institutional ICT migration).

Covers every builder method's honest-degradation behavior (None/empty
input -> empty list, never guessed), correct polarity tagging from real
engine dataclasses, the whitelisted-keys guarantee on volume confirmation
(retail indicator keys must never leak through even if present in the
source dict), the polarity validation guard, full `compile()` integration,
and a direct import-level distinctness check against the pre-existing
`app.agent.evidence_engine.EvidenceEngine` (a different module, different
responsibility - see this module's own docstring).
"""
import pandas as pd
import pytest

from app.ai.evidence_engine import (
    ICTEvidenceCategory,
    ICTEvidenceEngine,
    ICTEvidenceInputs,
    ICTEvidenceItem,
)
from app.smc.fvg import FairValueGap, FVGType
from app.smc.inducement_engine import InducementEvent
from app.smc.liquidity_engine import (
    LiquidityKind,
    LiquidityLevel,
    LiquidityScope,
    LiquidityStrength,
    LiquidityType,
    SweepOutcome,
)
from app.smc.market_structure_engine import StructureBreak, StructureScope, SwingPoint, SwingStrength, SwingType
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlock
from app.smc.supply_demand_engine import Zone, ZoneState, ZoneStrength, ZoneType


@pytest.fixture(scope="module")
def engine():
    return ICTEvidenceEngine()


def _ts(i=0):
    return pd.Timestamp("2026-01-05 12:00:00") + pd.Timedelta(minutes=15 * i)


class TestStructureEvidence:
    def test_bullish_bos_and_bearish_choch_labels(self, engine):
        swing = SwingPoint(timestamp=_ts(), price=100.0, type=SwingType.HH, index=0, strength=SwingStrength.STRONG, displacement_ratio=1.0)
        bos = StructureBreak(timestamp=_ts(1), type="BOS", direction="bullish", broken_swing=swing, level=101.0, scope=StructureScope.EXTERNAL, displacement_ratio=1.5, is_mss=False)
        choch = StructureBreak(timestamp=_ts(2), type="CHoCH", direction="bearish", broken_swing=swing, level=99.0, scope=StructureScope.EXTERNAL, displacement_ratio=1.2, is_mss=True)
        items = engine.structure_evidence([bos, choch], "MarketStructureEngine.external_breaks")
        assert items[0].label == "Bullish BOS"
        assert items[0].polarity == "bullish"
        assert items[1].label == "Bearish CHoCH"
        assert items[1].polarity == "bearish"
        assert "(MSS)" in items[1].detail

    def test_empty_when_none(self, engine):
        assert engine.structure_evidence(None, "x") == []
        assert engine.structure_evidence([], "x") == []


class TestLiquiditySweepEvidence:
    def _level(self, liq_type, outcome, swept=True):
        return LiquidityLevel(
            price=100.0, type=liq_type, swing_points=[], kind=LiquidityKind.EQUAL_HIGHS,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG,
            swept=swept, sweep_outcome=outcome, swept_at=_ts(), sweep_confirmed=True,
        )

    def test_bsl_grab_is_bearish(self, engine):
        items = engine.liquidity_sweep_evidence([self._level(LiquidityType.BUYSIDE, SweepOutcome.GRAB)])
        assert items[0].polarity == "bearish"

    def test_ssl_grab_is_bullish(self, engine):
        items = engine.liquidity_sweep_evidence([self._level(LiquidityType.SELLSIDE, SweepOutcome.GRAB)])
        assert items[0].polarity == "bullish"

    def test_bsl_breakout_is_bullish(self, engine):
        items = engine.liquidity_sweep_evidence([self._level(LiquidityType.BUYSIDE, SweepOutcome.BREAKOUT)])
        assert items[0].polarity == "bullish"

    def test_ssl_breakout_is_bearish(self, engine):
        items = engine.liquidity_sweep_evidence([self._level(LiquidityType.SELLSIDE, SweepOutcome.BREAKOUT)])
        assert items[0].polarity == "bearish"

    def test_unswept_level_produces_no_evidence(self, engine):
        items = engine.liquidity_sweep_evidence([self._level(LiquidityType.BUYSIDE, SweepOutcome.UNSWEPT, swept=False)])
        assert items == []


class TestInducementEvidence:
    def test_valid_inducement_label_and_polarity(self, engine):
        level = LiquidityLevel(
            price=110.0, type=LiquidityType.BUYSIDE, swing_points=[], kind=LiquidityKind.EQUAL_HIGHS,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG, swept=True, engineered=True,
            sweep_outcome=SweepOutcome.GRAB, swept_at=_ts(), sweep_confirmed=True,
            reversal_break_level=95.0, reversal_is_choch=True,
        )
        event = InducementEvent(
            liquidity_level=level, reversal_direction="bearish", reversal_break_type="CHoCH",
            reversal_break_timestamp=_ts(2), displacement_ratio=1.5, continuation_confirmed=True,
            conditions_met=["Liquidity sweep confirmed", "Engineered liquidity"],
        )
        items = engine.inducement_evidence([event])
        assert items[0].label == "Valid Inducement"
        assert items[0].polarity == "bearish"
        assert "CHoCH" in items[0].detail

    def test_empty_when_none(self, engine):
        assert engine.inducement_evidence(None) == []


class TestOrderBlockEvidence:
    def _ob(self, block_type, state=OBState.FRESH, strength=OBStrength.STRONG):
        return OrderBlock(
            timestamp=_ts(), high=101.0, low=99.0, type=block_type, state=state, mitigated=False,
            displacement_ratio=1.5, caused_bos=True, caused_choch=False, related_break_level=None,
            has_fvg_confluence=False, strength=strength, mitigated_at=None, breaker_at=None, invalidated_at=None,
        )

    def test_strong_bullish_ob_label(self, engine):
        items = engine.order_block_evidence([self._ob(BlockType.BULLISH_OB)])
        assert items[0].label == "Strong Order Block"
        assert items[0].polarity == "bullish"

    def test_moderate_bearish_ob_label(self, engine):
        items = engine.order_block_evidence([self._ob(BlockType.BEARISH_OB, strength=OBStrength.MODERATE)])
        assert items[0].label == "Order Block (Moderate)"
        assert items[0].polarity == "bearish"

    def test_invalidated_ob_skipped(self, engine):
        items = engine.order_block_evidence([self._ob(BlockType.BULLISH_OB, state=OBState.INVALIDATED)])
        assert items == []


class TestFVGEvidence:
    def test_filled_vs_present_labels(self, engine):
        filled = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.0, type=FVGType.BULLISH, filled=True)
        present = FairValueGap(timestamp=_ts(1), top=99.0, bottom=98.0, type=FVGType.BEARISH, filled=False)
        items = engine.fvg_evidence([filled, present])
        assert items[0].label == "FVG Filled"
        assert items[0].polarity == "bullish"
        assert items[1].label == "FVG Present"
        assert items[1].polarity == "bearish"


class TestSupplyDemandEvidence:
    def _zone(self, zone_type, state=ZoneState.FRESH, strength=ZoneStrength.STRONG):
        return Zone(
            top=101.0, bottom=99.0, type=zone_type, origin_index=5, origin_timestamp=_ts(),
            displacement_ratio=2.0, state=state, strength=strength,
        )

    def test_demand_zone_is_bullish(self, engine):
        items = engine.supply_demand_evidence([self._zone(ZoneType.DEMAND)])
        assert items[0].label == "Demand Zone (Strong)"
        assert items[0].polarity == "bullish"

    def test_supply_zone_is_bearish(self, engine):
        items = engine.supply_demand_evidence([self._zone(ZoneType.SUPPLY, strength=ZoneStrength.WEAK)])
        assert items[0].label == "Supply Zone"
        assert items[0].polarity == "bearish"

    def test_broken_zone_skipped(self, engine):
        items = engine.supply_demand_evidence([self._zone(ZoneType.DEMAND, state=ZoneState.BROKEN)])
        assert items == []


class TestPremiumDiscountEvidence:
    def test_discount_is_bullish(self, engine):
        items = engine.premium_discount_evidence("discount", 0.2)
        assert items[0].polarity == "bullish"

    def test_premium_is_bearish(self, engine):
        items = engine.premium_discount_evidence("premium", 0.8)
        assert items[0].polarity == "bearish"

    def test_equilibrium_is_neutral(self, engine):
        items = engine.premium_discount_evidence("equilibrium", 0.5)
        assert items[0].polarity == "neutral"

    def test_unknown_produces_no_evidence(self, engine):
        assert engine.premium_discount_evidence("unknown", None) == []
        assert engine.premium_discount_evidence(None, None) == []


class TestSessionEvidence:
    def test_kill_zone_context(self, engine):
        from app.smc.session_engine import SessionEngine
        se = SessionEngine()
        ctx = se.classify_timestamp(pd.Timestamp("2026-01-05 08:00:00"))  # London Kill Zone
        items = engine.session_evidence(ctx)
        assert "Kill Zone" in items[0].label
        assert items[0].polarity == "neutral"

    def test_off_session_context(self, engine):
        from app.smc.session_engine import SessionEngine
        se = SessionEngine()
        ctx = se.classify_timestamp(pd.Timestamp("2026-01-05 22:30:00"))
        items = engine.session_evidence(ctx)
        assert items[0].label == "Off-Session"

    def test_none_produces_no_evidence(self, engine):
        assert engine.session_evidence(None) == []


class TestHTFBiasEvidence:
    def test_aligned_bullish(self, engine):
        items = engine.htf_bias_evidence("aligned_bullish", "4H")
        assert items[0].label == "HTF Bullish (4H)"
        assert items[0].polarity == "bullish"

    def test_aligned_bearish(self, engine):
        items = engine.htf_bias_evidence("aligned_bearish", "1D")
        assert items[0].label == "HTF Bearish (1D)"
        assert items[0].polarity == "bearish"

    def test_conflicted_is_neutral(self, engine):
        items = engine.htf_bias_evidence("conflicted", "4H")
        assert items[0].polarity == "neutral"

    def test_none_produces_no_evidence(self, engine):
        assert engine.htf_bias_evidence(None, "4H") == []


class TestVolumeConfirmationEvidence:
    def test_only_whitelisted_keys_are_read(self, engine):
        confirmation = {
            "cvd": 123.4, "cvd_rising": True, "poc_price": 50000.0, "relative_volume": 1.8,
            # retail keys that must NEVER leak into evidence, even though present here:
            "rsi": 71.0, "macd_line": 2.5, "supertrend": 49000.0, "obv_rising": True,
        }
        items = engine.volume_confirmation_evidence(confirmation)
        assert len(items) == 3  # cvd_rising, poc_price, relative_volume
        all_detail_text = " ".join(i.detail.lower() for i in items)
        assert "rsi" not in all_detail_text
        assert "macd" not in all_detail_text
        assert "supertrend" not in all_detail_text
        assert "obv" not in all_detail_text

    def test_cvd_rising_true_is_bullish(self, engine):
        items = engine.volume_confirmation_evidence({"cvd_rising": True, "cvd": 10.0})
        assert items[0].polarity == "bullish"

    def test_cvd_rising_false_is_bearish(self, engine):
        items = engine.volume_confirmation_evidence({"cvd_rising": False, "cvd": -10.0})
        assert items[0].polarity == "bearish"

    def test_none_produces_no_evidence(self, engine):
        assert engine.volume_confirmation_evidence(None) == []
        assert engine.volume_confirmation_evidence({}) == []


class TestInstitutionalBiasAndRiskNotes:
    def test_institutional_bias_notes_passthrough(self, engine):
        items = engine.institutional_bias_evidence(["Funding rate positive - mild bullish crowding."])
        assert items[0].category == ICTEvidenceCategory.INSTITUTIONAL_BIAS
        assert items[0].detail == "Funding rate positive - mild bullish crowding."
        assert items[0].polarity == "neutral"

    def test_risk_notes_passthrough(self, engine):
        items = engine.risk_note_evidence(["Wide stop relative to ATR."])
        assert items[0].category == ICTEvidenceCategory.RISK

    def test_empty_when_none(self, engine):
        assert engine.institutional_bias_evidence(None) == []
        assert engine.risk_note_evidence(None) == []


class TestInvalidationEvidence:
    def test_bullish_uses_protected_low(self, engine):
        items = engine.invalidation_evidence(protected_high=105.0, protected_low=95.0, direction_context="bullish")
        assert "95.0" in items[0].detail
        assert "protected low" in items[0].detail.lower()

    def test_bearish_uses_protected_high(self, engine):
        items = engine.invalidation_evidence(protected_high=105.0, protected_low=95.0, direction_context="bearish")
        assert "105.0" in items[0].detail
        assert "protected high" in items[0].detail.lower()

    def test_no_direction_context_produces_no_evidence(self, engine):
        assert engine.invalidation_evidence(105.0, 95.0, None) == []

    def test_missing_relevant_level_produces_no_evidence(self, engine):
        assert engine.invalidation_evidence(protected_high=105.0, protected_low=None, direction_context="bullish") == []


class TestPolarityValidation:
    def test_invalid_polarity_raises(self):
        with pytest.raises(ValueError):
            ICTEvidenceItem(
                category=ICTEvidenceCategory.RISK, label="x", detail="y", polarity="up", source="test",
            )


class TestCompileOrchestration:
    def test_compile_full_bundle(self, engine):
        swing = SwingPoint(timestamp=_ts(), price=100.0, type=SwingType.HH, index=0, strength=SwingStrength.STRONG, displacement_ratio=1.0)
        bos = StructureBreak(timestamp=_ts(1), type="BOS", direction="bullish", broken_swing=swing, level=101.0, scope=StructureScope.EXTERNAL, displacement_ratio=1.5, is_mss=False)
        level = LiquidityLevel(
            price=99.0, type=LiquidityType.SELLSIDE, swing_points=[], kind=LiquidityKind.EQUAL_LOWS,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG, swept=True,
            sweep_outcome=SweepOutcome.GRAB, swept_at=_ts(), sweep_confirmed=True,
        )
        ob = OrderBlock(
            timestamp=_ts(), high=101.0, low=99.0, type=BlockType.BULLISH_OB, state=OBState.FRESH,
            mitigated=False, displacement_ratio=1.5, caused_bos=True, caused_choch=False,
            related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
            mitigated_at=None, breaker_at=None, invalidated_at=None,
        )
        gap = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.0, type=FVGType.BULLISH, filled=False)
        zone = Zone(top=101.0, bottom=99.0, type=ZoneType.DEMAND, origin_index=0, origin_timestamp=_ts(),
                    displacement_ratio=2.0, state=ZoneState.FRESH, strength=ZoneStrength.STRONG)

        from app.smc.session_engine import SessionEngine
        ctx = SessionEngine().classify_timestamp(pd.Timestamp("2026-01-05 13:00:00"))

        inputs = ICTEvidenceInputs(
            symbol="BTCUSDT", timeframe="15m", as_of=_ts(5), current_price=100.5,
            direction_context="bullish",
            structure_breaks=[bos], protected_low=95.0, protected_high=110.0,
            htf_structure_alignment="aligned_bullish", htf_timeframe_label="4H",
            liquidity_levels=[level], order_blocks=[ob], fvgs=[gap], supply_demand_zones=[zone],
            premium_discount_zone="discount", premium_discount_position=0.25,
            session_context=ctx,
            volume_confirmation={"cvd_rising": True, "cvd": 5.0},
            institutional_bias_notes=["Funding rate positive."],
            risk_notes=["Stop is 1.2x ATR - within normal range."],
        )
        report = engine.compile(inputs)

        assert report.symbol == "BTCUSDT"
        assert len(report.items) > 0
        assert "Bullish BOS" in report.as_labels()
        assert "Strong Order Block" in report.as_labels()
        assert "Demand Zone (Strong)" in report.as_labels()
        assert "Invalidation" in report.as_labels()
        assert len(report.bullish_evidence) > 0
        grouped = report.grouped_by_category()
        assert ICTEvidenceCategory.MARKET_STRUCTURE in grouped
        assert ICTEvidenceCategory.INVALIDATION in grouped

    def test_compile_empty_inputs_produces_empty_report(self, engine):
        report = engine.compile(ICTEvidenceInputs())
        assert report.items == []
        assert report.bullish_evidence == []
        assert report.as_labels() == []


class TestDistinctFromAgentEvidenceEngine:
    """Sanity check: this module must not collide with, or be confused
    with, the pre-existing app.agent.evidence_engine.EvidenceEngine (a
    different responsibility - see this module's docstring)."""

    def test_classes_are_distinct(self):
        from app.agent.evidence_engine import EvidenceEngine as AgentEvidenceEngine
        assert ICTEvidenceEngine is not AgentEvidenceEngine
        assert ICTEvidenceEngine.__module__ != AgentEvidenceEngine.__module__
