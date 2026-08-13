"""
varco_sa.migration.cli
========================
Backend-specific ``varco`` CLI extras for the SQLAlchemy/Alembic migrator —
registered via the ``varco.commands`` entry-point group
(``[project.entry-points."varco.commands"] sa = "varco_sa.migration.cli:register"``).

Adds two verbs under the ``migrate`` subparser:

- ``varco migrate revision -t myapp.db:migrator [--autogenerate] -m "message"``
  — delegates to ``alembic.command.revision`` with ``include_object``
  pre-wired to filter out framework-owned tables.
- ``varco migrate heads -t myapp.db:migrator`` — lists current heads across
  both the app and ``varco`` branches (a reminder that ``upgrade heads``
  is plural — see Plan 006's Risks section).

Only genuinely SA-specific verbs live here — the shared verbs
(``current``/``pending``/``upgrade``/``downgrade``/``stamp``/``adopt``) stay
in ``varco_core.cli.migrate`` since any ``AbstractMigrator`` satisfies them.

Thread safety:  N/A — one-shot CLI process.
Async safety:   N/A — Alembic's revision generation is synchronous.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def register(subparsers: argparse._SubParsersAction) -> None:
    """
    Register SA-specific verbs under the existing ``migrate`` subparser.

    Args:
        subparsers: The root parser's subparsers action (the same object
                    ``varco_core.cli.migrate.register`` added ``migrate`` to).
    """
    # Find the already-registered "migrate" subparser to attach onto.
    migrate_parser = None
    for choice, sp in subparsers.choices.items():
        if choice == "migrate":
            migrate_parser = sp
            break
    if migrate_parser is None:
        return

    # argparse does not expose the child subparsers action directly — reuse
    # it via the private _subparsers_action attribute set by add_subparsers.
    verb_action = None
    for action in migrate_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verb_action = action
            break
    if verb_action is None:
        return

    revision_parser = verb_action.add_parser("revision")
    revision_parser.add_argument("-t", "--target", required=True)
    revision_parser.add_argument("-m", "--message", default=None)
    revision_parser.add_argument("--autogenerate", action="store_true")
    revision_parser.set_defaults(_run=_run_revision)

    heads_parser = verb_action.add_parser("heads")
    heads_parser.add_argument("-t", "--target", required=True)
    heads_parser.set_defaults(_run=_run_heads)


def _resolve_migrator(target: str) -> Any:
    from varco_core.cli.migrate import _resolve_target

    return _resolve_target(target)


def _run_revision(args: argparse.Namespace) -> int:
    migrator = _resolve_migrator(args.target)
    if migrator is None:
        print(f"Could not resolve migration target {args.target!r}.", file=sys.stderr)
        return 2

    config, _script = migrator._build_config()
    from alembic import command

    command.revision(
        config,
        message=args.message,
        autogenerate=args.autogenerate,
        process_revision_directives=None,
    )
    return 0


def _run_heads(args: argparse.Namespace) -> int:
    migrator = _resolve_migrator(args.target)
    if migrator is None:
        print(f"Could not resolve migration target {args.target!r}.", file=sys.stderr)
        return 2

    _config, script = migrator._build_config()
    for head in script.get_heads():
        print(head)
    return 0


__all__ = ["register"]
