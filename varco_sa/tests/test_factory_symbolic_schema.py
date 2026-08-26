"""
Failing tests for SAModelFactory's symbolic-schema threading (Plan 007,
Phase 3, step 3).
"""

from __future__ import annotations

import dataclasses

from sqlalchemy.orm import DeclarativeBase
from varco_core.meta import MetaReader
from varco_core.model import DomainModel
from varco_core.tenancy.settings import TenantScope
from varco_sa.factory import SAModelFactory


@dataclasses.dataclass
class _TenantEntity(DomainModel):
    pass


@dataclasses.dataclass
class _GlobalEntity(DomainModel):
    class Meta:
        tenant_scope = TenantScope.GLOBAL


def test_under_shared_generated_table_schema_is_none(
    base: type[DeclarativeBase],
) -> None:
    parsed = MetaReader.read(_TenantEntity)
    orm_cls, _mapper = SAModelFactory(base).build(
        _TenantEntity, parsed, isolation="shared"
    )

    assert orm_cls.__table__.schema is None


def test_under_schema_isolation_tenant_model_carries_symbolic_token(
    base: type[DeclarativeBase],
) -> None:
    parsed = MetaReader.read(_TenantEntity)
    orm_cls, _mapper = SAModelFactory(base).build(
        _TenantEntity, parsed, isolation="schema"
    )

    assert orm_cls.__table__.schema == "tenant"


def test_under_schema_isolation_global_model_has_no_symbolic_token(
    base: type[DeclarativeBase],
) -> None:
    parsed = MetaReader.read(_GlobalEntity)
    orm_cls, _mapper = SAModelFactory(base).build(
        _GlobalEntity, parsed, isolation="schema"
    )

    assert orm_cls.__table__.schema is None
