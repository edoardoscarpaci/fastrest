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

from pathlib import Path

import mkdocs_gen_files

# Workspace package roots. Each package follows the ``varco_x/varco_x/`` layout
# (distribution dir / import package). Keep in sync with pyproject.toml members.
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
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = Path("reference")

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

        nav[parts] = doc_path.as_posix()

        identifier = ".".join(parts)
        with mkdocs_gen_files.open(full_doc_path, "w") as fd:
            fd.write(f"# `{identifier}`\n\n::: {identifier}\n")

        mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(REPO_ROOT))

with mkdocs_gen_files.open(REFERENCE_DIR / "SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
