#!/usr/bin/env python3
"""
scripts/sbom.py — per-distribution CycloneDX SBOMs for the varco workspace.

Plan 030 / Phase 3 (BACKLOG 3.1, row **D5**), design §D-D5-tooling. Evidence:
``design/research/004-flags-asyncapi-and-sbom-tooling.md`` §3.

For every distribution package it exports that package's *own* resolved runtime
dependency set from ``uv.lock`` and converts it into a CycloneDX 1.6 JSON
document::

    uv export --package <name> --no-dev … | cyclonedx-py requirements -

Output lands at ``<dir>/sbom/<name>.cdx.json``, and (unless ``--no-inject``) the
package's ``pyproject.toml`` gains::

    [tool.hatch.build.targets.wheel]
    sbom-files = ["sbom/<name>.cdx.json"]

so ``uv build --package <dir>`` places the document at ``.dist-info/sboms/`` per
**PEP 770**.

DESIGN: ONE SBOM PER DISTRIBUTION, not one workspace-wide document
    (Plan 030 Open question 2, decided here.)
    ✅ Accurate. ``varco-core`` has 25 components; the workspace has 154. A
       workspace-wide document attached to ``varco-core`` would over-report by
       ~6x — and an over-reporting SBOM is *actively misleading* to the exact
       consumer it exists to serve (a regulated downstream doing a vulnerability
       match against components it does not actually install).
    ✅ ``uv export --package <name>`` already produces precisely this subset, so
       accuracy costs one flag rather than a dependency-graph walk of our own.
    ✅ PEP 770 is per-wheel by construction — a workspace-wide document would be
       wrong *inside* the wheel even if it were acceptable on the Release page.
    ❌ Ten invocations instead of one, and ten artifacts to attach. Accepted: the
       run is seconds and the matrix that publishes them already has ten legs.

DESIGN: pin ``cyclonedx-bom`` exactly, invoke via ``uvx --from``
    ✅ Same discipline as the repo's pinned ``ruff``/``mypy``: a floating
       generator would silently change the SBOM's shape between releases, which
       is the one property an SBOM consumer diffs.
    ✅ No new workspace dependency — this is release tooling, not a library dep,
       and it must not appear in the very dependency set it is describing.
    ❌ ``uvx`` resolves from the network at run time. Bounded by the exact ``==``
       pin (this is the case CLAUDE.md's "never ``uvx ruff``" rule is about —
       *unpinned* ``uvx``; the pin is what makes it reproducible).

DESIGN: inject ``sbom-files`` at release time instead of committing it
    ✅ hatchling ≥ 1.31 resolves ``sbom-files`` as **literal, existing paths**
       (``builders/wheel.py``'s ``add_sboms``: ``os.path.isfile`` then "SBOM file
       not found") — no globbing, no tolerance for absence. Committing the key
       would make every local ``make build`` fail until someone generated an
       SBOM, and committing the *document* would ship a stale one.
    ✅ pyproject rewriting by script is an established mechanism here
       (``scripts/bump.py``, tomlkit, style-preserving).
    ❌ The release build runs against a mutated (uncommitted) pyproject. Bounded:
       the mutation is one key, printed, and never committed.
    Rejected — hand-patching the built wheel: ❌ breaks the RECORD hash file, and
    plan 030 forbids it explicitly.

Usage::

    uv run python scripts/sbom.py                    # all packages, inject config
    uv run python scripts/sbom.py --no-inject        # documents only
    uv run python scripts/sbom.py --packages varco_core
    uv run python scripts/sbom.py --out-dir sbom-artifacts   # also collect copies

Thread safety:  N/A — a one-shot CLI process.
Async safety:   N/A — subprocess-based, fully synchronous.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import tomlkit

ROOT = Path(__file__).resolve().parent.parent

#: Pinned exactly — see the module DESIGN block. Bump in a reviewed diff only.
CYCLONEDX_BOM_VERSION = "7.3.1"

#: CycloneDX spec version. 1.6 rather than the tool's newest (1.7) because 1.6 is
#: what the broadest set of consumer tooling ingests today; the generator
#: supports both and moving up is a one-line change.
CYCLONEDX_SPEC_VERSION = "1.6"


def packages() -> list[str]:
    """
    Return the workspace's distribution-package directories.

    Derived by *executing* ``scripts/packages.sh`` (Plan 020 / RL-18) rather than
    hand-listing ten names, so an eleventh workspace member needs no edit here —
    the same rule ``scripts/api_surface.py``, ``scripts/bump.py`` and
    ``release.yml`` already follow.

    Returns:
        Directory names such as ``["varco_core", "varco_kafka", …]``.

    Raises:
        subprocess.CalledProcessError: If the derivation script fails.
    """
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "packages.sh")],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def distribution_name(package_dir: str) -> str:
    """
    Map a package directory to its PyPI distribution name.

    Args:
        package_dir: e.g. ``"varco_core"``.

    Returns:
        e.g. ``"varco-core"`` — the same underscore→hyphen rule
        ``release.yml``'s matrix uses.
    """
    return package_dir.replace("_", "-")


def generate(package_dir: str, *, inject: bool) -> Path:
    """
    Generate the CycloneDX SBOM for one distribution.

    Args:
        package_dir: The package directory, e.g. ``"varco_core"``.
        inject:      When ``True``, also write ``sbom-files`` into the package's
                     ``pyproject.toml`` so the wheel carries the document per
                     PEP 770.

    Returns:
        The path of the written SBOM document.

    Raises:
        subprocess.CalledProcessError: If ``uv export`` or ``cyclonedx-py``
                                       fails — a partial SBOM is worse than
                                       none, so nothing is swallowed.

    Edge cases:
        - ``--no-dev`` is deliberate: an SBOM describes what a *consumer*
          installs, and no consumer installs our pytest/ruff/mypy tree.
        - ``--no-emit-workspace`` drops the sibling ``varco-*`` workspace members
          from the requirements set; they appear in the wheel's own metadata as
          declared dependencies, and emitting them here as local path
          requirements would produce components with no resolvable PURL.
    """
    name = distribution_name(package_dir)
    out_path = ROOT / package_dir / "sbom" / f"{name}.cdx.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    requirements = subprocess.run(
        [
            "uv",
            "export",
            "--package",
            name,
            "--no-dev",
            "--no-emit-workspace",
            "--no-annotate",
            "--format",
            "requirements.txt",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    subprocess.run(
        [
            "uvx",
            "--from",
            f"cyclonedx-bom=={CYCLONEDX_BOM_VERSION}",
            "cyclonedx-py",
            "requirements",
            "--pyproject",
            str(ROOT / package_dir / "pyproject.toml"),
            "--mc-type",
            "library",
            "--sv",
            CYCLONEDX_SPEC_VERSION,
            "--of",
            "JSON",
            # Reproducible output: no timestamp, no per-run serial number — two
            # runs over one lockfile must produce byte-identical documents, or a
            # consumer diffing releases sees noise.
            "--output-reproducible",
            "-o",
            str(out_path),
            "-",
        ],
        cwd=ROOT,
        input=requirements,
        text=True,
        check=True,
    )

    if inject:
        _inject_wheel_config(package_dir, out_path)

    return out_path


def _inject_wheel_config(package_dir: str, sbom_path: Path) -> None:
    """
    Point the package's wheel build at *sbom_path* (PEP 770).

    Args:
        package_dir: The package directory.
        sbom_path:   The generated SBOM document.

    Returns:
        None — ``<package_dir>/pyproject.toml`` is rewritten in place, preserving
        comments and formatting (tomlkit, as ``scripts/bump.py`` does).

    Edge cases:
        - Idempotent: re-running rewrites the same value.
        - The path is stored **relative to the package directory**, which is the
          build root ``uv build --package <dir>`` uses and the root hatchling
          resolves ``sbom-files`` against.
    """
    pyproject_path = ROOT / package_dir / "pyproject.toml"
    document = tomlkit.parse(pyproject_path.read_text(encoding="utf-8"))

    tool = document.setdefault("tool", tomlkit.table())
    hatch = tool.setdefault("hatch", tomlkit.table())
    build = hatch.setdefault("build", tomlkit.table())
    targets = build.setdefault("targets", tomlkit.table())
    wheel = targets.setdefault("wheel", tomlkit.table())
    wheel["sbom-files"] = [str(sbom_path.relative_to(ROOT / package_dir))]

    pyproject_path.write_text(tomlkit.dumps(document), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """
    Entry point.

    Args:
        argv: Argument list excluding the program name. ``None`` reads
              ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` if any package's generation failed.
    """
    parser = argparse.ArgumentParser(description="Generate per-distribution CycloneDX SBOMs")
    parser.add_argument(
        "--packages",
        nargs="*",
        default=None,
        metavar="PKG",
        help="Narrow to these package directories (default: all)",
    )
    parser.add_argument(
        "--no-inject",
        action="store_true",
        help="Do not write `sbom-files` into each pyproject.toml (no PEP 770 wheel embedding)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Also copy every generated document here (for a GitHub Release upload)",
    )
    args = parser.parse_args(argv)

    selected = args.packages if args.packages else packages()
    collected: Path | None = Path(args.out_dir) if args.out_dir else None
    if collected is not None:
        collected.mkdir(parents=True, exist_ok=True)

    failed = False
    for package_dir in selected:
        try:
            written = generate(package_dir, inject=not args.no_inject)
        except subprocess.CalledProcessError as exc:
            print(f"sbom: FAILED {package_dir}: {exc}", file=sys.stderr)
            failed = True
            continue
        if collected is not None:
            shutil.copy2(written, collected / written.name)
        print(f"sbom: wrote {written.relative_to(ROOT)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
