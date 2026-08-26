"""
assembler.py
============
DTO ↔ domain model translations for the ``Post`` entity.

``PostAssembler`` is a stateless singleton.  It is the ONLY place where
field-to-field mapping logic lives — services and repositories never touch
DTO fields directly.

DESIGN: ``apply_update`` uses ``domain_replace()`` not ``dataclasses.replace()``
    ``domain_replace()`` (from ``varco_core.model``) preserves all ``init=False``
    fields (``pk``, ``created_at``, ``updated_at``, ``_raw_orm``) from the
    original entity.  On Python ≤ 3.12, plain ``dataclasses.replace()`` resets
    ``init=False`` fields to their defaults, causing the repository to perform
    an INSERT instead of an UPDATE (because ``_raw_orm`` would become ``None``).

    ✅ Correct INSERT vs UPDATE detection in the repository.
    ✅ No manual ``object.__setattr__`` in the assembler.
    ❌ Requires ``varco_core.model.domain_replace`` instead of stdlib.

DESIGN: ``created_at`` stamped in assembler fallback, primary stamp in service
    The service ``_prepare_for_create`` hook stamps both timestamps before
    ``save()``.  The assembler's ``to_read_dto()`` falls back to ``now()``
    only as defence-in-depth for malformed data — it should never be needed.

    ✅ Assembler stays mostly pure (no control flow on "has this been saved?").
    ✅ Service hook is the authoritative timestamp source.
    ❌ Dual stamping is a minor code smell — acceptable for clarity.

Thread safety:  ✅ Stateless — all methods are pure transformations.
Async safety:   ✅ No I/O — all methods are synchronous.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dtos import PostCreate, PostRead, PostUpdate
from models import Post
from providify import Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.model import domain_replace


@Singleton
class PostAssembler(AbstractDTOAssembler[Post, PostCreate, PostRead, PostUpdate]):
    """
    Assembler for the ``Post`` entity.

    Registered as a ``@Singleton`` so the DI container injects one shared
    instance into ``PostService``.

    Thread safety:  ✅ Stateless — safe to share across concurrent requests.
    Async safety:   ✅ All methods are synchronous.
    """

    def to_domain(self, dto: PostCreate) -> Post:
        """
        Map ``PostCreate`` → fresh, unpersisted ``Post``.

        ``pk`` is left unset (``pk_field(init=False)``) — the Beanie Document
        generates a UUID via ``default_factory=uuid4`` on INSERT.

        ``created_at`` and ``updated_at`` are inherited from ``AuditedDomainModel``
        and default to ``None`` (``init=False``).  They are stamped by
        ``PostService._prepare_for_create()`` before ``save()``.

        Args:
            dto: Validated ``PostCreate`` payload from the HTTP layer.

        Returns:
            An unpersisted ``Post`` with ``title``, ``content``, and ``author`` set.

        Edge cases:
            - The returned entity has ``_raw_orm is None`` → repository does INSERT.
            - ``created_at``/``updated_at`` MUST be overwritten before ``save()``.
        """
        # Only business fields from the DTO — server-managed fields are left None.
        return Post(title=dto.title, content=dto.content, author=dto.author)

    def to_read_dto(self, entity: Post) -> PostRead:
        """
        Map a persisted ``Post`` → ``PostRead`` response.

        Called after every repository operation that returns a domain entity.

        Args:
            entity: A persisted ``Post`` with all fields populated.

        Returns:
            A ``PostRead`` with all fields from the entity.

        Edge cases:
            - ``entity.created_at`` should never be ``None`` for a persisted entity.
              The fallback to ``now()`` is defence-in-depth — it surfaces a real
              service-layer bug (missed ``_prepare_for_create``) as a wrong timestamp
              rather than a 500 ValidationError.
        """
        # Fallback to now() only as safety net — properly stamped entities always
        # have real timestamps populated by _prepare_for_create or the mapper.
        now = datetime.now(UTC)
        return PostRead(
            pk=entity.pk,
            title=entity.title,
            content=entity.content,
            author=entity.author,
            created_at=entity.created_at or now,
            updated_at=entity.updated_at or now,
        )

    def apply_update(self, entity: Post, dto: PostUpdate) -> Post:
        """
        Apply ``PostUpdate`` fields onto ``entity`` and return a new ``Post``.

        Uses ``domain_replace()`` (not plain ``dataclasses.replace()``) to
        preserve all ``init=False`` fields (``pk``, ``_raw_orm``, ``created_at``)
        from the original.  Preserving ``_raw_orm`` is critical — it tells the
        repository to perform an UPDATE, not an INSERT.

        ``updated_at`` is reset to now() so the PUT always refreshes the timestamp.
        ``created_at`` is preserved from the original — it is write-once.

        Args:
            entity: Current persisted state of the post.
            dto:    ``PostUpdate`` payload with the new field values.

        Returns:
            A new ``Post`` instance with all mutable fields updated and
            all ``init=False`` fields copied from the original.

        Edge cases:
            - All three dto fields are required for PUT — the API contract is a
              full replacement.  Partial updates would use PATCH + ``PostPatch``
              DTO with optional fields.
            - ``created_at`` is write-once — ``domain_replace`` copies it from the
              original automatically because it is an ``init=False`` field.
        """
        # Reset updated_at to now — every PUT refreshes the modification timestamp.
        now = datetime.now(UTC)
        return domain_replace(
            entity,
            title=dto.title,
            content=dto.content,
            author=dto.author,
            # Only updated_at is overwritten — created_at is write-once and
            # preserved by domain_replace() copying init=False fields from the
            # original.
            updated_at=now,
        )


__all__ = ["PostAssembler"]
