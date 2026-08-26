"""
assembler
=========
DTO ↔ domain model translations for the ``Note`` entity.

``NoteAssembler`` is a stateless singleton responsible for all DTO ↔ domain
mapping.  It never sets ``tenant_id`` — that is stamped by
``TenantAwareService._prepare_for_create`` from the authenticated JWT, making
it impossible for the HTTP layer to inject a foreign tenant ID.

DESIGN: ``apply_update`` uses ``domain_replace()`` not ``dataclasses.replace()``
    ``domain_replace()`` copies ``init=False`` fields (``pk``, ``_raw_orm``,
    ``created_at``, ``updated_at``) from the original entity.  On Python ≤ 3.12,
    plain ``dataclasses.replace()`` resets those fields to defaults — ``pk``
    becomes ``None`` and the repository would silently INSERT a duplicate row.

    ✅ No manual ``object.__setattr__`` in assembler implementations.
    ✅ Future ``init=False`` fields on the domain model are handled automatically.

DESIGN: ``deleted_at`` not in ``NoteRead`` mapper on the write path
    The assembler never sets ``deleted_at`` — that is the exclusive domain of
    ``SoftDeleteService`` which stamps it via ``dataclasses.replace``.

Thread safety:  ✅ Stateless — all methods are pure transformations.
Async safety:   ✅ No I/O — all methods are synchronous.
"""

from __future__ import annotations

from dtos import NoteCreate, NoteRead, NoteUpdate
from models import Note
from providify import Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace


@Singleton
class NoteAssembler(AbstractDTOAssembler[Note, NoteCreate, NoteRead, NoteUpdate]):
    """
    Assembler for the ``Note`` entity.

    Registered as a ``@Singleton`` — the DI container injects a single
    shared instance into every ``NoteService``.

    Thread safety:  ✅ Stateless — safe to share across all concurrent requests.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: NoteCreate) -> Note:
        """
        Map ``NoteCreate`` → fresh, unpersisted ``Note``.

        ``pk`` and ``tenant_id`` are left as empty-string defaults;
        the repository will assign ``pk`` on INSERT and
        ``TenantAwareService._prepare_for_create`` will stamp ``tenant_id``.
        ``deleted_at``, ``created_at``, ``updated_at`` are left as ``None``
        — the repository and service hooks manage them.

        Args:
            dto: Validated ``NoteCreate`` payload from the HTTP layer.

        Returns:
            An unpersisted ``Note`` with title and content set.
            ``pk``, ``tenant_id``, and timestamp fields are not set here.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` — the repository
              treats this as an INSERT.
            - ``tenant_id`` defaults to ``""`` — the service hook overwrites it;
              do not rely on its value from this method.
        """
        # Only business fields — cross-cutting fields (tenant_id, deleted_at)
        # are handled by service hooks, not the assembler.
        return Note(title=dto.title, content=dto.content)

    def to_read_dto(self, entity: Note) -> NoteRead:
        """
        Map a persisted (or soft-deleted) ``Note`` → ``NoteRead``.

        Includes ``deleted_at`` so the ``/deleted`` list endpoint can expose
        soft-deleted notes without a separate assembler.

        Args:
            entity: A ``Note`` with all fields populated (persisted).

        Returns:
            A ``NoteRead`` DTO with all entity fields.

        Edge cases:
            - ``deleted_at`` may be ``None`` (active note) or a UTC datetime
              (soft-deleted note) — included as-is in the response.
            - ``updated_at`` falls back to ``created_at`` as a safety guard
              for brand-new entities; the repository always sets it in practice.
        """
        return NoteRead(
            pk=entity.pk,
            tenant_id=entity.tenant_id,
            title=entity.title,
            content=entity.content,
            created_at=entity.created_at,
            # Fall back so the field is never None in the DTO even on brand-new
            # entities where the repository hasn't refreshed updated_at yet.
            updated_at=(entity.updated_at if entity.updated_at is not None else entity.created_at),
            deleted_at=entity.deleted_at,
        )

    def apply_update(self, entity: Note, dto: NoteUpdate) -> Note:
        """
        Apply ``NoteUpdate`` fields onto ``entity`` and return a new ``Note``.

        Only non-``None`` DTO fields replace entity fields; ``None`` means
        "no change" per the ``UpdateDTO`` convention.

        Uses ``domain_replace()`` not plain ``dataclasses.replace()`` — see
        module-level DESIGN note for why this matters on Python ≤ 3.12.

        Args:
            entity: Current persisted state of the note.
            dto:    ``NoteUpdate`` payload — ``None`` fields mean "no change".

        Returns:
            A new ``Note`` with the update applied.  All ``init=False`` fields
            (``pk``, ``_raw_orm``, timestamps) are copied from ``entity`` so
            the repository performs an UPDATE, not an INSERT.

        Edge cases:
            - Sending all-``None`` ``NoteUpdate`` produces an identical copy —
              a no-op UPDATE query.
            - ``tenant_id`` and ``deleted_at`` are NOT exposed in ``NoteUpdate``
              so they cannot be changed via the update endpoint.
        """
        return domain_replace(
            entity,
            title=dto.title if dto.title is not None else entity.title,
            content=dto.content if dto.content is not None else entity.content,
        )


__all__ = ["NoteAssembler"]
