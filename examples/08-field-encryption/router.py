"""
router.py
=========
HTTP router factory for the patient records API.

Returns a ``CRUDRouter`` subclass that provides all standard CRUD endpoints
plus a health check.  The router is intentionally public (no ``_auth``) to
keep this example focused on field-level encryption, not authentication.

Available endpoints
-------------------
``POST   /v1/patients``           — Create a patient (ssn + notes encrypted at rest)
``GET    /v1/patients``           — List all patients (ssn + notes decrypted)
``GET    /v1/patients/{id}``      — Fetch a single patient by UUID
``PUT    /v1/patients/{id}``      — Full replace (new plaintext values re-encrypted)
``PATCH  /v1/patients/{id}``      — Partial update
``DELETE /v1/patients/{id}``      — Remove
``GET    /health``                — Liveness probe

DESIGN: ``CRUDRouter`` over ``GenericRouter``
    The ``Patient`` entity needs standard CRUD — there is no custom domain logic
    for HTTP handling.  ``CRUDRouter`` provides all six endpoints automatically.
    A ``GenericRouter`` would be appropriate only if the endpoint shape were
    custom (e.g. custom filters, streaming, non-standard response shapes).

    ✅ Zero hand-written handler code — less surface area for bugs.
    ✅ Consistent URL + status-code conventions from the framework.
    ❌ Harder to add non-CRUD endpoints inline (use a separate ``GenericRouter``
       for the health check, mounted alongside the patient router).

Thread safety:  ✅ Router class is a ClassVar-only definition — no instance state.
Async safety:   ✅ All request handlers are ``async def`` (provided by the mixin).
"""

from __future__ import annotations

from uuid import UUID

from dtos import PatientCreate, PatientRead, PatientUpdate
from fastapi import FastAPI
from models import Patient
from varco_fastapi.router.presets import CRUDRouter


def make_patient_router(container) -> type[CRUDRouter]:
    """
    Build and return the ``PatientRouter`` class with ``_service`` wired from the container.

    Resolves ``PatientService`` by its concrete class (not via the generic alias)
    because generic-alias resolution depends on providify's type-matching internals,
    which vary across versions.  The concrete lookup is simpler and always unambiguous.

    Args:
        container: Fully configured ``DIContainer`` with ``PatientService`` bound.

    Returns:
        A ``CRUDRouter`` subclass for the Patient entity, with ``_service`` set.
    """
    from service import PatientService  # noqa: PLC0415

    svc = container.get(PatientService)

    class PatientRouter(CRUDRouter[Patient, UUID, PatientCreate, PatientRead, PatientUpdate]):
        """
        REST router for patient records.

        Provides standard CRUD endpoints:
            POST   /v1/patients/           → 201 Created + PatientRead
            GET    /v1/patients/           → 200 + list of PatientRead
            GET    /v1/patients/{id}       → 200 + PatientRead
            PUT    /v1/patients/{id}       → 200 + PatientRead
            PATCH  /v1/patients/{id}       → 200 + PatientRead
            DELETE /v1/patients/{id}       → 204 No Content

        No ``_auth`` is set — all endpoints are public for this demo.
        In production, add ``_auth = JwtBearerAuth(...)`` and a proper
        ``AbstractAuthorizer`` implementation.

        Thread safety:  ✅ ClassVars are read-only after class definition.
        Async safety:   ✅ All request handlers are ``async def``.
        """

        _prefix = "/v1/patients"
        _tags = ["patients"]
        _service = svc

    return PatientRouter


def add_health_route(app: FastAPI) -> None:
    """
    Register a ``GET /health`` liveness probe on the FastAPI application.

    The health endpoint returns immediately with ``{"status": "ok"}`` — it does
    not probe the database or any other backend.  A deeper readiness check would
    query the DB but is outside the scope of this example.

    Args:
        app: The ``FastAPI`` instance to attach the route to.

    Edge cases:
        - This route is registered AFTER ``create_varco_app()`` builds the app —
          it bypasses the varco router machinery and registers directly on FastAPI.
        - The route is not authenticated (no ``@auth`` decorator) — appropriate
          for infra-level health probes.
    """

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """
        Liveness probe.

        Returns:
            ``{"status": "ok"}`` when the process is running.
        """
        return {"status": "ok"}


__all__ = ["make_patient_router", "add_health_route"]
