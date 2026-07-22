#!/usr/bin/env bash
set -euo pipefail

# ----------------------------------------------------------------------
# AI Crypto Signal System – Module 1 Setup Script
# ----------------------------------------------------------------------
# This script creates the full backend foundation:
#   - Clean Architecture folder structure
#   - Core configuration, database, Redis, logging
#   - SQLAlchemy models (User, Coin, Signal)
#   - FastAPI app with health endpoint
#   - Dockerfiles and Compose
#   - Virtual environment and dependencies
# ----------------------------------------------------------------------

PROJECT_DIR="$(pwd)"
echo "📁 Setting up project in: $PROJECT_DIR"

# 1. Create folder structure
echo "📂 Creating directories..."
mkdir -p app/api/v1/endpoints
mkdir -p app/core
mkdir -p app/models
mkdir -p logs

# 2. Write .env.example
cat > .env.example << 'EOF'
# Application
APP_NAME=AI_Crypto_Signal
DEBUG=false
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/crypto_signals

# Redis
REDIS_URL=redis://localhost:6379/0

# Binance
BINANCE_API_KEY=your_api_key
BINANCE_SECRET_KEY=your_secret_key

# Security
SECRET_KEY=change_me_in_production
ACCESS_TOKEN_EXPIRE_MINUTES=30
EOF

# Copy .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅ .env file created from .env.example"
fi

# 3. Write requirements.txt
cat > requirements.txt << 'EOF'
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
pydantic-settings==2.7.0
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.1
redis[hiredis]==5.2.1
apscheduler==3.10.4
python-dotenv==1.0.1
httpx==0.28.1
numpy==2.2.1
pandas==2.2.3
ta==0.11.0
websockets==14.1
loguru==0.7.3
EOF

# 4. Write all Python files

# ---- app/__init__.py ----
touch app/__init__.py

# ---- app/main.py ----
cat > app/main.py << 'MAINEOF'
from fastapi import FastAPI
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import logger
from app.models.base import Base
from app.core.database import engine

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
async def on_startup():
    logger.info("Starting application...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured.")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down...")
    await engine.dispose()
MAINEOF

# ---- app/api/__init__.py ----
touch app/api/__init__.py

# ---- app/api/v1/__init__.py ----
cat > app/api/v1/__init__.py << 'EOF'
from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
EOF

# ---- app/api/v1/endpoints/__init__.py ----
touch app/api/v1/endpoints/__init__.py

# ---- app/api/v1/endpoints/health.py ----
cat > app/api/v1/endpoints/health.py << 'EOF'
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.redis import get_redis
from redis.asyncio import Redis

router = APIRouter()


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    try:
        await db.execute("SELECT 1")
        db_status = "ok"
    except Exception:
        db_status = "error"

    try:
        await redis.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"

    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "database": db_status,
        "redis": redis_status,
    }
EOF

# ---- app/core/__init__.py ----
touch app/core/__init__.py

# ---- app/core/config.py ----
cat > app/core/config.py << 'EOF'
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "AI_Crypto_Signal"
    debug: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/crypto_signals"
    redis_url: str = "redis://localhost:6379/0"

    binance_api_key: str = ""
    binance_secret_key: str = ""

    secret_key: str = "change_me_in_production"
    access_token_expire_minutes: int = 30

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
EOF

# ---- app/core/logging.py ----
cat > app/core/logging.py << 'EOF'
import sys
from loguru import logger
from app.core.config import get_settings

settings = get_settings()

logger.remove()
logger.add(
    sys.stdout,
    level=settings.log_level,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    colorize=True,
)
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
)
EOF

# ---- app/core/database.py ----
cat > app/core/database.py << 'EOF'
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
EOF

# ---- app/core/redis.py ----
cat > app/core/redis.py << 'EOF'
import redis.asyncio as aioredis
from app.core.config import get_settings

settings = get_settings()

redis_client = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    max_connections=10,
)

async def get_redis():
    yield redis_client
EOF

# ---- app/models/__init__.py ----
touch app/models/__init__.py

# ---- app/models/base.py ----
cat > app/models/base.py << 'EOF'
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func
import uuid
from datetime import datetime


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
EOF

# ---- app/models/user.py ----
cat > app/models/user.py << 'EOF'
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    full_name: Mapped[str | None] = mapped_column(String(128))
EOF

# ---- app/models/coin.py ----
cat > app/models/coin.py << 'EOF'
from sqlalchemy import String, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class Coin(Base, TimestampMixin):
    __tablename__ = "coins"

    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    rank: Mapped[int | None] = mapped_column()
    market_cap: Mapped[float | None] = mapped_column(Float)
    volume_24h: Mapped[float | None] = mapped_column(Float)
EOF

# ---- app/models/signal.py ----
cat > app/models/signal.py << 'EOF'
from sqlalchemy import String, Float, Integer, DateTime, Enum as SQLEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
import uuid
import enum
from app.models.base import Base, TimestampMixin


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


class Signal(Base, TimestampMixin):
    __tablename__ = "signals"

    coin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coins.id"), nullable=False)
    direction: Mapped[Direction] = mapped_column(SQLEnum(Direction), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_1: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_2: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_3: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[SignalStatus] = mapped_column(
        SQLEnum(SignalStatus), default=SignalStatus.ACTIVE
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeframe: Mapped[str] = mapped_column(String(10), default="1m")
    ai_model_version: Mapped[str | None] = mapped_column(String(20))
EOF

# 5. Write Dockerfile
cat > Dockerfile << 'EOF'
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
EOF

# 6. Write docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: crypto_signals
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  app:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
    env_file:
      - .env

volumes:
  postgres_data:
EOF

# 7. Set up Python virtual environment (optional but recommended)
echo "🐍 Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "✅ Module 1 setup complete!"
echo ""
echo "👉 Next steps:"
echo "   1. Review and edit .env file if needed"
echo "   2. Start services with: docker-compose up -d"
echo "   3. Or run locally: uvicorn app.main:app --reload"
echo "   4. Test health: curl http://localhost:8000/api/v1/health"