"""
Red tests for RIDER-2 (Plan 022 / Phase 3, step 16).

Both double-mount guards are ``_MOUNTED_APPS: set[int]`` keyed by ``id(app)``.
``id()`` is unique only among *live* objects: once a ``FastAPI`` app is
collected its address can be reused, so a later, unrelated app can collide
with a stale entry and have its admin surface **silently not mounted**.

Fix: ``weakref.WeakSet[FastAPI]`` in both files, changed together.

⚠️ A true id collision is not deterministically reproducible in CPython — the
allocator decides. So the defect is tested at its two observable, deterministic
consequences instead:
  1. the guard container is a ``WeakSet`` (entries are objects, not ints), and
  2. a collected app leaves the guard empty after ``gc.collect()``.
Together these make a stale id structurally impossible; a test that merely
hoped for address reuse would be flaky, not stronger.
"""

from __future__ import annotations

import gc
import weakref

import pytest
from fastapi import FastAPI, Request
from varco_core.auth.base import AuthContext
from varco_fastapi.admin import mount as admin_mount
from varco_fastapi.auth.server_auth import AbstractServerAuth
from varco_fastapi.tenancy import mount as tenancy_mount

MOUNT_MODULES = pytest.mark.parametrize(
    "module",
    [tenancy_mount, admin_mount],
    ids=["tenancy", "admin"],
)


class _StubAuth(AbstractServerAuth):
    async def __call__(self, request: Request) -> AuthContext:
        return AuthContext(user_id="u1", roles=frozenset({"admin"}))


class _FakeControlService:
    async def list_tenants(self, status=None):
        return []


class _FakeAuditRepo:
    async def list(self, *args, **kwargs):
        return []


@pytest.fixture(autouse=True)
def _clean_guards():
    """The guards are module-global — leave them as we found them."""
    yield
    tenancy_mount._MOUNTED_APPS.clear()
    admin_mount._MOUNTED_APPS.clear()


def _mount(module, app: FastAPI) -> None:
    if module is tenancy_mount:
        module.mount_tenant_admin(
            app,
            _FakeControlService(),
            acknowledge_bundled_admin=True,
            server_auth=_StubAuth(),
        )
    else:
        module.mount_reliability_admin(
            app,
            audit_repo=_FakeAuditRepo(),
            acknowledge_bundled_admin=True,
            server_auth=_StubAuth(),
        )


# ── the fix ───────────────────────────────────────────────────────────────────


@MOUNT_MODULES
def test_guard_container_is_a_weakset(module) -> None:
    """A set[int] cannot express 'this exact live app'; a WeakSet can."""
    assert isinstance(module._MOUNTED_APPS, weakref.WeakSet)


@MOUNT_MODULES
def test_guard_holds_the_app_object_not_its_id(module) -> None:
    """Membership must be identity-based on the object, so no stale id can ever match."""
    app = FastAPI()
    _mount(module, app)

    assert app in module._MOUNTED_APPS
    assert id(app) not in list(module._MOUNTED_APPS)


@MOUNT_MODULES
def test_collected_app_leaves_the_guard_empty(module) -> None:
    """The actual defect: a dead app must not keep occupying the guard."""
    app = FastAPI()
    _mount(module, app)
    assert len(module._MOUNTED_APPS) == 1

    del app
    gc.collect()

    assert len(module._MOUNTED_APPS) == 0


@MOUNT_MODULES
def test_a_later_app_can_still_mount_after_an_earlier_one_was_collected(module) -> None:
    """The user-visible symptom of the id-reuse bug: a silently unmounted admin surface."""
    first = FastAPI()
    _mount(module, first)
    del first
    gc.collect()

    second = FastAPI()
    _mount(module, second)

    assert second in module._MOUNTED_APPS
    # The reliability admin mounts under /reliability, the tenant one under
    # /tenants — assert on each module's own prefix rather than one guessed
    # substring that happens to miss the reliability paths entirely.
    prefix = "/tenancy" if module is tenancy_mount else "/reliability"
    assert any(route.path.startswith(prefix) for route in second.routes)


@MOUNT_MODULES
def test_guard_does_not_keep_the_app_alive(module) -> None:
    """A strong-set fix would leak every app ever mounted for the process's lifetime."""
    app = FastAPI()
    _mount(module, app)
    ref = weakref.ref(app)

    del app
    gc.collect()

    assert ref() is None


# ── the guarantee the fix must not lose ───────────────────────────────────────


@MOUNT_MODULES
def test_double_mount_on_the_same_live_app_still_raises(module) -> None:
    """Regression guard: the WeakSet swap must preserve the original refusal."""
    app = FastAPI()
    _mount(module, app)

    with pytest.raises(ValueError) as exc:
        _mount(module, app)

    assert "already" in str(exc.value).lower()


def test_both_modules_use_the_same_guard_type() -> None:
    """admin/mount.py:33 says it mirrors the tenancy one — fixing one alone re-drifts them."""
    assert type(tenancy_mount._MOUNTED_APPS) is type(admin_mount._MOUNTED_APPS)
