from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.signals import router as signals_router
from app.api.v1.endpoints.stats import router as stats_router
from app.api.v1.endpoints.history import router as history_router
from app.api.v1.endpoints.account import router as account_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.backtest import router as backtest_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.trading import router as trading_router
from app.api.v1.endpoints.token_scan import router as token_scan_router
from app.api.v1.endpoints.agent import router as agent_router
from app.api.v1.endpoints.portfolio import router as portfolio_router
from app.api.v1.endpoints.performance import router as performance_router
from app.api.v1.endpoints.trading_control import router as trading_control_router
from app.api.v1.endpoints.manual_trading import router as manual_trading_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(signals_router)
api_router.include_router(stats_router)
api_router.include_router(history_router)
api_router.include_router(account_router)
api_router.include_router(dashboard_router)
api_router.include_router(backtest_router)
api_router.include_router(trading_router)
api_router.include_router(token_scan_router)
api_router.include_router(agent_router)
# Phase 7 (2026-07-27): Portfolio Intelligence + AI Performance Monitoring -
# purely additive new routers, nothing above this line was changed.
api_router.include_router(portfolio_router)
api_router.include_router(performance_router)
# Auto Trading Control Panel (2026-07-30) - purely additive new router;
# nothing above this line was changed. Shares the "/trading" prefix with
# trading_router (execute/close-position) but registers distinct paths
# (/trading/status, /trading/engine/*, etc.) - no route collision.
api_router.include_router(trading_control_router)
api_router.include_router(manual_trading_router)
