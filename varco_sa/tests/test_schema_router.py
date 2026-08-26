"""
Failing tests for varco_sa.tenancy.router (Plan 007, Phase 3, step 1).

SASchemaRouter — schema_translate_map session binding, identifier
validation, and the search_path escape hatch.
"""

from __future__ import annotations

import pytest


def test_schema_name_for_applies_template() -> None:
    from varco_sa.tenancy.router import SASchemaRouter

    router = SASchemaRouter(schema_template="t_{tenant_id}")

    assert router.schema_name_for("acme") == "t_acme"


def test_schema_name_for_rejects_invalid_identifier() -> None:
    from varco_sa.tenancy.router import SASchemaRouter

    router = SASchemaRouter(schema_template="t_{tenant_id}")

    with pytest.raises(ValueError):
        router.schema_name_for("acme; DROP TABLE users;--")


def test_session_factory_binds_schema_translate_map() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from varco_sa.tenancy.router import SASchemaRouter

    engine = create_async_engine("sqlite+aiosqlite://")
    router = SASchemaRouter(schema_template="t_{tenant_id}")

    session_factory = router.session_factory_for(engine, "acme")
    session = session_factory()

    bind = session.get_bind()
    assert bind.get_execution_options().get("schema_translate_map") == {
        "tenant": "t_acme"
    }


def test_search_path_mechanism_emits_set_config_never_bare_set() -> None:
    from varco_sa.tenancy.router import SASchemaRouter

    router = SASchemaRouter(schema_template="t_{tenant_id}", mechanism="search_path")

    stmt = router.search_path_statement("acme")

    rendered = str(stmt)
    assert "set_config(" in rendered
    assert "SET search_path" not in rendered.upper().replace("SET_CONFIG", "")
