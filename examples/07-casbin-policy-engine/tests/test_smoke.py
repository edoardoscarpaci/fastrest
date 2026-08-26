"""
tests/test_smoke.py
===================
Integration smoke tests for the ``07-casbin-policy-engine`` example.

Exercises Casbin RBAC authorization at the service layer:
    1. Health / reachability check — app starts and returns a usable response.
    2. Admin adds a policy — ``PolicyManagement.add_policy(...)`` persists.
    3. Reader role can read — enforce("reader", "documents", "read") → True.
    4. Reader cannot write — enforce("reader", "documents", "create") → False.
    5. Role assignment — ``add_role_for_user("alice", "reader")`` → alice can read.
    6. Policy persists — a second engine shares the same DB; rules still work.

All tests are ``@pytest.mark.integration`` (require Docker / Postgres).
All tests are ``async def`` under ``asyncio_mode = "auto"`` — no decorator needed.

Casbin resource / action convention
-------------------------------------
``RequestMapper.object_for()`` calls ``_default_resource_key(Document, entity)``
which produces:
    - Collection ops (create, list): ``"documents"``
    - Instance ops (read, update, delete): ``"documents:<pk>"``

Because tests create documents and then read/delete them, we add policies for
BOTH ``"documents"`` AND ``"documents:*"`` — however the RBAC model's wildcard
matcher (``keyMatch2``) is not part of the basic RBAC preset.  Instead we add
explicit policies for the collection key ``"documents"`` and rely on the
service enforcing collection-level keys for read operations (the service passes
the entity at read time, which means instance-level key is used).

SIMPLIFICATION for this example:
    We use the basic RBAC model preset which enforces exact string matching on
    the object field.  To keep tests simple we:
    1. Add a policy for ``"documents"`` covering collection-level actions.
    2. Add a policy for ``"documents:*"`` — but the basic RBAC model does NOT
       do wildcard expansion.
    Instead, we directly call ``engine.add_policy("role", "documents", "read")``
    AND seed the correct instance-level key after creating the document.

    Alternatively, for read/update/delete (instance-level) we skip the
    HTTP endpoint test (which would need the exact pk) and test engine
    enforcement directly.

Thread safety:  ✅ Each test function receives its own client fixture.
Async safety:   ✅ All test methods are ``async def``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# ── Header helpers ────────────────────────────────────────────────────────────


def _user_header(user_id: str, role: str = "") -> dict[str, str]:
    """Build request headers for ``HeaderAuth``."""
    h = {"X-User-Id": user_id}
    if role:
        h["X-User-Role"] = role
    return h


# ── Test 1: App is reachable (no auth needed on the health check) ─────────────


class TestAppHealth:
    """Verify the app starts and responds (even unauthenticated = 401 or 500)."""

    async def test_app_responds_to_unauthenticated_request(self, client) -> None:
        """
        A request without ``X-User-Id`` header is rejected by the middleware stack.

        HeaderAuth raises ``HTTPException(401)``, which propagates through
        Starlette's ``BaseHTTPMiddleware`` task-group machinery as an
        ``ExceptionGroup``.  ``ErrorMiddleware`` may convert it to 401 (if it
        can re-raise) or 500 (if the ExceptionGroup wrapper makes it opaque).
        Either status confirms the app bootstrapped and middleware ran — the
        important thing is that we do NOT receive a 2xx or a connection error.

        DESIGN: accept both 401 and 500 here
            ✅ Both codes prove the middleware stack is active.
            ❌ 500 is not ideal UX; a production app would add an explicit
               HTTPException handler (like example 06) or use a different auth
               strategy that raises 401 before entering BaseHTTPMiddleware.
        """
        # POST without any headers — HeaderAuth rejects the request.
        resp = await client.post(
            "/v1/documents",
            json={"title": "health-check"},
        )
        # Any 4xx or 5xx confirms the app is running and rejecting the request.
        assert resp.status_code in (
            401,
            403,
            500,
        ), f"Expected auth rejection (401/403/500), got {resp.status_code}: {resp.text}"


# ── Test 2: Admin adds a policy and enforcement follows ───────────────────────


class TestPolicyManagement:
    """Verify that ``PolicyManagement.add_policy`` persists and is enforced."""

    async def test_add_policy_enforces_correctly(self, engine) -> None:
        """
        Adding a policy rule via ``PolicyManagement`` is immediately enforced.

        Uses the engine directly (not HTTP) so the test is independent of
        the router layer and focuses on the Casbin integration point.
        """
        from varco_core.auth.policy import EnforcementRequest

        # Unique role/resource names to avoid cross-test contamination.
        role = "test-admin-role"
        resource = "test-posts"

        # Add a rule: test-admin-role can do anything on test-posts.
        added = await engine.add_policy(role, resource, "*")
        # Returns True on first add, False if the rule already existed.
        assert isinstance(added, bool), "add_policy must return bool"

        # Enforce: test-admin-role + test-posts + read → True.
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject=role,
                    object=resource,
                    action="read",
                )
            )
            is True
        )

        # Enforce with a different resource → False (no matching rule).
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject=role,
                    object="other-resource",
                    action="read",
                )
            )
            is False
        )

    async def test_list_and_remove_policy(self, engine) -> None:
        """Add, list, and remove a policy — verifying state after each step."""
        from varco_core.auth.policy import EnforcementRequest

        role = "list-test-role"
        resource = "list-test-resource"
        action = "list-test-action"

        await engine.add_policy(role, resource, action)

        # Rule should appear in list.
        policies = await engine.list_policies()
        assert any(r[0] == role and r[1] == resource and r[2] == action for r in policies), (
            f"Expected ({role}, {resource}, {action}) in {policies}"
        )

        # Remove the rule.
        removed = await engine.remove_policy(role, resource, action)
        assert removed is True, "remove_policy should return True for existing rule"

        # Should no longer be enforced.
        assert (
            await engine.enforce(EnforcementRequest(subject=role, object=resource, action=action))
            is False
        )


# ── Test 3: Reader role can read ──────────────────────────────────────────────


class TestReaderRoleCanRead:
    """A user with the reader role must be allowed to read documents."""

    async def test_reader_role_can_read_documents(self, engine) -> None:
        """
        Enforce returns True for a reader with a read policy on documents.

        Tests the engine directly for isolation from the HTTP + assembler stack.
        """
        from varco_core.auth.policy import EnforcementRequest

        # Add policy: reader role can read "test-documents".
        await engine.add_policy("test-reader-role", "test-documents", "read")

        # Assign the role to a user.
        await engine.add_role_for_user("test-reader-user", "test-reader-role")

        # Enforce as the user (RBAC: user inherits role's permissions).
        allowed = await engine.enforce(
            EnforcementRequest(
                subject="test-reader-user",
                object="test-documents",
                action="read",
            )
        )
        assert allowed is True, "reader-role user must be allowed to read"


# ── Test 4: Reader cannot write ───────────────────────────────────────────────


class TestReaderCannotWrite:
    """A user with only a read policy must be denied write access."""

    async def test_reader_role_cannot_create_documents(self, engine) -> None:
        """
        Enforce returns False when the role has only a read policy, not create.
        """
        from varco_core.auth.policy import EnforcementRequest

        role = "read-only-role"
        resource = "write-test-documents"

        # Only grant read — no create policy.
        await engine.add_policy(role, resource, "read")
        await engine.add_role_for_user("read-only-user", role)

        # Read should be allowed.
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject="read-only-user",
                    object=resource,
                    action="read",
                )
            )
            is True
        )

        # Create should be denied.
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject="read-only-user",
                    object=resource,
                    action="create",
                )
            )
            is False
        )

    async def test_unauthenticated_user_is_denied(self, engine) -> None:
        """A user with no roles or policies is denied everything."""
        from varco_core.auth.policy import EnforcementRequest

        # Use a subject that has never been assigned a role or policy.
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject="ghost-user-with-no-policy",
                    object="documents",
                    action="read",
                )
            )
            is False
        )


# ── Test 5: Role assignment ───────────────────────────────────────────────────


class TestRoleAssignment:
    """Verify that ``add_role_for_user`` grants inherited permissions."""

    async def test_assign_role_and_enforce(self, engine) -> None:
        """
        A user gets a role → inherits that role's policy → enforcement passes.
        """
        from varco_core.auth.policy import EnforcementRequest

        role = "assign-test-role"
        user = "assign-test-user"
        resource = "assign-test-resource"

        # Add policy for the role.
        await engine.add_policy(role, resource, "read")

        # Assign role to user.
        assigned = await engine.add_role_for_user(user, role)
        assert isinstance(assigned, bool)

        # User should now inherit role's permissions.
        assert (
            await engine.enforce(
                EnforcementRequest(
                    subject=user,
                    object=resource,
                    action="read",
                )
            )
            is True
        )

        # Roles list should reflect the assignment.
        roles = await engine.roles_for_user(user)
        assert role in roles, f"Expected {role} in {roles}"

    async def test_revoke_role_removes_permission(self, engine) -> None:
        """Revoking a role removes the inherited permission."""
        from varco_core.auth.policy import EnforcementRequest

        role = "revoke-test-role"
        user = "revoke-test-user"
        resource = "revoke-test-resource"

        await engine.add_policy(role, resource, "read")
        await engine.add_role_for_user(user, role)

        # Confirm access before revocation.
        assert (
            await engine.enforce(EnforcementRequest(subject=user, object=resource, action="read"))
            is True
        )

        # Revoke the role.
        removed = await engine.remove_role_for_user(user, role)
        assert removed is True

        # Access should now be denied.
        assert (
            await engine.enforce(EnforcementRequest(subject=user, object=resource, action="read"))
            is False
        )


# ── Test 6: Policy persists across engine restarts ────────────────────────────


class TestPolicyPersistence:
    """Verify that the SQLAlchemy adapter persists policies across engine instances."""

    async def test_policies_survive_engine_restart(self, db_url: str) -> None:
        """
        Rules written in one engine instance are readable in a fresh instance.

        This test creates a second engine pointing at the same Postgres DB.
        It verifies that the durable store round-trips correctly — core value
        prop of the ``adapter="sqlalchemy"`` choice over the in-memory default.
        """
        from varco_casbin.config import CasbinSettings
        from varco_casbin.engine import CasbinPolicyEngine
        from varco_core.auth.policy import EnforcementRequest

        role = "persist-role"
        user = "persist-user"
        resource = "persist-resource"

        # Write policies through the session-scoped engine (already running).
        # We create a separate engine here to test the persistence round-trip
        # independently without polluting the shared session engine.
        settings = CasbinSettings(
            model_preset="rbac",
            adapter="sqlalchemy",
            db_url=db_url,
            auto_save=True,
        )

        # ── Writer engine ─────────────────────────────────────────────────────
        async with CasbinPolicyEngine(settings) as writer:
            await writer.add_policy(role, resource, "read")
            await writer.add_role_for_user(user, role)

        # ── Reader engine — a fresh instance, same DB ─────────────────────────
        async with CasbinPolicyEngine(settings) as reader:
            # Policy and role assignment must survive the restart.
            assert (
                await reader.enforce(
                    EnforcementRequest(
                        subject=user,
                        object=resource,
                        action="read",
                    )
                )
                is True
            )

            assert role in await reader.roles_for_user(user), (
                f"Role {role!r} not found after engine restart"
            )

            # Also verify that the persisted policy appears in list.
            policies = await reader.list_policies()
            assert any(p[0] == role and p[1] == resource and p[2] == "read" for p in policies), (
                f"Policy not found after restart in {policies}"
            )


# ── Test 7: HTTP endpoint + Casbin enforcement (end-to-end) ──────────────────


class TestHttpEndpointWithCasbin:
    """
    End-to-end tests via the HTTP client — exercises the full stack:
    HeaderAuth → RequestContextMiddleware → DocumentService → PolicyEngineAuthorizer
    → CasbinPolicyEngine.
    """

    async def test_create_document_denied_without_policy(self, client, engine) -> None:
        """
        A user with no Casbin policy gets HTTP 403 on POST /v1/documents.

        The Casbin engine has no rule for "no-policy-user" / "documents" / "create".
        ``PolicyEngineAuthorizer.authorize()`` should raise
        ``ServiceAuthorizationError`` → HTTP 403.
        """
        resp = await client.post(
            "/v1/documents",
            json={"title": "Forbidden"},
            headers=_user_header("no-policy-user"),
        )
        assert resp.status_code == 403, (
            f"Expected 403 without Casbin policy, got {resp.status_code}: {resp.text}"
        )

    async def test_create_document_allowed_with_policy(self, db_url: str) -> None:
        """
        A user with a matching Casbin policy gets HTTP 201 on POST /v1/documents.

        This test creates its own app instance and seeds the policy BEFORE
        starting the app (so the engine loads the rule from Postgres at
        ``start()`` time).  This avoids the need to call ``reload()`` on a
        running engine that belongs to a different fixture.
        """
        import httpx
        from app import create_app
        from httpx import ASGITransport
        from varco_casbin.config import CasbinSettings
        from varco_casbin.engine import CasbinPolicyEngine

        user = "http-writer-allow-test"

        # ── 1. Seed the policy via a standalone engine (same Postgres DB) ────
        settings = CasbinSettings(
            model_preset="rbac",
            adapter="sqlalchemy",
            db_url=db_url,
            auto_save=True,
        )
        async with CasbinPolicyEngine(settings) as seeder:
            await seeder.add_policy(user, "documents", "create")
            await seeder.add_policy(user, "documents", "read")

        # ── 2. Start the app — its engine loads from the same DB at startup ──
        fresh_app = create_app(db_url=db_url, model_preset="rbac")

        async with fresh_app.router.lifespan_context(fresh_app):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=fresh_app, raise_app_exceptions=False),
                base_url="http://test",
            ) as c:
                resp = await c.post(
                    "/v1/documents",
                    json={"title": "Allowed Doc", "content": "Hello Casbin"},
                    headers=_user_header(user),
                )
                assert resp.status_code == 201, (
                    f"Expected 201 with Casbin policy, got {resp.status_code}: {resp.text}"
                )
                body = resp.json()
                assert body["title"] == "Allowed Doc"
                assert "pk" in body
