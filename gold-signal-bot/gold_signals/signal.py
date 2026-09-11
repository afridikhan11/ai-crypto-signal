"""A detected setup turned into the message that reaches WhatsApp."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from .engulfing import (
    LONG,
    EngulfingBreak,
    entry_zone,
    stop_price,
    take_profit_price,
)


@dataclass(frozen=True)
class Signal:
    instrument: str
    direction: str
    entry: float
    stop: float
    take_profit: float
    zone_low: float
    zone_high: float
    risk_pips: float
    rr: float
    break_close: float
    bars_since_break: int
    engulfed_high: float
    engulfed_low: float
    candle_time: datetime

    def key(self) -> str:
        """The setup's identity, for not sending the same one twice.

        Built from the ENGULFED candle and the break that confirmed it: those
        do not move while the setup is alive, so every poll for the next few
        candles produces the same key. Prices are rounded to the cent, which
        is the resolution gold is quoted at - without that, a float that
        re-reads as 3912.3999999 would look like a brand new setup.
        """
        return "|".join([
            self.instrument,
            self.direction,
            f"{self.engulfed_high:.2f}",
            f"{self.engulfed_low:.2f}",
            f"{self.break_close:.2f}",
        ])

    def as_json(self) -> str:
        data = asdict(self)
        data["candle_time"] = self.candle_time.isoformat()
        return json.dumps(data, sort_keys=True)

    def message(self) -> str:
        """What arrives on the phone.

        Plain text on purpose. WhatsApp renders *bold* but strips anything
        fancier, and a message that must be read on a phone in a hurry is
        better short than decorated.
        """
        arrow = "🟢 BUY" if self.direction == LONG else "🔴 SELL"
        return "\n".join([
            f"{arrow}  {_pretty_instrument(self.instrument)}  ·  Engulfing Break 30m",
            "",
            f"Entry   {self.entry:,.2f}   (zone {self.zone_low:,.2f} - {self.zone_high:,.2f})",
            f"Stop    {self.stop:,.2f}   ({self.risk_pips:.0f} pips)",
            f"Target  {self.take_profit:,.2f}   (1:{self.rr:g})",
            "",
            f"Break closed at {self.break_close:,.2f}, {_bars_ago(self.bars_since_break)}.",
            f"Engulfed candle {self.engulfed_low:,.2f} - {self.engulfed_high:,.2f}.",
            "",
            f"{self.candle_time.strftime('%d %b %Y %H:%M')} UTC",
            "Signal only - no order has been placed.",
        ])


def _pretty_instrument(instrument: str) -> str:
    return "GOLD (XAU/USD)" if instrument.upper() in ("XAU_USD", "XAUUSD") else instrument


def _bars_ago(bars: int) -> str:
    if bars == 0:
        return "on the candle that just closed"
    return f"{bars} candle{'s' if bars > 1 else ''} ago"


def build_signal(
    setup: EngulfingBreak,
    settings,
    candle_time: datetime,
    instrument: Optional[str] = None,
) -> Optional[Signal]:
    """Prices for a setup, or None if the settings make it degenerate.

    A zero risk distance (pip size or pip counts misconfigured to zero) would
    make the take-profit equal the entry and the message meaningless, so it is
    refused here rather than sent.
    """
    pip = float(settings.pip_size)
    entry, far_edge = entry_zone(setup, pip, float(settings.entry_pips))
    stop = stop_price(setup, pip, float(settings.stop_pips))
    risk = abs(entry - stop)
    if risk <= 0 or pip <= 0:
        return None
    take_profit = take_profit_price(entry, stop, setup.direction, float(settings.rr))

    return Signal(
        instrument=(instrument or settings.instrument).upper(),
        direction=setup.direction,
        entry=round(entry, 2),
        stop=round(stop, 2),
        take_profit=round(take_profit, 2),
        zone_low=round(min(entry, far_edge), 2),
        zone_high=round(max(entry, far_edge), 2),
        risk_pips=round(risk / pip),
        rr=float(settings.rr),
        break_close=setup.break_close,
        bars_since_break=setup.bars_since_break,
        engulfed_high=setup.engulfed.high,
        engulfed_low=setup.engulfed.low,
        candle_time=candle_time,
    )
