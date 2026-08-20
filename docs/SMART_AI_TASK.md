# Task: "Smart AI" premium module — two strategies + owner-gated page

## Context

Same repo as the DexScreener task: FastAPI backend + WPF desktop client, running on
Google Cloud against testnet. ICT/SMC methodology, funding-rate component already present.

Add a new module branded **"Smart AI"** containing two independent strategies, exposed
through a password-protected premium page in the WPF client.

## Step 0 — Recon first, do not write code yet

Read the existing signal pipeline, config module, DB layer, WPF navigation/MVVM
structure, and the DexScreener service (if already merged). Then report back:

1. Where the strategy abstraction should live, and whether one already exists
2. Whether the existing signal model can carry a `strategy_id` or needs a migration
3. Which charting library the WPF app uses

**Wait for my confirmation before touching existing signal logic.**

## Architecture requirement — read this before anything else

Do **not** build these as two separate bots or two separate codebases. Build:

- A **shared core**: exchange client, risk manager, position sizing, order execution,
  logging, persistence, backtester
- A **strategy interface** (e.g. `BaseStrategy` with `evaluate(market_data) -> Signal | None`)
- Two strategy implementations plugged into that interface
- **Each strategy independently enable/disable-able via config, and each running in its
  own process** so one crashing cannot take the other down

Every signal, fill, and PnL row must be tagged with `strategy_id`. I need to be able to
attribute performance to each strategy separately — if I can't tell which one made or
lost the money, the whole thing is useless to me.

---

## Strategy 1 — ICT Levels

Encode the ICT/SMC concepts as a deterministic engine. No ML, no black box — every
signal must be explainable by which rules fired.

**Level detection (per timeframe):**
- **Dealing range** — swing high and swing low over a configurable lookback
- **Equilibrium** — midpoint of the dealing range
- **Premium / discount** — price position relative to equilibrium, as a percentage
- **Fair Value Gaps (FVG)** — 3-candle imbalance; track bullish and bearish, and mark
  each as untested / partially filled / fully filled
- **Order blocks** — last opposing candle before a displacement move
- **Liquidity pools** — swing highs/lows, and equal highs/lows within a configurable
  tick tolerance
- **Liquidity sweep** — wick beyond a liquidity level followed by a close back inside
- **OTE zone** — 0.62–0.79 retracement of the relevant leg
- **Market structure** — BOS (break of structure) and CHoCH (change of character)

**Multi-timeframe:**
- HTF (4h or 1D, configurable) sets directional bias via market structure
- LTF (15m, configurable) provides entry
- **No counter-bias entries** unless explicitly enabled in config

**Entry logic:**
Signal fires when: HTF bias aligned + liquidity sweep taken + displacement in bias
direction + retracement into an OTE/FVG/order-block confluence zone.
Each condition must be logged individually as pass/fail on every evaluation, so I can
see exactly why a signal did or did not fire.

**Stops and targets:**
- Stop beyond the sweep wick
- Targets at the next opposing liquidity pool, plus equilibrium as a partial-take level
- Reject any setup below a configurable minimum R:R

---

## Strategy 2 — CEX-DEX Divergence

Uses the DexScreener service.

**Core metric:**
`divergence_pct = (cex_perp_price - dex_spot_price) / dex_spot_price * 100`

**Logic:**
- Perp trading at a **premium** to DEX spot = crowded leverage longs, no spot demand
  backing it → mean-reversion short bias
- Perp at a **discount** → opposite
- Combine with funding rate as a confirmation, not a duplicate signal — document
  clearly how the two are weighted, since they are correlated by construction

**Hard guards (non-negotiable):**
- Skip any token below `DEXSCREENER_MIN_LIQUIDITY_USD` — thin pools make the DEX price
  meaningless and the divergence is then pure noise
- Skip if the DEX quote is older than a configurable staleness threshold
- Require the divergence to persist across N consecutive polls before firing; a single
  spike is usually a bad print, not a signal
- Cap position size relative to DEX pool depth

---

## Backtesting — required before either strategy goes live

Both strategies must run through the backtester with:
- Realistic fees, funding payments, slippage, and partial fills
- Walk-forward validation, not a single in-sample fit
- Per-strategy output: total trades, win rate, profit factor, max drawdown, Sharpe,
  and the distribution of R multiples

If a strategy has fewer than 100 trades in the test window, report that prominently —
the results are not yet meaningful and I should not be shown a tidy summary that hides it.

---

## Auth — "Smart AI" premium page

### Critical: the password does NOT live in the WPF client

A WPF app is a .NET assembly and decompiles trivially. A password, hash, or key
compiled into the client is public. **Client-side hiding of the page is UX only and
must never be the security boundary.**

**Backend (this is the real gate):**
- `POST /api/auth/login` — takes password, returns a JWT
- Store only an **argon2 or bcrypt hash** of the owner password, in an env var
  (`OWNER_PASSWORD_HASH`). Never the plaintext, never in the repo, never in the client.
- JWT signed with a secret from env, short expiry (e.g. 12h), with refresh
- **Every `/api/smartai/*` route requires a valid token** and returns 401 without one.
  Not middleware that can be bypassed by route ordering — verify this with a test that
  calls each protected route unauthenticated and asserts 401.
- Rate limit login: max 5 attempts per 15 minutes per IP, then temporary lockout
- Log every login attempt, success and failure, with timestamp and IP

**WPF client:**
- Login dialog on navigating to the Smart AI page
- Token held **in memory only** by default
- If a "remember me" option is added, encrypt the token at rest using Windows DPAPI
  (`ProtectedData`) — never plaintext on disk
- On 401 from any call, clear the token and return the user to the login dialog
- Show a clear locked state, not a crash or a blank page, when unauthenticated

---

## WPF — Smart AI page

Follow existing MVVM patterns, styles and resource dictionaries.

**Layout:**
- Locked state until authenticated
- Strategy selector: tabs or toggle for "ICT Levels" / "CEX-DEX Divergence"
- Per strategy: enable/disable switch, live status indicator, config panel bound to
  the backend config values
- Signals table: time, symbol, direction, entry, stop, target, R:R, and **which rules
  fired** (expandable row — this is the most important column, don't bury it)
- Performance panel per strategy: trades, win rate, profit factor, max drawdown,
  equity curve chart
- Prominent **TESTNET / LIVE** badge. I do not want to ever be confused about which
  mode I am looking at.

**ViewModel:**
- Async throughout, no `.Result` or `.Wait()`
- Explicit `IsLoading` / `ErrorMessage` / `IsEmpty` / `IsLocked` states, each with a
  visible UI representation
- Cancel in-flight requests on navigation away

---

## Constraints

- Do not add any new NuGet or pip dependency without asking me first
- Match existing code style, naming, logging and error-handling patterns
- No secrets in code or in the client
- Both strategies default to **disabled** and **testnet** on first run
- Commits logical and separate: strategy interface → ICT strategy → divergence
  strategy → auth → WPF page → tests

## Tests

- Unit tests for every ICT level-detection function, with hand-built candle fixtures
  including edge cases (gaps, equal highs, single-candle ranges)
- Divergence strategy: stale quote, thin liquidity, single-poll spike, sustained
  divergence — assert the guards actually block the first three
- Auth: every protected route returns 401 unauthenticated, rate limiting triggers,
  expired token rejected, wrong password rejected
- No live API calls in tests

## Deliverable

Summary of: files added/changed, config keys and env vars I need to set, migration
commands, how to generate the password hash, and anything you had to guess about my intent.
