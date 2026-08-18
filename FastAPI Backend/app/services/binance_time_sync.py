"""
Shared Binance server-clock synchroniser.

Every SIGNED Binance request carries a `timestamp` and is rejected with code
-1021 ("Timestamp for this request is outside of the recvWindow") if that
timestamp drifts too far from Binance's own server clock. On testnet the host
clock was measured ~2052ms ahead of Binance - already 40% of the old 5000ms
recvWindow - so signed requests failed intermittently.

This module keeps a cached offset (`server_time - local_time`, corrected for
round-trip latency) so `now_ms()` returns a Binance-aligned timestamp instead
of the raw host clock. The offset is:

  - fetched from GET /fapi/v1/time,
  - guarded by an async lock so a burst of callers triggers ONE fetch, not a
    thundering herd,
  - refreshed lazily every `resync_interval` seconds (default 30 min), and
  - force-refreshable (`resync()`) so a -1021 response can re-sync and retry.

Instances are shared per (base_url, time_path) via `get_time_sync()` so all
service objects hitting the same host share one offset and one lock.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from loguru import logger

# How often a cached offset is considered stale and re-fetched on the next use.
DEFAULT_RESYNC_INTERVAL = 1800.0  # 30 minutes
# Above this, the *host* clock is almost certainly wrong (NTP not running):
# the offset still corrects our timestamps, but it is worth a loud warning.
OFFSET_WARN_MS = 1000.0
# Collapse a storm of concurrent forced re-syncs (e.g. many parallel requests
# all getting -1021 at once) into a single fetch: a force that lands within
# this window of the last successful sync reuses it instead of re-fetching.
_MIN_FORCED_GAP = 2.0
_HTTP_TIMEOUT = 10.0


class BinanceTimeSync:
    """Caches the local<->Binance clock offset for one host.

    Not tied to any API key - it only reads the public server-time endpoint,
    so a single instance is safely shared across every service that talks to
    the same base URL.
    """

    def __init__(
        self,
        base_url: str,
        time_path: str = "/fapi/v1/time",
        resync_interval: float = DEFAULT_RESYNC_INTERVAL,
    ):
        self._base_url = base_url.rstrip("/")
        self._time_path = time_path
        self._resync_interval = resync_interval
        self._offset_ms: float = 0.0
        self._synced: bool = False
        # monotonic timestamp of the last SUCCESSFUL sync; None until first one.
        self._last_sync: Optional[float] = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)

    @property
    def offset_ms(self) -> float:
        """The most recently measured offset (0.0 until the first sync)."""
        return self._offset_ms

    @property
    def synced(self) -> bool:
        return self._synced

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    async def now_ms(self) -> int:
        """A Binance-aligned millisecond timestamp: local time + cached offset.

        Triggers a lazy (re)sync when the cache is empty or stale; if that
        sync fails the last-known offset is reused (falling back to the raw
        host clock only when no sync has ever succeeded), so this never raises.
        """
        await self._ensure_synced()
        return int(time.time() * 1000 + self._offset_ms)

    async def resync(self) -> None:
        """Force a re-sync now (used after a -1021 rejection). Concurrent
        forced re-syncs that land right after a fresh sync are collapsed."""
        await self._ensure_synced(force=True)

    # ------------------------------------------------------------------
    def _is_due(self) -> bool:
        if self._last_sync is None:
            return True
        return (time.monotonic() - self._last_sync) >= self._resync_interval

    async def _ensure_synced(self, force: bool = False) -> None:
        if not force and not self._is_due():
            return
        async with self._lock:
            # Re-check under the lock: another coroutine may have synced while
            # we waited for it (this is what prevents the thundering herd).
            if not force and not self._is_due():
                return
            if (
                force
                and self._last_sync is not None
                and (time.monotonic() - self._last_sync) < _MIN_FORCED_GAP
            ):
                # A sync just completed - reuse it rather than hammer the host.
                return
            try:
                await self._do_sync()
            except Exception as exc:  # network / parse / HTTP error
                # Keep the prior offset (or 0 if we never synced) and back off
                # to the normal interval so a down endpoint is not hammered on
                # every call. now_ms() therefore degrades to the host clock
                # rather than failing.
                self._last_sync = time.monotonic()
                logger.warning(
                    "Binance time sync failed ({}): {} - using {} offset",
                    self._base_url,
                    exc,
                    "cached" if self._synced else "zero (host clock)",
                )

    async def _do_sync(self) -> None:
        t0 = time.time()
        server_ms = await self._fetch_server_time_ms()
        t1 = time.time()
        rtt_ms = (t1 - t0) * 1000.0
        # The server generated `server_ms` at roughly the midpoint of the round
        # trip, assuming symmetric latency - so compare it against the local
        # clock at that same midpoint, not at t0 or t1.
        local_mid_ms = (t0 + t1) / 2.0 * 1000.0
        self._offset_ms = server_ms - local_mid_ms
        self._synced = True
        self._last_sync = time.monotonic()
        logger.info(
            "Binance time sync ({}): offset={:+.0f}ms rtt={:.0f}ms",
            self._base_url,
            self._offset_ms,
            rtt_ms,
        )
        if abs(self._offset_ms) > OFFSET_WARN_MS:
            logger.warning(
                "Binance clock offset {:+.0f}ms exceeds {:.0f}ms after sync - "
                "the host system clock is likely wrong; fix it with NTP.",
                self._offset_ms,
                OFFSET_WARN_MS,
            )

    async def _fetch_server_time_ms(self) -> int:
        """GET the server time in ms. Isolated so tests can override it."""
        resp = await self._client.get(f"{self._base_url}{self._time_path}")
        resp.raise_for_status()
        return int(resp.json()["serverTime"])


# ----------------------------------------------------------------------
# Shared registry - one synchroniser per host, so every service that talks to
# the same base URL shares a single offset and a single lock.
# ----------------------------------------------------------------------
_registry: dict[str, BinanceTimeSync] = {}


def get_time_sync(base_url: str, time_path: str = "/fapi/v1/time") -> BinanceTimeSync:
    """Return the shared `BinanceTimeSync` for `base_url`, creating it once.

    The get-then-set has no `await` between the check and the insert, so it is
    atomic within a single event loop - two coroutines constructing the same
    key concurrently is not possible.
    """
    key = f"{base_url.rstrip('/')}{time_path}"
    ts = _registry.get(key)
    if ts is None:
        ts = BinanceTimeSync(base_url, time_path)
        _registry[key] = ts
    return ts
