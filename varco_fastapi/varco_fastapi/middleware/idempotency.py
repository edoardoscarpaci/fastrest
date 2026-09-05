"""
varco_fastapi.middleware.idempotency
=====================================
``IdempotencyMiddleware`` — the HTTP adapter for D1's ``Idempotency-Key``
support (Plan 029 / D1b, Step 10).

Only the middleware lives here — the storage contract
(``AbstractIdempotencyStore``), the record value object, the fingerprint
function, and the in-memory default all live in ``varco_core.idempotency``
(§D-D1-home). This module never defines storage behaviour; it only wires
HTTP semantics (headers, status codes, streaming detection) onto that
contract.

**Opt-in, never global-by-default** (§D-D1-optin). Register it explicitly
via ``install_middleware_stack`` — it is not added by
``create_varco_app()``. **Placement is a correctness requirement, not a
preference**: it must sit INSIDE ``ErrorMiddleware`` (so its 409/422/400
render through the normal RFC-9457-shaped error path) and INSIDE
``RequestContextMiddleware`` (so ``current_tenant()``/the ambient
``AuthContext`` are populated before this middleware's scoping logic reads
them)::

    install_middleware_stack(app, [
        ErrorMiddleware,
        (RequestContextMiddleware, {"server_auth": auth}),
        (IdempotencyMiddleware, {"store": store}),
    ])

Usage::

    from varco_core.idempotency.memory import InMemoryIdempotencyStore
    from varco_fastapi.middleware.idempotency import IdempotencyMiddleware

    app.add_middleware(IdempotencyMiddleware, store=InMemoryIdempotencyStore())

DESIGN: content-length presence as the streaming/oversized-body signal
    ``BaseHTTPMiddleware.call_next()`` always hands back a response whose
    body is only reachable via ``body_iterator`` — there is no reliable way
    to ask "did the route handler return a ``StreamingResponse``?" once the
    response has passed through Starlette's ASGI machinery. A genuine
    ``StreamingResponse`` (chunked, unknown total length) never carries a
    ``Content-Length`` header; a normal ``JSONResponse``/``Response`` always
    does. Using that header's presence (and value, against
    ``max_stored_body_bytes``) as the "can this be captured?" signal means:
    ✅ Correctly identifies both cases §D-D1-replay requires — streaming
       responses AND over-ceiling responses both release the reservation
       and pass through unread, with **zero** extra bytes consumed for the
       over-ceiling case (the check happens before touching the iterator).
    ✅ No dependency on FastAPI/Starlette response *types* — this would
       break the moment a handler wraps a body in a different response
       subclass that still sets ``Content-Length``.
    ❌ A pathological handler that manually sets ``Content-Length`` on a
       ``StreamingResponse`` (unusual, and arguably itself a bug — the
       header would be a lie about a chunked body) would be captured as if
       it were a normal response. Accepted: not a shape any observed
       framework code produces.

Thread safety:  ✅ Stateless — the store instance is the only shared state,
                and it owns its own concurrency safety (§D-D1-atomic).
Async safety:   ✅ ``dispatch`` is ``async def``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import cast

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp
from varco_core.exception.idempotency import (
    IdempotencyFingerprintMismatchError,
    IdempotencyKeyInvalidError,
)
from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
from varco_core.idempotency.fingerprint import compute_fingerprint
from varco_core.idempotency.record import IdempotencyRecord
from varco_core.service.tenant import current_tenant

_logger = logging.getLogger(__name__)

_KEY_HEADER = "Idempotency-Key"
_REPLAYED_HEADER = "Idempotency-Replayed"

# §D-D1-replay: replayed unconditionally, regardless of the allowlist.
_ALWAYS_REPLAY_HEADERS = frozenset({"content-type", "location", "content-language"})

# §D-D1-replay: NEVER replayed, hard-coded — these either change with time,
# leak a second identity for the same logical response (Set-Cookie), or would
# make two distinct requests indistinguishable in the trace backend
# (correlation headers).
_NEVER_REPLAY_HEADERS = frozenset(
    {
        "date",
        "set-cookie",
        "cache-control",
        "age",
        "expires",
        "etag",
        "server",
        "content-length",
        "x-request-id",
        "x-correlation-id",
    }
)

#: Import-time deferred to avoid a hard varco_fastapi -> varco_fastapi.context
#: circular concern at module load; resolved lazily inside the dispatch path.
_MISSING = object()


def _coerce_chunk_to_bytes(chunk: str | bytes | memoryview) -> bytes:
    """
    Normalize one ``body_iterator`` chunk to ``bytes``.

    Starlette's ``StreamingResponse.body_iterator`` may yield ``str``,
    ``bytes``, or ``memoryview`` depending on how the route handler built
    its response — captured chunks must be joined into one ``bytes`` value
    for ``IdempotencyRecord.body``.
    """
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, memoryview):
        return bytes(chunk)
    return chunk.encode("utf-8")


def _filtered_replay_headers(
    headers: Mapping[str, str], allowlist: Iterable[str]
) -> dict[str, str]:
    """
    Build the header subset that is safe to replay verbatim (§D-D1-replay).

    Args:
        headers:   The original response headers (already lower-cased keys,
                   as Starlette's ``Headers.items()`` yields).
        allowlist: Additional header names (any case) that should also be
                   replayed, beyond the hard-coded always-replay set.

    Returns:
        A new dict containing only the headers that are safe to replay.
    """
    allow_lower = {name.lower() for name in allowlist}
    result: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in _NEVER_REPLAY_HEADERS:
            continue
        if lowered in _ALWAYS_REPLAY_HEADERS or lowered in allow_lower:
            result[key] = value
    return result


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware implementing the ``Idempotency-Key`` HTTP contract (D1).

    Args:
        app:                      The ASGI application to wrap.
        store:                    The ``AbstractIdempotencyStore`` backing
                                  reservations/records. Required — there is
                                  deliberately no default here (unlike most
                                  varco_fastapi middlewares) because silently
                                  falling back to ``InMemoryIdempotencyStore``
                                  behind a multi-process deployment would be
                                  a silent correctness trap (its own
                                  docstring's warning).
        methods:                 The HTTP methods this middleware applies
                                  to. Default ``{"POST", "PATCH"}`` — D1's
                                  scope per the plan's Non-goals (GET/PUT/
                                  DELETE are already idempotent per RFC 9110).
        require_key:              If ``True``, a request of an applicable
                                  method with no ``Idempotency-Key`` header
                                  is rejected with 400. Default ``False``.
        ttl:                      Seconds a reservation/record remains
                                  valid. Default ``86400.0`` (24h, following
                                  Stripe — §D-D1-ttl).
        max_key_length:            Maximum accepted ``Idempotency-Key``
                                  length. Default ``255``.
        max_stored_body_bytes:     Ceiling on a captured response body.
                                  Default ``1_048_576`` (1 MiB).
        replay_header_allowlist:   Extra header names replayed verbatim on a
                                  cache hit, beyond the hard-coded
                                  Content-Type/Location/Content-Language set.
        tenancy_enabled:           Whether to scope the storage key by
                                  ``current_tenant()`` (§D-D1-scope). Fails
                                  closed: if ``True`` and no ambient tenant
                                  is set, ``dispatch`` raises ``RuntimeError``
                                  rather than emitting an unscoped key.
        include_paths:             If given, only paths matching one of
                                  these prefixes are processed; all others
                                  pass through untouched.
        exclude_paths:             Paths matching one of these prefixes are
                                  never processed, even if they also match
                                  ``include_paths``. Same convention as
                                  ``RequestLoggingMiddleware.skip_paths``.

    Thread safety:  ✅ Stateless aside from the injected store.
    Async safety:   ✅ ``dispatch`` is ``async def``.

    Edge cases:
        - A request whose method is not in ``methods`` passes through
          untouched — no header is read, no store call is made.
        - An empty or over-length ``Idempotency-Key`` raises
          ``IdempotencyKeyInvalidError`` (400) regardless of ``require_key``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: AbstractIdempotencyStore,
        methods: frozenset[str] = frozenset({"POST", "PATCH"}),
        require_key: bool = False,
        ttl: float = 86400.0,
        max_key_length: int = 255,
        max_stored_body_bytes: int = 1_048_576,
        replay_header_allowlist: Iterable[str] = (),
        tenancy_enabled: bool = False,
        include_paths: Iterable[str] | None = None,
        exclude_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._store = store
        self._methods = frozenset(m.upper() for m in methods)
        self._require_key = require_key
        self._ttl = ttl
        self._max_key_length = max_key_length
        self._max_stored_body_bytes = max_stored_body_bytes
        self._replay_header_allowlist = tuple(replay_header_allowlist)
        self._tenancy_enabled = tenancy_enabled
        self._include_paths = tuple(include_paths) if include_paths is not None else None
        self._exclude_paths = tuple(exclude_paths) if exclude_paths is not None else ()

    def _applies_to(self, request: Request) -> bool:
        """Whether this request's method/path is in scope for D1 handling."""
        if request.method.upper() not in self._methods:
            return False
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._exclude_paths):
            return False
        if self._include_paths is not None:
            return any(path.startswith(prefix) for prefix in self._include_paths)
        return True

    def _scope_key(self, key: str) -> str:
        """
        Build the tenant/subject-scoped storage key (§D-D1-scope).

        Raises:
            RuntimeError: ``tenancy_enabled`` is ``True`` and
                ``current_tenant()`` is unset — fails closed rather than
                emitting an unnamespaced (cross-tenant-leak-prone) key, the
                same rule ``tenancy_cache_key()``/``localization_cache_key()``
                already enforce.

        Edge cases:
            - With tenancy disabled and no ambient auth subject (the
              overwhelming common case — no multitenancy, no auth
              middleware), the returned key is the **bare, unprefixed**
              ``key`` — byte-identical to what a caller manually driving
              the store directly would use. Namespacing is added only when
              there is something real to namespace by, rather than
              unconditionally prefixing every key with a literal ``"-"``
              placeholder that carries no information.
        """
        if self._tenancy_enabled:
            tenant = current_tenant()
            if tenant is None:
                raise RuntimeError(
                    "IdempotencyMiddleware: tenancy_enabled=True but "
                    "current_tenant() is unset. A TENANT-scoped idempotency "
                    "key is never emitted unnamespaced — wrap the request "
                    "with `with tenant_context(tenant_id): ...` (typically "
                    "done by RequestContextMiddleware/TenantResolutionMiddleware)."
                )
            subject = self._current_subject()
            return f"idempotency:{tenant}:{subject}:{key}"

        subject = self._current_subject()
        if subject == "-":
            return key
        return f"idempotency:-:{subject}:{key}"

    def _current_subject(self) -> str:
        """
        Best-effort ambient auth subject, or ``"-"`` when unavailable.

        Deliberately tolerant of ``varco_fastapi.context`` raising or
        returning ``None`` (no ``RequestContextMiddleware``, or an anonymous
        caller) — unlike tenant scoping, an unauthenticated caller is not a
        cross-tenant leak risk by itself; the tenant fails closed, the
        subject degrades gracefully.
        """
        try:
            from varco_fastapi.context import get_auth_context_or_none

            ctx = get_auth_context_or_none()
        except Exception:  # noqa: BLE001 — best-effort only
            return "-"
        if ctx is None or ctx.user_id is None:
            return "-"
        return str(ctx.user_id)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Enforce the ``Idempotency-Key`` contract for in-scope requests.

        Returns:
            The replayed response on a cache hit, the fresh response on a
            first execution, or the untouched downstream response for
            out-of-scope/streaming/over-ceiling cases.

        Raises:
            IdempotencyKeyInvalidError: The header is missing (when
                ``require_key=True``), empty, or over ``max_key_length``.
            IdempotencyKeyConflictError: A reservation is in flight for this
                key (409 — rendered by ``ErrorMiddleware``, which must sit
                outside this middleware).
            IdempotencyFingerprintMismatchError: The key was reused with a
                different method/path/query/body (422 — same rendering
                requirement).
            RuntimeError: §D-D1-scope's fail-closed tenancy check.
        """
        if not self._applies_to(request):
            return await call_next(request)

        raw_key = request.headers.get(_KEY_HEADER)
        if raw_key is None:
            if self._require_key:
                raise IdempotencyKeyInvalidError("Idempotency-Key header is required.")
            return await call_next(request)

        if raw_key == "" or len(raw_key) > self._max_key_length:
            raise IdempotencyKeyInvalidError(
                f"Idempotency-Key must be non-empty and at most "
                f"{self._max_key_length} characters (got length {len(raw_key)})."
            )

        body = await request.body()
        fingerprint = compute_fingerprint(request.method, request.url.path, request.url.query, body)
        storage_key = self._scope_key(raw_key)

        outcome = await self._store.reserve(storage_key, fingerprint, ttl=self._ttl)

        if outcome is ReserveOutcome.IN_FLIGHT:
            from varco_core.exception.idempotency import IdempotencyKeyConflictError

            raise IdempotencyKeyConflictError(raw_key)

        if outcome is ReserveOutcome.REPLAY:
            record = await self._store.get(storage_key)
            if record is None:
                # Defensive: the record vanished between reserve() and get()
                # (e.g. concurrent expiry). Treat as a fresh execution rather
                # than raising — the reservation was already re-acquired
                # implicitly by this call's own reserve(), so proceed exactly
                # like the ACQUIRED path below.
                return await self._execute_and_capture(request, call_next, storage_key, fingerprint)
            if record.fingerprint != fingerprint:
                raise IdempotencyFingerprintMismatchError(raw_key)
            headers = dict(record.headers)
            headers[_REPLAYED_HEADER] = "true"
            return Response(content=record.body, status_code=record.status, headers=headers)

        # ACQUIRED
        return await self._execute_and_capture(request, call_next, storage_key, fingerprint)

    async def _execute_and_capture(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
        storage_key: str,
        fingerprint: str,
    ) -> Response:
        """
        Run the downstream handler and, if capturable, store its response.

        See the module DESIGN block for why ``Content-Length`` presence is
        the streaming/oversized signal.
        """
        response = await call_next(request)

        content_length_header = response.headers.get("content-length")
        if content_length_header is None:
            # Genuine streaming response (§D-D1-replay) — never captured.
            await self._store.release(storage_key)
            return response

        try:
            content_length = int(content_length_header)
        except ValueError:
            content_length = self._max_stored_body_bytes + 1  # treat as over-ceiling

        if content_length > self._max_stored_body_bytes:
            # Over the ceiling — release without consuming a single body
            # byte, so the original (still-unread) response streams to the
            # client exactly as the handler produced it.
            await self._store.release(storage_key)
            return response

        # `call_next()` is typed as returning a plain `Response`, but at
        # runtime Starlette's `BaseHTTPMiddleware` always hands back its own
        # `_StreamingResponse` wrapper carrying `body_iterator` — the module
        # DESIGN block explains why we rely on this shape rather than a
        # response-type check. mypy only sees the public `Response` type, so
        # this attribute access needs an explicit acknowledgement.
        streaming_response = cast("StreamingResponse", response)
        body_chunks = [section async for section in streaming_response.body_iterator]
        body = b"".join(_coerce_chunk_to_bytes(chunk) for chunk in body_chunks)

        replay_headers = _filtered_replay_headers(
            dict(response.headers), self._replay_header_allowlist
        )
        record = IdempotencyRecord(
            status=response.status_code,
            body=body,
            headers=replay_headers,
            fingerprint=fingerprint,
        )
        await self._store.complete(storage_key, record)

        # Rebuild the response since body_iterator has been fully consumed —
        # strip Content-Length so Response recomputes it from `body`.
        rebuilt_headers = {k: v for k, v in response.headers.items() if k != "content-length"}
        return Response(
            content=body,
            status_code=response.status_code,
            headers=rebuilt_headers,
            media_type=response.media_type,
        )


__all__ = ["IdempotencyMiddleware"]
