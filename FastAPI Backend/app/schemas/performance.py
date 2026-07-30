"""
AI Performance Monitoring schemas (Phase 7, 2026-07-27).

Every figure below is a real count/average from already-stored `Signal`
rows (see app/models/signal.py) or the already-shipped calibration status
machinery (app/ai/calibration.py, app/ai/calibration_profiles.py) - see
ai_performance_service.py's module docstring. No score is computed here;
this is read-only aggregation and reporting, per Phase 7's explicit
"do not create a second scoring engine" instruction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.stats import StatsResponse


class ConfidenceBucketStat(BaseModel):
    bucket_label: str = Field(..., description="Fixed confidence band, e.g. '85-89'")
    trade_count: int
    wins: int
    losses: int
    win_rate_pct: Optional[float] = None


class SymbolPerformanceStat(BaseModel):
    symbol: str
    trade_count: int
    wins: int
    losses: int
    win_rate_pct: Optional[float] = None
    avg_confidence: Optional[float] = None
    avg_risk_reward: Optional[float] = None


class AssetClassPerformanceStat(BaseModel):
    asset_class: str
    trade_count: int
    wins: int
    losses: int
    win_rate_pct: Optional[float] = None


class CalibrationHealthItem(BaseModel):
    """One row per registered CalibrationProfile (crypto/gold/silver/oil/forex)
    - see app/ai/calibration_profiles.py's all_profiles(), reused unchanged."""
    asset_type: str
    wins_collected: int
    losses_collected: int
    min_required_per_group: int
    is_calibrated: bool = Field(..., description="Whether a calibrated-weights file already exists for this asset type")
    ready_to_calibrate: bool = Field(..., description="wins_collected AND losses_collected both >= min_required_per_group")


class AIPerformanceOverviewResponse(BaseModel):
    generated_at: datetime
    stats: StatsResponse
    date_from: Optional[datetime] = Field(
        None, description="Phase 8: if set, confidence_buckets/by_symbol/by_asset_class only include trades closed on/after this timestamp. Null means no start restriction (full history)."
    )
    date_to: Optional[datetime] = Field(
        None, description="Phase 8: if set, confidence_buckets/by_symbol/by_asset_class only include trades closed on/before this timestamp. Null means no end restriction (full history)."
    )
    confidence_buckets: List[ConfidenceBucketStat] = Field(default_factory=list)
    by_symbol: List[SymbolPerformanceStat] = Field(default_factory=list)
    by_asset_class: List[AssetClassPerformanceStat] = Field(default_factory=list)
    calibration_health: List[CalibrationHealthItem] = Field(
        default_factory=list,
        description="Always reflects FULL history regardless of date_from/date_to - calibration readiness is a lifetime-accumulation concept, not a recent-window one.",
    )


class TradeJournalEntry(BaseModel):
    """One closed-trade review row. `reason` is copied VERBATIM from the
    stored Signal (same "never invent evidence" discipline as
    app/agent/evidence_engine.py) - never rephrased or summarized."""
    id: uuid.UUID
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: Optional[float] = Field(None, description="take_profit if TP_HIT, stop_loss if STOPPED, else null (CANCELLED/unknown exit)")
    confidence: int
    risk_reward: float
    outcome: str = Field(..., description="Signal.status: TP_HIT | STOPPED | CANCELLED | ACTIVE")
    reason: str = Field(..., description="Verbatim Signal.reason - never rephrased")
    timeframe: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    held_duration_hours: Optional[float] = Field(None, description="Real (closed_at - opened_at) in hours, null while still ACTIVE or if closed_at is missing")


class TradeJournalResponse(BaseModel):
    total: int
    page: int
    page_size: int
    entries: List[TradeJournalEntry] = Field(default_factory=list)
