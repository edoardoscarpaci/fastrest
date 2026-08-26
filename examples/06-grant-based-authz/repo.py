"""
repo
====
In-memory repository, UoW, and UoW provider for the ``Document`` entity.

These implementations replace a real database backend for this example —
no Docker or external service required.

``InMemoryDocumentRepository``
    Stores documents in a plain ``{UUID: Document}`` dict.  All CRUD
    operations are synchronous internally but wrapped in ``async def`` to
    satisfy the ``AsyncRepository`` contract.

``InMemoryUoW``
    Trivial UoW that holds a reference to the shared repository.
    Commit and rollback are no-ops — changes are immediately visible.
    Exposes ``uow.documents`` so ``DocumentService._get_repo()`` can
    retrieve the repo without knowing the concrete UoW type.

``InMemoryUoWProvider``
    Creates ``InMemoryUoW`` instances that all share the same underlying
    repository dict, so data persists across requests within a process.

DESIGN: single shared dict in ``InMemoryUoWProvider``
    ✅ Data created in one request is visible in the next — realistic
       behaviour for a demo without a database.
    ✅ ``di.py`` creates one provider at bootstrap and the ``@Provider``
       caches it — only one repo dict is ever created.
    ❌ Not thread-safe — safe for a single-process ASGI demo, not for
       production multi-threaded use.

Thread safety:  ⚠️ Single-process demo — no concurrent-write protection.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, AsyncIterator, Sequence
from uuid import UUID, uuid4

from models import Document
from varco_core.repository import AsyncRepository
from varco_core.service.base import IUoWProvider
from varco_core.uow import AsyncUnitOfWork

if False:  # TYPE_CHECKING
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
        # Shared dict reference — mutations here are immediately visible
        # in any other repository pointing at the same dict.
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
        """
        Retrieve all documents with no filtering.

        Returns:
            List of all documents.  Empty when the store is empty.
        """
        return list(self._store.values())

    async def save(self, entity: Document) -> Document:
        """
        Persist ``entity`` (INSERT or UPDATE).

        INSERT: ``entity.pk is None`` — assigns a new UUID and sets
                ``created_at`` / ``updated_at`` to ``datetime.now(UTC)``.
        UPDATE: ``entity.pk is not None`` — updates ``updated_at`` only.

        Args:
            entity: The document to persist.

        Returns:
            The persisted document with ``pk`` and timestamps set.

        Edge cases:
            - ``object.__setattr__`` is used to mutate ``init=False`` fields
              (``pk``, ``created_at``, ``updated_at``) on the dataclass
              without violating the dataclass contract.
            - ``owner_id`` is NOT modified here — the service stamps it
              before calling ``save()`` via ``_prepare_for_create``.
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

    async def delete(self, entity: Document) -> None:
        """
        Delete the document from the store.

        Args:
            entity: The document to delete.

        Raises:
            ValueError: Entity has not been persisted yet (``pk is None``).
        """
        if entity.pk is None:
            raise ValueError(
                "Cannot delete an unpersisted Document — pk is None. "
                "Call save() before delete()."
            )
        pk = UUID(str(entity.pk)) if not isinstance(entity.pk, UUID) else entity.pk
        self._store.pop(pk, None)

    async def find_by_query(self, params: QueryParams) -> list[Document]:
        """
        Minimal query implementation — returns all documents with pagination.

        Args:
            params: ``QueryParams`` (only limit/offset are respected).

        Returns:
            Paginated list of documents.
        """
        items = list(self._store.values())
        offset = params.offset or 0
        if params.limit is not None:
            return items[offset : offset + params.limit]
        return items[offset:]

    async def count(self, params: QueryParams | None = None) -> int:
        """
        Count all documents in the store.

        Args:
            params: Ignored in this in-memory implementation.

        Returns:
            Total number of documents.
        """
        return len(self._store)

    async def exists(self, pk: UUID | str) -> bool:
        """
        Return ``True`` if a document with ``pk`` exists.

        Args:
            pk: Document UUID (or UUID string).

        Returns:
            ``True`` if found, ``False`` otherwise.
        """
        if isinstance(pk, str):
            pk = UUID(pk)
        return pk in self._store

    async def save_many(self, entities: Sequence[Document]) -> list[Document]:
        """
        Bulk save — delegates to ``save()`` for each entity.

        Args:
            entities: Documents to persist.

        Returns:
            List of persisted documents in input order.
        """
        return [await self.save(e) for e in entities]

    async def delete_many(self, entities: Sequence[Document]) -> None:
        """
        Bulk delete — delegates to ``delete()`` for each entity.

        Args:
            entities: Persisted documents to delete.
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
            "update_many_by_query is not implemented in InMemoryDocumentRepository. "
            "Use save() for individual updates."
        )

    def stream_by_query(self, params: QueryParams) -> AsyncIterator[Document]:
        """
        Async generator — yields each document one at a time.

        Args:
            params: ``QueryParams`` (offset/limit respected; filter ignored).

        Returns:
            An async iterator over matching documents.
        """

        async def _gen() -> AsyncIterator[Document]:  # type: ignore[override]
            items = await self.find_by_query(params)
            for item in items:
                yield item

        return _gen()


class InMemoryUoW(AsyncUnitOfWork):
    """
    Trivial in-memory unit of work.

    Exposes ``uow.documents`` so ``DocumentService._get_repo()`` can retrieve
    the repository via attribute access, matching the SA UoW convention.

    Thread safety:  ⚠️ No isolation — all UoW instances share the same store.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, repo: InMemoryDocumentRepository) -> None:
        """
        Args:
            repo: Shared ``InMemoryDocumentRepository`` — all UoW instances
                  must reference the same repo so data persists between requests.
        """
        # Exposed as ``uow.documents`` so DocumentService._get_repo() can
        # return ``uow.documents`` without knowing the UoW concrete type.
        self.documents = repo

    async def _begin(self) -> None:
        """No-op — in-memory store needs no transaction open."""

    async def commit(self) -> None:
        """No-op — in-memory changes are always immediately visible."""

    async def rollback(self) -> None:
        """No-op — in-memory store has no rollback mechanism."""


class InMemoryUoWProvider(IUoWProvider):
    """
    UoW provider that returns ``InMemoryUoW`` instances backed by a
    single shared ``InMemoryDocumentRepository``.

    DESIGN: provider creates the repo once in ``__init__``
        ✅ Single shared store across all UoW instances — data persists.
        ✅ ``di.py`` creates one provider at bootstrap and the ``@Provider``
           caches it — only one repo dict is ever created.
        ❌ No isolation between concurrent requests — acceptable for demo.

    Thread safety:  ⚠️ Shared mutable dict — not safe for concurrent writes.
    Async safety:   ✅ ``make_uow()`` is synchronous per ``IUoWProvider`` contract.
    """

    def __init__(self) -> None:
        """Initialise with a fresh empty document store."""
        # Single dict instance — all UoW instances returned by make_uow()
        # share this same dict, so data survives across request boundaries.
        self._store: dict[UUID, Document] = {}

    def make_uow(self) -> InMemoryUoW:
        """
        Return a fresh ``InMemoryUoW`` wrapping the shared document store.

        Returns:
            A new ``InMemoryUoW`` instance.  All instances share the same
            underlying dict — mutations are immediately visible across all UoW
            instances.

        Edge cases:
            - The returned UoW has no transaction isolation — ``rollback()``
              is a no-op.  Changes made before an exception propagates are
              NOT undone.
        """
        return InMemoryUoW(InMemoryDocumentRepository(self._store))


__all__ = ["InMemoryDocumentRepository", "InMemoryUoW", "InMemoryUoWProvider"]
