#!/usr/bin/env python3
"""Snapshot (and diff) the public API surface of every workspace package.

Plan 022 / Phase 0, design section §D-AUDIT. RL-8's audit is a *committed
artifact*, not an eyeball pass: for each distribution package this script
imports the top-level module, walks ``__all__``, and records for every
exported name its kind (``class`` / ``function`` / ``constant``), its defining
module and — for functions — ``inspect.signature()`` rendered as a string.

Two outputs, deliberately:

* ``design/api-freeze-and-standards/measurements/api-surface.json`` — machine
  readable, the thing ``--check`` diffs against.
* the sibling ``api-surface.md`` — sorted and human-diffable, so a reviewer can
  read a break out of a pull-request diff without running anything.

``--check`` regenerates the surface from the live tree and exits non-zero on a
**removal**, a **signature change** or a **kind change**. Additions and
module moves are reported but do not fail — an audit gate whose job is to
protect callers must not block a purely additive release.

DESIGN: a committed snapshot + ``--check`` gate, over a one-off manual review
  ✅ Reproducible. The same command re-run after any edit shows exactly what
     moved, which is the property BACKLOG's RL-20/RL-21 lesson says
     unrepeatable observations lack.
  ✅ Reusable as the post-freeze break detector RL-9's deprecation policy would
     otherwise have to invent from scratch.
  ✅ Scales: 468 exported names across ten packages is well past the point
     where an eyeball pass is honest.
  ❌ ``inspect.signature()`` under ``from __future__ import annotations``
     renders annotations as source strings, so a *semantic* narrowing that
     keeps the same source text is invisible. Accepted — mypy (Plan 021,
     ``strict = true``) already owns semantic typing; this detector owns
     removals and shape changes.
  ❌ A new script to maintain. Mitigated: its package list is derived by
     *executing* ``scripts/packages.sh`` (Plan 020 / RL-18), so it structurally
     cannot drift the way the four hand-written lists did.

Alternatives considered:
  * ``griffe`` / ``pdoc`` API-diff tooling — rejected: ✅ mature and
    annotation-aware, ❌ a new dev dependency and a new failure mode inside the
    very phase whose job is to shrink dependency and surface risk, for a need
    stdlib ``inspect`` covers.
  * Hand-written ``__all__`` tables — rejected: not reproducible.

Usage:
    uv run python scripts/api_surface.py            # regenerate .json + .md
    uv run python scripts/api_surface.py --check    # diff vs committed, gate
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_SCRIPT = REPO_ROOT / "scripts" / "packages.sh"
DEFAULT_SNAPSHOT = (
    REPO_ROOT / "design" / "api-freeze-and-standards" / "measurements" / "api-surface.json"
)

#: Sentinel for "attribute absent", distinct from a legitimately-``None`` value.
_MISSING = object()

#: Default values that fall back to ``object.__repr__`` render their *heap
#: address* (``<... object at 0x7f...>``), which differs on every interpreter
#: run. Left raw, the snapshot would report a spurious signature change on every
#: single ``--check`` — found the first time this script was run against its own
#: freshly written output (``varco_core.listen``'s ``_Unset`` sentinels).
_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+>")

#: Entry keys that constitute a *break* when they change between the committed
#: snapshot and the live tree. ``module`` is deliberately absent: moving a
#: symbol between submodules while keeping the top-level re-export is not a
#: break for anyone importing from the package root, which is the surface this
#: snapshot describes.
BREAKING_KEYS = ("kind", "signature")


class ApiSurfaceError(RuntimeError):
    """A package listed by ``scripts/packages.sh`` could not be introspected.

    Raised instead of skipping the package, because a silently smaller
    snapshot would report every symbol in that package as *removed* — turning
    a missing optional extra into a fake wall of breaking changes.
    """


def discover_packages() -> list[str]:
    """Return the distribution-package names, in ``[tool.uv.workspace]`` order.

    Derived by *executing* ``scripts/packages.sh`` rather than by
    re-implementing its ``members``-parsing rule.

    DESIGN: shell out to ``packages.sh`` over re-deriving from ``pyproject.toml``
      ✅ One derivation, not two. A copy of the "member is a distribution iff
         ``<m>/<m>/__init__.py`` exists" rule is exactly the drift Plan 020 /
         RL-18 removed — ``scripts/gen_ref_pages.py`` needed a runtime
         drift assertion precisely because it holds a second copy.
      ✅ The Makefile, ``unit_tests.sh`` and ``integration_tests.sh`` already
         consume it the same way, so there is one observable list.
      ❌ Requires ``bash`` on PATH and one subprocess per run. Accepted: this
         is a developer/CI script, and every other consumer already pays it.

    Returns:
        Package names, one per line of the script's stdout, order preserved.

    Raises:
        ApiSurfaceError: if ``scripts/packages.sh`` is missing or exits non-zero.

    Edge cases:
        * Blank lines in the script's output are dropped.

    Example:
        >>> "varco_core" in discover_packages()
        True
    """
    if not PACKAGES_SCRIPT.is_file():
        raise ApiSurfaceError(f"missing package-list script: {PACKAGES_SCRIPT}")
    try:
        completed = subprocess.run(
            ["bash", str(PACKAGES_SCRIPT)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
        raise ApiSurfaceError(
            f"{PACKAGES_SCRIPT.name} exited {exc.returncode}: {exc.stderr.strip()}"
        ) from exc
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _defining_module(package: str, name: str, obj: object) -> str:
    """Best-effort defining module for an exported object.

    Args:
        package: The top-level package the name was exported from.
        name: The exported name, used to search submodules for module-less values.
        obj: The exported object itself.

    Returns:
        A dotted module path, or the package name when the value carries no
        provenance at all.

    Edge cases:
        * Plain ``str``/``int`` constants have no ``__module__``. They are
          located by scanning already-imported submodules of ``package`` for an
          attribute of the same name bound to the *identical* object. Small
          strings are interned, so two modules can match; candidates are sorted
          and the first taken, which is arbitrary but **deterministic** — the
          only property the diff actually needs.
    """
    module = getattr(obj, "__module__", None)
    if isinstance(module, str) and module:
        return module

    prefix = f"{package}."
    candidates = sorted(
        mod_name
        for mod_name, mod in list(sys.modules.items())
        if (mod_name == package or mod_name.startswith(prefix))
        and mod is not None
        and getattr(mod, name, _MISSING) is obj
    )
    # The package root itself re-exports everything, so it is only a fallback.
    for candidate in candidates:
        if candidate != package:
            return candidate
    return package


def _describe(package: str, name: str, obj: object) -> dict[str, str]:
    """Describe one exported object as a snapshot entry.

    Args:
        package: The exporting top-level package.
        name: The exported name.
        obj: The exported object.

    Returns:
        A mapping with ``kind`` and ``module``, plus ``signature`` for
        functions only.

    Edge cases:
        * Classes get **no** ``signature``. Class signatures are synthesised
          from ``__init__``/``__new__`` and, for pydantic models and
          dataclasses, from generated code whose rendering is not guaranteed
          stable across the 3.12/3.13 test matrix — a snapshot that differs by
          interpreter would make ``--check`` unrunnable in CI.
        * ``inspect.signature()`` raises for some C-level callables; the entry
          then degrades to no signature rather than failing the whole run.
        * Heap addresses in sentinel default values are stripped — see
          :data:`_ADDRESS_RE`.
    """
    if inspect.isclass(obj):
        return {"kind": "class", "module": _defining_module(package, name, obj)}
    if inspect.isroutine(obj):
        entry = {"kind": "function", "module": _defining_module(package, name, obj)}
        try:
            entry["signature"] = _ADDRESS_RE.sub(">", str(inspect.signature(obj)))
        except (TypeError, ValueError):  # pragma: no cover - C-level callables
            pass
        return entry
    return {"kind": "constant", "module": _defining_module(package, name, obj)}


def build_snapshot(packages: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Import each package and describe every name in its ``__all__``.

    Args:
        packages: Distribution-package names to introspect, typically
            ``discover_packages()``.

    Returns:
        ``{"packages": {pkg: {name: entry}}}`` with both mappings sorted, so the
        emitted JSON and Markdown are byte-stable and diffable.

    Raises:
        ApiSurfaceError: if a package cannot be imported (naming the package
            and the underlying missing module, which is almost always an
            uninstalled optional extra), or if it declares no ``__all__``, or
            if a listed name is not actually present on the module.

    Edge cases:
        * A package with an empty ``__all__`` yields an empty mapping, not an
          error — that is a legitimate (if unusual) surface.

    Example:
        >>> snap = build_snapshot(["varco_core"])
        >>> snap["packages"]["varco_core"]["current_tenant"]["kind"]
        'function'
    """
    result: dict[str, dict[str, dict[str, str]]] = {}
    for package in packages:
        try:
            module = importlib.import_module(package)
        except ImportError as exc:
            raise ApiSurfaceError(
                f"could not import package {package!r}: {exc}. "
                "A required optional extra is probably missing — "
                "run `uv sync --all-packages --all-extras`."
            ) from exc

        exported = getattr(module, "__all__", None)
        if exported is None:
            raise ApiSurfaceError(
                f"package {package!r} declares no __all__; the API-surface "
                "snapshot has nothing to pin."
            )

        entries: dict[str, dict[str, str]] = {}
        for name in sorted(exported):
            try:
                obj = getattr(module, name)
            except AttributeError as exc:
                raise ApiSurfaceError(
                    f"{package}.__all__ lists {name!r} but the module has no such attribute."
                ) from exc
            entries[name] = _describe(package, name, obj)
        result[package] = entries

    return {"packages": {pkg: result[pkg] for pkg in sorted(result)}}


def render_markdown(snapshot: dict[str, Any]) -> str:
    """Render a snapshot as sorted, diffable Markdown.

    Args:
        snapshot: The mapping returned by :func:`build_snapshot`.

    Returns:
        Markdown text ending in a newline: one ``##`` section per package,
        one table row per exported name.

    Edge cases:
        * Pipe characters inside a signature would break the table; they are
          escaped. (``|`` appears in PEP 604 unions, so this is routine here,
          not defensive.)
    """
    lines: list[str] = [
        "# Public API surface — all workspace packages",
        "",
        "Generated by `scripts/api_surface.py` (Plan 022 / §D-AUDIT). Do not edit by hand.",
        "",
        "| Package | Exported names |",
        "|---|---|",
    ]
    packages: dict[str, dict[str, dict[str, str]]] = snapshot["packages"]
    for package, entries in packages.items():
        lines.append(f"| `{package}` | {len(entries)} |")
    lines.append("")

    for package, entries in packages.items():
        lines.extend(
            [
                f"## `{package}` — {len(entries)} exports",
                "",
                "| Name | Kind | Defining module | Signature |",
                "|---|---|---|---|",
            ]
        )
        for name, entry in entries.items():
            signature = entry.get("signature", "")
            cell = f"`{signature.replace('|', r'\|')}`" if signature else ""
            lines.append(f"| `{name}` | {entry['kind']} | `{entry['module']}` | {cell} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def diff_snapshots(committed: dict[str, Any], live: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Compare a committed snapshot against a freshly built one.

    Args:
        committed: The snapshot loaded from disk.
        live: The snapshot built from the current tree.

    Returns:
        ``(breaking, informational)`` — two lists of human-readable lines.
        ``breaking`` is non-empty exactly when ``--check`` must exit non-zero.

    Edge cases:
        * A package present in the committed snapshot but absent from
          ``scripts/packages.sh`` is a **breaking** finding (the whole
          distribution disappeared), while a brand-new package is only
          informational.
    """
    breaking: list[str] = []
    informational: list[str] = []

    committed_pkgs: dict[str, dict[str, dict[str, str]]] = committed.get("packages", {})
    live_pkgs: dict[str, dict[str, dict[str, str]]] = live.get("packages", {})

    for package in sorted(set(committed_pkgs) - set(live_pkgs)):
        breaking.append(f"REMOVED PACKAGE: {package}")
    for package in sorted(set(live_pkgs) - set(committed_pkgs)):
        informational.append(f"ADDED PACKAGE: {package}")

    for package in sorted(set(committed_pkgs) & set(live_pkgs)):
        old, new = committed_pkgs[package], live_pkgs[package]
        for name in sorted(set(old) - set(new)):
            breaking.append(f"REMOVED: {package}.{name}")
        for name in sorted(set(new) - set(old)):
            informational.append(f"ADDED: {package}.{name}")
        for name in sorted(set(old) & set(new)):
            for key in BREAKING_KEYS:
                before, after = old[name].get(key), new[name].get(key)
                if before != after:
                    breaking.append(
                        f"CHANGED {key.upper()}: {package}.{name}: {before!r} -> {after!r}"
                    )
            if old[name].get("module") != new[name].get("module"):
                informational.append(
                    f"MOVED: {package}.{name}: "
                    f"{old[name].get('module')!r} -> {new[name].get('module')!r}"
                )

    return breaking, informational


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="api_surface.py",
        description="Snapshot or verify the public API surface of every workspace package.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="diff the live tree against the committed snapshot; exit 1 on a break",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"snapshot JSON path (default: {DEFAULT_SNAPSHOT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--packages",
        nargs="+",
        metavar="PKG",
        help="restrict to these packages instead of the scripts/packages.sh list",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` on a detected break or an introspection failure.

    Edge cases:
        * An unimportable package fails loudly with a one-line message naming
          the package and the missing module — never a bare traceback and never
          a silently smaller snapshot.
    """
    args = _build_parser().parse_args(argv)

    try:
        packages = list(args.packages) if args.packages else discover_packages()
        live = build_snapshot(packages)
    except ApiSurfaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    json_path: Path = args.snapshot
    md_path = json_path.with_suffix(".md")

    if not args.check:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
        md_path.write_text(render_markdown(live))
        total = sum(len(v) for v in live["packages"].values())
        print(f"wrote {json_path.relative_to(REPO_ROOT)} ({total} exports across {len(packages)})")
        print(f"wrote {md_path.relative_to(REPO_ROOT)}")
        return 0

    if not json_path.is_file():
        print(f"error: no committed snapshot at {json_path}", file=sys.stderr)
        return 1

    committed = json.loads(json_path.read_text())
    breaking, informational = diff_snapshots(committed, live)

    for line in informational:
        print(f"note: {line}")
    for line in breaking:
        print(f"BREAK: {line}", file=sys.stderr)

    if breaking:
        print(
            f"error: {len(breaking)} breaking API-surface change(s) vs {json_path.name}. "
            "Each must map to an accepted AB-n in "
            "design/api-freeze-and-standards/api-break-candidates.md, "
            "or be reverted.",
            file=sys.stderr,
        )
        return 1

    print(f"API surface matches {json_path.name} ({len(informational)} non-breaking note(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
