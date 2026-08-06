# Binance Credential Wiring Audit — 2026-07-31

Read-only audit. No files were modified. Every claim below cites the file and
line it was verified against; nothing here is inferred or assumed.

---

## 1. Settings → Binance API Credentials

**UI**: `ViewModels/SettingsViewModel.cs`. `ApiKey`/`SecretKey`/`Testnet` are
plain `[ObservableProperty]` fields bound to the Settings screen. `Save()`
(lines 125-189) splits into two independent writes:

- Non-sensitive prefs (Telegram, theme, language, risk %, leverage) →
  `%AppData%\AI_Crypto_Signal_Pro\settings.json`, plaintext, local to the
  WPF machine (lines 136-154).
- Binance API key/secret → **never touch that file**. Sent straight to the
  backend via `POST account/credentials` with `{ApiKey, ApiSecret, Testnet}`
  (lines 159-166), and the fields are cleared from memory immediately after
  a successful save (lines 171-172). This is enforced by the code, not just
  documented — there is no code path in `SettingsViewModel.cs` that writes
  `ApiKey`/`SecretKey` to `SettingsData` or to disk.

**Backend storage**: `app/api/v1/endpoints/account.py:50-57` (`POST
/account/credentials`) → `app/services/binance_credentials.py:34-35`
(`save_credentials`) → `app/security/api_key_cipher.py:98-103`
(`ApiKeyCipher.save_to_file`).

**Format**: one file, `FastAPI Backend/data/binance_credentials.enc`. Fernet
symmetric encryption; the key is PBKDF2-HMAC-SHA256 derived from
`Settings.secret_key` (390,000 iterations, fixed non-secret salt —
`api_key_cipher.py:41-42`). The payload is a single JSON blob:
`{"api_key", "api_secret", "testnet"}` (`api_key_cipher.py:80-85`) — key,
secret, and environment are encrypted together, atomically. There is no
database table, no separate config file, and no way for the testnet flag to
be saved out of sync with the key/secret, because they're one write.

Confirmed only one such file exists anywhere in the repo:
```
$ find . -iname "*.enc"
./data/binance_credentials.enc
```

---

## 2. Credential loading — full trace

```
Settings UI (WPF)
  → POST /account/credentials  [account.py:50]
  → binance_credentials.save_credentials()  [binance_credentials.py:34]
  → ApiKeyCipher.encrypt() + write  [api_key_cipher.py:98]
  → data/binance_credentials.enc
```

Every read of that file goes through exactly one function:
`binance_credentials.load_credentials()` (`binance_credentials.py:38-45`) —
decrypts and returns a dict, or `None`. It is **not memoized**: every call
re-reads and re-decrypts the file from disk. Two thin wrappers build a ready
service from it:

- `build_service_from_saved()` → `BinanceAccountService` (read-only account
  data)
- `build_trading_service_from_saved()` → `BinanceTradingService` (order
  placement)

Both are in `binance_credentials.py:54-81`, both call `load_credentials()`
fresh, both raise `FileNotFoundError` if nothing is saved yet — no silent
fallback to anything else.

**Every consumer found in the repo** (full-text grep for
`build_service_from_saved`, `build_trading_service_from_saved`,
`BinanceTradingService(`, `BinanceAccountService(` across `app/`, `scripts/`,
`tests/`):

| # | Consumer | File : line | Loads via |
|---|---|---|---|
| 1 | Account Snapshot | `account.py:113` | `build_service_from_saved()` |
| 2 | Order history / income history | `account.py:139,166` | `build_service_from_saved()` |
| 3 | Execute signal (Manual/Auto) | `trading.py:109` | `build_trading_service_from_saved()` |
| 4 | Close position | `trading.py:231,246` | both wrappers |
| 5 | Auto Trading control plane (close-all, cancel-all, kill switch) | `trading_control_service.py:61,78,114` | both wrappers |
| 6 | Dashboard account panel | `dashboard.py:50` | `build_service_from_saved()` |
| 7 | Portfolio Intelligence (live exposure) | `portfolio_intelligence.py:89,205` | `build_service_from_saved()` |
| 8 | Position sizing / balance cache | `signal_service.py:78` | `build_service_from_saved()`, result cached 30s (see §3) |
| 9 | Signal Monitor — exchange stop sync | `signal_monitor.py:390,424` | `binance_credentials.load_credentials()` **directly**, service built inline |
| 10 | Scanner — Testnet confidence override | `universal_scanner.py:180` | `load_credentials()` **directly**, read-only, flag check only |
| 11 | Validation Harness | `validate_testnet_execution.py:470,807` | `build_service_from_saved()` + `build_trading_service_from_saved()` |
| 12 | `test_connection` (pre-save test) | `account.py:80-97` | request body **if supplied**, else `build_service_from_saved()` — see §6 |

Items 9-10 don't call the two wrapper functions — they call
`load_credentials()` directly and either build the service inline
(`signal_monitor.py`) or just read the `testnet` flag
(`universal_scanner.py`). This is the same underlying file and the same
decrypt call, so it is **not a wiring break** — but it is a second code
pattern doing what the wrappers already do, item 9 duplicating the
dict-unpacking that `build_trading_service_from_saved()` already
encapsulates. Noted in §5.

**Scanner market data** (`app/services/binance_service.py`, the klines/
ticker/liquidation-stream engine the Scanner actually runs on) has **zero**
references to `api_key`/`api_secret` anywhere in the file — confirmed by
grep. It doesn't need credentials: Binance Futures market-data endpoints are
public. The Scanner's only touch point with credentials at all is the
read-only testnet-flag check in item 10.

---

## 3. Stale / alternate / hardcoded sources

**`.env`-based `Settings.binance_api_key` / `binance_secret_key` — DEAD, not
wired to anything.**

`app/core/config.py:34-35` declares:
```python
binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
binance_secret_key: str = Field(default="", alias="BINANCE_SECRET_KEY")
```
and `.env.example` / `.env.production.example` both present these as if
they're how you configure Binance credentials. A full-repo grep for
`binance_api_key` / `binance_secret_key` (excluding the declaration itself)
returns **zero** matches — no endpoint, service, scheduler, or script reads
`settings.binance_api_key` or `settings.binance_secret_key` anywhere. This
is leftover configuration surface from before the encrypted-Settings-UI flow
existed; it is not a competing credential source because nothing consumes
it, but it is actively misleading — `.env.example` tells a reader this is
how you set up Binance access, and it does nothing.

Checked the live local `.env`: `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` are
present but still hold the literal example placeholders
(`your_api_key`/`your_secret_key`), confirmed by direct equality check
without printing the values. No real secret is sitting there unused — the
field is simply inert.

**Hardcoded credentials**: none found. Grepped the FastAPI backend for
key/secret-literal patterns and the WPF project for `ApiKey = "..."` /
`SecretKey = "..."` assignments — no matches in either.

**Old/duplicate config files**: none. Only `data/binance_credentials.enc`
exists on disk; no legacy JSON credential file, no second `.enc`.

**Cached credentials**: `load_credentials()` itself caches nothing — every
call re-reads and re-decrypts the file, so a Settings change takes effect on
the very next call, no restart required. The one cache in the whole flow is
`signal_service.py:57-65`, `_ACCOUNT_BALANCE_CACHE` — a 30-second TTL cache
of the **fetched balance value** used for position-sizing display, not of
the credentials or a service object. Practical effect: for up to 30 seconds
after changing Settings (key, secret, or environment), a displayed balance
figure can lag: it does not affect which credentials any trade actually
uses, since `_get_account_balance()` still calls `build_service_from_saved()`
fresh on every cache miss.

---

## 4. Component table

| Component | Credential Source | Status |
|---|---|---|
| Settings UI | Sends key/secret/testnet to `POST /account/credentials`; never persisted locally | Correct |
| Storage | `data/binance_credentials.enc`, Fernet-encrypted, one atomic blob | Correct |
| Account Snapshot | `build_service_from_saved()` | Correct |
| Manual Trading (Execute) | `build_trading_service_from_saved()` | Correct |
| Auto Trading control plane | `build_service_from_saved()` + `build_trading_service_from_saved()` | Correct |
| Dashboard / Portfolio Intelligence | `build_service_from_saved()` | Correct |
| Position sizing | `build_service_from_saved()`, balance cached 30s | Correct (cache is a value, not a credential) |
| Signal Monitor (stop sync) | `load_credentials()` direct + environment-match guard | Correct, inconsistent call style (see §5) |
| Scanner (market data) | none required — public endpoints | Correct / not applicable |
| Scanner (confidence override) | `load_credentials()` direct, read-only | Correct |
| Validation Harness | `build_service_from_saved()` + `build_trading_service_from_saved()` | Correct — identical to production |
| `test_connection` pre-save test | Request body if supplied, else saved credentials | Correct by design (opt-in test-before-save) |
| `.env` / `Settings.binance_api_key`/`binance_secret_key` | Declared, read by nothing | Dead / unused, misleading |

---

## 5. Multiple sources — which one is active

Exactly **one** source is live: the encrypted file via
`binance_credentials.load_credentials()`. The `.env`-backed
`Settings.binance_api_key`/`binance_secret_key` fields exist in the config
schema but are provably unread anywhere, so they are not a competing source
today — only a latent one, in the sense that nothing stops a future change
from wiring something to them by mistake, and the example files actively
invite a new operator to fill them in for no effect.

Two consumers (`signal_monitor.py`, `universal_scanner.py`) reach the
correct file through `load_credentials()` directly rather than through
`build_service_from_saved()` / `build_trading_service_from_saved()`. Same
data, same result — not a bug — but `signal_monitor.py:407-409` duplicates
the exact dict-to-constructor mapping the wrapper already does, which is a
second place that mapping would need to be updated if the credentials
schema ever changed shape.

---

## 6. Does the Validation Harness bypass Settings and build its own service?

**No.** `scripts/validate_testnet_execution.py` imports
`build_service_from_saved` and `build_trading_service_from_saved` directly
from `app.services.binance_credentials` (lines 467, 806) — the same two
functions every production endpoint uses, not a reimplementation. It
constructs no `httpx` client of its own and does not read `.env` or any
other source. This was already independently confirmed while fixing the
harness's import bug earlier in this engagement (the correct factory name
was verified from source, not guessed).

The one deliberate exception anywhere in the system is `test_connection`
(`account.py:80-97`): if the request body carries `api_key`/`api_secret`,
it tests those instead of the saved ones. That's an explicit, opt-in
"test before you save" path the endpoint's own docstring describes — it
never places an order, and it's not something the harness or any trading
path uses.

---

## 7. Testnet/Mainnet propagation

The flag is stored inside the same encrypted blob as the key/secret
(`api_key_cipher.py:82-84`), so it cannot be saved out of sync with them —
there is no separate "environment" setting anywhere.

Every consumer reads it the same way, `creds.get("testnet", False)`, and
both `BinanceTradingService.__init__` (`binance_trading_service.py:124-132`)
and `BinanceAccountService.__init__` (`binance_account_service.py:233-242`)
derive `self.testnet`, `self.environment` (`"testnet"`/`"mainnet"`), and
`self.base_url` from that single constructor argument:

```python
FUTURES_MAINNET_URL = "https://fapi.binance.com"
FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"
...
self.base_url = self.FUTURES_TESTNET_URL if testnet else self.FUTURES_MAINNET_URL
```

Every HTTP call in both classes routes through `self.base_url` — confirmed
by grep, no call site constructs a URL independently. Every place
`environment` is surfaced (dashboard panel, account snapshot, credentials
status, close-position response) reads `service.environment` /
`snapshot.environment`, never a second independently-stored value. WPF
never computes or stores its own environment flag — every "Testnet"/
"Mainnet" label on screen is deserialized from a backend DTO field.

One consumer goes further: `signal_monitor.py:398-405` compares a signal's
`executed_environment` (captured at the moment it was placed) against the
**currently** saved environment before syncing its exchange stop, and
refuses if they've diverged — e.g. Settings gets flipped from Testnet to
Mainnet while a Testnet position is still open. That protects against
exactly the cross-environment mixing this objective is checking for.

---

## Verdict

Wiring is correct. One saved-credentials file, one decrypt function, every
trading/account/harness/scanner consumer reaches it through that function
or its two direct wrappers, and the testnet/mainnet flag travels with the
key/secret as a single atomic unit with no possible divergence.

Two things worth a deliberate decision, not urgent, nothing fixed yet:

1. `Settings.binance_api_key` / `binance_secret_key` in `config.py` and the
   `.env*.example` files are dead — unread anywhere. Recommend either
   deleting them or wiring a one-time migration/import path, so the example
   files stop telling operators to configure something that does nothing.
2. `signal_monitor.py` reconstructs the service from `load_credentials()`
   inline instead of calling `build_trading_service_from_saved()` /
   `build_service_from_saved()`. Same data today; routing it through the
   existing wrappers would remove the duplicated dict-to-constructor
   mapping.
