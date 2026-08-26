"""
models
======
Domain model for the ``Note`` entity.

``Note`` inherits from ``SoftDeleteAuditedDomainModel`` — the recommended base
for multi-tenant, soft-deletable entities.  The inheritance chain gives us:

    DomainModel              (pk, _raw_orm)
    AuditedDomainModel       (created_at, updated_at)
    SoftDeleteMixin          (deleted_at)
    └── SoftDeleteAuditedDomainModel

``tenant_id`` is declared directly on ``Note`` with ``FieldHint(index=True)``
so the generated SQLAlchemy column is indexed for efficient per-tenant queries.

DESIGN: ``SoftDeleteAuditedDomainModel`` over manual fields
    ✅ ``deleted_at``, ``created_at``, ``updated_at`` are provided automatically.
    ✅ ``SoftDeleteService`` reads ``_soft_delete_field = "deleted_at"`` which
       matches the mixin's field name — no extra configuration needed.
    ✅ Audit timestamps come for free.
    ❌ The inheritance chain is longer than a plain ``DomainModel`` — minor
       cognitive overhead that's worth it for the behaviour provided.

DESIGN: ``tenant_id`` as a plain ``str`` (not UUID or foreign key)
    ✅ Flexible — works with any tenant identifier scheme.
    ✅ No foreign-key cascade to worry about.
    ❌ No referential integrity enforced at the DB level — the service layer
       (TenantAwareService) is the authoritative enforcement point.

Thread safety:  ✅ Dataclass — each instance is independent.
Async safety:   ✅ Pure value object — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from varco_core.meta import FieldHint, PKStrategy, PrimaryKey, pk_field
from varco_core.model import SoftDeleteAuditedDomainModel


@dataclass(kw_only=True)
class Note(SoftDeleteAuditedDomainModel):
    """
    Note entity with tenant isolation and soft deletion.

    Fields:
        pk:         UUID, auto-generated on first ``save()``.
        tenant_id:  Identifies which tenant owns this note.  Stamped from
                    ``ctx.metadata["tenant_id"]`` by ``TenantAwareService``;
                    the assembler never sets it.
        title:      Short title for the note — must not be blank (validated).
        content:    Note body text.
        created_at: UTC timestamp set on first ``save()``.  Inherited.
        updated_at: UTC timestamp refreshed on each ``save()``.  Inherited.
        deleted_at: ``None`` for active notes; UTC datetime when soft-deleted.
                    Inherited from ``SoftDeleteMixin``.

    Edge cases:
        - ``pk`` is ``None`` until the repository assigns one on INSERT.
        - ``tenant_id`` is an empty string until ``TenantAwareService`` stamps
          it — assembler must not depend on it being set.
        - ``deleted_at`` is always ``None`` on creation regardless of the
          constructor argument; ``SoftDeleteService._prepare_for_create``
          resets it.

    Thread safety:  ✅ Dataclass — each instance is independent.
    Async safety:   ✅ Pure value object — no I/O.
    """

    # UUID primary key, auto-assigned by the repository on INSERT.
    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()

    # Tenant identifier — indexed for fast per-tenant filtering.
    # DESIGN: FieldHint(index=True, nullable=False)
    #   ✅ DB index enables efficient ``WHERE tenant_id = ?`` queries.
    #   ✅ NOT NULL constraint prevents accidental null-tenant inserts.
    #   ❌ Index slightly slows INSERT/UPDATE — negligible for note-volume data.
    tenant_id: Annotated[str, FieldHint(index=True, nullable=False)] = ""

    # Note title — validated to be non-blank at service layer.
    title: Annotated[str, FieldHint(max_length=255)] = ""

    # Note body — no length limit; stored as TEXT in Postgres.
    content: str = ""

    class Meta:
        # SQLAlchemy table name — snake_case of the class name.
        table = "notes"


__all__ = ["Note"]
