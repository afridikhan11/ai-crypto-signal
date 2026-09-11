"""The loop: read candles, find the setup, send it once.

The whole bot is `poll_once`. `run` is a `while True` around it with sleeps
and error handling, so every decision worth testing is testable without a
clock, a network or a phone.

THIS BOT PLACES NO ORDERS. There is no exchange client in this project and
no credential that could place one. It reads prices and sends messages.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .candles import CandleFeedError, OandaCandles, last_closed, latest_price
from .config import Settings
from .engulfing import bars_from_candles, break_still_valid, find_engulfing_break
from .notify import NotifyError, Sender, build_sender
from .signal import Signal, build_signal
from .store import SignalStore

log = logging.getLogger("gold_signals")


@dataclass(frozen=True)
class PollResult:
    """What one poll did, and why.

    `reason` exists so the log says "no engulfing break" rather than going
    silent - a bot that prints nothing for six hours is indistinguishable
    from a bot that died six hours ago, and that exact confusion cost the
    trading bot seven hours of downtime.
    """

    sent: bool
    reason: str
    signal: Optional[Signal] = None


def market_is_open(now: Optional[datetime] = None) -> bool:
    """Roughly, is spot gold trading?

    OANDA's week runs from Sunday evening to Friday evening New York time,
    which drifts by an hour with US daylight saving. This approximation is
    used ONLY to poll less often over the weekend - never to suppress a
    signal - so being an hour out at the edges costs nothing. If the market
    is in fact open, the feed simply returns candles and they are acted on.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    weekday, hour = moment.weekday(), moment.hour   # Monday == 0
    if weekday == 5:                                 # Saturday
        return False
    if weekday == 4 and hour >= 21:                  # Friday evening
        return False
    if weekday == 6 and hour < 21:                   # Sunday, before the open
        return False
    return True


def poll_once(
    settings: Settings,
    feed: OandaCandles,
    sender: Sender,
    store: SignalStore,
    now: Optional[datetime] = None,
) -> PollResult:
    candles = feed.fetch(settings.instrument, settings.granularity, settings.candle_count)
    if not candles:
        return PollResult(False, "the feed returned no candles")

    bars = bars_from_candles(candles)
    if len(bars) < 3:
        return PollResult(False, f"only {len(bars)} closed candles, need 3")

    setup = find_engulfing_break(bars, settings.max_bars_since_break, settings.lookback)
    if setup is None:
        return PollResult(
            False,
            f"no engulfing break within {settings.max_bars_since_break} closed candles",
        )

    # Where price is NOW, which is the forming candle's close when there is
    # one. A break is only tradeable while price is still on the right side
    # of the engulfed candle's extreme.
    price = latest_price(candles)
    if price is not None and not break_still_valid(setup, price):
        return PollResult(False, f"the {setup.direction} break has failed - price is back through it")

    closed_candle = last_closed(candles)
    candle_time = closed_candle.time if closed_candle else (now or datetime.now(timezone.utc))
    signal = build_signal(setup, settings, candle_time)
    if signal is None:
        return PollResult(False, "entry and stop came out equal - check PIP_SIZE and the pip counts")

    if store.already_sent(signal.key()):
        return PollResult(False, "this setup was already sent", signal=signal)

    sender.send(signal.message())
    store.record(signal, now=now)
    return PollResult(True, f"sent a {signal.direction} signal", signal=signal)


def run(settings: Settings, feed=None, sender=None, store=None, max_polls: Optional[int] = None) -> None:
    """Poll forever. `max_polls` stops after N polls, which is how the tests
    drive this without a timeout."""
    problems = settings.problems()
    if problems:
        for problem in problems:
            log.error("configuration: %s", problem)
        raise SystemExit(
            "Refusing to start with a broken configuration - see the errors above. "
            "Copy .env.example to .env and fill it in."
        )

    feed = feed or OandaCandles(
        settings.oanda_token, settings.oanda_environment, timeout=settings.http_timeout
    )
    sender = sender or build_sender(settings)
    store = store or SignalStore(settings.db_path)

    log.info(
        "watching %s %s via OANDA (%s), delivering to %s, %s signals already sent",
        settings.instrument, settings.granularity, settings.oanda_environment,
        sender.name, store.count(),
    )

    polls = 0
    consecutive_failures = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            result = poll_once(settings, feed, sender, store)
            consecutive_failures = 0
            if result.sent:
                log.info("SIGNAL %s", result.reason)
            else:
                log.debug("no signal: %s", result.reason)
        except (CandleFeedError, NotifyError) as exc:
            consecutive_failures += 1
            # Quiet once, loud when it persists: one failed poll is the
            # internet, ten in a row is a broken deployment.
            level = logging.ERROR if consecutive_failures >= 3 else logging.WARNING
            log.log(level, "poll failed (%s in a row): %s", consecutive_failures, exc)
        except Exception:  # noqa: BLE001 - the loop must outlive any one poll
            consecutive_failures += 1
            log.exception("poll failed unexpectedly (%s in a row)", consecutive_failures)

        if max_polls is not None and polls >= max_polls:
            break
        delay = settings.poll_seconds if market_is_open() else settings.idle_poll_seconds
        time.sleep(delay)
