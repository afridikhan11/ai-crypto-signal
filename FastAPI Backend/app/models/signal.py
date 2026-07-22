from sqlalchemy import String, Float, Integer, DateTime, Enum as SQLEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
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

    # Relationship to Coin – enables eager loading and direct access to coin symbol
    coin: Mapped["Coin"] = relationship("Coin", back_populates="signals")
