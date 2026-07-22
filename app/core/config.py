from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="AI_Crypto_Signal", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

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

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
