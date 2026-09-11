"""The price feed: OANDA v20 candles for XAU_USD.

WHY OANDA AND NOT TRADINGVIEW
--------------------------------------------------------------------------
The owner watches this setup on a TradingView chart, and TradingView has no
public API to poll - its data can only leave through a Pine Script alert
webhook, which needs a paid plan and the strategy rewritten in Pine. The
owner does not want to look at a chart, only to receive signals, so the chart
is not needed at all: OANDA's own REST API serves the same XAU_USD candles
that TradingView's OANDA feed draws, from a free practice account.

The prices will not match a Binance XAUUSDT chart, and that is correct rather
than a bug - spot gold at OANDA and a USDT-margined perpetual on Binance are
different instruments with different prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

import httpx

HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class CandleFeedError(RuntimeError):
    """The feed could not be read. Carries the HTTP status when there was one.

    The message deliberately never includes the request headers, because the
    bearer token lives there.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    complete: bool

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open


def parse_oanda_time(raw: str) -> datetime:
    """RFC3339 as OANDA writes it, which Python cannot read unaided.

    OANDA stamps candles with NANOSECOND precision - "2026-09-11T14:30:00.
    000000000Z" - and `datetime.fromisoformat` accepts at most microseconds.
    Truncating the fraction to six digits is exact for our purposes: a 30
    minute candle is never distinguished by its nanoseconds.
    """
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        offset = tail[len(digits):]
        text = f"{head}.{digits[:6]}{offset}"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def candle_from_payload(row: Any) -> Candle:
    mid = row.get("mid") or {}
    missing = [k for k in ("o", "h", "l", "c") if k not in mid]
    if missing:
        raise CandleFeedError(f"candle is missing price fields: {', '.join(missing)}")
    return Candle(
        time=parse_oanda_time(row["time"]),
        open=float(mid["o"]),
        high=float(mid["h"]),
        low=float(mid["l"]),
        close=float(mid["c"]),
        # Absent `complete` is treated as INCOMPLETE. If the feed ever stops
        # sending the flag, the safe reading is "this candle may still be
        # forming" - detection then ignores it instead of trading a close
        # that has not happened.
        complete=bool(row.get("complete", False)),
    )


class OandaCandles:
    """Reads completed candles. Holds the token; never logs it."""

    def __init__(
        self,
        token: str,
        environment: str = "practice",
        client: Optional[httpx.Client] = None,
        timeout: float = 20.0,
    ) -> None:
        if environment not in HOSTS:
            raise ValueError(f"environment must be one of {sorted(HOSTS)}, got {environment!r}")
        self._token = token
        self.base_url = HOSTS[environment]
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def fetch(self, instrument: str, granularity: str = "M30", count: int = 120) -> List[Candle]:
        """Candles oldest-first, INCLUDING the one still forming.

        The forming candle is returned rather than hidden so the caller can
        use its close as the current price - checking whether the break is
        still valid needs where price is NOW, not where it was 30 minutes ago.
        Detection itself drops it.
        """
        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        try:
            response = self._client.get(
                url,
                params={"granularity": granularity, "count": count, "price": "M"},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise CandleFeedError(f"could not reach OANDA: {exc.__class__.__name__}: {exc}") from exc

        if response.status_code == 401:
            raise CandleFeedError(
                "OANDA rejected the token (401). Check OANDA_API_TOKEN and that "
                "OANDA_ENVIRONMENT matches the account the token was issued for.",
                status=401,
            )
        if response.status_code != 200:
            raise CandleFeedError(
                f"OANDA returned HTTP {response.status_code}: {response.text[:300]}",
                status=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CandleFeedError(f"OANDA returned a body that is not JSON: {exc}") from exc

        rows = payload.get("candles")
        if rows is None:
            raise CandleFeedError("OANDA response had no 'candles' field")
        return [candle_from_payload(row) for row in rows]

    def close(self) -> None:
        self._client.close()


def latest_price(candles: List[Candle]) -> Optional[float]:
    """The most recent close, forming candle included - price as of now."""
    return candles[-1].close if candles else None


def last_closed(candles: List[Candle]) -> Optional[Candle]:
    for candle in reversed(candles):
        if candle.complete:
            return candle
    return None
