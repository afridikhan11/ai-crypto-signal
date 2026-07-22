from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.signals import router as signals_router
from app.api.v1.endpoints.stats import router as stats_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(signals_router)
api_router.include_router(stats_router)