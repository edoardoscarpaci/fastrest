"""
varco_sa.migration.lock
========================
``migration_lock`` — the D2 multi-pod exclusion mechanic: a dedicated
connection, held open across Alembic's own transaction(s), releasing on
COMMIT rather than an explicit call.

DESIGN: dedicated NullPool connection with SET LOCAL idle_in_transaction_session_timeout = 0
    ✅ The lock lives in the database being migrated — the correct failure
       domain (see Plan 006 D2). A crashed process releases the lock
       automatically (connection death), with no TTL to size.
    ✅ ``SAXactAdvisoryLock.xact()`` is pooler-safe by construction — the
       lock is released by this function's own COMMIT, never by a separate
       ``release()`` call that a transaction-mode pooler could misroute.
    ✅ A dedicated engine with ``NullPool`` guarantees the lock connection
       is never subject to ``pool_recycle`` — a recycled connection mid-hold
       would silently un-exclude concurrent DDL.
    ❌ The held-open transaction pins one Postgres backend + one open
       snapshot for the whole migration — accepted vacuum/xmin-horizon cost
       (Plan 006 D2).
    ❌ SQLite (and any non-PostgreSQL dialect) has no advisory locks — this
       function short-circuits to "acquired" for those dialects, which is
       honest (SQLite is single-writer) rather than a silent downgrade.

Thread safety:  ⚠️ One migration_lock() call per process/coroutine — this is
                   not a re-entrant lock.
Async safety:   ✅ Fully async; polling uses ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from varco_core.migration.errors import MigrationLockTimeout

from varco_sa.advisory_lock import SAXactAdvisoryLock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from varco_core.lock import AbstractDistributedLock

logger = logging.getLogger(__name__)

_sqlite_shortcircuit_logged = False


@asynccontextmanager
async def migration_lock(
    engine: AsyncEngine,
    key: str,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    lock: AbstractDistributedLock | None = None,
) -> AsyncIterator[None]:
    """
    Async context manager implementing the D2 multi-pod exclusion algorithm.

    Args:
        engine:        The engine of the database to lock. On PostgreSQL, a
                        **separate** ``NullPool`` engine (same URL) is used
                        for the dedicated lock connection — this engine is
                        never touched directly.
        key:           Lock key (e.g. ``"varco:migrate"``).
        timeout:       Seconds to poll for the lock before raising.
        poll_interval: Seconds between acquisition attempts.
        lock:          Optional caller-supplied ``AbstractDistributedLock``
                        (e.g. ``RedisLock``) to use instead of the built-in
                        Postgres advisory-lock mechanic.

    Yields:
        Nothing — the context body runs with the lock held.

    Raises:
        MigrationLockTimeout: The lock could not be acquired within
            ``timeout`` seconds.

    Edge cases:
        - Non-PostgreSQL dialects (SQLite in unit tests) short-circuit to
          "acquired" immediately, with a one-time INFO log — SQLite is
          single-writer, so this is honest, not a silent downgrade.
        - On exit, the underlying transaction is COMMITted — this commit
          IS the release. There is no separate ``release()`` call.
    """
    if lock is not None:
        handle = await lock.try_acquire(key, ttl=timeout)
        if handle is None:
            raise MigrationLockTimeout(key, timeout)
        try:
            yield
        finally:
            await handle.release()
        return

    dialect_name = engine.dialect.name
    if dialect_name != "postgresql":
        global _sqlite_shortcircuit_logged
        if not _sqlite_shortcircuit_logged:
            logger.info(
                "migration_lock: dialect %r has no advisory locks — "
                "short-circuiting to 'acquired' (single-writer dialect).",
                dialect_name,
            )
            _sqlite_shortcircuit_logged = True
        yield
        return

    # DESIGN: a dedicated NullPool engine, not engine.connect() — guarantees
    # this connection is never subject to the caller engine's pool_recycle,
    # which could otherwise silently recycle the lock-holding connection
    # mid-migration and un-exclude concurrent DDL (Risks section, Plan 006).
    lock_engine = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await lock_engine.connect()
        try:
            trans = await conn.begin()
            try:
                await conn.execute(
                    text("SET LOCAL idle_in_transaction_session_timeout = 0")
                )

                xact_lock = SAXactAdvisoryLock()
                session = AsyncSession(bind=conn)
                deadline = time.monotonic() + timeout
                acquired = False
                while True:
                    async with xact_lock.xact(key, session) as got:
                        if got:
                            acquired = True
                    if acquired:
                        break
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(poll_interval)

                if not acquired:
                    await trans.rollback()
                    raise MigrationLockTimeout(key, timeout)

                try:
                    yield
                finally:
                    # The COMMIT here is the release — no explicit unlock
                    # call exists for pg_try_advisory_xact_lock.
                    await trans.commit()
            except BaseException:
                if trans.is_active:
                    await trans.rollback()
                raise
        finally:
            await conn.close()
    finally:
        await lock_engine.dispose()


__all__ = ["migration_lock"]
