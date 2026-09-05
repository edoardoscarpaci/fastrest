"""
varco_core.cli.asyncapi
========================
The ``export-asyncapi`` subcommand — render an AsyncAPI 3.1.0 document from the
app's *live, wired* consumers, and (with ``--check``) diff it against a committed
snapshot (Plan 022 §D-AA4, Plan 030 / Phase 2; name reserved by RS-3).

```
varco export-asyncapi --title "Orders" --version 1.0.0
                      [--path DIR]...
                      (--consumer module:Class | --source module:callable)...
                      [--protocol kafka|nats|redis] [--group-id G] [--queue-group Q]
                      [--server name=protocol://host]...
                      [--output FILE | --check]
```

Exit codes: ``0`` ok, ``1`` snapshot drift or a resolution failure, ``2`` usage
error.

DESIGN: ``--check`` diffs a committed snapshot, exactly like ``api_surface.py``
    ✅ One established shape for "a generated artifact must not silently drift",
       already understood by every contributor here.
    ✅ Runs inside ``make lint``'s no-``PKG`` path — no new CI job (§D-AA4).
    ❌ A contributor who changes the example app's wiring must regenerate and
       commit.  Bounded on purpose: the snapshot is generated from ONE example
       app's consumers, not the whole repo, so it moves only when that app moves.

⚠️ **JSON only, deliberately (v1).**  YAML would need ``pyyaml``, which is *not*
a ``varco_core`` runtime dependency (it is only present in this workspace's dev
environment, via the docs toolchain).  ``varco_core`` takes no new runtime
dependency for an output format — plan 030's Risks table names JSON-only as the
accepted v1, and AsyncAPI tooling reads JSON natively.

Thread safety:  N/A — a one-shot CLI process.
Async safety:   ✅ Fully synchronous: importing, constructing and introspecting
                   consumers involves no ``await``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from varco_core.asyncapi import generate_asyncapi
from varco_core.event.consumer import EventConsumer
from varco_core.event.memory import InMemoryEventBus

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Sequence


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """
    Register the ``export-asyncapi`` subcommand parser.

    Args:
        subparsers: The ``varco`` root parser's subparser action.

    Returns:
        None — the parser is attached to *subparsers* in place.
    """
    parser = subparsers.add_parser(
        "export-asyncapi",
        help="Render an AsyncAPI 3.1.0 document from wired EventConsumers",
    )
    parser.set_defaults(_run=_run)

    parser.add_argument("--title", required=True, help="info.title of the document")
    parser.add_argument("--version", required=True, help="info.version — the APP's version")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="DIR",
        help="Directory to prepend to sys.path before importing (repeatable)",
    )
    parser.add_argument(
        "--consumer",
        action="append",
        default=[],
        metavar="module:Class",
        help="An EventConsumer class to construct and register (repeatable)",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="module:callable",
        help=(
            "A callable returning already-wired consumers or a DIContainer "
            "(repeatable). The honest seam: the app owns its own wiring"
        ),
    )
    parser.add_argument("--protocol", default=None, choices=["kafka", "nats", "redis"])
    parser.add_argument("--group-id", default=None, help="Kafka operation binding groupId")
    parser.add_argument("--queue-group", default=None, help="NATS operation binding queue")
    parser.add_argument(
        "--server",
        action="append",
        default=[],
        metavar="NAME=protocol://host",
        help="Explicit server entry (repeatable). No servers block is emitted by default",
    )
    parser.add_argument("-o", "--output", default=None, help="Write the document here")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diff against --output's committed snapshot; exit 1 on drift",
    )


def _run(args: argparse.Namespace) -> int:
    """
    Execute ``varco export-asyncapi``.

    Args:
        args: The parsed namespace produced by :func:`register`'s parser.

    Returns:
        ``0`` ok, ``1`` on snapshot drift or a resolution failure, ``2`` on a
        usage error (no consumer source given, or a malformed ``--server``).

    Edge cases:
        - ⚠️ A ``--consumer``/``--source`` value with **no** ``:`` separator
          raises ``ValueError`` out of ``_import_attr`` and is *not* caught here
          (only ``ImportError``/``AttributeError``/``TypeError`` are), so it
          surfaces as a traceback rather than exit code ``2``.
    """
    for path in args.path:
        # Prepend so an explicitly named directory wins over anything already
        # importable — the caller is pointing at a specific app on purpose.
        sys.path.insert(0, str(Path(path).resolve()))

    if not args.consumer and not args.source:
        print(
            "Nothing to document — pass at least one --consumer or --source.",
            file=sys.stderr,
        )
        return 2

    try:
        servers = _parse_servers(args.server)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        consumers = _collect_consumers(args.consumer, args.source)
    except (ImportError, AttributeError, TypeError) as exc:
        print(f"Could not resolve a consumer source: {exc}", file=sys.stderr)
        return 1

    document = generate_asyncapi(
        consumers,
        title=args.title,
        version=args.version,
        protocol=args.protocol,
        group_id=args.group_id,
        queue_group=args.queue_group,
        servers=servers or None,
    )
    rendered = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    if args.check:
        return _check(rendered, args.output)

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


def _check(rendered: str, snapshot_path: str | None) -> int:
    """
    Compare *rendered* against the committed snapshot.

    Args:
        rendered:      The freshly generated document text.
        snapshot_path: Path to the committed snapshot.  Required under
                       ``--check`` — there is nothing to diff against otherwise.

    Returns:
        ``0`` when they match, ``1`` on drift or a missing snapshot, ``2`` when
        no path was supplied.

    Edge cases:
        - A missing snapshot file is drift, not a crash: it is what a fresh
          checkout of a branch that deleted the file looks like.
    """
    if not snapshot_path:
        print("--check requires --output (the snapshot to diff against).", file=sys.stderr)
        return 2

    path = Path(snapshot_path)
    if not path.exists():
        print(f"AsyncAPI snapshot {snapshot_path} does not exist.", file=sys.stderr)
        return 1
    if path.read_text(encoding="utf-8") != rendered:
        print(
            f"AsyncAPI snapshot {snapshot_path} is out of date — "
            "regenerate it with the same command minus --check.",
            file=sys.stderr,
        )
        return 1
    print(f"AsyncAPI document matches {snapshot_path}.")
    return 0


def _parse_servers(entries: Sequence[str]) -> dict[str, str]:
    """
    Parse ``--server NAME=protocol://host`` entries.

    Args:
        entries: The raw ``--server`` values.

    Returns:
        A ``{name: url}`` mapping — empty when none were given, which is the
        default and means "emit no ``servers`` block" (§D-AA2).

    Raises:
        ValueError: On an entry with no ``=``.
    """
    servers: dict[str, str] = {}
    for entry in entries:
        name, sep, url = entry.partition("=")
        if not sep or not name or not url:
            raise ValueError(f"--server must be NAME=protocol://host (got {entry!r})")
        servers[name] = url
    return servers


def _collect_consumers(
    consumer_targets: Sequence[str], source_targets: Sequence[str]
) -> list[EventConsumer]:
    """
    Import and wire every requested consumer source.

    ``--consumer`` classes are constructed here and registered to a throwaway
    ``InMemoryEventBus``, because the generator documents *registered* consumers
    only — an unwired instance describes a subscription that does not exist.
    ``--source`` callables are the honest seam for anything more complex: the app
    returns its own already-wired consumers (or its container).

    Args:
        consumer_targets: ``module:Class`` strings.
        source_targets:   ``module:callable`` strings.

    Returns:
        Consumer instances, all registered.

    Raises:
        ValueError:     If a target string has no ``module:attribute`` separator.
        ImportError:    If a module cannot be imported.
        AttributeError: If the named attribute does not exist.
        TypeError:      If a class cannot be constructed either with no arguments
                        or with a single bus argument.

    Edge cases:
        - A ``--source`` callable returning a container is passed through
          untouched: ``generate_asyncapi`` resolves consumers from it directly.
        - Construction is attempted as ``Class()`` first, then ``Class(bus)`` —
          the common shape for a consumer that stores its injected bus.
    """
    bus = InMemoryEventBus()
    collected: list[EventConsumer] = []

    for target in consumer_targets:
        cls = _import_attr(target)
        instance = _construct(cls, bus)
        instance.register_to(bus)
        collected.append(instance)

    for target in source_targets:
        obj = _import_attr(target)
        produced = obj() if callable(obj) and not isinstance(obj, EventConsumer) else obj
        if isinstance(produced, EventConsumer):
            collected.append(produced)
        elif hasattr(produced, "get_all_bindings"):
            # A DIContainer — the generator knows how to read one, and resolving
            # it here would duplicate that logic (and its skip-on-failure rules).
            collected.extend(_from_container(produced))
        else:
            collected.extend(produced)

    return collected


def _from_container(container: Any) -> list[EventConsumer]:
    """
    Resolve consumers from a container by delegating to the generator's own rule.

    Args:
        container: A ``DIContainer``.

    Returns:
        The consumer instances the generator would have found itself.
    """
    from varco_core.asyncapi.generator import _resolve_consumers  # noqa: PLC0415

    return _resolve_consumers(container)


def _import_attr(target: str) -> Any:
    """
    Resolve a ``module:dotted.attr`` string.

    Args:
        target: e.g. ``"example.consumer:PostEventConsumer"``.

    Returns:
        The named object.

    Raises:
        ValueError:     If *target* has no ``:`` separator.
        ImportError:    If the module cannot be imported.
        AttributeError: If any attribute in the dotted path is missing.
    """
    module_name, sep, attr_path = target.partition(":")
    if not sep:
        raise ValueError(f"target must be 'module:attribute' (got {target!r})")
    obj: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


def _construct(cls: type[EventConsumer], bus: InMemoryEventBus) -> EventConsumer:
    """
    Construct *cls*, tolerating the common "consumer holds its bus" shape.

    Args:
        cls: The ``EventConsumer`` subclass to instantiate.
        bus: The throwaway bus, passed as the single argument on the retry.

    Returns:
        A constructed instance.

    Raises:
        TypeError: If neither ``cls()`` nor ``cls(bus)`` is constructible — the
                   signal to use ``--source`` and let the app wire itself.
    """
    try:
        return cls()
    except TypeError:
        # A consumer whose __init__ takes an injected bus (`Inject[AbstractEventBus]`)
        # is the single most common shape in this repo — worth one retry before
        # telling the caller to write a --source factory.
        return cls(bus)  # type: ignore[call-arg]


__all__ = ["register"]
