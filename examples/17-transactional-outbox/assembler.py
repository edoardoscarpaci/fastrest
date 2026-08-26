"""
assembler.py
============
DTO ↔ domain model translation for ``Order`` entities.

``OrderAssembler`` is a stateless singleton that maps between HTTP-layer DTOs
and the ``Order`` domain dataclass.  It is the only place where field-to-field
mapping logic lives — no service or repository touches DTO fields directly.

DESIGN: domain_replace() over dataclasses.replace()
    ``domain_replace()`` from ``varco_core.model`` preserves all ``init=False``
    fields (``pk``, ``_raw_orm``, ``created_at``, ``updated_at``) from the
    original entity.  On Python ≤ 3.12, plain ``dataclasses.replace()`` resets
    those fields to defaults, causing the repository to do INSERT instead of
    UPDATE.

    ✅ Correct INSERT vs UPDATE detection in the SA repository.
    ✅ No manual ``object.__setattr__`` calls.
    ❌ Requires ``varco_core.model.domain_replace`` instead of stdlib.

Thread safety:  ✅ Stateless — safe to share across concurrent requests.
Async safety:   ✅ All methods are synchronous; no I/O.
"""

from __future__ import annotations

from dtos import OrderCreate, OrderRead, OrderUpdate
from models import Order
from providify import Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace


@Singleton
class OrderAssembler(AbstractDTOAssembler[Order, OrderCreate, OrderRead, OrderUpdate]):
    """
    Assembler for the ``Order`` entity.

    Registered as a ``@Singleton`` so the DI container injects one shared
    instance into ``OrderService``.

    Thread safety:  ✅ Stateless.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: OrderCreate) -> Order:
        """
        Map ``OrderCreate`` → fresh, unpersisted ``Order``.

        ``pk`` is left unset (``pk_field()``); the repository assigns it on
        INSERT via ``PKStrategy.UUID_AUTO``.  ``status`` defaults to
        ``"pending"`` — only the service layer may change it.

        Args:
            dto: Validated HTTP request body.

        Returns:
            An unpersisted ``Order`` with ``amount`` set and ``status="pending"``.
        """
        return Order(amount=dto.amount)

    def to_read_dto(self, entity: Order) -> OrderRead:
        """
        Map a persisted ``Order`` → ``OrderRead`` response DTO.

        Args:
            entity: A persisted ``Order`` with all fields populated.

        Returns:
            ``OrderRead`` with ``pk``, ``amount``, ``status``, timestamps.
        """
        return OrderRead(
            pk=entity.pk,
            amount=entity.amount,
            status=entity.status,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    def apply_update(self, entity: Order, dto: OrderUpdate) -> Order:
        """
        Apply ``OrderUpdate`` fields to ``entity`` and return a new ``Order``.

        Uses ``domain_replace()`` to preserve ``init=False`` fields (``pk``,
        ``_raw_orm``) so the repository issues an UPDATE, not an INSERT.

        ``None`` fields in the DTO mean "no change" — only non-None values
        overwrite the current entity value.

        Args:
            entity: Current persisted state of the order.
            dto:    ``OrderUpdate`` payload with optional new field values.

        Returns:
            A new ``Order`` with updated fields and preserved identity fields.
        """
        changes = {
            k: v
            for k, v in {"amount": dto.amount, "status": dto.status}.items()
            if v is not None
        }
        return domain_replace(entity, **changes)


__all__ = ["OrderAssembler"]
