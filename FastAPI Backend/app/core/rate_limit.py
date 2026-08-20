"""
Tiny in-memory login rate limiter (no external dependency).

A sliding window of failed-attempt timestamps per key (IP): once failures in
the window reach the limit, the key is locked out for a fixed cooldown. A
successful login clears the key. This is deliberately process-local - it
protects a single-owner login endpoint against brute force, not a distributed
fleet; a multi-instance deployment would move this to Redis.

The clock is injectable so lockout/window expiry can be tested without sleeping.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable, Dict, List, Tuple


class LoginRateLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: float,
        lockout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._failures: Dict[str, List[float]] = defaultdict(list)
        self._locked_until: Dict[str, float] = {}

    def is_locked(self, key: str) -> Tuple[bool, float]:
        """(locked, seconds_remaining). Clears an expired lock as a side effect."""
        until = self._locked_until.get(key)
        if until is None:
            return False, 0.0
        remaining = until - self._clock()
        if remaining <= 0:
            # Lock elapsed - clear it and the stale failure history.
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
            return False, 0.0
        return True, remaining

    def record_failure(self, key: str) -> None:
        now = self._clock()
        recent = [t for t in self._failures[key] if now - t < self._window]
        recent.append(now)
        self._failures[key] = recent
        if len(recent) >= self._max:
            self._locked_until[key] = now + self._lockout

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
