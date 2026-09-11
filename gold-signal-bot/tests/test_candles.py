"""Reading OANDA, including the parts of its response Python cannot parse
unaided."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from gold_signals.candles import (
    HOSTS,
    CandleFeedError,
    OandaCandles,
    candle_from_payload,
    last_closed,
    latest_price,
    parse_oanda_time,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _row(time="2026-09-11T14:30:00.000000000Z", o="3900.0", h="3910.0",
         l="3890.0", c="3905.0", complete=True):
    return {"time": time, "volume": 100, "complete": complete,
            "mid": {"o": o, "h": h, "l": l, "c": c}}


class TestParseOandaTime:
    def test_nanosecond_precision_is_accepted(self):
        """OANDA stamps candles with nine fractional digits; Python's
        fromisoformat accepts at most six. Unhandled, this raises on every
        single candle."""
        parsed = parse_oanda_time("2026-09-11T14:30:00.000000000Z")
        assert parsed == datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)

    def test_a_plain_timestamp_still_works(self):
        assert parse_oanda_time("2026-09-11T14:30:00Z").hour == 14

    def test_an_offset_is_converted_to_utc(self):
        assert parse_oanda_time("2026-09-11T16:30:00+02:00") == datetime(
            2026, 9, 11, 14, 30, tzinfo=timezone.utc
        )

    def test_the_result_is_always_timezone_aware(self):
        assert parse_oanda_time("2026-09-11T14:30:00").tzinfo is not None


class TestCandleFromPayload:
    def test_mid_prices_become_floats(self):
        candle = candle_from_payload(_row())
        assert candle.open == 3900.0 and candle.close == 3905.0
        assert candle.complete is True
        assert candle.is_green is True

    def test_an_incomplete_candle_is_flagged(self):
        assert candle_from_payload(_row(complete=False)).complete is False

    def test_a_missing_complete_flag_reads_as_incomplete(self):
        row = _row()
        del row["complete"]
        assert candle_from_payload(row).complete is False

    def test_a_missing_price_is_an_error_not_a_zero(self):
        row = _row()
        del row["mid"]["h"]
        with pytest.raises(CandleFeedError, match="missing price fields"):
            candle_from_payload(row)


class TestFetch:
    def test_it_asks_for_mid_prices_at_the_configured_granularity(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"candles": [_row()]})

        feed = OandaCandles("secret-token", "practice", client=_client(handler))
        candles = feed.fetch("XAU_USD", "M30", 120)

        assert len(candles) == 1
        assert "instruments/XAU_USD/candles" in seen["url"]
        assert "granularity=M30" in seen["url"]
        assert "price=M" in seen["url"]
        assert "count=120" in seen["url"]
        assert seen["auth"] == "Bearer secret-token"

    def test_the_forming_candle_is_returned_not_hidden(self):
        """The caller needs it: checking whether a break is still valid needs
        where price is NOW, not where it was 30 minutes ago."""
        def handler(request):
            return httpx.Response(200, json={
                "candles": [_row(), _row(complete=False, c="3950.0")]
            })

        feed = OandaCandles("t", client=_client(handler))
        candles = feed.fetch("XAU_USD")
        assert [c.complete for c in candles] == [True, False]
        assert latest_price(candles) == 3950.0
        assert last_closed(candles).close == 3905.0

    def test_401_says_what_to_check_and_never_echoes_the_token(self):
        def handler(request):
            return httpx.Response(401, json={"errorMessage": "Insufficient authorization"})

        feed = OandaCandles("super-secret", client=_client(handler))
        with pytest.raises(CandleFeedError) as exc:
            feed.fetch("XAU_USD")
        assert exc.value.status == 401
        assert "OANDA_API_TOKEN" in str(exc.value)
        assert "super-secret" not in str(exc.value)

    def test_other_http_errors_carry_the_status(self):
        def handler(request):
            return httpx.Response(503, text="upstream unavailable")

        feed = OandaCandles("t", client=_client(handler))
        with pytest.raises(CandleFeedError) as exc:
            feed.fetch("XAU_USD")
        assert exc.value.status == 503

    def test_a_network_failure_is_a_feed_error_not_a_raw_httpx_error(self):
        """So the loop's one `except CandleFeedError` catches everything the
        feed can do, and an offline VM does not kill the bot."""
        def handler(request):
            raise httpx.ConnectError("no route to host")

        feed = OandaCandles("t", client=_client(handler))
        with pytest.raises(CandleFeedError, match="could not reach OANDA"):
            feed.fetch("XAU_USD")

    def test_a_body_that_is_not_json_is_reported_clearly(self):
        def handler(request):
            return httpx.Response(200, text="<html>proxy error</html>")

        feed = OandaCandles("t", client=_client(handler))
        with pytest.raises(CandleFeedError, match="not JSON"):
            feed.fetch("XAU_USD")

    def test_a_response_without_candles_is_an_error(self):
        def handler(request):
            return httpx.Response(200, json={"instrument": "XAU_USD"})

        feed = OandaCandles("t", client=_client(handler))
        with pytest.raises(CandleFeedError, match="no 'candles' field"):
            feed.fetch("XAU_USD")


class TestEnvironment:
    def test_practice_and_live_are_different_hosts(self):
        assert HOSTS["practice"] != HOSTS["live"]
        assert OandaCandles("t", "practice").base_url == HOSTS["practice"]
        assert OandaCandles("t", "live").base_url == HOSTS["live"]

    def test_an_unknown_environment_is_rejected_at_construction(self):
        """A typo here silently points a practice token at the live host and
        returns 401 forever; fail immediately instead."""
        with pytest.raises(ValueError, match="environment must be"):
            OandaCandles("t", "demo")


class TestHelpers:
    def test_latest_price_of_nothing_is_none(self):
        assert latest_price([]) is None

    def test_last_closed_of_nothing_is_none(self):
        assert last_closed([]) is None

    def test_last_closed_skips_the_forming_candle(self):
        candles = [candle_from_payload(_row()), candle_from_payload(_row(complete=False))]
        assert last_closed(candles) is candles[0]
