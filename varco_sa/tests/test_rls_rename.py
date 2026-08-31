"""
Red tests for AB-1 (Plan 022 / Phase 3) — ``enable_rls_ddl`` → ``render_rls_ddl``.

Verdict recorded at the Phase 1 checkpoint: ``rename+alias``. So both names
must work; the old one must warn; and the two must produce identical output —
this is a rename, not a rewrite.
"""

from __future__ import annotations

import warnings

import pytest


def _call_old(*args, **kwargs):
    """Call the deprecated name with the warning silenced, to compare outputs."""
    from varco_sa import rls

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return rls.enable_rls_ddl(*args, **kwargs)


# ── the new name ──────────────────────────────────────────────────────────────


def test_render_rls_ddl_exists_and_returns_a_list_of_sql_strings() -> None:
    """`render_*` states the shape truthfully: strings out, no I/O."""
    from varco_sa.rls import render_rls_ddl

    statements = render_rls_ddl("public.orders")

    assert isinstance(statements, list)
    assert statements
    assert all(isinstance(s, str) for s in statements)


def test_render_rls_ddl_is_exported_from_the_rls_module_all() -> None:
    """__all__ is what scripts/api_surface.py snapshots."""
    from varco_sa import rls

    assert "render_rls_ddl" in rls.__all__


def test_render_rls_ddl_keeps_the_full_keyword_surface() -> None:
    """A rename must not narrow the signature — that would be a second, unapproved break."""
    import inspect

    from varco_sa.rls import render_rls_ddl

    parameters = inspect.signature(render_rls_ddl).parameters
    for name in ("table", "tenant_column", "setting", "policy_name", "cast_type"):
        assert name in parameters


def test_render_rls_ddl_still_emits_the_initplan_subquery_form() -> None:
    """The module docstring's InitPlan finding is load-bearing — pin it across the rename."""
    from varco_sa.rls import render_rls_ddl

    sql = "\n".join(render_rls_ddl("public.orders"))

    # The InitPlan form is the scalar subquery; production wraps the call in
    # NULLIF(...) inside it, so assert the two load-bearing parts separately
    # rather than a contiguous substring that the NULLIF breaks up.
    assert "(SELECT " in sql
    assert "current_setting(" in sql


# ── the deprecated alias ──────────────────────────────────────────────────────


def test_enable_rls_ddl_still_callable_and_returns_the_same_ddl() -> None:
    """Every existing caller keeps working, byte-for-byte."""
    from varco_sa.rls import render_rls_ddl

    assert _call_old("public.orders") == render_rls_ddl("public.orders")


def test_enable_rls_ddl_matches_the_new_name_for_custom_keywords() -> None:
    """The alias must forward every keyword, not just the positional table name."""
    from varco_sa.rls import render_rls_ddl

    kwargs = {
        "tenant_column": "org_id",
        "setting": "rls.org_id",
        "policy_name": "org_isolation",
        "cast_type": "text",
    }

    assert _call_old("public.orders", **kwargs) == render_rls_ddl("public.orders", **kwargs)


def test_enable_rls_ddl_emits_a_deprecation_warning() -> None:
    """AB-1 ships an alias, and an alias that never warns is never migrated off."""
    from varco_sa.rls import enable_rls_ddl

    with pytest.warns(DeprecationWarning) as record:
        enable_rls_ddl("public.orders")

    message = str(record[0].message)
    assert "enable_rls_ddl" in message
    assert "render_rls_ddl" in message


def test_enable_rls_ddl_warning_blames_the_caller() -> None:
    """A warning pointing at varco_sa/rls.py tells the caller nothing actionable."""
    from varco_sa.rls import enable_rls_ddl

    with pytest.warns(DeprecationWarning) as record:
        enable_rls_ddl("public.orders")

    assert record[0].filename == __file__
