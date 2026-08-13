"""
varco_sa.tenancy.models
==========================
``varco_tenants`` — the tenth framework table (Plan 007, Phase 4, step 2).

Stores the durable tenant catalog backing ``SATenantCatalog``. Self-
registers via ``register_framework_metadata()`` exactly like the other nine
framework tables (``varco_outbox``, ``varco_inbox``, ...) — a table added in
a future release is picked up on ``pip install -U varco-sa`` with no app
change, and apps never list it in their own Alembic ``target_metadata``
(they wire ``include_object`` from ``varco_sa.migration.env_template``
instead).

DESIGN: raw Core ``Table``, not an ORM/``DomainModel`` mapping
    ✅ Same rationale as ``varco_sa.dlq``/``varco_sa.job_store`` — this is
       an infrastructure table, not an application entity generated via
       ``SAModelFactory``. ``TenantDescriptor`` (``varco_core.tenancy.
       catalog``) is a plain frozen dataclass, not a ``DomainModel``.
    ✅ Own isolated ``MetaData`` — never pollutes the application's
       ``Base.metadata`` (same pattern as every other framework table).

Conceptually ``TenantScope.GLOBAL`` — the catalog itself is control-plane
data, never routed per-tenant; there is no ``ParsedMeta`` to force this
through since this table is not built via ``SAModelFactory``.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table

from varco_sa.metadata import register_framework_metadata

# Separate MetaData — never pollutes the application's Base.metadata, same
# pattern as varco_dead_letters / varco_jobs / varco_outbox.
_metadata = MetaData()

tenants_table = Table(
    "varco_tenants",
    _metadata,
    Column("tenant_id", String(255), primary_key=True),
    Column("schema_name", String(255), nullable=True),
    Column("database_name", String(255), nullable=True),
    Column("dsn_ref", String(1024), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Expose metadata for Alembic integration — include in target_metadata (or,
# for apps, rely on the "varco" branch to own it automatically).
tenants_metadata = _metadata

register_framework_metadata("varco_sa.tenancy", tenants_metadata)

__all__ = ["tenants_table", "tenants_metadata"]
