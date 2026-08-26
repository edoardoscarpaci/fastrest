"""
assembler
=========
DTO ↔ domain model translations for the ``Document`` entity.

``DocumentAssembler`` is a stateless singleton — all three mapping methods
are pure functions of their inputs.  It is the only place where
field-to-field mapping logic lives; services and repositories never touch
DTO fields directly.

DESIGN: assembler stays auth-agnostic
    Authorization in this example is handled entirely by the Casbin policy
    engine at the service layer.  The assembler has no knowledge of the
    caller's identity or role.

    ✅ Assembler stays pure (no auth context dependency).
    ✅ Separation of concerns: assembler maps fields, Casbin engine enforces.
    ✅ Same assembler works regardless of which authorizer is wired.

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
    Assembler for the ``Document`` entity in the Casbin policy example.

    Registered as a ``@Singleton`` — the DI container injects a single
    shared instance into every ``DocumentService`` instance.

    Thread safety:  ✅ Stateless — safe to share across all concurrent requests.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: DocumentCreate) -> Document:
        """
        Map ``DocumentCreate`` → fresh, unpersisted ``Document``.

        ``pk`` and timestamps are left unset (``init=False``) — the repository
        assigns them on first ``save()``.

        Args:
            dto: Validated ``DocumentCreate`` payload from the HTTP layer.

        Returns:
            An unpersisted ``Document`` with title and content set.
            ``pk`` and timestamps are ``None`` — set by the repository.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` — the repository
              treats this as an INSERT, not an UPDATE.
        """
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
              ``None`` — a defensive guard for freshly-assembled entities.
        """
        return DocumentRead(
            pk=entity.pk,
            title=entity.title,
            content=entity.content,
            # Defensive fallback: updated_at should never be None after save().
            created_at=entity.created_at,
            updated_at=(entity.updated_at if entity.updated_at is not None else entity.created_at),
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
            A new ``Document`` with the update applied.

        Edge cases:
            - Sending all-None ``DocumentUpdate`` produces an identical copy —
              a no-op UPDATE.
        """
        return domain_replace(
            entity,
            title=dto.title if dto.title is not None else entity.title,
            content=dto.content if dto.content is not None else entity.content,
        )


__all__ = ["DocumentAssembler"]
