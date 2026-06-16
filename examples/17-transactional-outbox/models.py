"""
models.py
=========
Domain model for the ``17-transactional-outbox`` example.

``Order`` is a minimal entity with an amount and status field.  It is
persisted via ``varco_sa``; the outbox pattern guarantees that every
created order emits an ``OrderCreatedEvent`` to the bus even if the
broker is temporarily unavailable at commit time.

DESIGN: AuditedDomainModel for automatic timestamps
    ✅ ``created_at`` / ``updated_at`` are stamped by the SA mapper —
       no manual timestamp code in the service layer.
    ✅ ``_raw_orm`` field is ``init=False`` — ``domain_replace()`` must be
       used when cloning entities to preserve this field on Python ≤ 3.12.
    ❌ Timestamps are Python-side, not DB-side — no ``DEFAULT NOW()`` in the
       schema.

Thread safety:  ✅ Immutable ``@dataclass`` value object after construction.
Async safety:   ✅ Pure value object; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from varco_core.meta import FieldHint, PrimaryKey, PKStrategy, pk_field
from varco_core.model import AuditedDomainModel


@dataclass(kw_only=True)
class Order(AuditedDomainModel):
    """
    Domain model for a customer order.

    Attributes:
        pk:     UUID primary key, auto-assigned on INSERT via
                ``PKStrategy.UUID_AUTO``.
        amount: Order total in arbitrary currency units (positive float).
        status: Lifecycle state — ``"pending"`` on creation, ``"fulfilled"``
                after processing.  Stored as a plain VARCHAR.

    Edge cases:
        - ``amount`` has no DB-level constraint here — validate in the service
          layer or add a CHECK constraint via ``FieldHint`` in production.
        - ``status`` is a free-form string in this example.  In production,
          use an Enum column or a DB CHECK constraint.
        - ``_raw_orm`` (``init=False``) is populated by the SA mapper on
          load.  Never pass it in constructors; use ``domain_replace()``
          when cloning.

    Thread safety:  ✅ Immutable after construction.
    Async safety:   ✅ Pure value object.
    """

    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()

    # Order total — positive decimal in practice; no DB constraint here.
    amount: Annotated[float, FieldHint()] = 0.0

    # Lifecycle: "pending" → "fulfilled"
    status: Annotated[str, FieldHint(max_length=32)] = "pending"

    class Meta:
        table = "orders"


__all__ = ["Order"]
