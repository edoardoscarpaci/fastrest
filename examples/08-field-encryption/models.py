"""
models.py
=========
Domain model for the ``08-field-encryption`` example.

``Patient`` holds sensitive PII — ``ssn`` and ``notes`` are marked with
``EncryptedHint`` so ``SAModelFactory`` stores them as ciphertext in the DB
while the service layer always sees plaintext.

DESIGN: EncryptedHint on ssn and notes, name in plaintext
    ✅ Demonstrates the common pattern: some PII fields encrypted, some not.
    ✅ ``EncryptedHint`` is a presence marker — its position in ``Annotated``
       is all that matters; no configuration is needed on the marker itself.
    ✅ The service layer and HTTP layer never deal with bytes/ciphertext —
       they always receive and return plaintext strings.
    ❌ Encrypted fields are stored as ``LargeBinary`` (bytes) in the DB —
       they cannot be used in WHERE clauses without decrypting first.

Thread safety:  ✅ ``@dataclass`` is immutable after construction.
Async safety:   ✅ Pure value object — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from varco_core.meta import EncryptedHint, FieldHint, PKStrategy, PrimaryKey, pk_field
from varco_core.model import AuditedDomainModel


@dataclass(kw_only=True)
class Patient(AuditedDomainModel):
    """
    Domain model representing a patient record.

    ``name`` is stored in plaintext — it may appear in logs and audit trails.
    ``ssn`` and ``notes`` carry ``EncryptedHint`` — they are opaque bytes in
    the database; the ORM mapper transparently encrypts on write and decrypts
    on read.

    Attributes:
        pk:    UUID primary key, auto-assigned on INSERT.
        name:  Full name — stored plaintext; used in listings and searches.
        ssn:   Social Security Number — encrypted at rest; never stored in
               clear text.  ``None`` if not provided.
        notes: Free-form clinical notes — encrypted at rest.  ``None`` if not
               provided.

    Edge cases:
        - ``None`` values on encrypted fields are stored as NULL in the DB —
          they are never encrypted (you cannot encrypt ``None``).
        - The SA table name is fixed as ``patients`` via ``Meta.table``.

    Thread safety:  ✅ Frozen ``@dataclass``-like semantics (``AuditedDomainModel``).
    Async safety:   ✅ Pure value object.
    """

    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()

    # Plaintext — safe to index, search, log
    name: Annotated[str, FieldHint(max_length=255)] = ""

    # EncryptedHint → SAModelFactory stores as LargeBinary (ciphertext)
    # Nullable: patient may not have provided SSN on admission
    ssn: Annotated[str | None, EncryptedHint()] = None

    # Encrypted clinical notes — can be lengthy; nullable
    notes: Annotated[str | None, EncryptedHint()] = None

    class Meta:
        # Explicit table name — avoids SA class-name → snake_case heuristic
        table = "patients"


__all__ = ["Patient"]
