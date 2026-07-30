"""
Regression suite for app.ai.calibration's category-agnostic
weights_from_samples()/DEFAULT_WEIGHTS logic.

UPDATED 2026-07-29 (ICT migration final phase): weights_from_samples()
itself is UNCHANGED production code - it has always iterated generically
over `for category in DEFAULT_WEIGHTS`, with no category name hardcoded
inside app/ai/calibration.py. Only the SAMPLE category names used by these
tests are updated here, from the old retail-mixed category set
(market_structure/liquidity_sweep/order_block_quality/fvg_presence/
supply_demand_zone/confirmation_alignment/institutional_filters/
fundamental_context/confluence) to the new ICT-only set AIScorer now
produces (see app/ai/scorer.py) - the exact same "does this pure
win/loss-spread math work correctly" behavior is still being tested,
just against real current category names instead of retired ones.
"""
from app.ai.calibration import DEFAULT_WEIGHTS, weights_from_samples


def test_default_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_default_weights_match_scorer_categories():
    expected = {
        "market_structure", "liquidity", "order_block", "fvg", "supply_demand",
        "session_killzone", "inducement", "ote", "institutional_bias",
        "htf_alignment", "volume_confirmation", "risk", "evidence_quality",
    }
    assert set(DEFAULT_WEIGHTS.keys()) == expected


def test_weights_from_samples_rewards_discriminating_category():
    flat = {
        "liquidity": 50, "order_block": 60, "fvg": 40, "supply_demand": 55,
        "session_killzone": 65, "inducement": 50, "ote": 50,
        "institutional_bias": 50, "htf_alignment": 50, "volume_confirmation": 50,
        "risk": 50, "evidence_quality": 50,
    }
    wins = [{"market_structure": 90, **flat} for _ in range(20)]
    losses = [{"market_structure": 40, **flat} for _ in range(20)]

    weights = weights_from_samples(wins, losses)

    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["market_structure"] > weights["liquidity"]
    # categories with zero win/loss spread all land at the same floor weight
    assert abs(weights["liquidity"] - weights["order_block"]) < 1e-9


def test_weights_from_samples_handles_missing_category():
    wins = [{"market_structure": 80} for _ in range(16)]
    losses = [{"market_structure": 30} for _ in range(16)]
    weights = weights_from_samples(wins, losses)
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["fvg"] < weights["market_structure"]


def test_weights_from_samples_empty_yields_uniform_floor_weights():
    # With zero samples, every category floors at WEIGHT_FLOOR and gets
    # normalized - since all categories floor equally, the result is
    # uniform (not DEFAULT_WEIGHTS, which has no data-free "reasonable
    # default" claim to make - it just means "we have no evidence yet").
    weights = weights_from_samples([], [])
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    values = list(weights.values())
    assert all(abs(v - values[0]) < 1e-9 for v in values)
