from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="AI_Crypto_Signal", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # SCHEMA OWNERSHIP (2026-07-31). Alembic is the single owner of the
    # database schema; `alembic upgrade head` runs from the container
    # command before uvicorn starts. This flag re-enables the quarantined
    # legacy bootstrap in app/core/legacy_schema_bootstrap.py
    # (`Base.metadata.create_all()` plus ~15 hand-written ALTER TABLE /
    # ALTER TYPE statements) and MUST stay False in any environment
    # Alembic manages - two owners is how the schema drifted away from
    # the migration history in the first place, leaving `alembic_version`
    # missing entirely. Provided only as an escape hatch for a throwaway
    # local database that Alembic does not manage.
    db_auto_bootstrap: bool = Field(default=False, alias="DB_AUTO_BOOTSTRAP")

    database_url: str = Field(
        default="postgresql+asyncpg://user:password@db:5432/crypto_signals",
        alias="DATABASE_URL",
    )

    redis_url: str = Field(
        default="redis://redis:6379/0",
        alias="REDIS_URL",
    )

    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_secret_key: str = Field(default="", alias="BINANCE_SECRET_KEY")

    secret_key: str = Field(default="change_me_in_production", alias="SECRET_KEY")
    access_token_expire_minutes: int = Field(
        default=30,
        alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    # Off by default so today's dev setup (WPF app calling the API with no
    # Authorization header) keeps working unchanged - see app/core/security.py
    # and app/api/dependencies.py. .env.production.example turns this on;
    # flipping it on requires ADMIN_USERNAME/ADMIN_PASSWORD_HASH to be set
    # (via scripts/generate_password_hash.py) and, if the WPF app is meant to
    # keep working, wiring a login step into it - see the auth module's
    # docstring for that follow-up.
    require_auth: bool = Field(default=False, alias="REQUIRE_AUTH")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password_hash: str = Field(default="", alias="ADMIN_PASSWORD_HASH")

    # Opt-in, testnet-only hands-off execution (app/scheduler/auto_executor.py).
    # Defaults OFF; even when true the executor refuses unless saved credentials
    # are testnet, so it can never place a mainnet order.
    auto_execute_testnet: bool = Field(default=False, alias="AUTO_EXECUTE_TESTNET")

    # ---- Trade-frequency & directional risk controls (2026-08-21) ----
    # Measured on testnet: fees consumed 72% of the bot's gross P/L (+226 gross,
    # -164 fees), driven by rapid re-entries on the same symbol (XRP shorted 6x
    # into a rally, AVAX repeatedly) and CANCELLED-then-reenter churn. These
    # gates cut that churn at the SIGNAL-CREATION layer, before any order or
    # fee exists. Each is independently tunable/disable-able via env.
    #
    # After a coin's trade CLOSES with a real outcome (TP_HIT / STOPPED /
    # CANCELLED - i.e. a position actually existed and paid fees), block a new
    # signal on that SAME coin for this many minutes. EXPIRED signals never
    # entered, cost nothing, and impose no cooldown. 0 disables.
    signal_reentry_cooldown_minutes: int = Field(
        default=60, alias="SIGNAL_REENTRY_COOLDOWN_MINUTES"
    )
    # Maximum live (pending or open) signals in the SAME direction across the
    # whole book at once. All 32 of the bot's 33 executed trades were shorts -
    # a single adverse reversal hits every position together. 0 disables.
    max_concurrent_same_direction: int = Field(
        default=3, alias="MAX_CONCURRENT_SAME_DIRECTION"
    )
    # How the HTF_OPPOSITION gate treats a counter-trend setup:
    #   "block"          - reject outright (the pre-2026-08-21 behavior).
    #   "confidence_bar" - allow it ONLY when confidence clears
    #                      counter_trend_min_confidence. The bar (80) sits
    #                      above every confidence the pipeline produced in the
    #                      first 136 live signals (68-79), so by default this
    #                      admits only genuinely exceptional counter-trend
    #                      setups instead of forcing hedges into existence.
    htf_opposition_mode: str = Field(
        default="confidence_bar", alias="HTF_OPPOSITION_MODE"
    )
    counter_trend_min_confidence: int = Field(
        default=80, alias="COUNTER_TREND_MIN_CONFIDENCE"
    )
    # FILL-RATE GATE: 71 of the first 136 signals (~52%) EXPIRED because price
    # never returned to the ICT entry zone. A symbol holds at most ONE live
    # signal, so a far-away pending entry squats on that slot for the whole
    # 12h expiry window and blocks nearer setups that would actually fill.
    # Skip creating a PENDING signal whose entry is further than this % from
    # the live price (market-mode signals enter at the live price and are
    # unaffected). Entry PRICING is untouched - this only filters, it never
    # moves an entry (the PR #21 regression lesson). 0 disables.
    max_pending_entry_distance_pct: float = Field(
        default=2.0, alias="MAX_PENDING_ENTRY_DISTANCE_PCT"
    )
    # DAILY TRADE CAP (owner's rule, 2026-08-24): the executor places at most
    # this many trades per ROLLING 24h, LONGs and SHORTs counted together.
    # A discipline brake, not a quality filter - it takes the first N, since
    # real-time cannot know the best N. It also naturally limits how many
    # resting limit orders can stack up on the exchange in one day (7 were
    # observed resting at once; resting orders can all fill together in one
    # move and no gate can stop a fill). Signals past the cap are still
    # RECORDED (paper) - only execution stops. 0 disables.
    max_trades_per_day: int = Field(default=3, alias="MAX_TRADES_PER_DAY")
    # OPEN-POSITIONS CAP (owner's rule, 2026-08-24): the 24h cap alone leaks -
    # 3 trades placed today that are still open tomorrow roll out of the 24h
    # window, and 3 MORE would be placed on top (6 concurrent, 6% at risk
    # together). This cap counts trades the executor placed that are still
    # LIVE (resting entry order or open position, any direction) and refuses
    # new placements until one closes: yesterday's open trades consume
    # today's allowance. 0 disables.
    max_open_positions: int = Field(default=3, alias="MAX_OPEN_POSITIONS")
    # WHICH PRICE TRIGGERS A PROTECTIVE STOP (2026-08-28). Binance defaults an
    # unspecified workingType to CONTRACT_PRICE - the LAST TRADED price - which
    # a single thin print can move. On a shallow book one wick then fires a
    # stop the real market never reached, booking the full planned loss for
    # nothing. MARK_PRICE is derived from an index across venues, is what
    # Binance itself liquidates on, and is the standard choice for protective
    # orders, so it is the default here.
    # This deliberately does NOT apply to a STOP_MARKET *entry*
    # (`place_stop_market_entry`), where the point is that price genuinely
    # TRADED through the level - that one keeps CONTRACT_PRICE.
    stop_working_type: str = Field(default="MARK_PRICE", alias="STOP_WORKING_TYPE")
    # WHOLE-POSITION STOPS (2026-08-28). A quantity-carrying reduceOnly stop
    # protects exactly the size it was placed with, which drifts out of step
    # with the real position: the partial take-profit banks 50% and the stop
    # still names the ORIGINAL quantity, a take-profit LIMIT part-fills in a
    # thin book, or a MARKET entry fills for slightly less than requested.
    # Whatever the stop does not name survives the trigger as an unprotected
    # residual - the dust left open after AAVE's TP_HIT.
    # `closePosition=true` tells Binance to flatten the WHOLE position when the
    # trigger fires, whatever it happens to be by then, so the stop can never
    # be the wrong size. It is mutually exclusive with quantity/reduceOnly.
    # Set false to go back to sized reduceOnly stops - relevant only if this
    # account ever holds a MANUAL position on a symbol the bot also trades,
    # since closePosition would close that too.
    stop_close_position: bool = Field(default=True, alias="STOP_CLOSE_POSITION")
    # MINIMUM STOP DISTANCE (2026-08-30). Structure can sit very close to
    # price, and the stops that came out of it were 0.15-0.21% from entry
    # (ETH 0.15%, DOT 0.17%, BTC 0.21%). In crypto that is inside ordinary
    # noise, and it fails three ways at once:
    #   - the stop is hit by a wick before the idea is right or wrong;
    #   - risk-based sizing (risk / stop distance) blows up until the notional
    #     cap catches it, so a round trip's fees reach ~47% of the whole
    #     planned loss - the fee drag that ate 72% of gross P/L before;
    #   - the stop can be refused outright with -2021 ("would immediately
    #     trigger") when price crosses it between fill and placement, which is
    #     exactly what happened to ETHUSDT on 2026-08-30.
    # A signal whose stop is nearer than this % of entry is not taken. 0
    # disables. This is a floor on the SIGNAL, never a nudge to the stop - the
    # stop stays exactly where structure put it (the PR #21 lesson: filter,
    # never move a price).
    min_stop_distance_pct: float = Field(default=0.6, alias="MIN_STOP_DISTANCE_PCT")
    # PER-POSITION NOTIONAL CAP (2026-08-25): risk-based sizing with a tight
    # structural stop produces a huge position (FIL ran ~2x equity on a ~0.5%
    # stop), and on tight stops EXECUTION cost becomes the real risk - the
    # software stop fills late and the "1%" loss lands bigger (COTI: -0.77%
    # price move booked as -1.4% of equity). Cap any single position's
    # notional to this % of equity: wide-stop trades size normally at full
    # 1% risk; tight-stop trades get capped smaller (risking LESS than 1%),
    # so the slippage overshoot is bounded. Institutional desks run exactly
    # this pairing (fixed-fractional risk + per-position gross cap). 0 disables.
    max_position_notional_pct: float = Field(
        default=50.0, alias="MAX_POSITION_NOTIONAL_PCT"
    )
    # PARTIAL TAKE-PROFIT (owner's idea, 2026-08-25): when an ACTIVE trade
    # moves signal_tp1_pct% in its favor, close signal_tp1_fraction of the
    # position and move the stop to breakeven; the remainder runs to the
    # signal's existing (structure-anchored) take_profit. Motivated directly
    # by the live record: a stream of trades trailed to breakeven and closed
    # at +0.00% - banking half at +2% turns those into ~+1% wins, at the cost
    # of half the payoff on the rare full runner. The final target is NOT
    # re-priced (the PR #21 lesson: never move the strategy's own levels);
    # when the structure target sits NEARER than TP1 there is simply no
    # partial and the trade runs exactly as before. 0 disables.
    signal_tp1_pct: float = Field(default=2.0, alias="SIGNAL_TP1_PCT")
    signal_tp1_fraction: float = Field(default=0.5, alias="SIGNAL_TP1_FRACTION")

    # ---- Smart AI module (app/strategy/base_strategy.py + strategies) ----
    # Master switch for the whole module and each strategy within it. Every
    # flag defaults OFF and the module defaults to TESTNET, so a fresh install
    # never trades a Smart AI strategy until it is explicitly turned on
    # (task: both strategies default to disabled + testnet on first run).
    smartai_enabled: bool = Field(default=False, alias="SMARTAI_ENABLED")
    smartai_testnet: bool = Field(default=True, alias="SMARTAI_TESTNET")
    smartai_poll_interval: float = Field(default=60.0, alias="SMARTAI_POLL_INTERVAL")
    strategy_ict_levels_enabled: bool = Field(
        default=False, alias="STRATEGY_ICT_LEVELS_ENABLED"
    )
    strategy_cex_dex_divergence_enabled: bool = Field(
        default=False, alias="STRATEGY_CEX_DEX_DIVERGENCE_ENABLED"
    )

    # ICT Levels strategy knobs (app/strategy/ict_levels_strategy.py). All
    # tunable so the same engine can be recalibrated without code changes.
    ict_htf_timeframe: str = Field(default="4h", alias="ICT_HTF_TIMEFRAME")
    ict_ltf_timeframe: str = Field(default="15m", alias="ICT_LTF_TIMEFRAME")
    ict_dealing_range_lookback: int = Field(default=50, alias="ICT_DEALING_RANGE_LOOKBACK")
    ict_equal_level_tolerance_pct: float = Field(
        default=0.05, alias="ICT_EQUAL_LEVEL_TOLERANCE_PCT"
    )
    ict_ote_low: float = Field(default=0.62, alias="ICT_OTE_LOW")
    ict_ote_high: float = Field(default=0.79, alias="ICT_OTE_HIGH")
    ict_min_risk_reward: float = Field(default=2.0, alias="ICT_MIN_RISK_REWARD")
    # No counter-HTF-bias entries unless this is explicitly enabled.
    ict_allow_counter_bias: bool = Field(default=False, alias="ICT_ALLOW_COUNTER_BIAS")

    # CEX-DEX Divergence strategy knobs (app/strategy/cex_dex_divergence_strategy.py).
    dexscreener_min_liquidity_usd: float = Field(
        default=100_000.0, alias="DEXSCREENER_MIN_LIQUIDITY_USD"
    )
    dexscreener_staleness_seconds: float = Field(
        default=120.0, alias="DEXSCREENER_STALENESS_SECONDS"
    )
    # A single divergence print is usually a bad quote; require it to persist
    # across this many consecutive polls before a signal may fire.
    divergence_min_consecutive_polls: int = Field(
        default=3, alias="DIVERGENCE_MIN_CONSECUTIVE_POLLS"
    )
    # Minimum |divergence_pct| to treat as a real dislocation, and how much the
    # (correlated-by-construction) funding rate is allowed to confirm it.
    divergence_threshold_pct: float = Field(default=1.0, alias="DIVERGENCE_THRESHOLD_PCT")
    divergence_funding_weight: float = Field(default=0.3, alias="DIVERGENCE_FUNDING_WEIGHT")
    # Cap position notional to this fraction of the DEX pool's USD liquidity.
    divergence_max_pool_depth_fraction: float = Field(
        default=0.01, alias="DIVERGENCE_MAX_POOL_DEPTH_FRACTION"
    )

    # Smart AI premium page owner gate (app/api/v1/endpoints/smartai.py). The
    # ONLY credential is a bcrypt hash of the owner password in the env - never
    # the plaintext, never in the repo, never in the WPF client (which
    # decompiles trivially). Tokens are signed with SECRET_KEY. Every
    # /api/v1/smartai/* route requires a valid token regardless of REQUIRE_AUTH.
    owner_password_hash: str = Field(default="", alias="OWNER_PASSWORD_HASH")
    smartai_token_expire_minutes: int = Field(
        default=720, alias="SMARTAI_TOKEN_EXPIRE_MINUTES"  # 12h access token
    )
    smartai_refresh_expire_minutes: int = Field(
        default=10_080, alias="SMARTAI_REFRESH_EXPIRE_MINUTES"  # 7d refresh token
    )
    smartai_login_max_attempts: int = Field(default=5, alias="SMARTAI_LOGIN_MAX_ATTEMPTS")
    smartai_login_window_minutes: int = Field(default=15, alias="SMARTAI_LOGIN_WINDOW_MINUTES")
    smartai_login_lockout_minutes: int = Field(
        default=15, alias="SMARTAI_LOGIN_LOCKOUT_MINUTES"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
