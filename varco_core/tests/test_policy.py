"""
Unit tests for varco_core.auth.policy
=========================================
Covers the backend-agnostic policy-engine seam (Features 1–3):

  - ``EnforcementRequest`` — frozen value object, attribute defaults.
  - ``attributes_of`` / ``attributes_of_context`` — ABAC attribute extraction,
    including exclusions and the leading-underscore (framework field) filter.
  - ``RequestMapper`` — default convention mapping + override hooks
    (custom subject, custom domain).
  - ``PolicyEngineAuthorizer`` — happy path (engine allows → no raise) and
    unhappy path (engine denies → ServiceAuthorizationError with internal reason).
  - Custom action verbs flow through as plain strings (extensible Action).

All tests are pure-logic + async (auto mode); the ``PolicyEngine`` is a
hand-rolled fake so there is no I/O and no DI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

import pytest
from varco_core.auth import (
    Action,
    AuthContext,
    EnforcementRequest,
    PolicyEngine,
    PolicyEngineAuthorizer,
    PolicyManagement,
    RequestMapper,
    Resource,
    attributes_of,
    attributes_of_context,
)
from varco_core.exception.service import ServiceAuthorizationError
from varco_core.meta import PKStrategy, PrimaryKey, pk_field
from varco_core.model import DomainModel

# ── Fixtures ──────────────────────────────────────────────────────────────────


@dataclass
class Post(DomainModel):
    """Minimal domain entity with an owner field for ABAC-style tests."""

    # STR_ASSIGNED pk so it can be passed in the constructor directly.
    pk: Annotated[str, PrimaryKey(strategy=PKStrategy.STR_ASSIGNED)] = pk_field(
        init=True
    )
    owner_id: str = ""
    title: str = ""

    class Meta:
        table = "posts"


class PostAction(StrEnum):
    """Domain-specific action verb beyond the built-in CRUD set."""

    PUBLISH = "publish"


class _FakeEngine(PolicyEngine):
    """
    Configurable in-memory engine: returns ``self.decision`` and records the
    last request it saw, so tests can assert on the mapping without a backend.
    """

    def __init__(self, decision: bool) -> None:
        self.decision = decision
        self.last_request: EnforcementRequest | None = None

    async def enforce(self, request: EnforcementRequest) -> bool:
        self.last_request = request
        return self.decision


# ── attributes_of ─────────────────────────────────────────────────────────────


def test_attributes_of_none_returns_empty() -> None:
    """Collection-level ops pass ``entity=None`` → no attributes."""
    assert attributes_of(None) == {}


def test_attributes_of_dataclass_excludes_framework_fields() -> None:
    """Leading-underscore fields (e.g. _raw_orm) must never be exposed."""
    post = Post(pk="42", owner_id="u1", title="hi")
    attrs = attributes_of(post)
    assert attrs == {"pk": "42", "owner_id": "u1", "title": "hi"}
    assert "_raw_orm" not in attrs


def test_attributes_of_honours_explicit_exclude() -> None:
    """Caller-supplied excludes drop sensitive columns."""
    post = Post(pk="42", owner_id="u1", title="secret")
    attrs = attributes_of(post, exclude=frozenset({"title"}))
    assert "title" not in attrs
    assert attrs == {"pk": "42", "owner_id": "u1"}


def test_attributes_of_slots_object_returns_empty() -> None:
    """An object with __slots__ and no dump/__dict__ yields {} (not an error)."""

    class Slotted:
        __slots__ = ()

    assert attributes_of(Slotted()) == {}


# ── attributes_of_context ─────────────────────────────────────────────────────


def test_attributes_of_context_shape_and_metadata() -> None:
    """Subject attrs expose id/roles/scopes plus flattened metadata."""
    ctx = AuthContext(
        user_id="u1",
        roles=frozenset({"editor", "author"}),
        scopes=frozenset({"read:posts"}),
        metadata={"tenant_id": "t1"},
    )
    attrs = attributes_of_context(ctx)
    assert attrs["id"] == "u1"
    assert attrs["roles"] == ["author", "editor"]  # sorted
    assert attrs["scopes"] == ["read:posts"]
    assert attrs["tenant_id"] == "t1"


def test_attributes_of_context_anonymous_id() -> None:
    """An anonymous context surfaces id == 'anonymous', never None."""
    attrs = attributes_of_context(AuthContext())
    assert attrs["id"] == "anonymous"


def test_attributes_of_context_metadata_cannot_shadow_reserved() -> None:
    """Reserved keys (id/roles/scopes) win over colliding metadata keys."""
    ctx = AuthContext(user_id="u1", metadata={"id": "spoofed", "roles": "x"})
    attrs = attributes_of_context(ctx)
    assert attrs["id"] == "u1"
    assert attrs["roles"] == []  # the real (empty) roles, not "x"


# ── EnforcementRequest ────────────────────────────────────────────────────────


def test_enforcement_request_defaults() -> None:
    """Attribute bags default to empty mappings; domain defaults to None."""
    req = EnforcementRequest(subject="u1", object="posts", action="read")
    assert req.subject_attrs == {}
    assert req.object_attrs == {}
    assert req.domain is None


def test_enforcement_request_is_frozen() -> None:
    """The value object is immutable."""
    req = EnforcementRequest(subject="u1", object="posts", action="read")
    with pytest.raises(Exception):
        req.subject = "u2"  # type: ignore[misc]


# ── RequestMapper ─────────────────────────────────────────────────────────────


def test_mapper_default_instance_level() -> None:
    """Instance-level resource → 'posts:<pk>' with object attrs populated."""
    ctx = AuthContext(user_id="u1")
    post = Post(pk="42", owner_id="u1")
    req = RequestMapper().to_request(ctx, Action.UPDATE, Resource(Post, post))
    assert req.subject == "u1"
    assert req.object == "posts:42"
    assert req.action == "update"
    assert req.object_attrs["owner_id"] == "u1"
    assert req.domain is None


def test_mapper_default_collection_level_anonymous() -> None:
    """Collection-level + anonymous → 'posts', subject 'anonymous', no attrs."""
    req = RequestMapper().to_request(AuthContext(), Action.LIST, Resource(Post))
    assert req.subject == "anonymous"
    assert req.object == "posts"
    assert req.object_attrs == {}


def test_mapper_excludes_propagate_to_object_attrs() -> None:
    """object_attr_excludes strips sensitive fields from the mapped request."""
    mapper = RequestMapper(object_attr_excludes=frozenset({"title"}))
    post = Post(pk="42", owner_id="u1", title="secret")
    req = mapper.to_request(
        AuthContext(user_id="u1"), Action.READ, Resource(Post, post)
    )
    assert "title" not in req.object_attrs


def test_mapper_override_subject_and_domain() -> None:
    """Subclasses can re-key subject and inject a tenant domain."""

    class TenantMapper(RequestMapper):
        def subject_for(self, ctx: AuthContext) -> str:
            # Key on the first role instead of the user id.
            return next(iter(sorted(ctx.roles)), "anonymous")

        def domain_for(self, ctx, action, resource) -> str | None:
            return ctx.metadata.get("tenant_id")

    ctx = AuthContext(
        user_id="u1", roles=frozenset({"editor"}), metadata={"tenant_id": "t9"}
    )
    req = TenantMapper().to_request(ctx, Action.READ, Resource(Post))
    assert req.subject == "editor"
    assert req.domain == "t9"


def test_mapper_custom_action_verb() -> None:
    """A custom StrEnum action round-trips as its string value."""
    req = RequestMapper().to_request(
        AuthContext(user_id="u1"), PostAction.PUBLISH, Resource(Post)
    )
    assert req.action == "publish"


# ── PolicyEngineAuthorizer ────────────────────────────────────────────────────


async def test_authorizer_allows_when_engine_permits() -> None:
    """Happy path: engine returns True → authorize() returns None (no raise)."""
    engine = _FakeEngine(decision=True)
    authorizer = PolicyEngineAuthorizer(engine)
    ctx = AuthContext(user_id="u1")
    post = Post(pk="42", owner_id="u1")

    result = await authorizer.authorize(ctx, Action.UPDATE, Resource(Post, post))

    assert result is None
    # The engine saw the correctly-mapped request.
    assert engine.last_request is not None
    assert engine.last_request.object == "posts:42"
    assert engine.last_request.action == "update"


async def test_authorizer_denies_when_engine_refuses() -> None:
    """Unhappy path: engine returns False → ServiceAuthorizationError (403)."""
    engine = _FakeEngine(decision=False)
    authorizer = PolicyEngineAuthorizer(engine)
    ctx = AuthContext(user_id="u1")
    post = Post(pk="42", owner_id="u2")  # not the owner

    with pytest.raises(ServiceAuthorizationError) as exc:
        await authorizer.authorize(ctx, Action.DELETE, Resource(Post, post))

    err = exc.value
    assert err.operation == "delete"
    assert err.entity_cls is Post
    # Internal reason carries the denied triple but is never in the public message.
    assert "posts:42" in (err.reason or "")
    assert "posts:42" not in str(err)


async def test_authorizer_uses_custom_mapper() -> None:
    """An injected mapper changes how the request is built."""

    class DomainMapper(RequestMapper):
        def domain_for(self, ctx, action, resource) -> str | None:
            return "tenant-x"

    engine = _FakeEngine(decision=True)
    authorizer = PolicyEngineAuthorizer(engine, DomainMapper())
    await authorizer.authorize(AuthContext(user_id="u1"), Action.READ, Resource(Post))

    assert engine.last_request is not None
    assert engine.last_request.domain == "tenant-x"


async def test_authorizer_propagates_engine_failure_fail_closed() -> None:
    """Infrastructure failure in the engine aborts the call (fail-closed)."""

    class _BrokenEngine(PolicyEngine):
        async def enforce(self, request: EnforcementRequest) -> bool:
            raise RuntimeError("store unreachable")

    authorizer = PolicyEngineAuthorizer(_BrokenEngine())
    with pytest.raises(RuntimeError, match="store unreachable"):
        await authorizer.authorize(
            AuthContext(user_id="u1"), Action.READ, Resource(Post)
        )


# ── ABC contract ──────────────────────────────────────────────────────────────


def test_policy_engine_is_abstract() -> None:
    """PolicyEngine cannot be instantiated without implementing enforce()."""
    with pytest.raises(TypeError):
        PolicyEngine()  # type: ignore[abstract]


def test_policy_management_is_abstract() -> None:
    """PolicyManagement cannot be instantiated without its mutators."""
    with pytest.raises(TypeError):
        PolicyManagement()  # type: ignore[abstract]
