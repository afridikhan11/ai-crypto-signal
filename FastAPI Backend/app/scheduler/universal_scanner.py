"""
Universal Scanner - the ONE scanner for every asset this platform supports.

STATUS (2026-07-30): PRODUCTION. Replaces `CryptoScanner`
(`app/scheduler/scanner.py`, deleted in this phase) as part of the
Universal Institutional ICT Platform migration.

There is deliberately no `CryptoScanner`, `GoldScanner`, `ForexScanner` or
any other per-asset scanner in this codebase, and adding one would be an
architectural regression. Crypto, gold, silver and oil all flow through the
single path below; a future asset class is onboarded by adding an
`AssetProfile`, not a scanner.

SCANNER FLOW
--------------------------------------------------------------------------
    Load Symbols
        |
        v
    Determine Asset Type          app.core.constants.asset_type_for_symbol
        |
        v
    Load Asset Profile            app.assets.get_asset_profile
        |                          (market characteristics + calibration)
        v
    Run Universal ICT Pipeline    SignalGenerator._evaluate:
        |                          Market Structure -> Liquidity -> Order
        |                          Blocks -> FVG -> Supply/Demand ->
        |                          Premium/Discount -> Session/Kill Zones ->
        |                          Inducement -> OTE -> HTF Structure ->
        |                          Institutional Bias
        v
    Generate Evidence             ICTEvidenceEngine
        |
        v
    AI Decision                   AIScorer (confidence) ->
        |                          ICTDecisionEngine (decision + why)
        v
    Signal                        persisted + published, or a fully
                                   explained NO_TRADE

Every symbol takes that exact path. The only thing that differs between a
BTCUSDT scan and an XAUUSDT scan is the `AssetProfile` handed to the
pipeline (session windows, kill zones, optional stricter filters, and that
symbol's existing calibration).

NO RETAIL STRATEGY CODE
--------------------------------------------------------------------------
The previous scanner computed five EMA20/50 trend reads per symbol per scan
(`btc_trend`, 1h, 4h, 1d, 5m) via the retail helper now quarantined in
`app/legacy/trend.py`, and passed them into the signal generator. The ICT
pipeline no longer consumes them - higher-timeframe context comes from real
Market Structure (`HTFStructureEngine` / `InstitutionalBiasEngine`) - so
this scanner does not compute them at all. That removes five EMA passes per
symbol per scan and the last legacy import from the production path.

PERFORMANCE
--------------------------------------------------------------------------
  - One `SignalGenerator` per symbol, built once at construction (each
    holds its resolved AssetProfile and its stateless engines), not rebuilt
    per candle.
  - HTF dataframes reuse the data manager's existing caches: 1h/4h come
    from the live websocket candle cache, 1d/1w from long-TTL REST caches.
    No extra network calls are introduced by the ICT pipeline's HTF read.
  - Fundamentals/macro reads are unchanged and still only fetched for the
    asset classes they apply to.
"""
import asyncio
import json
import os
import time
from dataclasses import replace as _dataclass_replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select

from app.ai.calibration_profiles import TESTNET_MIN_CONFIDENCE
from app.assets.asset_profile import AssetProfile, get_asset_profile
from app.core.constants import (
    ASSET_CLASS_COMMODITY,
    PENDING_ENTRY_EXPIRY_MINUTES,
    asset_class_for_symbol,
    asset_type_for_symbol,
)
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.diagnostics.signal_pipeline_diagnostics import diagnostics
from app.models.coin import Coin
from app.models.signal import (
    NON_TERMINAL_STATUSES,
    Direction,
    Signal,
    SignalStatus,
)
from app.services import binance_credentials
from app.services.binance_service import BinanceDataManager
from app.services.fundamentals_service import FundamentalsService
from app.services.macro_fundamentals_service import MacroFundamentalsService
from app.services.trading_settings import get_engine_run_state, is_ict_pending_entry
from app.strategy.signal_generator import SignalGenerator
from app.smc.smt_divergence import default_reference_symbol

# Primary structure timeframe. SMC/ICT concepts (BOS/CHoCH, order blocks,
# FVG, liquidity sweeps) are institutional footprint that shows up on 15m+
# charts; 1m is dominated by retail noise and produces false breaks.
PRIMARY_TIMEFRAME = "15m"
PRIMARY_CANDLES = 500

# Higher timeframes fed to HTFStructureEngine. Ordered heaviest-first to
# match the engine's own top-down weighting.
HTF_TIMEFRAMES = ("1w", "1d", "4h", "1h")
HTF_CANDLES = 100


class UniversalScanner:
    """One scanner, one pipeline, every asset. See module docstring."""

    def __init__(self, symbols: List[str]):
        self.symbols = list(symbols)
        self.data_manager = BinanceDataManager(self.symbols)
        self.fundamentals = FundamentalsService()
        # Only queried for commodity symbols - DXY/real-yield/economic
        # calendar say nothing about crypto. Constructing it is free (lazy
        # HTTP client, only opened when actually called).
        self.macro_fundamentals = MacroFundamentalsService()

        # Resolve each symbol's Asset Profile ONCE, and build one pipeline
        # instance per symbol around it.
        self.asset_profiles: Dict[str, AssetProfile] = {
            symbol: get_asset_profile(symbol) for symbol in self.symbols
        }
        self.generators: Dict[str, SignalGenerator] = {
            symbol: SignalGenerator(symbol, asset_profile=self.asset_profiles[symbol])
            for symbol in self.symbols
        }
        self._apply_testnet_confidence_override()

        self._running = False
        self._symbol_locks = {symbol: asyncio.Lock() for symbol in self.symbols}
        # Strong references to the long-lived market-data tasks. Declared
        # here (not only in start()) so stop() is safe to call on a scanner
        # that was constructed but never started.
        self._ws_task: Optional[asyncio.Task] = None
        self._liquidation_task: Optional[asyncio.Task] = None

        logger.info(
            "UniversalScanner initialised for {} symbols across profiles: {}",
            len(self.symbols),
            sorted({p.display_name for p in self.asset_profiles.values()}),
        )

    def _apply_testnet_confidence_override(self) -> None:
        """
        TEMPORARY Binance Futures TESTNET validation change (2026-07-30).

        Lowers every symbol's live min_confidence gate from its
        CalibrationProfile default (85, unchanged for every asset class -
        see app/ai/calibration_profiles.py) to
        app.ai.calibration_profiles.TESTNET_MIN_CONFIDENCE (60), ONLY when
        the currently saved Binance credentials (app.services.binance_
        credentials - the SAME "environment" source account.py/dashboard.py/
        trading_control.py already use to label testnet vs mainnet) are
        pointed at Testnet. Mainnet credentials, or no saved credentials at
        all, leave every generator's profile exactly as
        get_asset_profile()/get_profile_for_symbol() already resolved it -
        this method is then a no-op.

        BACKTEST is unaffected because BacktestEngine never calls this
        method at all - it builds its own separate SignalGenerator directly
        (app/backtest/engine.py) and always uses each profile's real 85 (or
        an explicit min_confidence request param that has nothing to do
        with this override).

        Uses `dataclasses.replace()` to swap each generator's `.profile`
        for a copy with only `min_confidence` changed - the exact same
        mechanism BacktestEngine's own min_confidence override already
        uses, not a new one. No ICT logic, AI scoring algorithm, Risk
        Engine, Trade Management, or position sizing is touched - this
        changes one input number to an existing, unmodified gate.
        """
        creds = binance_credentials.load_credentials()
        if not creds or not creds.get("testnet"):
            return

        for generator in self.generators.values():
            generator.profile = _dataclass_replace(
                generator.profile, min_confidence=TESTNET_MIN_CONFIDENCE
            )

        logger.warning(
            "TESTNET credentials detected - AI minimum confidence threshold "
            f"temporarily lowered to {TESTNET_MIN_CONFIDENCE}% for Testnet validation "
            "(Backtest and Mainnet are unaffected and continue using each asset "
            "class's normal 85% threshold)."
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self):
        logger.info("Initializing historical market data...")
        await self.data_manager.initialise_historical_data()
        logger.success("Historical data loaded successfully.")

        for symbol in self.symbols:
            try:
                await self.analyze_symbol(symbol)
            except Exception as e:
                logger.exception(f"Initial scan failed for {symbol}: {e}")

        logger.success("Initial universal market scan completed.")

        self.data_manager.set_on_candle_callback(self.on_new_candle)
        self._running = True

        # MARKET-DATA TASKS - held, supervised, and cancelled on stop.
        #
        # These were previously bare `asyncio.create_task(...)` calls whose
        # results were discarded. Two real hazards, both silent:
        #
        #   1. asyncio holds only a WEAK reference to a running task. A task
        #      with no strong reference anywhere can be garbage-collected
        #      mid-flight ("Task was destroyed but it is pending!").
        #   2. If either coroutine raises above its own retry loop, the
        #      exception is swallowed into the unretrieved task result and
        #      nothing logs it.
        #
        # Either way the candle stream stops while the scanner keeps
        # running. `get_dataframe()` deliberately returns the last cached
        # frame "even if stale" (binance_service.py), so the scanner would
        # go on generating signals from FROZEN PRICES with no error
        # anywhere - a trading-correctness failure, not just a reliability
        # one. Holding the references and attaching done-callbacks makes a
        # dead stream loud and prevents the GC case entirely.
        # REST candle polling is the reliable, WEBSOCKET-INDEPENDENT data feed:
        # some networks complete the Binance ws handshake but never receive
        # stream data (silently freezing all scanning), while REST still works.
        # It runs ALWAYS. The websocket is an optional faster feed layered on
        # top - disable it with BINANCE_WS_ENABLED=false where it delivers no
        # data, to avoid the every-60s stall/reconnect log noise.
        self._rest_poll_task = asyncio.create_task(self.data_manager.start_rest_polling())
        supervised = [("REST candle poller", self._rest_poll_task)]

        if os.getenv("BINANCE_WS_ENABLED", "true").strip().lower() != "false":
            self._ws_task = asyncio.create_task(self.data_manager.start_websocket())
            supervised.append(("market data websocket", self._ws_task))
        else:
            self._ws_task = None
            logger.info(
                "Market-data websocket DISABLED (BINANCE_WS_ENABLED=false) - "
                "using REST candle polling only."
            )

        self._liquidation_task = asyncio.create_task(
            self.data_manager.start_liquidation_stream()
        )
        supervised.append(("liquidation stream", self._liquidation_task))

        for name, task in supervised:
            task.add_done_callback(
                lambda t, _name=name: self._market_data_task_done(_name, t)
            )
        logger.info("Universal scanner started.")

    @staticmethod
    def _market_data_task_done(name: str, task: asyncio.Task) -> None:
        """Surface the death of a market-data task instead of swallowing it.

        A completed task here is ALWAYS notable: both coroutines are meant
        to run for the lifetime of the process. Normal shutdown cancels
        them, which is the only non-alarming outcome.
        """
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            logger.info(f"{name} task cancelled (normal shutdown).")
            return
        if exc is not None:
            logger.exception(
                f"{name} task DIED: {exc!r}. Candle data is no longer "
                f"updating; any signal generated from here on would use "
                f"stale prices. Restart the scanner."
            )
        else:
            logger.error(
                f"{name} task returned unexpectedly with no exception. It is "
                f"meant to run for the process lifetime; market data is no "
                f"longer updating."
            )

    async def stop(self):
        self._running = False
        # Cancel before closing the data manager so neither task is left
        # awaiting a socket that is being torn down underneath it.
        for task in (getattr(self, "_ws_task", None),
                     getattr(self, "_rest_poll_task", None),
                     getattr(self, "_liquidation_task", None)):
            if task is not None and not task.done():
                task.cancel()
        await self.data_manager.stop()
        await self.fundamentals.close()
        await self.macro_fundamentals.close()

    @property
    def is_running(self) -> bool:
        """Whether the scanner's own asyncio lifecycle is alive (started, not
        yet stopped) - distinct from `engine_run_state` (Pause/Resume), which
        governs whether it currently ANALYZES new candles. Read by
        `GET /trading/status` for the Auto Trading Control panel's Scanner
        Status indicator."""
        return self._running

    async def on_new_candle(self, symbol: str, interval: str, candles):
        # Analysis is triggered off a CLOSED primary-timeframe candle, so
        # scan frequency matches how SMC setups actually form.
        if interval != PRIMARY_TIMEFRAME:
            return

        # Auto Trading Control Panel (2026-07-30) - Pause/Stop gate. The
        # WebSocket candle stream and data manager caches keep running
        # regardless (so Resume/Start is instant, no re-warm needed) - only
        # the ICT pipeline analysis that could PRODUCE a new signal is
        # skipped. Defaults "running", so this is a no-op until a user
        # explicitly pauses/stops from the panel. See
        # app/services/trading_settings.py for the full design note.
        if get_engine_run_state() != "running":
            return

        async with self._symbol_locks[symbol]:
            await self.analyze_symbol(symbol)

    # ------------------------------------------------------------------
    # Data assembly
    # ------------------------------------------------------------------
    async def _load_htf_dataframes(self, symbol: str) -> Dict[str, Any]:
        """
        Real higher-timeframe OHLCV for `HTFStructureEngine`.

        1h/4h come from the live-streamed candle cache and 1d/1w from
        long-TTL REST caches, so this adds no network round-trips beyond
        what the data manager already does. A timeframe that comes back
        empty (e.g. a newly-listed symbol with no weekly history) is simply
        omitted - HTFStructureEngine and InstitutionalBiasEngine both
        degrade that honestly rather than guessing.
        """
        frames: Dict[str, Any] = {}
        for timeframe in HTF_TIMEFRAMES:
            try:
                if timeframe == "1w":
                    df = await self.data_manager.get_weekly_dataframe(symbol, limit=HTF_CANDLES)
                elif timeframe == "1d":
                    df = await self.data_manager.get_daily_dataframe(symbol, limit=HTF_CANDLES)
                else:
                    df = self.data_manager.get_dataframe(symbol, timeframe, limit=HTF_CANDLES)
            except Exception as e:
                logger.warning(f"{symbol}: could not load {timeframe} candles for HTF structure: {e}")
                continue
            if df is not None and not df.empty:
                frames[timeframe] = df
        return frames

    async def _load_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Macro/positioning context. Which reads apply is decided by the
        symbol's asset CLASS, not by a separate code path: crypto gets
        crypto-market sentiment, commodities get dollar/real-yield/event
        risk. Both end up in the same dict consumed by the same pipeline.
        """
        oi_data = await self.fundamentals.get_open_interest_trend(symbol)
        long_short_ratio = await self.fundamentals.get_long_short_ratio(symbol)
        fear_greed = await self.fundamentals.get_fear_greed_index()
        btc_dominance = await self.fundamentals.get_btc_dominance()

        fundamentals: Dict[str, Any] = {
            "oi_trend": oi_data.get("oi_trend", "flat"),
            "oi_change_pct": oi_data.get("oi_change_pct", 0.0),
            "long_short_ratio": long_short_ratio,
            "fear_greed_value": fear_greed.get("value", 50),
            "btc_dominance": btc_dominance,
        }

        if asset_class_for_symbol(symbol) == ASSET_CLASS_COMMODITY:
            dxy = await self.macro_fundamentals.get_dollar_index_trend()
            real_yield = await self.macro_fundamentals.get_real_yield_trend()
            event_risk = await self.macro_fundamentals.get_high_impact_event_risk()
            event_name = event_risk.get("event_name")
            fundamentals.update({
                "dxy_trend": dxy.get("dxy_trend", "flat"),
                "dxy_change_pct": dxy.get("dxy_change_pct", 0.0),
                "real_yield_trend": real_yield.get("real_yield_trend", "flat"),
                "high_impact_event_soon": event_risk.get("high_impact_event_soon", False),
                "event_name": event_name,
                # Ranked by THIS asset's own profile rather than a global
                # assumption about which releases matter.
                "event_priority": self.asset_profiles[symbol].economic_events.priority_of(event_name),
            })

        return fundamentals

    # ------------------------------------------------------------------
    # The one scan path
    # ------------------------------------------------------------------
    async def analyze_symbol(self, symbol: str):
        try:
            profile = self.asset_profiles[symbol]
            logger.info(
                f"[{symbol}] Universal scan started | asset_type={profile.asset_type} "
                f"| profile={profile.display_name}"
            )

            df = self.data_manager.get_dataframe(symbol, PRIMARY_TIMEFRAME, limit=PRIMARY_CANDLES)
            if df.empty:
                logger.warning(f"[{symbol}] No {PRIMARY_TIMEFRAME} candles available")
                diagnostics.record_market_data(symbol, has_candles=False)
                diagnostics.maybe_log_summary()
                return
            diagnostics.record_market_data(symbol, has_candles=True)

            # Live 1m close used ONLY for a precise entry/zone-classification
            # price, so the signal isn't stale by up to a full primary
            # candle. It never affects structure detection.
            df_1m = self.data_manager.get_dataframe(symbol, "1m", limit=5)
            live_price = float(df_1m["close"].iloc[-1]) if not df_1m.empty else None

            funding_rate = await self.data_manager.get_funding_rate(symbol)
            liquidation_pressure = self.data_manager.get_liquidation_pressure(symbol)
            htf_dataframes = await self._load_htf_dataframes(symbol)
            fundamentals = await self._load_fundamentals(symbol)

            # Correlated reference frame for SMT (Smart Money Technique)
            # divergence. Only available when the partner symbol is one this
            # scanner is already streaming; otherwise SMT simply doesn't run
            # (advisory, like any other engine that finds nothing). Never lets
            # a reference-data hiccup break the primary scan.
            reference_symbol = default_reference_symbol(symbol)
            reference_df = None
            if reference_symbol != symbol and reference_symbol in self.symbols:
                try:
                    _ref = self.data_manager.get_dataframe(
                        reference_symbol, PRIMARY_TIMEFRAME, limit=PRIMARY_CANDLES
                    )
                    if _ref is not None and not _ref.empty:
                        reference_df = _ref
                except Exception as _e:  # noqa: BLE001 - SMT is advisory
                    logger.debug(f"[{symbol}] SMT reference {reference_symbol} unavailable: {_e}")

            # ONE pipeline, ONE AI, for every asset - and ONE pass over the
            # data. `evaluate()` returns both the fully-explained decision
            # and the signal dict from a single run, so a NO_TRADE is logged
            # with its real reasons instead of vanishing, without scanning
            # the same candle twice.
            _eval_started = time.perf_counter()
            decision, signal_data = self.generators[symbol].evaluate(
                df,
                funding_rate=funding_rate,
                entry_price=live_price,
                liquidation_pressure=liquidation_pressure,
                fundamentals=fundamentals,
                htf_dataframes=htf_dataframes or None,
                reference_df=reference_df,
                reference_symbol=reference_symbol,
            )
            # Diagnostics-only observation of the decision `evaluate()`
            # already computed and returned above - see
            # app/diagnostics/signal_pipeline_diagnostics.py's module
            # docstring for why this cannot affect the decision itself.
            diagnostics.record_evaluation(
                symbol, decision, (time.perf_counter() - _eval_started) * 1000.0
            )

            if not decision.is_tradeable or signal_data is None:
                logger.info(
                    f"[{symbol}] NO TRADE | confidence={decision.confidence} "
                    f"| gates={decision.blocking_gates} "
                    f"| missing={[m.component for m in decision.missing_evidence]}"
                )
                diagnostics.maybe_log_summary()
                return

            logger.success(
                f"[{symbol}] SIGNAL | {signal_data['direction']} "
                f"| confidence={signal_data['confidence']} | RR={signal_data['risk_reward']}"
            )
            await self.save_signal(signal_data)
            diagnostics.maybe_log_summary()

        except Exception as e:
            logger.exception(f"Error analyzing {symbol}: {e}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def save_signal(self, data: Dict[str, Any]):
        async with AsyncSessionLocal() as session:
            stmt = select(Coin).where(Coin.symbol == data["symbol"].upper())
            coin = (await session.execute(stmt)).scalar_one_or_none()

            if coin is None:
                coin = Coin(
                    symbol=data["symbol"].upper(),
                    asset_class=asset_class_for_symbol(data["symbol"]),
                )
                session.add(coin)
                await session.flush()

            # A symbol may hold at most ONE live signal, where "live" now
            # covers both a pending ICT entry and an open trade. Without
            # PENDING_ENTRY in this check a symbol could accumulate a
            # resting entry AND an open position simultaneously.
            stmt = select(Signal).where(
                Signal.coin_id == coin.id,
                Signal.status.in_(NON_TERMINAL_STATUSES),
            )
            if (await session.execute(stmt)).scalar_one_or_none():
                logger.info(f"{data['symbol']}: A pending or active signal already exists.")
                diagnostics.record_database_save(data["symbol"], saved=False, duplicate=True)
                return

            # ICT Pending Limit Entry (2026-07-30): under entry_mode
            # "ict_pending" a signal is BORN pending - the ICT entry zone
            # has been identified but price has not traded into it yet, so
            # no position exists and nothing is at risk until it fills.
            # Under entry_mode "market" the signal is born ACTIVE at the
            # live price, exactly as before. One switch, both subsystems.
            pending_mode = is_ict_pending_entry()
            now = datetime.now(timezone.utc)
            signal = Signal(
                coin_id=coin.id,
                direction=Direction[data["direction"]],
                entry_price=data["entry"],
                # Both stops start identical. `stop_loss` is then free to be
                # moved by Trade Management for the life of the trade;
                # `initial_stop_loss` is frozen here and is what every
                # risk/sizing/exposure calculation reads forever after.
                stop_loss=data["stop_loss"],
                initial_stop_loss=data["stop_loss"],
                take_profit=data["take_profit"],
                risk_reward=data["risk_reward"],
                confidence=data["confidence"],
                reason=data["reason"],
                timeframe=PRIMARY_TIMEFRAME,
                ai_model_version="2.0.0-universal-ict",
                score_breakdown=data.get("score_breakdown"),
                status=SignalStatus.PENDING_ENTRY if pending_mode else SignalStatus.ACTIVE,
                entry_type=data.get("entry_type"),
                entry_zone_top=data.get("entry_zone_top"),
                entry_zone_bottom=data.get("entry_zone_bottom"),
                entry_expires_at=(
                    now + timedelta(minutes=PENDING_ENTRY_EXPIRY_MINUTES) if pending_mode else None
                ),
                # A market-mode signal is live at the live price from the
                # instant it is created, so it is "filled" by definition.
                filled_at=None if pending_mode else now,
                actual_fill_price=None if pending_mode else data["entry"],
            )
            session.add(signal)
            await session.commit()
            diagnostics.record_database_save(data["symbol"], saved=True, duplicate=False)

            logger.success(
                f"Saved signal: {data['symbol']} {data['direction']} "
                f"Confidence={data['confidence']} | status={signal.status.value} "
                f"| entry={data['entry']} ({data.get('entry_type')})"
            )

            # The full explainability payload (why long/short, evidence,
            # missing evidence, risk, invalidation) rides along here. It is
            # NOT persisted - that needs a new Signal column and this repo
            # has no migration tool configured; see
            # AI_DECISION_ENGINE_IMPLEMENTATION_REPORT.md.
            await redis_client.publish("new_signal", json.dumps(data, default=str))
            diagnostics.record_wpf_update(data["symbol"], published=True)
