"""
Smart AI strategy interface + registry. Verifies the contract every strategy
plugs into: a signal serialises its rule-log for persistence/UI, and the
registry discovers/instantiates only enabled strategies and fails loudly on a
missing or duplicate strategy_id.
"""
import asyncio

import pytest

from app.models.signal import Direction
from app.strategy import base_strategy as bs
from app.strategy.base_strategy import (
    BaseStrategy,
    ConditionResult,
    MarketData,
    StrategySignal,
    build_enabled_strategies,
    build_strategy,
    register_strategy,
    registered_ids,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot and restore the module-global registry so test strategies never
    leak into other tests (or into the real strategies)."""
    saved = dict(bs._REGISTRY)
    bs._REGISTRY.clear()
    try:
        yield
    finally:
        bs._REGISTRY.clear()
        bs._REGISTRY.update(saved)


class _Settings:
    def __init__(self, **flags):
        self.__dict__.update(flags)


def _signal(**over):
    base = dict(
        strategy_id="x", symbol="BTCUSDT", direction=Direction.SHORT,
        entry_price=100.0, stop_loss=101.0, take_profit=97.0,
        risk_reward=3.0, confidence=72,
    )
    base.update(over)
    return StrategySignal(**base)


class TestStrategySignal:
    def test_rules_fired_serialises_conditions(self):
        sig = _signal(conditions=[
            ConditionResult("htf_bias", True, "4h bearish"),
            ConditionResult("liquidity_sweep", False, "no sweep"),
        ])
        assert sig.rules_fired() == [
            {"name": "htf_bias", "passed": True, "detail": "4h bearish"},
            {"name": "liquidity_sweep", "passed": False, "detail": "no sweep"},
        ]

    def test_defaults_are_empty_not_shared(self):
        a, b = _signal(), _signal()
        a.conditions.append(ConditionResult("x", True))
        assert b.conditions == []          # no shared mutable default


class TestRegistry:
    def _make(self, sid, enabled_flag):
        @register_strategy
        class _S(BaseStrategy):
            strategy_id = sid
            display_name = sid.title()

            @property
            def enabled(self):
                return getattr(self.settings, enabled_flag, False)

            async def evaluate(self, market_data):
                return None

        return _S

    def test_register_and_build_by_id(self):
        self._make("alpha", "alpha_on")
        assert registered_ids() == ["alpha"]
        inst = build_strategy("alpha", _Settings(alpha_on=True))
        assert isinstance(inst, BaseStrategy) and inst.strategy_id == "alpha"

    def test_build_enabled_filters_on_flag(self):
        self._make("alpha", "alpha_on")
        self._make("beta", "beta_on")
        enabled = build_enabled_strategies(_Settings(alpha_on=True, beta_on=False))
        assert [s.strategy_id for s in enabled] == ["alpha"]

    def test_missing_id_raises(self):
        with pytest.raises(ValueError):
            @register_strategy
            class _Bad(BaseStrategy):
                async def evaluate(self, market_data):
                    return None

    def test_duplicate_id_raises(self):
        self._make("dup", "on")
        with pytest.raises(ValueError):
            self._make("dup", "on")

    def test_build_unknown_id_raises(self):
        with pytest.raises(KeyError):
            build_strategy("nope", _Settings())

    def test_evaluate_contract_returns_none(self):
        cls = self._make("gamma", "on")
        inst = cls(_Settings(on=True))
        assert asyncio.run(inst.evaluate(MarketData(symbol="BTCUSDT"))) is None
