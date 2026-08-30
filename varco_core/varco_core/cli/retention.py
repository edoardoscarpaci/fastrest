"""
varco_core.cli.retention
==========================
The ``retention`` subcommand — chunked-sweep pruning for a DLQ or an audit
log, resolved via ``module:callable`` targets (Plan 009, Phase 2 / R3),
mirroring ``varco migrate``'s own resolution convention.

```
varco retention prune --type {dlq,audit} --before <ISO8601> [--limit N]
                      [--chunk 1000] [--dry-run] --target module:factory
```

``--target`` names an importable zero-arg factory returning the
``AbstractDeadLetterQueue`` / ``AuditRepository`` — the CLI cannot know the
app's DI container (same reasoning as ``varco migrate``'s ``-t``).
``--dry-run`` requires ``count()``/``list()`` support and prints the count
without deleting. Default behaviour is the chunked sweep: loop
``delete_where(..., limit=chunk)`` until it returns ``0``.

Exit codes: ``0`` ok, ``1`` the backend refuses (e.g. Kafka/NATS naming their
own retention mechanism), ``2`` usage error.

Thread safety:  N/A — a one-shot CLI process.
Async safety:   ✅ Resolves the target synchronously, then ``asyncio.run()``s
                   the chunked sweep.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from datetime import datetime
from typing import Any


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``retention`` subcommand parser and its verbs."""
    parser = subparsers.add_parser(
        "retention", help="Prune DLQ / audit-log entries (retention sweep)"
    )
    parser.set_defaults(_run=_run)

    verb_parsers = parser.add_subparsers(dest="verb", required=True)

    prune_p = verb_parsers.add_parser("prune")
    prune_p.add_argument("--type", required=True, choices=["dlq", "audit"])
    prune_p.add_argument("--before", required=True, help="ISO8601 cutoff")
    prune_p.add_argument("--limit", type=int, default=None)
    prune_p.add_argument("--chunk", type=int, default=1000)
    prune_p.add_argument("--dry-run", action="store_true", dest="dry_run")
    prune_p.add_argument(
        "-t",
        "--target",
        default=os.environ.get("VARCO_RETENTION_TARGET"),
        required=os.environ.get("VARCO_RETENTION_TARGET") is None,
        help="module:callable target (env fallback: VARCO_RETENTION_TARGET)",
    )


def _resolve(target: str) -> Any:
    """Resolve ``module:callable`` → an instance, calling zero-arg callables."""
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
    if callable(obj):
        obj = obj()
    return obj


async def _run_prune(args: argparse.Namespace, target: Any) -> int:
    cutoff = datetime.fromisoformat(args.before)

    if args.dry_run:
        try:
            count = await target.count()
        except (NotImplementedError, AttributeError):
            print("dry-run requires count() support on this target.", file=sys.stderr)
            return 1
        print(f"Would prune (dry-run) — current total count: {count}. Nothing deleted.")
        return 0

    total = 0
    try:
        while True:
            deleted = await target.delete_where(older_than=cutoff, limit=args.limit or args.chunk)
            total += deleted
            if deleted == 0 or args.limit is not None:
                break
    except NotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Pruned {total} entr{'y' if total == 1 else 'ies'}.")
    return 0


def _run(args: argparse.Namespace) -> int:
    """
    Resolve the ``-t module:factory`` target synchronously first, then
    dispatch the async body — see ``varco_core.cli.dlq._run``'s DESIGN block
    for why resolution must not happen inside our own coroutine.
    """
    try:
        asyncio.get_running_loop()
        loop_running = True
    except RuntimeError:
        loop_running = False

    if not loop_running:
        return _resolve_and_dispatch(args)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_resolve_and_dispatch, args)
        return future.result()


def _resolve_and_dispatch(args: argparse.Namespace) -> int:
    """Runs on a thread with no event loop yet — safe to resolve the target."""
    target = _resolve(args.target)
    if target is None:
        print(f"Could not resolve retention target {args.target!r}.", file=sys.stderr)
        return 2
    return asyncio.run(_run_prune(args, target))


__all__ = ["register"]
