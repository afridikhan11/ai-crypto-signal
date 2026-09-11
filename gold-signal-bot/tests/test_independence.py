"""The guarantee this project exists for: it shares nothing with the trading
bot next door.

The owner asked for a signal bot "bilkul alehda" - completely apart. That is
not a feeling, it is a property, and a property that decays the first time
someone reaches across for a helper. These tests fail the moment it does.

Independence is also what keeps the trading bot's measurement era intact: it
is mid-experiment, and a shared import is how a change here becomes an
outage there.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "gold_signals"

# Anything owned by the trading bot in `FastAPI Backend/`.
FORBIDDEN_ROOTS = {"app", "alembic", "sqlalchemy", "fastapi", "binance", "ccxt", "pandas"}

# Everything this bot is allowed to reach for, beyond the standard library.
ALLOWED_THIRD_PARTY = {"httpx", "dotenv", "pytest"}


def _modules() -> list:
    return sorted(PACKAGE.glob("*.py"))


def _imported_roots(path: Path) -> set:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # a relative import - our own package
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_module_imports_the_trading_bot(path):
    leaked = _imported_roots(path) & FORBIDDEN_ROOTS
    assert not leaked, (
        f"{path.name} imports {sorted(leaked)}. This project must stay "
        "standalone: different instrument, different feed, no orders, no "
        "shared database. Copy what you need instead of importing it."
    )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_dependencies_stay_to_the_declared_list(path):
    stdlib = sys.stdlib_module_names
    outside = {
        root for root in _imported_roots(path)
        if root not in stdlib and root not in ALLOWED_THIRD_PARTY and root != "gold_signals"
    }
    assert not outside, (
        f"{path.name} imports {sorted(outside)}, which is not in "
        "requirements.txt. Add it there deliberately, or do without it - the "
        "short dependency list is why this deploys anywhere in one step."
    )


def test_the_package_places_no_orders():
    """There is no exchange client here and there must never be one. A signal
    bot that can trade is a trading bot, and this one has no risk limits, no
    position tracking and no stop management to make that safe."""
    banned = ("new_order", "place_order", "algoOrder", "api_key", "api_secret")
    for path in _modules():
        source = path.read_text()
        for term in banned:
            assert term not in source, f"{path.name} mentions {term!r}"
