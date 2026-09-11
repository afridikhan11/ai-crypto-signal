"""Settings, read from the environment.

One rule runs through this file: secrets are read, never printed. `Settings`
has a custom __repr__ that masks every credential, because the first thing
anyone does when a bot misbehaves is print its config into a log they later
paste somewhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import List

# Which env vars hold something that must never reach a log or a screenshot.
_SECRET_FIELDS = frozenset({
    "oanda_token",
    "twilio_auth_token",
    "telegram_bot_token",
})

CONSOLE = "console"
WHATSAPP = "whatsapp"
TELEGRAM = "telegram"
CHANNELS = (CONSOLE, WHATSAPP, TELEGRAM)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- what to watch -------------------------------------------------
    instrument: str = "XAU_USD"
    granularity: str = "M30"
    candle_count: int = 120

    # --- the setup -----------------------------------------------------
    # 1 pip = $0.10 on gold, the owner's convention. It is a setting and not
    # a constant because "a pip" on gold means different things to different
    # brokers, and getting it wrong changes the trade by a factor of ten.
    pip_size: float = 0.10
    entry_pips: float = 30.0
    stop_pips: float = 90.0
    rr: float = 2.0
    max_bars_since_break: int = 3
    lookback: int = 40

    # --- the feed ------------------------------------------------------
    oanda_token: str = ""
    oanda_environment: str = "practice"     # "practice" or "live"

    # --- delivery ------------------------------------------------------
    channel: str = CONSOLE
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""          # e.g. whatsapp:+14155238886
    whatsapp_to: str = ""                   # e.g. whatsapp:+923001234567
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- running -------------------------------------------------------
    poll_seconds: int = 60
    idle_poll_seconds: int = 900            # when the market is shut
    db_path: str = "signals.db"
    http_timeout: float = 20.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            instrument=_env("INSTRUMENT", "XAU_USD").upper(),
            granularity=_env("GRANULARITY", "M30").upper(),
            candle_count=_env_int("CANDLE_COUNT", 120),
            pip_size=_env_float("PIP_SIZE", 0.10),
            entry_pips=_env_float("ENTRY_PIPS", 30.0),
            stop_pips=_env_float("STOP_PIPS", 90.0),
            rr=_env_float("RISK_REWARD", 2.0),
            max_bars_since_break=_env_int("MAX_BARS_SINCE_BREAK", 3),
            lookback=_env_int("LOOKBACK", 40),
            oanda_token=_env("OANDA_API_TOKEN"),
            oanda_environment=_env("OANDA_ENVIRONMENT", "practice").lower(),
            channel=_env("SIGNAL_CHANNEL", CONSOLE).lower(),
            twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=_env("TWILIO_WHATSAPP_FROM"),
            whatsapp_to=_env("WHATSAPP_TO"),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            poll_seconds=_env_int("POLL_SECONDS", 60),
            idle_poll_seconds=_env_int("IDLE_POLL_SECONDS", 900),
            db_path=_env("DB_PATH", "signals.db"),
            http_timeout=_env_float("HTTP_TIMEOUT", 20.0),
        )

    def problems(self, include_feed: bool = True) -> List[str]:
        """Everything wrong with this configuration, all at once.

        Reported together rather than one-at-a-time so a first-time setup is
        a single round trip instead of five restarts.

        `include_feed=False` checks only delivery, so `--test-message` can
        prove WhatsApp works before an OANDA token exists - which is the
        order people actually set these things up in.
        """
        out: List[str] = []
        if include_feed:
            if not self.oanda_token:
                out.append("OANDA_API_TOKEN is not set - the bot has no price feed.")
            if self.oanda_environment not in ("practice", "live"):
                out.append(
                    f"OANDA_ENVIRONMENT is '{self.oanda_environment}'; "
                    "it must be 'practice' or 'live'."
                )
        if self.channel not in CHANNELS:
            out.append(
                f"SIGNAL_CHANNEL is '{self.channel}'; it must be one of {', '.join(CHANNELS)}."
            )
        if self.channel == WHATSAPP:
            for name, value in (
                ("TWILIO_ACCOUNT_SID", self.twilio_account_sid),
                ("TWILIO_AUTH_TOKEN", self.twilio_auth_token),
                ("TWILIO_WHATSAPP_FROM", self.twilio_whatsapp_from),
                ("WHATSAPP_TO", self.whatsapp_to),
            ):
                if not value:
                    out.append(f"{name} is required when SIGNAL_CHANNEL=whatsapp.")
        if self.channel == TELEGRAM:
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
                ("TELEGRAM_CHAT_ID", self.telegram_chat_id),
            ):
                if not value:
                    out.append(f"{name} is required when SIGNAL_CHANNEL=telegram.")
        if self.pip_size <= 0:
            out.append("PIP_SIZE must be greater than zero.")
        if self.entry_pips + self.stop_pips <= 0:
            out.append("ENTRY_PIPS + STOP_PIPS must be greater than zero, or entry == stop.")
        if self.poll_seconds < 5:
            out.append("POLL_SECONDS below 5 will rate-limit the feed; raise it.")
        return out

    def __repr__(self) -> str:  # pragma: no cover - exercised via tests below
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _SECRET_FIELDS:
                value = "***set***" if value else "***unset***"
            parts.append(f"{f.name}={value!r}")
        return f"Settings({', '.join(parts)})"

    __str__ = __repr__
