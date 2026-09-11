"""Delivery. WhatsApp via Twilio, Telegram, or the console.

WHY THREE CHANNELS
--------------------------------------------------------------------------
WhatsApp is what the owner asked for, and Twilio is the shortest route to it
- their sandbox sends a real WhatsApp message within minutes of signing up,
with no business verification. Meta's own Cloud API is cheaper at volume but
needs a verified business account, which takes days; when that is approved it
can be added here as a fourth sender without touching anything else.

Telegram is included because it is free, instant and needs no approval at
all, which makes it the right place to watch the bot for a week before paying
Twilio for messages that might be wrong.

Console is the default so the bot RUNS with no credentials at all. A first
run that needs an account before it can print anything is a first run nobody
completes.

Neither the Twilio auth token nor the Telegram bot token is ever put in a
message, an exception, or a log line.
"""
from __future__ import annotations

from typing import Optional, Protocol

import httpx

from .config import CONSOLE, TELEGRAM, WHATSAPP, Settings


class NotifyError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class Sender(Protocol):
    name: str

    def send(self, text: str) -> None:
        ...


class ConsoleSender:
    """Prints. Useful on its own, and the reason the bot needs no account to
    be tried out."""

    name = "console"

    def __init__(self, stream=None) -> None:
        self._stream = stream

    def send(self, text: str) -> None:
        import sys

        stream = self._stream or sys.stdout
        stream.write("\n" + "-" * 52 + "\n" + text + "\n" + "-" * 52 + "\n")
        stream.flush()


class TwilioWhatsAppSender:
    """Twilio's REST API directly - it is one authenticated POST, so the SDK
    would be a dependency earning nothing."""

    name = "whatsapp"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        client: Optional[httpx.Client] = None,
        timeout: float = 20.0,
    ) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._from = _whatsapp_address(from_number)
        self._to = _whatsapp_address(to_number)
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, text: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        try:
            response = self._client.post(
                url,
                data={"From": self._from, "To": self._to, "Body": text},
                auth=(self._sid, self._token),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"could not reach Twilio: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code in (200, 201):
            return
        if response.status_code == 401:
            raise NotifyError(
                "Twilio rejected the credentials (401). Check TWILIO_ACCOUNT_SID "
                "and TWILIO_AUTH_TOKEN.",
                status=401,
            )
        raise NotifyError(
            f"Twilio returned HTTP {response.status_code}: {response.text[:300]}",
            status=response.status_code,
        )


class TelegramSender:
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        client: Optional[httpx.Client] = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            response = self._client.post(
                url,
                data={"chat_id": self._chat_id, "text": text},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"could not reach Telegram: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code == 200:
            return
        # Telegram puts the token in the URL, so the URL must never appear in
        # the error - only the status and the body it sent back.
        raise NotifyError(
            f"Telegram returned HTTP {response.status_code}: {response.text[:300]}",
            status=response.status_code,
        )


def _whatsapp_address(number: str) -> str:
    """Twilio wants `whatsapp:+123...`; accept a bare number and fix it.

    Getting this wrong returns a Twilio error that names neither the field nor
    the fix, so it is cheaper to normalise than to explain.
    """
    text = number.strip()
    if not text:
        return text
    return text if text.startswith("whatsapp:") else f"whatsapp:{text}"


def build_sender(settings: Settings, client: Optional[httpx.Client] = None) -> Sender:
    if settings.channel == WHATSAPP:
        return TwilioWhatsAppSender(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_whatsapp_from,
            settings.whatsapp_to,
            client=client,
            timeout=settings.http_timeout,
        )
    if settings.channel == TELEGRAM:
        return TelegramSender(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            client=client,
            timeout=settings.http_timeout,
        )
    if settings.channel == CONSOLE:
        return ConsoleSender()
    raise ValueError(f"unknown SIGNAL_CHANNEL {settings.channel!r}")
