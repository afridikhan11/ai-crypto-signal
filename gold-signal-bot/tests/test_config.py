"""Settings: read from the environment, and never printed."""
from __future__ import annotations

import pytest

from gold_signals.config import CONSOLE, TELEGRAM, WHATSAPP, Settings


class TestDefaults:
    def test_the_defaults_are_the_owners_numbers(self):
        s = Settings()
        assert s.instrument == "XAU_USD"
        assert s.granularity == "M30"
        assert s.pip_size == 0.10          # 1 pip = $0.10 on gold
        assert s.entry_pips == 30.0
        assert s.stop_pips == 90.0
        assert s.rr == 2.0

    def test_the_default_channel_is_the_one_that_needs_no_account(self):
        assert Settings().channel == CONSOLE

    def test_the_default_feed_is_the_practice_account(self):
        """Nothing here can trade, but a live token has a live account behind
        it; defaulting to practice keeps a copied token harmless."""
        assert Settings().oanda_environment == "practice"


class TestFromEnv:
    def test_values_are_read_and_typed(self, monkeypatch):
        monkeypatch.setenv("OANDA_API_TOKEN", " tok ")
        monkeypatch.setenv("PIP_SIZE", "1.0")
        monkeypatch.setenv("MAX_BARS_SINCE_BREAK", "5")
        monkeypatch.setenv("SIGNAL_CHANNEL", "WhatsApp")
        s = Settings.from_env()
        assert s.oanda_token == "tok"          # whitespace stripped
        assert s.pip_size == 1.0
        assert s.max_bars_since_break == 5
        assert s.channel == "whatsapp"         # case-insensitive

    def test_the_instrument_is_upper_cased(self, monkeypatch):
        monkeypatch.setenv("INSTRUMENT", "xau_usd")
        assert Settings.from_env().instrument == "XAU_USD"

    def test_a_nonsense_number_falls_back_to_the_default(self, monkeypatch):
        """A typo in PIP_SIZE must not crash the bot at import time with a
        traceback nobody reads; `problems()` reports real mistakes instead."""
        monkeypatch.setenv("PIP_SIZE", "one tenth")
        assert Settings.from_env().pip_size == 0.10


class TestProblems:
    def test_a_complete_configuration_has_none(self):
        assert Settings(oanda_token="t", channel=CONSOLE).problems() == []

    def test_a_missing_feed_token_is_reported(self):
        problems = Settings().problems()
        assert any("OANDA_API_TOKEN" in p for p in problems)

    def test_every_problem_is_reported_at_once(self):
        """One round trip instead of five restarts."""
        problems = Settings(channel=WHATSAPP).problems()
        assert len(problems) >= 5   # the token plus all four Twilio settings

    def test_whatsapp_needs_all_four_twilio_settings(self):
        base = dict(oanda_token="t", channel=WHATSAPP)
        assert len(Settings(**base).problems()) == 4
        complete = Settings(
            **base, twilio_account_sid="AC", twilio_auth_token="tok",
            twilio_whatsapp_from="whatsapp:+1", whatsapp_to="whatsapp:+2",
        )
        assert complete.problems() == []

    def test_telegram_needs_its_token_and_chat(self):
        assert len(Settings(oanda_token="t", channel=TELEGRAM).problems()) == 2

    def test_an_unknown_channel_is_reported(self):
        problems = Settings(oanda_token="t", channel="sms").problems()
        assert any("SIGNAL_CHANNEL" in p for p in problems)

    def test_an_unknown_environment_is_reported(self):
        problems = Settings(oanda_token="t", oanda_environment="demo").problems()
        assert any("OANDA_ENVIRONMENT" in p for p in problems)

    def test_include_feed_false_checks_only_delivery(self):
        """So `--test-message` can prove WhatsApp works before an OANDA token
        exists - the order people actually set these up in."""
        s = Settings(channel=TELEGRAM, telegram_bot_token="t", telegram_chat_id="1")
        assert s.problems() != []                       # no OANDA token
        assert s.problems(include_feed=False) == []

    def test_degenerate_pip_settings_are_reported(self):
        assert any("PIP_SIZE" in p for p in Settings(oanda_token="t", pip_size=0).problems())
        assert any(
            "ENTRY_PIPS" in p
            for p in Settings(oanda_token="t", entry_pips=0, stop_pips=0).problems()
        )

    def test_a_punishing_poll_interval_is_reported(self):
        assert any("POLL_SECONDS" in p for p in Settings(oanda_token="t", poll_seconds=1).problems())


class TestSecretsAreNeverPrinted:
    """The first thing anyone does when a bot misbehaves is print its config
    into a log they later paste into a chat."""

    @pytest.mark.parametrize("field,value", [
        ("oanda_token", "oanda-secret-value"),
        ("twilio_auth_token", "twilio-secret-value"),
        ("telegram_bot_token", "telegram-secret-value"),
    ])
    def test_a_credential_never_appears_in_repr_or_str(self, field, value):
        s = Settings(**{field: value})
        assert value not in repr(s)
        assert value not in str(s)
        assert value not in f"{s}"
        assert "***set***" in repr(s)

    def test_an_unset_credential_is_labelled_as_such(self):
        assert "***unset***" in repr(Settings())

    def test_non_secret_settings_are_still_visible(self):
        """Masking everything would make the repr useless for debugging."""
        assert "XAU_USD" in repr(Settings())
        assert "'M30'" in repr(Settings())

    def test_the_account_sid_and_phone_numbers_are_not_masked(self):
        # They are identifiers, not credentials - and seeing the wrong number
        # is how a misdelivered signal gets diagnosed.
        s = Settings(twilio_account_sid="AC123", whatsapp_to="whatsapp:+92300")
        assert "AC123" in repr(s) and "+92300" in repr(s)
