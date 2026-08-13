"""
Failing tests for varco_core.tenancy.global_scope (Plan 007, Phase 2, step 3).

GlobalUoWProvider — a distinct DI-token wrapper that resolves regardless of
(and ignores) any active tenant_context().
"""

from __future__ import annotations


def test_make_uow_works_outside_any_tenant_context() -> None:
    from varco_core.tenancy.global_scope import GlobalUoWProvider

    class _FakeUoWProvider:
        def make_uow(self):
            return "global-uow"

    provider = GlobalUoWProvider(delegate=_FakeUoWProvider())

    assert provider.make_uow() == "global-uow"


def test_make_uow_ignores_an_active_tenant_context() -> None:
    from varco_core.service.tenant import tenant_context
    from varco_core.tenancy.global_scope import GlobalUoWProvider

    class _FakeUoWProvider:
        def make_uow(self):
            return "global-uow"

    provider = GlobalUoWProvider(delegate=_FakeUoWProvider())

    with tenant_context("acme"):
        result = provider.make_uow()

    assert result == "global-uow"


def test_global_uow_provider_is_a_distinct_type_from_iuowprovider_binding() -> None:
    from varco_core.service.base import IUoWProvider
    from varco_core.tenancy.global_scope import GlobalUoWProvider

    assert GlobalUoWProvider is not IUoWProvider
    assert (
        not issubclass(GlobalUoWProvider, IUoWProvider)
        or GlobalUoWProvider.__name__ != "IUoWProvider"
    )
