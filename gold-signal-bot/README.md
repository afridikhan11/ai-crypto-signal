# Gold Signal Bot

Engulfing Break setups on gold (XAU/USD, 30 minute candles), sent to WhatsApp.

**It sends messages. It does not trade.** There is no exchange client in this
project and no credential that could place an order.

---

## Why this is a separate project

It lives beside the trading bot in this repository but shares nothing with it:

| | Trading bot (`FastAPI Backend/`) | This |
|---|---|---|
| Instrument | Binance XAUUSDT perpetual | OANDA XAU/USD spot |
| Data | Binance WebSocket + REST | OANDA v20 REST |
| Does | places orders, manages stops, sizes positions | sends a message |
| Storage | PostgreSQL + Alembic | one SQLite file |
| Needs | Postgres, Redis, exchange keys | a token and a phone number |

The two prices are genuinely different - a USDT-margined perpetual is not spot
gold - so there was nothing to reuse even if it were wise to. And the trading
bot is currently mid-measurement; anything shared is a way for a change here to
become an outage there. `tests/test_independence.py` fails if an import ever
crosses between them.

To move this into its own repository later, copy the `gold-signal-bot/`
directory. It has no parent-directory dependencies.

---

## The setup it watches

An **engulfing** is one candle swallowing the next, full range - high and low,
not just the body. An **engulfing break** is a later candle *closing* beyond
both candles of that pair, the other way. A bearish engulfing broken upward is
a buy; a bullish engulfing broken downward is a sell.

The trade is not taken at the break. It waits for price to come back to the
candle that *was* engulfed:

```
BUY    entry  engulfed low  + 30 pips     stop  engulfed low  - 90 pips
SELL   entry  engulfed high - 30 pips     stop  engulfed high + 90 pips
```

1 pip = $0.10 on gold, so risk is always 120 pips = **$12.00**, and the target
is 1:2 — **$24.00**. Every one of those numbers is a setting.

Two rules keep stale setups out: only the **first** candle to close beyond the
pattern counts as the break, and it must be within `MAX_BARS_SINCE_BREAK`
closed candles. A setup dies the moment price trades back *through* the
engulfed candle's extreme.

---

## Setup

### 1. A price feed

Open a free **practice** account at [oanda.com](https://www.oanda.com), then
Manage API Access → generate a token. Practice and live issue *different*
tokens; a practice token against the live host returns 401.

### 2. A way to reach your phone

Pick one:

- **WhatsApp (Twilio)** — what was asked for. The
  [Twilio WhatsApp sandbox](https://console.twilio.com) sends a real message
  within minutes of signing up, no business verification. Roughly half a cent
  per message. A dedicated number needs verification later.
- **Telegram** — free, instant, no approval. Message
  [@BotFather](https://t.me/BotFather) for a token. Worth running for a week
  first to see whether the signals are any good before paying for them.
- **Console** — the default. Prints to the terminal, needs no account.

### 3. Configure and run

```bash
cd gold-signal-bot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in OANDA_API_TOKEN, then SIGNAL_CHANNEL and its credentials

python run.py --check          # is anything missing?
python run.py --test-message   # does a message actually arrive?
python run.py --once           # what does it see right now?
python run.py                  # watch
```

`--check` and `--test-message` are the two that matter on a first setup: they
prove the credentials without waiting for a setup to appear, which on a 30
minute chart can take a day.

### On the VM

```bash
cd gold-signal-bot
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f
```

Its own compose project - no shared network, no shared volume, nothing in
common with the trading bot's stack. The SQLite file lives on a mounted volume
so a rebuild does not forget what was already sent and re-announce all of it.

---

## Settings

Every one lives in `.env`; `.env.example` lists them all with comments.

| | Default | |
|---|---|---|
| `INSTRUMENT` | `XAU_USD` | |
| `GRANULARITY` | `M30` | `M15`, `H1`… also work |
| `PIP_SIZE` | `0.10` | brokers disagree on this; getting it wrong moves the trade by 10× |
| `ENTRY_PIPS` | `30` | into the engulfed candle |
| `STOP_PIPS` | `90` | beyond its extreme |
| `RISK_REWARD` | `2.0` | |
| `MAX_BARS_SINCE_BREAK` | `3` | how long a break stays tradeable |
| `POLL_SECONDS` | `60` | |
| `IDLE_POLL_SECONDS` | `900` | over the weekend, when gold is shut |

---

## Repeats, and why there are none

A setup stays alive for several candles while the bot polls every minute.
Without a memory the same trade would arrive sixty times, and a bot that spams
is a bot that gets muted.

Each setup has an identity built from the engulfed candle and the break that
confirmed it - none of which move while the setup is alive - and SQLite
remembers what has been sent across restarts. A *failed* send is deliberately
not recorded, so it retries on the next poll: a duplicate message is a
nuisance, a missed signal is the product failing.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No network, no clock, no phone: the OANDA and Twilio calls run against a mock
transport, and the loop is driven with `max_polls`.
