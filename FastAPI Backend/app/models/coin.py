from sqlalchemy import String, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class Coin(Base, TimestampMixin):
    __tablename__ = "coins"

    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "crypto" or "commodity" - see app.core.constants.asset_class_for_symbol().
    # Lets the frontend show a dedicated "Gold Signal" tab (XAUUSDT/XAGUSDT/
    # CLUSDT/BZUSDT) without hand-picking a symbol list on every request.
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False, default="crypto", server_default="crypto")
    rank: Mapped[int | None] = mapped_column()
    market_cap: Mapped[float | None] = mapped_column(Float)
    volume_24h: Mapped[float | None] = mapped_column(Float)

    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="coin")
