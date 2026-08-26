"""
varco_fastapi/tests/router/_typing_fixtures/rule_router_typed.py
==================================================================
Static-typing proof fixture for Plan 001 (6th `S` TypeVar on VarcoCRUDRouter).

NOT executed by pytest — this module lives under ``_typing_fixtures/`` (not a
``test_*.py`` file) and is never collected. It exists solely to be checked
manually with pyright, per the plan's Step 6 / Verification section::

    pyright varco_fastapi/tests/router/_typing_fixtures/rule_router_typed.py

Before Plan 001 is implemented:
    - ``CRUDRouter[Rule, UUID, RuleCreate, RuleRead, RuleUpdate, RuleService]``
      is a pyright error — ``CRUDRouter`` only accepts 5 type parameters today.
    - ``self.service`` does not exist (no such property on ``VarcoCRUDRouter``).
    - ``self._service.compile(...)`` is a pyright error — ``self._service`` is
      typed as the illegal ``ClassVar[AsyncService[D, PK, C, R, U] | None]``,
      which erases to ``AsyncService[Any, ...] | None`` and has no ``.compile``.

After Plan 001 lands, this file must type-check clean under ``# pyright: strict``:
    - ``self._service`` narrows to ``RuleService | None`` (the 6th ``S`` TypeVar).
    - ``self.service`` narrows to ``RuleService`` (non-Optional accessor).
    - Both expose ``.compile()`` / ``.validate()`` — the custom methods declared
      on ``RuleService`` beyond the ``AsyncService`` base — with zero
      ``reportAttributeAccessIssue`` / LSP-invariance errors.

This is the human/pyright-verifiable proof referenced by the plan's Risks
section ("No configured type checker in CI"); runtime arity/MRO/property
behaviour is covered separately by ``test_service_typevar.py``.
"""

# pyright: strict

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from varco_core.meta import PKStrategy, PrimaryKey, pk_field
from varco_core.model import DomainModel
from varco_core.repository import AsyncRepository
from varco_core.service import AsyncService
from varco_core.uow import AsyncUnitOfWork
from varco_fastapi.router.presets import CRUDRouter


@dataclass
class Rule(DomainModel):
    """Minimal domain model — mirrors the `pk_field()` idiom documented on
    `DomainModel` (see varco_core/varco_core/model.py)."""

    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()
    name: str = ""


class RuleCreate(BaseModel):
    name: str


class RuleRead(BaseModel):
    id: UUID
    name: str


class RuleUpdate(BaseModel):
    name: str | None = None


class RuleService(AsyncService[Rule, UUID, RuleCreate, RuleRead, RuleUpdate]):
    """Concrete service with custom methods not on the AsyncService base — the
    whole point of the 6th `S` TypeVar is that these are visible on `self.service`
    /  `self._service` without a cast or per-subclass boilerplate property."""

    def compile(self, source: str) -> str:
        return source.upper()

    async def validate(self, source: str) -> bool:
        return bool(source)

    def _get_repo(self, uow: AsyncUnitOfWork) -> AsyncRepository[Rule, UUID]:
        raise NotImplementedError  # typing fixture only — never called


class RuleRouter(CRUDRouter[Rule, UUID, RuleCreate, RuleRead, RuleUpdate, RuleService]):
    """
    Proof router: `self._service` is typed `RuleService | None`, `self.service`
    is typed `RuleService` — both expose `.compile()` / `.validate()` cleanly.
    """

    _prefix = "/rules"

    async def use_service_directly(self, source: str) -> str | None:
        # self._service: RuleService | None -> narrowed by the `is None` guard
        if self._service is None:
            return None
        return self._service.compile(source)

    async def use_service_property(self, source: str) -> bool:
        # self.service: RuleService (non-Optional) -> no `| None` handling needed
        return await self.service.validate(source)
