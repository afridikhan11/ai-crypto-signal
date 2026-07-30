"""
Legacy Isolation enforcement.

This suite is the mechanical guarantee behind the Universal ICT Platform's
central claim: **no production code depends on retail strategies.**

It does not read docstrings or trust comments - it parses the real `import`
statements of every module in the repository with `ast` and fails if a
production package reaches into `app/legacy/`, or if retail indicator code
reappears outside the quarantine.

If this suite fails, the architectural boundary has been broken and the
platform is no longer "universal ICT" regardless of what any report says.
"""
import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path("/sessions/eloquent-gracious-goodall/mnt/ai-crypto-signal/FastAPI Backend")
APP_ROOT = BACKEND_ROOT / "app"

# Packages that make up the production Universal ICT path. Nothing in here
# may import from app.legacy.
PRODUCTION_PACKAGES = (
    "app/smc",
    "app/ai",
    "app/assets",
    "app/risk",
    "app/strategy",
)

# Individual production modules outside those packages.
PRODUCTION_MODULES = (
    "app/scheduler/universal_scanner.py",
    "app/scheduler/signal_monitor.py",
)

# Retail TA surface that must never be imported by production code.
RETAIL_MODULE_PREFIXES = (
    "app.legacy",
    "ta",            # the third-party retail TA package
)

# Modules legitimately allowed to use legacy code: the pre-ICT dashboards
# and scan paths that predate the migration, plus the backtest engine which
# is explicitly out of scope for modification. Each is a KNOWN, DOCUMENTED
# consumer - see app/legacy/__init__.py.
ALLOWED_LEGACY_CONSUMERS = {
    "app/services/market_scorer.py",
    "app/services/ta_dashboard.py",
    "app/services/token_scorer.py",
    "app/backtest/engine.py",
}


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports_of(path: Path):
    """Every module path this file imports, from real AST - not text search."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:  # pragma: no cover - a syntax error is its own failure
        pytest.fail(f"{path} does not parse: {e}")
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.append(node.module)
    return found


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _production_files():
    seen = set()
    for package in PRODUCTION_PACKAGES:
        for path in _python_files(BACKEND_ROOT / package):
            seen.add(path)
    for module in PRODUCTION_MODULES:
        candidate = BACKEND_ROOT / module
        if candidate.exists():
            seen.add(candidate)
    return sorted(seen)


class TestProductionNeverImportsLegacy:
    def test_production_files_were_found(self):
        """Guard against the suite silently passing because it matched nothing."""
        files = _production_files()
        assert len(files) > 20, f"expected the production ICT surface, found {len(files)} files"

    def test_no_production_module_imports_retail_code(self):
        violations = []
        for path in _production_files():
            for imported in _imports_of(path):
                for banned in RETAIL_MODULE_PREFIXES:
                    if imported == banned or imported.startswith(banned + "."):
                        violations.append(f"{_rel(path)} imports {imported}")
        assert not violations, (
            "Production ICT code must not import retail strategy code:\n  "
            + "\n  ".join(violations)
        )

    def test_signal_generator_specifically_is_clean(self):
        """The pipeline itself is the highest-value single check."""
        imports = _imports_of(BACKEND_ROOT / "app/strategy/signal_generator.py")
        assert not any(i.startswith("app.legacy") for i in imports)
        assert not any(i == "ta" or i.startswith("ta.") for i in imports)

    def test_universal_scanner_specifically_is_clean(self):
        imports = _imports_of(BACKEND_ROOT / "app/scheduler/universal_scanner.py")
        assert not any(i.startswith("app.legacy") for i in imports)
        assert not any(i == "ta" or i.startswith("ta.") for i in imports)


class TestLegacyConsumersAreKnown:
    def test_every_legacy_importer_is_an_approved_consumer(self):
        """A NEW module importing legacy code fails here until it is
        explicitly acknowledged - the boundary cannot erode by accident."""
        unexpected = []
        for path in _python_files(APP_ROOT):
            rel = _rel(path)
            if rel.startswith("app/legacy/"):
                continue
            if any(i.startswith("app.legacy") for i in _imports_of(path)):
                if rel not in ALLOWED_LEGACY_CONSUMERS:
                    unexpected.append(rel)
        assert not unexpected, (
            "These modules import app.legacy but are not documented consumers "
            f"(see app/legacy/__init__.py): {unexpected}"
        )

    def test_documented_consumers_still_exist(self):
        """Keeps ALLOWED_LEGACY_CONSUMERS honest: if a consumer is migrated
        off legacy or deleted, this fails so the allowlist gets tightened
        instead of quietly over-permitting."""
        for rel in ALLOWED_LEGACY_CONSUMERS:
            path = BACKEND_ROOT / rel
            assert path.exists(), f"allowlisted legacy consumer no longer exists: {rel}"
            assert any(i.startswith("app.legacy") for i in _imports_of(path)), (
                f"{rel} no longer imports app.legacy - remove it from "
                f"ALLOWED_LEGACY_CONSUMERS so the allowlist stays minimal"
            )


class TestLegacyQuarantineIsPopulated:
    def test_retail_modules_live_under_legacy(self):
        for name in ("confirmation.py", "candlestick_patterns.py", "chart_patterns.py", "trend.py"):
            assert (APP_ROOT / "legacy" / name).exists(), f"expected app/legacy/{name}"

    def test_old_locations_are_gone(self):
        """They were MOVED, not copied - a lingering duplicate would let
        production keep importing the old path."""
        for stale in (
            "app/indicators/confirmation.py",
            "app/indicators/candlestick_patterns.py",
            "app/indicators/chart_patterns.py",
            "app/analysis/trend.py",
        ):
            assert not (BACKEND_ROOT / stale).exists(), f"{stale} should have been moved to app/legacy/"

    def test_legacy_package_documents_itself(self):
        text = (APP_ROOT / "legacy" / "__init__.py").read_text(encoding="utf-8")
        assert "LEGACY" in text
        assert "app/smc/order_flow.py" in text, "must point readers at the ICT-native replacement"


class TestExactlyOneOfEachCoreComponent:
    """The Universal Platform success criteria, asserted structurally."""

    def test_exactly_one_scanner_class(self):
        scanner_classes = []
        for path in _python_files(APP_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Scanner"):
                    scanner_classes.append(f"{_rel(path)}::{node.name}")
        assert scanner_classes == ["app/scheduler/universal_scanner.py::UniversalScanner"], (
            f"expected exactly one UniversalScanner, found: {scanner_classes}"
        )

    def test_no_per_asset_scanner_names_anywhere(self):
        for path in _python_files(APP_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert node.name not in ("CryptoScanner", "GoldScanner", "ForexScanner"), (
                        f"per-asset scanner {node.name} found in {_rel(path)}"
                    )

    def test_exactly_one_signal_generator_class(self):
        found = []
        for path in _python_files(APP_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "SignalGenerator":
                    found.append(_rel(path))
        assert found == ["app/strategy/signal_generator.py"], f"found: {found}"

    def test_exactly_one_risk_engine_class(self):
        found = []
        for path in _python_files(APP_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "RiskEngine":
                    found.append(_rel(path))
        assert found == ["app/risk/risk_engine.py"], f"found: {found}"

    def test_no_per_asset_ai_classes(self):
        for path in _python_files(APP_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert node.name not in (
                        "CryptoAI", "GoldAI", "ForexAI", "CryptoScorer", "GoldScorer",
                    ), f"per-asset AI {node.name} found in {_rel(path)}"
