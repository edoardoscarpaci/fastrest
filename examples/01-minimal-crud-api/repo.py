"""
repo
====
In-memory repository, UoW, and UoW provider for the ``Product`` entity.

These implementations replace a real database backend (SQLAlchemy, Beanie)
for this minimal example — no Docker or external service required.

``InMemoryProductRepository``
    Stores products in a plain Python dict (``pk → Product``).  All CRUD
    operations are synchronous internally but wrapped in ``async def`` to
    satisfy the ``AsyncRepository`` contract.

``InMemoryUoW``
    A trivial UoW that holds a reference to the shared repository.
    Commit and rollback are no-ops — changes are visible immediately.
    Accepts the same ``uow.products`` attribute access pattern as the
    SQLAlchemy UoW so services work without change.

``InMemoryUoWProvider``
    Creates ``InMemoryUoW`` instances that all share the same underlying
    repository dict, so data persists across requests within a process.

DESIGN: InMemoryUoWProvider shares one _repo instance
    ✅ Data created in one request is visible in the next — realistic
       behaviour for a demo without a database.
    ✅ ``_Impl()`` in ``di.py`` creates one provider at bootstrap time
       and the ``@Provider`` returns the same instance every call.
    ❌ Not thread-safe — safe for a single-process ASGI demo, not for
       production multi-threaded use.

DESIGN: ``object.__setattr__`` to mutate frozen-like fields
    ``AuditedDomainModel`` fields ``pk``, ``created_at``, ``updated_at``
    are ``init=False`` — the dataclass constructor does not accept them.
    The standard mutation path for ``init=False`` fields on a dataclass is
    ``object.__setattr__(obj, field, value)``, which bypasses the frozen guard.
    ``DomainModel`` is not declared ``frozen=True`` but uses ``init=False``
    for framework-managed fields — ``object.__setattr__`` is the documented
    mutation path throughout the varco codebase (e.g. SA mapper, pk_field).

Thread safety:  ⚠️ Single-process demo — no concurrent-write protection.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

from models import Product
from varco_core.repository import AsyncRepository
from varco_core.service.base import IUoWProvider
from varco_core.uow import AsyncUnitOfWork

if False:  # TYPE_CHECKING
    from varco_core.query.params import QueryParams


class InMemoryProductRepository(AsyncRepository[Product, UUID]):
    """
    In-memory async CRUD repository for ``Product`` entities.

    Stores all products in a dict keyed by UUID.  All query methods
    (``find_by_query``, ``count``, ``exists``, ``stream_by_query``,
    ``save_many``, ``delete_many``, ``update_many_by_query``) have minimal
    implementations sufficient for the smoke tests — production usage should
    use ``varco_sa`` or ``varco_beanie`` backends.

    Class attributes:
        _store: ``{UUID: Product}`` — shared across all instances that
                reference the same dict object (passed in via constructor).

    Thread safety:  ⚠️ Dict operations are GIL-protected but not safe under
                       concurrent async writes to the same key.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, store: dict[UUID, Product]) -> None:
        """
        Args:
            store: Shared ``{UUID: Product}`` dict.  All UoW instances
                   referencing this repository share the same dict so data
                   persists across requests.
        """
        # Shared dict reference — mutations here are immediately visible
        # in any other repository pointing at the same dict.
        self._store = store

    async def find_by_id(self, pk: UUID | str) -> Product | None:
        """
        Retrieve a product by primary key.

        Args:
            pk: UUID (or UUID string) of the product.

        Returns:
            The ``Product``, or ``None`` if not found.
        """
        if isinstance(pk, str):
            pk = UUID(pk)
        return self._store.get(pk)

    async def find_all(self) -> list[Product]:
        """
        Retrieve all products with no filtering.

        Returns:
            List of all products.  Empty when the store is empty.
        """
        return list(self._store.values())

    async def save(self, entity: Product) -> Product:
        """
        Persist ``entity`` (INSERT or UPDATE).

        INSERT: ``entity.pk is None`` — assigns a new UUID and sets
                ``created_at`` / ``updated_at`` to ``datetime.now(UTC)``.
        UPDATE: ``entity.pk is not None`` — updates ``updated_at`` only.

        Args:
            entity: The product to persist.

        Returns:
            The persisted product with ``pk`` and timestamps set.
            Always use the returned value — the input is never mutated.

        Edge cases:
            - ``object.__setattr__`` is used to mutate ``init=False`` fields
              (``pk``, ``created_at``, ``updated_at``) on the dataclass
              without violating the dataclass contract.
        """
        now = datetime.now(UTC)

        if entity.pk is None:
            # INSERT path — assign pk and set both timestamps.
            object.__setattr__(entity, "pk", uuid4())
            object.__setattr__(entity, "created_at", now)

        # Always refresh updated_at — mirrors the SA ORM ``onupdate`` behaviour.
        object.__setattr__(entity, "updated_at", now)

        self._store[entity.pk] = entity
        return entity

    async def delete(self, entity: Product) -> None:
        """
        Delete the product from the store.

        Args:
            entity: The product to delete.

        Raises:
            ValueError: Entity has not been persisted yet (``pk is None``).
        """
        if entity.pk is None:
            raise ValueError(
                "Cannot delete an unpersisted Product — pk is None. "
                "Call save() before delete()."
            )
        pk = UUID(entity.pk) if isinstance(entity.pk, str) else entity.pk
        self._store.pop(pk, None)

    async def find_by_query(self, params: QueryParams) -> list[Product]:
        """
        Minimal query implementation — returns all products.

        In a production backend this would apply the ``QueryParams`` AST
        filter, sort, and pagination.  For this example it returns the
        full store so ``ListMixin`` works without a full query engine.

        Args:
            params: ``QueryParams`` (ignored in this implementation).

        Returns:
            All products in the store.
        """
        # Pagination via limit/offset so list endpoints feel realistic.
        items = list(self._store.values())
        offset = params.offset or 0
        if params.limit is not None:
            return items[offset : offset + params.limit]
        return items[offset:]

    async def count(self, params: QueryParams | None = None) -> int:
        """
        Count all products in the store.

        Args:
            params: Ignored in this in-memory implementation.

        Returns:
            Total number of products.
        """
        return len(self._store)

    async def exists(self, pk: UUID | str) -> bool:
        """
        Return ``True`` if a product with ``pk`` exists.

        Args:
            pk: Product UUID (or UUID string).

        Returns:
            ``True`` if found, ``False`` otherwise.
        """
        if isinstance(pk, str):
            pk = UUID(pk)
        return pk in self._store

    async def save_many(self, entities: Sequence[Product]) -> list[Product]:
        """
        Bulk save — delegates to ``save()`` for each entity.

        Args:
            entities: Products to persist.

        Returns:
            List of persisted products in input order.

        Edge cases:
            - Empty sequence → returns ``[]`` without modifying the store.
        """
        return [await self.save(e) for e in entities]

    async def delete_many(self, entities: Sequence[Product]) -> None:
        """
        Bulk delete — delegates to ``delete()`` for each entity.

        Args:
            entities: Persisted products to delete.

        Raises:
            ValueError: Any entity without a ``pk``.
        """
        for entity in entities:
            await self.delete(entity)

    async def update_many_by_query(
        self,
        params: QueryParams,
        update: dict[str, Any],
    ) -> int:
        """
        Bulk field update — not implemented for this example.

        Raises:
            NotImplementedError: Always.  Not needed for the demo.
        """
        raise NotImplementedError(
            "update_many_by_query is not implemented in InMemoryProductRepository. "
            "Use save() for individual updates."
        )

    def stream_by_query(self, params: QueryParams) -> AsyncIterator[Product]:
        """
        Async generator — yields each product one at a time.

        Args:
            params: ``QueryParams`` (offset/limit respected; filter ignored).

        Returns:
            An async iterator over matching products.
        """

        # Defined as a synchronous method returning an async generator, matching
        # the abstract method contract in AsyncRepository (plain def returning
        # AsyncIterator[D]).  The async def / yield form makes it an async
        # generator, which is an AsyncIterator subtype.
        async def _gen() -> AsyncIterator[Product]:  # type: ignore[override]
            items = await self.find_by_query(params)
            for item in items:
                yield item

        return _gen()


class InMemoryUoW(AsyncUnitOfWork):
    """
    Trivial in-memory unit of work.

    Exposes the shared ``InMemoryProductRepository`` as ``uow.products``,
    matching the attribute-access pattern used by ``ProductService._get_repo()``.

    Commit and rollback are no-ops — the in-memory store has no transaction
    concept and changes are immediately durable.

    Thread safety:  ⚠️ No isolation — all UoW instances share the same store.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, repo: InMemoryProductRepository) -> None:
        """
        Args:
            repo: Shared ``InMemoryProductRepository`` — all UoW instances
                  must reference the same repo so data persists between requests.
        """
        # Exposed as ``uow.products`` so ``ProductService._get_repo()`` can
        # return ``uow.products`` without knowing the UoW concrete type.
        self.products = repo

    async def _begin(self) -> None:
        """No-op — in-memory store needs no transaction open."""

    async def commit(self) -> None:
        """No-op — in-memory changes are always immediately visible."""

    async def rollback(self) -> None:
        """No-op — in-memory store has no rollback mechanism."""


class InMemoryUoWProvider(IUoWProvider):
    """
    UoW provider that returns ``InMemoryUoW`` instances backed by a
    single shared ``InMemoryProductRepository``.

    All UoW instances share the same underlying ``_repo`` dict so data
    written in one request is visible in subsequent requests — essential
    for a realistic CRUD demo without a database.

    DESIGN: provider creates the repo once in ``__init__``
        ✅ Single shared store across all UoW instances — data persists.
        ✅ ``di.py`` creates one provider at bootstrap and the ``@Provider``
           caches it — only one repo dict is ever created.
        ❌ No isolation between concurrent requests — acceptable for demo.

    Thread safety:  ⚠️ Shared mutable dict — not safe for concurrent writes.
    Async safety:   ✅ ``make_uow()`` is synchronous per ``IUoWProvider`` contract.
    """

    def __init__(self) -> None:
        """Initialise with a fresh empty product store."""
        # Single dict instance — all UoW instances returned by make_uow()
        # share this same dict, so data survives across request boundaries.
        self._store: dict[UUID, Product] = {}

    def make_uow(self) -> InMemoryUoW:
        """
        Return a fresh ``InMemoryUoW`` wrapping the shared product store.

        Returns:
            A new ``InMemoryUoW`` instance.  All instances share the same
            underlying dict — mutations are immediately visible across all UoW
            instances.

        Edge cases:
            - The returned UoW has no transaction isolation — ``rollback()``
              is a no-op.  Changes made before an exception propagates are
              NOT undone.
        """
        # Create a new UoW wrapper each call but pass the shared dict —
        # the repo object is cheap to create; only the dict matters for
        # state persistence.
        return InMemoryUoW(InMemoryProductRepository(self._store))


__all__ = ["InMemoryProductRepository", "InMemoryUoW", "InMemoryUoWProvider"]
