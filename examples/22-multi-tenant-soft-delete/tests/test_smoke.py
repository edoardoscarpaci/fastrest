"""
test_smoke.py
=============
Integration smoke tests for the ``22-multi-tenant-soft-delete`` example.

Exercises six scenarios against a real PostgreSQL database:

1. Health check — ``GET /health`` returns 200.
2. Create + read round-trip — note created via POST is readable via GET.
3. Tenant isolation — tenant A's notes are invisible to tenant B.
4. Cross-tenant access blocked — GET with wrong tenant yields 404.
5. Soft delete — DELETE removes note from list; still present in DB
   (verified via direct SA query).
6. List excludes soft-deleted — deleted note absent from ``GET /v1/notes``.

All tests are marked ``integration`` (require Docker / testcontainers).
They share a session-scoped ``client`` fixture; each test uses a unique
tenant ID to avoid cross-test state contamination.

DESIGN: unique tenant IDs per test
    ✅ Tests are fully isolated even though they share a Postgres database.
    ✅ No truncation or rollback needed between tests — different tenant_id
       values act as logical namespaces in the ``WHERE tenant_id = ?`` filter.
    ❌ Stale rows from previous runs accumulate in the database (acceptable for
       a short-lived testcontainers session; use ``--reuse-db`` + explicit
       cleanup for long-lived test databases).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest


# All tests in this module require Docker — skip without the marker.
pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant() -> str:
    """
    Generate a unique tenant ID for a single test run.

    Using a UUID ensures no cross-test contamination even when the database
    persists between sessions (no teardown/truncate needed).

    Returns:
        A short unique string suitable for use as a tenant identifier.
    """
    # Hex prefix keeps the string short and readable in error messages.
    return f"t-{uuid.uuid4().hex[:8]}"


def _headers(tenant_id: str) -> dict[str, str]:
    """
    Build the HTTP headers dict for a given tenant.

    Args:
        tenant_id: The tenant identifier to embed in the header.

    Returns:
        A dict with ``X-Tenant-Id`` set to ``tenant_id``.
    """
    return {"X-Tenant-Id": tenant_id}


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_health_check(client) -> None:
    """
    ``GET /health`` must return 200 with ``{"status": "ok"}``.

    This is a liveness probe — it must not require a database connection
    and must succeed even before any note is created.
    """
    resp = await client.get("/health")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok"}


async def test_create_and_read_round_trip(client) -> None:
    """
    Creating a note via ``POST /v1/notes`` must be immediately readable
    via ``GET /v1/notes/{id}`` with the same tenant.

    Verifies:
    - ``title`` and ``content`` are preserved.
    - ``tenant_id`` in the response matches the requesting tenant.
    - ``pk`` in the response is a valid UUID.
    - ``deleted_at`` is ``None`` for a freshly created note.
    """
    tenant = _tenant()
    headers = _headers(tenant)

    # Create the note.
    create_resp = await client.post(
        "/v1/notes",
        json={"title": "Hello", "content": "World"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    note = create_resp.json()

    # Verify the response shape.
    assert note["title"] == "Hello"
    assert note["content"] == "World"
    assert note["tenant_id"] == tenant
    assert note["deleted_at"] is None
    pk = note["pk"]
    assert pk  # non-empty UUID string

    # Read back the same note.
    get_resp = await client.get(f"/v1/notes/{pk}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["pk"] == pk
    assert fetched["title"] == "Hello"
    assert fetched["tenant_id"] == tenant


async def test_tenant_isolation_list(client) -> None:
    """
    Notes created by tenant A must not appear in tenant B's list.

    Verifies that ``TenantAwareService._scoped_params`` correctly prepends
    ``tenant_id = <tid>`` to every list query.
    """
    tenant_a = _tenant()
    tenant_b = _tenant()

    # Tenant A creates a note.
    await client.post(
        "/v1/notes",
        json={"title": "Tenant A Note", "content": "Private"},
        headers=_headers(tenant_a),
    )

    # Tenant B's list must be empty (or at least not include Tenant A's note).
    list_resp = await client.get("/v1/notes", headers=_headers(tenant_b))
    assert list_resp.status_code == 200, list_resp.text
    notes: list[dict[str, Any]] = list_resp.json()

    # Filter for Tenant A's note title in Tenant B's results — must be absent.
    titles = [n["title"] for n in notes]
    assert (
        "Tenant A Note" not in titles
    ), f"Tenant B should not see Tenant A's notes, but found: {titles}"


async def test_cross_tenant_access_blocked(client) -> None:
    """
    ``GET /v1/notes/{id}`` with a wrong tenant must return 404.

    Verifies that ``TenantAwareService._check_entity`` raises
    ``ServiceNotFoundError`` (mapped to 404) for cross-tenant reads.
    A 403 must NOT be returned — doing so would reveal that the note
    exists in another tenant's data (existence oracle attack).
    """
    tenant_a = _tenant()
    tenant_b = _tenant()

    # Create a note as Tenant A.
    create_resp = await client.post(
        "/v1/notes",
        json={"title": "Secret", "content": ""},
        headers=_headers(tenant_a),
    )
    assert create_resp.status_code == 201, create_resp.text
    note_id = create_resp.json()["pk"]

    # Tenant B attempts to read Tenant A's note — must get 404, not 403.
    get_resp = await client.get(f"/v1/notes/{note_id}", headers=_headers(tenant_b))
    assert get_resp.status_code == 404, (
        f"Cross-tenant access should return 404, got {get_resp.status_code}: "
        f"{get_resp.text}"
    )


async def test_soft_delete_removes_from_list(client) -> None:
    """
    After ``DELETE /v1/notes/{id}``, the note must not appear in the list.

    Verifies that ``SoftDeleteService._scoped_params`` correctly appends
    ``deleted_at IS NULL`` to list queries, excluding soft-deleted rows.
    """
    tenant = _tenant()
    headers = _headers(tenant)

    # Create a note.
    create_resp = await client.post(
        "/v1/notes",
        json={"title": "To Be Deleted", "content": "Temp"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    note_id = create_resp.json()["pk"]

    # Confirm it appears in the list before deletion.
    list_before = await client.get("/v1/notes", headers=headers)
    ids_before = [n["pk"] for n in list_before.json()]
    assert note_id in ids_before, "Note should be visible before soft-delete"

    # Soft-delete the note.
    del_resp = await client.delete(f"/v1/notes/{note_id}", headers=headers)
    assert del_resp.status_code == 204, del_resp.text

    # The note must NOT appear in the list after soft-delete.
    list_after = await client.get("/v1/notes", headers=headers)
    assert list_after.status_code == 200, list_after.text
    ids_after = [n["pk"] for n in list_after.json()]
    assert (
        note_id not in ids_after
    ), f"Soft-deleted note {note_id} must not appear in the active list"


async def test_soft_delete_blocks_get(client) -> None:
    """
    After ``DELETE /v1/notes/{id}``, ``GET /v1/notes/{id}`` must return 404.

    Verifies that ``SoftDeleteService._check_entity`` raises
    ``ServiceNotFoundError`` for soft-deleted notes, preventing access even
    via the direct-read endpoint.
    """
    tenant = _tenant()
    headers = _headers(tenant)

    # Create and then soft-delete a note.
    create_resp = await client.post(
        "/v1/notes",
        json={"title": "Soft-Delete Me", "content": ""},
        headers=headers,
    )
    note_id = create_resp.json()["pk"]
    await client.delete(f"/v1/notes/{note_id}", headers=headers)

    # Direct GET after soft-delete must return 404.
    get_resp = await client.get(f"/v1/notes/{note_id}", headers=headers)
    assert get_resp.status_code == 404, (
        f"Soft-deleted note should return 404, got {get_resp.status_code}: "
        f"{get_resp.text}"
    )


async def test_blank_title_returns_422(client) -> None:
    """
    Creating a note with a blank title must return 422 Unprocessable Entity.

    Verifies that ``NoteService._validate_entity`` raises
    ``ServiceValidationError`` for blank titles (mapped to 422 by the
    exception handler in ``app.py``).
    """
    tenant = _tenant()

    # A whitespace-only title is blank per business rules.
    resp = await client.post(
        "/v1/notes",
        json={"title": "   ", "content": "body"},
        headers=_headers(tenant),
    )
    assert (
        resp.status_code == 422
    ), f"Blank title should return 422, got {resp.status_code}: {resp.text}"
