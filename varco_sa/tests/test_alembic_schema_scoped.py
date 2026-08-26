"""
Failing tests for AlembicMigrator's schema-scoped fan-out support (Plan 007,
Phase 9, step 3).
"""

from __future__ import annotations

import inspect

from sqlalchemy.ext.asyncio import create_async_engine
from varco_core.migration.settings import MigrationSettings
from varco_sa.migration.migrator import AlembicMigrator


def test_schema_kwarg_sets_version_table_schema_and_translate_map() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    migrator = AlembicMigrator(engine, settings=MigrationSettings(), schema="t_acme")

    assert migrator.schema == "t_acme"  # type: ignore[attr-defined]
    assert migrator.version_table_schema == "t_acme"  # type: ignore[attr-defined]


def test_sync_current_heads_is_an_instance_method() -> None:
    # The single load-bearing edit named in the plan: _sync_current_heads
    # must become an instance method (currently @staticmethod) so it can
    # carry the schema through to MigrationContext.configure().
    func = AlembicMigrator.__dict__["_sync_current_heads"]
    assert not isinstance(func, staticmethod)

    params = list(inspect.signature(AlembicMigrator._sync_current_heads).parameters)
    assert params[0] == "self"


def test_schema_none_configure_kwargs_identical_to_today() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    migrator_scoped = AlembicMigrator(engine, settings=MigrationSettings(), schema=None)
    migrator_plain = AlembicMigrator(engine, settings=MigrationSettings())

    assert migrator_scoped.version_table_schema is None  # type: ignore[attr-defined]
    assert migrator_plain.version_table_schema is None  # type: ignore[attr-defined]


def test_per_tenant_lock_key_is_scoped_by_schema() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    base_settings = MigrationSettings(lock_key="varco:migrate")
    migrator = AlembicMigrator(engine, settings=base_settings, schema="t_acme")

    assert migrator._settings.lock_key == "varco:migrate:t_acme"  # type: ignore[attr-defined]
