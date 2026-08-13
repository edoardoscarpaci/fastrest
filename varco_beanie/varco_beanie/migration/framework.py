"""
varco_beanie.migration.framework
=================================
varco's own Mongo migrations — the analogue of ``varco_sa.migration``'s
packaged Alembic ``varco`` branch, so ``pip install -U varco-beanie`` can
ship a new framework index requirement the same way an app ships its own.

DESIGN: an initial no-op migration + framework collections' index reconciliation
    ✅ Framework collections (``varco_outbox``, ``varco_jobs``,
       ``varco_audit_log``, the encryption-key store's ``scope`` index, …)
       get their required indexes the same way an app's own domain classes
       do — via ``BeanieIndexGuard``/``IndexReconciler`` — rather than a
       second, parallel indexing mechanism.
    ❌ Unlike the SA framework branch, there is no DDL to "create tables"
       here — Mongo collections are created implicitly on first write, so
       this module's only job is registering an (initially empty) baseline
       migration placeholder that future framework releases can extend.

Thread safety:  ✅ Registration happens at import/wiring time.
Async safety:   ✅ ``up()`` is ``async def`` (currently a no-op placeholder).
"""

from __future__ import annotations

from typing import Any

from varco_beanie.migration.base import Migration, MigrationRegistry


class _FrameworkBaseline(Migration):
    """
    Framework baseline placeholder — reserves version ``"0000_varco_framework_baseline"``.

    MongoDB collections are created implicitly on first write, so there is
    no DDL to run here (unlike the SA framework branch's
    ``op.create_table`` baseline). This exists so future framework
    releases have a version to anchor new framework migrations after, and
    so the framework registry is never empty (making
    ``register_framework_migrations`` easy to test for presence).
    """

    version = "0000_varco_framework_baseline"
    name = "varco framework baseline"

    async def up(self, db: Any) -> None:
        return None


def register_framework_migrations(registry: MigrationRegistry) -> None:
    """
    Register varco's own framework migrations into ``registry``.

    Called automatically by a fresh ``MigrationRegistry`` unless the caller
    opts out (see ``MigrationRegistry`` construction in application code —
    a future ``include_framework=False`` flag is left for the app to filter
    at the ``BeanieMigrator`` layer via a custom registry if ever needed).

    Args:
        registry: The ``MigrationRegistry`` to register into.
    """
    registry.register(_FrameworkBaseline)


__all__ = ["register_framework_migrations"]
