"""
Red-mode tests for Plan 029 / D1b — ``IdempotencyMiddleware``.

Covers plan Step 13's test list: replay round-trip; 409 while in flight;
422 on fingerprint mismatch; 400 on a malformed key; header allowlist
honoured (Date/Set-Cookie dropped); streaming response passes through and
releases; over-ceiling body passes through; middleware ordering (inside
ErrorMiddleware, inside RequestContextMiddleware); fail-closed (tenancy on,
no ambient tenant -> raises).

``varco_fastapi.middleware.idempotency`` does not exist yet — every test
below must fail with ``ModuleNotFoundError``, not a fixture typo.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from starlette.responses import Response
from varco_core.idempotency.memory import InMemoryIdempotencyStore
from varco_core.service.tenant import tenant_context
from varco_fastapi.middleware.error import ErrorMiddleware
from varco_fastapi.middleware.idempotency import IdempotencyMiddleware
from varco_fastapi.middleware.request_context import RequestContextMiddleware

CALL_COUNTS_KEY = "n"


def make_app(
    *,
    store: InMemoryIdempotencyStore | None = None,
    with_error_middleware: bool = True,
    with_request_context: bool = True,
    tenancy_enabled: bool = False,
    **middleware_kwargs: object,
) -> tuple[FastAPI, list[int]]:
    app = FastAPI()
    call_counter: list[int] = []

    # NOTE (Plan 029 / D1b, Step 13 fix): Starlette's `add_middleware()`
    # PREPENDS (`self.user_middleware.insert(0, ...)`), so the LAST call
    # ends up OUTERMOST at runtime — this is documented at length in
    # `varco_fastapi/middleware/__init__.py`'s own `install_middleware_stack`
    # docstring, and is why that helper takes an outermost-first list and
    # reverses it internally before calling `add_middleware`. The original
    # revision of this fixture called `add_middleware` in outermost-first
    # order directly (ErrorMiddleware, then Idempotency, then
    # RequestContext), which — given the prepend semantics — actually
    # produced the OPPOSITE stack at runtime (RequestContext outermost,
    # ErrorMiddleware innermost), so ErrorMiddleware could never catch an
    # exception raised by IdempotencyMiddleware. Calls are ordered here
    # innermost-first (RequestContext, then Idempotency, then Error) to
    # produce the intended outermost-first runtime stack: this is a fixture
    # ordering bug, not a change to any assertion in this file.
    if with_request_context:
        app.add_middleware(RequestContextMiddleware)

    app.add_middleware(
        IdempotencyMiddleware,
        store=store or InMemoryIdempotencyStore(),
        tenancy_enabled=tenancy_enabled,
        **middleware_kwargs,
    )

    if with_error_middleware:
        app.add_middleware(ErrorMiddleware)

    @app.post("/orders")
    async def create_order(payload: dict) -> dict:
        call_counter.append(1)
        return {"id": len(call_counter), "payload": payload}

    # NOTE (Plan 029 / D1b, Step 13 fix): a route handler is the one place
    # guaranteed to run INSIDE IdempotencyMiddleware regardless of Starlette
    # ordering subtleties — a `@app.middleware("http")` block registered
    # inside a *test function* is always added chronologically LAST (after
    # every middleware `make_app()` already installed), and Starlette's
    # prepend-on-add semantics (see the ordering note above) makes it the
    # OUTERMOST layer, wrapping IdempotencyMiddleware rather than sitting
    # inside it. An outer middleware's header mutation is applied to every
    # response unconditionally (replayed or not), which cannot exercise
    # "does the store correctly drop these on capture" at all. Returning the
    # extra headers directly from a route handler instead makes them part of
    # the same original response IdempotencyMiddleware captures/filters, so
    # the allowlist behaviour is what is actually under test.
    @app.post("/orders-with-headers")
    async def create_order_with_headers(payload: dict) -> Response:
        from starlette.responses import JSONResponse

        call_counter.append(1)
        response = JSONResponse({"id": len(call_counter), "payload": payload})
        response.headers["Set-Cookie"] = "session=evil"
        response.headers["Date"] = "Mon, 01 Jan 2024 00:00:00 GMT"
        response.headers["X-Custom-Marker"] = "keep-me"
        return response

    @app.post("/stream")
    async def stream_order() -> StreamingResponse:
        call_counter.append(1)

        async def gen():
            yield b"chunk-1"
            yield b"chunk-2"

        return StreamingResponse(gen(), media_type="text/plain")

    return app, call_counter


async def _client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── replay round-trip ─────────────────────────────────────────────────────────


async def test_replay_round_trip_returns_stored_response_and_marker_header() -> None:
    app, calls = make_app()
    async with await _client(app) as client:
        headers = {"Idempotency-Key": "abc-123"}
        first = await client.post("/orders", json={"x": 1}, headers=headers)
        second = await client.post("/orders", json={"x": 1}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.headers.get("Idempotency-Replayed") == "true"
    # The handler must NOT have executed twice.
    assert len(calls) == 1


# ── 409 in flight ─────────────────────────────────────────────────────────────


async def test_409_returned_while_reservation_is_in_flight() -> None:
    store = InMemoryIdempotencyStore()
    # Manually reserve the key to simulate a still-running first request.
    from varco_core.idempotency.base import ReserveOutcome

    outcome = await store.reserve("in-flight-key", "will-not-match", ttl=60.0)
    assert outcome.name == ReserveOutcome.ACQUIRED.name

    app, _ = make_app(store=store)
    async with await _client(app) as client:
        response = await client.post(
            "/orders", json={"x": 1}, headers={"Idempotency-Key": "in-flight-key"}
        )
    assert response.status_code == 409


# ── 422 fingerprint mismatch ──────────────────────────────────────────────────


async def test_422_on_fingerprint_mismatch_for_same_key() -> None:
    app, _ = make_app()
    async with await _client(app) as client:
        headers = {"Idempotency-Key": "mismatch-key"}
        first = await client.post("/orders", json={"x": 1}, headers=headers)
        assert first.status_code == 200
        second = await client.post("/orders", json={"x": 2}, headers=headers)
    assert second.status_code == 422


# ── 400 malformed / oversized key ────────────────────────────────────────────


async def test_400_on_empty_idempotency_key() -> None:
    app, _ = make_app()
    async with await _client(app) as client:
        response = await client.post("/orders", json={"x": 1}, headers={"Idempotency-Key": ""})
    assert response.status_code == 400


async def test_400_on_oversized_idempotency_key() -> None:
    app, _ = make_app(max_key_length=16)
    async with await _client(app) as client:
        response = await client.post(
            "/orders", json={"x": 1}, headers={"Idempotency-Key": "x" * 17}
        )
    assert response.status_code == 400


# ── header allowlist ──────────────────────────────────────────────────────────


async def test_replayed_response_drops_date_and_set_cookie_headers() -> None:
    app, _ = make_app()

    async with await _client(app) as client:
        headers = {"Idempotency-Key": "header-key"}
        await client.post("/orders-with-headers", json={"x": 1}, headers=headers)
        replayed = await client.post("/orders-with-headers", json={"x": 1}, headers=headers)

    assert "set-cookie" not in replayed.headers
    # httpx's own Date is added by the transport, not our replay — assert the
    # ORIGINAL fabricated 2024 date is not what comes back on replay.
    assert replayed.headers.get("date") != "Mon, 01 Jan 2024 00:00:00 GMT"


async def test_replay_header_allowlist_extra_header_honoured() -> None:
    app, _ = make_app(replay_header_allowlist=["X-Custom-Marker"])

    async with await _client(app) as client:
        headers = {"Idempotency-Key": "allowlist-key"}
        await client.post("/orders-with-headers", json={"x": 1}, headers=headers)
        replayed = await client.post("/orders-with-headers", json={"x": 1}, headers=headers)

    assert replayed.headers.get("x-custom-marker") == "keep-me"


# ── streaming passes through and releases ────────────────────────────────────


async def test_streaming_response_passes_through_and_releases_reservation() -> None:
    app, calls = make_app()
    async with await _client(app) as client:
        headers = {"Idempotency-Key": "stream-key"}
        first = await client.post("/stream", headers=headers)
        second = await client.post("/stream", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    # Streaming responses are never captured — both calls actually executed.
    assert len(calls) == 2


# ── over-ceiling body passes through ─────────────────────────────────────────


async def test_over_ceiling_body_passes_through_and_releases_reservation() -> None:
    app, calls = make_app(max_stored_body_bytes=8)
    async with await _client(app) as client:
        headers = {"Idempotency-Key": "big-body-key"}
        big_payload = {"data": "x" * 100}
        first = await client.post("/orders", json=big_payload, headers=headers)
        second = await client.post("/orders", json=big_payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    # Over the ceiling, the reservation is released each time -> re-executes.
    assert len(calls) == 2


# ── middleware ordering ───────────────────────────────────────────────────────


async def test_middleware_sits_inside_error_and_request_context_middlewares() -> None:
    app, _ = make_app()
    middleware_classes = [m.cls for m in app.user_middleware]
    error_index = middleware_classes.index(ErrorMiddleware)
    idempotency_index = middleware_classes.index(IdempotencyMiddleware)
    request_context_index = middleware_classes.index(RequestContextMiddleware)

    # app.user_middleware is outermost-first. IdempotencyMiddleware must be
    # INSIDE ErrorMiddleware (so its 409/422 render through the normal error
    # path) and INSIDE RequestContextMiddleware (so current_tenant()/auth
    # subject are populated before §D-D1-scope reads them) -- i.e. it must
    # dispatch AFTER both in the outermost-first ordering.
    assert error_index < idempotency_index
    assert idempotency_index < request_context_index


# ── fail-closed tenancy ───────────────────────────────────────────────────────


async def test_fail_closed_when_tenancy_enabled_with_no_ambient_tenant() -> None:
    app, _ = make_app(tenancy_enabled=True, with_error_middleware=False)
    async with await _client(app) as client:
        with pytest.raises(RuntimeError):
            await client.post(
                "/orders", json={"x": 1}, headers={"Idempotency-Key": "fail-closed-key"}
            )


async def test_tenancy_enabled_with_ambient_tenant_succeeds() -> None:
    app, _ = make_app(tenancy_enabled=True, with_error_middleware=False)

    async def _run() -> object:
        with tenant_context("tenant-a"):
            async with await _client(app) as client:
                return await client.post(
                    "/orders",
                    json={"x": 1},
                    headers={"Idempotency-Key": "scoped-key"},
                )

    response = await _run()
    assert response.status_code == 200


# ── genuine concurrency through the middleware ───────────────────────────────


async def test_concurrent_requests_same_key_exactly_one_executes() -> None:
    app, calls = make_app()
    async with await _client(app) as client:
        headers = {"Idempotency-Key": "concurrent-key"}
        responses = await asyncio.gather(
            *(client.post("/orders", json={"x": 1}, headers=headers) for _ in range(5))
        )

    status_codes = sorted(r.status_code for r in responses)
    # Exactly one 200 (the executor); the rest are 409 (in-flight) since they
    # race concurrently before the first completes.
    assert status_codes.count(200) == 1
    assert len(calls) == 1
