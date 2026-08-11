"""
Integration tests for varco_sa.advisory_lock — SAXactAdvisoryLock (Step 62).
================================================================================

Plan 005, Phase 5, Step 62 — failing tests first.

RED until Step 63 lands: ``SAXactAdvisoryLock`` (transaction-scoped
``pg_try_advisory_xact_lock`` / released at COMMIT/ROLLBACK, no explicit
``release()`` call) is added to ``varco_sa.advisory_lock``, alongside today's
session-scoped ``SAAdvisoryLock``.

Advisory locks are PostgreSQL-only — every test in this module requires a
real PostgreSQL instance (``testcontainers.postgres``) and is marked
``@pytest.mark.integration``, disabled by default (matches the pattern in
``test_sa_encryption_store.py`` / ``test_sa_job_store.py`` — set
``VARCO_RUN_INTEGRATION=1`` to run it).
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


@pytest.fixture(scope="module")
def pg_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


@pytest_asyncio.fixture
async def engine(pg_container):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = pg_container.get_connection_url().replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


class TestSAXactAdvisoryLockImportability:
    def test_sa_xact_advisory_lock_is_importable(self) -> None:
        # Cheapest possible RED signal — no DB needed to prove the class
        # does not exist yet.
        from varco_sa import advisory_lock

        assert hasattr(advisory_lock, "SAXactAdvisoryLock")


class TestSAXactAdvisoryLockContention:
    async def test_two_sessions_contend_exactly_one_acquires(
        self, session_factory
    ) -> None:
        from varco_sa.advisory_lock import SAXactAdvisoryLock

        lock = SAXactAdvisoryLock()

        async with session_factory() as session_a, session_factory() as session_b:
            async with session_a.begin():
                async with lock.xact("contended-key", session_a) as acquired_a:
                    assert acquired_a is True

                    async with session_b.begin():
                        async with lock.xact("contended-key", session_b) as acquired_b:
                            assert acquired_b is False


class TestSAXactAdvisoryLockReleaseSemantics:
    async def test_lock_released_at_commit_with_no_release_call(
        self, session_factory
    ) -> None:
        from varco_sa.advisory_lock import SAXactAdvisoryLock

        lock = SAXactAdvisoryLock()

        async with session_factory() as session_a:
            async with session_a.begin():
                async with lock.xact("commit-key", session_a) as acquired:
                    assert acquired is True
            # session_a's transaction has committed — lock must be released.

        async with session_factory() as session_b:
            async with session_b.begin():
                async with lock.xact("commit-key", session_b) as acquired_b:
                    assert acquired_b is True

    async def test_lock_released_at_rollback(self, session_factory) -> None:
        from varco_sa.advisory_lock import SAXactAdvisoryLock

        lock = SAXactAdvisoryLock()

        async with session_factory() as session_a:
            async with session_a.begin() as txn:
                async with lock.xact("rollback-key", session_a) as acquired:
                    assert acquired is True
                await txn.rollback()

        async with session_factory() as session_b:
            async with session_b.begin():
                async with lock.xact("rollback-key", session_b) as acquired_b:
                    assert acquired_b is True


class TestSAXactAdvisoryLockAbcRoundTrip:
    async def test_try_acquire_release_round_trips_via_abc(
        self, session_factory
    ) -> None:
        from varco_sa.advisory_lock import SAXactAdvisoryLock

        lock = SAXactAdvisoryLock()
        handle = await lock.try_acquire("abc-key", ttl=30.0)
        assert handle is not None
        released = await lock.release("abc-key", handle.token)
        assert released is True
