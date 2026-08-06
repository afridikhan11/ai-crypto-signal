"""ICT Kill Zones and trading sessions.

Kill zones are the windows where institutional order-flow is most active and
where ICT setups have the highest expectancy. Windows are defined in **UTC**
to avoid daylight-saving ambiguity (a common, well-understood simplification).

    Asian KZ      00:00 - 03:00 UTC   (accumulation / range build)
    London KZ     07:00 - 10:00 UTC   (London open manipulation)
    New York AM   12:00 - 15:00 UTC   (primary NY session move)
    New York PM   17:00 - 19:00 UTC   (London close / PM reversal)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import List, Optional


@dataclass(frozen=True)
class KillZone:
    name: str
    start: time
    end: time
    # Relative expectancy weight (0-1) used by the scorer.
    weight: float


# Ordered by session sequence within the UTC day.
KILL_ZONES: List[KillZone] = [
    KillZone("Asian", time(0, 0), time(3, 0), 0.55),
    KillZone("London", time(7, 0), time(10, 0), 0.95),
    KillZone("New York AM", time(12, 0), time(15, 0), 1.00),
    KillZone("New York PM", time(17, 0), time(19, 0), 0.80),
]


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def active_kill_zone(now: Optional[datetime] = None) -> Optional[KillZone]:
    """Return the kill zone active at ``now`` (UTC), or ``None`` if outside all."""
    t = _as_utc(now).time()
    for kz in KILL_ZONES:
        if kz.start <= t < kz.end:
            return kz
    return None


def in_kill_zone(now: Optional[datetime] = None) -> bool:
    return active_kill_zone(now) is not None


def kill_zone_weight(now: Optional[datetime] = None) -> float:
    """Expectancy weight of the current kill zone (0.3 baseline when outside)."""
    kz = active_kill_zone(now)
    return kz.weight if kz else 0.3


def session_label(now: Optional[datetime] = None) -> str:
    kz = active_kill_zone(now)
    return kz.name if kz else "Off-session"
