"""
Schema ownership enforcement (2026-07-31).

THE INVARIANT
--------------------------------------------------------------------------
**Alembic is the single owner of the database schema.** No application
code creates, alters or drops a table, column or type.

WHY THIS SUITE EXISTS
--------------------------------------------------------------------------
This project spent its whole life with two schema owners. `app/main.py`
called `Base.metadata.create_all()` on every startup and then applied
about fifteen hand-written `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
statements, a destructive TP1/2/3 collapse and `ALTER TYPE ... ADD VALUE`
statements. Alembic was never invoked anywhere - not in the Dockerfile,
not in either compose file, not in any entrypoint - so it never created
its `alembic_version` table and the migration history silently diverged
from the live schema.

That failure was invisible: the schema was CORRECT, just untracked, so
nothing broke until someone needed a migration to apply. A comment saying
"Alembic owns this" would not have prevented it. These tests would have.

Everything here is static analysis of the repository - no database, no
imports, no dependency stack. It parses real syntax rather than grepping,
so a DDL string in a comment or a docstring is correctly ignored while a
DDL string in executable code is not.
"""
import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT / "app"

# The ONE module allowed to contain the legacy in-process bootstrap. It is
# quarantined, documented, and refuses to run unless DB_AUTO_BOOTSTRAP is
# explicitly enabled.
QUARANTINE = "app/core/legacy_schema_bootstrap.py"

# SQL that changes schema. Data statements (SELECT/INSERT/UPDATE/DELETE)
# are deliberately absent - the application is expected to write data.
DDL_MARKERS = (
    "ALTER TABLE",
    "ALTER TYPE",
    "CREATE TYPE",
    "DROP TYPE",
    "CREATE TABLE",
    "DROP TABLE",
    "CREATE INDEX",
)


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of Constant nodes that are docstrings, so prose about DDL in a
    module/class/function docstring is not mistaken for executable SQL."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _executable_ddl_strings(path: Path):
    """Every string literal containing DDL that is NOT a docstring.

    Comments never appear in the AST at all, which is exactly why this is
    parsed rather than grepped: the cleanup left explanatory comments
    naming these statements, and those must not trip the check.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            upper = " ".join(node.value.upper().split())
            for marker in DDL_MARKERS:
                if marker in upper:
                    found.append((marker, node.value.strip()[:70]))
                    break
    return found


def _create_all_calls(path: Path):
    """Real `...create_all(...)` calls and bare `create_all` references in
    executable positions - not the words appearing in prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "create_all":
            found.append(ast.dump(node)[:80])
        elif isinstance(node, ast.Name) and node.id == "create_all":
            found.append(node.id)
    return found


# ==========================================================================
# The core invariant
# ==========================================================================
class TestApplicationCodePerformsNoDDL:
    def test_the_suite_actually_sees_the_application(self):
        """Guard against a silent pass because nothing was scanned."""
        assert len(list(_python_files(APP_ROOT))) > 50

    def test_no_create_all_anywhere_outside_the_quarantine(self):
        violations = []
        for path in _python_files(APP_ROOT):
            if _rel(path) == QUARANTINE:
                continue
            for hit in _create_all_calls(path):
                violations.append(f"{_rel(path)}: {hit}")
        assert not violations, (
            "Base.metadata.create_all() must not be called by application "
            "code - Alembic owns the schema:\n  " + "\n  ".join(violations)
        )

    def test_no_ddl_statements_outside_the_quarantine(self):
        violations = []
        for path in _python_files(APP_ROOT):
            if _rel(path) == QUARANTINE:
                continue
            for marker, snippet in _executable_ddl_strings(path):
                violations.append(f"{_rel(path)}: {marker} -> {snippet!r}")
        assert not violations, (
            "Schema DDL must live in alembic/versions/, not in application "
            "code:\n  " + "\n  ".join(violations)
        )

    def test_main_py_is_completely_free_of_schema_management(self):
        """The specific file that owned the schema for this project's whole
        life. Called out separately so a failure names the real regression
        instead of appearing as one entry in a list."""
        main = BACKEND_ROOT / "app/main.py"
        assert not _create_all_calls(main), "app/main.py must never call create_all() again"
        assert not _executable_ddl_strings(main), "app/main.py must never execute DDL again"


# ==========================================================================
# The quarantine itself
# ==========================================================================
class TestLegacyBootstrapIsQuarantined:
    def test_the_quarantine_module_exists_and_still_holds_the_history(self):
        """It was MOVED, not deleted - it is the only executable record of
        how the pre-Alembic schema was built, including the one-time
        TP1/2/3 collapse that no migration reproduces."""
        path = BACKEND_ROOT / QUARANTINE
        assert path.exists(), f"{QUARANTINE} is missing - the legacy bootstrap must be preserved, not deleted"
        markers = {m for m, _ in _executable_ddl_strings(path)}
        assert "ALTER TABLE" in markers
        assert "ALTER TYPE" in markers
        assert _create_all_calls(path), "the quarantined bootstrap should still contain create_all()"

    def test_the_bootstrap_refuses_to_run_when_the_flag_is_off(self):
        """Defence in depth: the caller in main.py checks the flag, and the
        function checks it again. A future caller that forgets cannot
        silently re-enable in-process schema management."""
        source = (BACKEND_ROOT / QUARANTINE).read_text(encoding="utf-8")
        tree = ast.parse(source)
        func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "run_legacy_schema_bootstrap"),
            None,
        )
        assert func is not None, "run_legacy_schema_bootstrap() not found"
        raises = [n for n in ast.walk(func) if isinstance(n, ast.Raise)]
        assert raises, "the bootstrap must raise when DB_AUTO_BOOTSTRAP is disabled"
        assert "db_auto_bootstrap" in source

    def test_the_flag_defaults_to_false(self):
        """The single most important line in this cleanup. If this default
        ever flips, every deployment silently regains a second schema
        owner."""
        config = (BACKEND_ROOT / "app/core/config.py").read_text(encoding="utf-8")
        tree = ast.parse(config)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "db_auto_bootstrap":
                    call = node.value
                    assert isinstance(call, ast.Call)
                    default = next(
                        (kw.value for kw in call.keywords if kw.arg == "default"), None
                    )
                    assert isinstance(default, ast.Constant) and default.value is False, (
                        "DB_AUTO_BOOTSTRAP must default to False"
                    )
                    return
        raise AssertionError("db_auto_bootstrap setting not found in app/core/config.py")

    def test_only_main_startup_may_reach_the_quarantine(self):
        importers = []
        for path in _python_files(APP_ROOT):
            if _rel(path) == QUARANTINE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "legacy_schema_bootstrap" in node.module:
                    importers.append(_rel(path))
        assert importers == ["app/main.py"], (
            f"only app/main.py's startup may reference the quarantined bootstrap, found: {importers}"
        )


# ==========================================================================
# Alembic must actually be invoked
# ==========================================================================
class TestContainersRunMigrations:
    """The root cause was not a bad migration - it was that Alembic was
    never RUN. These assert the invocation exists, runs after the database
    is genuinely reachable, and cannot be bypassed.

    Migrations used to live in each file's `command:` string. That was
    fragile: `command:` overrides CMD wholesale, so editing the server
    arguments in one compose file could silently drop the migration from
    that deployment and nothing would notice. They now live in the image's
    ENTRYPOINT, which `command:` cannot override.
    """

    COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")
    ENTRYPOINT = "scripts/docker-entrypoint.sh"

    def test_entrypoint_script_exists(self):
        assert (BACKEND_ROOT / self.ENTRYPOINT).exists(), f"{self.ENTRYPOINT} is missing"

    def test_entrypoint_waits_then_migrates_then_execs(self):
        """Order is the whole point. Migrating before the wait turns a
        startup race into a traceback that looks like a migration bug;
        exec'ing before migrating serves an unmigrated schema."""
        body = (BACKEND_ROOT / self.ENTRYPOINT).read_text(encoding="utf-8")
        wait = body.find("wait_for_db")
        migrate = body.find("alembic upgrade head")
        serve = body.find('exec "$@"')

        assert wait != -1, "the entrypoint must wait for the database"
        assert migrate != -1, "the entrypoint must run `alembic upgrade head`"
        assert serve != -1, (
            'the entrypoint must `exec "$@"` so the server becomes PID 1 and '
            "receives SIGTERM directly"
        )
        assert wait < migrate < serve, (
            f"entrypoint order must be wait -> migrate -> exec "
            f"(found positions wait={wait}, migrate={migrate}, exec={serve})"
        )

    def test_the_wait_is_invoked_as_a_module_not_a_file_path(self):
        """`python /app/scripts/wait_for_db.py` puts /app/scripts on
        sys.path[0] - NOT the working directory. WORKDIR is irrelevant:
        cwd is only prepended for `python -m`, `python -c` and the REPL.
        That produced `ModuleNotFoundError: No module named 'app'` on a
        container whose cwd was /app and which had /app/app/__init__.py.

        `alembic` and `uvicorn` were unaffected only because each adds the
        current directory itself - alembic.ini's `prepend_sys_path = .` and
        uvicorn's `--app-dir` (default "."). This script had no equivalent.
        """
        body = (BACKEND_ROOT / self.ENTRYPOINT).read_text(encoding="utf-8")
        offenders = [
            line.strip() for line in body.splitlines()
            if "wait_for_db" in line
            and not line.strip().startswith("#")
            and "-m scripts.wait_for_db" not in line
        ]
        assert not offenders, (
            "invoke the wait as `python -m scripts.wait_for_db`; running it by "
            f"file path leaves the project root off sys.path: {offenders}"
        )

    def test_scripts_is_a_real_package(self):
        assert (BACKEND_ROOT / "scripts/__init__.py").exists(), (
            "scripts/__init__.py makes `python -m scripts.wait_for_db` resolve "
            "deterministically instead of via namespace-package scanning"
        )

    def test_wait_script_also_works_when_run_by_path(self):
        """Defence in depth. Entrypoint scripts get debugged with
        `docker exec ... python scripts/wait_for_db.py`, and `-m` depends on
        cwd. Anchoring sys.path on __file__ makes every invocation work."""
        body = (BACKEND_ROOT / "scripts/wait_for_db.py").read_text(encoding="utf-8")
        assert "Path(__file__).resolve().parent.parent" in body, (
            "wait_for_db.py must put the project root on sys.path itself, "
            "anchored on __file__ rather than on cwd or PYTHONPATH"
        )
        assert "sys.path.insert" in body

    def test_entrypoint_aborts_on_any_failure(self):
        body = (BACKEND_ROOT / self.ENTRYPOINT).read_text(encoding="utf-8")
        assert "set -e" in body, (
            "without `set -e` a failed migration would be followed by the "
            "server starting anyway, against a half-migrated database"
        )

    def test_dockerfile_wires_the_entrypoint(self):
        body = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        entry = [l for l in body.splitlines() if l.startswith("ENTRYPOINT")]
        assert entry, "Dockerfile must set ENTRYPOINT"
        assert "docker-entrypoint.sh" in entry[0], f"unexpected ENTRYPOINT: {entry[0]}"
        assert any(l.startswith("CMD") and "uvicorn" in l for l in body.splitlines()), (
            "Dockerfile must provide a default uvicorn CMD for the entrypoint to exec"
        )

    def test_no_compose_file_overrides_the_entrypoint(self):
        """Overriding `entrypoint:` WOULD bypass the wait and the
        migration - the one hole left in this design."""
        for name in self.COMPOSE_FILES:
            body = (BACKEND_ROOT / name).read_text(encoding="utf-8")
            offenders = [
                l for l in body.splitlines()
                if l.strip().startswith("entrypoint:")
            ]
            assert not offenders, (
                f"{name} overrides entrypoint, which skips the database wait and "
                f"`alembic upgrade head`: {offenders}"
            )

    def test_compose_waits_for_postgres_to_be_healthy(self):
        """`condition: service_started` only waits for the container to
        exist. PostgreSQL inside it is still starting, which is what
        produced ConnectionRefusedError on ('db', 5432)."""
        for name in self.COMPOSE_FILES:
            body = (BACKEND_ROOT / name).read_text(encoding="utf-8")
            assert "condition: service_healthy" in body, (
                f"{name}: app must depend on db with condition: service_healthy"
            )
            assert "condition: service_started" not in body, (
                f"{name}: service_started does not wait for readiness"
            )
            # The pre-fix shorthand list form carries no condition at all.
            assert "\n      - db\n" not in body, (
                f"{name}: `depends_on: [- db]` shorthand cannot express readiness"
            )

    def test_postgres_has_a_tcp_healthcheck(self):
        for name in self.COMPOSE_FILES:
            body = (BACKEND_ROOT / name).read_text(encoding="utf-8")
            assert "pg_isready" in body, f"{name}: db needs a pg_isready healthcheck"
            check = next(l for l in body.splitlines() if "pg_isready" in l and "test:" in l)
            # A socket-only check passes during initdb, when the temporary
            # server accepts no TCP connections - exactly the window the
            # healthcheck is supposed to cover.
            assert "-h 127.0.0.1" in check, (
                f"{name}: pg_isready must check over TCP (-h 127.0.0.1), not the "
                f"unix socket: {check.strip()}"
            )

    def test_redis_dependency_is_retained(self):
        for name in self.COMPOSE_FILES:
            body = (BACKEND_ROOT / name).read_text(encoding="utf-8")
            assert "redis:" in body, f"{name}: redis service missing"
            assert "redis-cli" in body, f"{name}: redis needs a healthcheck"


# ==========================================================================
# The chain must be able to build an empty database
# ==========================================================================
class TestEnumTypesAreCreatedExactlyOnce:
    """Regression guard for the 2026-07-31 fresh-database failure.

    `alembic upgrade head` aborted on an empty PostgreSQL with
    `DuplicateObjectError: type "direction" already exists`, because the
    baseline both created the type explicitly AND used a generic
    `sa.Enum(..., create_type=False)` in `create_table`. `create_type` is a
    PostgreSQL-dialect keyword; the generic `sa.Enum` silently accepts and
    discards it, so `CREATE TYPE` was emitted a second time.

    Nothing about that is visible from column names or the revision graph -
    which is exactly why the earlier static verification passed a migration
    that could not run. These tests inspect the TYPE OBJECTS.
    """

    VERSIONS = BACKEND_ROOT / "alembic" / "versions"

    def _enum_calls_in_create_table(self, tree):
        """Every `X.Enum(...)` / `X.ENUM(...)` appearing inside a
        `create_table()` call, with the qualifier it was called on."""
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "create_table":
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if inner.func.attr in ("Enum", "ENUM"):
                        qualifier = getattr(inner.func.value, "id", "?")
                        found.append((qualifier, inner))
        return found

    def test_no_generic_sa_enum_is_used_inside_create_table(self):
        violations = []
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for qualifier, call in self._enum_calls_in_create_table(tree):
                if (qualifier, call.func.attr) != ("postgresql", "ENUM"):
                    name = next(
                        (kw.value.value for kw in call.keywords
                         if kw.arg == "name" and isinstance(kw.value, ast.Constant)),
                        "?",
                    )
                    violations.append(
                        f"{path.name}:{call.lineno} uses {qualifier}.{call.func.attr} "
                        f"for '{name}' inside create_table"
                    )
        assert not violations, (
            "A generic sa.Enum inside create_table emits its own CREATE TYPE. "
            "Use sqlalchemy.dialects.postgresql.ENUM(..., create_type=False) and "
            "create the type once explicitly:\n  " + "\n  ".join(violations)
        )

    def test_create_type_is_never_passed_to_a_generic_sa_enum(self):
        """The silent-discard trap. `sa.Enum(..., create_type=False)` looks
        correct, raises nothing, and does nothing."""
        violations = []
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr != "Enum":
                    continue
                if any(kw.arg == "create_type" for kw in node.keywords):
                    violations.append(f"{path.name}:{node.lineno}")
        assert not violations, (
            "create_type is a postgresql.ENUM keyword. sa.Enum accepts it and "
            "ignores it, which is how the fresh-database migration broke:\n  "
            + "\n  ".join(violations)
        )

    def test_every_enum_used_by_a_column_declares_create_type_false(self):
        """Module-level enum objects referenced by columns must suppress
        automatic creation, or CREATE TYPE is emitted twice."""
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if (getattr(node.func.value, "id", None), node.func.attr) != ("postgresql", "ENUM"):
                    continue
                # An ENUM built at module level and bound to a name is a
                # column type; one built inline inside _create_enum_types()
                # is the explicit creator and must NOT suppress creation.
                parent_is_module_assign = any(
                    isinstance(stmt, ast.Assign) and node in ast.walk(stmt)
                    for stmt in tree.body
                )
                if not parent_is_module_assign:
                    continue
                declared = next(
                    (kw.value for kw in node.keywords if kw.arg == "create_type"), None
                )
                assert isinstance(declared, ast.Constant) and declared.value is False, (
                    f"{path.name}:{node.lineno}: an ENUM used as a column type must "
                    f"declare create_type=False"
                )

    def test_each_enum_type_is_explicitly_created_at_most_once(self):
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            created = []
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Call)
                ):
                    name = next(
                        (kw.value.value for kw in node.func.value.keywords
                         if kw.arg == "name" and isinstance(kw.value, ast.Constant)),
                        None,
                    )
                    if name:
                        created.append(name)
            assert len(created) == len(set(created)), (
                f"{path.name}: enum type created more than once: {created}"
            )

    def test_enum_creation_uses_checkfirst(self):
        """Without checkfirst, re-running against a database that already
        has the types fails - which includes every existing deployment."""
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Call)
                    and getattr(node.func.value.func, "attr", None) in ("Enum", "ENUM")
                ):
                    checkfirst = next(
                        (kw.value for kw in node.keywords if kw.arg == "checkfirst"), None
                    )
                    assert isinstance(checkfirst, ast.Constant) and checkfirst.value is True, (
                        f"{path.name}:{node.lineno}: enum .create() must pass checkfirst=True"
                    )


class TestNullabilityMatchesTheModels:
    """`alembic check` must report no drift. It reported twelve
    `modify_nullable` operations against a database the chain had just
    built, because of one asymmetry:

        sa.Column(...)  defaults to nullable=TRUE
        Mapped[X]       defaults to nullable=FALSE

    The defaults are OPPOSITE, so any column transcribed from a model
    without an explicit `nullable=` silently inverts its constraint. The
    column is present, correctly named and correctly typed - and wrong.

    The models are authoritative here. `create_all()` built every existing
    database, including production, directly from them; the migrations are
    the newcomer and were the thing that disagreed.
    """

    VERSIONS = BACKEND_ROOT / "alembic" / "versions"
    MODELS = BACKEND_ROOT / "app" / "models"
    # From TimestampMixin: Mapped[datetime], NOT Optional -> NOT NULL.
    MIXIN = {"id": False, "created_at": False, "updated_at": False}

    @staticmethod
    def _literal_kw(call, name):
        for kw in call.keywords:
            if kw.arg == name and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return None

    def _model_tables(self):
        out = {}
        for path in sorted(self.MODELS.glob("*.py")):
            if path.name in ("__init__.py", "base.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                table, cols = None, dict(self.MIXIN)
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and getattr(stmt.targets[0], "id", "") == "__tablename__"
                        and isinstance(stmt.value, ast.Constant)
                    ):
                        table = stmt.value.value
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        call = stmt.value
                        if not (isinstance(call, ast.Call)
                                and getattr(call.func, "id", "") == "mapped_column"):
                            continue
                        if self._literal_kw(call, "primary_key") is True:
                            cols[stmt.target.id] = False
                            continue
                        declared = self._literal_kw(call, "nullable")
                        if declared is not None:
                            cols[stmt.target.id] = bool(declared)
                        else:
                            annotation = ast.unparse(stmt.annotation)
                            cols[stmt.target.id] = (
                                "None" in annotation or "Optional" in annotation
                            )
                if table:
                    out[table] = cols
        return out

    def _migration_tables(self):
        out = {}
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            upgrade = next(
                (n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "upgrade"),
                None,
            )
            if upgrade is None:
                continue
            for node in ast.walk(upgrade):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                table = node.args[0].value

                if node.func.attr == "create_table":
                    cols = {}
                    for arg in node.args[1:]:
                        if not (isinstance(arg, ast.Call)
                                and getattr(arg.func, "attr", "") == "Column"):
                            continue
                        name = arg.args[0].value
                        if self._literal_kw(arg, "primary_key") is True:
                            cols[name] = False
                            continue
                        declared = self._literal_kw(arg, "nullable")
                        # ABSENT means nullable=True - sa.Column's default.
                        cols[name] = True if declared is None else bool(declared)
                    out[table] = cols

                elif node.func.attr == "add_column" and len(node.args) > 1:
                    spec = node.args[1]
                    if isinstance(spec, ast.Call) and getattr(spec.func, "attr", "") == "Column":
                        declared = self._literal_kw(spec, "nullable")
                        out.setdefault(table, {})[spec.args[0].value] = (
                            True if declared is None else bool(declared)
                        )

                elif node.func.attr == "alter_column" and len(node.args) > 1:
                    declared = self._literal_kw(node, "nullable")
                    if declared is not None and isinstance(node.args[1], ast.Constant):
                        out.setdefault(table, {})[node.args[1].value] = bool(declared)
        return out

    def test_the_comparison_actually_found_both_sides(self):
        models, migrations = self._model_tables(), self._migration_tables()
        shared = set(models) & set(migrations)
        assert len(shared) >= 4, f"expected the four live tables, compared {sorted(shared)}"

    def test_no_column_disagrees_on_nullability(self):
        models, migrations = self._model_tables(), self._migration_tables()
        drift = []
        for table in sorted(set(models) & set(migrations)):
            for column in sorted(set(models[table]) & set(migrations[table])):
                want, got = models[table][column], migrations[table][column]
                if want != got:
                    drift.append(
                        f"{table}.{column}: model="
                        f"{'NULL' if want else 'NOT NULL'}, migration="
                        f"{'NULL' if got else 'NOT NULL'}"
                    )
        assert not drift, (
            "`alembic check` will report modify_nullable for each of these:\n  "
            + "\n  ".join(drift)
        )

    def test_every_created_column_states_nullable_explicitly(self):
        """Relying on `sa.Column`'s default is how the drift happened. An
        explicit `nullable=` on every column makes the intent reviewable
        instead of inferred."""
        implicit = []
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "create_table"):
                    continue
                for arg in node.args[1:]:
                    if not (isinstance(arg, ast.Call)
                            and getattr(arg.func, "attr", "") == "Column"):
                        continue
                    if self._literal_kw(arg, "primary_key") is True:
                        continue
                    if self._literal_kw(arg, "nullable") is None:
                        implicit.append(f"{path.name}:{arg.lineno} {arg.args[0].value}")
        assert not implicit, (
            "these columns inherit sa.Column's nullable=True default instead of "
            "declaring it:\n  " + "\n  ".join(implicit)
        )

    def test_not_null_timestamp_columns_have_a_server_default(self):
        """The trap that makes this fix dangerous if done carelessly.

        `created_at`/`updated_at` carry `server_default=func.now()` in the
        model, so SQLAlchemy OMITS them from every INSERT and expects the
        database to fill them. Marking them NOT NULL without a matching
        database default turns every insert into a NotNullViolation - and
        for risk_assessments those writes are deliberately best-effort, so
        the failure would be swallowed and the audit trail would silently
        stay empty.
        """
        offenders = []
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "create_table"):
                    continue
                for arg in node.args[1:]:
                    if not (isinstance(arg, ast.Call)
                            and getattr(arg.func, "attr", "") == "Column"):
                        continue
                    name = arg.args[0].value
                    if name not in ("created_at", "updated_at"):
                        continue
                    if self._literal_kw(arg, "nullable") is not False:
                        continue
                    has_default = any(kw.arg == "server_default" for kw in arg.keywords)
                    if not has_default:
                        offenders.append(f"{path.name}:{arg.lineno} {node.args[0].value}.{name}")
        assert not offenders, (
            "NOT NULL without a server_default, on columns the ORM does not send:\n  "
            + "\n  ".join(offenders)
        )


class TestMigrationChain:
    VERSIONS = BACKEND_ROOT / "alembic" / "versions"

    def _revisions(self):
        revs = {}
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rev = down = None
            for node in tree.body:
                target = None
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    target = node.target.id
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target = node.targets[0].id
                if target in ("revision", "down_revision") and isinstance(node.value, ast.Constant):
                    if target == "revision":
                        rev = node.value.value
                    else:
                        down = node.value.value
            assert rev, f"{path.name}: no revision id"
            revs[rev] = down
        return revs

    def test_chain_is_linear_with_one_base_and_one_head(self):
        revs = self._revisions()
        assert len(revs) >= 3
        bases = [r for r, d in revs.items() if d is None]
        assert len(bases) == 1, f"expected exactly one base revision, found {bases}"
        parents = [d for d in revs.values() if d is not None]
        assert len(parents) == len(set(parents)), f"branch point in the chain: {parents}"
        heads = [r for r in revs if r not in set(parents)]
        assert len(heads) == 1, f"expected exactly one head, found {heads}"

    def test_the_expected_chain_order(self):
        revs = self._revisions()
        assert revs.get("20260730_00") is None, "20260730_00 must be the base"
        assert revs.get("20260730_01") == "20260730_00"
        assert revs.get("20260730_02") == "20260730_01"

    def test_no_migration_touches_a_table_no_earlier_migration_created(self):
        """The exact defect that made `alembic upgrade head` impossible on an
        empty database: the chain used to begin with `add_column("signals")`,
        silently relying on create_all() having made the table."""
        revs = self._revisions()
        children = {d: r for r, d in revs.items() if d is not None}
        order, current = [], next(r for r, d in revs.items() if d is None)
        while current:
            order.append(current)
            current = children.get(current)

        by_rev = {}
        for path in sorted(self.VERSIONS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.AnnAssign, ast.Assign)):
                    t = node.target.id if isinstance(node, ast.AnnAssign) else node.targets[0].id
                    if t == "revision" and isinstance(node.value, ast.Constant):
                        by_rev[node.value.value] = tree

        tables, problems = set(), []
        for rev in order:
            upgrade = next(
                (n for n in by_rev[rev].body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "upgrade"),
                None,
            )
            assert upgrade is not None, f"{rev}: no upgrade()"
            for node in ast.walk(upgrade):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if not node.args or not isinstance(node.args[0], ast.Constant):
                        continue
                    name = node.args[0].value
                    if node.func.attr == "create_table":
                        tables.add(name)
                    elif node.func.attr in ("add_column", "alter_column", "drop_column"):
                        if name not in tables:
                            problems.append(f"{rev}: {node.func.attr}('{name}') before any migration creates it")
        assert not problems, (
            "The chain cannot build an empty database:\n  " + "\n  ".join(problems)
        )
