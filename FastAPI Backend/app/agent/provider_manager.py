"""
Provider Manager (Phase 2, 2026-07-27).

Centralizes access to this project's external data providers with health
telemetry and a fallback hook - WITHOUT reimplementing any provider's own
HTTP/parsing logic. Every provider call still runs through the SAME
existing service function/class it always has (fetch_dexscreener_summary,
GeckoTerminalService, fetch_token_security_summary,
build_contract_security_report, MacroFundamentalsService,
FundamentalsService, BinanceDataManager) - this module only wraps the call
site so callers stop reaching for these directly and get uniform
success/failure/latency tracking for free.

NAMING NOTE: the brief that requested this manager listed "CoinGecko" as an
existing provider. This codebase does not integrate CoinGecko's own API -
it integrates GeckoTerminal, CoinGecko's separate on-chain DEX-data product
(different API, different docs, same company). Registered below under its
real name ("geckoterminal") rather than inventing a CoinGecko connector
that doesn't exist in this codebase.

INSTRUMENTATION LEVEL: calls are tracked at the OPERATION level (one entry
per "market scan", "token scan", "contract security report", etc.), not by
reaching inside those functions to instrument each individual HTTP request
they make internally (DexScreener/GeckoTerminal/GoPlus/HoneypotIs/RugCheck
calls inside token_scorer.py and contract_security_service.py are already
try/except-guarded and self-report "not available" per source - see those
files). This gives real, useful health visibility today without touching
any of those files; finer-grained per-request instrumentation can be added
later without changing this class's interface.

FALLBACK: this project has few genuinely interchangeable providers today
(most cover distinct data, not redundant copies of the same data - see the
Architecture Review). Where real redundancy already exists internally
(smart_money_service.py's OnChain vs Binance TradeFlowProvider,
contract_security_service.py's multi-source GoPlus/HoneypotIs/RugCheck
merge) it's already handled inside those modules and is not duplicated
here. What THIS manager adds is `call_with_fallback`, for the case where a
caller has a genuine second choice (e.g. try GeckoTerminal candles, fall
back to Binance candles for a symbol that exists on both) - unused by any
provider pair as of this phase, but available for the next one that needs it.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, TypeVar

from loguru import logger

T = TypeVar("T")

_HISTORY_LEN = 20  # how many recent calls each provider's health is judged on


@dataclass
class ProviderCallRecord:
    ok: bool
    latency_ms: float
    error: Optional[str]
    at: float  # time.time()


@dataclass
class ProviderHealth:
    name: str
    total_calls: int = 0
    total_failures: int = 0
    recent: Deque[ProviderCallRecord] = field(default_factory=lambda: deque(maxlen=_HISTORY_LEN))
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None

    @property
    def status(self) -> str:
        """
        "unknown" - never called yet.
        "healthy" - no failures in the recent window.
        "degraded" - some but less than half of the recent window failed.
        "down" - half or more of the recent window failed.
        """
        if not self.recent:
            return "unknown"
        failures = sum(1 for r in self.recent if not r.ok)
        ratio = failures / len(self.recent)
        if ratio == 0:
            return "healthy"
        if ratio < 0.5:
            return "degraded"
        return "down"

    @property
    def recent_success_rate_pct(self) -> Optional[float]:
        if not self.recent:
            return None
        ok = sum(1 for r in self.recent if r.ok)
        return round(ok / len(self.recent) * 100, 1)

    @property
    def avg_latency_ms(self) -> Optional[float]:
        if not self.recent:
            return None
        return round(sum(r.latency_ms for r in self.recent) / len(self.recent), 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "recent_success_rate_pct": self.recent_success_rate_pct,
            "avg_latency_ms": self.avg_latency_ms,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }


# Real provider names this project actually has, for consistent
# registration across callers - not an exhaustive enum (call() accepts any
# name so a caller can register a new one without editing this file), just
# the ones known today.
PROVIDER_BINANCE_MARKET = "binance_market"
PROVIDER_BINANCE_ACCOUNT = "binance_account"
PROVIDER_DEXSCREENER = "dexscreener"
PROVIDER_GECKOTERMINAL = "geckoterminal"
PROVIDER_TOKEN_SECURITY = "token_security_goplus"
PROVIDER_CONTRACT_SECURITY = "contract_security"
PROVIDER_SMART_MONEY = "smart_money"
PROVIDER_MACRO_FUNDAMENTALS = "macro_fundamentals"
PROVIDER_FUNDAMENTALS = "fundamentals"


class ProviderManager:
    """Stateful per-process registry - construct once (e.g. at app startup,
    alongside app.state.scanner) and share; safe to also construct
    per-request if simpler, health history just resets."""

    def __init__(self) -> None:
        self._health: Dict[str, ProviderHealth] = {}

    def _get_or_create(self, name: str) -> ProviderHealth:
        if name not in self._health:
            self._health[name] = ProviderHealth(name=name)
        return self._health[name]

    async def call(self, provider_name: str, awaitable: Awaitable[T]) -> T:
        """
        Runs `awaitable` (a single already-constructed coroutine, e.g.
        `provider_manager.call(PROVIDER_DEXSCREENER, fetch_dexscreener_summary(chain, addr))`),
        records success/failure/latency under `provider_name`, and
        re-raises on failure - this manager tracks health, it does not
        swallow errors (callers already have their own honest
        "Not Available" handling, e.g. TokenScanReport.unavailable_data).
        """
        health = self._get_or_create(provider_name)
        start = time.perf_counter()
        try:
            result = await awaitable
            latency_ms = (time.perf_counter() - start) * 1000
            health.total_calls += 1
            health.last_success_at = time.time()
            health.recent.append(ProviderCallRecord(ok=True, latency_ms=latency_ms, error=None, at=time.time()))
            return result
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            health.total_calls += 1
            health.total_failures += 1
            health.last_error = str(e)
            health.recent.append(ProviderCallRecord(ok=False, latency_ms=latency_ms, error=str(e), at=time.time()))
            logger.warning(f"[ProviderManager] {provider_name} call failed: {e}")
            raise

    async def call_with_fallback(
        self,
        primary_name: str,
        primary_factory: Callable[[], Awaitable[T]],
        fallback_name: Optional[str] = None,
        fallback_factory: Optional[Callable[[], Awaitable[T]]] = None,
    ) -> T:
        """
        `primary_factory`/`fallback_factory` are zero-arg callables that
        RETURN a fresh coroutine each time (not an already-awaited one),
        since a coroutine object can only be awaited once and the fallback
        path needs its own. Only attempts the fallback if one was actually
        provided - otherwise a primary failure still raises, same as call().
        """
        try:
            return await self.call(primary_name, primary_factory())
        except Exception:
            if fallback_factory is None or fallback_name is None:
                raise
            logger.info(f"[ProviderManager] Falling back from {primary_name} to {fallback_name}.")
            return await self.call(fallback_name, fallback_factory())

    def get_health(self, provider_name: str) -> Dict[str, Any]:
        return self._get_or_create(provider_name).to_dict()

    def get_all_health(self) -> Dict[str, Dict[str, Any]]:
        return {name: h.to_dict() for name, h in self._health.items()}
