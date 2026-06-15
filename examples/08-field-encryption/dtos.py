"""
dtos.py
=======
Data Transfer Objects for the patient records API.

Three DTOs follow the varco ``CreateDTO / ReadDTO / UpdateDTO`` convention:

- ``PatientCreate``  — inbound payload for ``POST /v1/patients``
- ``PatientRead``    — outbound representation from ``GET /v1/patients/{id}``
- ``PatientUpdate``  — partial patch payload for ``PUT /v1/patients/{id}``

DESIGN: plaintext DTOs — no EncryptedDTOField
    The service layer decrypts on read (via the ORM mapper), so DTOs always
    carry plaintext values.  DTO-level re-encryption (``varco_core.dto.encryption``)
    would be needed only if the HTTP response itself must be opaque to
    intermediaries — not the goal of this example.

    ✅ Simple — callers send and receive plain strings.
    ✅ Aligns with the "transparent at-rest encryption" use case.
    ❌ Plaintext travels over the wire — use TLS in production (not shown here).

Thread safety:  ✅ Pydantic models are effectively immutable after validation.
Async safety:   ✅ Pure value objects.
"""

from __future__ import annotations


from varco_core.dto.base import CreateDTO, ReadDTO, UpdateDTO


class PatientCreate(CreateDTO):
    """
    Payload for creating a new patient record.

    Attributes:
        name:  Required full name.
        ssn:   Optional Social Security Number in plaintext — stored encrypted.
        notes: Optional clinical notes in plaintext — stored encrypted.
    """

    name: str
    ssn: str | None = None
    notes: str | None = None


class PatientRead(ReadDTO):
    """
    Outbound representation of a patient record.

    ``pk`` is inherited from ``ReadDTO``.  All fields are plaintext —
    decryption happened transparently in the ORM mapper.

    Attributes:
        pk:        UUID primary key.
        name:      Full name (plaintext).
        ssn:       SSN decrypted from DB ciphertext; ``None`` if not stored.
        notes:     Clinical notes decrypted from DB ciphertext; ``None`` if absent.
        created_at: ISO-8601 creation timestamp (from ``AuditedDomainModel``).
        updated_at: ISO-8601 last-update timestamp.
    """

    name: str
    ssn: str | None = None
    notes: str | None = None


class PatientUpdate(UpdateDTO):
    """
    Partial patch payload for updating a patient record.

    All fields are optional — only provided fields are updated.

    Attributes:
        name:  New full name.
        ssn:   New SSN in plaintext (will be re-encrypted on write).
        notes: New clinical notes (will be re-encrypted on write).
    """

    name: str | None = None
    ssn: str | None = None
    notes: str | None = None


__all__ = ["PatientCreate", "PatientRead", "PatientUpdate"]
