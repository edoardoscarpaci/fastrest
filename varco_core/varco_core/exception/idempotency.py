"""
varco_core.exception.idempotency
=================================
Exceptions raised by ``IdempotencyMiddleware`` (Plan 029 / D1a, Step 6).

Three subtypes, one per §D-D1-fingerprint's outcome table:

    IdempotencyKeyConflictError         → HTTP 409 (reservation in flight)
    IdempotencyFingerprintMismatchError → HTTP 422 (same key, different body)
    IdempotencyKeyInvalidError          → HTTP 400 (malformed/oversized key)

``IdempotencyKeyConflictError`` and ``IdempotencyFingerprintMismatchError``
subclass the existing ``ServiceConflictError``/``ServiceValidationError`` so
they get the correct HTTP status for free via ``_EXCEPTION_CODE_MAP``'s MRO
walk (``varco_core/exception/http.py``) — no new ``ErrorCode`` needed.
``IdempotencyKeyInvalidError`` has no existing 400-mapped
``ServiceException`` parent, so it registers its own ``ErrorCode`` via
``register_error_code()`` at import time (module import happens at
application startup, before request handling — the same "call before
requests begin" contract ``register_error_code`` already documents).
"""

from __future__ import annotations

from typing import Any

from varco_core.exception.codes import ErrorCode
from varco_core.exception.http import register_error_code
from varco_core.exception.service import (
    ServiceConflictError,
    ServiceException,
    ServiceValidationError,
)

__all__ = [
    "IdempotencyKeyConflictError",
    "IdempotencyFingerprintMismatchError",
    "IdempotencyKeyInvalidError",
]


class IdempotencyKeyConflictError(ServiceConflictError):
    """
    Raised when an ``Idempotency-Key`` has an in-flight (not yet completed)
    reservation — ``ReserveOutcome.IN_FLIGHT``.

    Maps to HTTP 409 Conflict (inherited from ``ServiceConflictError``).
    §D-D1-fingerprint's DESIGN block explains why this is 409-on-in-flight
    rather than blocking until the first request completes: no held
    connection, no server-side wait ceiling, no thread/task pileup under a
    retry storm.

    Thread safety:  ✅ Immutable after construction.
    Async safety:   ✅ Safe to raise in async contexts.
    """

    message_key = "varco.error.idempotency_conflict"

    #: Plan 029 / D1, open question 2 (§D-D1-fingerprint): seconds, not a
    #: date — HTTP convention, and simpler for a client to act on than
    #: computing a delta from an HTTP-date. A fixed, conservative value
    #: rather than the reservation's *actual* remaining TTL: exposing the
    #: real remaining TTL would require plumbing it back out of
    #: ``AbstractIdempotencyStore.reserve()``, which currently returns only
    #: a ``ReserveOutcome`` enum member (§D-D1-atomic deliberately keeps
    #: that return type minimal). A fixed 1s is a safe, generic backoff hint
    #: for "the first request is almost certainly still running" without
    #: widening the ABC's atomic primitive's return type for this alone.
    retry_after_seconds: int = 1

    def __init__(self, key: str, *args: Any, **kwargs: Any) -> None:
        """
        Args:
            key:    The ``Idempotency-Key`` header value that is in flight.
            args:   Forwarded to ``Exception.__init__``.
            kwargs: Forwarded to ``Exception.__init__``.
        """
        self.key = key
        super().__init__(
            f"A request with Idempotency-Key {key!r} is already in flight.",
            *args,
            **kwargs,
        )

    def error_params(self) -> dict[str, Any]:
        return {"key": self.key}


class IdempotencyFingerprintMismatchError(ServiceValidationError):
    """
    Raised when an ``Idempotency-Key`` is reused with a different
    method/path/query/body fingerprint than the request that originally
    completed it.

    Maps to HTTP 422 Unprocessable Content (inherited from
    ``ServiceValidationError``) — the status brief 005 §1's status-code
    table assigns to this exact situation.

    Thread safety:  ✅ Immutable after construction.
    Async safety:   ✅ Safe to raise in async contexts.
    """

    message_key = "varco.error.idempotency_fingerprint_mismatch"

    def __init__(self, key: str, *args: Any, **kwargs: Any) -> None:
        """
        Args:
            key:    The reused ``Idempotency-Key`` header value.
            args:   Forwarded to ``Exception.__init__``.
            kwargs: Forwarded to ``Exception.__init__``.
        """
        self.key = key
        super().__init__(
            f"Idempotency-Key {key!r} was already used with a different "
            "request (method/path/query/body do not match).",
            "Idempotency-Key",
            *args,
            **kwargs,
        )

    def error_params(self) -> dict[str, Any]:
        return {"key": self.key}


class IdempotencyKeyInvalidError(ServiceException):
    """
    Raised when the ``Idempotency-Key`` header itself is malformed — empty,
    or longer than ``IdempotencySettings.max_key_length``.

    Maps to HTTP 400 Bad Request via a registered ``ErrorCode`` (no
    existing built-in ``ServiceException`` maps to 400).

    Thread safety:  ✅ Immutable after construction.
    Async safety:   ✅ Safe to raise in async contexts.
    """

    message_key = "varco.error.idempotency_key_invalid"

    def __init__(self, detail: str, *args: Any, **kwargs: Any) -> None:
        """
        Args:
            detail: Human-readable description of what was wrong with the key.
            args:   Forwarded to ``Exception.__init__``.
            kwargs: Forwarded to ``Exception.__init__``.
        """
        self.detail = detail
        super().__init__(f"Invalid Idempotency-Key: {detail}", *args, **kwargs)

    def error_params(self) -> dict[str, Any]:
        return {"detail": self.detail}


# Register the 400 mapping for IdempotencyKeyInvalidError — module import
# time, before request handling begins (register_error_code's documented
# "call at startup only" contract).
register_error_code(
    IdempotencyKeyInvalidError,
    ErrorCode(
        code="VARCO_IDEMPOTENCY_001",
        http_status=400,
        default_message="The Idempotency-Key header is missing or malformed.",
        message_key="varco.error.idempotency_key_invalid",
    ),
)
