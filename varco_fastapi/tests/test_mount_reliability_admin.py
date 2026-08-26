"""
tests.test_mount_reliability_admin
====================================
Plan 014 / audit finding F4 — characterization tests for
``varco_fastapi.admin.mount.mount_reliability_admin`` written against
**unmodified** production code (Phase 1 of Plan 014).

Originally ``mount_reliability_admin`` had no double-mount guard (unlike
``mount_tenant_admin``, which already had one). Plan 014 step 16 ported the
same ``_MOUNTED_APPS`` guard onto this module; the two tests that pinned the
prior defect (marked ⟳ in git history) were inverted accordingly.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from varco_core.event.dlq import InMemoryDeadLetterQueue


@pytest.fixture(autouse=True)
def _clear_mounted_apps():
    """
    ``mount_reliability_admin`` mounts no ``_MOUNTED_APPS`` guard today, so
    this fixture is a no-op against unmodified production code — it exists
    so this file is ready, unmodified, for Plan 014 step 16 (which adds the
    module-level ``_MOUNTED_APPS: set[int]``), and so tests in this file
    never depend on `id()` values leaked from a previous test once that
    guard exists.
    """
    from varco_fastapi.admin import mount as mount_module

    mounted_apps = getattr(mount_module, "_MOUNTED_APPS", None)
    if mounted_apps is not None:
        mounted_apps.clear()
    yield
    mounted_apps = getattr(mount_module, "_MOUNTED_APPS", None)
    if mounted_apps is not None:
        mounted_apps.clear()


@pytest.fixture
def dlq() -> InMemoryDeadLetterQueue:
    """Same fixture shape as ``varco_fastapi/tests/test_dlq_router.py``."""
    return InMemoryDeadLetterQueue()


# ── working contract that must not regress ──────────────────────────────────


class TestWorkingContract:
    def test_mount_without_acknowledgement_raises_value_error(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        with pytest.raises(ValueError, match="acknowledge_bundled_admin"):
            mount_reliability_admin(app, dlq=dlq)

    def test_mount_with_acknowledgement_exposes_dlq_route(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

        client = TestClient(app)
        resp = client.get("/reliability/dlq/entries")
        assert resp.status_code != 404

    def test_mount_with_no_server_auth_logs_exactly_one_warning(
        self, dlq: InMemoryDeadLetterQueue, caplog: pytest.LogCaptureFixture
    ) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        with caplog.at_level("WARNING"):
            mount_reliability_admin(
                app, dlq=dlq, acknowledge_bundled_admin=True, server_auth=None
            )

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "UNAUTHENTICATED" in warnings[0].getMessage()


# ── double-mount guard (Plan 014 step 16 inverted the two ⟳ tests below) ───


class TestDoubleMountGuard:
    def test_double_mount_same_prefix_raises(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        """
        Plan 014 step 9, inverted by step 16.

        Calling ``mount_reliability_admin`` twice with the same ``prefix``
        now raises ``ValueError`` on the second call instead of silently
        doubling the routes under ``/reliability/dlq``.
        """
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

        with pytest.raises(ValueError, match="already"):
            mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

    def test_double_mount_different_prefix_raises(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        """
        Plan 014 step 10, inverted by step 16.

        A second mount with a *different* ``prefix`` also raises
        ``ValueError`` — the guard is per-app, not per-prefix.
        """
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

        with pytest.raises(ValueError, match="already"):
            mount_reliability_admin(
                app,
                dlq=dlq,
                acknowledge_bundled_admin=True,
                prefix="/reliability2",
            )


# ── invariants the future guard must preserve ───────────────────────────────


class TestInvariants:
    def test_mounting_on_two_different_apps_both_succeed(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app_a = FastAPI()
        app_b = FastAPI()

        mount_reliability_admin(app_a, dlq=dlq, acknowledge_bundled_admin=True)
        mount_reliability_admin(app_b, dlq=dlq, acknowledge_bundled_admin=True)

        client_a = TestClient(app_a)
        client_b = TestClient(app_b)
        assert client_a.get("/reliability/dlq/entries").status_code != 404
        assert client_b.get("/reliability/dlq/entries").status_code != 404

    def test_mount_nothing_does_not_poison_app_for_a_later_real_mount(
        self, dlq: InMemoryDeadLetterQueue
    ) -> None:
        """
        Mounting with neither ``audit_repo`` nor ``dlq`` mounts nothing and
        does not raise — and a subsequent real mount on that same app still
        succeeds. This is the deliberate deviation from
        ``mount_tenant_admin`` described in the plan's Design section; it
        must stay green both before and after step 16.
        """
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()

        # Mounts nothing — must not raise.
        mount_reliability_admin(app, acknowledge_bundled_admin=True)

        # A later real mount on the same app must still succeed.
        mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

        client = TestClient(app)
        assert client.get("/reliability/dlq/entries").status_code != 404
