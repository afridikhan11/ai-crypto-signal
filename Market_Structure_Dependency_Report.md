# Market Structure Dependency Report

**Scope:** `app/smc/market_structure.py`
**Method:** Full-project text search (imports, call sites) + manual read of every matching file. No code was modified. No conclusions beyond what a specific file/line shows.
**Date:** 2026-07-28

---

## 1. Every place `MarketStructure` is imported

| # | File | Line | Purpose (from surrounding code) |
|---|---|---|---|
| 1 | `app/strategy/signal_generator.py` | 5 | `SignalGenerator.generate()` — the live signal-generation path used by the background scanner and the backtest engine |
| 2 | `app/api/v1/endpoints/dashboard.py` | 15 | `_build_market_panel()` — computes the "Market Structure" field shown on the Dashboard screen |
| 3 | `app/services/market_scorer.py` | 57 | `_analyze()` — the on-demand Market Scan path (Crypto Signals / Gold Signal, AI Agent, Portfolio Intelligence) |
| 4 | `app/services/ta_dashboard.py` | 34 | `build_ta_dashboard()` — the shared Technical Analysis dashboard used by both Market Scan and Token Scanner |
| 5 | `app/services/token_scorer.py` | 60 | `_run_technical_analysis()` — the Token Scanner's on-chain technical read |
| 6 | `scripts/analyze_smc_frequency.py` | 94 | A standalone diagnostic/offline script (produces the `smc_frequency_report_*.json` files) — not part of the running API or scanner |
| 7 | `setup_module2.sh` | 850 | A one-time bash scaffold script containing a heredoc (`cat > app/smc/market_structure.py << 'PYEOF'`) that originally *wrote* this file. It is a `.sh` file, not Python — it cannot be imported by the running application. |

Two additional files mention `MarketStructure` only in comments, with no import and no call:

| File | Line | Nature |
|---|---|---|
| `app/indicators/chart_patterns.py` | 7 | Docstring comment: "Built on top of the SAME swing highs/lows MarketStructure already computes" — descriptive text only |
| `app/services/geckoterminal_service.py` | 169 | Docstring comment listing module names for context — descriptive text only |

---

## 2. Every place `detect_bos_choch()` is called

| # | File | Line | Call chain (what calls the method, and what holds the result) |
|---|---|---|---|
| 1 | `app/strategy/signal_generator.py` | 87 | `ms = MarketStructure(df)` (line 86) → `breaks = ms.detect_bos_choch()` (line 87), inside `SignalGenerator.generate()` |
| 2 | `app/api/v1/endpoints/dashboard.py` | 110 | `ms = MarketStructure(df)` (line 109) → `breaks = ms.detect_bos_choch()` (line 110), inside `_build_market_panel()` |
| 3 | `app/services/market_scorer.py` | 300 | `ms = MarketStructure(df)` (line 299) → `breaks = ms.detect_bos_choch()` (line 300), inside `_analyze()` |
| 4 | `app/services/ta_dashboard.py` | 134 | `ms_default = MarketStructure(df, pivot_window=5)` (line 130) → `breaks_default = ms_default.detect_bos_choch()` (line 134) |
| 5 | `app/services/ta_dashboard.py` | 135 | `ms_internal = MarketStructure(df, pivot_window=3)` (line 131) → `breaks_internal = ms_internal.detect_bos_choch()` (line 135) |
| 6 | `app/services/ta_dashboard.py` | 136 | `ms_external = MarketStructure(df, pivot_window=8)` (line 132) → `breaks_external = ms_external.detect_bos_choch()` (line 136) |
| 7 | `app/services/token_scorer.py` | 285 | `ms = MarketStructure(df)` (line 284) → `breaks = ms.detect_bos_choch()` (line 285), inside `_run_technical_analysis()` |
| 8 | `scripts/analyze_smc_frequency.py` | 148, 337, 521, 659 | Diagnostic script, 4 separate call sites, all offline/manual-run only |
| 9 | `setup_module2.sh` | 871 | Inside the heredoc text that originally generated `signal_generator.py` — not an executed call in the running app |

**Every call in rows 1–7 sits inside a function that IS reachable from either the registered FastAPI router (`app/api/v1/__init__.py`) or the background scheduler (`app/main.py`'s startup event) — traced in Section 3.**

---

## 3. Complete execution path — real call chains traced end to end

Six independent chains all terminate at `MarketStructure`. Each was traced by reading the actual calling code, not assumed from naming.

### Chain A — Background live scanner (writes Signals to the database)

```
app/main.py  (startup event, already documented in 02_System_Architecture.md)
   ↓
CryptoScanner.start()                       app/scheduler/scanner.py
   ↓
CryptoScanner.analyze_symbol(symbol)        app/scheduler/scanner.py:114
   ↓
generator = self.generators[symbol]         app/scheduler/scanner.py:182
generator.generate(df, ...)                 app/scheduler/scanner.py:186
   ↓
SignalGenerator.generate()                  app/strategy/signal_generator.py:61
   ↓
ms = MarketStructure(df)                    app/strategy/signal_generator.py:86
breaks = ms.detect_bos_choch()              app/strategy/signal_generator.py:87
```

### Chain B — On-demand Market Scan (Crypto Signals screen, Gold Signal screen, AI Agent, Portfolio Intelligence)

```
API request  (/agent/query, or Portfolio Intelligence's internal per-position call)
   ↓
app/agent/orchestrator.py  imports build_market_scan_report  (line 90)
   -- OR --
app/services/portfolio_intelligence.py  imports build_market_scan_report  (line 71)
   ↓
build_market_scan_report()                  app/services/market_scorer.py:126
   ↓
_analyze()                                   app/services/market_scorer.py:280
   ↓
ms = MarketStructure(df)                     app/services/market_scorer.py:299
breaks = ms.detect_bos_choch()               app/services/market_scorer.py:300
   ↓ (same _analyze() call, separate sub-call)
build_ta_dashboard(df, ...)                  app/services/market_scorer.py:320
   ↓
3× additional MarketStructure instances      app/services/ta_dashboard.py:130-136
```

### Chain C — Token Scanner (on-chain contract analysis)

```
API  POST /token-scan
   ↓
scan_token()                                 app/api/v1/endpoints/token_scan.py:48
   ↓
build_token_scan_report()                    app/services/token_scorer.py (imported line 16 of token_scan.py)
   ↓
_run_technical_analysis()                    app/services/token_scorer.py:281
   ↓
ms = MarketStructure(df)                     app/services/token_scorer.py:284
breaks = ms.detect_bos_choch()                app/services/token_scorer.py:285
   ↓ (same report, separate sub-call)
build_ta_dashboard(df, ...)                   app/services/token_scorer.py:56 (imported)
   ↓
3× additional MarketStructure instances       app/services/ta_dashboard.py:130-136
```

### Chain D — Dashboard screen's "Market Structure" panel field

```
API  GET /dashboard
   ↓
_build_market_panel()                         app/api/v1/endpoints/dashboard.py:90
   ↓
ms = MarketStructure(df)                      app/api/v1/endpoints/dashboard.py:109   (df = live scanner's cached BTCUSDT 15m candles)
breaks = ms.detect_bos_choch()                app/api/v1/endpoints/dashboard.py:110
```
This chain calls `MarketStructure` directly — it does not go through `market_scorer.py` or `signal_generator.py`.

### Chain E — AI Trading Agent (asks about a token instead of a trading pair)

```
API  POST /agent/query
   ↓
query_agent()                                  app/api/v1/endpoints/agent.py:175
   ↓
app/agent/orchestrator.py  imports build_token_scan_report  (line 91)
   ↓
(same as Chain C from build_token_scan_report() onward)
```

### Chain F — Backtesting engine

```
app/backtest/engine.py:42  imports SignalGenerator
   ↓
(same as Chain A from SignalGenerator.generate() onward)
```

**All six chains are confirmed reachable:** `app/api/v1/__init__.py` registers `dashboard_router`, `token_scan_router`, and `agent_router` on the live FastAPI app (`api_router.include_router(...)`, lines 24/27/28); `app/main.py`'s startup event starts `CryptoScanner`, which owns Chain A. None of these six chains are orphaned, commented out, or behind an unused flag.

---

## 4. Classification: is `market_structure.py` production, helper, legacy, or dead code?

**Conclusion: (A) Production code — and specifically, it is the single foundational building block underneath every SMC-driven feature in the application.**

Evidence:

- 5 distinct production modules import it directly (Section 1, rows 1–5), each with a real instantiation and a real `.detect_bos_choch()` call (Section 2, rows 1–7) — not a stub, not a commented-out call.
- Every one of those 5 modules is reachable from a registered API route or the startup-scheduled background scanner (Section 3, Chains A–F) — traced through actual `include_router()` calls and the actual scanner start sequence, not assumed.
- It is called from **7 separate call sites** across production code (1 in `signal_generator.py`, 1 in `dashboard.py`, 1 in `market_scorer.py`, 3 in `ta_dashboard.py`, 1 in `token_scorer.py`), each with a different `pivot_window` in one case (`ta_dashboard.py` uses 5/3/8) — evidence of active, intentional reuse across the codebase rather than a single forgotten reference.
- No evidence of dead-code markers: no `# TODO: remove`, no `# unused`, no `# deprecated` comments were found attached to `market_structure.py` or any of its call sites.
- The only non-runtime references are a standalone diagnostic script (`scripts/analyze_smc_frequency.py` — a manually-run analysis tool, not part of the API or scanner) and a `.sh` scaffold script that cannot be imported by Python at all.

It is not "just a helper" in the sense of being optional or peripheral — every trend/structure/BOS/CHoCH read shown anywhere in the product (Dashboard panel, Market Scan, Token Scanner, AI Agent, backtests, live signal generation) traces back to this one file.

---

## 5. Search for any other implementation of BOS / CHoCH / Market Structure / Internal BOS / External BOS / Market Structure Shift / Swing Structure / Structure Break

Full-project search for the classes/concepts `SwingType`, `SwingPoint`, `StructureBreak`, and the literal strings `"BOS"` / `"CHoCH"`.

**Files where `SwingType` / `SwingPoint` / `StructureBreak` are referenced:**

| File | Nature of reference |
|---|---|
| `app/smc/market_structure.py` | **Definition** — these 2 dataclasses and 1 enum are defined here, nowhere else |
| `app/smc/liquidity.py` | Consumes `swing_highs`/`swing_lows` passed in as constructor arguments (e.g. `LiquidityDetector(df, ms.swing_highs, ms.swing_lows)`) — does not detect its own swings |
| `app/services/market_scorer.py` | Uses the `StructureBreak` objects returned by `MarketStructure` |
| `app/services/token_scorer.py` | Same — consumes `MarketStructure`'s output |
| `app/services/ta_dashboard.py` | Same — consumes `MarketStructure`'s output, imports `StructureBreak` by name for a type hint (`latest_of_type(breaks: List[StructureBreak], ...)`) |
| `app/indicators/chart_patterns.py` | Comment only, referencing the same swing data conceptually (see Section 1) |
| `app/agent/strategy_profile_manager.py` | Comment only |
| `app/schemas/token_scan.py` | Comment only, documenting that `"BOS"`, `"Liquidity Sweep"`, `"Order Block"` are example string values produced elsewhere |
| `app/ai/scorer.py` | Contains the literal strings `"BOS"`/`"CHoCH"` — this is the AI Scorer READING the `type` field off a `StructureBreak`-derived dict to weight it in scoring, not a separate detector |

**No second class, function, or module anywhere in the project independently detects swing highs/lows, BOS, or CHoCH.** Every file that deals with these concepts either defines them once (`market_structure.py`) or consumes the output of that one definition.

**The "internal" / "external" naming** seen in `ta_dashboard.py` (`ms_internal`, `ms_external` at lines 131–132) is **not** a second implementation — it is the *same* `MarketStructure` class instantiated three times with three different `pivot_window` values (5, 3, 8) to read minor vs. major swing structure. This is parameterization of one engine, not multiple engines.

---

## 6. Comparison — is there more than one implementation?

**No. There is exactly one implementation of market structure / BOS / CHoCH detection in this project: `app/smc/market_structure.py`'s `MarketStructure` class.**

The only other place the class's source code appears is the heredoc inside `setup_module2.sh` (lines 245–298 verified byte-for-byte identical to the live file for the sections checked). That heredoc is the original scaffold command (`cat > app/smc/market_structure.py << 'PYEOF' ... PYEOF`) that was used to **create** `market_structure.py` in the first place — it is the file's own origin, preserved in a setup script, not a competing or diverged second engine. It is not Python-importable and has no independent existence at runtime.

Because there is only one implementation, "which one is actually used," "which one is more advanced," and "which one should be considered production" all collapse to the same answer: **`app/smc/market_structure.py` — there is nothing to compare it against.**

---

## 7. Dependency graph

```
                         Token Scanner            Crypto Signals / Gold Signal        Dashboard screen
                         screen (WPF)              screen (WPF) + AI Agent              "Market Structure"
                              │                              │                            panel field (WPF)
                              ▼                              ▼                                   ▼
                  POST /token-scan              POST /agent/query  or  internal        GET /dashboard
                  (token_scan.py)               Portfolio Intelligence call                (dashboard.py)
                              │                              │                                   │
                              ▼                              ▼                                   ▼
                 build_token_scan_report()    build_market_scan_report()              _build_market_panel()
                 (token_scorer.py)             (market_scorer.py)                     (dashboard.py, DIRECT call)
                              │                              │                                   │
                              ▼                              ▼                                   │
                _run_technical_analysis()              _analyze()                                │
                              │                              │                                   │
                              └──────────────┬───────────────┘                                   │
                                             ▼                                                    │
                                   MarketStructure(df)  ◄───────────────────────────────────────────┘
                                   .detect_bos_choch()
                                             ▲
                              ┌──────────────┴───────────────┐
                              │                               │
                 build_ta_dashboard()                SignalGenerator.generate()
                 (ta_dashboard.py — 3 extra           (signal_generator.py)
                  MarketStructure calls,                       ▲
                  pivot_window 5/3/8)                           │
                              ▲                        ┌────────┴────────┐
                 (called from BOTH                     │                 │
                  market_scorer.py AND          CryptoScanner        Backtest engine
                  token_scorer.py)               (scheduler/          (backtest/engine.py)
                                                   scanner.py,
                                                   background loop)
```

Every path shown above was confirmed by reading the actual import and call-site code in Sections 1–3 — none is inferred from file or function naming alone.

---

## 8. Duplicate SMC logic check

**Finding: one SMC engine, not two.**

- `app/smc/` contains 5 files: `market_structure.py`, `fvg.py`, `liquidity.py`, `order_blocks.py`, `supply_demand.py`. Each covers a **different** SMC concept (structure/BOS-CHoCH, fair value gaps, liquidity sweeps, order blocks, supply/demand zones respectively) — this is one engine split into focused modules, not duplicate engines for the same concept.
- Within `fvg.py` specifically, two methods do coexist (`detect_fvg()` — live, and `detect_fvg_ict()` — additive/diagnostic-only, discussed in the prior conversation turn), which is a genuine example of two candidate implementations of the *same* concept existing side by side. **`market_structure.py` has no equivalent second method** — `detect_bos_choch()` is the only structure-detection method in the class, called identically everywhere it's used (Section 2).
- No "legacy" or "v1/v2" naming pattern was found anywhere in `app/smc/`.
- No experimental or feature-flagged alternate engine was found — every call site in Section 2 calls the same unconditional `MarketStructure` class with no conditional branch selecting between implementations.

---

## Summary of findings (evidence-based, no recommendation)

| Question | Answer | Evidence location |
|---|---|---|
| Is `market_structure.py` imported anywhere real? | Yes, 5 production files | Section 1 |
| Is `detect_bos_choch()` actually called? | Yes, 7 production call sites | Section 2 |
| Does the AI Scoring path depend on it? | Yes, both the live scanner path (Chain A) and the on-demand Market Scan / Token Scan paths (Chains B, C) route through it before reaching the AI Scorer | Section 3 |
| Is it reachable from the live API? | Yes — confirmed via `app/api/v1/__init__.py`'s `include_router()` calls and `app/main.py`'s startup scanner | Section 3 |
| Production, helper, legacy, or dead? | **Production** | Section 4 |
| Any other BOS/CHoCH/structure implementation exists? | No — one definition, everything else consumes it | Section 5 |
| Multiple SMC engines? | No — one engine, 5 focused modules for 5 different concepts | Section 6, 8 |

No code was reviewed for correctness, no changes were made, and no recommendation is made beyond identifying what is currently used. Waiting for approval before reviewing or modifying any SMC module.
