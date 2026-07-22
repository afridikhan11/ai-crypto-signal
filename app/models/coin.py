from sqlalchemy import String, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class Coin(Base, TimestampMixin):
    __tablename__ = "coins"

    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    rank: Mapped[int | None] = mapped_column()
    market_cap: Mapped[float | None] = mapped_column(Float)
    volume_24h: Mapped[float | None] = mapped_column(Float)

    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="coin")
