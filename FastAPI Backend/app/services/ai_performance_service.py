"""
AI Performance Monitoring (Phase 7, 2026-07-27).

Read-only reporting over data this project already collects and stores on
every `Signal` row (confidence, risk_reward, status, score_breakdown,
timestamps) plus the already-shipped calibration status machinery. Per
Phase 7's explicit instructions:

  - "Do not create a second scoring engine": every number below is a COUNT,
    AVERAGE, or a real elapsed-time difference over already-stored fields -
    nothing here re-scores a trade or re-runs AIScorer.
  - "Everything must reuse existing platform data": symbol/asset-class
    breakdowns reuse the exact same Coin join pattern
    `SignalService.get_stats()` already uses; the trade journal reuses the
    EXISTING `HistoryRepository`/`HistoryQueryParams` (app/repositories/
    history_repository.py, app/schemas/history.py) completely unchanged -
    same filters, same pagination, same sort whitelist. Calibration health
    reuses `app.ai.calibration`'s own `WIN_STATUSES`/`LOSS_STATUSES`/
    `MIN_SAMPLES_PER_GROUP` constants and `app.ai.calibration_profiles`'s
    own `all_profiles()`/`CalibrationProfile.weights_file` - no new
    calibration logic, this only COUNTS what's already there per asset
    type (the same win/loss counting `calibrate_weights_for_profile`
    already does internally, exposed here as a read instead of triggering
    a recalibration). Nothing in app/ai/ was modified for this phase - the
    per-asset-type count query lives here, not there, keeping that package
    exactly as Phase 6 approved it ("architecture frozen").
  - "Never fabricate statistics": every bucket/breakdown below only reports
    real counts; a bucket with zero trades reports `win_rate_pct=None`
    (not 0%), and calibration health is a real "not enough data yet" when
    that's genuinely the case - never an invented readiness signal.

Phase 8 (Beta Hardening, 2026-07-28) added optional `date_from`/`date_to`
filtering to the four analytics functions below, so a caller can scope a
report to a recent window instead of always scanning full trade history.
Same date-range convention already used by the History module
(app/repositories/history_repository.py's `start_date`/`end_date`, filtered
on `Signal.closed_at`) - reused verbatim, not a new convention. Both
parameters remain optional and default to `None` (full history, byte-for-byte
the same behavior as before this change) so this is purely additive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.calibration import LOSS_STATUSES, MIN_SAMPLES_PER_GROUP, WIN_STATUSES
from app.ai.calibration_profiles import all_profiles
from app.core.constants import asset_type_for_symbol
from app.models.coin import Coin
from app.models.signal import Signal, SignalStatus
from app.repositories.history_repository import HistoryRepository
from app.schemas.history import HistoryQueryParams
from app.schemas.performance import (
    AIPerformanceOverviewResponse,
    AssetClassPerformanceStat,
    CalibrationHealthItem,
    ConfidenceBucketStat,
    SymbolPerformanceStat,
    TradeJournalEntry,
    TradeJournalResponse,
)
from app.services.signal_service import SignalService

# Fixed, disclosed confidence bands - deliberately centered on the existing
# min_confidence=85 hard gate (see app/ai/calibration_profiles.py) so a
# reader can see performance just below vs. at/above the live bar.
_CONFIDENCE_BUCKETS = [
    (0, 69, "<70"), (70, 79, "70-79"), (80, 84, "80-84"),
    (85, 89, "85-89"), (90, 94, "90-94"), (95, 100, "95-100"),
]

_COUNTED_STATUSES = WIN_STATUSES + LOSS_STATUSES  # TP_HIT / STOPPED - matches app/ai/calibration.py exactly


def _win_rate(wins: int, total: int) -> Optional[float]:
    return round(wins / total * 100, 2) if total else None


def _date_range_filters(
    date_from: Optional[datetime], date_to: Optional[datetime]
) -> list:
    """Same convention as HistoryRepository._build_filters: filters on
    Signal.closed_at, the real close timestamp of a finished trade - not
    created_at, since these analytics are about trade OUTCOMES. Both
    optional; an empty list here means "no date restriction", identical to
    the pre-Phase-8 behavior."""
    filters = []
    if date_from is not None:
        filters.append(Signal.closed_at >= date_from)
    if date_to is not None:
        filters.append(Signal.closed_at <= date_to)
    return filters


async def get_confidence_bucket_stats(
    session: AsyncSession,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[ConfidenceBucketStat]:
    stmt = select(Signal.confidence, Signal.status).where(
        Signal.status.in_(_COUNTED_STATUSES), *_date_range_filters(date_from, date_to)
    )
    rows = (await session.execute(stmt)).all()

    buckets: List[ConfidenceBucketStat] = []
    for lo, hi, label in _CONFIDENCE_BUCKETS:
        in_bucket = [r for r in rows if lo <= r.confidence <= hi]
        wins = sum(1 for r in in_bucket if r.status in WIN_STATUSES)
        losses = sum(1 for r in in_bucket if r.status in LOSS_STATUSES)
        buckets.append(ConfidenceBucketStat(
            bucket_label=label, trade_count=len(in_bucket), wins=wins, losses=losses,
            win_rate_pct=_win_rate(wins, wins + losses),
        ))
    return buckets


async def get_symbol_performance(
    session: AsyncSession,
    limit: int = 15,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[SymbolPerformanceStat]:
    # A per-symbol win/loss GROUP BY with a portable boolean-sum expression
    # varies across SQL backends, so instead this fetches the (small) set of
    # real closed-trade rows once and aggregates per symbol in Python - the
    # same "aggregate in Python over a real fetch" approach
    # app.ai.calibration.py's own weights_from_samples() already uses.
    raw_stmt = (
        select(Coin.symbol, Signal.status, Signal.confidence, Signal.risk_reward)
        .join(Signal, Signal.coin_id == Coin.id)
        .where(Signal.status.in_(_COUNTED_STATUSES), *_date_range_filters(date_from, date_to))
    )
    rows = (await session.execute(raw_stmt)).all()

    per_symbol: Dict[str, List] = {}
    for r in rows:
        per_symbol.setdefault(r.symbol, []).append(r)

    stats: List[SymbolPerformanceStat] = []
    for symbol, symbol_rows in per_symbol.items():
        total = len(symbol_rows)
        wins = sum(1 for r in symbol_rows if r.status in WIN_STATUSES)
        losses = total - wins
        confidences = [r.confidence for r in symbol_rows if r.confidence is not None]
        rrs = [r.risk_reward for r in symbol_rows if r.risk_reward is not None]
        stats.append(SymbolPerformanceStat(
            symbol=symbol, trade_count=total, wins=wins, losses=losses,
            win_rate_pct=_win_rate(wins, total),
            avg_confidence=round(sum(confidences) / len(confidences), 1) if confidences else None,
            avg_risk_reward=round(sum(rrs) / len(rrs), 2) if rrs else None,
        ))
    stats.sort(key=lambda s: s.trade_count, reverse=True)
    return stats[:limit]


async def get_asset_class_performance(
    session: AsyncSession,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[AssetClassPerformanceStat]:
    stmt = (
        select(Coin.asset_class, Signal.status)
        .join(Signal, Signal.coin_id == Coin.id)
        .where(Signal.status.in_(_COUNTED_STATUSES), *_date_range_filters(date_from, date_to))
    )
    rows = (await session.execute(stmt)).all()

    per_class: Dict[str, List] = {}
    for r in rows:
        per_class.setdefault(r.asset_class, []).append(r.status)

    result: List[AssetClassPerformanceStat] = []
    for asset_class, statuses in per_class.items():
        total = len(statuses)
        wins = sum(1 for s in statuses if s in WIN_STATUSES)
        result.append(AssetClassPerformanceStat(
            asset_class=asset_class, trade_count=total, wins=wins, losses=total - wins,
            win_rate_pct=_win_rate(wins, total),
        ))
    result.sort(key=lambda s: s.trade_count, reverse=True)
    return result


async def get_calibration_health(
    session: AsyncSession,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[CalibrationHealthItem]:
    """Same win/loss counting query `app.ai.calibration.calibrate_weights_for_profile`
    already runs internally (score_breakdown not null, status in WIN/LOSS,
    filtered by asset_type_for_symbol), exposed here as a read-only status
    report per registered profile - never triggers a recalibration.

    Note: date_from/date_to are honored if passed, but the default (None,
    None - full history) is almost always the right choice for THIS
    function specifically, since it reports total accumulated samples
    toward MIN_SAMPLES_PER_GROUP calibration readiness, not a recent-window
    performance snapshot. Exposed for consistency with the other three
    analytics functions above.
    """
    stmt = (
        select(Signal.status, Coin.symbol)
        .join(Coin, Signal.coin_id == Coin.id)
        .where(
            Signal.status.in_(_COUNTED_STATUSES),
            Signal.score_breakdown.is_not(None),
            *_date_range_filters(date_from, date_to),
        )
    )
    rows = (await session.execute(stmt)).all()

    items: List[CalibrationHealthItem] = []
    for asset_type, profile in all_profiles().items():
        matching = [r for r in rows if asset_type_for_symbol(r.symbol) == asset_type]
        wins = sum(1 for r in matching if r.status in WIN_STATUSES)
        losses = sum(1 for r in matching if r.status in LOSS_STATUSES)
        items.append(CalibrationHealthItem(
            asset_type=asset_type,
            wins_collected=wins,
            losses_collected=losses,
            min_required_per_group=MIN_SAMPLES_PER_GROUP,
            is_calibrated=profile.weights_file.exists(),
            ready_to_calibrate=wins >= MIN_SAMPLES_PER_GROUP and losses >= MIN_SAMPLES_PER_GROUP,
        ))
    return items


async def get_performance_overview(
    session: AsyncSession,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> AIPerformanceOverviewResponse:
    """date_from/date_to are optional (Phase 8) and scope confidence_buckets/
    by_symbol/by_asset_class to that closed_at window when provided; omitting
    both reproduces the exact pre-Phase-8 full-history behavior. `stats`
    (SignalService.get_stats(), existing/unmodified) and calibration_health
    intentionally always reflect full history regardless of the filter -
    calibration readiness is a lifetime-accumulation concept, not a
    recent-window one (see get_calibration_health's docstring)."""
    stats = await SignalService(session).get_stats()  # existing, unmodified
    return AIPerformanceOverviewResponse(
        generated_at=datetime.now(timezone.utc),
        stats=stats,
        date_from=date_from,
        date_to=date_to,
        confidence_buckets=await get_confidence_bucket_stats(session, date_from, date_to),
        by_symbol=await get_symbol_performance(session, date_from=date_from, date_to=date_to),
        by_asset_class=await get_asset_class_performance(session, date_from, date_to),
        calibration_health=await get_calibration_health(session),
    )


_EXIT_PRICE_BY_STATUS = {
    SignalStatus.TP_HIT: lambda s: s.take_profit,
    SignalStatus.STOPPED: lambda s: s.stop_loss,
}


def _signal_to_journal_entry(signal: Signal) -> TradeJournalEntry:
    exit_price_fn = _EXIT_PRICE_BY_STATUS.get(signal.status)
    exit_price = exit_price_fn(signal) if exit_price_fn else None

    held_hours = None
    if signal.closed_at is not None and signal.created_at is not None:
        held_hours = round((signal.closed_at - signal.created_at).total_seconds() / 3600.0, 2)

    return TradeJournalEntry(
        id=signal.id,
        symbol=signal.coin.symbol if signal.coin else "UNKNOWN",
        direction=signal.direction.value,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        exit_price=exit_price,
        confidence=signal.confidence,
        risk_reward=signal.risk_reward,
        outcome=signal.status.value,
        reason=signal.reason,
        timeframe=signal.timeframe,
        opened_at=signal.created_at,
        closed_at=signal.closed_at,
        held_duration_hours=held_hours,
    )


async def get_trade_journal(session: AsyncSession, params: HistoryQueryParams) -> TradeJournalResponse:
    """Reuses the EXISTING HistoryRepository.get_history() unchanged (same
    filters/sort/pagination the History module already uses) - this
    function only reshapes each result row into a TradeJournalEntry."""
    repo = HistoryRepository(session)
    signals, total = await repo.get_history(params)
    return TradeJournalResponse(
        total=total, page=params.page, page_size=params.page_size,
        entries=[_signal_to_journal_entry(s) for s in signals],
    )
