"""
Entry Validation Engine - the accept/reject gate for a trade candidate,
checking every required piece of institutional evidence together.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Distinct from `EntryEngine` (which only
constructs an entry plan) - this module is exclusively responsible for
deciding whether that plan is COMPLETE enough to act on.

CHECKS PERFORMED (per this phase's explicit requirement list)
--------------------------------------------------------------------------
Each check is independently pass/fail with a disclosed reason. A check
that has no relevant data supplied PASSES by default (honest degradation -
matches this project's established pattern: absence of optional context
never manufactures a rejection on its own), EXCEPT where the check's own
definition is specifically about whether required evidence exists at all
(Confluence, Invalidation) - those two fail on missing data, since "we
have no anchor at all" / "we don't know where this idea is wrong" is
exactly the kind of incomplete setup this engine exists to reject.

  - institutional_bias : fails only if a bias WAS computed and actively
                          opposes the trade direction (not just neutral/
                          conflicted/absent).
  - htf_alignment       : fails only if HTF structure WAS supplied and the
                          heaviest available timeframe's structure
                          actively opposes the trade direction.
  - confluence          : fails if NONE of {liquidity, order block, FVG}
                          evidence is present at all - matches
                          SignalGenerator's own pre-existing "a real
                          sweep or order block must be present" hard gate,
                          made explicit and reusable here.
  - session_killzone    : fails only in the degenerate combination of
                          off-session AND zero confluence (see above) -
                          crypto trades 24/7, so being outside a Kill Zone
                          alone is not disqualifying, only "off-session
                          AND otherwise unsupported" is.
  - risk                : fails if a risk:reward ratio was supplied and is
                          below `min_risk_reward`.
  - invalidation        : fails if no invalidation price level was
                          supplied at all.

`required_checks` controls which of the above must PASS for the overall
result to be valid - defaults to all six, per "reject incomplete setups."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_MIN_RISK_REWARD = 1.5

ALL_CHECK_NAMES = (
    "institutional_bias", "htf_alignment", "confluence",
    "session_killzone", "risk", "invalidation",
)


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EntryValidationResult:
    valid: bool
    checks: List[ValidationCheck]

    @property
    def failed_checks(self) -> List[str]:
        return [c.name for c in self.checks if not c.passed]

    def detail_for(self, name: str) -> Optional[str]:
        for c in self.checks:
            if c.name == name:
                return c.detail
        return None


class EntryValidationEngine:
    """Stateless. `validate()` called twice with the same inputs returns
    an equivalent `EntryValidationResult`."""

    def __init__(
        self,
        required_checks: Optional[List[str]] = None,
        min_risk_reward: float = DEFAULT_MIN_RISK_REWARD,
    ) -> None:
        self.required_checks = list(required_checks) if required_checks is not None else list(ALL_CHECK_NAMES)
        self.min_risk_reward = min_risk_reward

    def validate(
        self,
        direction: str,
        institutional_bias: Optional[object] = None,
        htf_snapshot: Optional[object] = None,
        liquidity_present: bool = False,
        order_block_present: bool = False,
        fvg_present: bool = False,
        session_context: Optional[object] = None,
        risk_reward: Optional[float] = None,
        invalidation_level: Optional[float] = None,
        counter_trend_exception: bool = False,
    ) -> EntryValidationResult:
        """`counter_trend_exception` (2026-08-21): when True, an OPPOSING
        institutional bias / HTF structure no longer fails its check - the
        caller has already decided this specific counter-trend setup clears the
        configured confidence bar (see Settings.htf_opposition_mode), and this
        engine must not silently re-block what that policy admitted. The check
        detail records the exception honestly. Default False, so every other
        caller (and the backtester) is unchanged."""
        checks: List[ValidationCheck] = []

        # institutional_bias
        if institutional_bias is None or institutional_bias.direction in ("neutral", "conflicted"):
            checks.append(ValidationCheck("institutional_bias", True, "No opposing institutional bias."))
        else:
            wanted = "bullish" if direction == "LONG" else "bearish"
            if institutional_bias.direction == wanted:
                checks.append(ValidationCheck("institutional_bias", True, f"Institutional bias aligned ({institutional_bias.direction})."))
            elif counter_trend_exception:
                checks.append(ValidationCheck(
                    "institutional_bias", True,
                    f"Institutional bias opposes trade ({institutional_bias.direction}) but the "
                    f"counter-trend confidence exception applies.",
                ))
            else:
                checks.append(ValidationCheck("institutional_bias", False, f"Institutional bias opposes trade ({institutional_bias.direction})."))

        # htf_alignment
        htf_fail = False
        htf_detail = "No higher-timeframe structure supplied."
        if htf_snapshot is not None and not htf_snapshot.is_empty():
            opposing_alignment = "aligned_bearish" if direction == "LONG" else "aligned_bullish"
            from app.smc.htf_structure_engine import SUPPORTED_TIMEFRAMES
            heaviest = next((tf for tf in SUPPORTED_TIMEFRAMES if htf_snapshot.get(tf) is not None), None)
            if heaviest is not None:
                ts = htf_snapshot.get(heaviest)
                if ts.structure_alignment == opposing_alignment:
                    if counter_trend_exception:
                        htf_detail = (
                            f"{heaviest.upper()} structure opposes trade ({ts.structure_alignment}) "
                            f"but the counter-trend confidence exception applies."
                        )
                    else:
                        htf_fail = True
                        htf_detail = f"{heaviest.upper()} structure opposes trade ({ts.structure_alignment})."
                else:
                    htf_detail = f"{heaviest.upper()} structure does not oppose trade ({ts.structure_alignment})."
        checks.append(ValidationCheck("htf_alignment", not htf_fail, htf_detail))

        # confluence
        confluence_count = sum([liquidity_present, order_block_present, fvg_present])
        checks.append(ValidationCheck(
            "confluence", confluence_count > 0,
            f"{confluence_count}/3 confluence signals present (liquidity/order block/FVG)."
            if confluence_count > 0 else "No liquidity, order block, or FVG evidence present at all.",
        ))

        # session_killzone
        off_session = bool(session_context is not None and getattr(session_context, "is_off_session", False))
        session_fail = off_session and confluence_count == 0
        checks.append(ValidationCheck(
            "session_killzone", not session_fail,
            "Off-session with no confluence backing the setup." if session_fail else "Session context acceptable.",
        ))

        # risk
        risk_fail = risk_reward is not None and risk_reward < self.min_risk_reward
        checks.append(ValidationCheck(
            "risk", not risk_fail,
            f"Risk:Reward {risk_reward} below minimum {self.min_risk_reward}." if risk_fail
            else "Risk:Reward acceptable or not yet known.",
        ))

        # invalidation
        checks.append(ValidationCheck(
            "invalidation", invalidation_level is not None,
            f"Invalidation level known ({invalidation_level})." if invalidation_level is not None
            else "No invalidation level known - setup incomplete.",
        ))

        valid = all(c.passed for c in checks if c.name in self.required_checks)
        return EntryValidationResult(valid=valid, checks=checks)
