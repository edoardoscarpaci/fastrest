"""
Failing tests for varco_sa.alembic_helpers (Plan 006, Phase 0, step 1).

``print_create_ddl`` is currently broken on SQLAlchemy 2.x (source correction
1): ``create_engine(..., strategy="mock", executor=...)`` was removed in
1.4+. These tests are the oracle for the Phase 0 repair.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dataclasses import dataclass
from typing import Annotated

from varco_core.meta import PrimaryKey
from varco_core.model import DomainModel
from varco_sa.alembic_helpers import get_target_metadata, print_create_ddl
from varco_sa.factory import SAModelFactory


@dataclass
class _Post(DomainModel):
    """Minimal domain class registered via SAModelFactory, per test_sa_factory.py's pattern."""

    id: Annotated[int, PrimaryKey()] = 0
    title: str = ""


async def _build_domain_cls(factory: SAModelFactory) -> type:
    """Build and register a simple domain class via the factory fixture."""
    factory.build(_Post)
    return _Post


async def test_print_create_ddl_postgresql_returns_create_table(
    factory: SAModelFactory,
) -> None:
    # Regression guard for source correction 1 — this raised TypeError before
    # the Phase 0 repair (removed strategy=/executor= kwargs on SA 2.x).
    domain_cls = await _build_domain_cls(factory)

    ddl = print_create_ddl(domain_cls, dialect="postgresql")

    assert isinstance(ddl, str)
    assert ddl.strip() != ""
    assert "CREATE TABLE" in ddl


async def test_print_create_ddl_sqlite_returns_create_table(
    factory: SAModelFactory,
) -> None:
    domain_cls = await _build_domain_cls(factory)

    ddl = print_create_ddl(domain_cls, dialect="sqlite")

    assert isinstance(ddl, str)
    assert ddl.strip() != ""
    assert "CREATE TABLE" in ddl


async def test_get_target_metadata_includes_generated_table(
    factory: SAModelFactory,
) -> None:
    domain_cls = await _build_domain_cls(factory)
    orm_cls, _mapper = factory.build(domain_cls)

    md = get_target_metadata(domain_cls)

    assert orm_cls.__table__.name in md.tables


async def test_get_target_metadata_with_no_args_returns_empty_metadata() -> None:
    md = get_target_metadata()

    assert md.tables == {}


async def test_get_target_metadata_with_base_includes_hand_written_tables(
    base: type[DeclarativeBase],
) -> None:
    # Exercises the "hand-crafted DeclarativeBase" path documented in
    # get_target_metadata's docstring — tables never touched by the factory.
    class HandWritten(base):  # type: ignore[misc, valid-type]
        __tablename__ = "hand_written"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String)

    md = get_target_metadata(base=base)

    assert "hand_written" in md.tables


async def test_get_target_metadata_unregistered_domain_class_raises_keyerror() -> None:
    class NeverRegistered:
        pass

    with pytest.raises(KeyError):
        get_target_metadata(NeverRegistered)  # type: ignore[arg-type]
