"""
Failing tests for varco_sa.tenancy.global_scope (Plan 007, Phase 2, step 5).

SQLSTATE 42501 -> GlobalScopeReadOnlyError translation, installed only on the
global UoW and only when global_writable is False.
"""

from __future__ import annotations

import pytest


class _FakeDBAPIError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.orig = type("orig", (), {"sqlstate": sqlstate, "pgcode": sqlstate})()


async def test_42501_from_global_uow_is_translated() -> None:
    from varco_core.tenancy.global_scope import GlobalScopeReadOnlyError
    from varco_sa.tenancy.global_scope import install_global_readonly_translation

    async def failing_call() -> None:
        raise _FakeDBAPIError("42501")

    wrapped = install_global_readonly_translation(failing_call, entity_name="Artifact")

    with pytest.raises(GlobalScopeReadOnlyError) as exc:
        await wrapped()

    message = str(exc.value)
    assert "Artifact" in message
    assert "global_writable" in message


async def test_42501_from_tenant_uow_is_not_translated() -> None:
    from varco_sa.tenancy.global_scope import install_tenant_passthrough

    async def failing_call() -> None:
        raise _FakeDBAPIError("42501")

    wrapped = install_tenant_passthrough(failing_call)

    with pytest.raises(_FakeDBAPIError):
        await wrapped()


async def test_no_translation_wrapper_when_global_writable() -> None:
    from varco_sa.tenancy.global_scope import maybe_install_global_readonly_translation

    async def call() -> str:
        return "ok"

    wrapped = maybe_install_global_readonly_translation(
        call, entity_name="Artifact", global_writable=True
    )

    assert wrapped is call
