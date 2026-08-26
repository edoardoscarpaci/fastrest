"""
varco_fastapi.composite
========================
All-in-one composite deployment: combine several independently-built varco
services into a single ASGI process without changing any of them.

Each microservice is built exactly as it is today via ``create_varco_app()``
(:mod:`varco_fastapi.app`) — its own :class:`DIContainer`, its own database,
its own middleware stack, its own ``/docs``.  ``create_composite_app`` mounts
each one under a path prefix (``/orders/*``, ``/billing/*``) so they run in one
process while remaining fully isolated.

Usage::

    from orders_service.app import app as orders_app      # its own create_varco_app()
    from billing_service.app import app as billing_app     # its own container + DB

    composite = create_composite_app([
        ServiceMount("/orders", orders_app),
        ServiceMount("/billing", billing_app),
    ])
    # uvicorn composite:composite

Why a dedicated composite instead of ``root.mount(...)`` directly
-----------------------------------------------------------------
Mounting a full ``FastAPI`` app as an ASGI sub-application preserves its routes,
middleware, exception handlers and ``/docs`` — but Starlette's ``Router.lifespan``
only runs the **root** app's lifespan and never descends into mounted sub-apps.
Every varco service wires its DB pools, ``AbstractEventBus``, ``OutboxRelay`` and
job runners through the app's lifespan (:class:`varco_fastapi.lifespan.VarcoLifespan`).
Mount them naively and none of that startup ever runs.  :class:`CompositeLifespan`
is the one piece doing real work here: it drives every sub-app's own lifespan so
each service starts and stops exactly as it would standalone.

DESIGN: mount pre-built apps vs. merge routers into one container
    ✅ Strongest isolation — each service keeps its own container, DB, env,
       middleware and docs; combining them changes nothing about how they run
    ✅ Purely additive — existing service code is untouched (no re-wiring)
    ✅ Each sub-app serves its own ``{prefix}/docs`` + ``{prefix}/openapi.json``
    ❌ No single merged OpenAPI schema across services (each is browsed separately)
    ❌ No cross-service DI sharing — intentional; services stay decoupled
    Alternative considered: collect every VarcoRouter onto one FastAPI + one
    container.  Rejected — two services' same-typed singletons (AsyncEngine,
    settings) would collide, breaking the "different database, different
    environment" guarantee that is the whole point of this feature.

Schema migrations in a composite
--------------------------------
No composite-level code is needed for correctness: because
:class:`CompositeLifespan` drives each sub-app's own lifespan, each service's
own ``MigrationLifecycle`` (:mod:`varco_fastapi.migrate`) runs with its own
:class:`~varco_core.migration.MigrationSettings` against its own database,
exactly as it would standalone.  Two operational properties follow and must be
planned for:

- **Startup time is the sum, not the max.**  Composite startup is serial by
  design (fail-fast, so one service's failure aborts the process), which means
  N services migrate one after another.  Budget ``N × VARCO_MIGRATE_TIMEOUT``
  in the readiness probe's ``initialDelaySeconds``, not one timeout.
- **Two services sharing one database converge on the same lock
  automatically.**  The default lock key is the literal ``"varco:migrate"`` and
  PostgreSQL advisory locks are already scoped per-database, so two composite
  members pointed at the same DB serialize with no configuration at all.  Two
  services on *different* databases never contend.  Set ``lock_key=`` /
  ``VARCO_MIGRATE_LOCK_KEY`` only for schema-per-service setups that want finer
  granularity.

See ``technical_docs/features/schema-migrations.md`` for the full migration
model, and ``technical_docs/features/composite-deployment.md`` for this module.

Thread safety:  ⚠️ ``create_composite_app`` / ``build_service`` are build-time
                only — call them during startup, not concurrently at runtime.
Async safety:   ✅ :class:`CompositeLifespan` is an async context manager and
                aggregate health runs sub-app probes concurrently.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    # Imported only for type hints — keeps import-time cost off the hot path and
    # avoids forcing fastapi/httpx import order at module load.
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)

# Worst-case severity ordering for aggregate health — mirrors the reduction in
# ``varco_fastapi.router.health._aggregate_status`` so the composite and each
# sub-app agree on what "worse" means.
_STATUS_SEVERITY: Final[dict[str, int]] = {
    "healthy": 0,
    "degraded": 1,
    "unhealthy": 2,
}


# ── ServiceMount ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceMount:
    """
    A single already-built varco service to mount into a composite deployment.

    Args:
        prefix:      URL prefix the service is mounted under (e.g. ``"/orders"``).
                     Must be non-empty and start with ``"/"``; must be unique
                     across the composite and must not collide with the
                     composite's own root health path.
        app:         The ``FastAPI`` instance produced by that service's own
                     ``create_varco_app(...)`` call.  Mounted verbatim — its
                     middleware, exception handlers and ``/docs`` are preserved.
        name:        Human-readable key used in aggregate-health output.
                     Defaults to ``prefix`` with slashes stripped (``"orders"``).
        health_path: The sub-app path the aggregate health probe calls in-process
                     (default ``"/health"`` — the path ``create_varco_app`` mounts
                     its :class:`HealthRouter` under).

    Edge cases:
        - ``name`` left as ``None`` → derived from ``prefix`` at validation time.
        - A service with no health endpoint → its aggregate-health entry is
          reported as ``unhealthy`` with the probe error as detail (never raises).

    Thread safety:  ✅ Frozen — immutable and hashable after construction.
    """

    prefix: str
    app: FastAPI
    name: str | None = None
    health_path: str = "/health"

    def resolved_name(self) -> str:
        """
        Return the health-key name for this mount.

        Returns:
            ``name`` if set, otherwise ``prefix`` with leading/trailing slashes
            stripped (e.g. ``"/orders"`` → ``"orders"``).  Falls back to the raw
            prefix if stripping yields an empty string.
        """
        if self.name:
            return self.name
        return self.prefix.strip("/") or self.prefix


# ── CompositeLifespan ─────────────────────────────────────────────────────────


class CompositeLifespan:
    """
    FastAPI ``lifespan`` that drives every mounted sub-app's own lifespan.

    Starlette does not propagate lifespan events into mounted sub-applications,
    so this context manager enters each sub-app's ``lifespan_context`` explicitly
    on startup and exits them in reverse (LIFO) on shutdown — the same fail-fast /
    LIFO philosophy as :class:`varco_fastapi.lifespan.VarcoLifespan`.

    Args:
        services: The mounted services, in startup order.  Each is started after
                  the ones before it and stopped before them (LIFO shutdown).

    Usage::

        lifespan = CompositeLifespan([orders_mount, billing_mount])
        app = FastAPI(lifespan=lifespan)

    Edge cases:
        - A sub-app's startup raises → already-started sub-apps are torn down
          (via ``AsyncExitStack`` unwinding) and the composite startup fails.
          The whole process refuses to serve traffic — no half-broken deployment.
        - A sub-app's shutdown raises → ``AsyncExitStack`` still unwinds the
          remaining contexts; the error propagates after cleanup.

    Async safety:   ✅ ``__call__`` is an async context manager (FastAPI lifespan).
    Thread safety:  ⚠️ Not reentrant — one composite app, one lifespan run.
    """

    __slots__ = ("_services",)

    def __init__(self, services: list[ServiceMount]) -> None:
        # Copy so later mutation of the caller's list can't change startup order.
        self._services: list[ServiceMount] = list(services)

    def __repr__(self) -> str:
        names = ", ".join(s.resolved_name() for s in self._services)
        return f"CompositeLifespan(services=[{names}])"

    @asynccontextmanager
    async def __call__(self, app: Any) -> AsyncIterator[None]:
        """
        Start every sub-app's lifespan, yield to FastAPI, then stop them (LIFO).

        Args:
            app: The composite ``FastAPI`` app (passed by FastAPI; unused here —
                 sub-app lifespans receive their *own* app instance).

        Yields:
            Nothing — control returns to FastAPI's request-handling loop.

        Raises:
            Exception: Re-raises whatever a sub-app's startup raised, after
                       tearing down the sub-apps that had already started.

        Async safety: ✅ ``AsyncExitStack`` guarantees LIFO teardown of exactly
                      the contexts that were successfully entered, on both the
                      happy path and the startup-failure path.
        """
        # AsyncExitStack gives us fail-fast + LIFO teardown for free: if entering
        # service N's lifespan raises, the stack unwinds services 0..N-1 that were
        # already entered, then the exception propagates and startup fails.
        async with AsyncExitStack() as stack:
            for service in self._services:
                sub_app = service.app
                try:
                    # Enter the sub-app's OWN lifespan (VarcoLifespan et al.).
                    # This is what actually starts its DB pool / bus / outbox.
                    await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
                    _logger.info(
                        "CompositeLifespan: started service %r at %s",
                        service.resolved_name(),
                        service.prefix,
                    )
                except Exception:
                    _logger.error(
                        "CompositeLifespan: service %r failed to start at %s — "
                        "aborting composite startup",
                        service.resolved_name(),
                        service.prefix,
                        exc_info=True,
                    )
                    # Re-raise: the stack unwinds already-started services and
                    # FastAPI turns this into a failed lifespan startup.
                    raise
            # All services started — hand control to FastAPI.
            yield
        # Stack exit here stops every started service in LIFO order.


# ── Composite app factory ─────────────────────────────────────────────────────


def create_composite_app(
    services: list[ServiceMount],
    *,
    title: str = "Varco Composite Deployment",
    version: str = "0.1.0",
    description: str = "",
    aggregate_health: bool = True,
    health_path: str = "/health",
    landing_page: bool = True,
) -> FastAPI:
    """
    Combine several already-built varco services into one deployable app.

    Each ``ServiceMount`` is mounted as an ASGI sub-application under its prefix.
    A :class:`CompositeLifespan` drives every sub-app's own lifespan so DB pools,
    event buses and outbox relays start and stop exactly as they would standalone.

    Args:
        services:         Services to mount.  Must be non-empty; prefixes must be
                          unique, start with ``"/"``, and not collide with
                          ``health_path`` or the landing route ``"/"``.
        title:            OpenAPI title for the composite root app.
        version:          OpenAPI version for the composite root app.
        description:      OpenAPI description for the composite root app.
        aggregate_health: If ``True``, expose ``GET {health_path}`` on the root
                          that probes every sub-app's own health in-process and
                          returns a per-service breakdown (503 if any is unhealthy).
        health_path:      Root path for the aggregate health endpoint.
        landing_page:     If ``True``, expose ``GET /`` returning a JSON index that
                          links each service's ``{prefix}/docs``.

    Returns:
        A ``FastAPI`` instance with every service mounted and the composite
        lifespan installed.  Assign it to a module-level name for ``uvicorn``.

    Raises:
        ValueError: ``services`` is empty, or a prefix is invalid / duplicated /
                    collides with ``health_path`` or ``"/"``.

    Example::

        composite = create_composite_app([
            ServiceMount("/orders", orders_app),
            ServiceMount("/billing", billing_app),
        ])

    Edge cases:
        - ``aggregate_health=False`` → no root health endpoint is added; each
          sub-app's own ``{prefix}/health`` still works.
        - Two services both mount their own ``/health`` internally → unaffected;
          those live at ``{prefix}/health`` and never collide with the root one.

    Async safety:   ✅ The returned app's lifespan is async and starts sub-apps
                    sequentially in registration order.
    Thread safety:  ⚠️ Build-time only — construct the composite at startup.
    """
    # Import here (not at module top) so importing this module never forces the
    # full fastapi import chain before it's needed — matches app.py's lazy style.
    from fastapi import FastAPI  # noqa: PLC0415

    _validate_mounts(services, health_path=health_path)

    app = FastAPI(
        title=title,
        version=version,
        description=description,
        lifespan=CompositeLifespan(services),
    )

    # Mount each service verbatim — Starlette routes the whole subtree to the
    # sub-app, preserving its middleware, exception handlers and docs.
    for service in services:
        app.mount(service.prefix, service.app, name=service.resolved_name())

    if aggregate_health:
        _install_aggregate_health(app, services, health_path=health_path)

    if landing_page:
        _install_landing_page(app, services)

    return app


# ── build_service (optional scoped-env construction) ──────────────────────────


def build_service(
    prefix: str,
    factory: Callable[[], FastAPI],
    *,
    env: Mapping[str, str] | None = None,
    name: str | None = None,
    health_path: str = "/health",
) -> ServiceMount:
    """
    Build a service via ``factory`` under a scoped environment, then wrap it.

    All varco services in a composite share one OS process and therefore one
    ``os.environ``.  Runtime isolation is automatic — each app captures its own
    container/engine as plain objects — but *build-time* env reads can collide:
    if two services both read ``os.environ["DATABASE_URL"]`` they see the same
    value.  ``build_service`` overlays ``env`` for the duration of the ``factory``
    call and restores the previous environment afterwards, so each service can be
    built against its own configuration even under identical bare env-var names.

    Args:
        prefix:      Mount prefix for the resulting :class:`ServiceMount`.
        factory:     Zero-argument callable that builds and returns the service's
                     ``FastAPI`` app (typically wraps ``create_varco_app(...)``).
        env:         Environment overlay applied only while ``factory`` runs.
                     ``None`` means no overlay (behaves like calling ``factory``
                     then wrapping the result).
        name:        Optional health-key name (see :class:`ServiceMount`).
        health_path: Sub-app health path for aggregate probing.

    Returns:
        A :class:`ServiceMount` wrapping the freshly built app.

    Raises:
        Exception: Whatever ``factory`` raises — the previous environment is
                   restored first (``finally``), so a failed build never leaks
                   overlay values into the process environment.

    Example::

        orders = build_service(
            "/orders",
            lambda: create_orders_app(),
            env={"DATABASE_URL": "postgresql+asyncpg://.../orders"},
        )
        billing = build_service(
            "/billing",
            lambda: create_billing_app(),
            env={"DATABASE_URL": "postgresql+asyncpg://.../billing"},
        )
        composite = create_composite_app([orders, billing])

    Edge cases:
        - A key in ``env`` was previously unset → it is removed again on restore
          (not left as an empty string).
        - ``factory`` reads env lazily (after it returns) rather than during the
          call → the overlay will already be gone.  ⚠️ Factories must read their
          configuration synchronously inside the call for isolation to hold.

    Thread safety:  ❌ Mutates ``os.environ`` for the duration of the call —
                    do NOT call concurrently with other env-reading code.  Intended
                    for single-threaded startup only.
    """
    if env is None:
        # No overlay requested — build and wrap directly.
        return ServiceMount(prefix=prefix, app=factory(), name=name, health_path=health_path)

    # Snapshot only the keys we are about to touch so restore is exact — a key
    # that was previously unset must end up unset again, not "" .
    _MISSING: Final = object()
    saved: dict[str, str | object] = {k: os.environ.get(k, _MISSING) for k in env}
    try:
        os.environ.update(env)
        app = factory()
    finally:
        # Restore prior environment exactly, even if factory() raised.
        for key, prior in saved.items():
            if prior is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior  # type: ignore[assignment]

    return ServiceMount(prefix=prefix, app=app, name=name, health_path=health_path)


# ── Validation ────────────────────────────────────────────────────────────────


def _validate_mounts(services: list[ServiceMount], *, health_path: str) -> None:
    """
    Validate mount prefixes before building the composite app.

    Args:
        services:    The mounts to validate.
        health_path: The composite's own root health path (must not be shadowed).

    Raises:
        ValueError: ``services`` is empty, or a prefix is empty / missing a leading
                    slash / duplicated / equal to ``"/"`` / equal to ``health_path``.

    Edge cases:
        - Prefix ``"/"`` is rejected — a service mounted at root would swallow the
          composite's own health and landing routes.
        - Duplicate prefixes are rejected up front rather than letting the second
          silently shadow the first at request time.
    """
    if not services:
        raise ValueError(
            "create_composite_app requires at least one ServiceMount, got an "
            "empty list. Pass the services you want to combine, e.g. "
            "[ServiceMount('/orders', orders_app), ServiceMount('/billing', billing_app)]."
        )

    seen: set[str] = set()
    for service in services:
        prefix = service.prefix
        if not prefix or not prefix.startswith("/"):
            raise ValueError(
                f"Invalid mount prefix {prefix!r} for service "
                f"{service.resolved_name()!r}: prefix must be non-empty and start "
                f"with '/', e.g. '/orders'."
            )
        if prefix == "/":
            raise ValueError(
                f"Mount prefix '/' is not allowed (service "
                f"{service.resolved_name()!r}): a service mounted at root would "
                f"shadow the composite's own health and landing routes. Use a "
                f"named prefix like '/orders'."
            )
        if prefix == health_path:
            raise ValueError(
                f"Mount prefix {prefix!r} collides with the composite health path "
                f"{health_path!r}. Mount the service under a different prefix or "
                f"pass a different health_path= to create_composite_app."
            )
        if prefix in seen:
            raise ValueError(
                f"Duplicate mount prefix {prefix!r}: two services cannot share the "
                f"same prefix. Give each service a unique prefix."
            )
        seen.add(prefix)


# ── Aggregate health ──────────────────────────────────────────────────────────


def _install_aggregate_health(
    app: FastAPI, services: list[ServiceMount], *, health_path: str
) -> None:
    """
    Register a root health endpoint that aggregates every sub-app's own health.

    The endpoint probes each service's ``health_path`` in-process via an ASGI
    transport (no network hop), reusing each service's real component checks
    rather than re-implementing them.

    Args:
        app:         The composite ``FastAPI`` app to register the route on.
        services:    The mounted services to probe.
        health_path: Root path for the aggregate endpoint (e.g. ``"/health"``).

    Edge cases:
        - A service's probe raises or times out → that service is reported
          ``unhealthy`` with the error as detail; other services still report.
        - Any service ``unhealthy`` → overall status is ``unhealthy`` and the
          endpoint returns HTTP 503 (Kubernetes-friendly readiness signal).

    Async safety: ✅ All sub-app probes run concurrently via ``asyncio.gather``.
    """
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    @app.get(
        health_path,
        summary="Aggregate health of all mounted services",
        description=(
            "Probes every mounted service's own health endpoint in-process and "
            "returns a per-service breakdown. Returns HTTP 503 if any service "
            "reports UNHEALTHY — suitable for a single Kubernetes readiness probe "
            "covering the whole deployment."
        ),
        include_in_schema=True,
    )
    async def composite_health() -> JSONResponse:
        """Return the aggregated health of all mounted services."""
        per_service = await _probe_all_services(services)
        overall = _worst_status(per_service.values())
        status_code = 503 if overall == "unhealthy" else 200
        return JSONResponse(
            content={"status": overall, "services": per_service},
            status_code=status_code,
        )


async def _probe_all_services(
    services: list[ServiceMount],
) -> dict[str, dict[str, Any]]:
    """
    Probe every service's health endpoint concurrently, in-process.

    Args:
        services: The mounted services to probe.

    Returns:
        Mapping of ``service.resolved_name()`` → a JSON-safe dict with at least a
        ``status`` key (``"healthy"`` / ``"degraded"`` / ``"unhealthy"``) plus the
        sub-app's own health body under ``detail`` when available.

    Edge cases:
        - A probe raising any exception → ``{"status": "unhealthy", "detail": ...}``
          for that service; the aggregate never fails because one service is down.

    Async safety: ✅ Uses ``asyncio.gather`` — one concurrent probe per service.
    """
    import asyncio  # noqa: PLC0415

    async def probe(service: ServiceMount) -> tuple[str, dict[str, Any]]:
        return service.resolved_name(), await _probe_one_service(service)

    results = await asyncio.gather(*(probe(s) for s in services))
    return dict(results)


async def _probe_one_service(service: ServiceMount) -> dict[str, Any]:
    """
    Probe a single service's health endpoint via an in-process ASGI call.

    Args:
        service: The mounted service to probe.

    Returns:
        A JSON-safe dict with a ``status`` key and, when the sub-app returns JSON,
        its body under ``detail``.

    Edge cases:
        - Non-2xx/503 status or non-JSON body → best-effort ``status`` derived from
          the HTTP code (``"healthy"`` for 2xx, else ``"unhealthy"``).
        - Transport/probe error → ``{"status": "unhealthy", "detail": <error>}``.
    """
    import httpx  # noqa: PLC0415

    try:
        # ASGITransport calls the sub-app directly — no sockets, no network. The
        # base_url is arbitrary; the sub-app only sees its own health_path.
        transport = httpx.ASGITransport(app=service.app)
        # follow_redirects: create_varco_app mounts HealthRouter's aggregate at
        # "/health/" (trailing slash), so a probe to "/health" gets a 307. Follow
        # it so the default health_path works without the caller knowing the quirk.
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://composite.local",
            follow_redirects=True,
        ) as client:
            resp = await client.get(service.health_path)
    except Exception as exc:  # noqa: BLE001 — a bad probe must not sink the aggregate
        return {"status": "unhealthy", "detail": f"health probe failed: {exc}"}

    # Prefer the sub-app's own reported status; fall back to the HTTP code.
    body: dict[str, Any] | None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON health body is tolerated
        body = None

    if isinstance(body, dict) and "status" in body:
        status = str(body["status"]).lower()
        return {"status": status, "detail": body}

    status = "healthy" if 200 <= resp.status_code < 300 else "unhealthy"
    return {"status": status, "detail": {"http_status": resp.status_code}}


def _worst_status(entries: Any) -> str:
    """
    Reduce per-service statuses to the worst-case overall status.

    Args:
        entries: Iterable of per-service dicts each carrying a ``status`` key.

    Returns:
        The worst status by severity (``unhealthy`` > ``degraded`` > ``healthy``).
        Returns ``"healthy"`` when there are no entries.

    Edge cases:
        - An unrecognised status string → treated as severity 0 (``healthy``) so a
          typo can never mask a genuine ``unhealthy`` elsewhere.
    """
    worst = "healthy"
    worst_sev = -1
    for entry in entries:
        status = str(entry.get("status", "healthy")).lower()
        sev = _STATUS_SEVERITY.get(status, 0)
        if sev > worst_sev:
            worst_sev = sev
            worst = status
    return worst


# ── Landing page ──────────────────────────────────────────────────────────────


def _install_landing_page(app: FastAPI, services: list[ServiceMount]) -> None:
    """
    Register ``GET /`` returning a JSON index of the mounted services.

    The composite root's own ``/docs`` only shows its health/landing routes; each
    service's real API docs live at ``{prefix}/docs``.  This index makes those
    discoverable from the root.

    Args:
        app:      The composite ``FastAPI`` app to register the route on.
        services: The mounted services to list.

    Edge cases:
        - No effect on any sub-app — this only adds a root-level informational route.
    """

    @app.get(
        "/",
        summary="Composite deployment index",
        description="Lists every mounted service and links its API docs.",
        include_in_schema=True,
    )
    async def index() -> dict[str, Any]:
        """Return a JSON index linking each mounted service's docs."""
        return {
            "deployment": app.title,
            "services": [
                {
                    "name": s.resolved_name(),
                    "prefix": s.prefix,
                    "docs": f"{s.prefix}/docs",
                    "openapi": f"{s.prefix}/openapi.json",
                }
                for s in services
            ],
        }


__all__ = [
    "ServiceMount",
    "CompositeLifespan",
    "create_composite_app",
    "build_service",
]
