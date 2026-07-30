"""
Portfolio Intelligence endpoints (Phase 7, 2026-07-27).

Read-only. Every response is built by app/services/portfolio_intelligence.py
from data this project already computes/stores - see that module's own
docstring for exactly what's reused vs. newly (and only additively)
computed. `scanner` is read the SAME way app/api/v1/endpoints/agent.py's
own `/agent/query` endpoint already reads it (`request.app.state.scanner`,
None-safe) - correlation analysis needs its live `data_manager` for real
cached candles; every other section works with no scanner attached.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_user, get_db
from app.schemas.portfolio import (
    CorrelationAnalysisResponse,
    DiversificationResponse,
    ExposureAnalysisResponse,
    PortfolioIntelligenceResponse,
)
from app.services.portfolio_intelligence import (
    build_correlation_analysis,
    build_diversification_analysis,
    build_exposure_analysis,
    build_portfolio_intelligence,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get(
    "/intelligence",
    response_model=PortfolioIntelligenceResponse,
    summary="Full Portfolio Intelligence report",
    description=(
        "Composes the existing portfolio risk summary and daily-loss tracking (Phase 1, unchanged) "
        "with exposure, correlation, and diversification analysis (Phase 7, new). Exposure reuses live "
        "Binance positions when an account is linked (else falls back to active-signal suggested risk); "
        "each exposure line is enriched with a fresh read from the existing Decision Engine. Correlation "
        "reuses the same Pearson-correlation computation as the existing per-signal correlation warnings. "
        "Diversification is a standard Herfindahl-Hirschman Index over the exposure weights above. "
        "Nothing here is a new scoring engine - see app/services/portfolio_intelligence.py."
    ),
)
async def portfolio_intelligence(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    scanner = getattr(request.app.state, "scanner", None)
    return await build_portfolio_intelligence(db, scanner=scanner)


@router.get(
    "/exposure",
    response_model=ExposureAnalysisResponse,
    summary="Exposure analysis",
    description=(
        "Real exposure per symbol/direction - from live Binance futures positions when an account is "
        "linked, else from currently ACTIVE signals' suggested position risk. Each line is enriched with "
        "a fresh read from the existing Decision Engine (capped per report - see the service module)."
    ),
)
async def exposure_analysis(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    return await build_exposure_analysis(db)


@router.get(
    "/correlation",
    response_model=CorrelationAnalysisResponse,
    summary="Correlation analysis across current exposure",
    description=(
        "Pairwise Pearson correlation (15m returns) across every symbol currently held, reusing the same "
        "computation and threshold as the existing per-signal correlation warnings. Requires a live "
        "scanner/data manager to be attached to this process - honestly unavailable otherwise."
    ),
)
async def correlation_analysis(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    scanner = getattr(request.app.state, "scanner", None)
    exposure = await build_exposure_analysis(db, enrich_with_decisions=False)
    return await build_correlation_analysis(exposure.items, scanner)


@router.get(
    "/diversification",
    response_model=DiversificationResponse,
    summary="Diversification analysis (HHI concentration)",
    description=(
        "Herfindahl-Hirschman Index over current exposure weights, by symbol and by asset class - pure "
        "arithmetic over the same real weights the Exposure Analysis report already computes."
    ),
)
async def diversification_analysis(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    exposure = await build_exposure_analysis(db, enrich_with_decisions=False)
    return build_diversification_analysis(exposure)
