"""
Red tests for AB-2 (Plan 022 / Phase 3) — the schema-migration pair rename.

`varco_core.migration.MigrationError` / `.MigrationPlan` become
`SchemaMigrationError` / `SchemaMigrationPlan`, re-exported from the
``varco_core`` top level, with the old names kept as deprecated aliases that
resolve to the **same objects**.

The load-bearing constraint: the *older, unrelated* domain-migration
``varco_core.MigrationError`` / ``varco_core.MigrationPlan``
(``varco_core/migrator.py:89`` / ``:167``) must be untouched. That collision is
the entire reason AB-2 renames the newer pair rather than the older one.

Mechanism note: attribute-access-time deprecation on a module requires a PEP
562 module ``__getattr__``. These tests assert the *behaviour*, not the
implementation — any mechanism that produces it is acceptable.
"""

from __future__ import annotations

import warnings

import pytest


def _get_ignoring_deprecation(module, name: str):
    """Fetch a possibly-deprecated module attribute without tripping -W error."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return getattr(module, name)


# ── the new names exist, at the top level ─────────────────────────────────────


def test_schema_migration_error_is_importable_from_varco_core_top_level() -> None:
    """Closing the deliberate hole at varco_core/__init__.py is the point of the rename."""
    import varco_core

    assert hasattr(varco_core, "SchemaMigrationError")
    assert issubclass(varco_core.SchemaMigrationError, Exception)


def test_schema_migration_plan_is_importable_from_varco_core_top_level() -> None:
    """Same hole, second symbol."""
    import varco_core

    assert hasattr(varco_core, "SchemaMigrationPlan")


def test_both_new_names_are_in_varco_core_dunder_all() -> None:
    """__all__ is what scripts/api_surface.py snapshots — a bare attribute is not enough."""
    import varco_core

    assert "SchemaMigrationError" in varco_core.__all__
    assert "SchemaMigrationPlan" in varco_core.__all__


def test_new_names_live_in_the_migration_subpackage() -> None:
    """The rename happens at the definition site, not via a top-level re-alias."""
    import varco_core.migration as migration

    assert hasattr(migration, "SchemaMigrationError")
    assert hasattr(migration, "SchemaMigrationPlan")


def test_top_level_and_subpackage_new_names_are_the_same_objects() -> None:
    """A re-export must not duplicate the class, or `except` breaks across import paths."""
    import varco_core.migration as migration

    import varco_core

    assert varco_core.SchemaMigrationError is migration.SchemaMigrationError
    assert varco_core.SchemaMigrationPlan is migration.SchemaMigrationPlan


# ── the old names still resolve, and are the same objects ─────────────────────


def test_old_migration_error_alias_is_the_same_object_as_the_new_name() -> None:
    """`except SchemaMigrationError` must catch something raised as MigrationError."""
    import varco_core.migration as migration

    old = _get_ignoring_deprecation(migration, "MigrationError")

    assert old is migration.SchemaMigrationError


def test_old_migration_plan_alias_is_the_same_object_as_the_new_name() -> None:
    """Same identity requirement for the value object."""
    import varco_core.migration as migration

    old = _get_ignoring_deprecation(migration, "MigrationPlan")

    assert old is migration.SchemaMigrationPlan


def test_raising_the_new_name_is_caught_by_the_old_alias() -> None:
    """The behavioural form of the identity assertion — this is what downstreams rely on."""
    import varco_core.migration as migration

    old = _get_ignoring_deprecation(migration, "MigrationError")

    try:
        raise migration.SchemaMigrationError("boom")
    except old as exc:
        assert isinstance(exc, migration.SchemaMigrationError)
    else:  # pragma: no cover - defensive
        pytest.fail("the deprecated alias did not catch the renamed exception")


def test_existing_subclasses_still_inherit_from_the_renamed_base() -> None:
    """PendingMigrationsError & friends must keep their single-except-clause family."""
    import varco_core.migration as migration

    assert issubclass(migration.PendingMigrationsError, migration.SchemaMigrationError)


# ── the old names warn on access ──────────────────────────────────────────────


def test_accessing_the_old_migration_error_name_warns() -> None:
    """The deprecation has to be visible, or nobody migrates before removal."""
    import varco_core.migration as migration

    with pytest.warns(DeprecationWarning) as record:
        migration.MigrationError

    message = str(record[0].message)
    assert "MigrationError" in message
    assert "SchemaMigrationError" in message


def test_accessing_the_old_migration_plan_name_warns() -> None:
    """Same for the plan."""
    import varco_core.migration as migration

    with pytest.warns(DeprecationWarning) as record:
        migration.MigrationPlan

    message = str(record[0].message)
    assert "MigrationPlan" in message
    assert "SchemaMigrationPlan" in message


def test_migration_subpackage_still_raises_attribute_error_for_unknown_names() -> None:
    """A module __getattr__ must not swallow genuine typos."""
    import varco_core.migration as migration

    with pytest.raises(AttributeError) as exc:
        migration.NoSuchSymbol  # noqa: B018

    assert "NoSuchSymbol" in str(exc.value)


# ── the OLD, unrelated domain-migration pair is untouched ─────────────────────


def test_top_level_migration_error_is_still_the_domain_migrator_class() -> None:
    """AB-2's whole justification: varco_core.MigrationError must not change meaning."""
    from varco_core.migrator import MigrationError as DomainMigrationError

    import varco_core

    assert varco_core.MigrationError is DomainMigrationError


def test_top_level_migration_plan_is_still_the_domain_migrator_class() -> None:
    """Same guarantee for the value object."""
    from varco_core.migrator import MigrationPlan as DomainMigrationPlan

    import varco_core

    assert varco_core.MigrationPlan is DomainMigrationPlan


def test_top_level_domain_names_do_not_warn() -> None:
    """They are not deprecated — warning on them would be a false alarm for every caller."""
    import varco_core

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        varco_core.MigrationError  # noqa: B018
        varco_core.MigrationPlan  # noqa: B018

    assert [w for w in record if issubclass(w.category, DeprecationWarning)] == []


def test_domain_and_schema_error_classes_remain_distinct() -> None:
    """If the rename accidentally unified them, every `except` in the tree changes meaning."""
    import varco_core.migration as migration

    import varco_core

    assert varco_core.MigrationError is not migration.SchemaMigrationError
    assert varco_core.MigrationPlan is not migration.SchemaMigrationPlan
