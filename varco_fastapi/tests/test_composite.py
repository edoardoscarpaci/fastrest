"""
tests / test_composite.py
=========================
Unit tests for the all-in-one composite deployment (:mod:`varco_fastapi.composite`).

Covers:
- Mounting + isolation: two services route independently, each keeps its own docs
- Lifespan aggregation: every sub-app's own lifespan is started and stopped (LIFO)
- Fail-fast: one sub-app failing to start aborts startup + tears down the rest
- Aggregate health: per-service breakdown, 503 when any service is unhealthy
- build_service(): scoped env overlay is applied during build and restored after
- Prefix validation: empty / bad / duplicate / colliding prefixes raise ValueError

All tests are ``async def`` (pytest-asyncio auto mode) except pure-sync ones.
Lifespan aggregation is driven by entering the composite's lifespan context
directly — no server or asgi-lifespan dependency needed.
"""

from __future__ import annotations

import os

import httpx
import pytest
from fastapi import FastAPI

from varco_fastapi.composite import (
    CompositeLifespan,
    ServiceMount,
    build_service,
    create_composite_app,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────


class _RecordingLifecycle:
    """
    A fake ``AbstractLifecycle`` that records start/stop against a shared log.

    Used to prove that each sub-app's own lifespan is driven by the composite,
    and in the correct (LIFO) order.

    Args:
        name:      Identifier recorded in the shared event log.
        events:    Shared list every instance appends ("start:<name>" / "stop:<name>").
        fail_start: If ``True``, ``start()`` raises to exercise the fail-fast path.
    """

    def __init__(
        self, name: str, events: list[str], *, fail_start: bool = False
    ) -> None:
        self._name = name
        self._events = events
        self._fail_start = fail_start

    async def start(self) -> None:
        if self._fail_start:
            # Record the attempt so tests can assert it was reached before failing.
            self._events.append(f"start-fail:{self._name}")
            raise RuntimeError(f"{self._name} boom")
        self._events.append(f"start:{self._name}")

    async def stop(self) -> None:
        self._events.append(f"stop:{self._name}")


def _service_app(title: str, *, lifecycle: object | None = None) -> FastAPI:
    """
    Build a tiny FastAPI app standing in for a real varco service.

    Args:
        title:     App title (also used to make its route body identifiable).
        lifecycle: Optional object with async start()/stop() to run in the app's
                   own lifespan (mirrors how VarcoLifespan drives components).

    Returns:
        A FastAPI app with ``GET /ping`` returning its title, ``GET /health``
        returning a status dict, and (optionally) a lifespan driving ``lifecycle``.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if lifecycle is not None:
            await lifecycle.start()  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if lifecycle is not None:
                await lifecycle.stop()  # type: ignore[attr-defined]

    app = FastAPI(title=title, lifespan=lifespan if lifecycle is not None else None)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"service": title}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


def _composite_client(app: FastAPI) -> httpx.AsyncClient:
    """Return an httpx client that calls ``app`` in-process (no network)."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test.local")


# ── 1. Mounting + isolation ───────────────────────────────────────────────────


async def test_services_route_independently():
    """Each mounted service serves its own routes under its own prefix."""
    orders = _service_app("orders")
    billing = _service_app("billing")
    app = create_composite_app(
        [ServiceMount("/orders", orders), ServiceMount("/billing", billing)]
    )

    async with _composite_client(app) as client:
        r_orders = await client.get("/orders/ping")
        r_billing = await client.get("/billing/ping")

    assert r_orders.json() == {"service": "orders"}
    assert r_billing.json() == {"service": "billing"}


async def test_each_service_keeps_its_own_docs_and_openapi():
    """Mounted sub-apps preserve their own /docs and /openapi.json."""
    orders = _service_app("orders")
    billing = _service_app("billing")
    app = create_composite_app(
        [ServiceMount("/orders", orders), ServiceMount("/billing", billing)]
    )

    async with _composite_client(app) as client:
        docs = await client.get("/orders/docs")
        schema = await client.get("/orders/openapi.json")

    assert docs.status_code == 200
    assert schema.status_code == 200
    # The sub-app's own title flows into its isolated schema.
    assert schema.json()["info"]["title"] == "orders"


async def test_landing_page_lists_services():
    """GET / returns a JSON index linking each service's docs."""
    app = create_composite_app(
        [
            ServiceMount("/orders", _service_app("orders")),
            ServiceMount("/billing", _service_app("billing")),
        ]
    )

    async with _composite_client(app) as client:
        body = (await client.get("/")).json()

    names = {s["name"] for s in body["services"]}
    assert names == {"orders", "billing"}
    docs_links = {s["docs"] for s in body["services"]}
    assert docs_links == {"/orders/docs", "/billing/docs"}


# ── 2. Lifespan aggregation ───────────────────────────────────────────────────


async def test_composite_lifespan_starts_and_stops_all_services_lifo():
    """
    The composite lifespan drives every sub-app's own lifespan, in LIFO order.

    This is the crux: Starlette does not propagate lifespan into mounted sub-apps,
    so without CompositeLifespan these components would never start.
    """
    events: list[str] = []
    orders_app = _service_app("orders", lifecycle=_RecordingLifecycle("orders", events))
    billing_app = _service_app(
        "billing", lifecycle=_RecordingLifecycle("billing", events)
    )

    lifespan = CompositeLifespan(
        [ServiceMount("/orders", orders_app), ServiceMount("/billing", billing_app)]
    )
    # Drive the lifespan directly — same contract FastAPI uses.
    async with lifespan(FastAPI()):
        # Both started, in registration order, before yield.
        assert events == ["start:orders", "start:billing"]

    # After exit: stopped in reverse (LIFO).
    assert events == [
        "start:orders",
        "start:billing",
        "stop:billing",
        "stop:orders",
    ]


# ── 3. Fail-fast ──────────────────────────────────────────────────────────────


async def test_startup_failure_aborts_and_tears_down_started_services():
    """
    If a later service fails to start, the composite startup raises and the
    already-started service is stopped (no leak).
    """
    events: list[str] = []
    good_app = _service_app("good", lifecycle=_RecordingLifecycle("good", events))
    bad_app = _service_app(
        "bad", lifecycle=_RecordingLifecycle("bad", events, fail_start=True)
    )

    lifespan = CompositeLifespan(
        [ServiceMount("/good", good_app), ServiceMount("/bad", bad_app)]
    )

    with pytest.raises(RuntimeError, match="bad boom"):
        async with lifespan(FastAPI()):
            pytest.fail("composite startup should not complete")  # pragma: no cover

    # good started, bad attempted+failed, then good was torn down (LIFO unwind).
    assert events == ["start:good", "start-fail:bad", "stop:good"]


# ── 4. Aggregate health ───────────────────────────────────────────────────────


async def test_aggregate_health_reports_per_service_and_503_on_unhealthy():
    """Aggregate /health returns a per-service breakdown and 503 if any is down."""
    healthy = _service_app("healthy")

    # A service whose /health reports unhealthy.
    unhealthy = FastAPI(title="unhealthy")

    @unhealthy.get("/health")
    async def _bad_health() -> dict[str, str]:
        return {"status": "unhealthy"}

    app = create_composite_app(
        [ServiceMount("/ok", healthy), ServiceMount("/bad", unhealthy)]
    )

    async with _composite_client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["services"]["ok"]["status"] == "healthy"
    assert body["services"]["bad"]["status"] == "unhealthy"


async def test_aggregate_health_all_healthy_returns_200():
    """Aggregate /health returns 200 when every service is healthy."""
    app = create_composite_app(
        [
            ServiceMount("/a", _service_app("a")),
            ServiceMount("/b", _service_app("b")),
        ]
    )

    async with _composite_client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_aggregate_health_missing_endpoint_marked_unhealthy():
    """A service with no health endpoint is reported unhealthy, not fatal."""
    no_health = FastAPI(title="no-health")

    @no_health.get("/ping")
    async def _ping() -> dict[str, str]:
        return {"ok": "yes"}

    app = create_composite_app([ServiceMount("/nh", no_health)])

    async with _composite_client(app) as client:
        resp = await client.get("/health")

    # 404 from the sub-app's missing /health → derived "unhealthy".
    assert resp.status_code == 503
    assert resp.json()["services"]["nh"]["status"] == "unhealthy"


# ── 5. build_service() env scoping ────────────────────────────────────────────


async def test_build_service_applies_and_restores_scoped_env():
    """
    build_service overlays env only during the factory call and restores it,
    letting two services read the same bare env var with different values.
    """
    captured: dict[str, str] = {}

    def make_factory(key: str):
        def factory() -> FastAPI:
            # Read the bare env var at build time, like a real service would.
            captured[key] = os.environ["COMPOSITE_TEST_DB_URL"]
            return _service_app(key)

        return factory

    # Ensure the key is unset beforehand so we test the "restore to unset" path.
    os.environ.pop("COMPOSITE_TEST_DB_URL", None)

    orders = build_service(
        "/orders",
        make_factory("orders"),
        env={"COMPOSITE_TEST_DB_URL": "postgres://orders"},
    )
    billing = build_service(
        "/billing",
        make_factory("billing"),
        env={"COMPOSITE_TEST_DB_URL": "postgres://billing"},
    )

    # Each factory saw its own scoped value.
    assert captured == {
        "orders": "postgres://orders",
        "billing": "postgres://billing",
    }
    # The overlay was removed — env restored to its prior (unset) state.
    assert "COMPOSITE_TEST_DB_URL" not in os.environ
    assert orders.prefix == "/orders"
    assert billing.prefix == "/billing"


async def test_build_service_restores_env_even_when_factory_raises():
    """A failing factory must not leak overlay values into the environment."""
    os.environ.pop("COMPOSITE_TEST_LEAK", None)

    def boom() -> FastAPI:
        raise ValueError("factory failed")

    with pytest.raises(ValueError, match="factory failed"):
        build_service("/x", boom, env={"COMPOSITE_TEST_LEAK": "leaked"})

    assert "COMPOSITE_TEST_LEAK" not in os.environ


async def test_build_service_restores_prior_value_not_just_unset():
    """An env key that already had a value is restored to that value."""
    os.environ["COMPOSITE_TEST_PRIOR"] = "original"
    try:

        def factory() -> FastAPI:
            assert os.environ["COMPOSITE_TEST_PRIOR"] == "overlay"
            return _service_app("svc")

        build_service("/svc", factory, env={"COMPOSITE_TEST_PRIOR": "overlay"})
        assert os.environ["COMPOSITE_TEST_PRIOR"] == "original"
    finally:
        os.environ.pop("COMPOSITE_TEST_PRIOR", None)


# ── 6. Prefix validation ──────────────────────────────────────────────────────


def test_empty_services_raises():
    with pytest.raises(ValueError, match="at least one ServiceMount"):
        create_composite_app([])


def test_prefix_without_leading_slash_raises():
    with pytest.raises(ValueError, match="must be non-empty and start"):
        create_composite_app([ServiceMount("orders", _service_app("o"))])


def test_root_prefix_raises():
    with pytest.raises(ValueError, match="not allowed"):
        create_composite_app([ServiceMount("/", _service_app("o"))])


def test_duplicate_prefix_raises():
    with pytest.raises(ValueError, match="Duplicate mount prefix"):
        create_composite_app(
            [
                ServiceMount("/orders", _service_app("a")),
                ServiceMount("/orders", _service_app("b")),
            ]
        )


def test_prefix_colliding_with_health_path_raises():
    with pytest.raises(ValueError, match="collides with the composite health path"):
        create_composite_app(
            [ServiceMount("/health", _service_app("h"))], health_path="/health"
        )


# ── ServiceMount name derivation ──────────────────────────────────────────────


def test_service_mount_name_defaults_to_stripped_prefix():
    assert ServiceMount("/orders", FastAPI()).resolved_name() == "orders"
    assert ServiceMount("/orders", FastAPI(), name="ord").resolved_name() == "ord"
