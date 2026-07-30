"""
Weight calibration for AIScorer.

The AIScorer's category weights started as hand-picked constants with no
grounding in real outcomes. This module lets them be re-derived from actual
closed-trade history instead, once enough of it exists:

  1. SignalGenerator.generate() now saves the AIScorer's raw per-category
     scores (0-100 each) alongside every signal, as Signal.score_breakdown.
  2. calibrate_weights() reads closed trades that have a score_breakdown,
     compares each category's average score in WINS vs LOSSES, and gives
     more weight to categories that actually separate winners from losers.
  3. The result is written to a small JSON file and loaded by AIScorer on
     startup. If there isn't enough closed history yet, calibration is
     skipped and the original hand-picked defaults are used untouched -
     this never silently fabricates a "calibrated" result from noise.
"""
from pathlib import Path
import json
from typing import Dict, Optional

from loguru import logger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.signal import Signal, SignalStatus
from app.models.coin import Coin

DEFAULT_WEIGHTS: Dict[str, float] = {
    # REBUILT (2026-07-29) for the ICT-only AIScorer - see
    # app/ai/scorer.py's module docstring for the full "what was removed,
    # and where it went" disclosure. Every retail-indicator-driven bucket
    # from the previous 13-category set (trend_filters, momentum,
    # volume_order_flow, volatility_context, pattern_confirmation,
    # multi_tf_alignment, fundamental_context) is GONE, not renamed - none
    # of them read anything but ICT engine output anymore. The 13 keys
    # below are exactly this migration phase's required scoring inputs:
    # Market Structure, Liquidity, Order Block, FVG, Supply/Demand,
    # Session/Kill Zone, Inducement, OTE, Institutional Bias, HTF
    # Alignment, Volume Confirmation (CVD/POC/real liquidations only, no
    # OBV), Risk, Evidence Quality. Sums to 1.00.
    # Any file calibrated before this key-set existed fails
    # load_calibrated_weights()'s key-set check and is safely ignored in
    # favor of these defaults - no manual migration needed, same as every
    # previous rebalance here.
    #
    # SMC core (market_structure through supply_demand) keeps the largest
    # combined share (0.14+0.10+0.12+0.06+0.06 = 0.48) since it is both
    # the most foundational ICT read and the most heavily tested/oldest
    # code in this scorer. institutional_bias + htf_alignment together
    # (0.10+0.12 = 0.22) form the next-largest share - real top-down
    # institutional confluence, the entire point of this migration.
    # inducement + ote (0.08 each = 0.16) are strict-AND-gated, high-
    # conviction-when-present signals, weighted accordingly. session_
    # killzone, volume_confirmation, risk, evidence_quality are context/
    # quality modifiers rather than primary directional evidence, so each
    # gets a smaller slice (0.04/0.05/0.03/0.02 = 0.14 combined).
    "market_structure": 0.14,
    "liquidity": 0.10,
    "order_block": 0.12,
    "fvg": 0.06,
    "supply_demand": 0.06,
    "session_killzone": 0.04,
    "inducement": 0.08,
    "ote": 0.08,
    "institutional_bias": 0.10,
    "htf_alignment": 0.12,
    "volume_confirmation": 0.05,
    "risk": 0.03,
    "evidence_quality": 0.02,
}

WIN_STATUSES = [SignalStatus.TP_HIT]
LOSS_STATUSES = [SignalStatus.STOPPED]

WEIGHTS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ai_scorer_weights.json"

# Need at least this many wins AND this many losses with a recorded
# score_breakdown before the win/loss comparison is trusted at all.
MIN_SAMPLES_PER_GROUP = 15

# No category is ever fully zeroed out, even with zero measured discrimination.
WEIGHT_FLOOR = 0.03


async def get_calibration_status() -> Dict[str, object]:
    """
    Lightweight read-only status for the Dashboard AI panel - how much
    closed-trade data has been collected toward calibration, without
    triggering a recalibration (calibrate_weights() actually writes the
    weights file; this just reports counts).
    """
    async with AsyncSessionLocal() as session:
        stmt = select(Signal.status).where(
            Signal.status.in_(WIN_STATUSES + LOSS_STATUSES),
            Signal.score_breakdown.is_not(None),
        )
        rows = (await session.execute(stmt)).all()

    wins = sum(1 for r in rows if r.status in WIN_STATUSES)
    losses = sum(1 for r in rows if r.status in LOSS_STATUSES)

    return {
        "wins_collected": wins,
        "losses_collected": losses,
        "min_required_per_group": MIN_SAMPLES_PER_GROUP,
        "is_calibrated": WEIGHTS_FILE.exists(),
    }


def load_calibrated_weights() -> Optional[Dict[str, float]]:
    """Load previously-calibrated weights from disk, if present and valid."""
    if not WEIGHTS_FILE.exists():
        return None
    try:
        data = json.loads(WEIGHTS_FILE.read_text())
        if set(data.keys()) != set(DEFAULT_WEIGHTS.keys()):
            logger.warning("Calibrated AI scorer weights file has stale/mismatched keys, ignoring.")
            return None
        total = sum(data.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in data.items()}
    except Exception as e:
        logger.warning(f"Could not load calibrated AI scorer weights: {e}")
        return None


def weights_from_samples(
    wins: list[Dict[str, float]], losses: list[Dict[str, float]]
) -> Dict[str, float]:
    """
    Pure win/loss-spread weight computation - for each category, compares
    the average recorded score in winning samples vs losing samples. A
    category whose score is consistently higher in wins than losses is a
    real predictor and gets more weight; a category with no separation (or
    an inverted one) keeps only the floor weight.

    Shared by calibrate_weights() (live closed-trade history) and
    app.backtest.calibration.calibrate_weights_from_backtest() (samples
    generated by replaying historical candles) so both sources of evidence
    are turned into weights the exact same way.
    """
    raw_weights: Dict[str, float] = {}
    for category in DEFAULT_WEIGHTS:
        win_scores = [w.get(category) for w in wins if w.get(category) is not None]
        loss_scores = [l.get(category) for l in losses if l.get(category) is not None]
        if not win_scores or not loss_scores:
            raw_weights[category] = WEIGHT_FLOOR
            continue
        avg_win = sum(win_scores) / len(win_scores)
        avg_loss = sum(loss_scores) / len(loss_scores)
        # Only credit a category when it's higher in wins than losses (the
        # intended direction for every category as currently scored) - an
        # inverted category just gets floored instead of rewarded for
        # "predicting" the opposite of what it's supposed to.
        spread = max(avg_win - avg_loss, 0.0)
        raw_weights[category] = spread + WEIGHT_FLOOR

    total = sum(raw_weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in raw_weights.items()}


def write_weights_file(weights: Dict[str, float]) -> None:
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_FILE.write_text(json.dumps(weights, indent=2))


async def calibrate_weights() -> Optional[Dict[str, float]]:
    """
    Re-derive AIScorer category weights from LIVE closed-trade outcomes.

    Returns None (and leaves any existing weights file untouched) if there
    isn't enough closed history with score_breakdown yet to trust the
    comparison - call this again later as more trades close. For a much
    faster source of calibration evidence, see
    app.backtest.calibration.calibrate_weights_from_backtest(), which
    generates samples by replaying historical candles instead of waiting
    for live trades to close.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(Signal.status, Signal.score_breakdown).where(
            Signal.status.in_(WIN_STATUSES + LOSS_STATUSES),
            Signal.score_breakdown.is_not(None),
        )
        rows = (await session.execute(stmt)).all()

    wins = [r.score_breakdown for r in rows if r.status in WIN_STATUSES and r.score_breakdown]
    losses = [r.score_breakdown for r in rows if r.status in LOSS_STATUSES and r.score_breakdown]

    if len(wins) < MIN_SAMPLES_PER_GROUP or len(losses) < MIN_SAMPLES_PER_GROUP:
        logger.info(
            f"AI scorer calibration skipped: only {len(wins)} wins / {len(losses)} losses "
            f"with recorded score_breakdown (need >= {MIN_SAMPLES_PER_GROUP} of each). "
            f"Using existing/default weights."
        )
        return None

    weights = weights_from_samples(wins, losses)
    write_weights_file(weights)

    logger.success(
        f"AI scorer weights calibrated from {len(wins)} wins / {len(losses)} losses: {weights}"
    )
    return weights


# ==========================================================================
# PER-ASSET-CLASS CALIBRATION (2026-07-26 addition)
# ==========================================================================
# Everything above this line (DEFAULT_WEIGHTS, WEIGHTS_FILE,
# load_calibrated_weights, calibrate_weights, get_calibration_status) is
# UNCHANGED and still works exactly as before - the Dashboard's AI panel
# (app/api/v1/endpoints/dashboard.py) and the backtest calibration feature
# both keep using this single global path with zero behavior change.
#
# The functions below are a purely ADDITIVE, parallel path used by
# AIScorer once it was made asset-aware (see app/ai/calibration_profiles.py
# and app/ai/scorer.py): each CalibrationProfile gets its own calibrated-
# weights file, and calibrate_weights_for_profile() only looks at closed
# trades whose symbol maps to THAT profile's asset type - so calibrating
# Gold's weights can never be influenced by crypto's trade history (which
# is what the single old WEIGHTS_FILE would otherwise do, since it has no
# asset-type awareness at all).
# ==========================================================================


def load_calibrated_weights_for_profile(profile) -> Optional[Dict[str, float]]:
    """Same loading/validation logic as load_calibrated_weights(), but reads
    `profile.weights_file` instead of the single global WEIGHTS_FILE."""
    if not profile.weights_file.exists():
        return None
    try:
        data = json.loads(profile.weights_file.read_text())
        if set(data.keys()) != set(DEFAULT_WEIGHTS.keys()):
            logger.warning(
                f"Calibrated AI scorer weights file for asset_type={profile.asset_type} "
                "has stale/mismatched keys, ignoring."
            )
            return None
        total = sum(data.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in data.items()}
    except Exception as e:
        logger.warning(f"Could not load calibrated weights for asset_type={profile.asset_type}: {e}")
        return None


def write_weights_file_for_profile(profile, weights: Dict[str, float]) -> None:
    profile.weights_file.parent.mkdir(parents=True, exist_ok=True)
    profile.weights_file.write_text(json.dumps(weights, indent=2))


async def calibrate_weights_for_profile(profile) -> Optional[Dict[str, float]]:
    """
    Same win/loss-spread calibration as calibrate_weights(), scoped to only
    the closed trades whose symbol belongs to `profile.asset_type` (see
    app.core.constants.asset_type_for_symbol). Returns None (writes
    nothing) if this asset type doesn't have enough closed history yet -
    for Gold/Silver/Oil today that means every trade is filtered out and
    this simply returns None every time, same as it always has: no
    fabricated calibration from zero real trades, matching this project's
    "never hallucinate" rule. AIScorer falls back to the profile's
    DEFAULT_WEIGHTS-derived defaults in that case (see scorer.py), NOT the
    crypto profile's calibrated weights - the two are never mixed.
    """
    from app.core.constants import asset_type_for_symbol  # local import: avoids a
    # calibration_profiles.py <-> calibration.py import cycle (calibration_profiles
    # imports DEFAULT_WEIGHTS from this module).

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Signal.status, Signal.score_breakdown, Coin.symbol)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(
                Signal.status.in_(WIN_STATUSES + LOSS_STATUSES),
                Signal.score_breakdown.is_not(None),
            )
        )
        rows = (await session.execute(stmt)).all()

    rows = [r for r in rows if asset_type_for_symbol(r.symbol) == profile.asset_type]
    wins = [r.score_breakdown for r in rows if r.status in WIN_STATUSES and r.score_breakdown]
    losses = [r.score_breakdown for r in rows if r.status in LOSS_STATUSES and r.score_breakdown]

    if len(wins) < MIN_SAMPLES_PER_GROUP or len(losses) < MIN_SAMPLES_PER_GROUP:
        logger.info(
            f"AI scorer calibration skipped for asset_type={profile.asset_type}: only "
            f"{len(wins)} wins / {len(losses)} losses with recorded score_breakdown "
            f"(need >= {MIN_SAMPLES_PER_GROUP} of each). Using existing/default weights."
        )
        return None

    weights = weights_from_samples(wins, losses)
    write_weights_file_for_profile(profile, weights)

    logger.success(
        f"AI scorer weights calibrated for asset_type={profile.asset_type} from "
        f"{len(wins)} wins / {len(losses)} losses: {weights}"
    )
    return weights


async def calibrate_all_profiles() -> Dict[str, Optional[Dict[str, float]]]:
    """Runs calibrate_weights_for_profile() for every registered
    CalibrationProfile (crypto/gold/silver/oil/forex) - called from
    app.main's startup alongside the existing global calibrate_weights(),
    additive, never replacing it."""
    from app.ai.calibration_profiles import all_profiles

    results: Dict[str, Optional[Dict[str, float]]] = {}
    for asset_type, profile in all_profiles().items():
        results[asset_type] = await calibrate_weights_for_profile(profile)
    return results
