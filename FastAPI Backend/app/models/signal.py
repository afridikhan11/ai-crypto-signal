from sqlalchemy import String, Float, Integer, DateTime, Enum as SQLEnum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import uuid
import enum
from app.models.base import Base, TimestampMixin


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, enum.Enum):
    # ICT Pending Limit Entry migration (2026-07-30). PENDING_ENTRY is the
    # NON-terminal state a signal is born into under entry_mode
    # "ict_pending": the ICT entry zone has been identified but price has
    # not traded into it yet, so no position exists and nothing is at risk.
    # It becomes ACTIVE only on a real fill.
    PENDING_ENTRY = "PENDING_ENTRY"
    ACTIVE = "ACTIVE"
    TP_HIT = "TP_HIT"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"
    # Terminal, and deliberately DISTINCT from CANCELLED: EXPIRED means the
    # trade was never entered at all (price never reached the entry zone, or
    # invalidated by hitting the stop level first). CANCELLED means a trade
    # WAS entered and was later closed by structure failure. Conflating them
    # would corrupt performance analysis - an unfilled setup is not a loss.
    EXPIRED = "EXPIRED"


# Statuses in which a signal is still "live" for the platform in some sense
# but is NOT a closed/decided trade. Used by every stats/history read so a
# pending or expired signal can never be mistaken for a resolved outcome.
NON_TERMINAL_STATUSES = (SignalStatus.PENDING_ENTRY, SignalStatus.ACTIVE)

# Statuses that represent a real, filled position currently open.
OPEN_POSITION_STATUSES = (SignalStatus.ACTIVE,)

# Statuses where the trade actually RAN and reached an outcome. This is what
# "closed" must mean in every statistic: EXPIRED is excluded because the
# trade was never entered, and counting it would dilute the win rate with
# trades that were never taken. PENDING_ENTRY and ACTIVE are excluded
# because they have not finished.
DECIDED_STATUSES = (SignalStatus.TP_HIT, SignalStatus.STOPPED, SignalStatus.CANCELLED)

# Terminal statuses - the signal will never change again.
TERMINAL_STATUSES = DECIDED_STATUSES + (SignalStatus.EXPIRED,)


class Signal(Base, TimestampMixin):
    __tablename__ = "signals"

    coin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coins.id"), nullable=False)
    direction: Mapped[Direction] = mapped_column(SQLEnum(Direction), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)

    # THE LIVE stop. Trade Management moves this over the life of the trade
    # (breakeven at 1R, then structure trailing - see
    # app/strategy/trade_management_engine.py). It is what the exchange's
    # resting STOP_MARKET order is synchronised to, and what TP/SL
    # resolution compares price against. It is deliberately NOT a stable
    # reference point.
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)

    # THE ORIGINAL stop, frozen at signal creation and never modified
    # afterwards by anything.
    #
    # WHY THIS EXISTS (2026-07-30): position size is `risk_usd /
    # abs(entry_price - stop)`. Reading the LIVE `stop_loss` for that made
    # the computed size depend on how far Trade Management had already
    # trailed the stop, which is wrong twice over:
    #   1. At breakeven the engine sets the stop EXACTLY to entry_price
    #      (trade_management_engine.py `new_stop_loss=entry_price`), so the
    #      risk distance collapses to 0 and position sizing returns None -
    #      surfacing as the misleading "unknown account balance, or a
    #      degenerate entry/stop pair".
    #   2. Short of breakeven, a partly-trailed stop shrinks the risk
    #      distance and therefore INFLATES notional exposure without any
    #      real position having changed.
    # The size of a position is fixed the moment it is taken; it cannot
    # change because the stop later moved. `initial_stop_loss` is that
    # fixed reference, so every risk/sizing/exposure read is stable for the
    # life of the trade.
    initial_stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    # Single target - previously three cascading TP1/TP2/TP3 levels, but the
    # monitor/backtest engine only ever acted on whichever was reached
    # first (price always crosses TP1 before TP2/TP3), so the other two
    # were never real partial exits, just unused extra numbers.
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[SignalStatus] = mapped_column(
        SQLEnum(SignalStatus), default=SignalStatus.ACTIVE
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeframe: Mapped[str] = mapped_column(String(10), default="1m")
    ai_model_version: Mapped[str | None] = mapped_column(String(20))
    # Raw per-category AIScorer scores (0-100 each) at signal-generation
    # time, e.g. {"market_structure": 90, "liquidity_sweep": 85, ...}.
    # Used by app/ai/calibration.py to re-derive scorer weights from real
    # win/loss outcomes once enough closed trades have accumulated.
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- Smart AI module (2026-08-20) ----
    # Which strategy produced this signal, for per-strategy performance
    # attribution (the whole point of the module). NULL / "legacy" = the
    # original production ICT pipeline that predates Smart AI. Indexed because
    # every per-strategy stats read filters on it.
    strategy_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # The individual strategy conditions evaluated for this signal - each
    # {"name", "passed", "detail"} - i.e. the "which rules fired" explanation
    # surfaced in the Smart AI UI. NULL for legacy signals.
    rules_fired: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Auto Trading execution tracking - set once (and only once) when this
    # signal's suggested entry/SL/TP is actually placed on Binance via
    # POST /trading/execute/{signal_id}. `executed` gates against placing
    # the same signal's order twice; `executed_environment` records whether
    # it went to testnet or mainnet, since a signal executed on testnet
    # means nothing about real capital and must never be confused with one
    # executed on mainnet.
    executed: Mapped[bool] = mapped_column(default=False, server_default="false")
    executed_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_environment: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # ---- ICT Pending Limit Entry (2026-07-30) ----
    # Which ICT anchor produced `entry_price` - "ote" | "order_block" |
    # "fvg" | "supply_demand" | "limit" | "market" (app/strategy/entry_engine.py's
    # EntryType). "market" means no zone anchor was available and the live
    # price was used, which is the documented weakest entry type.
    entry_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The real bounds of that anchor zone. A fill is recognised when price
    # trades anywhere INSIDE this zone, not only at the exact midpoint that
    # `entry_price` holds - a midpoint-only test would miss most real fills.
    entry_zone_top: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_zone_bottom: Mapped[float | None] = mapped_column(Float, nullable=True)
    # When the pending entry stops being valid. Set at creation from
    # app.core.constants.PENDING_ENTRY_EXPIRY_CANDLES; once passed with no
    # fill the signal becomes EXPIRED and any resting LIMIT is cancelled.
    entry_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set at the moment of a real fill. `actual_fill_price` is recorded
    # separately from `entry_price` so the two can be compared/audited even
    # though a LIMIT order fills at its limit price by definition.
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The resting LIMIT entry order on Binance, when Auto Trading has placed
    # one. Distinct from `executed_order_id` (which records the order that
    # actually opened the position) so a pending entry can be reconciled or
    # cancelled without guessing which order id is which.
    entry_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationship to Coin – enables eager loading and direct access to coin symbol
    coin: Mapped["Coin"] = relationship("Coin", back_populates="signals")
