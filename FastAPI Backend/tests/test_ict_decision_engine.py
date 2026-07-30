"""Regression suite for app.ai.ict_decision_engine."""
import pandas as pd
import pytest

from app.ai.ict_decision_engine import (
    ICT_COMPONENT_CHECKLIST,
    BlockingReason,
    DecisionInputs,
    DecisionType,
    ICTDecisionEngine,
    RejectionGate,
    RiskLevel,
)
from app.ai.evidence_engine import (
    ICTEvidenceCategory,
    ICTEvidenceItem,
    ICTEvidenceReport,
)


ALL_COMPONENTS = {c[0] for c in ICT_COMPONENT_CHECKLIST}


def _item(category, label, polarity, detail="detail"):
    return ICTEvidenceItem(
        category=category, label=label, detail=detail, polarity=polarity, source="test",
    )


def _full_report():
    return ICTEvidenceReport(symbol="BTCUSDT", items=[
        _item(ICTEvidenceCategory.MARKET_STRUCTURE, "Bullish BOS", "bullish"),
        _item(ICTEvidenceCategory.ORDER_BLOCK, "Strong Order Block", "bullish"),
        _item(ICTEvidenceCategory.FVG, "FVG Present", "bearish"),
        _item(ICTEvidenceCategory.SESSION, "London Kill Zone", "neutral"),
        _item(ICTEvidenceCategory.RISK, "Risk Note", "neutral", detail="Volatility elevated."),
        _item(ICTEvidenceCategory.INVALIDATION, "Invalidation", "neutral",
              detail="Break below the protected low (95.0) invalidates this bullish bias."),
    ])


def _all_present_inputs(**overrides):
    base = dict(
        symbol="BTCUSDT",
        direction="LONG",
        confidence=88,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        risk_reward=3.0,
        timeframe="15m",
    )
    for component in ALL_COMPONENTS:
        base[f"has_{component}"] = True
    base.update(overrides)
    return DecisionInputs(**base)


@pytest.fixture(scope="module")
def engine():
    return ICTDecisionEngine()


class TestDecisionType:
    def test_no_blocking_reasons_yields_trade(self, engine):
        decision = engine.decide(_all_present_inputs())
        assert decision.decision is DecisionType.LONG
        assert decision.is_tradeable is True
        assert decision.why_no_trade == []

    def test_short_direction_respected(self, engine):
        decision = engine.decide(_all_present_inputs(direction="SHORT"))
        assert decision.decision is DecisionType.SHORT

    def test_any_blocking_reason_yields_no_trade(self, engine):
        decision = engine.decide(_all_present_inputs(blocking_reasons=[
            BlockingReason(gate=RejectionGate.MIN_CONFIDENCE, detail="too low",
                           measured=60.0, threshold=85.0),
        ]))
        assert decision.decision is DecisionType.NO_TRADE
        assert decision.is_tradeable is False
        assert decision.blocking_gates == ["min_confidence"]

    def test_no_direction_yields_no_trade_with_synthesized_gate(self, engine):
        decision = engine.decide(_all_present_inputs(direction=None))
        assert decision.decision is DecisionType.NO_TRADE
        assert RejectionGate.NO_STRUCTURE_BREAK.value in decision.blocking_gates

    def test_multiple_gates_all_reported(self, engine):
        decision = engine.decide(_all_present_inputs(blocking_reasons=[
            BlockingReason(gate=RejectionGate.MIN_CONFIDENCE, detail="a"),
            BlockingReason(gate=RejectionGate.MIN_RISK_REWARD, detail="b"),
            BlockingReason(gate=RejectionGate.ENTRY_VALIDATION, detail="c"),
        ]))
        assert len(decision.why_no_trade) == 3
        assert set(decision.blocking_gates) == {
            "min_confidence", "min_risk_reward", "entry_validation",
        }


class TestWhyLongWhyShort:
    def test_both_cases_populated_from_real_evidence(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        assert any("Bullish BOS" in line for line in decision.why_long)
        assert any("Strong Order Block" in line for line in decision.why_long)
        # The counter-case is shown even on a LONG decision - see module docstring.
        assert any("FVG Present" in line for line in decision.why_short)

    def test_counter_case_visible_on_a_taken_trade(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        assert decision.decision is DecisionType.LONG
        assert decision.why_short, "bearish evidence must still be reported on a LONG"

    def test_non_directional_categories_excluded_from_both_cases(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        joined = " ".join(decision.why_long + decision.why_short)
        assert "London Kill Zone" not in joined
        assert "Invalidation" not in joined

    def test_no_evidence_report_yields_empty_cases(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=None))
        assert decision.why_long == []
        assert decision.why_short == []


class TestMissingEvidence:
    def test_everything_present_means_nothing_missing(self, engine):
        decision = engine.decide(_all_present_inputs())
        assert decision.missing_evidence == []

    def test_absent_component_is_reported_with_reason(self, engine):
        decision = engine.decide(_all_present_inputs(has_ote=False))
        missing = {m.component for m in decision.missing_evidence}
        assert missing == {"ote"}
        item = decision.missing_evidence[0]
        assert item.label == "Optimal Trade Entry"
        assert item.why_it_matters

    def test_nothing_detected_reports_every_component(self, engine):
        decision = engine.decide(DecisionInputs(symbol="BTCUSDT", direction=None))
        assert {m.component for m in decision.missing_evidence} == ALL_COMPONENTS

    def test_every_checklist_entry_has_a_reason(self):
        for component, label, why in ICT_COMPONENT_CHECKLIST:
            assert component and label and why
            assert len(why) > 20, f"{component} needs a real explanation"


class TestScoreFactors:
    def test_strong_categories_become_supporting_factors(self, engine):
        decision = engine.decide(_all_present_inputs(score_breakdown={
            "market_structure": 92.0, "liquidity": 78.0, "ote": 50.0, "fvg": 22.0,
        }))
        joined = " ".join(decision.supporting_factors)
        assert "Market Structure" in joined
        assert "Liquidity" in joined

    def test_weak_categories_become_detracting_factors(self, engine):
        decision = engine.decide(_all_present_inputs(score_breakdown={
            "market_structure": 92.0, "fvg": 22.0,
        }))
        assert any("Fvg" in f for f in decision.detracting_factors)

    def test_middling_categories_cited_neither_way(self, engine):
        decision = engine.decide(_all_present_inputs(score_breakdown={"ote": 52.0}))
        assert decision.supporting_factors == []
        assert decision.detracting_factors == []

    def test_supporting_factors_sorted_strongest_first(self, engine):
        decision = engine.decide(_all_present_inputs(score_breakdown={
            "liquidity": 70.0, "market_structure": 95.0, "order_block": 80.0,
        }))
        assert "Market Structure" in decision.supporting_factors[0]

    def test_no_breakdown_yields_no_factors(self, engine):
        decision = engine.decide(_all_present_inputs(score_breakdown=None))
        assert decision.supporting_factors == []
        assert decision.detracting_factors == []


class TestRisk:
    def test_high_rr_is_low_risk(self, engine):
        decision = engine.decide(_all_present_inputs(risk_reward=3.0))
        assert decision.risk.level is RiskLevel.LOW

    def test_mid_rr_is_medium_risk(self, engine):
        decision = engine.decide(_all_present_inputs(risk_reward=2.0))
        assert decision.risk.level is RiskLevel.MEDIUM

    def test_low_rr_is_high_risk(self, engine):
        decision = engine.decide(_all_present_inputs(risk_reward=1.0))
        assert decision.risk.level is RiskLevel.HIGH

    def test_unknown_rr_is_unknown_risk(self, engine):
        decision = engine.decide(_all_present_inputs(risk_reward=None))
        assert decision.risk.level is RiskLevel.UNKNOWN

    def test_risk_notes_merged_from_caller_and_evidence(self, engine):
        decision = engine.decide(_all_present_inputs(
            risk_notes=["Caller supplied note."], evidence_report=_full_report(),
        ))
        assert "Caller supplied note." in decision.risk.notes
        assert any("Volatility elevated." in n for n in decision.risk.notes)

    def test_prices_carried_through(self, engine):
        decision = engine.decide(_all_present_inputs())
        assert decision.risk.entry_price == 100.0
        assert decision.risk.stop_loss == 95.0
        assert decision.risk.take_profit == 115.0


class TestInvalidation:
    def test_prefers_evidence_engine_invalidation(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        assert "protected low" in decision.invalidation
        assert decision.invalidation_level == 95.0

    def test_falls_back_to_stop_loss_wording(self, engine):
        decision = engine.decide(_all_present_inputs(evidence_report=None))
        assert "below 95.0" in decision.invalidation
        assert decision.invalidation_level == 95.0

    def test_short_uses_above_wording(self, engine):
        decision = engine.decide(_all_present_inputs(direction="SHORT", stop_loss=105.0))
        assert "above 105.0" in decision.invalidation

    def test_no_stop_loss_yields_no_invalidation_text(self, engine):
        decision = engine.decide(_all_present_inputs(stop_loss=None, evidence_report=None))
        assert decision.invalidation is None


class TestSerialization:
    def test_to_dict_is_json_serializable(self, engine):
        import json
        decision = engine.decide(_all_present_inputs(
            evidence_report=_full_report(),
            score_breakdown={"market_structure": 92.0, "fvg": 20.0},
            has_ote=False,
            blocking_reasons=[BlockingReason(
                gate=RejectionGate.MIN_CONFIDENCE, detail="d", measured=1.0, threshold=2.0)],
        ))
        payload = json.dumps(decision.to_dict())
        assert "min_confidence" in payload
        assert "Optimal Trade Entry" in payload

    def test_to_dict_has_every_explainability_field(self, engine):
        d = engine.decide(_all_present_inputs()).to_dict()
        for key in (
            "decision", "confidence", "why_long", "why_short", "why_no_trade",
            "evidence", "supporting_factors", "detracting_factors",
            "missing_evidence", "risk", "invalidation",
        ):
            assert key in d, f"missing explainability field: {key}"

    def test_summary_reports_gates_on_no_trade(self, engine):
        decision = engine.decide(_all_present_inputs(blocking_reasons=[
            BlockingReason(gate=RejectionGate.HTF_OPPOSITION, detail="x"),
        ]))
        assert "NO TRADE" in decision.summary()
        assert "htf_opposition" in decision.summary()

    def test_summary_reports_direction_on_trade(self, engine):
        assert "LONG" in engine.decide(_all_present_inputs()).summary()


class TestDeterminism:
    def test_same_inputs_produce_equal_decisions(self, engine):
        a = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        b = engine.decide(_all_present_inputs(evidence_report=_full_report()))
        assert a.to_dict() == b.to_dict()
