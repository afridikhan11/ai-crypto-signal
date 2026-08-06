from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_name: str = Field(default="AI_Crypto_Signal", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="production", alias="ENVIRONMENT")

    # ------------------------------------------------------------------
    # Datastores
    # ------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://user:password@db:5432/crypto_signals",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # ------------------------------------------------------------------
    # Binance
    # ------------------------------------------------------------------
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_secret_key: str = Field(default="", alias="BINANCE_SECRET_KEY")

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    secret_key: str = Field(default="change_me_in_production", alias="SECRET_KEY")
    access_token_expire_minutes: int = Field(
        default=30, alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    # When empty, API auth is DISABLED (open) — keeps the desktop client working
    # out of the box. Set an API key in production to require an X-API-Key header.
    api_key: str = Field(default="", alias="API_KEY")

    # Comma-separated list of allowed CORS origins ("*" allows all)
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    # ------------------------------------------------------------------
    # Scanner / Strategy (multi-timeframe ICT)
    # ------------------------------------------------------------------
    # Higher-timeframes used to establish directional bias
    htf_timeframes: str = Field(default="4h,1h", alias="HTF_TIMEFRAMES")
    # Lower-timeframe used to trigger analysis and refine entries
    ltf_timeframe: str = Field(default="15m", alias="LTF_TIMEFRAME")
    # All timeframes the data manager should stream/cache
    stream_timeframes: str = Field(
        default="5m,15m,1h,4h", alias="STREAM_TIMEFRAMES"
    )

    # Minimum AI confidence (0-100) required to publish a signal
    min_confidence: int = Field(default=65, alias="MIN_CONFIDENCE")
    # Minimum acceptable risk/reward for a signal to be published
    min_risk_reward: float = Field(default=1.5, alias="MIN_RISK_REWARD")

    # Restrict signal generation to ICT kill zones (London / NY sessions).
    # When True, setups outside kill zones are rejected.
    enforce_killzones: bool = Field(default=False, alias="ENFORCE_KILLZONES")

    # Number of top symbols to scan (see market.universe)
    scan_symbols: str = Field(default="", alias="SCAN_SYMBOLS")

    # ------------------------------------------------------------------
    # Signal lifecycle tracker
    # ------------------------------------------------------------------
    tracker_enabled: bool = Field(default=True, alias="TRACKER_ENABLED")
    tracker_interval_seconds: int = Field(default=15, alias="TRACKER_INTERVAL_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> List[str]:
        raw = self.cors_origins.strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def htf_list(self) -> List[str]:
        return [tf.strip() for tf in self.htf_timeframes.split(",") if tf.strip()]

    @property
    def stream_tf_list(self) -> List[str]:
        # Ensure LTF and all HTFs are always present in the stream set
        tfs = [tf.strip() for tf in self.stream_timeframes.split(",") if tf.strip()]
        for tf in [self.ltf_timeframe, *self.htf_list]:
            if tf not in tfs:
                tfs.append(tf)
        return tfs

    @property
    def scan_symbol_list(self) -> List[str]:
        return [s.strip().upper() for s in self.scan_symbols.split(",") if s.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key.strip())

    @field_validator("min_confidence")
    @classmethod
    def _clamp_confidence(cls, v: int) -> int:
        return max(0, min(100, v))


@lru_cache
def get_settings() -> Settings:
    return Settings()
