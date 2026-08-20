"""
Smart AI premium API - owner-gated.

Two routers under /smartai:
  - an UNPROTECTED auth router (login / refresh), rate-limited per IP;
  - a PROTECTED router whose EVERY route carries a router-level
    `require_smartai_auth` dependency, so a valid Smart AI access token is
    required regardless of route ordering or the global REQUIRE_AUTH flag.

The password never reaches the client: login takes the plaintext, checks it
against the bcrypt OWNER_PASSWORD_HASH server-side, and returns JWTs. See
app/core/smartai_auth.py and app/core/rate_limit.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core import smartai_auth
from app.core.config import get_settings
from app.core.rate_limit import LoginRateLimiter
from app.models.coin import Coin
from app.models.signal import Signal
from app.schemas.smartai import (
    SmartAiLoginRequest,
    SmartAiRefreshRequest,
    SmartAiSignal,
    SmartAiStatus,
    SmartAiTokenResponse,
    StrategyInfo,
    StrategyPerformance,
)
from app.strategy.base_strategy import build_strategy, registered_ids

# Process-local login limiter, sized from settings. Exposed at module scope so
# tests can swap it for one with a controllable clock.
_settings = get_settings()
login_limiter = LoginRateLimiter(
    max_attempts=_settings.smartai_login_max_attempts,
    window_seconds=_settings.smartai_login_window_minutes * 60,
    lockout_seconds=_settings.smartai_login_lockout_minutes * 60,
)

_bearer = HTTPBearer(auto_error=False)


async def require_smartai_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Gate every protected Smart AI route. 401 unless a valid, unexpired Smart
    AI *access* token is presented (a refresh token or an admin token is
    rejected)."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    subject = smartai_auth.decode_token(credentials.credentials, expected_typ="access")
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    return subject


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------------------
# Auth router (unprotected)
# ----------------------------------------------------------------------
auth_router = APIRouter(prefix="/smartai/auth", tags=["smartai-auth"])


@auth_router.post("/login", response_model=SmartAiTokenResponse)
async def login(body: SmartAiLoginRequest, request: Request) -> SmartAiTokenResponse:
    ip = _client_ip(request)
    locked, retry_after = login_limiter.is_locked(ip)
    if locked:
        logger.warning("Smart AI login BLOCKED (rate-limited) ip={} retry_after={:.0f}s", ip, retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    if not smartai_auth.verify_owner_password(body.password):
        login_limiter.record_failure(ip)
        logger.warning("Smart AI login FAILED ip={}", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    login_limiter.record_success(ip)
    logger.info("Smart AI login SUCCESS ip={}", ip)
    return SmartAiTokenResponse(
        access_token=smartai_auth.create_access_token(),
        refresh_token=smartai_auth.create_refresh_token(),
        expires_in_minutes=get_settings().smartai_token_expire_minutes,
    )


@auth_router.post("/refresh", response_model=SmartAiTokenResponse)
async def refresh(body: SmartAiRefreshRequest) -> SmartAiTokenResponse:
    subject = smartai_auth.decode_token(body.refresh_token, expected_typ="refresh")
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )
    return SmartAiTokenResponse(
        access_token=smartai_auth.create_access_token(),
        expires_in_minutes=get_settings().smartai_token_expire_minutes,
    )


# ----------------------------------------------------------------------
# Protected router - EVERY route requires a valid access token
# ----------------------------------------------------------------------
router = APIRouter(
    prefix="/smartai", tags=["smartai"], dependencies=[Depends(require_smartai_auth)]
)


def _strategy_infos() -> list[StrategyInfo]:
    """Every registered Smart AI strategy with its live enabled state. Imports
    the concrete strategies defensively so this works whether or not they have
    landed yet (they self-register on import)."""
    for module in ("ict_levels_strategy", "cex_dex_divergence_strategy"):
        try:  # pragma: no cover - import side effect (registration)
            __import__(f"app.strategy.{module}")
        except ImportError:
            pass
    settings = get_settings()
    infos = []
    for sid in registered_ids():
        strat = build_strategy(sid, settings)
        infos.append(
            StrategyInfo(strategy_id=sid, display_name=strat.display_name or sid, enabled=strat.enabled)
        )
    return infos


@router.get("/status", response_model=SmartAiStatus)
async def status_endpoint() -> SmartAiStatus:
    settings = get_settings()
    return SmartAiStatus(
        module_enabled=settings.smartai_enabled,
        testnet=settings.smartai_testnet,
        strategies=_strategy_infos(),
    )


@router.get("/strategies", response_model=list[StrategyInfo])
async def list_strategies() -> list[StrategyInfo]:
    return _strategy_infos()


@router.get("/signals", response_model=list[SmartAiSignal])
async def list_signals(
    strategy_id: str, limit: int = 50, db: AsyncSession = Depends(get_db)
) -> list[SmartAiSignal]:
    rows = (
        await db.execute(
            select(Signal, Coin.symbol)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(Signal.strategy_id == strategy_id)
            .order_by(Signal.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).all()
    return [
        SmartAiSignal(
            id=str(sig.id),
            strategy_id=sig.strategy_id,
            symbol=symbol,
            direction=sig.direction.value,
            status=sig.status.value,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            risk_reward=sig.risk_reward,
            confidence=sig.confidence,
            rules_fired=sig.rules_fired,
            created_at=sig.created_at.isoformat() if sig.created_at else None,
        )
        for sig, symbol in rows
    ]


def _runner(request: Request):
    """The live SmartAiRunner, or None when the module is not running (disabled
    or not yet started). Endpoints degrade to a clear state instead of 500ing."""
    return getattr(request.app.state, "smart_ai_runner", None)


@router.get("/runner/status")
async def runner_status(request: Request) -> dict:
    runner = _runner(request)
    if runner is None:
        return {"running": False, "strategies": []}
    return {"running": True, "strategies": [s.to_dict() for s in runner.status()]}


@router.post("/strategies/{strategy_id}/enable")
async def enable_strategy(strategy_id: str, request: Request) -> dict:
    return _set_enabled(request, strategy_id, True)


@router.post("/strategies/{strategy_id}/disable")
async def disable_strategy(strategy_id: str, request: Request) -> dict:
    return _set_enabled(request, strategy_id, False)


def _set_enabled(request: Request, strategy_id: str, enabled: bool) -> dict:
    runner = _runner(request)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smart AI runner is not active (module disabled). Set SMARTAI_ENABLED=true.",
        )
    try:
        runner.set_enabled(strategy_id, enabled)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown strategy '{strategy_id}'")
    return {"strategy_id": strategy_id, "enabled": enabled}


@router.get("/performance", response_model=StrategyPerformance)
async def performance(strategy_id: str, db: AsyncSession = Depends(get_db)) -> StrategyPerformance:
    """Thin live tally per strategy. The FULL metrics (profit factor, max
    drawdown, Sharpe, R-multiple distribution, walk-forward) come from the
    backtester; this endpoint is the live-DB placeholder it will supersede."""
    from app.models.signal import SignalStatus

    rows = (
        await db.execute(
            select(Signal.status).where(Signal.strategy_id == strategy_id)
        )
    ).scalars().all()
    wins = sum(1 for s in rows if s is SignalStatus.TP_HIT)
    losses = sum(1 for s in rows if s is SignalStatus.STOPPED)
    decided = wins + losses
    return StrategyPerformance(
        strategy_id=strategy_id,
        total_trades=decided,
        wins=wins,
        losses=losses,
        win_rate=(wins / decided * 100.0) if decided else 0.0,
        note="Live DB tally only; run the backtester for validated metrics.",
    )
