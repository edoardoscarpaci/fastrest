"""
assembler
=========
DTO ↔ domain model translations for the ``Product`` entity.

``ProductAssembler`` is a stateless singleton — all three mapping methods
are pure functions of their inputs.  It is the only place where
field-to-field mapping logic lives; services and repositories never touch
DTO fields directly.

DESIGN: ``apply_update`` uses ``domain_replace()`` not ``dataclasses.replace()``
    ``domain_replace()`` copies ``init=False`` fields (``pk``, ``_raw_orm``,
    ``created_at``, ``updated_at``) from the original entity.  On Python ≤ 3.12,
    plain ``dataclasses.replace()`` resets those fields to their dataclass
    defaults — ``pk`` becomes ``None`` and the repository would silently INSERT
    a duplicate row instead of updating the existing one.

    ✅ No manual ``object.__setattr__`` in assembler implementations.
    ✅ Future ``init=False`` fields on the domain model are handled automatically.
    ❌ ``domain_replace()`` is specific to ``varco_core`` — a minor import cost.

Thread safety:  ✅ Stateless — all methods are pure transformations.
Async safety:   ✅ No I/O — all methods are synchronous.
"""

from __future__ import annotations

from dtos import ProductCreate, ProductRead, ProductUpdate
from models import Product
from providify import Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace


@Singleton
class ProductAssembler(AbstractDTOAssembler[Product, ProductCreate, ProductRead, ProductUpdate]):
    """
    Assembler for the ``Product`` entity.

    Registered as a ``@Singleton`` — the DI container injects a single
    shared instance into every ``ProductService`` instance.

    Thread safety:  ✅ Stateless — safe to share across all concurrent requests.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: ProductCreate) -> Product:
        """
        Map ``ProductCreate`` → fresh, unpersisted ``Product``.

        ``pk`` is left unset (``pk_field(init=False)``) — the in-memory
        repository assigns a UUID on the first ``save()``.
        ``created_at`` / ``updated_at`` are also unset — the repository
        manages them.

        Args:
            dto: Validated ``ProductCreate`` payload from the HTTP layer.

        Returns:
            An unpersisted ``Product`` with all business fields set.
            ``pk`` and timestamps are ``None`` — set by the repository.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` — the repository
              treats this as an INSERT, not an UPDATE.
        """
        return Product(
            name=dto.name,
            description=dto.description,
            price=dto.price,
            in_stock=dto.in_stock,
        )

    def to_read_dto(self, entity: Product) -> ProductRead:
        """
        Map a persisted ``Product`` → ``ProductRead`` response.

        Called after every ``save()``, ``find_by_id()``, and
        ``find_by_query()`` to produce the value returned to the caller.

        Args:
            entity: A persisted ``Product`` with all fields populated.

        Returns:
            A ``ProductRead`` with all fields from the entity.

        Edge cases:
            - ``updated_at`` falls back to ``created_at`` when the field is
              ``None`` (e.g. a just-inserted entity whose ``updated_at`` was
              not set by the repo before this call).  In practice the
              in-memory repo always sets ``updated_at`` — this guard exists
              for correctness.
        """
        return ProductRead(
            pk=entity.pk,
            name=entity.name,
            description=entity.description,
            price=entity.price,
            in_stock=entity.in_stock,
            created_at=entity.created_at,
            # Guard: fall back to created_at so updated_at is never None
            # in the response, even on a brand-new entity where the repo has
            # not yet refreshed the field.
            updated_at=(entity.updated_at if entity.updated_at is not None else entity.created_at),
        )

    def apply_update(self, entity: Product, dto: ProductUpdate) -> Product:
        """
        Apply ``ProductUpdate`` fields onto ``entity`` and return a new ``Product``.

        Only fields present in ``dto`` (not ``None``) are changed.

        Uses ``domain_replace()`` not plain ``dataclasses.replace()`` — see
        module-level DESIGN note for why this matters on Python ≤ 3.12.

        Args:
            entity: Current persisted state of the product.
            dto:    ``ProductUpdate`` payload — ``None`` fields mean "no change".

        Returns:
            A new ``Product`` with the update applied.  All ``init=False``
            fields (``pk``, ``_raw_orm``, timestamps) are copied from ``entity``
            so the repository performs an UPDATE, not an INSERT.

        Edge cases:
            - Sending all-None ``ProductUpdate`` produces an identical copy —
              a no-op UPDATE query.
        """
        return domain_replace(
            entity,
            name=dto.name if dto.name is not None else entity.name,
            description=(dto.description if dto.description is not None else entity.description),
            price=dto.price if dto.price is not None else entity.price,
            in_stock=dto.in_stock if dto.in_stock is not None else entity.in_stock,
        )


__all__ = ["ProductAssembler"]
