"""
varco_core.cli.dlq
====================
The ``dlq`` subcommand — operator-triggered DLQ browse/redrive/purge, resolved
via ``module:callable`` target strings (Plan 009, Phase 4 / R1), mirroring
``varco migrate``'s own resolution convention.

```
varco dlq list    -t myapp.dlq:factory [--channel C] [--source S] [--limit N]
varco dlq redrive -t myapp.dlq:factory -b myapp.bus:factory
                  (--entry-id UUID | --batch [--limit N] [--channel C] [--source S])
                  [--dry-run]
varco dlq purge   -t myapp.dlq:factory --before ISO8601 [--limit N]   # → R3, see cli/retention.py
```

Exit codes: ``0`` ok, ``1`` any redrive/purge failure occurred, ``2`` usage error.

Thread safety:  N/A — a one-shot CLI process.
Async safety:   ✅ Resolves targets synchronously, then ``asyncio.run()``s the
                   chosen operation.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from datetime import datetime
from typing import Any
from uuid import UUID

from varco_core.event.dlq import AbstractDeadLetterQueue, DeadLetterSource
from varco_core.event.redrive import DeadLetterNotAddressable, DlqRedriver


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``dlq`` subcommand parser and its verbs."""
    parser = subparsers.add_parser("dlq", help="Browse, redrive, or purge a dead letter queue")
    parser.set_defaults(_run=_run)

    verb_parsers = parser.add_subparsers(dest="verb", required=True)

    list_p = verb_parsers.add_parser("list")
    list_p.add_argument(
        "-t",
        "--target",
        required=True,
        help="module:callable → AbstractDeadLetterQueue",
    )
    list_p.add_argument("--channel", default=None)
    list_p.add_argument("--source", default=None, choices=[s.value for s in DeadLetterSource])
    list_p.add_argument("--limit", type=int, default=50)

    redrive_p = verb_parsers.add_parser("redrive")
    redrive_p.add_argument(
        "-t",
        "--target",
        required=True,
        help="module:callable → AbstractDeadLetterQueue",
    )
    redrive_p.add_argument("-b", "--bus", required=True, help="module:callable → AbstractEventBus")
    redrive_p.add_argument("--entry-id", default=None)
    redrive_p.add_argument("--batch", action="store_true")
    redrive_p.add_argument("--limit", type=int, default=10)
    redrive_p.add_argument("--channel", default=None)
    redrive_p.add_argument("--source", default=None, choices=[s.value for s in DeadLetterSource])
    redrive_p.add_argument("--dry-run", action="store_true", dest="dry_run")

    purge_p = verb_parsers.add_parser("purge")
    purge_p.add_argument(
        "-t",
        "--target",
        required=True,
        help="module:callable → AbstractDeadLetterQueue",
    )
    purge_p.add_argument(
        "--before",
        required=True,
        help="ISO8601 cutoff — delete entries older than this",
    )
    purge_p.add_argument("--limit", type=int, default=None)


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
    if callable(obj) and not isinstance(obj, AbstractDeadLetterQueue):
        obj = obj()
    return obj


async def _run_list(args: argparse.Namespace, dlq: Any) -> int:
    source = DeadLetterSource(args.source) if args.source else None
    try:
        entries = await dlq.list_entries(limit=args.limit, channel=args.channel, source=source)
    except NotImplementedError:
        entries = await dlq.pop_batch(limit=args.limit)
    for e in entries:
        print(
            f"{e.entry_id}  channel={e.channel!r}  source={e.source}  error={e.error_type}: {e.error_message}"
        )
    print(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
    return 0


async def _run_redrive(args: argparse.Namespace, dlq: Any, bus: Any) -> int:
    if not args.entry_id and not args.batch:
        print("Specify exactly one of --entry-id or --batch.", file=sys.stderr)
        return 2

    redriver = DlqRedriver(dlq, bus)
    try:
        if args.entry_id:
            outcome = await redriver.redrive(UUID(args.entry_id), dry_run=args.dry_run)
            print(f"{outcome.entry_id}: published={outcome.published} error={outcome.error}")
            return 0 if (outcome.published or args.dry_run) and outcome.error != "not found" else 1
        report = await redriver.redrive_batch(
            limit=args.limit,
            channel=args.channel,
            source=DeadLetterSource(args.source) if args.source else None,
            dry_run=args.dry_run,
        )
        print(f"attempted={report.attempted} succeeded={report.succeeded} failed={report.failed}")
        return 1 if report.failed > 0 else 0
    except DeadLetterNotAddressable as exc:
        print(str(exc), file=sys.stderr)
        return 1


async def _run_purge(args: argparse.Namespace, dlq: Any) -> int:
    cutoff = datetime.fromisoformat(args.before)
    total = 0
    chunk = args.limit or 1000
    try:
        while True:
            deleted = await dlq.delete_where(older_than=cutoff, limit=chunk)
            total += deleted
            if deleted == 0 or args.limit is not None:
                break
    except NotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Purged {total} entr{'y' if total == 1 else 'ies'}.")
    return 0


def _run(args: argparse.Namespace) -> int:
    """
    Resolve every ``module:callable`` target **synchronously** first, then
    dispatch the async body.

    DESIGN: resolve-then-run, not resolve-inside-the-coroutine
        A ``-t module:factory`` target's factory may itself call
        ``asyncio.run()`` (a common pattern for a sync CLI entry point
        wrapping an async constructor — see the test fixtures). If
        resolution happened *inside* a coroutine already driven by our own
        ``asyncio.run()``, that nested call would raise "asyncio.run()
        cannot be called from a running event loop". Resolving first, on
        whichever thread has no loop running yet, avoids the nesting
        entirely — the async body below never re-resolves anything.

    Thread selection mirrors ``varco_core.cli.migrate._run``: if the calling
    thread already has a running loop (tests calling ``main(argv)`` directly
    from an ``async def test_...``), do everything in a fresh worker thread.
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
    """Runs on a thread with no event loop yet — safe to resolve targets."""
    if args.verb == "list":
        dlq = _resolve(args.target)
        if dlq is None:
            print(f"Could not resolve DLQ target {args.target!r}.", file=sys.stderr)
            return 2
        return asyncio.run(_run_list(args, dlq))
    if args.verb == "redrive":
        dlq = _resolve(args.target)
        bus = _resolve(args.bus)
        if dlq is None or bus is None:
            print(
                f"Could not resolve target(s): dlq={args.target!r} bus={args.bus!r}.",
                file=sys.stderr,
            )
            return 2
        return asyncio.run(_run_redrive(args, dlq, bus))
    if args.verb == "purge":
        dlq = _resolve(args.target)
        if dlq is None:
            print(f"Could not resolve DLQ target {args.target!r}.", file=sys.stderr)
            return 2
        return asyncio.run(_run_purge(args, dlq))
    return 2  # pragma: no cover - argparse choices already constrain this


__all__ = ["register"]
