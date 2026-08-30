"""
varco_beanie.migration.cli
============================
Backend-specific ``varco`` CLI extras for the Beanie/MongoDB migrator —
registered via the ``varco.commands`` entry-point group
(``[project.entry-points."varco.commands"] beanie = "varco_beanie.migration.cli:register"``).

Adds two verbs under the ``migrate`` subparser:

- ``varco migrate index -t myapp.db:migrator [--create]`` — ``--check``
  (default) reports index drift; ``--create`` applies missing indexes
  (Plan 006 D5 — opt-in, unsafe on large collections, meant for a
  pre-deploy job).
- ``varco migrate new -t myapp.db:migrator --name my_migration`` — scaffold
  a new ``Migration`` file with a timestamped ``version``.

Thread safety:  N/A — one-shot CLI process.
Async safety:   ✅ Index operations are ``async def``, run via
                   ``asyncio.run()`` at the shared CLI boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

_MIGRATION_TEMPLATE = '''"""{name}"""

from __future__ import annotations

from typing import Any

from varco_beanie.migration.base import Migration


class {class_name}(Migration):
    version = "{version}"
    name = "{name}"

    async def up(self, db: Any) -> None:
        raise NotImplementedError("Fill in the migration body.")

    # async def down(self, db: Any) -> None:
    #     ...  # optional — omit to leave this migration irreversible
'''


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register Beanie-specific verbs under the existing ``migrate`` subparser."""
    migrate_parser = subparsers.choices.get("migrate")
    if migrate_parser is None:
        return

    verb_action = None
    for action in migrate_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verb_action = action
            break
    if verb_action is None:
        return

    index_parser = verb_action.add_parser("index")
    index_parser.add_argument("-t", "--target", required=True)
    index_parser.add_argument("--create", action="store_true")
    index_parser.set_defaults(_run=_run_index)

    new_parser = verb_action.add_parser("new")
    new_parser.add_argument("--name", required=True)
    new_parser.add_argument("--out", default=".")
    new_parser.set_defaults(_run=_run_new)


def _run_index(args: argparse.Namespace) -> int:
    from varco_core.cli.migrate import _resolve_target

    migrator = _resolve_target(args.target)
    if migrator is None:
        print(f"Could not resolve migration target {args.target!r}.", file=sys.stderr)
        return 2

    async def _do() -> int:
        index_guard = getattr(migrator, "_index_guard", None)
        if index_guard is None:
            print("Target migrator has no index_guard configured.", file=sys.stderr)
            return 2

        from varco_beanie.migration.indexes import IndexReconciler

        reconciler = IndexReconciler(index_guard, migrator._db)  # type: ignore[attr-defined]
        if args.create:
            drift = await reconciler.apply()
        else:
            drift = await reconciler.report()
        print(drift.format())
        return 1 if drift.has_drift and not args.create else 0

    return asyncio.run(_do())


def _run_new(args: argparse.Namespace) -> int:
    version = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    class_name = "".join(part.capitalize() for part in args.name.split("_"))
    content = _MIGRATION_TEMPLATE.format(name=args.name, class_name=class_name, version=version)
    from pathlib import Path

    out_path = Path(args.out) / f"{version}_{args.name}.py"
    out_path.write_text(content)
    print(f"Wrote {out_path}")
    return 0


__all__ = ["register"]
