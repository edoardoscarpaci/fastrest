"""Generate the API-reference pages for every workspace package.

Run automatically by the ``mkdocs-gen-files`` plugin at build time (see
``mkdocs.yml``). For each Python module under each ``varco_*`` package source
root, it writes a virtual markdown page containing a single ``mkdocstrings``
directive (``::: dotted.module.path``). ``mkdocstrings`` then renders the
module's classes/functions from their docstrings.

A ``reference/SUMMARY.md`` file is also emitted so the ``literate-nav`` plugin
can build the navigation tree automatically — no manual nav upkeep as modules
are added or removed.

DESIGN: generate-at-build over committing reference stubs
  ✅ Reference pages never drift from the source tree — regenerated each build.
  ✅ Adding a module needs zero doc changes; it appears automatically.
  ❌ Reference content only exists in the built ``site/`` (gitignored), not in git.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import mkdocs_gen_files

REPO_ROOT = Path(__file__).resolve().parent.parent


def _derive_packages() -> tuple[str, ...]:
    """Derive the distribution-package list from ``[tool.uv.workspace] members``.

    Single source of truth (Plan 020 / RL-18) — the literal ``PACKAGES`` tuple
    below used to be a hand-written copy that silently drifted from the
    workspace (it was missing ``varco_casbin``, so ``make docs`` never
    rendered that package's API reference). Each package follows the
    ``varco_x/varco_x/`` layout (distribution dir / import package); a member
    is a distribution iff ``<member>/<member>/__init__.py`` exists — this
    structurally excludes non-distribution members (e.g. ``examples``)
    without naming them.

    Returns:
        Distribution-package names, in ``members`` order.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    members = data["tool"]["uv"]["workspace"]["members"]
    return tuple(m for m in members if (REPO_ROOT / m / m / "__init__.py").is_file())


# Workspace package roots, kept as a literal tuple (not a call) so a static
# reader (varco_core/tests/test_repo_package_lists.py parses this file with
# `ast` rather than importing it, since importing triggers mkdocs_gen_files'
# build-time side effects) can verify it without executing anything.
#
# DESIGN: literal tuple + runtime drift assertion, instead of a pure
# `PACKAGES = _derive_packages()` call
#   ✅ Stays statically inspectable (ast.literal_eval) for the test suite,
#      with zero import-time side effects.
#   ✅ Still can't silently drift: the assertion immediately below fails
#      loudly (RuntimeError) the moment this literal disagrees with the live
#      `[tool.uv.workspace] members` derivation — the exact defect this row
#      exists to remove, just checked at doc-build time instead of at every
#      Python-level call site.
#   ❌ Requires updating this literal by hand when a workspace member is
#      added/removed. Accepted: the assertion turns a missed update into an
#      immediate `make docs` failure naming the mismatch, never a silent gap
#      (which is exactly what happened to `varco_casbin` before this fix).
PACKAGES: tuple[str, ...] = (
    "varco_core",
    "varco_kafka",
    "varco_nats",
    "varco_redis",
    "varco_beanie",
    "varco_sa",
    "varco_memcached",
    "varco_ws",
    "varco_fastapi",
    "varco_casbin",
)

_derived_packages = _derive_packages()
if PACKAGES != _derived_packages:
    raise RuntimeError(
        f"scripts/gen_ref_pages.py's PACKAGES literal {PACKAGES!r} has drifted from "
        f"[tool.uv.workspace] members' derived list {_derived_packages!r} — update the "
        "literal above to match (Plan 020 / RL-18)."
    )
REFERENCE_DIR = Path("reference")

# Directory names whose contents are NOT importable API and must never reach
# mkdocstrings. Alembic revision scripts live in ``versions/`` and are loaded by
# Alembic *by file path*, so their filenames follow the ``0001_description.py``
# revision convention rather than Python's identifier rules — importing them by
# dotted path is impossible and aborts the build. They are also not API: the
# documented surface is ``varco_sa.migration``, not the individual revisions.
SKIP_DIRS: frozenset[str] = frozenset({"versions"})


def _is_importable(parts: tuple[str, ...]) -> bool:
    """True when every path segment is a valid Python identifier.

    A module whose dotted path contains a non-identifier segment (a leading
    digit, a hyphen) cannot be imported by ``mkdocstrings`` and would abort the
    build, so it is skipped rather than emitted.
    """
    return all(part.isidentifier() for part in parts)


nav = mkdocs_gen_files.Nav()

for package in sorted(PACKAGES):
    src_root = REPO_ROOT / package / package
    if not src_root.is_dir():
        # Package not present locally — skip rather than fail the whole build.
        continue

    for path in sorted(src_root.rglob("*.py")):
        module_path = path.relative_to(src_root.parent).with_suffix("")
        doc_path = path.relative_to(src_root.parent).with_suffix(".md")
        full_doc_path = REFERENCE_DIR / doc_path

        parts = tuple(module_path.parts)

        if parts[-1] == "__init__":
            parts = parts[:-1]
            doc_path = doc_path.with_name("index.md")
            full_doc_path = REFERENCE_DIR / doc_path
        elif parts[-1] == "__main__":
            continue

        if not parts:
            continue

        if SKIP_DIRS.intersection(parts) or not _is_importable(parts):
            continue

        nav[parts] = doc_path.as_posix()

        identifier = ".".join(parts)
        with mkdocs_gen_files.open(full_doc_path, "w") as fd:
            fd.write(f"# `{identifier}`\n\n::: {identifier}\n")

        mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(REPO_ROOT))

with mkdocs_gen_files.open(REFERENCE_DIR / "SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
