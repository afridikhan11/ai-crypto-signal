#!/usr/bin/env python3
"""
Run a SLICE of the offline test suite, using the canonical harness logic.

The full offline harness run exceeds this sandbox's 45-second command
limit, and the older split runner (`run_half.py`) cannot execute async
tests or inject `@pytest.fixture` arguments, which produced false
failures. This wrapper reuses the canonical runner's collection and
execution functions verbatim - so async tests, fixtures and setup_method
all behave identically - and simply restricts which test FILES are run.

Usage:
    python3 offline_test_slice.py <harness_dir> <repo_root> <start> <end>
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from pathlib import Path


def main(argv) -> int:
    harness_dir, repo_root = Path(argv[1]), Path(argv[2])
    start, end = int(argv[3]), int(argv[4])

    sys.path.insert(0, str(harness_dir / "stubs"))
    sys.path.insert(1, str(repo_root))

    spec = importlib.util.spec_from_file_location("_runner", harness_dir / "run_tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)  # safe: guarded by __name__ == "__main__"

    import pytest as _pytest_stub
    runner.Skipped = _pytest_stub.Skipped

    test_files = sorted((repo_root / "tests").glob("test_*.py"))[start:end]

    totals = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    problems, import_errors = [], []

    for test_file in test_files:
        modname = f"tests.{test_file.stem}"
        sys.modules.pop(modname, None)
        try:
            module = importlib.import_module(modname)
        except Exception:
            import_errors.append((test_file.name, traceback.format_exc()))
            continue

        for test_id, status, detail in runner._collect_and_run(module):
            totals[status] += 1
            if status in ("failed", "error"):
                last = detail.strip().splitlines()[-1] if detail else "?"
                problems.append(f"{test_file.name}::{test_id} :: {last}")

    print(
        f"SLICE[{start}:{end}] files={len(test_files)} "
        f"passed={totals['passed']} failed={totals['failed']} "
        f"error={totals['error']} skipped={totals['skipped']} "
        f"import_errors={len(import_errors)}"
    )
    for name, tb in import_errors:
        print(f"  IMPORT ERROR {name}: {tb.strip().splitlines()[-1]}")
    for p in problems:
        print(f"  {p}")
    return 1 if (totals["failed"] or totals["error"] or import_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
