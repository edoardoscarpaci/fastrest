"""
service.py
==========
Business logic for the blog post entity backed by MongoDB.

``PostService`` extends ``AsyncService[Post, UUID, PostCreate, PostRead, PostUpdate]``
and overrides ``_get_repo()`` (the only required abstract method) to return the
``posts``-keyed repository from the active unit-of-work.

The service also overrides ``_prepare_for_create()`` to stamp ``created_at``
and ``updated_at`` timestamps before the entity is saved.  Without this, both
fields would be ``None`` in MongoDB — the ``PostRead`` DTO expects real datetimes.

DESIGN: timestamp stamping in the service layer, not the assembler
    ✅ The assembler stays pure (no ``datetime.now()`` calls, no UTC imports).
    ✅ Timestamps are available immediately after ``save()`` returns — no
       extra read needed to retrieve server-side timestamps.
    ✅ ``_prepare_for_create`` is called by ``AsyncService.create()`` inside
       the UoW, so timestamps are consistent with the write time.
    ❌ Timestamps are set in Python, not the DB — millisecond precision
       matches Python's ``datetime.now(UTC)`` resolution (adequate here).

DESIGN: ``domain_replace()`` for timestamp stamping
    ``_prepare_for_create`` uses ``dataclasses.replace()`` (safe here because
    the entity is fresh and has no ``_raw_orm`` yet) rather than ``domain_replace()``.
    Either works for stamping; ``domain_replace()`` is required in assembler's
    ``apply_update`` where ``_raw_orm`` must be preserved.

Thread safety:  ⚠️ Singleton — methods must be stateless; each call opens
                   its own unit-of-work via ``self._uow_provider.make_uow()``.
Async safety:   ✅ All public methods are ``async def``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from dtos import PostCreate, PostRead, PostUpdate
from models import Post
from providify import Inject, Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.auth.base import AbstractAuthorizer
from varco_core.service.base import AsyncService, IUoWProvider

if TYPE_CHECKING:
    from varco_core.auth import AuthContext
    from varco_core.uow import AsyncUnitOfWork


@Singleton
class PostService(AsyncService[Post, UUID, PostCreate, PostRead, PostUpdate]):
    """
    CRUD service for ``Post`` entities backed by MongoDB/Beanie.

    Inherits the full ``AsyncService`` contract:
    - Authorization via injected ``AbstractAuthorizer`` (defaults to permissive).
    - DTO ↔ domain translation via injected ``PostAssembler``.
    - Unit-of-work management via ``IUoWProvider`` (provided by BeanieModule).
    - No event publishing in this example — keeps it focused on Beanie patterns.

    Thread safety:  ⚠️ Singleton — must be stateless; each request gets its own UoW.
    Async safety:   ✅ All public methods are ``async def``.
    """

    def __init__(
        self,
        uow_provider: Inject[IUoWProvider],
        authorizer: Inject[AbstractAuthorizer],
        # Concrete generic alias so providify resolves the correct PostAssembler
        # binding (registered under AbstractDTOAssembler[Post, PostCreate, PostRead, PostUpdate]).
        assembler: Inject[AbstractDTOAssembler[Post, PostCreate, PostRead, PostUpdate]],
    ) -> None:
        """
        Args:
            uow_provider: Injected ``IUoWProvider`` — provided by ``BeanieModule``.
            authorizer:   Injected ``AbstractAuthorizer`` — ``BaseAuthorizer``
                          (permissive) is bound explicitly in ``app.py``.
            assembler:    Injected ``PostAssembler`` — handles DTO ↔ Post mapping.
        """
        # No producer injection — this example does not publish domain events.
        super().__init__(
            uow_provider=uow_provider,
            authorizer=authorizer,
            assembler=assembler,
        )

    def _get_repo(self, uow: AsyncUnitOfWork):
        """
        Return the ``AsyncRepository[Post]`` from the open unit-of-work.

        ``BeanieUnitOfWork`` exposes repositories as attributes named after the
        entity class: ``Post → uow.posts`` (lowercased + "s").
        The attribute is set by ``BeanieUnitOfWork._begin()`` from the
        ``repo_factories`` dict built by ``BeanieRepositoryProvider.make_uow()``.

        DESIGN: attribute access over ``uow.get_repository(Post)``
            ✅ Matches the ``BeanieUnitOfWork`` API — no ``get_repository``
               method exists on the UoW object; only on ``RepositoryProvider``.
            ❌ Attribute name must match the auto-derived name: ``Post → "posts"``.
               Derived by ``_repo_attr()`` in ``provider.py``:
               ``cls.__name__.lstrip("_").lower() + "s"`` → "posts".

        Args:
            uow: The open ``AsyncUnitOfWork`` for this request.

        Returns:
            ``AsyncRepository[Post]`` backed by Beanie/Motor.
        """
        return uow.posts  # type: ignore[attr-defined]

    def _prepare_for_create(self, entity: Post, ctx: AuthContext) -> Post:
        """
        Stamp ``created_at`` and ``updated_at`` before the first INSERT.

        Called by ``AsyncService.create()`` after ``assembler.to_domain()``
        and BEFORE ``repo.save()``.  The entity is assembled but not yet
        persisted when this hook fires.

        Args:
            entity: Freshly assembled ``Post`` (timestamps are ``None``).
            ctx:    Caller's auth context — not used in this example (no auth).

        Returns:
            A new ``Post`` with ``created_at`` and ``updated_at`` both set to now.

        Edge cases:
            - Always calls ``super()._prepare_for_create(entity, ctx)`` so that
              other mixins in the MRO (e.g. ``TenantAwareService``) can also
              stamp their fields after this hook runs.
            - Uses ``dataclasses.replace()`` (not ``domain_replace()``) because
              the entity is fresh — it has no ``_raw_orm`` or ``pk`` to preserve.
              Both approaches are equivalent here; ``dataclasses.replace()`` is
              used for clarity.
            - The returned entity still has ``_raw_orm is None`` — it is
              unpersisted and the repository will INSERT it.
        """
        entity = super()._prepare_for_create(entity, ctx)

        # Stamp both timestamps to the same instant for consistency.
        # Using UTC explicitly so the stored datetime is timezone-aware and
        # readable without ambiguity across deployments in different time zones.
        now = datetime.now(UTC)

        # dataclasses.replace() is safe here — entity is fresh with _raw_orm=None.
        # init=False fields (pk, created_at, updated_at) are NOT copied by
        # dataclasses.replace(); use object.__setattr__ to stamp them.
        # domain_replace() handles this automatically but is required only when
        # _raw_orm must be preserved (UPDATE path, not here).
        from varco_core.model import domain_replace as _domain_replace  # noqa: PLC0415

        return _domain_replace(entity, created_at=now, updated_at=now)


__all__ = ["PostService"]
