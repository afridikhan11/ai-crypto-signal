"""
CEX-DEX Divergence strategy tests.

Proves the non-negotiable guards actually BLOCK (stale quote, thin pool, and a
single-poll spike all return None), that a sustained divergence fires with the
correct mean-reversion direction and a populated `max_notional_usd`, and that
funding confirmation only nudges confidence rather than gating the trade. No
live API calls - the DEX quote is injected via `market_data.extra["dex"]` and
the clock is fixed, exactly as the runner will supply them in production.
"""
import asyncio

from app.models.signal import Direction
from app.strategy.base_strategy import MarketData
from app.strategy.cex_dex_divergence_strategy import CexDexDivergenceStrategy


def _run(coro):
    return asyncio.run(coro)


NOW_MS = 1_700_000_000_000


class _Settings:
    def __init__(self, **over):
        self.smartai_enabled = True
        self.strategy_cex_dex_divergence_enabled = True
        self.dexscreener_min_liquidity_usd = 100_000.0
        self.dexscreener_staleness_seconds = 120.0
        self.divergence_min_consecutive_polls = 3
        self.divergence_threshold_pct = 1.0
        self.divergence_funding_weight = 0.3
        self.divergence_max_pool_depth_fraction = 0.01
        self.__dict__.update(over)


def _strategy(now_ms=NOW_MS, **over):
    return CexDexDivergenceStrategy(_Settings(**over), now_ms=lambda: now_ms)


def _market(cex_price, dex_price, liquidity=1_000_000.0, ts_ms=NOW_MS,
            funding=None, symbol="ETHUSDT"):
    return MarketData(
        symbol=symbol,
        current_price=cex_price,
        funding_rate=funding,
        extra={"dex": {"price": dex_price, "liquidity_usd": liquidity, "timestamp_ms": ts_ms}},
    )


def _cond(sig_or_conditions, name):
    conds = sig_or_conditions.conditions if hasattr(sig_or_conditions, "conditions") else sig_or_conditions
    return next(c for c in conds if c.name == name)


class TestEnabled:
    def test_enabled_requires_both_flags(self):
        assert _strategy().enabled is True
        assert _strategy(smartai_enabled=False).enabled is False
        assert _strategy(strategy_cex_dex_divergence_enabled=False).enabled is False


class TestGuardsBlock:
    def test_stale_quote_returns_none(self):
        strat = _strategy()
        # Quote timestamp is 300s old, staleness cap is 120s.
        md = _market(cex_price=102.0, dex_price=100.0, ts_ms=NOW_MS - 300_000)
        # Feed it enough times to rule out the consecutive-poll guard as the cause.
        for _ in range(5):
            sig = _run(strat.evaluate(md))
        assert sig is None

    def test_thin_liquidity_returns_none(self):
        strat = _strategy()
        md = _market(cex_price=102.0, dex_price=100.0, liquidity=50_000.0)
        for _ in range(5):
            sig = _run(strat.evaluate(md))
        assert sig is None

    def test_single_spike_does_not_fire(self):
        strat = _strategy()
        md = _market(cex_price=102.0, dex_price=100.0)  # +2% premium, well above threshold
        sig = _run(strat.evaluate(md))
        assert sig is None  # first divergent poll: streak 1 < 3

    def test_staleness_is_the_isolated_blocker(self):
        # A stale quote never fires no matter how many polls; making ONLY the
        # timestamp fresh (everything else identical) then fires -> freshness
        # was the blocker, and the fired signal's quote_fresh condition passes.
        strat = _strategy()
        stale = _market(cex_price=102.0, dex_price=100.0, ts_ms=NOW_MS - 300_000)
        for _ in range(5):
            assert _run(strat.evaluate(stale)) is None
        fresh = _market(cex_price=102.0, dex_price=100.0, ts_ms=NOW_MS)
        sig = None
        for _ in range(3):
            sig = _run(strat.evaluate(fresh))
        assert sig is not None
        assert _cond(sig, "quote_fresh").passed is True

    def test_liquidity_is_the_isolated_blocker(self):
        # Thin pool never fires; fattening ONLY the liquidity then fires ->
        # liquidity was the blocker, and the fired signal's liquidity_ok passes.
        strat = _strategy()
        thin = _market(cex_price=102.0, dex_price=100.0, liquidity=50_000.0)
        for _ in range(5):
            assert _run(strat.evaluate(thin)) is None
        fat = _market(cex_price=102.0, dex_price=100.0, liquidity=1_000_000.0)
        sig = None
        for _ in range(3):
            sig = _run(strat.evaluate(fat))
        assert sig is not None
        assert _cond(sig, "liquidity_ok").passed is True


class TestSustainedDivergenceFires:
    def test_premium_fires_short(self):
        strat = _strategy()
        md = _market(cex_price=102.0, dex_price=100.0)  # perp premium -> SHORT
        sig = None
        for _ in range(3):
            sig = _run(strat.evaluate(md))
        assert sig is not None
        assert sig.direction == Direction.SHORT
        assert sig.entry_price == 102.0
        assert sig.take_profit == 100.0          # converge to spot
        assert sig.stop_loss > sig.entry_price   # adverse = further premium
        assert sig.metadata["max_notional_usd"] == 1_000_000.0 * 0.01
        assert _cond(sig, "consecutive_polls").passed is True
        assert round(sig.risk_reward, 2) == 2.0

    def test_discount_fires_long(self):
        strat = _strategy()
        md = _market(cex_price=98.0, dex_price=100.0)  # perp discount -> LONG
        sig = None
        for _ in range(3):
            sig = _run(strat.evaluate(md))
        assert sig is not None
        assert sig.direction == Direction.LONG
        assert sig.take_profit == 100.0
        assert sig.stop_loss < sig.entry_price
        assert sig.metadata["max_notional_usd"] == 1_000_000.0 * 0.01

    def test_streak_resets_on_non_divergent_poll(self):
        strat = _strategy()
        div = _market(cex_price=102.0, dex_price=100.0)
        flat = _market(cex_price=100.2, dex_price=100.0)  # +0.2% < 1% threshold
        _run(strat.evaluate(div))   # streak 1
        _run(strat.evaluate(div))   # streak 2
        assert _run(strat.evaluate(flat)) is None  # resets to 0
        assert _run(strat.evaluate(div)) is None    # streak 1 again, no fire
        assert _run(strat.evaluate(div)) is None    # streak 2
        assert _run(strat.evaluate(div)) is not None  # streak 3 -> fire

    def test_streak_resets_on_direction_flip(self):
        strat = _strategy()
        premium = _market(cex_price=102.0, dex_price=100.0)   # SHORT
        discount = _market(cex_price=98.0, dex_price=100.0)   # LONG
        _run(strat.evaluate(premium))  # streak 1 SHORT
        _run(strat.evaluate(premium))  # streak 2 SHORT
        assert _run(strat.evaluate(discount)) is None  # flip -> streak 1 LONG
        assert _run(strat.evaluate(discount)) is None  # streak 2 LONG
        sig = _run(strat.evaluate(discount))           # streak 3 LONG -> fire
        assert sig is not None and sig.direction == Direction.LONG

    def test_streak_is_per_symbol(self):
        strat = _strategy()
        a = _market(cex_price=102.0, dex_price=100.0, symbol="AAAUSDT")
        b = _market(cex_price=102.0, dex_price=100.0, symbol="BBBUSDT")
        _run(strat.evaluate(a))
        _run(strat.evaluate(a))
        # B is on its first poll; its streak must be independent of A's.
        assert _run(strat.evaluate(b)) is None
        assert _run(strat.evaluate(a)) is not None  # A reaches 3, fires


class TestFundingConfirmation:
    def _fire(self, funding):
        strat = _strategy()
        md = _market(cex_price=102.0, dex_price=100.0, funding=funding)  # SHORT
        sig = None
        for _ in range(3):
            sig = _run(strat.evaluate(md))
        return sig

    def test_agreeing_funding_raises_confidence(self):
        # Positive funding + perp premium (SHORT) => agrees => confidence up.
        neutral = self._fire(funding=None)
        agree = self._fire(funding=0.01)
        assert agree.confidence > neutral.confidence
        assert _cond(agree, "funding_confirmation").passed is True

    def test_disagreeing_funding_lowers_confidence(self):
        # Negative funding + perp premium (SHORT) => disagrees => confidence down.
        neutral = self._fire(funding=None)
        disagree = self._fire(funding=-0.01)
        assert disagree.confidence < neutral.confidence
        assert _cond(disagree, "funding_confirmation").passed is False

    def test_disagreeing_funding_still_fires(self):
        # Funding is confirmation-only: contradiction must not block the trade.
        assert self._fire(funding=-0.01) is not None
