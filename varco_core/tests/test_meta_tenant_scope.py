"""
Failing tests for ParsedMeta.tenant_scope / MetaReader (Plan 007, Phase 2, step 1).
"""

from __future__ import annotations

import dataclasses

import pytest


def test_tenant_scope_defaults_to_tenant_when_meta_absent() -> None:
    from varco_core.meta import MetaReader
    from varco_core.tenancy.settings import TenantScope

    @dataclasses.dataclass
    class Plain:
        id: int | None = None

    parsed = MetaReader.read(Plain)

    assert parsed.tenant_scope == TenantScope.TENANT


def test_tenant_scope_reads_declared_global() -> None:
    from varco_core.meta import MetaReader
    from varco_core.tenancy.settings import TenantScope

    @dataclasses.dataclass
    class GlobalEntity:
        id: int | None = None

        class Meta:
            tenant_scope = TenantScope.GLOBAL

    parsed = MetaReader.read(GlobalEntity)

    assert parsed.tenant_scope == TenantScope.GLOBAL


def test_invalid_tenant_scope_raises_value_error_naming_field() -> None:
    from varco_core.meta import MetaReader

    @dataclasses.dataclass
    class BadEntity:
        id: int | None = None

        class Meta:
            tenant_scope = "nonsense"

    with pytest.raises(ValueError) as exc:
        MetaReader.read(BadEntity)

    assert "tenant_scope" in str(exc.value)


def test_parsed_meta_is_frozen_and_constructible_without_tenant_scope() -> None:
    from varco_core.meta import ParsedMeta
    from varco_core.tenancy.settings import TenantScope

    fields = {f.name for f in dataclasses.fields(ParsedMeta)}
    assert "tenant_scope" in fields

    field = next(f for f in dataclasses.fields(ParsedMeta) if f.name == "tenant_scope")
    assert field.default == TenantScope.TENANT
