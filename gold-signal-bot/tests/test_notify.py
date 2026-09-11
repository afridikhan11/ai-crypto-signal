"""Delivery, including the promise that no credential ever reaches a log."""
from __future__ import annotations

import io

import httpx
import pytest

from gold_signals.config import Settings
from gold_signals.notify import (
    ConsoleSender,
    NotifyError,
    TelegramSender,
    TwilioWhatsAppSender,
    _whatsapp_address,
    build_sender,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestConsole:
    def test_it_writes_the_message(self):
        buffer = io.StringIO()
        ConsoleSender(stream=buffer).send("hello")
        assert "hello" in buffer.getvalue()

    def test_it_needs_no_credentials_at_all(self):
        """The default channel, so a first run produces output before anyone
        has signed up for anything."""
        assert build_sender(Settings()).name == "console"


class TestWhatsAppAddresses:
    def test_a_bare_number_gets_the_whatsapp_prefix(self):
        """Twilio's error for a missing prefix names neither the field nor the
        fix, so normalise instead of explaining."""
        assert _whatsapp_address("+923001234567") == "whatsapp:+923001234567"

    def test_an_already_prefixed_number_is_left_alone(self):
        assert _whatsapp_address("whatsapp:+923001234567") == "whatsapp:+923001234567"

    def test_whitespace_is_stripped(self):
        assert _whatsapp_address("  +923001234567 ") == "whatsapp:+923001234567"

    def test_an_empty_number_stays_empty(self):
        assert _whatsapp_address("") == ""


class TestTwilio:
    def test_it_posts_the_message_with_basic_auth(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(201, json={"sid": "SM123"})

        TwilioWhatsAppSender("AC123", "tok", "+14155238886", "+923001234567",
                             client=_client(handler)).send("BUY GOLD")

        assert "Accounts/AC123/Messages.json" in seen["url"]
        assert "BUY" in seen["body"]
        assert "whatsapp" in seen["body"]
        assert seen["auth"].startswith("Basic ")

    def test_200_and_201_are_both_success(self):
        for code in (200, 201):
            sender = TwilioWhatsAppSender(
                "AC", "tok", "+1", "+2",
                client=_client(lambda r, c=code: httpx.Response(c, json={})),
            )
            sender.send("x")   # must not raise

    def test_401_names_the_two_settings_to_check(self):
        sender = TwilioWhatsAppSender(
            "AC", "super-secret", "+1", "+2",
            client=_client(lambda r: httpx.Response(401, json={"message": "denied"})),
        )
        with pytest.raises(NotifyError) as exc:
            sender.send("x")
        assert exc.value.status == 401
        assert "TWILIO_AUTH_TOKEN" in str(exc.value)
        assert "super-secret" not in str(exc.value)

    def test_other_failures_carry_the_status_and_the_bodys_explanation(self):
        sender = TwilioWhatsAppSender(
            "AC", "tok", "+1", "+2",
            client=_client(lambda r: httpx.Response(
                400, json={"message": "To number is not a valid WhatsApp endpoint"})),
        )
        with pytest.raises(NotifyError) as exc:
            sender.send("x")
        assert exc.value.status == 400
        assert "not a valid WhatsApp endpoint" in str(exc.value)

    def test_a_network_failure_is_a_notify_error(self):
        def handler(request):
            raise httpx.ConnectTimeout("timed out")

        sender = TwilioWhatsAppSender("AC", "tok", "+1", "+2", client=_client(handler))
        with pytest.raises(NotifyError, match="could not reach Twilio"):
            sender.send("x")


class TestTelegram:
    def test_it_posts_to_the_chat(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"ok": True})

        TelegramSender("123:ABC", "555", client=_client(handler)).send("BUY GOLD")
        assert "/bot123:ABC/sendMessage" in seen["url"]
        assert "chat_id=555" in seen["body"]

    def test_the_error_never_contains_the_url_because_the_token_is_in_it(self):
        """Telegram puts the bot token in the path. An error that echoes the
        URL leaks the credential into every log and screenshot."""
        sender = TelegramSender(
            "123:SUPERSECRET", "555",
            client=_client(lambda r: httpx.Response(400, json={"description": "chat not found"})),
        )
        with pytest.raises(NotifyError) as exc:
            sender.send("x")
        assert "SUPERSECRET" not in str(exc.value)
        assert "chat not found" in str(exc.value)


class TestBuildSender:
    def test_each_channel_builds_its_own_sender(self):
        assert build_sender(Settings(channel="console")).name == "console"
        assert build_sender(Settings(channel="whatsapp")).name == "whatsapp"
        assert build_sender(Settings(channel="telegram")).name == "telegram"

    def test_an_unknown_channel_is_rejected(self):
        with pytest.raises(ValueError, match="unknown SIGNAL_CHANNEL"):
            build_sender(Settings(channel="carrier-pigeon"))
