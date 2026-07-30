from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    symbol: str = Field(..., description="Futures symbol, e.g. BTCUSDT")
    timeframe: str = Field("15m", description="Primary structure timeframe to backtest")
    days: int = Field(30, ge=1, le=90, description="How many days of history to replay")
    min_confidence: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="Override SignalGenerator.MIN_CONFIDENCE for this run only (defaults to the live threshold)",
    )


class BacktestTradeResult(BaseModel):
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: int
    score_breakdown: Optional[Dict[str, float]] = None
    outcome: str
    realized_rr: float
    entry_time: datetime
    exit_time: Optional[datetime] = None


class BacktestSummary(BaseModel):
    total_signals: int
    wins: int
    losses: int
    unresolved: int
    win_rate: float
    avg_realized_rr: float
    by_confidence_bucket: Dict[str, Dict[str, int]]


class BacktestRunResponse(BaseModel):
    symbol: str
    timeframe: str
    days: int
    summary: BacktestSummary
    trades: List[BacktestTradeResult]


class BacktestCalibrationRequest(BaseModel):
    symbols: Optional[List[str]] = Field(
        None,
        description="Symbols to sweep. Defaults to a 10-symbol liquid basket if omitted.",
    )
    timeframe: str = Field("15m", description="Primary structure timeframe to backtest")
    days: int = Field(90, ge=1, le=90, description="How many days of history to replay per symbol")
    min_confidence: Optional[int] = Field(
        None, ge=0, le=100, description="Override SignalGenerator.MIN_CONFIDENCE for this sweep only"
    )


class BacktestCalibrationResponse(BaseModel):
    symbols_used: List[str]
    total_wins: int
    total_losses: int
    total_unresolved: int
    per_symbol_summary: Dict[str, Any]
    weights: Optional[Dict[str, float]]
    calibrated: bool
    message: str
