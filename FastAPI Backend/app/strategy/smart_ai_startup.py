"""
Live wiring for the Smart AI runner.

Connects the pure strategies to the running system: market data comes from the
scanner's existing data manager, and produced signals are persisted as Signal
rows tagged with their `strategy_id` (mirroring UniversalScanner.save_signal).
Everything here is guarded and OFF unless SMARTAI_ENABLED is true, and the
whole startup is wrapped so a failure can never break the main app.

This module is the one part of the Smart AI backend that talks to the live
exchange + DB, so it is verified on the VM rather than in unit tests; only the
pure signal->model mapping is unit-tested here.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select

from app.core.config import get_settings
from app.core.constants import asset_class_for_symbol
from app.core.database import AsyncSessionLocal
from app.models.coin import Coin
from app.models.signal import NON_TERMINAL_STATUSES, Direction, Signal, SignalStatus
from app.strategy.base_strategy import BaseStrategy, MarketData, StrategySignal, build_enabled_strategies
from app.strategy.smart_ai_runner import SmartAiRunner

# Import concrete strategies so they self-register in the strategy registry.
from app.strategy import cex_dex_divergence_strategy  # noqa: F401
from app.strategy import ict_levels_strategy  # noqa: F401


def signal_to_model_kwargs(sig: StrategySignal, coin_id, now: datetime) -> Dict[str, Any]:
    """Map a StrategySignal onto Signal constructor kwargs. Pure/testable. A
    Smart AI signal is born ACTIVE at its entry price (the strategy already
    decided to trade), tagged with strategy_id and its rules_fired log."""
    return dict(
        coin_id=coin_id,
        direction=Direction(sig.direction.value),
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        initial_stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        risk_reward=sig.risk_reward,
        confidence=sig.confidence,
        reason=sig.reason or f"{sig.strategy_id} signal",
        timeframe=get_settings().ict_ltf_timeframe,
        ai_model_version="smartai-1.0",
        status=SignalStatus.ACTIVE,
        strategy_id=sig.strategy_id,
        rules_fired=sig.rules_fired(),
        filled_at=now,
        actual_fill_price=sig.entry_price,
    )


async def _persist(sig: StrategySignal) -> None:
    """Persist a produced signal, deduped per (coin, strategy) so one strategy's
    live signal never blocks another's on the same symbol."""
    async with AsyncSessionLocal() as session:
        symbol = sig.symbol.upper()
        coin = (await session.execute(select(Coin).where(Coin.symbol == symbol))).scalar_one_or_none()
        if coin is None:
            coin = Coin(symbol=symbol, asset_class=asset_class_for_symbol(symbol))
            session.add(coin)
            await session.flush()

        existing = (
            await session.execute(
                select(Signal).where(
                    Signal.coin_id == coin.id,
                    Signal.strategy_id == sig.strategy_id,
                    Signal.status.in_(NON_TERMINAL_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("Smart AI {}: live signal already open for {}", sig.strategy_id, symbol)
            return

        session.add(Signal(**signal_to_model_kwargs(sig, coin.id, datetime.now(timezone.utc))))
        await session.commit()
        logger.success("Smart AI {} signal saved: {} {} @ {}", sig.strategy_id, symbol, sig.direction.value, sig.entry_price)


async def _dex_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Best-effort DEX spot quote for the CEX-DEX strategy. Returns None (so the
    strategy fails its liquidity/freshness guards) when no confident pair is
    found - a proper Binance-symbol -> DEX-pair mapping is a VM-side config
    concern, so this never fabricates a price."""
    try:
        from app.services.dexscreener_service import DexScreenerService

        base = symbol.upper().replace("USDT", "").replace("USD", "")
        async with DexScreenerService() as svc:
            pairs = await svc.search(base)
            pair = svc.pick_primary_pair(pairs)
            if not pair:
                return None
            summary = svc.summarize(pair)
        price = summary.get("price_usd") or summary.get("price")
        liquidity = summary.get("liquidity_usd")
        if price is None or liquidity is None:
            return None
        # DexScreener's REST carries no per-price timestamp; stamp the fetch
        # instant, so "staleness" honestly measures our own polling lag.
        return {"price": float(price), "liquidity_usd": float(liquidity), "timestamp_ms": int(time.time() * 1000)}
    except Exception as exc:  # noqa: BLE001 - advisory data source
        logger.warning("Smart AI DEX quote for {} failed: {}", symbol, exc)
        return None


def _make_provider(data_manager, settings):
    async def provider(strat: BaseStrategy, symbol: str) -> Optional[MarketData]:
        funding = await data_manager.get_funding_rate(symbol)
        if strat.strategy_id == "cex_dex_divergence":
            price_df = data_manager.get_dataframe(symbol, settings.ict_ltf_timeframe)
            price = float(price_df["close"].iloc[-1]) if price_df is not None and not price_df.empty else None
            dex = await _dex_quote(symbol)
            return MarketData(
                symbol=symbol, current_price=price, funding_rate=funding,
                extra={"dex": dex} if dex else {},
            )
        # ICT Levels (and any other dataframe strategy)
        df = data_manager.get_dataframe(symbol, settings.ict_ltf_timeframe)
        if df is None or df.empty:
            return None
        htf = data_manager.get_dataframe(symbol, settings.ict_htf_timeframe)
        htf_frames = {settings.ict_htf_timeframe: htf} if htf is not None and not htf.empty else {}
        return MarketData(
            symbol=symbol, dataframe=df, htf_dataframes=htf_frames,
            funding_rate=funding, current_price=float(df["close"].iloc[-1]),
        )

    return provider


async def start_smart_ai(app) -> Optional[SmartAiRunner]:
    """Build enabled strategies and start the runner, wired to the live scanner
    data + DB. No-ops (returns None) unless SMARTAI_ENABLED and at least one
    strategy is enabled, and requires the scanner to be up for its data feed."""
    settings = get_settings()
    if not settings.smartai_enabled:
        logger.info("Smart AI module disabled (SMARTAI_ENABLED=false).")
        return None

    strategies = build_enabled_strategies(settings)
    if not strategies:
        logger.info("Smart AI enabled but no strategy is switched on.")
        return None

    scanner = getattr(app.state, "scanner", None)
    if scanner is None or getattr(scanner, "data_manager", None) is None:
        logger.warning("Smart AI: scanner/data manager not available; runner not started.")
        return None

    runner = SmartAiRunner(
        strategies,
        symbols=list(scanner.data_manager.symbols),
        data_provider=_make_provider(scanner.data_manager, settings),
        sink=_persist,
        poll_interval=float(getattr(settings, "smartai_poll_interval", 60.0)),
    )
    await runner.start()
    app.state.smart_ai_runner = runner
    logger.success(
        "Smart AI runner started ({} strategy/ies): {}",
        len(strategies), ", ".join(s.strategy_id for s in strategies),
    )
    return runner
