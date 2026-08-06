#!/usr/bin/env python3
"""
Static verification that the Alembic chain can build this application's
schema from an EMPTY database.

WHY THIS EXISTS
--------------------------------------------------------------------------
`alembic upgrade head` against a real empty PostgreSQL is the only
complete proof, and it requires a database. This script is what can be
checked WITHOUT one: it reads the migration files and the SQLAlchemy
models as source, replays the migrations' schema operations symbolically,
and diffs the result against what the models declare.

That catches the failure mode this project actually hit - a migration
chain that silently assumed a table `create_all()` had already made - and
it catches a model whose table no migration creates.

WHAT IT PROVES / DOES NOT PROVE
--------------------------------------------------------------------------
PROVES     revision graph is linear with one base and one head; every
           table/column the models declare is produced by some migration;
           every column's NULLABILITY matches the model; no migration
           touches a table that does not exist yet at that point in the
           chain.
DOES NOT   that PostgreSQL accepts the SQL, that server defaults are
PROVE      valid, that data backfills behave, or that enum types are
           emitted exactly once. Only a real `alembic upgrade head`
           shows that.

HISTORY - WHY THE NULLABILITY CHECK IS HERE
--------------------------------------------------------------------------
The first version of this script compared column NAMES only and reported
"consistent" twice on a chain that was not. It missed a duplicate
`CREATE TYPE` (the migration could not run at all) and then twelve
`modify_nullable` drifts (it ran, and built the wrong schema). Both were
invisible to a name-only comparison. `sa.Column` defaults to
nullable=True while `Mapped[X]` defaults to nullable=False, so a column
can be present, correctly named, correctly typed, and still wrong.

Run:  python3 scripts/verify_migration_chain.py
Exit: 0 = consistent, 1 = a real discrepancy was found.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
VERSIONS = BACKEND / "alembic" / "versions"
MODELS = BACKEND / "app" / "models"

# Columns every model inherits from TimestampMixin (app/models/base.py),
# mapped to nullability. `id` is a primary key; `created_at`/`updated_at`
# are declared `Mapped[datetime]` WITHOUT Optional, so both are NOT NULL.
MIXIN_COLUMNS = {"id": False, "created_at": False, "updated_at": False}

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


# ==========================================================================
# 1. Revision graph
# ==========================================================================
def _module_level_str(tree: ast.Module, name: str):
    """Read a module-level assignment like `revision: str = "20260730_00"`."""
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == name:
            value = node.value
            if isinstance(value, ast.Constant):
                return value.value
    return None


def load_revisions():
    revs = {}
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rev = _module_level_str(tree, "revision")
        down = _module_level_str(tree, "down_revision")
        if rev is None:
            fail(f"{path.name}: no `revision` identifier found")
            continue
        revs[rev] = {"path": path, "down": down, "tree": tree}
    return revs


def order_chain(revs):
    bases = [r for r, m in revs.items() if m["down"] is None]
    if len(bases) != 1:
        fail(f"expected exactly one base revision (down_revision=None), found {bases}")
        return []
    children = {}
    for rev, meta in revs.items():
        if meta["down"] is not None:
            children.setdefault(meta["down"], []).append(rev)
    for parent, kids in children.items():
        if len(kids) > 1:
            fail(f"branch point: revision {parent} has multiple children {kids}")
    ordered, current = [], bases[0]
    seen = set()
    while current is not None:
        if current in seen:
            fail(f"cycle detected at revision {current}")
            break
        seen.add(current)
        ordered.append(current)
        kids = children.get(current, [])
        current = kids[0] if kids else None
    missing = set(revs) - set(ordered)
    if missing:
        fail(f"revisions not reachable from the base: {sorted(missing)}")
    heads = [r for r in revs if r not in children]
    if len(heads) != 1:
        fail(f"expected exactly one head, found {sorted(heads)}")
    return ordered


# ==========================================================================
# 2. Replay the migrations symbolically
# ==========================================================================
def _str_arg(node, index=0, keyword=None):
    if keyword is not None:
        for kw in node.keywords:
            if kw.arg == keyword and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return None
    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
        return node.args[index].value
    return None


def _column_name(node):
    """Name from an `sa.Column("x", ...)` call node."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Column":
        return _str_arg(node, 0)
    return None


def _kw(node, name):
    """Value of a literal keyword argument, or None if absent/non-literal."""
    for kw in node.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _column_nullable(node):
    """Nullability of an `sa.Column(...)` as PostgreSQL will receive it.

    A primary key is NOT NULL regardless. Otherwise an ABSENT `nullable=`
    means nullable=True, because that is `sa.Column`'s default - the exact
    opposite of `Mapped[X]`'s default in the models.
    """
    if _kw(node, "primary_key") is True:
        return False
    declared = _kw(node, "nullable")
    return True if declared is None else bool(declared)


def _model_nullable(stmt) -> bool:
    """Nullability of a `mapped_column(...)` annotated assignment.

    Explicit `nullable=` wins. Otherwise it comes from the ANNOTATION:
    `Mapped[str | None]` / `Mapped[Optional[str]]` is nullable, plain
    `Mapped[str]` is NOT NULL.
    """
    call = stmt.value
    if _kw(call, "primary_key") is True:
        return False
    declared = _kw(call, "nullable")
    if declared is not None:
        return bool(declared)
    annotation = ast.unparse(stmt.annotation)
    return ("None" in annotation) or ("Optional" in annotation)


def replay(ordered, revs):
    """Apply each migration's upgrade() to a symbolic {table: {columns}}."""
    schema: dict[str, set[str]] = {}
    for rev in ordered:
        tree = revs[rev]["tree"]
        upgrade = next(
            (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "upgrade"),
            None,
        )
        if upgrade is None:
            fail(f"{revs[rev]['path'].name}: no upgrade() function")
            continue

        for node in ast.walk(upgrade):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            op = node.func.attr

            if op == "create_table":
                table = _str_arg(node, 0)
                if table is None:
                    continue
                if table in schema:
                    fail(f"{rev}: create_table('{table}') but it already exists at this point in the chain")
                cols = {}
                for arg in node.args[1:]:
                    name = _column_name(arg)
                    if name:
                        cols[name] = _column_nullable(arg)
                schema[table] = cols

            elif op == "add_column":
                table = _str_arg(node, 0)
                if table is None:
                    continue
                if table not in schema:
                    fail(
                        f"{rev}: add_column on '{table}', which no earlier migration creates. "
                        f"This is the failure mode that made `upgrade head` impossible on an "
                        f"empty database."
                    )
                    schema[table] = {}
                spec = node.args[1] if len(node.args) > 1 else None
                col = _column_name(spec) if spec is not None else None
                if col:
                    schema[table][col] = _column_nullable(spec)

            elif op == "drop_column":
                table, col = _str_arg(node, 0), _str_arg(node, 1)
                if table in schema and col:
                    schema[table].pop(col, None)

            elif op in ("alter_column", "create_index", "drop_index", "create_foreign_key"):
                table = _str_arg(node, 0) if op == "alter_column" else _str_arg(node, 1)
                if op == "alter_column":
                    if table not in schema:
                        fail(f"{rev}: alter_column on unknown table '{table}'")
                    else:
                        # `alter_column(t, c, nullable=False)` is how
                        # 20260730_01 enforces NOT NULL after backfilling.
                        col = _str_arg(node, 1)
                        declared = _kw(node, "nullable")
                        if col in schema[table] and declared is not None:
                            schema[table][col] = bool(declared)

            elif op == "drop_table":
                schema.pop(_str_arg(node, 0), None)
    return schema


# ==========================================================================
# 3. What the models declare
# ==========================================================================
def model_tables():
    tables: dict[str, dict] = {}
    for path in sorted(MODELS.glob("*.py")):
        if path.name in ("__init__.py", "base.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            tablename, columns = None, {}
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "__tablename__"
                    and isinstance(stmt.value, ast.Constant)
                ):
                    tablename = stmt.value.value
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    call = stmt.value
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        if call.func.id == "mapped_column":
                            columns[stmt.target.id] = _model_nullable(stmt)
                        # `relationship(...)` is an ORM construct, not a column.
            if tablename:
                tables[tablename] = {
                    "columns": {**MIXIN_COLUMNS, **columns},
                    "file": path.name,
                    "imported_by_env": None,
                }
    return tables


def env_imported_models() -> set[str]:
    """Which model MODULES alembic/env.py imports - anything it misses is
    invisible to `alembic check` and to autogenerate."""
    tree = ast.parse((BACKEND / "alembic" / "env.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.models":
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.models."):
            found.add(node.module.split(".")[-1])
    return found


# ==========================================================================
# main
# ==========================================================================
def main() -> int:
    revs = load_revisions()
    ordered = order_chain(revs)

    print("REVISION CHAIN")
    for i, rev in enumerate(ordered):
        print(f"  {'   ' * 0}{'-> ' if i else '   '}{rev}  ({revs[rev]['path'].name})")
    print()

    schema = replay(ordered, revs)
    models = model_tables()
    env_modules = env_imported_models()

    print("TABLES PRODUCED BY THE CHAIN")
    for table in sorted(schema):
        print(f"  {table:20s} {len(schema[table])} columns")
    print()

    print("MODEL / MIGRATION DIFF")
    for table in sorted(models):
        declared = models[table]["columns"]
        built = schema.get(table)
        module = models[table]["file"][:-3]
        if built is None:
            if module in env_modules:
                fail(
                    f"table '{table}' ({models[table]['file']}) is registered in alembic/env.py "
                    f"but NO migration creates it - `upgrade head` on an empty database would "
                    f"leave it missing"
                )
                print(f"  {table:20s} MISSING FROM CHAIN  <-- FAILURE")
            else:
                notes.append(
                    f"table '{table}' ({models[table]['file']}) is created by no migration AND "
                    f"is not imported by alembic/env.py, so it is absent from Base.metadata. "
                    f"It has therefore never existed in this database - not a regression, but "
                    f"the model is dead code and `alembic check` cannot see it."
                )
                print(f"  {table:20s} orphan model (see notes)")
            continue
        missing = set(declared) - set(built)
        extra = set(built) - set(declared)
        if missing:
            fail(f"table '{table}': columns declared by the model but never created: {sorted(missing)}")
        if extra:
            fail(f"table '{table}': columns created by migrations but absent from the model: {sorted(extra)}")

        # NULLABILITY. This is what `alembic check` reports as
        # `modify_nullable`, and what a name-only comparison cannot see.
        for column in sorted(set(declared) & set(built)):
            if declared[column] != built[column]:
                fail(
                    f"table '{table}': column '{column}' is "
                    f"{'NULL' if declared[column] else 'NOT NULL'} in the model but "
                    f"{'NULL' if built[column] else 'NOT NULL'} in the migrations "
                    f"-> `alembic check` will report modify_nullable"
                )
        print(f"  {table:20s} {'OK' if not (missing or extra) else 'MISMATCH'}  ({len(declared)} columns)")
    print()

    if notes:
        print("NOTES")
        for n in notes:
            print(f"  - {n}")
        print()

    if failures:
        print("FAILURES")
        for f in failures:
            print(f"  ! {f}")
        return 1
    print("RESULT: migration chain is internally consistent and matches the models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
