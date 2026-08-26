"""
varco_sa.advisory_lock
=======================
PostgreSQL advisory lock implementations of ``AbstractDistributedLock``.

Two siblings live in this module (Plan 005, Phase 5, U-16):

``SAAdvisoryLock``
    **Session-level** lock — ``pg_try_advisory_lock`` / ``pg_advisory_unlock``.
    ⚠️ **Unsafe behind a transaction-mode connection pooler** (PgBouncer
    ``pool_mode=transaction`` and equivalents) — see the prominent warning on
    the class docstring below before choosing this one.

``SAXactAdvisoryLock``
    **Transaction-level** lock — ``pg_try_advisory_xact_lock``, released
    automatically at ``COMMIT``/``ROLLBACK`` with no explicit ``release()``
    call. Safe under transaction pooling because the lock's lifetime never
    outlives the single transaction it was taken in. **This is the
    recommended default** for any topology that might run behind a pooler —
    see ``technical_docs/features/distributed-locks.md`` for the full
    session-vs-transaction table and pooling matrix.

Uses PostgreSQL's session-level advisory lock functions::

    SELECT pg_try_advisory_lock(int8)  — non-blocking acquire
    SELECT pg_advisory_unlock(int8)    — release

Each held lock pins one connection from the ``AsyncEngine`` pool for the
duration of the lock.  The connection is returned to the pool on ``release()``
or when the lock's ``LockHandle`` is used as an async context manager.

Key hashing
-----------
PostgreSQL advisory locks use an ``int8`` (64-bit signed integer) key.  String
lock keys are hashed deterministically with ``hashlib.md5`` (truncated to 63
bits to stay within signed int64 range)::

    hash = int.from_bytes(md5(key.encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF

DESIGN: advisory lock over application-level locking (Redis SET NX)
    ✅ Native to PostgreSQL — no additional infrastructure (no Redis).
    ✅ Session-level lock is automatically released if the process crashes
       (PostgreSQL detects the broken connection and releases the lock).
    ✅ Fair on most PostgreSQL versions — other waiters are unblocked in order.
    ❌ Requires a pinned connection for the lock lifetime — may starve the pool
       in high-concurrency scenarios.  Tune pool size accordingly.
    ❌ TTL is not natively enforced — a process holding the lock indefinitely
       is only released when its connection closes.  Pair with ``timeout``
       in ``acquire()`` and ensure critical sections are time-bounded.
    ❌ Lock granularity is int64 — collisions across different string keys
       are astronomically unlikely (2^63 key space) but theoretically possible.

DESIGN: one pinned connection per held lock
    ✅ Advisory locks are session-scoped — the lock is tied to the connection.
       Releasing the connection releases the lock.
    ✅ Allows concurrent locks on different keys from the same process.
    ❌ Each held lock consumes one DB connection.  Avoid holding many locks
       simultaneously; release promptly.

DESIGN: in-process dict for token → connection tracking
    ✅ Token check matches the ``AbstractDistributedLock`` contract — the
       original holder cannot release a lock another process has re-acquired.
    ❌ State is process-local — tokens are not replicated across replicas.
       This is correct: each process holds its own advisory lock via its own
       session; the token just prevents double-release within one process.

Thread safety:  ⚠️ asyncio.Lock used for the token dict.  Not safe across OS threads.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 📐 https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
  pg_try_advisory_lock / pg_advisory_unlock reference
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from varco_core.lock import AbstractDistributedLock, LockHandle

_logger = logging.getLogger(__name__)

# Maximum value of a signed int64 (PostgreSQL bigint).
_MAX_INT64 = 0x7FFFFFFFFFFFFFFF


def _key_to_int64(key: str) -> int:
    """
    Hash a string lock key to a signed int64 for PostgreSQL advisory locking.

    Uses the first 8 bytes of the MD5 digest, interpreted as a big-endian
    unsigned integer, then masked to 63 bits to stay within signed int64 range.

    Args:
        key: The string lock key (e.g. ``"inventory:item_42"``).

    Returns:
        A signed int64 in the range [0, 2^63 - 1].

    Edge cases:
        - Two keys may hash to the same int64 (collision probability ~2^-63).
          This is negligible for typical key spaces.
        - Empty string produces a valid hash — callers should validate keys
          before passing to ``try_acquire()`` (validated by the base class).
    """
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & _MAX_INT64


# ── SAAdvisoryLock ────────────────────────────────────────────────────────────


class SAAdvisoryLock(AbstractDistributedLock):
    """
    PostgreSQL **session-level** advisory lock implementation of
    ``AbstractDistributedLock``.

    ⚠️⚠️⚠️ **UNSUPPORTED TOPOLOGY: transaction-mode connection pooling**
    (PgBouncer ``pool_mode=transaction`` and equivalents — e.g. pgcat,
    Supavisor in transaction mode) ⚠️⚠️⚠️

    This class's design note at module scope assumes *"each process holds
    its own advisory lock via its own connection"* — i.e. a **direct**
    connection, or at minimum a **session-mode** pooler where one logical
    connection maps to one physical server connection for the connection's
    lifetime. Under **transaction-mode** pooling that assumption breaks in
    four concrete steps:

    1. ``try_acquire()`` runs ``pg_try_advisory_lock`` on borrowed connection A.
    2. The pooler returns physical connection A to its pool as soon as the
       *implicit* transaction around that single statement ends — a
       transaction-mode pooler does this between every statement, not just
       at session end.
    3. ``release()`` later runs ``pg_advisory_unlock`` on whichever physical
       connection the pooler happens to route it to — call it B, a
       *different* physical connection from A. PostgreSQL's advisory-unlock
       functions only release a lock held by the **calling session** — B
       never held it, so the call returns ``false`` and nothing happens.
    4. The lock **leaks on physical connection A** until that connection is
       closed (pool recycling, restart) — and because A is now back in the
       pool, the **next, unrelated borrower of A silently inherits the held
       lock**, with no idea why some of its own advisory-lock operations on
       the same key are being rejected as contended.

    **If your deployment sits behind a transaction-mode pooler, use
    ``SAXactAdvisoryLock`` instead** — its lock is scoped to (and released by)
    the transaction itself, so pooling never has a chance to reroute the
    release. See ``technical_docs/features/distributed-locks.md`` for the
    full pooling compatibility matrix. **This class receives no runtime
    warning for this case** — that would be noise for the many
    correctly-deployed direct-connection/session-pooler users; the warning
    exists in this docstring instead.

    Each call to ``try_acquire()`` borrows a connection from the engine's pool
    and holds it for the duration of the lock.  The connection is returned to
    the pool when ``LockHandle.release()`` is called (or the context manager
    exits).

    Thread safety:  ⚠️ asyncio.Lock guards the internal token dict — safe for
                        concurrent coroutines; not safe across OS threads.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        engine: ``AsyncEngine`` — connection pool used to borrow connections.

    Edge cases:
        - ``ttl`` is accepted by ``try_acquire`` but NOT enforced at the
          database level.  PostgreSQL session-level advisory locks last until
          the connection is closed.  Use the ``timeout`` parameter of
          ``acquire()`` to bound the waiting time, and keep critical sections
          short.
        - Calling ``try_acquire`` with the same key from the same process
          will succeed (PostgreSQL advisory locks are NOT re-entrant at the
          session level — a second lock on the same session and key is counted
          separately and requires a matching unlock).  The ``InMemoryLock``
          pattern avoids re-entrant use; so does keeping lock scopes narrow.
        - On connection failure during acquire, the connection is closed and
          ``None`` is returned (as if the lock were contended).
        - This implementation is NOT compatible with SQLite (advisory lock
          functions are PostgreSQL-specific).  Use ``InMemoryLock`` for tests
          that do not need cross-process coordination.

    Example::

        engine = create_async_engine("postgresql+asyncpg://...")
        lock = SAAdvisoryLock(engine)

        handle = await lock.try_acquire("inventory:item_42", ttl=30)
        if handle is not None:
            async with handle:
                await reserve_item(42)
        else:
            # Lock contended — skip or retry
            ...
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """
        Args:
            engine: Async SQLAlchemy engine used to borrow connections.
        """
        self._engine = engine
        # Maps token UUID → (key_int64, pinned_connection).
        self._held: dict[UUID, tuple[int, AsyncConnection]] = {}
        # Protects mutations to _held across concurrent coroutines.
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the dict guard lock, creating it lazily inside the event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def try_acquire(
        self,
        key: str,
        *,
        ttl: float,
    ) -> LockHandle | None:
        """
        Attempt to acquire the PostgreSQL advisory lock for ``key``.

        ⚠️ **``ttl`` is accepted but silently IGNORED — read this before
        relying on it.** Executes ``SELECT pg_try_advisory_lock(:key_int)``
        on a borrowed connection.  Returns a ``LockHandle`` if the lock is
        free, or ``None`` if it is already held by another session.
        Session-level advisory locks persist until the connection is
        explicitly closed or the PostgreSQL session ends — there is no
        server-side timer that reads ``ttl`` and force-expires the lock.

        Args:
            key: The string lock key — hashed to int64 internally.
            ttl: Accepted for interface compatibility with
                 ``AbstractDistributedLock`` only; NOT enforced by PostgreSQL
                 advisory locks — the lock is held until ``release()`` is
                 called (or the pinned connection closes).  Bound the actual
                 hold time with the ``timeout`` parameter of ``acquire()``
                 and keep critical sections short.

        Returns:
            A ``LockHandle`` if the lock was acquired; ``None`` if contended.

        Raises:
            ValueError: If ``key`` is empty.

        Edge cases:
            - On database connection failure, ``None`` is returned — the caller
              treats it the same as contention.
            - Each successful ``try_acquire`` pins one connection from the pool.
              Release promptly to avoid pool exhaustion.

        Async safety: ✅ Acquires asyncio.Lock before mutating ``_held``.
        """
        if not key:
            raise ValueError("Lock key must be a non-empty string.")

        key_int = _key_to_int64(key)
        conn: AsyncConnection | None = None

        try:
            conn = await self._engine.connect()
            result = await conn.execute(
                sa.text("SELECT pg_try_advisory_lock(:key)").bindparams(key=key_int)
            )
            acquired: bool = bool(result.scalar())
        except Exception as exc:
            _logger.warning(
                "SAAdvisoryLock.try_acquire: connection error for key=%r: %s",
                key,
                exc,
            )
            if conn is not None:
                await conn.close()
            return None

        if not acquired:
            # Lock is held by another session — return connection to pool.
            await conn.close()
            _logger.debug(
                "SAAdvisoryLock.try_acquire: contended for key=%r (int=%d)",
                key,
                key_int,
            )
            return None

        token = uuid4()
        async with self._get_lock():
            self._held[token] = (key_int, conn)

        _logger.debug(
            "SAAdvisoryLock.try_acquire: acquired key=%r (int=%d, token=%s)",
            key,
            key_int,
            token,
        )
        return LockHandle(key=key, token=token, lock=self)

    async def release(self, key: str, token: UUID) -> None:
        """
        Release the advisory lock identified by ``token``.

        Executes ``SELECT pg_advisory_unlock(:key_int)`` on the pinned
        connection, then closes the connection (returning it to the pool).

        Token mismatch (e.g. double-release or stale handle) → silent no-op.

        Args:
            key:   The original string lock key (used for logging only;
                   the stored int is looked up via ``token``).
            token: The token issued by ``try_acquire`` — must match to release.

        Edge cases:
            - Token not found → silent no-op (already released or never acquired
              by this process).
            - ``pg_advisory_unlock`` returns ``false`` if the lock was not held
              by this session (e.g. the connection was recycled) — logged as
              a warning but not raised.
            - The connection is always closed in the ``finally`` block, even if
              unlock raises.

        Async safety: ✅ Acquires asyncio.Lock before mutating ``_held``.
        """
        async with self._get_lock():
            entry = self._held.pop(token, None)

        if entry is None:
            # Token not in our dict — already released or from another process.
            _logger.debug(
                "SAAdvisoryLock.release: token %s not found (already released?)",
                token,
            )
            return

        key_int, conn = entry

        try:
            result = await conn.execute(
                sa.text("SELECT pg_advisory_unlock(:key)").bindparams(key=key_int)
            )
            unlocked: bool = bool(result.scalar())
            if not unlocked:
                _logger.warning(
                    "SAAdvisoryLock.release: pg_advisory_unlock returned false "
                    "for key=%r (int=%d, token=%s) — session may have been recycled.",
                    key,
                    key_int,
                    token,
                )
        except Exception as exc:
            _logger.error("SAAdvisoryLock.release: error unlocking key=%r: %s", key, exc)
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        _logger.debug(
            "SAAdvisoryLock.release: released key=%r (int=%d, token=%s)",
            key,
            key_int,
            token,
        )

    def __repr__(self) -> str:
        held = len(self._held)
        return f"SAAdvisoryLock(engine={self._engine!r}, held={held})"


# ── SAXactAdvisoryLock ───────────────────────────────────────────────────────


class SAXactAdvisoryLock(AbstractDistributedLock):
    """
    PostgreSQL **transaction-level** advisory lock (Plan 005, Phase 5, U-16).

    Uses ``pg_try_advisory_xact_lock`` — a lock that is automatically released
    at ``COMMIT`` or ``ROLLBACK``, with **no explicit unlock call**. Because
    the lock's entire lifetime is bounded by one transaction, it is safe under
    **transaction-mode connection pooling** (PgBouncer ``pool_mode=transaction``
    and equivalents) — unlike the session-scoped ``SAAdvisoryLock`` sibling in
    this module, there is no window where the acquiring statement and the
    releasing statement can be routed to two different physical connections.

    Two ways to use this class:

    1. **``xact()`` — the primary, recommended API.** Runs
       ``pg_try_advisory_xact_lock`` on the **caller's own** ``AsyncSession``,
       inside a transaction the caller already owns and will commit/rollback
       itself. This is the natural, connection-pool-friendly shape: the lock
       piggybacks on a transaction you were opening anyway, and it is released
       automatically the instant that transaction ends — no separate release
       call, no separate pinned connection.
    2. **``try_acquire`` / ``release`` — the ``AbstractDistributedLock`` ABC
       shape**, provided so the interface can be bound in DI and downstream
       code that already depends on ``AbstractDistributedLock`` can drop its
       own local reimplementation. ❌ **This shape opens and pins its own
       connection/transaction for the lock's entire lifetime** — the same
       pool-pinning cost as ``SAAdvisoryLock``, just transaction-scoped
       instead of session-scoped. **Prefer ``xact()`` as the default** —
       reach for ``try_acquire``/``release`` only when you must satisfy the
       ABC (e.g. resolving ``Inject[AbstractDistributedLock]`` generically).

    ``ttl`` on ``try_acquire`` is **meaningless** here, not merely unenforced:
    a transaction-scoped advisory lock's lifetime is bounded by the
    transaction itself (commit/rollback), so there is no timer for a ``ttl``
    value to drive in the first place — see the docstring on ``try_acquire``
    below, which says so explicitly rather than silently accepting-and-
    ignoring the parameter (the exact defect Plan 005's source corrections
    flagged on ``SAAdvisoryLock.try_acquire``).

    DESIGN: xact() as the primary API, try_acquire/release as an ABC-compat shape
        ✅ xact() has zero extra connection cost — it reuses a transaction the
           caller already needed for its own writes/reads.
        ✅ xact() is safe under transaction pooling by construction — Postgres
           itself releases the lock at COMMIT/ROLLBACK, so there is no
           separate "release" round-trip that a pooler could misroute.
        ✅ try_acquire/release satisfies AbstractDistributedLock so callers
           that only know the ABC (e.g. generic retry/coordination code) can
           still use this class.
        ❌ try_acquire/release pins one pooled connection for the lock's
           entire lifetime — document this prominently and steer callers to
           xact() first.

    Thread safety:  ⚠️ asyncio.Lock guards the internal token dict (used only
                        by the try_acquire/release ABC shape) — safe for
                        concurrent coroutines; not safe across OS threads.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        engine: ``AsyncEngine`` used ONLY by the ``try_acquire``/``release``
                ABC shape to open its own connection+transaction. ``xact()``
                does not use it at all — it runs entirely on the caller's
                supplied ``session``. May be omitted if the caller only ever
                uses ``xact()``.

    Edge cases:
        - Re-entering ``xact()`` with the same key on the **same session**
          (nested transaction / savepoint) is per PostgreSQL's own
          re-entrancy semantics for ``pg_try_advisory_xact_lock``: a second
          call on the same session and key succeeds immediately (Postgres
          advisory xact locks are stackable per session — each acquisition
          within the same top-level transaction is independent and all are
          released together at that transaction's end). This differs from
          ``SAAdvisoryLock``'s session-level lock, which the module docstring
          calls out as NOT re-entrant across two separate session-level
          acquisitions requiring matched unlocks.
        - This implementation is NOT compatible with SQLite — advisory lock
          functions are PostgreSQL-specific.

    Example (primary API)::

        lock = SAXactAdvisoryLock()

        async with session_factory() as session:
            async with session.begin():
                async with lock.xact("inventory:item_42", session) as acquired:
                    if acquired:
                        await reserve_item(session, 42)
                    else:
                        ...  # contended — skip or retry
            # COMMIT here releases the lock automatically — no release() call.

    Example (ABC shape — pins its own connection)::

        lock = SAXactAdvisoryLock(engine)
        handle = await lock.try_acquire("inventory:item_42", ttl=30.0)
        if handle is not None:
            async with handle:
                await reserve_item(42)
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        """
        Args:
            engine: Async SQLAlchemy engine used ONLY by the
                    ``try_acquire``/``release`` ABC shape. Omit it if the
                    caller only ever uses ``xact()``.
        """
        self._engine = engine
        # Maps token UUID → (key_int64, pinned_connection, pinned_transaction).
        # Only populated by the try_acquire/release ABC shape — xact() never
        # touches this dict.
        self._held: dict[UUID, tuple[int, AsyncConnection, Any]] = {}
        # Protects mutations to _held across concurrent coroutines.
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the dict guard lock, creating it lazily inside the event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @asynccontextmanager
    async def xact(self, key: str, session: AsyncSession) -> AsyncIterator[bool]:
        """
        Acquire a transaction-scoped advisory lock on the caller's session.

        **This is the primary, recommended API for this class.** Runs
        ``SELECT pg_try_advisory_xact_lock(:key_int)`` on ``session`` —
        which must already be inside a transaction the caller manages
        (``async with session.begin(): ...`` or an equivalent explicit
        transaction). The lock is released automatically when that
        transaction commits or rolls back — there is no ``release()`` call
        for this API; the caller's own commit/rollback IS the release.

        Args:
            key:     The string lock key — hashed to int64 internally via
                     ``_key_to_int64`` (shared, unchanged, with
                     ``SAAdvisoryLock``).
            session: The caller's ``AsyncSession`` — must be inside an active
                     transaction. The lock is scoped to THAT transaction.

        Yields:
            ``True`` if the lock was acquired (free), ``False`` if it is
            already held by another concurrent transaction. Unlike
            ``try_acquire``, contention does NOT prevent entering the
            context — the caller inspects the yielded boolean and decides
            what to do (mirrors ``pg_try_advisory_xact_lock``'s own
            non-blocking, always-returns semantics).

        Raises:
            ValueError: If ``key`` is empty.

        Edge cases:
            - The lock is held for the remainder of the CALLER's transaction,
              however long that is — there is no independent TTL or timeout
              here; the transaction's own commit/rollback bounds it.
            - Calling ``xact()`` again with the same key on the same session
              within the same top-level transaction succeeds again
              (PostgreSQL advisory xact locks are stackable per session/key;
              all stacked acquisitions release together at transaction end).
            - If ``session`` is not actually inside a transaction, Postgres
              still executes the lock function in its own implicit
              transaction, which is released essentially immediately — the
              lock is NOT held across your intended critical section. Always
              wrap the caller in ``session.begin()`` explicitly.

        Async safety: ✅ Runs on the caller's own session — no additional
                          connection is pinned by this method.
        """
        if not key:
            raise ValueError("Lock key must be a non-empty string.")

        key_int = _key_to_int64(key)
        result = await session.execute(
            sa.text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=key_int)
        )
        acquired: bool = bool(result.scalar())

        _logger.debug(
            "SAXactAdvisoryLock.xact: key=%r (int=%d) acquired=%s",
            key,
            key_int,
            acquired,
        )
        try:
            yield acquired
        finally:
            # No explicit release — pg_try_advisory_xact_lock is released by
            # the CALLER's own COMMIT/ROLLBACK. Nothing to do here; this
            # finally block exists only to document that fact at the call site.
            pass

    async def try_acquire(
        self,
        key: str,
        *,
        ttl: float,
    ) -> LockHandle | None:
        """
        ``AbstractDistributedLock`` ABC shape — opens and holds its OWN
        connection+transaction for the lock's entire lifetime.

        ❌ **Prefer ``xact()`` instead** — this method exists only so
        ``AbstractDistributedLock`` can be bound to ``SAXactAdvisoryLock`` in
        DI and generic coordination code. Every successful call pins one
        connection from the engine's pool until ``release()`` is called,
        exactly like ``SAAdvisoryLock.try_acquire`` — the only difference is
        that the pinned transaction's lock is ``pg_try_advisory_xact_lock``
        instead of the session-level function.

        ⚠️ **``ttl`` is not merely unenforced here — it is meaningless.** A
        transaction-scoped advisory lock's lifetime is bounded by the
        transaction (COMMIT/ROLLBACK), so there is no timer construct for a
        ``ttl`` value to drive even in principle. This is stated up front
        deliberately — Plan 005's source corrections flagged
        ``SAAdvisoryLock.try_acquire`` for accepting-and-silently-ignoring
        its ``ttl``; this docstring is the fix applied here as well.

        Args:
            key: The string lock key — hashed to int64 internally.
            ttl: Accepted for ``AbstractDistributedLock`` interface
                 compatibility only. Meaningless for a transaction-scoped
                 lock — the lock's lifetime is the pinned transaction's
                 lifetime, ended only by ``release()`` (which commits it).

        Returns:
            A ``LockHandle`` if the lock was acquired; ``None`` if contended
            or if no ``engine`` was supplied to the constructor.

        Raises:
            ValueError: If ``key`` is empty.
            RuntimeError: If this instance was constructed without an
                ``engine`` — the ABC shape needs one to open its own
                connection; ``xact()`` does not.

        Async safety: ✅ Acquires asyncio.Lock before mutating ``_held``.
        """
        if not key:
            raise ValueError("Lock key must be a non-empty string.")
        if self._engine is None:
            raise RuntimeError(
                "SAXactAdvisoryLock.try_acquire requires an `engine` passed to "
                "the constructor to open its own connection+transaction. "
                "Prefer xact(key, session) with a caller-managed session "
                "instead — it needs no engine and pins no extra connection."
            )

        key_int = _key_to_int64(key)
        conn: AsyncConnection | None = None

        try:
            conn = await self._engine.connect()
            txn = await conn.begin()
            result = await conn.execute(
                sa.text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=key_int)
            )
            acquired: bool = bool(result.scalar())
        except Exception as exc:
            _logger.warning(
                "SAXactAdvisoryLock.try_acquire: connection error for key=%r: %s",
                key,
                exc,
            )
            if conn is not None:
                await conn.close()
            return None

        if not acquired:
            # Lock is held by another transaction — roll back and return
            # the connection to the pool.
            await txn.rollback()
            await conn.close()
            _logger.debug(
                "SAXactAdvisoryLock.try_acquire: contended for key=%r (int=%d)",
                key,
                key_int,
            )
            return None

        token = uuid4()
        async with self._get_lock():
            self._held[token] = (key_int, conn, txn)

        _logger.debug(
            "SAXactAdvisoryLock.try_acquire: acquired key=%r (int=%d, token=%s)",
            key,
            key_int,
            token,
        )
        return LockHandle(key=key, token=token, lock=self)

    async def release(self, key: str, token: UUID) -> bool:  # type: ignore[override]
        """
        Release the ABC-shape lock identified by ``token`` by committing its
        pinned transaction — the transaction's commit IS the release for
        ``pg_try_advisory_xact_lock``.

        Only relevant to locks acquired via ``try_acquire`` — locks acquired
        via ``xact()`` are released by the caller's own commit/rollback and
        never touch this method.

        Args:
            key:   The original string lock key (used for logging only; the
                   stored int is looked up via ``token``).
            token: The token issued by ``try_acquire`` — must match to release.

        Returns:
            ``True`` if a held lock was found and committed/released;
            ``False`` if the token was not found (already released, or never
            acquired by this process) — a no-op in that case.

        Edge cases:
            - Token not found → ``False``, silent no-op.
            - The connection is always closed in the ``finally`` block, even
              if commit raises.

        Async safety: ✅ Acquires asyncio.Lock before mutating ``_held``.
        """
        async with self._get_lock():
            entry = self._held.pop(token, None)

        if entry is None:
            _logger.debug(
                "SAXactAdvisoryLock.release: token %s not found (already released?)",
                token,
            )
            return False

        _key_int, conn, txn = entry

        try:
            await txn.commit()
        except Exception as exc:
            _logger.error(
                "SAXactAdvisoryLock.release: error committing for key=%r: %s",
                key,
                exc,
            )
            raise
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        _logger.debug("SAXactAdvisoryLock.release: released key=%r (token=%s)", key, token)
        return True

    def __repr__(self) -> str:
        held = len(self._held)
        return f"SAXactAdvisoryLock(engine={self._engine!r}, held={held})"


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "SAAdvisoryLock",
    "SAXactAdvisoryLock",
]
