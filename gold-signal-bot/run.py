#!/usr/bin/env python3
"""Entry point.

    python run.py                 # watch, and send signals as they appear
    python run.py --once          # one poll, print what it decided, exit
    python run.py --test-message  # prove the WhatsApp/Telegram side works
    python run.py --recent        # what has been sent so far
    python run.py --check         # is the configuration complete?

`--test-message` and `--check` are the two that matter on a first setup: they
answer "are my credentials right" without waiting for a setup to appear on a
30 minute chart, which can take a day.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from gold_signals.bot import poll_once, run
from gold_signals.candles import OandaCandles
from gold_signals.config import Settings
from gold_signals.notify import build_sender
from gold_signals.store import SignalStore


def _load_dotenv() -> None:
    """Read .env if python-dotenv is installed. Optional on purpose - the bot
    runs fine with plain environment variables, and one fewer required
    dependency is one fewer thing to install on a VM."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gold Engulfing Break signal bot")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--test-message", action="store_true",
                        help="send a test message through the configured channel")
    parser.add_argument("--recent", type=int, nargs="?", const=10, metavar="N",
                        help="print the last N signals sent (default 10)")
    parser.add_argument("--check", action="store_true",
                        help="report configuration problems and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _load_dotenv()
    _configure_logging(args.verbose)
    settings = Settings.from_env()

    if args.check:
        problems = settings.problems()
        if not problems:
            print(f"Configuration looks complete. Channel: {settings.channel}.")
            return 0
        print("Configuration problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if args.recent is not None:
        store = SignalStore(settings.db_path)
        rows = store.recent(args.recent)
        if not rows:
            print("No signals sent yet.")
        for row in rows:
            print(f"{row['sent_at']}  {row['direction']:<5} {row['instrument']:<8} "
                  f"entry {row['entry']:,.2f}  stop {row['stop']:,.2f}  tp {row['take_profit']:,.2f}")
        store.close()
        return 0

    if args.test_message:
        problems = settings.problems(include_feed=False)
        if problems:
            for problem in problems:
                print(f"  - {problem}")
            return 1
        sender = build_sender(settings)
        stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M")
        sender.send(
            "Gold signal bot is connected.\n\n"
            f"Channel: {sender.name}\n"
            f"Watching: {settings.instrument} {settings.granularity}\n"
            f"{stamp} UTC\n\n"
            "This is a test message. Real signals look different."
        )
        print(f"Test message sent via {sender.name}.")
        return 0

    if args.once:
        problems = settings.problems()
        if problems:
            for problem in problems:
                print(f"  - {problem}")
            return 1
        feed = OandaCandles(settings.oanda_token, settings.oanda_environment,
                            timeout=settings.http_timeout)
        sender = build_sender(settings)
        store = SignalStore(settings.db_path)
        result = poll_once(settings, feed, sender, store)
        print(("SENT: " if result.sent else "no signal: ") + result.reason)
        feed.close()
        store.close()
        return 0

    run(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
