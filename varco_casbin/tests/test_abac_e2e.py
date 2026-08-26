"""
End-to-end ABAC test (Feature 9)
================================
Proves attributes flow the whole way:

    AuthContext + loaded Resource.entity
        → RequestMapper (attributes_of / attributes_of_context)
            → EnforcementRequest.subject_attrs / object_attrs
                → CasbinPolicyEngine (_AttrStr)
                    → ABAC matcher (r.obj.owner_id == r.sub.id || "admin" in r.sub.roles)

This is the realistic service-layer path: PolicyEngineAuthorizer is what an
AsyncService calls, with a loaded entity in the Resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import pytest
from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_core.auth import Action, AuthContext, PolicyEngineAuthorizer, Resource
from varco_core.exception.service import ServiceAuthorizationError
from varco_core.meta import PKStrategy, PrimaryKey, pk_field
from varco_core.model import DomainModel


@dataclass
class Post(DomainModel):
    """Domain entity carrying an owner attribute for the ABAC matcher."""

    pk: Annotated[str, PrimaryKey(strategy=PKStrategy.STR_ASSIGNED)] = pk_field(
        init=True
    )
    owner_id: str = ""

    class Meta:
        table = "posts"


async def test_abac_authorizer_owner_allowed() -> None:
    """The owner of the entity is allowed to update it (no raise)."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="abac")) as engine:
        authorizer = PolicyEngineAuthorizer(engine)
        ctx = AuthContext(user_id="u1")
        post = Post(pk="1", owner_id="u1")
        # owner_id (u1) == subject id (u1) → matcher allows.
        await authorizer.authorize(ctx, Action.UPDATE, Resource(Post, post))


async def test_abac_authorizer_non_owner_denied() -> None:
    """A non-owner without the admin role is denied (403)."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="abac")) as engine:
        authorizer = PolicyEngineAuthorizer(engine)
        ctx = AuthContext(user_id="u2")
        post = Post(pk="1", owner_id="u1")  # owned by u1, not u2
        with pytest.raises(ServiceAuthorizationError):
            await authorizer.authorize(ctx, Action.UPDATE, Resource(Post, post))


async def test_abac_authorizer_admin_override() -> None:
    """An admin may update any entity regardless of ownership."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="abac")) as engine:
        authorizer = PolicyEngineAuthorizer(engine)
        ctx = AuthContext(user_id="u3", roles=frozenset({"admin"}))
        post = Post(pk="1", owner_id="u1")
        await authorizer.authorize(ctx, Action.UPDATE, Resource(Post, post))
