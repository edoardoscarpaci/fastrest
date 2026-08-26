"""
tests/test_smoke.py
===================
Integration smoke tests for the ``08-field-encryption`` example.

All tests require a running PostgreSQL instance — they are tagged
``pytest.mark.integration`` and skipped by default.  Run with::

    uv run pytest examples/08-field-encryption/tests/ -v -m integration

What these tests verify
-----------------------
1. **Create + read round-trip** — POST a patient with a plaintext SSN, GET it
   back, verify the returned SSN matches the original plaintext.

2. **DB stores ciphertext** — after creating a patient, query the ``patients``
   table directly via a raw SA query and verify that the ``ssn`` column value
   is NOT the plaintext "123-45-6789" (it should be opaque ciphertext bytes).

3. **Update encrypted field** — PUT a patient with a new SSN, GET it back,
   verify the returned SSN is the updated plaintext.

4. **List with decrypted fields** — create two patients, GET /v1/patients,
   verify both appear with their plaintext SSNs.

5. **Health check** — GET /health returns 200 ``{"status": "ok"}``.

DESIGN: session-scoped fixtures, integration marker
    ✅ All tests share one Docker container and one app instance — fast.
    ✅ ``pytestmark`` ensures every test is tagged without per-test decoration.
    ✅ Tests use explicit SSN values unique enough to avoid cross-test collisions.
    ❌ DB state accumulates across tests — tests must not depend on an empty DB.
       Each test creates its own records and asserts only on those records.

Thread safety:  N/A — pytest runs one test at a time per worker.
Async safety:   ✅ All tests are ``async def`` — no ``@pytest.mark.asyncio``
                   needed (``asyncio_mode = "auto"`` in pyproject.toml).
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import pytest

# ── sys.path guard (belt-and-suspenders) ──────────────────────────────────────
# conftest.py already inserts the example root, but repeat here to be safe
# in case this file is run directly or collected before conftest.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

# All tests in this module require Docker — skip by default.
pytestmark = pytest.mark.integration


# ════════════════════════════════════════════════════════════════════════════════
# Helper
# ════════════════════════════════════════════════════════════════════════════════


async def _create_patient(
    client,
    *,
    name: str,
    ssn: str,
    notes: str | None = None,
) -> dict:
    """
    POST a new patient and return the parsed JSON response body.

    Args:
        client: Session-scoped ``httpx.AsyncClient``.
        name:   Patient full name.
        ssn:    SSN in plaintext — will be encrypted at rest.
        notes:  Optional clinical notes.

    Returns:
        Parsed JSON body of the 201 Created response.

    Raises:
        AssertionError: Non-201 status code.
    """
    payload: dict = {"name": name, "ssn": ssn}
    if notes is not None:
        payload["notes"] = notes
    resp = await client.post("/v1/patients", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ════════════════════════════════════════════════════════════════════════════════
# 1. Create + read round-trip
# ════════════════════════════════════════════════════════════════════════════════


class TestCreateAndRead:
    """POST a patient, GET it back, verify plaintext SSN is returned."""

    async def test_create_patient_returns_201(self, app_client) -> None:
        """POST /v1/patients returns 201 with the patient data."""
        data = await _create_patient(
            app_client,
            name="Alice Smith",
            ssn="111-22-3333",
        )
        assert data["name"] == "Alice Smith"
        # ssn is returned as plaintext — encryption is transparent to the caller
        assert data["ssn"] == "111-22-3333"
        # pk must be a valid UUID string
        UUID(data["pk"])

    async def test_get_returns_plaintext_ssn(self, app_client) -> None:
        """GET /v1/patients/{id} returns the plaintext SSN after decryption."""
        # Create the record
        created = await _create_patient(
            app_client,
            name="Bob Jones",
            ssn="123-45-6789",
            notes="Allergic to penicillin",
        )
        pk = created["pk"]

        # Fetch by ID
        resp = await app_client.get(f"/v1/patients/{pk}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # The ORM mapper decrypts ssn and notes transparently on SELECT —
        # the caller always receives and works with plaintext.
        assert data["ssn"] == "123-45-6789"
        assert data["notes"] == "Allergic to penicillin"
        assert data["name"] == "Bob Jones"


# ════════════════════════════════════════════════════════════════════════════════
# 2. DB stores ciphertext
# ════════════════════════════════════════════════════════════════════════════════


class TestDbStoresCiphertext:
    """Verify the DB column contains ciphertext, not the plaintext SSN."""

    async def test_ssn_column_is_not_plaintext(self, app_client, db_url) -> None:
        """
        After creating a patient, query the DB directly and verify the ssn
        column does NOT contain the plaintext value.

        This test directly verifies the "at-rest encryption" property:
        even if someone gains read access to the ``patients`` table,
        they see opaque ciphertext bytes, not the real SSN.
        """
        # Create the record via the API (plaintext ssn is encrypted on INSERT)
        created = await _create_patient(
            app_client,
            name="Carol Davis",
            ssn="999-88-7777",
        )
        pk = created["pk"]

        # Open a raw SA connection bypassing the mapper — reads raw bytes from DB
        import sqlalchemy as sa
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    sa.text("SELECT ssn FROM patients WHERE id = :pk"),
                    {"pk": pk},
                )
                row = result.fetchone()
        finally:
            await engine.dispose()

        assert row is not None, "Patient not found in DB after creation"

        # ssn column is LargeBinary — the DB returns bytes.
        # The plaintext string "999-88-7777" must NOT appear as the raw value.
        raw_ssn = row[0]
        # raw_ssn is bytes (Fernet token) — it will never equal the plaintext string
        plaintext_as_bytes = b"999-88-7777"
        assert raw_ssn != plaintext_as_bytes, (
            f"SSN was stored in plaintext — encryption did NOT apply. Raw DB value: {raw_ssn!r}"
        )
        # Also verify that the raw value is non-trivially long (Fernet overhead)
        assert len(raw_ssn) > 20, f"Raw SSN is suspiciously short: {raw_ssn!r}"


# ════════════════════════════════════════════════════════════════════════════════
# 3. Update encrypted field
# ════════════════════════════════════════════════════════════════════════════════


class TestUpdateEncryptedField:
    """PUT a patient with a new SSN — verify the updated plaintext is returned."""

    async def test_put_updates_encrypted_ssn(self, app_client) -> None:
        """
        PUT /v1/patients/{id} re-encrypts the new SSN on write and returns
        the updated plaintext on the subsequent GET.
        """
        # Create initial record
        created = await _create_patient(
            app_client,
            name="Dave Wilson",
            ssn="444-55-6666",
            notes="Initial notes",
        )
        pk = created["pk"]

        # PUT with a new SSN
        resp = await app_client.put(
            f"/v1/patients/{pk}",
            json={
                "name": "Dave Wilson",
                "ssn": "777-88-9999",
                "notes": "Updated notes",
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # GET and verify the plaintext reflects the update
        resp = await app_client.get(f"/v1/patients/{pk}")
        assert resp.status_code == 200
        data = resp.json()

        # Both ssn and notes should reflect the PUT values — mapper decrypts correctly
        assert data["ssn"] == "777-88-9999", (
            f"Expected updated SSN '777-88-9999', got {data['ssn']!r}. "
            "The mapper may have failed to re-encrypt the new value."
        )
        assert data["notes"] == "Updated notes"


# ════════════════════════════════════════════════════════════════════════════════
# 4. List with decrypted fields
# ════════════════════════════════════════════════════════════════════════════════


class TestListWithDecryptedFields:
    """GET /v1/patients returns all patients with plaintext ssn values."""

    async def test_list_returns_decrypted_ssns(self, app_client) -> None:
        """
        Create two patients and verify GET /v1/patients returns both with
        their SSNs decrypted.

        DESIGN: filter by name prefix to isolate this test's records from
        patients created by other tests — avoids assumptions about empty DB.
        """
        # Create two patients with recognisable names for this test
        tag = "ListTest"
        await _create_patient(app_client, name=f"{tag} Patient A", ssn="001-00-0001")
        await _create_patient(app_client, name=f"{tag} Patient B", ssn="002-00-0002")

        # GET all patients — CRUDRouter may return a paged envelope or a plain list
        resp = await app_client.get("/v1/patients")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        all_patients = body["results"] if isinstance(body, dict) and "results" in body else body

        # Find this test's patients in the response list
        this_test_patients = [p for p in all_patients if p.get("name", "").startswith(tag)]
        assert len(this_test_patients) >= 2, (
            f"Expected at least 2 patients with name prefix {tag!r}, "
            f"found {len(this_test_patients)}: {this_test_patients}"
        )

        # Collect SSNs from this test's patients
        returned_ssns = {p["ssn"] for p in this_test_patients}

        # Both SSNs should be returned as plaintext — mapper decrypts on SELECT
        assert "001-00-0001" in returned_ssns, (
            f"SSN '001-00-0001' not found in list response. Returned: {returned_ssns}"
        )
        assert "002-00-0002" in returned_ssns, (
            f"SSN '002-00-0002' not found in list response. Returned: {returned_ssns}"
        )


# ════════════════════════════════════════════════════════════════════════════════
# 5. Health check
# ════════════════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """GET /health returns 200."""

    async def test_health_returns_ok(self, app_client) -> None:
        """Health probe must return 200 with ``{"status": "ok"}``."""
        resp = await app_client.get("/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body == {"status": "ok"}, f"Unexpected health body: {body}"
