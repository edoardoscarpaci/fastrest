"""
Failing tests for varco_beanie.tenancy.binding (Plan 007, Phase 7, step 1).
"""

from __future__ import annotations

import asyncio
import dataclasses

from varco_core.model import DomainModel
from varco_core.tenancy.settings import TenantScope


@dataclasses.dataclass
class _TenantDoc(DomainModel):
    id: str | None = None


@dataclasses.dataclass
class _GlobalDoc(DomainModel):
    id: str | None = None

    class Meta:
        tenant_scope = TenantScope.GLOBAL


async def test_clones_are_distinct_classes_per_tenant() -> None:
    from varco_beanie.tenancy.binding import build_tenant_binding

    binding_a = await build_tenant_binding(
        "acme", database_name="db_acme", document_models=[_TenantDoc]
    )
    binding_b = await build_tenant_binding(
        "globex", database_name="db_globex", document_models=[_TenantDoc]
    )

    clone_a = binding_a.clone_for(_TenantDoc)
    clone_b = binding_b.clone_for(_TenantDoc)

    assert clone_a is not clone_b
    assert clone_a is not _TenantDoc


async def test_ten_concurrent_ensures_for_one_tenant_call_init_beanie_once(
    monkeypatch,
) -> None:
    from varco_beanie.tenancy import binding as binding_module

    calls = []

    async def fake_init_beanie(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0.01)

    monkeypatch.setattr(binding_module, "init_beanie", fake_init_beanie)

    # NOTE: build_tenant_binding() caches process-globally, keyed by
    # (tenant_id, database_name) — using "acme"/"db_acme" here (the same
    # key test_clones_are_distinct_classes_per_tenant already populates)
    # made this test order-dependent (a cache hit from that earlier test
    # skips init_beanie entirely, so `calls` stayed empty regardless of
    # this test's own behaviour). Using a dedicated key isolates this
    # test's concurrency assertion from that shared global cache.
    await asyncio.gather(
        *(
            binding_module.build_tenant_binding(
                "concurrent-tenant",
                database_name="db_concurrent",
                document_models=[_TenantDoc],
            )
            for _ in range(10)
        )
    )

    assert len(calls) == 1


async def test_beanie_doc_registry_still_returns_base_class() -> None:
    from varco_beanie.factory import BeanieDocRegistry
    from varco_beanie.tenancy.binding import build_tenant_binding

    BeanieDocRegistry._registry[_TenantDoc] = _TenantDoc  # type: ignore[attr-defined]

    await build_tenant_binding("acme", database_name="db_acme", document_models=[_TenantDoc])

    assert BeanieDocRegistry.get(_TenantDoc) is _TenantDoc


async def test_global_entity_binds_to_shared_database_not_cloned() -> None:
    from varco_beanie.tenancy.binding import build_tenant_binding

    binding = await build_tenant_binding(
        "acme",
        database_name="db_acme",
        document_models=[_GlobalDoc],
        global_document_models=[_GlobalDoc],
    )

    assert binding.clone_for(_GlobalDoc) is _GlobalDoc
