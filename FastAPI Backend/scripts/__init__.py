"""Operational scripts that are run, not imported by the application.

This file exists so `scripts` is a REGULAR package rather than an implicit
namespace package, which makes `python -m scripts.wait_for_db` (used by
scripts/docker-entrypoint.sh) resolve deterministically instead of
depending on namespace-package path scanning.

Nothing in `app/` imports from here, and nothing here is part of the
running application.
"""
