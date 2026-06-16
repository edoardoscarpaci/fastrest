"""
assembler.py
============
DTO ↔ domain model translations for the ``Patient`` entity.

``PatientAssembler`` is a stateless singleton that maps between the HTTP-layer
DTOs and the domain ``Patient`` dataclass.  It is the single place where
field-to-field mapping logic lives — no service or repository touches DTO
fields directly.

DESIGN: ``apply_update`` uses ``domain_replace()`` not ``dataclasses.replace()``
    ``domain_replace()`` (from ``varco_core.model``) preserves all ``init=False``
    fields (``pk``, ``_raw_orm``, ``created_at``) from the original entity.
    On Python ≤ 3.12, plain ``dataclasses.replace()`` resets ``init=False``
    fields to their defaults, causing the repository to perform an INSERT instead
    of an UPDATE (because ``_raw_orm`` would become ``None``).

    ✅ Correct INSERT vs UPDATE detection in the repository.
    ✅ No manual ``object.__setattr__`` in the assembler.
    ❌ Requires ``varco_core.model.domain_replace`` instead of stdlib.

Note on encryption transparency
    The assembler never sees ciphertext — it always receives and returns
    plaintext strings.  Encryption/decryption is handled transparently by the
    ORM mapper (``_SAAutoMapper``) when reading from and writing to the DB.

Thread safety:  ✅ Stateless — safe to share across concurrent requests.
Async safety:   ✅ All methods are synchronous — no I/O.
"""

from __future__ import annotations

from providify import Singleton

from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace

from dtos import PatientCreate, PatientRead, PatientUpdate
from models import Patient


@Singleton
class PatientAssembler(
    AbstractDTOAssembler[Patient, PatientCreate, PatientRead, PatientUpdate]
):
    """
    Assembler for the ``Patient`` entity.

    Registered as a ``@Singleton`` so the DI container injects one shared
    instance into ``PatientService``.

    Thread safety:  ✅ Stateless — safe to share across concurrent requests.
    Async safety:   ✅ All methods are synchronous — no I/O.
    """

    def to_domain(self, dto: PatientCreate) -> Patient:
        """
        Map ``PatientCreate`` → fresh, unpersisted ``Patient``.

        ``pk`` is left unset (``pk_field(init=False)``) — the repository
        generates the UUID on INSERT via ``PKStrategy.UUID_AUTO``.

        ``created_at`` and ``updated_at`` are left as ``None`` — they are
        stamped by ``PatientService._prepare_for_create()`` before ``save()``.

        Args:
            dto: Validated ``PatientCreate`` payload from the HTTP layer.
                 Fields ``ssn`` and ``notes`` are plaintext strings — the ORM
                 mapper will encrypt them transparently on INSERT.

        Returns:
            An unpersisted ``Patient`` with ``name``, ``ssn``, and ``notes`` set.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` → repository does INSERT.
            - ``created_at``/``updated_at`` MUST be overwritten by
              ``PatientService._prepare_for_create()`` before ``save()``.
        """
        # Plaintext fields from the DTO — the mapper encrypts ssn and notes
        # transparently when persisting to the DB.
        return Patient(name=dto.name, ssn=dto.ssn, notes=dto.notes)

    def to_read_dto(self, entity: Patient) -> PatientRead:
        """
        Map a persisted ``Patient`` → ``PatientRead`` response.

        Called after every repository operation that returns a domain entity.
        The ORM mapper has already decrypted ``ssn`` and ``notes`` from the DB —
        this method always receives plaintext strings.

        Args:
            entity: A persisted ``Patient`` with all fields decrypted and populated.

        Returns:
            A ``PatientRead`` DTO with all fields in plaintext.

        Edge cases:
            - ``entity.ssn`` / ``entity.notes`` are ``None`` when not provided on
              creation — ``PatientRead`` accepts ``None`` for those fields.
        """
        return PatientRead(
            pk=entity.pk,
            name=entity.name,
            ssn=entity.ssn,
            notes=entity.notes,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    def apply_update(self, entity: Patient, dto: PatientUpdate) -> Patient:
        """
        Apply ``PatientUpdate`` fields onto ``entity`` and return a new ``Patient``.

        Uses ``domain_replace()`` (not plain ``dataclasses.replace()``) to
        preserve all ``init=False`` fields (``pk``, ``_raw_orm``) from the
        original entity.  Preserving ``_raw_orm`` is critical — it tells the
        repository to perform an UPDATE, not an INSERT.

        Only non-``None`` fields in ``dto`` overwrite the current value — all
        fields are optional in ``PatientUpdate`` (partial update semantics).

        Args:
            entity: Current persisted state of the patient.
            dto:    ``PatientUpdate`` payload with the new field values.
                    Any field left as ``None`` retains the current entity value.

        Returns:
            A new ``Patient`` instance with updated fields and ``init=False``
            fields copied from the original.

        Edge cases:
            - ``dto.ssn = None`` is ambiguous: does the caller want to clear SSN
              or leave it unchanged?  This example treats ``None`` as "no change"
              (partial update).  To explicitly clear SSN, clients must send ``""``
              or a dedicated ``PATCH`` endpoint should be added.
        """
        # DESIGN: None = "no change" for partial update semantics.
        # Each field is only replaced when the DTO provides a non-None value.
        return domain_replace(
            entity,
            # Provide a value only when the DTO supplies one; fall back to current
            **{
                field: getattr(dto, field)
                for field in ("name", "ssn", "notes")
                if getattr(dto, field) is not None
            },
        )


__all__ = ["PatientAssembler"]
