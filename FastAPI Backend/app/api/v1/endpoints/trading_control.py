"""
Auto Trading Control Panel - backend control surface (2026-07-30).

Every endpoint here either flips one of the two persisted switches in
app/services/trading_settings.py (auto_trading_enabled, engine_run_state),
reads already-existing state (scanner/monitor/RiskEngine/SignalService), or
delegates a bulk action to app/services/trading_control_service.py, which
itself only loops over already-existing, already-tested primitives
(BinanceTradingService.close_position/.cancel_order). No new trading
decision, no new order-placing logic, and no ICT/AI/Scanner change lives in
this file - it is a control/visualization layer over the production
backend, exactly matching what the WPF Auto Trading Control panel is
allowed to be.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.ai.calibration import get_calibration_status
from app.api.dependencies import get_current_user, get_signal_service
from app.risk.risk_engine import RiskEngine
from app.smc.session_engine import SessionEngine
from app.schemas.trading_control import (
    AutoTradingToggleResponse,
    BulkActionItemResponse,
    BulkActionResponse,
    EngineActionResponse,
    KillSwitchResponse,
    LogEntryResponse,
    LogsResponse,
    RiskLimitsResponse,
    SessionStatusResponse,
    TradingStatusResponse,
)
from app.services import binance_credentials, trading_control_service
from app.services.signal_service import SignalService
from app.services.trading_settings import (
    get_auto_trading_enabled,
    get_engine_run_state,
    get_entry_mode,
    set_auto_trading_enabled,
    set_engine_run_state,
)
from app.core.log_buffer import get_recent_logs

router = APIRouter(prefix="/trading", tags=["trading-control"])

_RISK_LIMITS = RiskEngine().limits  # stateless, defaults - see app/risk/risk_engine.py
_SESSION_ENGINE = SessionEngine()  # stateless - see app/smc/session_engine.py
REFERENCE_SYMBOL = "BTCUSDT"  # same reference symbol Dashboard's Market panel already uses


def _bulk_action_to_response(result) -> BulkActionResponse:
    return BulkActionResponse(
        action=result.action,
        environment=result.environment,
        attempted=result.attempted,
        succeeded=result.succeeded,
        items=[
            BulkActionItemResponse(symbol=i.symbol, success=i.success, detail=i.detail, order_id=i.order_id)
            for i in result.items
        ],
        message=result.message,
    )


def _build_session_status(request: Request) -> SessionStatusResponse:
    scanner = getattr(request.app.state, "scanner", None)
    if scanner is None:
        return SessionStatusResponse(reference_symbol=REFERENCE_SYMBOL, message="Scanner not started yet.")

    df = scanner.data_manager.get_dataframe(REFERENCE_SYMBOL, "15m", limit=10)
    if df is None or df.empty:
        return SessionStatusResponse(reference_symbol=REFERENCE_SYMBOL, message="No candle data yet.")

    ctx = _SESSION_ENGINE.latest_context(df)
    if ctx is None:
        return SessionStatusResponse(reference_symbol=REFERENCE_SYMBOL, message="Could not classify the current session yet.")

    return SessionStatusResponse(
        reference_symbol=REFERENCE_SYMBOL,
        active_sessions=[s.value for s in ctx.active_sessions],
        in_kill_zone=ctx.in_kill_zone,
        kill_zone=ctx.kill_zone.value if ctx.kill_zone else None,
        is_london_ny_overlap=ctx.is_london_ny_overlap,
    )


@router.get(
    "/status",
    response_model=TradingStatusResponse,
    summary="Aggregated Auto Trading Control Panel status",
    description=(
        "One combined read for the control panel: environment, the two control "
        "switches (auto_trading_enabled, engine_run_state), Binance/Scanner/Signal "
        "Monitor/Risk Engine health, the configured risk limits plus current portfolio "
        "utilization, today's PnL, win rate, and signal counts. Everything here is a "
        "real read from existing backend state - nothing is computed or invented here."
    ),
)
async def get_trading_status(
    request: Request,
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    creds = binance_credentials.load_credentials()
    has_credentials = creds is not None
    environment = ("testnet" if creds.get("testnet") else "mainnet") if creds else None

    scanner = getattr(request.app.state, "scanner", None)
    monitor = getattr(request.app.state, "signal_monitor", None)

    portfolio_risk = await service.get_portfolio_risk()
    daily_loss = await service.get_daily_loss()
    stats = await service.get_stats()
    counts = await service.get_signal_counts_for_control_panel()

    # AI Status - reuses the exact same calibration read the Dashboard's AI
    # panel already uses (app.ai.calibration.get_calibration_status), never
    # a second implementation.
    calibration = await get_calibration_status()
    ai_calibrated = bool(calibration["is_calibrated"])
    ai_status_message = (
        f"Calibrated ({calibration['wins_collected']}W / {calibration['losses_collected']}L closed trades)."
        if ai_calibrated
        else f"Not yet calibrated - {calibration['wins_collected']}W / {calibration['losses_collected']}L collected, "
             f"{calibration['min_required_per_group']} of each needed. Using default weights until then."
    )

    return TradingStatusResponse(
        has_credentials=has_credentials,
        environment=environment,
        auto_trading_enabled=get_auto_trading_enabled(),
        engine_run_state=get_engine_run_state(),
        binance_connected=bool(scanner.data_manager.is_connected) if scanner is not None else False,
        scanner_running=bool(scanner.is_running) if scanner is not None else False,
        signal_monitor_running=bool(monitor.is_running) if monitor is not None else False,
        risk_engine_available=portfolio_risk.account_balance is not None,
        ai_calibrated=ai_calibrated,
        ai_status_message=ai_status_message,
        risk_limits=RiskLimitsResponse(
            max_risk_percent_per_trade=_RISK_LIMITS.max_risk_percent_per_trade,
            max_open_risk_percent=_RISK_LIMITS.max_open_risk_percent,
            max_daily_loss_percent=_RISK_LIMITS.max_daily_loss_percent,
            max_exposure_percent=_RISK_LIMITS.max_exposure_percent,
            max_drawdown_percent=_RISK_LIMITS.max_drawdown_percent,
        ),
        portfolio_open_risk_percent=portfolio_risk.total_risk_percent_of_balance,
        daily_pnl_usd=daily_loss.realized_pnl_usd,
        daily_pnl_percent=daily_loss.loss_percent_of_balance,
        daily_loss_breached=daily_loss.breached,
        win_rate=stats.win_rate,
        total_signals=stats.total_signals,
        active_trades=counts["active_trades"],
        pending_signals=counts["pending_signals"],
        pending_entries=counts["pending_entries"],
        executed_signals=counts["executed_signals"],
        entry_mode=get_entry_mode(),
        session=_build_session_status(request),
    )


@router.post(
    "/auto-trading/enable",
    response_model=AutoTradingToggleResponse,
    summary="Turn Auto Trading ON - allow POST /trading/execute to place real orders",
)
async def enable_auto_trading(_user: str = Depends(get_current_user)):
    set_auto_trading_enabled(True)
    return AutoTradingToggleResponse(auto_trading_enabled=True, message="Auto Trading is ON. Signals can be executed.")


@router.post(
    "/auto-trading/disable",
    response_model=AutoTradingToggleResponse,
    summary="Turn Auto Trading OFF - block POST /trading/execute (existing positions untouched)",
)
async def disable_auto_trading(_user: str = Depends(get_current_user)):
    set_auto_trading_enabled(False)
    return AutoTradingToggleResponse(
        auto_trading_enabled=False,
        message="Auto Trading is OFF. No new signal can be executed until turned back on. Existing positions/orders are untouched.",
    )


@router.post(
    "/engine/start",
    response_model=EngineActionResponse,
    summary="Start the scanner + signal monitor (resume new signal generation and trade management)",
)
async def start_engine(_user: str = Depends(get_current_user)):
    set_engine_run_state("running")
    return EngineActionResponse(engine_run_state="running", message="Engine running - scanning for new signals and managing open trades.")


@router.post(
    "/engine/pause",
    response_model=EngineActionResponse,
    summary="Pause the scanner + signal monitor (no new signals, no trade management this tick)",
)
async def pause_engine(_user: str = Depends(get_current_user)):
    set_engine_run_state("paused")
    return EngineActionResponse(
        engine_run_state="paused",
        message="Engine paused. Real orders already resting on Binance for executed signals are untouched.",
    )


@router.post(
    "/engine/stop",
    response_model=EngineActionResponse,
    summary="Stop the scanner + signal monitor",
)
async def stop_engine(_user: str = Depends(get_current_user)):
    set_engine_run_state("stopped")
    return EngineActionResponse(
        engine_run_state="stopped",
        message="Engine stopped. Real orders already resting on Binance for executed signals are untouched.",
    )


@router.post(
    "/engine/resume",
    response_model=EngineActionResponse,
    summary="Resume the scanner + signal monitor after a pause",
)
async def resume_engine(_user: str = Depends(get_current_user)):
    set_engine_run_state("running")
    return EngineActionResponse(engine_run_state="running", message="Engine resumed.")


@router.post(
    "/close-all-positions",
    response_model=BulkActionResponse,
    summary="Close every open Futures position with a reduceOnly MARKET order each",
)
async def close_all_positions(_user: str = Depends(get_current_user)):
    result = await trading_control_service.close_all_positions()
    return _bulk_action_to_response(result)


@router.post(
    "/cancel-all-orders",
    response_model=BulkActionResponse,
    summary="Cancel every currently-open order account-wide",
)
async def cancel_all_orders(_user: str = Depends(get_current_user)):
    result = await trading_control_service.cancel_all_orders()
    return _bulk_action_to_response(result)


@router.post(
    "/kill-switch",
    response_model=KillSwitchResponse,
    summary="EMERGENCY: disable Auto Trading, stop the engine, close every position, cancel every order",
    description=(
        "The single most drastic control this panel exposes. Disables Auto Trading and "
        "stops the engine FIRST, then closes every open position and cancels every "
        "pending order - reusing the exact same primitives as the individual Close All "
        "Positions / Cancel Pending Orders actions, not a separate implementation."
    ),
)
async def kill_switch(_user: str = Depends(get_current_user)):
    result = await trading_control_service.kill_switch()
    return KillSwitchResponse(
        auto_trading_disabled=result.auto_trading_disabled,
        engine_stopped=result.engine_stopped,
        close_positions=_bulk_action_to_response(result.close_positions),
        cancel_orders=_bulk_action_to_response(result.cancel_orders),
    )


@router.get(
    "/logs",
    response_model=LogsResponse,
    summary="Recent application log lines (Trading Logs panel)",
    description="Most-recent-first, from an in-memory ring buffer (see app/core/log_buffer.py) - the same log lines already written to stdout/logs/*.log, not a separate log stream.",
)
async def get_trading_logs(
    limit: int = Query(200, ge=1, le=500),
    level: str | None = Query(None, description="Minimum level, e.g. WARNING"),
    _user: str = Depends(get_current_user),
):
    items = get_recent_logs(limit=limit, level=level)
    return LogsResponse(
        items=[LogEntryResponse(**item) for item in items],
        count=len(items),
    )
