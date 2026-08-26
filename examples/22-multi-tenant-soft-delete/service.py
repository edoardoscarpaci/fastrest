"""
service
=======
Business logic for the ``Note`` entity.

``NoteService`` demonstrates MRO-based mixin composition:

    ValidatorServiceMixin    ← validates title is non-blank (leftmost)
    TenantAwareService       ← tenant scoping + stamping
    SoftDeleteService        ← soft delete instead of physical removal
    AsyncService[Note, ...]  ← base CRUD service (rightmost)

MRO hook chain for ``_scoped_params`` (called by ``list()`` / ``count()``):

    NoteService._scoped_params (not defined — falls through to TenantAwareService)
    → TenantAwareService._scoped_params  (injects tenant_id = <tid>)
        → SoftDeleteService._scoped_params  (injects deleted_at IS NULL)
            → AsyncService._scoped_params  (no-op, returns params)

MRO hook chain for ``_check_entity`` (called by ``get()`` / ``update()`` / ``delete()``):

    TenantAwareService._check_entity  (raises 404 for wrong tenant)
    → SoftDeleteService._check_entity  (raises 404 for soft-deleted)
        → AsyncService._check_entity   (no-op)

MRO hook chain for ``_prepare_for_create`` (called by ``create()``):

    TenantAwareService._prepare_for_create  (stamps tenant_id)
    → SoftDeleteService._prepare_for_create  (resets deleted_at = None)
        → AsyncService._prepare_for_create   (no-op, returns entity)

MRO hook chain for ``_validate_entity`` (called by ``create()`` / ``update()``):

    ValidatorServiceMixin._validate_entity  (delegates to NoteValidator)
    → AsyncService._validate_entity  (no-op)

DESIGN: ValidatorServiceMixin leftmost
    ✅ ``_validate_entity`` override fires before any other mixin's hook.
    ✅ Consistent with the pattern documented in ``validation.py``:
       "This mixin MUST appear first in the MRO".
    ❌ A validation failure aborts before tenant stamping — but validation
       runs AFTER stamping (AsyncService.create calls _prepare_for_create
       before _validate_entity), so tenant_id is available to validators.

DESIGN: TenantAwareService before SoftDeleteService in MRO
    ✅ Tenant filter is applied first in ``_scoped_params`` — queries are
       scoped to the tenant before the soft-delete filter is added.
    ✅ Tenant check runs first in ``_check_entity`` — cross-tenant access
       is blocked before the soft-delete check (avoids leaking information
       about deleted cross-tenant entities).
    ❌ No scenario where reversing the order would be correct.

Additional endpoint: ``list_deleted(tenant_id)``
    ``SoftDeleteService.restore()`` undoes a soft delete.
    We also expose a custom ``list_deleted`` method so callers can see
    their own soft-deleted notes.  This bypasses ``_scoped_params`` by
    building an explicit query with ``deleted_at IS NOT NULL``.

Thread safety:  ⚠️ Singleton — all methods must be stateless;
                   each call opens its own UoW.
Async safety:   ✅ All public methods are ``async def``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from dtos import NoteCreate, NoteRead, NoteUpdate
from models import Note
from providify import Inject, InjectMeta, Singleton
from varco_core.assembler import AbstractDTOAssembler
from varco_core.auth import AuthContext
from varco_core.auth.base import AbstractAuthorizer
from varco_core.event import AbstractEventProducer
from varco_core.exception.service import ServiceValidationError
from varco_core.query.builder import QueryBuilder
from varco_core.query.params import QueryParams
from varco_core.repository import AsyncRepository
from varco_core.service.base import AsyncService, IUoWProvider
from varco_core.service.soft_delete import SoftDeleteService
from varco_core.service.tenant import TenantAwareService
from varco_core.service.validation import ValidatorServiceMixin
from varco_core.uow import AsyncUnitOfWork


@Singleton
class NoteService(
    # DESIGN: mixin order determines MRO hook chain order.
    # ValidatorServiceMixin leftmost — _validate_entity runs first.
    # TenantAwareService before SoftDeleteService — tenant filter/check runs first.
    ValidatorServiceMixin[Note, UUID, NoteCreate, NoteRead, NoteUpdate],
    TenantAwareService[Note, UUID, NoteCreate, NoteRead, NoteUpdate],
    SoftDeleteService[Note, UUID, NoteCreate, NoteRead, NoteUpdate],
    AsyncService[Note, UUID, NoteCreate, NoteRead, NoteUpdate],
):
    """
    CRUD service for ``Note`` entities with tenant isolation and soft delete.

    Composes three mixins via Python's MRO:

    - ``ValidatorServiceMixin``  — validates title is non-blank on create/update.
    - ``TenantAwareService``     — scopes all operations to the caller's tenant.
    - ``SoftDeleteService``      — soft-deletes instead of physical removal.

    All five standard CRUD operations are tenant-safe:

    - ``create()``  — stamps ``tenant_id`` from ``ctx``; resets ``deleted_at``
                      to ``None``; validates title.
    - ``get()``     — raises 404 for cross-tenant or soft-deleted notes.
    - ``list()``    — returns only active notes for the caller's tenant.
    - ``update()``  — checks tenant + soft-delete before applying changes;
                      validates title after update.
    - ``delete()``  — soft-deletes (sets ``deleted_at``); no physical removal.

    Additional operations:
    - ``restore(pk, ctx)``          — undo soft delete (from ``SoftDeleteService``).
    - ``list_deleted(ctx)``         — list soft-deleted notes for this tenant.

    Thread safety:  ⚠️ Singleton — all methods must be stateless.
    Async safety:   ✅ All public methods are ``async def``.
    """

    def __init__(
        self,
        uow_provider: Inject[IUoWProvider],
        authorizer: Inject[AbstractAuthorizer],
        assembler: Inject[AbstractDTOAssembler[Note, NoteCreate, NoteRead, NoteUpdate]],
        # Optional — no bus in this example; declared so events can be added later.
        producer: Annotated[AbstractEventProducer, InjectMeta(optional=True)] = None,
    ) -> None:
        """
        Args:
            uow_provider: Injected UoW factory — the SA provider in tests.
            authorizer:   Injected authorizer — ``BaseAuthorizer`` (permissive).
            assembler:    Injected ``NoteAssembler``.
            producer:     Optional event producer — no-op when not wired.

        Edge cases:
            - All three mixins' ``super().__init__()`` chains are invoked through
              MRO co-operative inheritance — no manual super() calls needed.
        """
        super().__init__(
            uow_provider=uow_provider,
            authorizer=authorizer,
            assembler=assembler,
            producer=producer,
        )

    def _get_repo(self, uow: AsyncUnitOfWork) -> AsyncRepository[Note, UUID]:
        """
        Return the ``AsyncRepository[Note]`` from the open unit of work.

        The ``SQLAlchemyUnitOfWork`` exposes repositories as snake-case plural
        attributes of the entity class name: ``Note`` → ``uow.notes``.

        Args:
            uow: Open ``AsyncUnitOfWork`` for the current operation.

        Returns:
            ``AsyncSQLAlchemyRepository[Note, UUID]`` bound to this UoW.
        """
        return uow.notes  # type: ignore[attr-defined]

    def _validate_entity(self, entity: Note, ctx: AuthContext) -> None:
        """
        Validate business invariants for a ``Note`` entity.

        Rules enforced:
        - ``title`` must not be blank (empty or whitespace-only).

        Called by:
        - ``create()`` after ``_prepare_for_create`` — entity is fully stamped.
        - ``update()`` after ``assembler.apply_update`` — entity reflects new state.

        Chains to ``super()._validate_entity`` via MRO so ``ValidatorServiceMixin``
        can also run its injected validator if one is registered.

        Args:
            entity: The fully-assembled (and stamped) ``Note``.
            ctx:    Caller's identity (not needed for this invariant).

        Raises:
            ServiceValidationError: ``title`` is blank.

        Edge cases:
            - ``title.strip()`` handles whitespace-only titles (e.g. "   ").
            - Chain via ``super()`` so ``ValidatorServiceMixin`` also runs.

        Thread safety:  ✅ Stateless — reads entity fields only.
        Async safety:   ✅ Synchronous — does not yield the event loop.
        """
        if not entity.title.strip():
            # Use 422 semantics — the field value is present but invalid per
            # business rule (empty title is not a type error, it's a rule error).
            raise ServiceValidationError(
                "title must not be blank",
                field="title",
            )
        # Always chain — ValidatorServiceMixin or other mixins may also validate.
        super()._validate_entity(entity, ctx)

    async def list_deleted(self, ctx: AuthContext) -> list[NoteRead]:
        """
        List soft-deleted notes owned by the caller's tenant.

        Bypasses ``_scoped_params`` (which filters OUT soft-deleted notes) and
        builds an explicit query with ``tenant_id = <tid> AND deleted_at IS NOT NULL``.

        Authorization: uses ``Action.LIST`` on ``Note`` — same grant as ``list()``.
        Any tenant member who can list active notes can also list their deleted ones.

        Args:
            ctx: Caller's identity — ``ctx.metadata["tenant_id"]`` required.

        Returns:
            List of ``NoteRead`` DTOs for soft-deleted notes.  Empty when none.

        Raises:
            ServiceAuthorizationError: ``tenant_id`` absent from ``ctx.metadata``.

        Edge cases:
            - Returns an empty list (not 404) when no deleted notes exist.
            - Does NOT call ``_check_entity`` — deleted entities pass through
              intentionally.

        Thread safety:  ⚠️ Singleton — opens its own UoW.
        Async safety:   ✅ ``async def``.
        """
        from varco_core.auth import Action, Resource  # local import avoids circular

        # Require list permission before any DB access.
        await self._authorizer.authorize(
            ctx,
            Action.LIST,
            Resource(entity_type=self._entity_type()),
        )

        # Require tenant identity — same guard as TenantAwareService._require_tenant.
        tid = self._require_tenant(ctx)

        # Build a query: tenant_id = <tid> AND deleted_at IS NOT NULL
        # This is the inverse of the normal list query which uses IS NULL.
        query = (
            QueryBuilder()
            .eq("tenant_id", tid)
            .and_(QueryBuilder().is_not_null("deleted_at"))
            .build()
        )
        params = QueryParams(node=query)

        async with self._uow_provider.make_uow() as uow:
            notes = await self._get_repo(uow).find_by_query(params)
            return [self._assembler.to_read_dto(n) for n in notes]


__all__ = ["NoteService"]
