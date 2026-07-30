# Phase 1 – Backend & WPF Stability Fix Report

**Date:** 2026-07-30
**Scope:** Bug fixes only. No architecture change, no new features, no change to ICT logic, AI scoring, Universal Scanner, Risk Engine, Trade Management, or Auto Trading logic. Every fix below is additive and narrowly scoped, verified against the file(s) it touches.

---

## 1. Bugs Found

1. **Dashboard `AttributeError: 'UniversalScanner' object has no attribute 'get_symbol_htf_trend'`** — `app/api/v1/endpoints/dashboard.py` called `scanner.get_symbol_htf_trend(symbol, timeframe)` twice (for BTC 1h/4h trend), a method that does not exist anywhere on `UniversalScanner`.
2. **`Models/SignalDto.cs` silently dropped 5 backend fields** — the WPF signal DTO (backing Live Signals, Gold Signals, and Auto Trading's signal list) was missing `correlation_warning`, `risk_approved`, `risk_reasons`, `portfolio_open_risk_percent`, `portfolio_exposure_percent`, which the backend's `SignalResponse` schema has always returned. `System.Text.Json` silently ignores unmapped JSON fields rather than erroring, so this failed silently rather than crashing — the per-signal Risk Engine advisory data was simply never available to the UI.

No other backend `scanner.<attr>` / `data_manager.<attr>` / `fundamentals.<attr>` access, and no other backend-to-WPF DTO field, was found to be missing or mismatched (see Root Cause and Files Modified for how this was verified, not just read).

---

## 2. Root Cause

**Bug 1** is a leftover from the ICT migration completed earlier. `UniversalScanner`'s own module docstring documents the change directly: *"The previous scanner computed five EMA20/50 trend reads per symbol per scan (`btc_trend`, 1h, 4h, 1d, 5m) via the retail helper now quarantined in `app/legacy/trend.py`... The ICT pipeline no longer consumes them — higher-timeframe context comes from real Market Structure (`HTFStructureEngine`/`InstitutionalBiasEngine`) — so this scanner does not compute them at all."* The method was deliberately removed when the retail EMA-trend helper was quarantined; `dashboard.py`'s Market panel was never updated to match and kept calling the old name. This is **dashboard.py calling an obsolete method**, not a missing scanner implementation — confirmed by reading the scanner's real class surface directly (see Files Modified) rather than assuming.

**Bug 2** is a plain DTO drift: the backend's `SignalResponse` schema (`app/schemas/signal.py`) gained 5 fields when portfolio-risk advisories were added to signals in an earlier phase; the WPF-side `SignalDto.cs` was never updated to match. `System.Text.Json`'s default behavior (ignore unknown/unmapped fields) means this never threw an error — it just silently dropped data, which is arguably worse since nothing signaled the gap.

---

## 3. Files Modified

- `FastAPI Backend/app/api/v1/endpoints/dashboard.py` — replaced the obsolete `scanner.get_symbol_htf_trend()` calls with a real read from the production ICT pipeline.
- `AI_Crypto_Signal_Pro/AI_Crypto_Signal_Pro/Models/SignalDto.cs` — added the 5 missing properties with correct nullable types matching the backend's `Optional[...]` fields.

No other file was modified this phase.

---

## 4. Bugs Fixed

**Bug 1 fix** — `dashboard.py`'s `_build_market_panel()` now calls `scanner._load_htf_dataframes(BTC_SYMBOL)` (the scanner's own existing HTF-dataframe loader — the exact same one the live signal-generation pipeline uses for every symbol, not a new fetch path), runs the result through `HTFStructureEngine` (the same production engine `HTFStructureEngine`/`InstitutionalBiasEngine` already use elsewhere in the pipeline), and maps `structure_alignment` (`"aligned_bullish"` / `"aligned_bearish"` / `"conflicted"` / `"insufficient_data"`) to a simple `"bullish"` / `"bearish"` / `"neutral"` label — `"neutral"` for anything ambiguous, matching the exact fallback value this same field already used when no scanner was attached at all. No new trend computation, no EMA, no duplicated scanner logic — purely a rewire onto the real ICT pipeline's own output, per the task's explicit instruction.

Verified two ways, not just read:
- A real, dynamic Python import of the actual `UniversalScanner` class confirmed `get_symbol_htf_trend` genuinely doesn't exist and that every other `scanner.<attr>` access anywhere in `app/api` (16 endpoint files) resolves to a real attribute — same check run against `BinanceDataManager`/`FundamentalsService` for their sub-attribute accesses, all clean.
- A functional smoke test built synthetic bullish/bearish/empty OHLCV series and ran them through the new code path end-to-end: bearish data correctly produced `"bearish"`, empty/insufficient data correctly degraded to `"neutral"` with no exception in every case. (`HTFStructureEngine`/`MarketStructureEngine` themselves are separately regression-tested in `tests/test_htf_structure_engine.py`/`tests/test_market_structure_engine.py`, both passing — this phase only needed to confirm the new call site is wired correctly, not re-verify swing-detection accuracy.)

**Bug 2 fix** — added the 5 missing properties to `SignalDto.cs` with `[JsonPropertyName]` matching the backend field names exactly and nullable C# types (`string?`, `bool?`, `List<string>`, `double?`) matching the backend's `Optional[...]` Pydantic types. Confirmed every other DTO the WPF app actually calls (Dashboard, Stats, History, Account, Portfolio, Performance, Trading Control, Trading, Token Scan, Agent, Health) already matches its backend schema field-for-field — this was the only gap found across the whole WPF↔backend JSON boundary.

---

## 5. Backend Build Status

**Clean.** Every file under `app/` (all ~90+ modules) compiles with zero errors (`python3 -m py_compile`, full sweep). The real `fastapi`/`pydantic`/`sqlalchemy` packages cannot be installed in this sandbox (no network egress to PyPI), so a true `uvicorn` boot could not be performed here — compile-cleanliness plus the regression suite below is the strongest evidence available in this environment. The full offline regression suite was re-run after both fixes: **626/627 tests passing, 0 failed, 0 errored** (1 self-skip by design — `test_order_flow.py`'s ATR-vs-real-`ta`-library equivalence check, which correctly declines to fake a comparison it can't perform against an offline stub). Identical result to before the fixes — zero regressions.

---

## 6. WPF Build Status

**Cannot be compiled in this environment** — no .NET SDK/MSBuild is available in this sandbox (confirmed again this phase: `dotnet` is not on PATH). This is an environment limitation, disclosed rather than worked around. In its place, two independent static-review passes were run:

- **Binding/command/polling integrity** (all 11 screens + MainWindow + shared services): every `{Binding}` resolves to a real property/command, every `DataContext` is set, every background polling loop has a re-entrancy guard, every destructive Auto Trading action has a busy-state guard.
- **DTO/API-mapping + null-safety** (this phase's new check): every backend response schema WPF actually consumes was field-for-field diffed against its C# DTO; every `.Value`/nullable-chain dereference in every ViewModel was checked for a preceding null guard.

Both passes are believed correct by direct inspection and cross-referencing against the real backend schemas, but remain **compiler-unverified**. Build the WPF app on a Windows machine with the .NET SDK before shipping to confirm no syntax/type error was introduced.

---

## 7. Remaining Issues

- WPF compile/runtime verification is still outstanding (Section 6) — must be done on a machine with the .NET SDK.
- Live Binance Testnet execution is still outstanding (unchanged from the prior validation phase — no network egress to Binance in this sandbox).
- No other backend or WPF bugs were found in this pass beyond the two above.

---

## 8. Ready for Binance Futures Testnet? **NO**

Backend is stable (clean compile, 626/627 regression tests passing, the reported Dashboard `AttributeError` is fixed and verified, and a systematic sweep found no other instance of the same bug class anywhere in the API layer). The WPF DTO gap is fixed. However, certification requires "WPF builds successfully" and neither this phase nor the prior one could compile the WPF project — there is no .NET toolchain in this sandbox. Build and smoke-test the WPF app on Windows first; once that passes (and the still-outstanding live Testnet execution check from the prior report is run), this platform is ready to proceed.
