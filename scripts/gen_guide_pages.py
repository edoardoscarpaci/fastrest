"""Publish the repository's ``docs/`` guides into the MkDocs site.

Run automatically by the ``mkdocs-gen-files`` plugin at build time (see
``mkdocs.yml``). ``docs_dir`` is ``technical_docs/``, so the hand-written
guides under the repo's ``docs/`` directory — the regulatory-posture position
statement and the three service-integration how-tos — were never part of the
published site at all. This script copies each of them into the virtual docs
tree under ``guides/`` so ``mkdocs.yml``'s nav can reference them.

DESIGN: copy-at-build over moving the files or a second ``docs_dir``
  ✅ MkDocs supports exactly one ``docs_dir``; ``mkdocs-gen-files`` is the
     supported way to add pages from elsewhere in the tree, and it is already
     a build dependency (``scripts/gen_ref_pages.py``).
  ✅ The files keep their current paths, so every existing reference to
     ``docs/client.md`` (README, CLAUDE.md, sibling guides) stays valid.
  ❌ The published page's "edit" link needs setting explicitly
     (``set_edit_path``) — done below.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "docs"
GUIDES_DIR = Path("guides")

# Guides published from ``docs/``, in nav order. A literal tuple rather than a
# glob: a new file under ``docs/`` should be a deliberate nav decision in
# ``mkdocs.yml`` (which lists the same pages), not an entry that silently
# appears in the site with no navigation title. ``--strict`` fails loudly on a
# page that exists but is missing from nav, so the pair cannot drift unnoticed.
GUIDES: tuple[str, ...] = (
    "client.md",
    "client-code-generation.md",
    "peer-service-integration.md",
    "regulatory-posture.md",
)

for name in GUIDES:
    source = SOURCE_DIR / name
    if not source.is_file():
        raise RuntimeError(
            f"scripts/gen_guide_pages.py lists {name!r} but {source} does not exist — "
            "update GUIDES (and mkdocs.yml's Guides nav section) to match."
        )
    with mkdocs_gen_files.open(GUIDES_DIR / name, "w") as fd:
        fd.write(source.read_text(encoding="utf-8"))
    mkdocs_gen_files.set_edit_path(GUIDES_DIR / name, source.relative_to(REPO_ROOT))
