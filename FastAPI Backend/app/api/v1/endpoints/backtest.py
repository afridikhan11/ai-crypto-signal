from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user
from app.backtest.calibration import calibrate_weights_from_backtest
from app.backtest.engine import BacktestEngine
from app.schemas.backtest import (
    BacktestCalibrationRequest,
    BacktestCalibrationResponse,
    BacktestRunRequest,
    BacktestRunResponse,
)

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post(
    "/run",
    response_model=BacktestRunResponse,
    summary="Replay historical candles through the live signal-generation pipeline",
    description=(
        "Runs the SAME SignalGenerator/AIScorer/SMC pipeline used live against "
        "historical candles for `symbol`, simulating SL/TP outcomes candle-by-candle. "
        "funding_rate, liquidation pressure, and fundamentals have no free historical "
        "series, so they are passed as neutral/empty at every step - AIScorer already "
        "treats missing values as neutral, so results lean on the SMC structure/"
        "liquidity/order-block/FVG/zone/confirmation categories more than a live "
        "signal would. btc_trend and htf_trend_1h/4h ARE real, computed from real "
        "historical candles with no lookahead."
    ),
)
async def run_backtest(
    payload: BacktestRunRequest,
    _user: str = Depends(get_current_user),
):
    engine = BacktestEngine(payload.symbol, payload.timeframe)
    try:
        result = await engine.run(days=payload.days, min_confidence=payload.min_confidence)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backtest failed: {e}")

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return BacktestRunResponse(
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe,
        days=payload.days,
        summary=result["summary"],
        trades=result["trades"],
    )


@router.post(
    "/calibrate",
    response_model=BacktestCalibrationResponse,
    summary="Recalibrate AIScorer category weights from a multi-symbol backtest sweep",
    description=(
        "Runs the backtest across several symbols, pools every simulated trade's "
        "score_breakdown by win/loss, and feeds them through the same win/loss-spread "
        "formula app.ai.calibration.calibrate_weights() uses for live trades - just with "
        "far more samples, available immediately instead of after weeks of live trading. "
        "Writes the result to the same weights file the live AIScorer loads on startup; "
        "restart the app afterwards for the live scanner to pick up the new weights. "
        "If there still aren't enough combined wins/losses, existing weights are left "
        "untouched and this reports how many more samples are needed."
    ),
)
async def run_backtest_calibration(
    payload: BacktestCalibrationRequest,
    _user: str = Depends(get_current_user),
):
    try:
        result = await calibrate_weights_from_backtest(
            symbols=payload.symbols,
            timeframe=payload.timeframe,
            days=payload.days,
            min_confidence=payload.min_confidence,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Calibration sweep failed: {e}")

    return BacktestCalibrationResponse(**result)
