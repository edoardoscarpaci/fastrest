"""
varco_core.cli.migrate
=======================
The ``migrate`` subcommand — the CI / pre-deploy-job path for running
migrations against any ``AbstractMigrator``, resolved via a ``module:callable``
target string, mirroring ``uvicorn app:app`` and Alembic's ``env.py``: the
CLI does not invent a second config-discovery mechanism.

```
varco migrate current   -t myapp.db:migrator
varco migrate pending   -t myapp.db:migrator          # exit 1 if pending → CI gate
varco migrate upgrade   -t myapp.db:migrator [--to heads] [--dry-run]
varco migrate downgrade -t myapp.db:migrator --to <rev> --yes
varco migrate stamp     -t myapp.db:migrator [--to heads]
```

Exit codes: ``0`` ok, ``1`` migration/drift error, ``2`` usage error.

Thread safety:  N/A — a one-shot CLI process.
Async safety:   ✅ Resolves the target, then ``asyncio.run()``s the chosen
                   ``AbstractMigrator`` method.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from typing import Any

from varco_core.migration.base import (
    AbstractMigrator,
    MigrationReport,
    SchemaMigrationPlan,
)
from varco_core.migration.errors import SchemaMigrationError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``migrate`` subcommand parser and its verbs."""
    parser = subparsers.add_parser("migrate", help="Run/inspect schema migrations")
    parser.set_defaults(_run=_run)

    verb_parsers = parser.add_subparsers(dest="verb", required=True)

    for verb in (
        "current",
        "pending",
        "check",
        "upgrade",
        "downgrade",
        "stamp",
        "adopt",
        "ddl",
    ):
        vp = verb_parsers.add_parser(verb)
        vp.add_argument("-t", "--target", required=True, help="module:callable target")
        vp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
        if verb in ("upgrade", "stamp"):
            vp.add_argument("--to", default="heads", dest="to")
            vp.add_argument("--dry-run", action="store_true", dest="dry_run")
        if verb == "downgrade":
            vp.add_argument("--to", required=True, dest="to")
            vp.add_argument("--yes", action="store_true", dest="yes")
        if verb in ("upgrade", "check"):
            # Plan 007, Phase 9 — tenant fan-out flags. `--target` must
            # resolve to a `varco_core.migration.fanout.TenantFanoutMigrator`
            # for these to have any effect; recognised here so the CLI
            # never errors with "unrecognized arguments" for a fan-out
            # deployment.
            vp.add_argument(
                "--all-tenants",
                action="store_true",
                dest="all_tenants",
                help="Fan out across every active/suspended tenant (global-first).",
            )
            vp.add_argument("--tenant", dest="tenant", default=None, help="Target one tenant only.")
            vp.add_argument(
                "--skip-global",
                action="store_true",
                dest="skip_global",
                help="Explicitly omit the global/framework run (dangerous — see docs).",
            )


def _resolve_target(target: str) -> AbstractMigrator | None:
    """Resolve ``module:callable`` → an ``AbstractMigrator`` instance, or ``None``."""
    if ":" not in target:
        return None
    module_name, _, attr_name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    obj: Any = module
    for part in attr_name.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    if callable(obj) and not isinstance(obj, AbstractMigrator):
        obj = obj()
    if asyncio.iscoroutine(obj):
        obj = asyncio.get_event_loop().run_until_complete(obj)  # pragma: no cover
    return obj if isinstance(obj, AbstractMigrator) else None


def _plan_to_json(plan: SchemaMigrationPlan) -> dict[str, Any]:
    return {
        "current": list(plan.current),
        "pending": [{"id": r.id, "label": r.label, "branch": r.branch} for r in plan.pending],
    }


def _report_to_json(report: MigrationReport) -> dict[str, Any]:
    return {
        "applied": [{"id": r.id, "label": r.label, "branch": r.branch} for r in report.applied],
        "duration_s": report.duration_s,
        "skipped_locked": report.skipped_locked,
    }


async def _run_async(args: argparse.Namespace) -> int:
    migrator = _resolve_target(args.target)
    if migrator is None:
        print(
            f"Could not resolve migration target {args.target!r}. "
            "Expected the 'module:callable' form, e.g. myapp.db:migrator",
            file=sys.stderr,
        )
        return 2

    try:
        if args.verb == "current":
            plan = await migrator.plan()
            if args.json:
                print(json.dumps(_plan_to_json(plan)))
            else:
                print(", ".join(plan.current) or "(none)")
            return 0

        if args.verb == "pending":
            plan = await migrator.plan()
            if args.json:
                print(json.dumps(_plan_to_json(plan)))
            else:
                print(plan.format())
            return 1 if not plan.is_empty else 0

        if args.verb == "check":
            plan = await migrator.check()
            if args.json:
                print(json.dumps(_plan_to_json(plan)))
            return 0

        if args.verb == "upgrade":
            report = await migrator.upgrade(args.to, dry_run=args.dry_run)
            if args.json:
                print(json.dumps(_report_to_json(report)))
            else:
                print(report.format())
            return 0

        if args.verb == "downgrade":
            if not args.yes:
                print(
                    "Refusing to downgrade without --yes — this is a "
                    "deliberate, human-invoked action.",
                    file=sys.stderr,
                )
                return 2
            report = await migrator.downgrade(args.to)
            if args.json:
                print(json.dumps(_report_to_json(report)))
            else:
                print(report.format())
            return 0

        if args.verb == "stamp":
            await migrator.stamp(args.to)
            return 0

        if args.verb == "adopt":
            adopt_fn = getattr(migrator, "adopt_framework_tables", None)
            if adopt_fn is None:
                print(
                    f"{type(migrator).__name__} has no adopt_framework_tables().",
                    file=sys.stderr,
                )
                return 2
            adopted = await adopt_fn()
            if args.json:
                print(json.dumps(list(adopted)))
            else:
                print(f"Adopted {len(adopted)} table(s): {', '.join(adopted) or '(none)'}")
            return 0

        if args.verb == "ddl":
            print(
                "The 'ddl' verb is backend-specific — resolve a "
                "varco_sa.migration.AlembicMigrator target and use "
                "print_create_ddl() directly, or the 'sa' CLI extra.",
                file=sys.stderr,
            )
            return 2

        print(f"Unknown verb: {args.verb!r}", file=sys.stderr)
        return 2
    except SchemaMigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, don't crash
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await migrator.close()


def _run(args: argparse.Namespace) -> int:
    # DESIGN: run in a fresh thread when a loop is already running.
    #   ✅ `varco migrate ...` is normally invoked as a standalone process
    #      (no running loop), where `asyncio.run()` is correct and simplest.
    #   ✅ Tests call `main(argv)` directly from an `async def test_...`
    #      under pytest-asyncio — `asyncio.run()` would raise
    #      "cannot be called from a running event loop" there. A dedicated
    #      thread gets its own loop, sidestepping the conflict without
    #      changing the CLI's synchronous `main(argv) -> int` contract.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run_async(args))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run_async(args))
        return future.result()


__all__ = ["register"]
