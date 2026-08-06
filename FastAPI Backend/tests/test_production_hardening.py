"""
Production hardening invariants (audit of 2026-07-31).

Each test here corresponds to a confirmed finding, not a style preference.
All are static analysis of the repository - no database, no imports, no
dependency stack - so they run in any environment.
"""
import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


# ==========================================================================
# CRITICAL - secrets must not be in version control or in the image
# ==========================================================================
class TestSecretsAreNotDistributable:
    """`data/binance_credentials.enc` was committed to git (commit 810edaf)
    AND baked into every image layer by `COPY . .`.

    It is Fernet-encrypted, but the key is PBKDF2-derived from SECRET_KEY
    using a salt and iteration count that are both literals in
    app/security/api_key_cipher.py. With SECRET_KEY at its placeholder
    default, every input to the derivation is public.
    """

    def test_gitignore_excludes_the_credentials_file(self):
        body = (BACKEND_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "data/binance_credentials.enc" in body or "data/*.enc" in body, (
            "the encrypted Binance credentials file must be gitignored"
        )

    def test_gitignore_still_excludes_env(self):
        body = (BACKEND_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in body

    def test_dockerignore_exists(self):
        assert (BACKEND_ROOT / ".dockerignore").exists(), (
            "without .dockerignore, `COPY . .` bakes .git, .env and the "
            "credentials file into every image layer"
        )

    def test_dockerignore_excludes_secrets_and_git_history(self):
        body = (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8")
        for required in (".env", "data/*.enc", ".git"):
            assert required in body, f".dockerignore must exclude {required}"

    def test_dockerignore_does_not_break_the_image(self):
        """scripts/ holds the ENTRYPOINT; app/ and alembic/ are the
        application. Excluding any of them produces an image that cannot
        start, and the failure would only appear at deploy time."""
        lines = [
            l.strip() for l in (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("!")
        ]
        for essential in ("app", "app/", "scripts", "scripts/", "alembic", "alembic/",
                          "requirements.txt", "alembic.ini"):
            assert essential not in lines, (
                f".dockerignore excludes {essential!r} - the image would not run"
            )


# ==========================================================================
# HIGH - a dead market-data stream must never look like a healthy one
# ==========================================================================
class TestMarketDataTasksAreSupervised:
    """`asyncio.create_task(...)` whose result is discarded is doubly
    unsafe: asyncio keeps only a WEAK reference, so the task can be
    garbage-collected mid-flight, and any exception it raises is swallowed
    into an unretrieved result.

    For the candle websocket that is a TRADING-CORRECTNESS failure, not
    merely a reliability one: `get_dataframe()` returns the last cached
    frame "even if stale", so a dead stream means the scanner keeps
    generating signals from frozen prices with nothing logged anywhere.
    """

    SCANNER = BACKEND_ROOT / "app/scheduler/universal_scanner.py"

    def _tree(self):
        return ast.parse(self.SCANNER.read_text(encoding="utf-8"))

    def test_no_fire_and_forget_create_task(self):
        """Every create_task result must be bound to a name, not discarded
        as a bare expression statement."""
        orphans = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "create_task":
                    orphans.append(f"line {node.lineno}")
        assert not orphans, (
            "create_task results are discarded (weak reference -> may be "
            f"garbage-collected; exceptions swallowed): {orphans}"
        )

    def test_both_market_data_tasks_are_held_and_supervised(self):
        body = self.SCANNER.read_text(encoding="utf-8")
        for attr in ("self._ws_task", "self._liquidation_task"):
            assert attr in body, f"{attr} must hold a strong reference to its task"
        assert "add_done_callback" in body, (
            "a market-data task that dies must be logged, not silently dropped"
        )

    def test_stop_cancels_the_market_data_tasks(self):
        tree = self._tree()
        stop = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "stop"),
            None,
        )
        assert stop is not None, "UniversalScanner.stop() not found"
        cancels = [
            n for n in ast.walk(stop)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "cancel"
        ]
        assert cancels, "stop() must cancel the market-data tasks it started"

    def test_task_attributes_are_initialised_in_constructor(self):
        """So stop() is safe on a scanner that was constructed but never
        started - which is exactly what happens if startup fails partway."""
        tree = self._tree()
        init = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        assigned = {
            ast.unparse(t) for n in ast.walk(init)
            if isinstance(n, (ast.Assign, ast.AnnAssign))
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        }
        assert "self._ws_task" in assigned
        assert "self._liquidation_task" in assigned


# ==========================================================================
# HIGH - runtime control-plane state must survive a redeploy
# ==========================================================================
class TestControlPlaneStatePersists:
    """data/trading_settings.json holds engine_run_state, auto-trading
    on/off and entry_mode, and is WRITTEN at runtime. In production it had
    no volume, so it lived on the container's ephemeral filesystem: every
    redeploy silently reverted the trading control plane to whatever was
    baked into the image, including re-enabling Auto Trading after a
    deliberate stop."""

    def test_production_mounts_the_data_directory(self):
        body = (BACKEND_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        assert "/app/data" in body, (
            "docker-compose.prod.yml must persist /app/data outside the image, "
            "or runtime trading-control state is lost on every redeploy"
        )

    def test_credentials_path_lives_under_the_mounted_directory(self):
        """If the credentials path ever moves out of data/, the volume stops
        covering it and the image would need to carry secrets again."""
        body = (BACKEND_ROOT / "app/services/binance_credentials.py").read_text(encoding="utf-8")
        assert '"data"' in body and "binance_credentials.enc" in body
