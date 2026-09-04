"""
varco_core.idempotency.base
============================
``AbstractIdempotencyStore`` — the storage contract behind D1's
``Idempotency-Key`` middleware (Plan 029 / D1a, Step 2).

§D-D1-atomic is the load-bearing decision this module encodes: ``reserve()``
is the **one** atomic primitive every implementation must offer, because
``AsyncCache`` (``varco_core/cache/base.py``) exposes no atomic
set-if-absent and CLAUDE.md's decision tree forbids adding one (Plan 011
D-11 — the same reasoning that keeps bulk operations off ``AsyncCache`` via
``BulkCache`` instead). Pushing atomicity up into a *new* ABC, rather than
trying to emulate it with ``exists()`` + ``set()``, is what makes every
implementation correct under concurrency instead of merely correct under a
sequential test.

A store that cannot offer an atomic ``reserve()`` is not a valid
implementation of this ABC — see each concrete subclass's own docstring for
the native primitive it uses (``SET NX PX`` for Redis, a unique index +
``IntegrityError``/``DuplicateKeyError`` for SQL/Mongo, a lazily-created
``asyncio.Lock`` for the in-process default).
"""

from __future__ import annotations

import abc
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varco_core.idempotency.record import IdempotencyRecord


class ReserveOutcome(Enum):
    """
    The three possible results of ``AbstractIdempotencyStore.reserve()``.

    Members:
        ACQUIRED:  No prior reservation or completed record exists for this
                   key — the caller is the first to see it and must proceed
                   to execute the wrapped operation and eventually call
                   ``complete()`` (or ``release()`` if it cannot capture a
                   response — see §D-D1-replay's streaming/over-ceiling
                   cases).
        IN_FLIGHT: A reservation exists but has not yet been completed —
                   another request with the same key is still executing.
                   The caller must return **409 Conflict**
                   (``IdempotencyKeyConflictError``) rather than execute
                   again or block.
        REPLAY:    A completed record exists for this key. The caller must
                   fetch it via ``get()`` and either replay it (fingerprint
                   matches) or return **422 Unprocessable Content**
                   (``IdempotencyFingerprintMismatchError``, fingerprint
                   differs) — the store itself does not compare
                   fingerprints; that comparison is the HTTP-layer caller's
                   job (§D-D1-fingerprint), keeping this ABC storage-only.

    Thread safety:  ✅ ``Enum`` members are module-level singletons.
    Async safety:   ✅ Pure value — no I/O.
    """

    ACQUIRED = auto()
    IN_FLIGHT = auto()
    REPLAY = auto()


class AbstractIdempotencyStore(abc.ABC):
    """
    Storage contract for HTTP idempotency records (§D-D1-home, §D-D1-atomic).

    Distinct from ``varco_core.service.inbox``'s ``InboxRepository``: the
    inbox pattern persists an *incoming event* before a handler runs so a
    poller can re-deliver it after a crash (bus → handler gap), and deletes
    the entry once processed. This ABC persists an **outgoing response** to
    suppress re-execution of a retried HTTP request, and the record must
    *survive* processing for its full TTL — retention is the entire point,
    the opposite of the inbox's delete-when-done lifecycle. See
    ``varco_core/service/inbox.py``'s own docstring for the same
    cross-reference in the other direction.

    Implementations MUST make ``reserve()`` atomic — see the module
    docstring's §D-D1-atomic summary. A caller (``IdempotencyMiddleware``)
    relies on this to guarantee that of N concurrent retries carrying the
    same key, exactly one receives ``ACQUIRED``.

    Thread safety:  ⚠️ Implementation-defined — the in-memory implementation
                    is single-process only (see its own docstring); durable
                    backends (Redis/SA/Beanie) are safe across processes.
    Async safety:   ✅ All methods are ``async def``.
    """

    @abc.abstractmethod
    async def reserve(self, key: str, fingerprint: str, *, ttl: float) -> ReserveOutcome:
        """
        Atomically claim ``key``, or report its current state.

        This is the **single atomic primitive** of the ABC (§D-D1-atomic) —
        every implementation must guarantee that concurrent callers racing
        on the same ``key`` receive exactly one ``ACQUIRED`` and the rest
        receive ``IN_FLIGHT`` or ``REPLAY``, never two ``ACQUIRED``.

        Args:
            key:         The scoped storage key (already namespaced by
                         tenant/subject per §D-D1-scope — this ABC does not
                         know about tenancy).
            fingerprint: The ``compute_fingerprint()`` output for the
                         incoming request. Stored alongside a fresh
                         reservation so a later ``get()`` can expose it for
                         comparison; NOT compared by ``reserve()`` itself —
                         see ``ReserveOutcome.REPLAY``'s docstring.
            ttl:         Seconds the reservation (and, once completed, the
                         record) remains valid for. Must be > 0.

        Returns:
            ``ReserveOutcome.ACQUIRED`` if this call created a fresh
            reservation; ``IN_FLIGHT`` if an unfinished reservation already
            existed; ``REPLAY`` if a completed record already exists.

        Raises:
            ValueError: ``ttl`` is not positive.

        Edge cases:
            - A reservation that is never completed nor released (e.g. the
              process crashes mid-request) remains ``IN_FLIGHT`` until
              ``ttl`` elapses, after which a fresh ``reserve()`` for the same
              key returns ``ACQUIRED`` again — the TTL is the failure-mode
              backstop, not ``release()``.

        Async safety: ✅ Must be safe to call concurrently for the same key
            from multiple coroutines/processes — this is the entire
            contract.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def complete(self, key: str, record: IdempotencyRecord) -> None:
        """
        Store the final response for a previously-reserved ``key``.

        Args:
            key:    The same scoped storage key passed to ``reserve()``.
            record: The captured response to persist.

        Raises:
            None expected under normal operation — implementations should
            let genuine storage errors propagate (unlike
            ``AbstractDeadLetterQueue.push()``, there is no "must never
            raise" contract here: a failure to persist an idempotency
            record means the *next* retry re-executes, which is merely a
            loss of the optimization, not data loss).

        Edge cases:
            - Calling ``complete()`` for a key that was never reserved is
              implementation-defined (in-memory: creates the record anyway;
              durable backends may reject it) — callers (the middleware)
              always reserve first, so this is not part of the load-bearing
              contract.

        Async safety: ✅ Safe to call once per successful reservation.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get(self, key: str) -> IdempotencyRecord | None:
        """
        Return the completed record for ``key``, or ``None``.

        Args:
            key: The scoped storage key.

        Returns:
            The stored ``IdempotencyRecord`` if ``complete()`` has been
            called for this key and it has not expired; ``None`` if the key
            is unknown, still only reserved (not yet completed), or expired.

        Async safety: ✅ Read-only — safe to call concurrently.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def release(self, key: str) -> None:
        """
        Release a reservation without completing it.

        Used by ``IdempotencyMiddleware`` when a response cannot be
        captured — a streaming response, or one over
        ``max_stored_body_bytes`` (§D-D1-replay) — so a subsequent retry of
        the same key is free to re-execute rather than getting stuck
        reporting ``IN_FLIGHT`` until the TTL expires.

        Args:
            key: The scoped storage key to release.

        Edge cases:
            - Releasing an unknown key, or a key that has already been
              completed, is a no-op — never raises.

        Async safety: ✅ Idempotent — safe to call more than once.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_expired(self) -> int:
        """
        Best-effort sweep of expired reservations/records.

        Backends with native TTL support (Redis's ``PX``, Mongo's TTL index)
        may implement this as a no-op returning ``0`` — expiry is already
        handled by the storage engine itself. Backends without native TTL
        (SQL) should implement a real ``DELETE ... WHERE expires_at < now()``
        sweep, called periodically by the application (same convention as
        ``SADeduplicator.purge_expired()``).

        Returns:
            The number of entries removed by this call (``0`` if the
            backend relies on native expiry).

        Async safety: ✅ Safe to call concurrently with ``reserve()``/
            ``complete()`` — removes only already-expired entries.
        """
        raise NotImplementedError


__all__ = ["AbstractIdempotencyStore", "ReserveOutcome"]
