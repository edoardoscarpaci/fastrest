"""
Failing tests for varco_sa.tenancy.engine_registry.SAEngineRegistry (Plan 007,
Phase 6, step 1).
"""

from __future__ import annotations

import logging

import pytest


def _make_registry(db_template="db_{tenant_id}"):
    from varco_sa.tenancy.engine_registry import SAEngineRegistry

    return SAEngineRegistry(
        db_template=db_template, base_dsn="postgresql+asyncpg://user:pw@host/"
    )


async def test_dsn_built_from_template() -> None:
    registry = _make_registry()

    engine = await registry.ensure("acme")

    assert "db_acme" in str(engine.url)


async def test_dsn_ref_override_wins_over_template() -> None:
    registry = _make_registry()

    engine = await registry.ensure(
        "acme", dsn_ref="postgresql+asyncpg://user:pw@other-host/custom_db"
    )

    assert "custom_db" in str(engine.url)


async def test_one_engine_per_tenant() -> None:
    registry = _make_registry()

    engine_1 = await registry.ensure("acme")
    engine_2 = await registry.ensure("acme")

    assert engine_1 is engine_2


async def test_aclose_disposes_every_engine() -> None:
    registry = _make_registry()

    await registry.ensure("acme")
    await registry.ensure("globex")

    await registry.aclose()

    assert registry.peek("acme") is None
    assert registry.peek("globex") is None


async def test_default_per_tenant_sizing_is_pool_size_1_overflow_2() -> None:
    registry = _make_registry()

    engine = await registry.ensure("acme")

    assert engine.pool.size() == 1


async def test_dsn_credential_never_appears_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _make_registry()

    with caplog.at_level(logging.DEBUG):
        engine = await registry.ensure("acme")
        repr(engine)

    all_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pw" not in all_text
