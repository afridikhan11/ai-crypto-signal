"""
Is this process actually doing its job right now?

WHY THIS EXISTS (2026-09-10)
--------------------------------------------------------------------------
The test suite passed 1159 tests, the deploy reported success, `docker
compose ps` said `Up` - and the bot had not monitored a single signal for
seven hours. A relationship added to `Signal` pointed at a class the
application's import path never loaded, so every ORM query raised at
mapper-configuration time. Nothing noticed. It was found because a human
happened to run a check.

That is the failure worth engineering against. Bugs will keep happening;
what must not keep happening is a silent outage measured in hours.

`Up` is not health. A container stays `Up` while the loops inside it are
dead, wedged, or raising every cycle. Health is: the scanner completed a
sweep recently, the monitor completed a poll recently, and the database
answers. This module is the one place that knows.

DESIGN
--------------------------------------------------------------------------
Beat, don't ask. Each loop calls `beat()` when it finishes real work; the
health check reads how long ago that was. There is no way to "ask" an
asyncio task whether it is healthy, and a task that raises on every
iteration is not cancelled - it just retries forever, which is exactly the
shape today's outage took.

Every function here is total and never raises: health reporting must not
be able to break the thing it reports on.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

# Components that must be beating for this process to be doing its job.
SCANNER = "scanner"
MONITOR = "signal_monitor"

# How stale a beat may be before the component counts as unhealthy.
#
# The scanner sweeps on closed 15m candles but REST-polls every 60s; the
# monitor polls every ~30s. A full sweep of ~35 symbols takes well under a
# minute. These bounds are several multiples of the real cadence, so a
# breach means something is genuinely wrong, not merely slow.
STALE_AFTER_SECONDS: Dict[str, float] = {
    SCANNER: 900.0,    # 15 minutes
    MONITOR: 300.0,    # 5 minutes
}

_beats: Dict[str, float] = {}
_started_at = time.time()


def beat(component: str) -> None:
    """Record that `component` just completed real work."""
    _beats[component] = time.time()


def last_beat(component: str) -> Optional[float]:
    """Seconds since `component` last beat, or None if it never has."""
    stamp = _beats.get(component)
    return None if stamp is None else time.time() - stamp


def uptime_seconds() -> float:
    return time.time() - _started_at


def reset() -> None:
    """Clear all beats - for tests only."""
    _beats.clear()


def component_status(component: str, grace_seconds: Optional[float] = None) -> dict:
    """One component's health.

    `grace_seconds` covers startup: a loop that has never beaten is only a
    failure once the process has been up long enough that it should have.
    Without this every restart would report unhealthy for its first cycle
    and teach everyone to ignore the check.

    THE GRACE IS PER-COMPONENT (2026-09-15). It defaults to that component's
    own staleness limit, because a loop cannot be called late until one full
    cycle of its own has had time to pass. A flat two minutes was wrong for
    the scanner: it beats on CLOSED 15m candles, so after a restart it has
    nothing to report for up to fifteen minutes - and reported "never ran",
    503, on a perfectly healthy deployment for thirteen of them. A check that
    cries wolf after every restart is a check nobody reads, which is the one
    outcome this module exists to prevent.
    """
    age = last_beat(component)
    limit = STALE_AFTER_SECONDS.get(component, 300.0)
    if grace_seconds is None:
        grace_seconds = limit
    if age is None:
        healthy = uptime_seconds() < grace_seconds
        detail = "not started yet" if healthy else "never ran"
    else:
        healthy = age <= limit
        detail = "ok" if healthy else f"last ran {age:.0f}s ago, limit {limit:.0f}s"
    return {
        "healthy": healthy,
        "seconds_since_last_run": None if age is None else round(age, 1),
        "limit_seconds": limit,
        "detail": detail,
    }
