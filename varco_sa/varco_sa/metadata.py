"""
varco_sa.metadata
==================
Aggregated ``MetaData`` for every framework-owned table.

``varco_sa`` ships several infrastructure tables (outbox, inbox, jobs, sagas,
conversation turns, dedup log, audit log, dead letters, encryption keys), each
declared with its own ``MetaData``/``DeclarativeBase`` in its owning module so
that importing e.g. ``varco_sa.outbox`` does not pull in every other
framework table. This module is the single place a caller (or Alembic's
``env.py``) goes to get *all* of them at once.

DESIGN: lazy self-registration over a hand-maintained list
    ✅ Each owning module calls ``register_framework_metadata()`` at import
       time — a framework table added in a future ``varco_sa`` release
       registers itself; existing callers of ``framework_metadata()`` pick it
       up on ``pip install -U varco-sa`` with zero code change.
    ✅ ``framework_metadata()`` imports all nine owning modules lazily on
       first call, so a caller who only wants the aggregate does not have to
       import each module by hand.
    ❌ A framework table that forgets to call ``register_framework_metadata``
       is invisible here — guarded by
       ``varco_sa/tests/test_framework_metadata.py``'s completeness walk.

Thread safety:  ⚠️ Registration is expected at import time (single-threaded
                   interpreter startup), same as ``SAModelRegistry``.
Async safety:   ✅ Synchronous — no I/O.
"""

from __future__ import annotations

from sqlalchemy import MetaData

# module-qualified name → MetaData, populated by register_framework_metadata()
_FRAMEWORK_METADATA: dict[str, MetaData] = {}

# Owning modules that must be imported (at least once) before the aggregate
# is considered complete. Listed explicitly so ``framework_metadata()`` does
# not depend on import order elsewhere in the process.
_OWNING_MODULES = (
    "varco_sa.outbox",
    "varco_sa.inbox",
    "varco_sa.job_store",
    "varco_sa.saga",
    "varco_sa.conversation",
    "varco_sa.deduplication",
    "varco_sa.audit",
    "varco_sa.dlq",
    "varco_sa.encryption_store",
    # Plan 007, Phase 4 — the tenant catalog, the tenth framework table.
    "varco_sa.tenancy.models",
    # Plan 029 / D1b — the eleventh framework table.
    "varco_sa.idempotency",
    # Plan 031 / D4a — the twelfth framework table.
    "varco_sa.webhook",
    # Plan 032 / D6, Step 10 — the thirteenth framework table.
    "varco_sa.schedule",
)


def register_framework_metadata(name: str, md: MetaData) -> None:
    """
    Register a framework-owned ``MetaData`` under ``name``.

    Called by each owning module at import time. Idempotent — registering
    the same ``name`` twice simply overwrites the entry with the (typically
    identical) ``MetaData`` object.

    Args:
        name: A unique, human-readable key (module-qualified name by
              convention, e.g. ``"varco_sa.outbox"``).
        md:   The ``MetaData`` instance owning that module's table(s).
    """
    _FRAMEWORK_METADATA[name] = md


def _ensure_owning_modules_imported() -> None:
    """Import every known owning module so its ``register_framework_metadata``
    call at module scope has run at least once."""
    import importlib

    for module_name in _OWNING_MODULES:
        importlib.import_module(module_name)


def framework_metadata() -> MetaData:
    """
    Return one merged ``MetaData`` containing every framework-owned table.

    Imports all owning modules lazily on first call (see module docstring),
    so a caller gets the complete set without importing nine modules by
    hand. Pass this to Alembic's ``target_metadata`` (or
    ``get_target_metadata(include_framework=True)``) to include framework
    tables in autogenerate/comparison — but note Phase 2's ``varco`` branch
    normally owns these tables; only use this for the single-branch escape
    hatch.

    Returns:
        A fresh ``MetaData`` with every framework table copied in via
        ``Table.to_metadata()``.

    Edge cases:
        - Calling this before any owning module import still works — the
          function imports them itself.
        - Safe to call repeatedly; each call builds a fresh ``MetaData``
          (cheap — table counts are small).
    """
    _ensure_owning_modules_imported()

    merged = MetaData()
    for md in _FRAMEWORK_METADATA.values():
        for table in md.tables.values():
            table.to_metadata(merged)
    return merged


def framework_table_names() -> frozenset[str]:
    """
    Return the set of every framework-owned table name.

    Cheaper than ``framework_metadata().tables.keys()`` when only the names
    are needed — used by ``varco_sa.migration.env_template.include_object``
    to filter framework tables out of app-side autogenerate.
    """
    _ensure_owning_modules_imported()

    names: set[str] = set()
    for md in _FRAMEWORK_METADATA.values():
        names.update(md.tables.keys())
    return frozenset(names)


__all__ = [
    "framework_metadata",
    "framework_table_names",
    "register_framework_metadata",
]
