"""`AsyncService.create()` over an in-memory repository (Plan 028 / Phase 3, P2).

The composite path a user actually pays for one write request: pre-check →
authorize → open a UoW → assemble a domain entity from the ``CreateDTO`` →
persist → assemble the ``ReadDTO`` back → commit. Every mixin hook, every
``super()`` chain and both pydantic validations are inside the measured
region; only the database is not.

That is the point. A regression in *any* of the layers Plan 028 might later
touch (query AST allocation, reflection caching, a new middleware) shows up
here first, in the shape a user experiences it, rather than in an isolated
micro-benchmark that flatters itself.

The in-memory doubles below are deliberately re-declared rather than imported
from ``varco_core/tests/test_service.py``: benchmarks must not depend on a test
module's private scaffolding, which is free to change for reasons that have
nothing to do with performance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Annotated, Any

from conftest import FIXED_TS
from varco_core.assembler import AbstractDTOAssembler
from varco_core.auth.authorizer import BaseAuthorizer
from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO
from varco_core.meta import PKStrategy, PrimaryKey, pk_field
from varco_core.model import AuditedDomainModel
from varco_core.query.params import QueryParams
from varco_core.repository import AsyncRepository
from varco_core.service import AsyncService, IUoWProvider
from varco_core.uow import AsyncUnitOfWork


@dataclass
class _Post(AuditedDomainModel):
    """Two-field audited entity — the smallest thing that still exercises timestamps."""

    pk: Annotated[str, PrimaryKey(strategy=PKStrategy.STR_ASSIGNED)] = pk_field(init=True)
    title: str = ""

    class Meta:
        table = "bench_posts"


class _CreatePostDTO(CreateDTO):
    title: str


class _PostReadDTO(ReadDTO):
    title: str


class _UpdatePostDTO(UpdateDTO):
    title: str | None = None


class _PostAssembler(AbstractDTOAssembler[_Post, _CreatePostDTO, _PostReadDTO, _UpdatePostDTO]):
    """Straight field mapping — no computed fields, no lookups."""

    def to_domain(self, dto: _CreatePostDTO) -> _Post:
        return _Post(title=dto.title)

    def to_read_dto(self, entity: _Post) -> _PostReadDTO:
        return _PostReadDTO(
            pk=entity.pk,
            title=entity.title,
            created_at=entity.created_at or FIXED_TS,
            updated_at=entity.updated_at or FIXED_TS,
        )

    def apply_update(self, entity: _Post, dto: _UpdatePostDTO) -> _Post:
        return replace(entity, title=dto.title if dto.title is not None else entity.title)


class _InMemoryPostRepository(AsyncRepository[_Post, str]):
    """Dict-backed repository. Every method is O(1) or O(n) over a tiny dict."""

    def __init__(self) -> None:
        self._store: dict[str, _Post] = {}
        self._seq = 0

    async def find_by_id(self, pk: str) -> _Post | None:
        return self._store.get(pk)

    async def find_all(self) -> list[_Post]:
        return list(self._store.values())

    async def save(self, entity: _Post) -> _Post:
        if entity.pk is None:
            self._seq += 1
            entity.pk = f"gen-{self._seq}"
            entity.created_at = FIXED_TS
        entity.updated_at = FIXED_TS
        self._store[entity.pk] = entity
        return entity

    async def delete(self, entity: _Post) -> None:
        if entity.pk is not None:
            self._store.pop(entity.pk, None)

    async def find_by_query(self, params: QueryParams) -> list[_Post]:  # noqa: ARG002
        return list(self._store.values())

    async def count(self, params: QueryParams | None = None) -> int:  # noqa: ARG002
        return len(self._store)

    async def exists(self, pk: str) -> bool:
        return pk in self._store

    async def stream_by_query(self, params: QueryParams):  # type: ignore[override]  # noqa: ARG002
        for entity in list(self._store.values()):
            yield entity

    async def save_many(self, entities):  # type: ignore[override]
        return [await self.save(e) for e in entities]

    async def delete_many(self, entities):  # type: ignore[override]
        for entity in entities:
            await self.delete(entity)

    async def update_many_by_query(self, params, update):  # type: ignore[override]
        raise NotImplementedError


class _InMemoryUoW(AsyncUnitOfWork):
    """No-op transaction boundary exposing the shared repository as ``.posts``."""

    def __init__(self, repo: _InMemoryPostRepository) -> None:
        self.posts = repo

    async def _begin(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class _UoWProvider(IUoWProvider):
    """Shares one repository across every UoW — mirrors a real session factory."""

    def __init__(self) -> None:
        self.repo = _InMemoryPostRepository()

    def make_uow(self) -> AsyncUnitOfWork:
        return _InMemoryUoW(self.repo)


class _PostService(AsyncService[_Post, str, _CreatePostDTO, _PostReadDTO, _UpdatePostDTO]):
    """No mixins — the plain service path, so the number is a floor, not a composite."""

    def _get_repo(self, uow: AsyncUnitOfWork) -> AsyncRepository[_Post, Any]:
        return uow.posts  # type: ignore[attr-defined,no-any-return]


def test_service_create(benchmark) -> None:  # type: ignore[no-untyped-def]
    service = _PostService(
        uow_provider=_UoWProvider(),
        authorizer=BaseAuthorizer(),
        assembler=_PostAssembler(),
    )
    dto = _CreatePostDTO(title="benchmark")

    # One loop for the whole benchmark: asyncio.run() per iteration would
    # measure event-loop construction, which dwarfs the service call itself.
    loop = asyncio.new_event_loop()
    try:
        result = benchmark(lambda: loop.run_until_complete(service.create(dto)))
    finally:
        loop.close()
    assert result.title == "benchmark"
