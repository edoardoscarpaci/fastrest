"""
assembler
=========
DTO ↔ domain model translations for the ``Document`` entity.

``DocumentAssembler`` is a stateless singleton — all three mapping methods
are pure functions of their inputs.  It is the only place where
field-to-field mapping logic lives; services and repositories never touch
DTO fields directly.

DESIGN: ``owner_id`` handled in service, not assembler
    ``to_domain()`` leaves ``owner_id`` as ``None`` — the service stamps it
    in ``_prepare_for_create()`` from ``ctx.user_id``.  This keeps the
    assembler transport-agnostic: it never needs access to the JWT.

    ✅ Assembler stays pure (no auth context dependency).
    ✅ Separation of concerns: assembler maps fields, service applies policy.
    ❌ ``owner_id`` in the domain object is ``None`` until the service hook
       runs — never read it before the hook completes.

Thread safety:  ✅ Stateless — all methods are pure transformations.
Async safety:   ✅ No I/O — all methods are synchronous.
"""

from __future__ import annotations

from dtos import DocumentCreate, DocumentRead, DocumentUpdate
from models import Document
from providify import Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace


@Singleton
class DocumentAssembler(
    AbstractDTOAssembler[Document, DocumentCreate, DocumentRead, DocumentUpdate]
):
    """
    Assembler for the ``Document`` entity.

    Registered as a ``@Singleton`` — the DI container injects a single
    shared instance into every ``DocumentService`` instance.

    Thread safety:  ✅ Stateless — safe to share across all concurrent requests.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: DocumentCreate) -> Document:
        """
        Map ``DocumentCreate`` → fresh, unpersisted ``Document``.

        ``pk`` and timestamps are left unset (``init=False``) — the repository
        assigns them on first ``save()``.  ``owner_id`` is also left as ``None``
        here — ``DocumentService._prepare_for_create()`` stamps it from the JWT.

        Args:
            dto: Validated ``DocumentCreate`` payload from the HTTP layer.

        Returns:
            An unpersisted ``Document`` with title and content set.
            ``pk``, ``owner_id``, and timestamps are ``None`` — set later.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` — the repository
              treats this as an INSERT, not an UPDATE.
            - Do NOT read ``owner_id`` from the returned entity — it is ``None``
              until the service stamps it in ``_prepare_for_create``.
        """
        # owner_id is intentionally omitted — the service will stamp it
        # from ctx.user_id in _prepare_for_create(), not from the DTO.
        return Document(
            title=dto.title,
            content=dto.content,
        )

    def to_read_dto(self, entity: Document) -> DocumentRead:
        """
        Map a persisted ``Document`` → ``DocumentRead`` response.

        Called after every ``save()``, ``find_by_id()``, and
        ``find_by_query()`` to produce the value returned to the caller.

        Args:
            entity: A persisted ``Document`` with all fields populated.

        Returns:
            A ``DocumentRead`` with all fields from the entity.

        Edge cases:
            - ``updated_at`` falls back to ``created_at`` when the field is
              ``None`` (e.g. a just-inserted entity where the repo hasn't set
              it yet).  The in-memory repo always sets it — this guard
              is defensive only.
        """
        return DocumentRead(
            pk=entity.pk,
            title=entity.title,
            content=entity.content,
            owner_id=entity.owner_id,
            created_at=entity.created_at,
            # Defensive fallback: updated_at should never be None after save(),
            # but guard against it in case the entity is freshly constructed.
            updated_at=(
                entity.updated_at
                if entity.updated_at is not None
                else entity.created_at
            ),
        )

    def apply_update(self, entity: Document, dto: DocumentUpdate) -> Document:
        """
        Apply ``DocumentUpdate`` fields onto ``entity`` and return a new ``Document``.

        Only non-``None`` fields in ``dto`` are changed.  Uses ``domain_replace()``
        not plain ``dataclasses.replace()`` to preserve ``init=False`` fields
        (``pk``, ``_raw_orm``, timestamps) so the repository performs an UPDATE.

        Args:
            entity: Current persisted state of the document.
            dto:    ``DocumentUpdate`` payload — ``None`` fields mean "no change".

        Returns:
            A new ``Document`` with the update applied.  All ``init=False``
            fields are copied from ``entity`` so the repository updates the
            existing row.

        Edge cases:
            - Sending all-None ``DocumentUpdate`` produces an identical copy —
              a no-op UPDATE.
            - ``owner_id`` is always copied from the original entity; it is
              never changed via this path.
        """
        return domain_replace(
            entity,
            title=dto.title if dto.title is not None else entity.title,
            content=dto.content if dto.content is not None else entity.content,
        )


__all__ = ["DocumentAssembler"]
