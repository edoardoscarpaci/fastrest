"""
varco_beanie.uow
====================
Beanie / pymongo async Unit of Work.

DESIGN: motor → pymongo async (beanie 2.x migration)
    Beanie 2.0 dropped motor and uses pymongo>=4.11's native async client.
    We mirror that change here so the UoW types are consistent with what
    beanie uses internally.

    ✅ No extra motor dependency — pymongo is already required by beanie 2.x
    ❌ pymongo>=4.11 required — cannot run on older pymongo with this module
    ❌ AsyncClientSession does NOT mirror AsyncIOMotorClientSession's
       sync/async shape — a prior version of this docstring claimed it did,
       which is exactly what caused a shipped bug (see below). The two
       diverge on the calls that matter most to this module:

       | Call                | motor (AsyncIOMotorClient*)     | pymongo (AsyncMongoClient / AsyncClientSession) |
       |---------------------|----------------------------------|--------------------------------------------------|
       | ``start_session()`` | coroutine — must ``await``      | plain sync method — must NOT ``await``          |
       | ``start_transaction()`` | sync — returns a context manager | coroutine — must ``await``                 |
       | ``end_session()`` / ``close()`` | sync                | coroutine — must ``await``               |

       Getting the first two backwards previously shipped as a real bug:
       ``await client.start_session()`` raised
       ``TypeError: object AsyncClientSession can't be used in 'await'
       expression``, and a bare (un-awaited) ``start_transaction()`` call
       silently opened no transaction at all — ``transactional=True`` was a
       no-op and commit/abort acted on a transaction that never existed.
       Always check the real pymongo type, never assume motor's shape
       carries over.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pymongo import AsyncMongoClient
from varco_core.uow import AsyncUnitOfWork

if TYPE_CHECKING:
    # AsyncClientSession is only needed for type annotations — import under
    # TYPE_CHECKING to avoid hard runtime dependency on the private submodule
    # path, which could change across pymongo minor versions.
    from pymongo.asynchronous.client_session import AsyncClientSession


class BeanieUnitOfWork(AsyncUnitOfWork):
    """
    Unit of Work backed by a pymongo ``AsyncClientSession``.

    Set ``transactional=True`` only when connected to a MongoDB replica set
    or sharded cluster — standalone instances do not support multi-document
    transactions.

    Args:
        mongo_client:    Connected ``AsyncMongoClient`` (pymongo>=4.11).
        repo_factories:  ``{attr_name: factory(session | None) → repo}``.
        transactional:   Wrap operations in a MongoDB transaction.

    Thread safety:  ❌ One UoW per request / task.
    Async safety:   ✅ Uses pymongo AsyncClientSession throughout.
    """

    def __init__(
        self,
        mongo_client: AsyncMongoClient[dict[str, Any]],
        repo_factories: dict[str, Callable[[AsyncClientSession | None], Any]],
        *,
        transactional: bool = False,
    ) -> None:
        self._client = mongo_client
        self._repo_factories = repo_factories
        self._transactional = transactional
        self._session: AsyncClientSession | None = None

    async def _begin(self) -> None:
        if self._session is None:
            # NOT awaited: unlike motor's AsyncIOMotorClient (which this module
            # was migrated off), pymongo's native async client makes only the
            # *session's* operations awaitable — start_session() itself is a
            # plain sync factory returning an AsyncClientSession. Awaiting it
            # raises `TypeError: object AsyncClientSession can't be used in
            # 'await' expression`. end_session()/close() ARE coroutines.
            self._session = self._client.start_session()
        if self._transactional:
            # Awaited for the same reason: pymongo's AsyncClientSession makes
            # start_transaction() a coroutine (motor's was a sync method
            # returning a context manager). Calling it bare created a
            # never-awaited coroutine, so NO transaction was ever opened and
            # the subsequent commit/abort acted on a non-existent transaction.
            await self._session.start_transaction()
        for attr, factory in self._repo_factories.items():
            setattr(self, attr, factory(self._session))

    async def commit(self) -> None:
        if self._session:
            if self._transactional:
                await self._session.commit_transaction()
            await self._session.end_session()
            self._session = None

    async def rollback(self) -> None:
        if self._session:
            if self._transactional:
                await self._session.abort_transaction()
            await self._session.end_session()
            self._session = None

    def __repr__(self) -> str:
        tx = "transactional" if self._transactional else "no-tx"
        return f"BeanieUnitOfWork({'open' if self._session else 'closed'}, {tx})"
