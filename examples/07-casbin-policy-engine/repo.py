"""
repo
====
In-memory repository, UoW, and UoW provider for the ``Document`` entity.

These implementations replace a real database backend for this example —
only the Casbin policy store uses PostgreSQL (via the sqlalchemy adapter).

DESIGN: in-memory store for documents, Postgres only for policy rules
    The example focuses on teaching the Casbin policy engine.  Using a real
    database for the document store would require SA model generation and SA
    config, adding noise that distracts from the policy-engine integration.

    ✅ Test isolation is easy — each fixture creates a fresh container.
    ✅ No SQLAlchemy session management needed for the domain layer.
    ❌ Data is lost when the process exits — not a production pattern.

Thread safety:  ⚠️ Single-process demo — no concurrent-write protection.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from models import Document
from varco_core.repository import AsyncRepository
from varco_core.service.base import IUoWProvider
from varco_core.uow import AsyncUnitOfWork

if False:  # TYPE_CHECKING — avoids import at runtime to keep repo framework-free
    from varco_core.query.params import QueryParams


class InMemoryDocumentRepository(AsyncRepository[Document, UUID]):
    """
    In-memory async CRUD repository for ``Document`` entities.

    Stores all documents in a dict keyed by UUID.  Query methods have minimal
    implementations sufficient for the smoke tests (list/count/exists).

    Thread safety:  ⚠️ Dict operations are GIL-protected but not safe under
                       concurrent async writes to the same key.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, store: dict[UUID, Document]) -> None:
        """
        Args:
            store: Shared ``{UUID: Document}`` dict.  All UoW instances
                   referencing this repository share the same dict so data
                   persists across requests.
        """
        # Shared dict reference — mutations are immediately visible to all
        # UoW instances pointing at the same dict.
        self._store = store

    async def find_by_id(self, pk: UUID | str) -> Document | None:
        """
        Retrieve a document by primary key.

        Args:
            pk: UUID (or UUID string) of the document.

        Returns:
            The ``Document``, or ``None`` if not found.
        """
        if isinstance(pk, str):
            pk = UUID(pk)
        return self._store.get(pk)

    async def find_all(self) -> list[Document]:
        """Return all documents. Empty when the store is empty."""
        return list(self._store.values())

    async def save(self, entity: Document) -> Document:
        """
        Persist ``entity`` (INSERT or UPDATE).

        INSERT: ``entity.pk is None`` — assigns a new UUID and sets timestamps.
        UPDATE: ``entity.pk is not None`` — refreshes ``updated_at`` only.

        Args:
            entity: The document to persist.

        Returns:
            The persisted document with ``pk`` and timestamps set.

        Edge cases:
            - ``object.__setattr__`` mutates ``init=False`` fields on the
              dataclass without violating the dataclass contract.
        """
        now = datetime.now(UTC)
        if entity.pk is None:
            # INSERT path — assign pk and both timestamps.
            object.__setattr__(entity, "pk", uuid4())
            object.__setattr__(entity, "created_at", now)
        # Always refresh updated_at — mirrors SA ORM ``onupdate`` behaviour.
        object.__setattr__(entity, "updated_at", now)
        self._store[entity.pk] = entity
        return entity

    async def delete(self, entity: Document) -> None:
        """
        Delete the document from the store.

        Args:
            entity: The persisted document to delete.

        Raises:
            ValueError: Entity has not been persisted (``pk is None``).
        """
        if entity.pk is None:
            raise ValueError("Cannot delete an unpersisted Document — pk is None.")
        pk = UUID(str(entity.pk)) if not isinstance(entity.pk, UUID) else entity.pk
        self._store.pop(pk, None)

    async def find_by_query(self, params: QueryParams) -> list[Document]:
        """
        Minimal query — returns all documents with limit/offset applied.

        Args:
            params: ``QueryParams`` (only limit/offset respected).

        Returns:
            Paginated list of documents.
        """
        items = list(self._store.values())
        offset = params.offset or 0
        if params.limit is not None:
            return items[offset : offset + params.limit]
        return items[offset:]

    async def count(self, params: QueryParams | None = None) -> int:
        """Count all documents (params ignored)."""
        return len(self._store)

    async def exists(self, pk: UUID | str) -> bool:
        """Return ``True`` if a document with ``pk`` exists."""
        if isinstance(pk, str):
            pk = UUID(pk)
        return pk in self._store

    async def save_many(self, entities: Sequence[Document]) -> list[Document]:
        """Bulk save — delegates to ``save()`` for each entity."""
        return [await self.save(e) for e in entities]

    async def delete_many(self, entities: Sequence[Document]) -> None:
        """Bulk delete — delegates to ``delete()`` for each entity."""
        for entity in entities:
            await self.delete(entity)

    async def update_many_by_query(
        self,
        params: QueryParams,
        update: dict[str, Any],
    ) -> int:
        """Not implemented — not needed for this example."""
        raise NotImplementedError(
            "update_many_by_query is not implemented in InMemoryDocumentRepository."
        )

    def stream_by_query(self, params: QueryParams) -> AsyncIterator[Document]:
        """Async generator — yields each document one at a time."""

        async def _gen() -> AsyncIterator[Document]:  # type: ignore[override]
            items = await self.find_by_query(params)
            for item in items:
                yield item

        return _gen()


class InMemoryUoW(AsyncUnitOfWork):
    """
    Trivial in-memory unit of work.

    Exposes ``uow.documents`` so ``DocumentService._get_repo()`` can retrieve
    the repository via attribute access.

    Thread safety:  ⚠️ No isolation — all UoW instances share the same store.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, repo: InMemoryDocumentRepository) -> None:
        """
        Args:
            repo: Shared ``InMemoryDocumentRepository`` instance.
        """
        # Exposed as ``uow.documents`` for DocumentService._get_repo()
        self.documents = repo

    async def _begin(self) -> None:
        """No-op — in-memory store needs no transaction."""

    async def commit(self) -> None:
        """No-op — in-memory changes are always immediately visible."""

    async def rollback(self) -> None:
        """No-op — in-memory store has no rollback mechanism."""


class InMemoryUoWProvider(IUoWProvider):
    """
    UoW provider returning ``InMemoryUoW`` instances that share one store.

    Thread safety:  ⚠️ Shared mutable dict — not safe for concurrent writes.
    Async safety:   ✅ ``make_uow()`` is synchronous per the contract.
    """

    def __init__(self) -> None:
        """Initialise with a fresh empty document store."""
        # Single dict — all UoW instances share this, so data persists.
        self._store: dict[UUID, Document] = {}

    def make_uow(self) -> InMemoryUoW:
        """
        Return a fresh ``InMemoryUoW`` wrapping the shared document store.

        Returns:
            A new ``InMemoryUoW``.  All instances share the same underlying
            dict — mutations are immediately visible across all UoW instances.

        Edge cases:
            - ``rollback()`` is a no-op — changes made before an exception
              are NOT undone.
        """
        return InMemoryUoW(InMemoryDocumentRepository(self._store))


__all__ = ["InMemoryDocumentRepository", "InMemoryUoW", "InMemoryUoWProvider"]
