"""
varco_sa.tenancy.global_scope
================================
SQLSTATE ``42501`` (``InsufficientPrivilege``) -> ``GlobalScopeReadOnlyError``
translation (Plan 007, Phase 2, step 5-6, RD-10).

Driver specifics stay here, out of ``varco_core`` — the contract
(``GlobalScopeReadOnlyError``) lives in ``varco_core.tenancy.global_scope``.

DESIGN: a call-wrapping function, not a session event listener
    ✅ Installed only on the global UoW's call path — a tenant UoW's genuine
       permission bugs (`install_tenant_passthrough`) must never be
       mislabelled as "the read-only-global design".
    ✅ Trivial to test without a real Postgres connection — the wrapped
       callable just needs to raise something carrying a ``.orig.sqlstate``/
       ``.orig.pgcode`` attribute, exactly like SQLAlchemy's ``DBAPIError``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from varco_core.tenancy.global_scope import GlobalScopeReadOnlyError

_READ_ONLY_SQLSTATE = "42501"

T = TypeVar("T")


def _sqlstate_of(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def install_global_readonly_translation(
    call: Callable[..., Awaitable[T]], *, entity_name: str
) -> Callable[..., Awaitable[T]]:
    """
    Wrap ``call`` so a SQLSTATE ``42501`` raised through it becomes a
    legible ``GlobalScopeReadOnlyError`` naming ``entity_name`` (RD-10).

    Installed on the **global** UoW only — see ``install_tenant_passthrough``
    for the (deliberately unwrapped) tenant-UoW counterpart.
    """

    async def wrapped(*args: Any, **kwargs: Any) -> T:
        try:
            return await call(*args, **kwargs)
        except Exception as exc:
            if _sqlstate_of(exc) == _READ_ONLY_SQLSTATE:
                raise GlobalScopeReadOnlyError(entity_name) from exc
            raise

    return wrapped


def install_tenant_passthrough(call: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Identity wrapper for the **tenant** UoW's call path.

    A SQLSTATE ``42501`` from a tenant UoW is a genuine permission bug, not
    the read-only-global design — it must propagate as the raw driver
    error, never be translated into ``GlobalScopeReadOnlyError``. This
    function exists purely to make that "no translation here" decision a
    named, testable no-op rather than an absence.
    """
    return call


def maybe_install_global_readonly_translation(
    call: Callable[..., Awaitable[T]], *, entity_name: str, global_writable: bool
) -> Callable[..., Awaitable[T]]:
    """
    Install the read-only translation wrapper unless ``global_writable``.

    Args:
        call:            The call to (maybe) wrap.
        entity_name:      Entity name for the resulting error message.
        global_writable: When ``True`` (``TenancySettings.global_writable`` /
                         ``VARCO_TENANCY_GLOBAL_WRITABLE=true``), no wrapper
                         is installed at all — ``call`` is returned unchanged.

    Returns:
        The wrapped (or, if ``global_writable``, the original) callable.
    """
    if global_writable:
        return call
    return install_global_readonly_translation(call, entity_name=entity_name)
