#!/usr/bin/env python3
"""Lockstep version bump for every varco distribution package.

Plan 023 / Phase 1, design section §RL-9-bump / §RL-9-pins. This is the
**one mechanism** that ever writes a version number into a `pyproject.toml`
in this workspace — see CLAUDE.md's "Commands" section for the paired
`scripts/api_surface.py` and the contrast in how their `--check` modes are
wired (this one **is** a CI gate; that one deliberately is not).

Contract (verbatim from the plan — keep this table and the code in sync):

| Aspect | Decision |
|---|---|
| Package list | Derived by **executing** `scripts/packages.sh` (Plan 020 / RL-18) — never a hand-written list, same subprocess pattern as `scripts/api_surface.py`. |
| Edits (1) | `[project].version` in each distribution's `pyproject.toml`. |
| Edits (2) | Every sibling `varco-*` requirement string inside `[project].dependencies`. |
| Edits (3) | Every sibling `varco-*` requirement string inside `[project.optional-dependencies].*` — these **do** ship in wheel metadata (`Provides-Extra` + `Requires-Dist`). |
| Never edits | `[dependency-groups]` sibling entries — PEP 735 groups never reach a published artifact; pinning them would be noise. |
| Never edits | `[tool.uv.sources]` (a resolution directive, not a version), the root `pyproject.toml` (not a distribution), `examples/**` (not a distribution — `scripts/packages.sh` already excludes it). |
| `uv.lock` | Re-locked by the CLI after a successful `--set`/`--bump` write, unless `--no-lock` — `uv version` does not re-lock, so this is this script's job. |
| Modes | `--set X.Y.Z` · `--bump major\\|minor\\|patch` · `--dry-run` (print a unified diff, write nothing) · `--check` (verify, write nothing, exit 1 on drift). |
| `--check` semantics | Exit 1 if (a) the ten `[project].version` values are not all identical, or (b) any shipped sibling requirement string differs from the canonical pin derived from that version. Prints a package/version/pin table either way. |

DESIGN: tomlkit (style-preserving), not a bare TOML re-serialize or a regex
  ✅ tomlkit's `parse()`/`dumps()` round-trip is byte-identical when nothing
     is mutated (verified empirically against the real tree before this
     script was written — see `TestRoundTripFidelity` in the test module),
     and mutating a single array item or table value preserves every
     comment, blank line and the file's aligned `key       = value` style.
     A whole-file re-render (e.g. via `tomllib` + hand-written dump) would
     silently reformat all ten files on every bump, an unreviewable diff.
  ✅ Parsing gives real structure, so it can tell a `[project].dependencies`
     sibling entry from a `[dependency-groups].dev` one — a regex over
     `varco-\\w+` cannot, and getting that wrong ships a dev-only dependency
     in a wheel (see `varco_core`'s dev-group `varco-fastapi`, which must
     stay bare).
  ❌ One more dev dependency. Accepted — `tomlkit>=0.13` is small, has no
     transitive dependencies of its own, and is already the tool
     `scripts/api_surface.py`-adjacent tooling in this ecosystem reaches for
     (LlamaIndex's own uv monorepo bump script, cited in brief 004 §3, uses
     the same library).

DESIGN: `~=<major>.0` compatible-release pins, not exact `==<version>` ones
  ✅ Brief 004 §4: exact pins force the resolver to reconcile two different
     exact demands for `varco-core` the moment two siblings are on different
     patch versions — a diamond conflict this monorepo will hit on its very
     first post-3.0.0 patch release. `~=3.0` tolerates it.
  ✅ The pin is a function of the **major version only**, so a 3.0.1/3.1.0
     bump changes ten `version =` lines and *zero* requirement strings.
  ❌ The lockstep guarantee is now carried by the *release process* (all ten
     published from one tag), not by the metadata itself — accepted; the
     honest alternative is an umbrella meta-package, already Parked in
     BACKLOG.md as more machinery than the benefit justifies.

Usage:
    uv run python scripts/bump.py --set 3.0.0
    uv run python scripts/bump.py --bump minor
    uv run python scripts/bump.py --set 3.0.0 --dry-run
    uv run python scripts/bump.py --check
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

# Matches a workspace-sibling dependency string, e.g. "varco-core",
# "varco-core~=3.0", "varco-ws>=1,<2". Group 1 is the bare package name;
# group 2 (possibly empty) is any existing version specifier, which is
# always discarded and replaced with the canonical `~=<major>.0` pin —
# see the "sibling requirement already carries a specifier" Edge case.
_SIBLING_RE = re.compile(r"^(varco-[a-z0-9][a-z0-9-]*)([<>=!~,\s\d.*a-zA-Z]*)?$")

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def discover_packages(root: Path) -> list[str]:
    """Return the workspace's distribution package directory names.

    Derives the list by **executing** ``scripts/packages.sh`` (Plan 020 /
    RL-18) rather than hand-listing the ten names — see the module
    docstring's contract table.

    Args:
        root: Workspace root containing ``scripts/packages.sh``.

    Returns:
        Directory names (underscore form, e.g. ``varco_core``), one per
        line of the script's stdout, in the order it printed them.

    Raises:
        subprocess.CalledProcessError: if ``scripts/packages.sh`` exits
            non-zero (e.g. no ``python3`` on PATH — see that script's own
            docstring for why it deliberately does not fall back silently).
    """
    result = subprocess.run(
        ["bash", str(root / "scripts" / "packages.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def bump_version(version: str, part: str) -> str:
    """Compute the next semantic version for one part of ``version``.

    Args:
        version: A ``MAJOR.MINOR.PATCH`` string (no pre-release/build
            metadata — this workspace does not use them outside the
            release-candidate tag, which is applied to the git tag, never
            written into a ``pyproject.toml``).
        part: One of ``"major"``, ``"minor"``, ``"patch"``.

    Returns:
        The bumped version string, following standard SemVer 2.0.0 reset
        rules (a major bump resets minor and patch to 0; a minor bump
        resets patch to 0).

    Raises:
        ValueError: if ``version`` is not ``MAJOR.MINOR.PATCH`` or ``part``
            is not one of the three accepted values.
    """
    match = _SEMVER_RE.match(version)
    if match is None:
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    major, minor, patch = (int(x) for x in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"part must be 'major', 'minor' or 'patch', got {part!r}")


def _pin_sibling_strings(items: list[str], major: str) -> bool:
    """Rewrite every sibling ``varco-*`` string in ``items`` in place.

    Args:
        items: A tomlkit array's items, addressed by index so mutation is
            visible through the original array object (tomlkit arrays do
            not support in-place string mutation via iteration).
        major: The target version's major component, e.g. ``"3"`` for a
            ``"3.0.0"`` bump — the canonical pin is always ``~=<major>.0``.

    Returns:
        True if any item was rewritten (used to decide whether the owning
        file needs to be written at all).
    """
    changed = False
    for i in range(len(items)):
        current = str(items[i])
        match = _SIBLING_RE.match(current)
        if match is None:
            continue
        name = match.group(1)
        canonical = f"{name}~={major}.0"
        if current != canonical:
            items[i] = canonical
            changed = True
    return changed


def _apply_version(doc: TOMLDocument, version: str) -> bool:
    """Rewrite ``[project].version`` and every sibling pin in ``doc``.

    Args:
        doc: A parsed ``pyproject.toml`` (tomlkit document — mutated in
            place so the caller controls whether/when to write it back).
        version: The target ``MAJOR.MINOR.PATCH`` string.

    Returns:
        True if anything in the document changed.

    Edge cases:
        A package with no ``[project.optional-dependencies]`` table (most
        of them) or no sibling entries in it is a no-op for that section —
        checked defensively rather than assumed present.
    """
    major = version.split(".")[0]
    project = doc["project"]
    changed = False

    if str(project.get("version")) != version:
        project["version"] = version
        changed = True

    dependencies = project.get("dependencies")
    if dependencies is not None:
        changed = _pin_sibling_strings(dependencies, major) or changed

    optional = project.get("optional-dependencies")
    if optional is not None:
        for extra_name in list(optional.keys()):
            changed = _pin_sibling_strings(optional[extra_name], major) or changed

    return changed


def compute_changes(root: Path, version: str) -> dict[Path, tuple[str, str]]:
    """Compute (without writing) the per-file text change for a version bump.

    Args:
        root: Workspace root.
        version: Target ``MAJOR.MINOR.PATCH`` string.

    Returns:
        A mapping of changed ``pyproject.toml`` paths to
        ``(old_text, new_text)`` pairs. A package whose file would be
        byte-identical after the bump (e.g. re-running ``--set`` with the
        version it already has) is omitted — this is what makes
        ``--dry-run`` a genuine "what would change" report rather than a
        list of every file it touches.
    """
    changes: dict[Path, tuple[str, str]] = {}
    for pkg in discover_packages(root):
        path = root / pkg / "pyproject.toml"
        old_text = path.read_text()
        doc = tomlkit.parse(old_text)
        _apply_version(doc, version)
        new_text = tomlkit.dumps(doc)
        if new_text != old_text:
            changes[path] = (old_text, new_text)
    return changes


def set_version(root: Path, version: str) -> list[Path]:
    """Write the target version into every distribution's ``pyproject.toml``.

    Args:
        root: Workspace root.
        version: Target ``MAJOR.MINOR.PATCH`` string.

    Returns:
        The list of files actually written (a re-run with an
        already-current version writes nothing and returns an empty list —
        this is the round-trip-fidelity guarantee the test suite checks).

    Edge cases:
        Never touches ``[dependency-groups]``, ``[tool.uv.sources]``, or any
        file outside a package returned by :func:`discover_packages` (which
        structurally excludes ``examples`` and the root ``pyproject.toml`` —
        see the module docstring's contract table).
    """
    changes = compute_changes(root, version)
    for path, (_old, new_text) in changes.items():
        path.write_text(new_text)
    return list(changes.keys())


def _render_diff(old_text: str, new_text: str, path: Path) -> str:
    """Render a unified diff for one file's dry-run preview."""
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def _read_current_state(root: Path) -> dict[str, dict[str, str]]:
    """Read every package's current version and sibling pin strings.

    Returns:
        ``{package: {"version": "1.2.0", "varco-core": "varco-core", ...}}``
        — the ``"version"`` key is always present; every other key is a
        sibling package name mapped to its full current requirement string
        (as it appears in ``[project].dependencies`` or
        ``[project.optional-dependencies].*``).
    """
    state: dict[str, dict[str, str]] = {}
    for pkg in discover_packages(root):
        path = root / pkg / "pyproject.toml"
        doc = tomlkit.parse(path.read_text())
        project = doc["project"]
        entry: dict[str, str] = {"version": str(project.get("version"))}
        for items in [
            project.get("dependencies") or [],
            *[items for items in (project.get("optional-dependencies") or {}).values()],
        ]:
            for raw in items:
                text = str(raw)
                match = _SIBLING_RE.match(text)
                if match is not None:
                    entry[match.group(1)] = text
        state[pkg] = entry
    return state


def check_coherence(root: Path) -> tuple[bool, str]:
    """Verify the ten distributions agree on a version and canonical pins.

    Args:
        root: Workspace root.

    Returns:
        ``(ok, report)`` — ``ok`` is False if (a) the versions diverge, or
        (b) any sibling requirement string is not the canonical
        ``~=<major>.0`` pin for the common version. ``report`` is always a
        human-readable package/version/pin table (printed either way, per
        the plan's ``--check`` semantics table).
    """
    state = _read_current_state(root)
    versions = {pkg: entry["version"] for pkg, entry in state.items()}
    distinct_versions = set(versions.values())

    lines = ["package\tversion\tsibling pins"]
    for pkg in sorted(state):
        entry = state[pkg]
        pins = ", ".join(
            f"{name}={value}" for name, value in sorted(entry.items()) if name != "version"
        )
        lines.append(f"{pkg}\t{entry['version']}\t{pins or '(none)'}")
    report = "\n".join(lines)

    if len(distinct_versions) > 1:
        majority = max(distinct_versions, key=lambda v: list(versions.values()).count(v))
        divergent = sorted(pkg for pkg, v in versions.items() if v != majority)
        report += (
            f"\n\nDIVERGENT: {', '.join(divergent)} do not match the majority version {majority!r}"
        )
        return False, report

    version = next(iter(distinct_versions))
    major = version.split(".")[0]
    bad: list[str] = []
    for pkg, entry in state.items():
        for name, value in entry.items():
            if name == "version":
                continue
            canonical = f"{name}~={major}.0"
            if value != canonical:
                bad.append(f"{pkg}: {name} pinned as {value!r}, expected {canonical!r}")
    if bad:
        report += "\n\nMISMATCHED PINS:\n" + "\n".join(bad)
        return False, report

    return True, report


def _run_uv_lock(root: Path) -> None:
    """Re-lock the workspace after a version write.

    ``uv version`` does not re-lock on its own (brief 004 §1); a bump that
    leaves ``uv.lock`` stale breaks CI's `uv sync --locked` on the very next
    push, so this is the CLI's job whenever it writes files.
    """
    subprocess.run(["uv", "lock"], cwd=root, check=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — see the module docstring's Usage section.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 on success (including a clean ``--dry-run`` or
        a coherent ``--check``), 1 on ``--check`` divergence.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", metavar="X.Y.Z", help="Set the exact target version.")
    group.add_argument(
        "--bump", choices=["major", "minor", "patch"], help="Bump relative to the current version."
    )
    group.add_argument(
        "--check", action="store_true", help="Verify coherence; write nothing; exit 1 on drift."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the diff; write nothing.")
    parser.add_argument(
        "--no-lock", action="store_true", help="Skip `uv lock` after writing (--set/--bump only)."
    )
    args = parser.parse_args(argv)

    root = Path.cwd()

    if args.check:
        ok, report = check_coherence(root)
        print(report)
        return 0 if ok else 1

    if args.bump:
        state = _read_current_state(root)
        # Bumping requires a coherent starting point; take the first
        # package's version as the baseline (identical to every other
        # package's once Phase 3 has landed once).
        current = next(iter(state.values()))["version"]
        target = bump_version(current, args.bump)
    else:
        target = args.set

    changes = compute_changes(root, target)

    if args.dry_run:
        for path, (old_text, new_text) in changes.items():
            print(_render_diff(old_text, new_text, path), end="")
        if not changes:
            print(f"No changes: workspace already at {target}.")
        return 0

    for path, (_old, new_text) in changes.items():
        path.write_text(new_text)

    if changes and not args.no_lock:
        _run_uv_lock(root)

    print(f"Set {len(changes)} package(s) to {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
