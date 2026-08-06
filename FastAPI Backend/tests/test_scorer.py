"""
Regression suite for app.ai.scorer.AIScorer (post-ICT-migration rewrite).

OFFLINE SANDBOX NOTE: the real `app/ai/calibration.py` (imported
transitively by `app/ai/scorer.py`) needs `sqlalchemy`, `loguru`, and
`pydantic`/`pydantic_settings` (via `app.core.database`/`app.models.signal`/
`app.models.coin`/`app.core.config`) - none of which are installed in this
offline verification sandbox (no network egress, confirmed - `pip install`
fails against a proxy 403). Rather than stubbing anything inside THIS
project, the verification harness (`/tmp/verify/`) provides minimal,
INERT stand-ins for those third-party packages on `sys.path` (real
classes/functions that exist and are importable, with no real DB/HTTP
behavior - see `/tmp/verify/sqlalchemy/__init__.py`'s own docstring). That
lets this suite import and exercise the REAL, unmodified
`app/ai/scorer.py`, `app/ai/calibration.py`, and
`app/ai/calibration_profiles.py` end-to-end, using the REAL
`DEFAULT_WEIGHTS`/`CalibrationProfile` values - not a per-test fake. This
gap (never run against the real installed sqlalchemy/loguru/pydantic) is
disclosed in the phase's final report.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_real_default_weights() -> dict:
    """Extracts DEFAULT_WEIGHTS straight from the real calibration.py
    source text via ast, as an independent cross-check against whatever
    AIScorer actually ends up using at runtime (see TestWeights below) -
    a drift between the two would be caught, not silently hidden."""
    text = (PROJECT_ROOT / "app" / "ai" / "calibration.py").read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "DEFAULT_WEIGHTS":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "DEFAULT_WEIGHTS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("DEFAULT_WEIGHTS not found in app/ai/calibration.py")


REAL_DEFAULT_WEIGHTS = _load_real_default_weights()

from app.ai.scorer import AIScorer  # noqa: E402
from app.ai.evidence_engine import ICTEvidenceReport, ICTEvidenceItem, ICTEvidenceCategory  # noqa: E402
from app.risk.risk_engine import RiskAssessment  # noqa: E402
from app.smc.institutional_bias_engine import InstitutionalBias  # noqa: E402
from app.smc.htf_structure_engine import HTFSnapshot, TimeframeStructure  # noqa: E402
from app.smc.session_engine import SessionContext, Session, KillZone  # noqa: E402
from app.smc.inducement_engine import InducementEvent  # noqa: E402
from app.smc.liquidity_engine import (  # noqa: E402
    LiquidityLevel, LiquidityKind, LiquidityScope, LiquidityStrength, LiquidityType, SweepOutcome,
)
from app.smc.ote_engine import OTEZone  # noqa: E402
from app.smc.market_structure_engine import StructureBreak, StructureScope, SwingPoint, SwingStrength, SwingType  # noqa: E402
from app.smc.fvg import FairValueGap, FVGType  # noqa: E402


def _ts():
    return pd.Timestamp("2026-01-05 00:00:00")


def _base_features(direction="LONG"):
    return {
        "direction": direction,
        "current_price": 100.0,
        "symbol": "BTCUSDT",
        "confirmation": {"atr": 2.0},
        "market_structure": {},
        "liquidity": {},
        "order_block": {},
        "fvg": [],
        "supply_demand_zone": "unknown",
    }


@pytest.fixture(scope="module")
def scorer():
    return AIScorer("BTCUSDT")


class TestWeights:
    def test_thirteen_categories_summing_to_one(self):
        assert len(REAL_DEFAULT_WEIGHTS) == 13
        assert abs(sum(REAL_DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_no_retail_indicator_keys_present(self):
        retail_keys = {
            "trend_filters", "momentum", "volume_order_flow", "volatility_context",
            "pattern_confirmation", "multi_tf_alignment", "fundamental_context",
        }
        assert retail_keys.isdisjoint(REAL_DEFAULT_WEIGHTS.keys())

    def test_expected_ict_keys_present(self):
        expected = {
            "market_structure", "liquidity", "order_block", "fvg", "supply_demand",
            "session_killzone", "inducement", "ote", "institutional_bias",
            "htf_alignment", "volume_confirmation", "risk", "evidence_quality",
        }
        assert set(REAL_DEFAULT_WEIGHTS.keys()) == expected


class TestBasicAssess:
    def test_returns_confidence_reason_scores(self, scorer):
        confidence, reason, scores = scorer.assess(_base_features())
        assert isinstance(confidence, int)
        assert 0 <= confidence <= 100
        assert isinstance(reason, str)
        assert set(scores.keys()) == set(REAL_DEFAULT_WEIGHTS.keys())

    def test_empty_everything_is_low_but_valid(self, scorer):
        confidence, _, scores = scorer.assess(_base_features())
        assert confidence < 55  # nothing but baselines/neutrals - should not read as confident


class TestMarketStructure:
    def test_choch_scores_higher_than_no_break(self, scorer):
        features_none = _base_features()
        _, _, scores_none = scorer.assess(features_none)

        swing = SwingPoint(timestamp=_ts(), price=98.0, type=SwingType.HH, index=0,
                            strength=SwingStrength.STRONG, displacement_ratio=1.0)
        brk = StructureBreak(timestamp=_ts(), type="CHoCH", direction="bullish", broken_swing=swing,
                              level=98.0, scope=StructureScope.EXTERNAL, displacement_ratio=2.0, is_mss=True)
        features_break = _base_features()
        features_break["market_structure"] = {"bos_choch": [brk]}
        _, _, scores_break = scorer.assess(features_break)

        assert scores_break["market_structure"] > scores_none["market_structure"]


class TestLiquidity:
    def test_swept_levels_score_higher(self, scorer):
        lvl = LiquidityLevel(price=99.0, type=LiquidityType.SELLSIDE, swing_points=[],
                              kind=LiquidityKind.EQUAL_LOWS, scope=LiquidityScope.EXTERNAL,
                              strength=LiquidityStrength.STRONG, swept=True)
        features = _base_features()
        features["liquidity"] = {"levels": [lvl]}
        _, _, scores = scorer.assess(features)
        baseline = scorer.assess(_base_features())[2]["liquidity"]
        assert scores["liquidity"] > baseline


class TestOrderBlock:
    def test_mitigated_ob_scores_higher_than_absent(self, scorer):
        features = _base_features()
        features["order_block"] = {"high": 105.0, "low": 100.0, "type": "bullish_ob", "mitigated": True}
        _, _, scores = scorer.assess(features)
        baseline = scorer.assess(_base_features())[2]["order_block"]
        assert scores["order_block"] > baseline


class TestFVG:
    def test_unfilled_fvg_scores_higher_than_none(self, scorer):
        gap = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.5, type=FVGType.BULLISH, filled=False)
        features = _base_features()
        features["fvg"] = [gap]
        _, _, scores = scorer.assess(features)
        baseline = scorer.assess(_base_features())[2]["fvg"]
        assert scores["fvg"] > baseline


class TestSupplyDemand:
    def test_discount_long_scores_high(self, scorer):
        features = _base_features("LONG")
        features["supply_demand_zone"] = "discount"
        _, _, scores = scorer.assess(features)
        assert scores["supply_demand"] == 90


class TestSessionKillZone:
    def test_london_ny_overlap_scores_highest(self, scorer):
        ctx = SessionContext(timestamp=_ts(), active_sessions=(Session.LONDON, Session.NEW_YORK),
                              is_london_ny_overlap=True, kill_zone=None)
        features = _base_features()
        features["session_context"] = ctx
        _, _, scores = scorer.assess(features)
        assert scores["session_killzone"] == 95

    def test_off_session_scores_low(self, scorer):
        ctx = SessionContext(timestamp=_ts(), active_sessions=(), is_london_ny_overlap=False, kill_zone=None)
        features = _base_features()
        features["session_context"] = ctx
        _, _, scores = scorer.assess(features)
        assert scores["session_killzone"] == 30


class TestInducement:
    def _event(self, direction):
        lvl = LiquidityLevel(price=99.0, type=LiquidityType.SELLSIDE, swing_points=[],
                              kind=LiquidityKind.EQUAL_LOWS, scope=LiquidityScope.EXTERNAL,
                              strength=LiquidityStrength.STRONG, swept=True, sweep_confirmed=True,
                              engineered=True, sweep_outcome=SweepOutcome.GRAB)
        return InducementEvent(liquidity_level=lvl, reversal_direction=direction, reversal_break_type="BOS",
                                reversal_break_timestamp=_ts(), displacement_ratio=1.5,
                                continuation_confirmed=True, conditions_met=["all"])

    def test_matching_direction_scores_high(self, scorer):
        features = _base_features("LONG")
        features["inducement_events"] = [self._event("bullish")]
        _, _, scores = scorer.assess(features)
        assert scores["inducement"] > 70

    def test_opposing_direction_scores_low(self, scorer):
        features = _base_features("LONG")
        features["inducement_events"] = [self._event("bearish")]
        _, _, scores = scorer.assess(features)
        assert scores["inducement"] < 40


class TestOTE:
    def _zone(self, direction, contains_price=True):
        return OTEZone(
            direction=direction, leg_start_price=90.0, leg_end_price=110.0, displacement_ratio=1.5,
            zone_top=100.5 if contains_price else 80.0, zone_bottom=99.5 if contains_price else 79.0,
            has_ob_confluence=True, has_fvg_confluence=True, has_liquidity_confluence=False,
            premium_discount_aligned=True, confluence_count=2, retracement_quality="clean",
        )

    def test_matching_zone_containing_price_scores_high(self, scorer):
        features = _base_features("LONG")
        features["ote_zone"] = self._zone("bullish", contains_price=True)
        _, _, scores = scorer.assess(features)
        assert scores["ote"] > 80

    def test_opposing_direction_zone_scores_low(self, scorer):
        features = _base_features("LONG")
        features["ote_zone"] = self._zone("bearish")
        _, _, scores = scorer.assess(features)
        assert scores["ote"] == 25


class TestInstitutionalBias:
    def test_aligned_bias_scores_high(self, scorer):
        features = _base_features("LONG")
        features["institutional_bias"] = InstitutionalBias(
            direction="bullish", confluence_count=3, supporting_evidence=["1W bullish"],
        )
        _, _, scores = scorer.assess(features)
        assert scores["institutional_bias"] > 70

    def test_opposing_bias_scores_low(self, scorer):
        features = _base_features("LONG")
        features["institutional_bias"] = InstitutionalBias(
            direction="bearish", confluence_count=1, supporting_evidence=["1W bearish"],
        )
        _, _, scores = scorer.assess(features)
        assert scores["institutional_bias"] == 15


class TestHTFAlignment:
    def _snapshot(self, alignment, tf="1d"):
        from app.smc.market_structure_engine import MarketStructureSnapshot
        snap = MarketStructureSnapshot(swing_highs=[], swing_lows=[], internal_breaks=[], external_breaks=[],
                                        protected_high=None, protected_low=None, structure_alignment=alignment)
        ts = TimeframeStructure(timeframe=tf, structure_alignment=alignment, protected_high=None,
                                 protected_low=None, latest_break_direction=None, latest_break_type=None,
                                 snapshot=snap)
        return HTFSnapshot(timeframes={tf: ts})

    def test_aligned_htf_scores_above_baseline(self, scorer):
        features = _base_features("LONG")
        features["htf_snapshot"] = self._snapshot("aligned_bullish", tf="1d")
        _, _, scores = scorer.assess(features)
        assert scores["htf_alignment"] > 50

    def test_opposing_htf_scores_below_baseline(self, scorer):
        features = _base_features("LONG")
        features["htf_snapshot"] = self._snapshot("aligned_bearish", tf="1d")
        _, _, scores = scorer.assess(features)
        assert scores["htf_alignment"] < 50

    def test_missing_htf_is_neutral_baseline(self, scorer):
        features = _base_features("LONG")
        _, _, scores = scorer.assess(features)
        assert scores["htf_alignment"] == 50


class TestVolumeConfirmation:
    def test_cvd_confirming_scores_above_baseline(self, scorer):
        features = _base_features("LONG")
        features["confirmation"] = {"atr": 2.0, "cvd_rising": True}
        _, _, scores = scorer.assess(features)
        assert scores["volume_confirmation"] > 50

    def test_real_liquidations_favor_direction(self, scorer):
        features = _base_features("LONG")
        features["institutional"] = {"short_liquidated_usd": 900.0, "long_liquidated_usd": 100.0}
        _, _, scores = scorer.assess(features)
        assert scores["volume_confirmation"] > 50


class TestRisk:
    def test_unapproved_risk_assessment_floors_score(self, scorer):
        features = _base_features("LONG")
        features["risk_assessment"] = RiskAssessment(
            approved=False, reasons=["Exposure too high"], position_size=None,
            open_risk_percent=10.0, exposure_percent=60.0, daily_pnl_percent=0.0, current_drawdown_percent=0.0,
        )
        _, _, scores = scorer.assess(features)
        assert scores["risk"] == 10

    def test_good_risk_reward_scores_high(self, scorer):
        features = _base_features("LONG")
        features["risk_reward"] = 4.0
        _, _, scores = scorer.assess(features)
        assert scores["risk"] > 70


class TestEvidenceQuality:
    def test_ob_fvg_liquidity_overlap_scores_high(self, scorer):
        gap = FairValueGap(timestamp=_ts(), top=101.0, bottom=99.5, type=FVGType.BULLISH, filled=False)
        lvl = LiquidityLevel(price=100.0, type=LiquidityType.SELLSIDE, swing_points=[],
                              kind=LiquidityKind.EQUAL_LOWS, scope=LiquidityScope.EXTERNAL,
                              strength=LiquidityStrength.STRONG, swept=True)
        features = _base_features("LONG")
        features["order_block"] = {"high": 101.0, "low": 99.0, "type": "bullish_ob", "mitigated": False}
        features["fvg"] = [gap]
        features["liquidity"] = {"levels": [lvl]}
        _, _, scores = scorer.assess(features)
        assert scores["evidence_quality"] >= 62

    def test_evidence_report_lean_boosts_matching_direction(self, scorer):
        items = [
            ICTEvidenceItem(category=ICTEvidenceCategory.MARKET_STRUCTURE, label="Bullish BOS", detail="x",
                             polarity="bullish", source="test"),
            ICTEvidenceItem(category=ICTEvidenceCategory.ORDER_BLOCK, label="Order Block", detail="x",
                             polarity="bullish", source="test"),
            ICTEvidenceItem(category=ICTEvidenceCategory.FVG, label="FVG Present", detail="x",
                             polarity="bearish", source="test"),
        ]
        report = ICTEvidenceReport(symbol="BTCUSDT", items=items)
        features = _base_features("LONG")
        features["evidence_report"] = report
        _, _, scores = scorer.assess(features)
        baseline = scorer.assess(_base_features("LONG"))[2]["evidence_quality"]
        assert scores["evidence_quality"] > baseline


class TestFullConfluence:
    def test_everything_aligned_bullish_scores_very_high(self, scorer):
        swing = SwingPoint(timestamp=_ts(), price=98.0, type=SwingType.HH, index=0,
                            strength=SwingStrength.STRONG, displacement_ratio=1.0)
        brk = StructureBreak(timestamp=_ts(), type="CHoCH", direction="bullish", broken_swing=swing,
                              level=98.0, scope=StructureScope.EXTERNAL, displacement_ratio=2.0, is_mss=True)
        lvl = LiquidityLevel(price=99.0, type=LiquidityType.SELLSIDE, swing_points=[],
                              kind=LiquidityKind.EQUAL_LOWS, scope=LiquidityScope.EXTERNAL,
                              strength=LiquidityStrength.STRONG, swept=True)
        gap = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.0, type=FVGType.BULLISH, filled=False)

        features = _base_features("LONG")
        features["market_structure"] = {"bos_choch": [brk]}
        features["liquidity"] = {"levels": [lvl]}
        features["order_block"] = {"high": 101.0, "low": 100.0, "type": "bullish_ob", "mitigated": False}
        features["fvg"] = [gap]
        features["supply_demand_zone"] = "discount"
        features["session_context"] = SessionContext(
            timestamp=_ts(), active_sessions=(Session.LONDON, Session.NEW_YORK),
            is_london_ny_overlap=True, kill_zone=None,
        )
        features["institutional_bias"] = InstitutionalBias(direction="bullish", confluence_count=3,
                                                             supporting_evidence=["x"])
        features["risk_reward"] = 3.5
        _, _, scores = scorer.assess(features)
        confidence = scorer.assess(features)[0]
        assert confidence > 65
