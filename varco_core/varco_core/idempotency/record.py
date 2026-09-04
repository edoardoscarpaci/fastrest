"""
varco_core.idempotency.record
==============================
``IdempotencyRecord`` — the immutable value object stored once a request has
been fully executed and its response captured (Plan 029 / D1a, Step 1).

This is deliberately the *only* mutable-looking state ``AbstractIdempotencyStore``
hands back to a caller — everything else (reservation bookkeeping, TTL
tracking) is private to the store implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class IdempotencyRecord:
    """
    A captured HTTP response, keyed by an ``Idempotency-Key`` (§D-D1-replay).

    Stored by ``AbstractIdempotencyStore.complete()`` once the wrapped route
    handler has run to completion, and returned unchanged by ``get()``/via a
    ``REPLAY`` outcome from ``reserve()`` on every subsequent request carrying
    the same key.

    Attributes:
        status:      The HTTP status code of the original response (e.g. 200,
                     201).
        body:        The raw response body bytes, exactly as sent to the
                     first caller. Never re-serialized — replaying it byte-
                     for-byte is what makes the replay trustworthy.
        headers:     A case-insensitive-by-convention mapping of header name
                     to value, already filtered down to the replay allowlist
                     (§D-D1-replay) by the caller (``IdempotencyMiddleware``)
                     before ``complete()`` is invoked. The store itself does
                     not know about the allowlist — that is an HTTP-layer
                     concern, not a storage concern.
        fingerprint: The ``compute_fingerprint()`` output for the request that
                     produced this response. Compared against a *new*
                     request's fingerprint by the caller to distinguish a
                     legitimate replay from a reused key with a different
                     payload (422 territory — see
                     ``IdempotencyFingerprintMismatchError``).
        created_at:  UTC timestamp of when this record was completed. Not
                     used for TTL enforcement by the in-memory store (which
                     tracks expiry via the reservation's own ``ttl``), but
                     kept for observability/debugging and for durable stores
                     that may want it as an index column.

    Thread safety:  ✅ ``frozen=True`` — immutable after construction, safe to
                    share across concurrent replay reads.
    Async safety:   ✅ Pure value object — no I/O.

    Edge cases:
        - ``body`` may be empty bytes (``b""``) for a ``204 No Content``
          response — this is valid and replayed as-is.
        - ``headers`` is a plain ``Mapping`` (usually ``dict``), not a
          ``starlette.Headers`` object — the store must not depend on
          FastAPI/Starlette types (§D-D1-home: keep ``varco_core`` framework
          agnostic).
    """

    status: int
    body: bytes
    headers: Mapping[str, str]
    fingerprint: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


__all__ = ["IdempotencyRecord"]
