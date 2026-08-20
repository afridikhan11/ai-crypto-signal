"""
Smart AI strategy interface + registry.

The Smart AI module is a shared core (exchange client, risk manager, position
sizing, execution, persistence, backtester - all already in this codebase)
plus a thin strategy layer plugged in behind ONE interface. A strategy takes
market data and returns a `StrategySignal` or `None`; it never talks to the
exchange or the database itself.

Every produced signal carries its `strategy_id`, so signals, fills and PnL can
be attributed to each strategy separately - that attribution is the whole point
of the module (see docs/SMART_AI_TASK.md).

This module deliberately holds NO trading logic and NO I/O: the concrete
strategies (ICT Levels, CEX-DEX Divergence) live beside it and register
themselves here. Keeping the contract tiny and dependency-free is what lets the
two strategies stay genuinely independent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.models.signal import Direction


@dataclass
class ConditionResult:
    """One rule in a strategy's decision, evaluated pass/fail with a reason.

    The full list for a signal is the "which rules fired" explanation the task
    asks to be surfaced prominently - so a human can see EXACTLY why a signal
    did or did not fire, on every evaluation, not just the ones that fired."""

    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class MarketData:
    """Everything a strategy is allowed to see. Assembled by the runner so a
    strategy never fetches anything itself (keeps it pure and testable).

    `dataframe` is the primary (entry) timeframe OHLCV; `htf_dataframes` maps a
    timeframe label ("4h"/"1d"/...) to its OHLCV for multi-timeframe bias.
    `extra` carries strategy-specific inputs (e.g. the CEX-DEX strategy's DEX
    quote) so the shared shape does not grow a field per strategy."""

    symbol: str
    dataframe: Any = None
    htf_dataframes: Dict[str, Any] = field(default_factory=dict)
    funding_rate: Optional[float] = None
    current_price: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategySignal:
    """A strategy's output - a proposed trade, or the fields needed to persist
    one. This is NOT the DB model: the runner maps it onto `app.models.Signal`
    (tagging `strategy_id` and storing `conditions` as `rules_fired`), which
    keeps strategies decoupled from persistence.

    `targets` is the laddered exit list (e.g. equilibrium as a partial-take,
    then the opposing liquidity pool); `take_profit` is the primary/final
    target and always equals the last target when targets are given."""

    strategy_id: str
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: int
    conditions: List[ConditionResult] = field(default_factory=list)
    targets: List[float] = field(default_factory=list)
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def rules_fired(self) -> List[Dict[str, Any]]:
        """JSON-serialisable condition log for persistence / the UI."""
        return [c.as_dict() for c in self.conditions]


class BaseStrategy(ABC):
    """The one interface every Smart AI strategy implements.

    Subclasses set `strategy_id` (stable, stored on every signal) and
    `display_name` (for the UI), read their knobs from the injected settings in
    `__init__`, and implement `evaluate`. They must be side-effect free: no
    exchange calls, no DB writes - just market data in, a signal or None out."""

    strategy_id: str = ""
    display_name: str = ""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        """Whether this strategy is switched on. Overridden per strategy to read
        its own config flag; defaults to off so a new strategy never trades
        until it is explicitly enabled (task: default disabled + testnet)."""
        return False

    @abstractmethod
    async def evaluate(self, market_data: MarketData) -> Optional[StrategySignal]:
        """Return a `StrategySignal` when a setup fires, else `None`. Must log
        every checked condition (pass and fail) onto the returned signal's
        `conditions`; a `None` return should still be explainable from the
        strategy's own logging."""
        raise NotImplementedError


# ----------------------------------------------------------------------
# Registry - strategies register themselves so the runner can discover and
# instantiate only the enabled ones without importing each by name.
# ----------------------------------------------------------------------
_REGISTRY: Dict[str, Callable[[Any], BaseStrategy]] = {}


def register_strategy(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """Class decorator: register a BaseStrategy subclass by its `strategy_id`.

    Raises on a missing or duplicate id so a copy-paste mistake fails loudly at
    import time rather than silently shadowing another strategy."""
    sid = getattr(cls, "strategy_id", "")
    if not sid:
        raise ValueError(f"{cls.__name__} must set a non-empty strategy_id to register.")
    if sid in _REGISTRY:
        raise ValueError(f"Duplicate strategy_id '{sid}' ({cls.__name__}).")
    _REGISTRY[sid] = cls
    return cls


def registered_ids() -> List[str]:
    return sorted(_REGISTRY)


def build_strategy(strategy_id: str, settings: Any) -> BaseStrategy:
    if strategy_id not in _REGISTRY:
        raise KeyError(f"Unknown strategy_id '{strategy_id}'. Known: {registered_ids()}")
    return _REGISTRY[strategy_id](settings)


def build_enabled_strategies(settings: Any) -> List[BaseStrategy]:
    """Instantiate every registered strategy and keep the ones whose own
    `enabled` flag is set. Order is stable (by id) for deterministic startup."""
    built = [_REGISTRY[sid](settings) for sid in registered_ids()]
    return [s for s in built if s.enabled]
