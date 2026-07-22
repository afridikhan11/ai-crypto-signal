from __future__ import annotations

from pydantic import BaseModel


class StatsResponse(BaseModel):
    total_signals: int
    active_signals: int
    closed_signals: int
    win_rate: float
    avg_confidence: float
    avg_risk_reward: float
    best_performing_symbol: str