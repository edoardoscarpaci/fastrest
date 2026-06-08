"""
varco_core.auth.policy
======================
Backend-agnostic **policy engine** abstractions for the authorization layer.

Where ``varco_core.auth.base`` provides the *static*, token-derived authorization
primitives (``AuthContext.can()`` over JWT-encoded ``ResourceGrant``\\s), this module
provides the *dynamic* seam: a pluggable policy engine that evaluates ACL / RBAC /
ABAC rules held outside the token (in a file, a database, or a remote service).

Layer map::

    AsyncService (service layer)
        ↓ injects
    AbstractAuthorizer                  ← varco_core.auth.base
        ↑ implemented by
    PolicyEngineAuthorizer              ← THIS MODULE (bridge)
        ↓ delegates to
    PolicyEngine.enforce(request)       ← THIS MODULE (hot path, backend-agnostic)
        ↑ implemented by
    CasbinPolicyEngine (varco_casbin)   OpaPolicyEngine (varco_opa, future)

    PolicyManagement                    ← THIS MODULE (admin surface)
        ↑ used by
    CasbinPolicyRouter (varco_casbin)   ← REST admin of rules / role assignments

Three concepts live here:

``EnforcementRequest``
    Immutable value object describing *one* authorization question in
    engine-neutral terms: ``(subject, object, action)`` plus optional
    ``subject_attrs`` / ``object_attrs`` (for ABAC) and ``domain`` (for
    multi-tenant / domain-scoped RBAC).

``PolicyEngine`` / ``PolicyManagement``
    The two halves of a policy backend.  ``PolicyEngine.enforce()`` is the
    hot path (one boolean decision); ``PolicyManagement`` is the cold,
    admin-only surface (add / remove / list rules and role assignments).
    They are split so the REST management router can depend on
    ``PolicyManagement`` alone, and high-traffic service code on
    ``PolicyEngine`` alone.

``RequestMapper`` / ``PolicyEngineAuthorizer``
    The bridge from varco's ``(AuthContext, Action, Resource)`` triple into
    an ``EnforcementRequest``, and an ``AbstractAuthorizer`` that runs the
    engine.  Binding ``PolicyEngineAuthorizer`` makes every existing
    ``AsyncService`` policy-aware with no service-code changes.

DESIGN: split enforce (hot) from management (cold) into two ABCs
    ✅ The REST admin router depends only on ``PolicyManagement`` — it can
       never accidentally couple to enforcement internals.
    ✅ A read-only deployment can bind ``PolicyEngine`` without exposing any
       mutation surface at all.
    ✅ A single backend class (e.g. ``CasbinPolicyEngine``) may implement
       both and be bound to both interfaces in DI.
    ❌ Two interfaces instead of one — backends that implement both must
       register two bindings.  Acceptable: the separation is the point.

DESIGN: attributes carried on EnforcementRequest, not a second interface
    ✅ ABAC (attribute matchers like ``r.obj.owner_id == r.sub.id``) works
       through the same ``enforce()`` call as ACL/RBAC — no parallel API.
    ✅ The mapper decides *which* attributes to expose, in one place.
    ❌ Engines that ignore attributes (pure RBAC) carry two empty mappings —
       negligible cost (``field(default_factory=dict)``).

Thread safety:  ✅ ``EnforcementRequest`` / ``RequestMapper`` are frozen — safe to share.
Async safety:   ✅ ``PolicyEngine.enforce`` / ``PolicyManagement`` methods are ``async def``.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from varco_core.auth.base import AbstractAuthorizer, Action, AuthContext, Resource
from varco_core.auth.helpers import _default_resource_key
from varco_core.exception.service import ServiceAuthorizationError

if TYPE_CHECKING:
    # Imported only for type hints — providify is a hard runtime dependency of
    # backends, but core keeps the import behind TYPE_CHECKING so importing the
    # abstractions never forces the DI machinery to load.
    pass


# ── Attribute extraction (Feature 3) ──────────────────────────────────────────


def attributes_of(
    entity: Any,
    *,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """
    Extract a flat attribute mapping from a domain entity for ABAC matchers.

    Policy engines that support attribute-based rules (e.g. a Casbin matcher
    ``m = r.obj.owner_id == r.sub.id``) need the object's fields as named
    attributes.  This helper turns a ``DomainModel`` (a ``@dataclass``) — or
    any object — into a plain ``dict`` the engine can wrap and expose.

    Resolution order:
        1. ``dataclasses.fields`` — preferred; ``DomainModel`` is a dataclass.
        2. ``model_dump()`` — pydantic models (DTOs) if a dataclass it is not.
        3. ``vars(entity)`` — last resort for plain objects with ``__dict__``.
        4. ``{}`` — objects with ``__slots__`` and no ``__dict__`` / dump.

    Args:
        entity:  The object to extract attributes from.  Typically a loaded
                 ``DomainModel`` instance (``Resource.entity``).
        exclude: Field names to omit — use for sensitive columns
                 (``password_hash``, ``secret``) that must never reach a
                 policy expression or audit log.

    Returns:
        A ``dict[str, Any]`` of attribute name → value, minus ``exclude``.
        Empty ``dict`` when ``entity`` is ``None`` or exposes no fields.

    Edge cases:
        - ``entity is None``                     → ``{}`` (collection-level op).
        - dataclass with ``init=False`` fields   → included (e.g. ``pk``).
        - ``model_dump`` raising                 → falls through to ``vars``.
        - ``__slots__`` object, no dump          → ``{}`` (cannot introspect).
        - an ``exclude`` name not present        → ignored silently.

    Example::

        attributes_of(post)                              # {"pk": 42, "owner_id": "u1", ...}
        attributes_of(user, exclude=frozenset({"password_hash"}))
    """
    if entity is None:
        # Collection-level operations have no instance to extract from.
        return {}

    # 1. Dataclass — the DomainModel case.  asdict() recurses into nested
    #    dataclasses which we do NOT want (it would deep-copy the graph), so we
    #    read top-level fields directly via getattr instead.  Leading-underscore
    #    fields (e.g. DomainModel._raw_orm) are framework-managed plumbing —
    #    never expose them to a policy matcher or audit log.
    if dataclasses.is_dataclass(entity) and not isinstance(entity, type):
        return {
            f.name: getattr(entity, f.name)
            for f in dataclasses.fields(entity)
            if not f.name.startswith("_") and f.name not in exclude
        }

    # 2. Pydantic-style model — DTOs expose model_dump().  Guard the call so a
    #    raising dump (custom validators) degrades to the vars() path instead
    #    of propagating an unexpected error out of attribute extraction.
    dump = getattr(entity, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
        except Exception:  # noqa: BLE001 — extraction must never break enforce()
            data = None
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if k not in exclude}

    # 3. Plain object with __dict__.
    raw = getattr(entity, "__dict__", None)
    if isinstance(raw, Mapping):
        return {k: v for k, v in raw.items() if k not in exclude}

    # 4. __slots__ object with no introspectable attributes.
    return {}


def attributes_of_context(ctx: AuthContext) -> dict[str, Any]:
    """
    Extract subject attributes from an ``AuthContext`` for ABAC matchers.

    Exposes the caller's identity in a shape ABAC expressions can read as
    ``r.sub.<attr>``.  ``roles`` / ``scopes`` are materialized to sorted
    ``list``\\s (engines and matchers handle lists, not ``frozenset``), and
    every ``metadata`` key is surfaced at the top level so a matcher can
    reference ``r.sub.tenant_id`` directly.

    Args:
        ctx: The caller's identity / grant snapshot.

    Returns:
        ``dict`` with stable keys ``id`` (the ``user_id`` or ``"anonymous"``),
        ``roles``, ``scopes``, plus every ``metadata`` entry.  ``metadata``
        never shadows the reserved keys.

    Edge cases:
        - Anonymous caller (``user_id is None``) → ``id == "anonymous"``.
        - A ``metadata`` key named ``id`` / ``roles`` / ``scopes`` is dropped
          (reserved keys win) to keep the subject shape predictable.
    """
    reserved = {"id", "roles", "scopes"}
    attrs: dict[str, Any] = {
        # Mirror RequestMapper.subject: None becomes the literal "anonymous"
        # so a matcher comparing strings never sees a Python None.
        "id": ctx.user_id if ctx.user_id is not None else "anonymous",
        "roles": sorted(ctx.roles),
        "scopes": sorted(ctx.scopes),
    }
    # Surface custom claims (tenant_id, plan, region, …) without letting them
    # clobber the reserved identity keys above.
    for key, value in ctx.metadata.items():
        if key not in reserved:
            attrs[key] = value
    return attrs


# ── EnforcementRequest (Feature 1) ────────────────────────────────────────────


@dataclass(frozen=True)
class EnforcementRequest:
    """
    Immutable, engine-neutral description of a single authorization question.

    Maps cleanly onto Casbin's ``enforce(sub, obj, act)`` and onto an OPA
    ``input`` document, so one value object serves every backend.  Attributes
    drive ABAC; ``domain`` drives domain-scoped RBAC (multi-tenant).

    Attributes:
        subject:       Principal identifier — Casbin ``sub``.  Convention:
                       the caller's ``user_id``, or ``"anonymous"``.
        object:        Resource identifier — Casbin ``obj``.  Convention:
                       ``"<entity_plural>"`` or ``"<entity_plural>:<pk>"``.
        action:        Operation — Casbin ``act``.  An ``Action`` value
                       (``"read"``) or any custom verb (``"publish"``); typed
                       as ``str`` so custom actions need no enum membership.
        subject_attrs: Subject attribute bag for ABAC (roles, tenant, claims).
                       See ``attributes_of_context``.
        object_attrs:  Object attribute bag for ABAC (entity fields).
                       See ``attributes_of``.
        domain:        Domain / tenant scope for domain-aware RBAC, or
                       ``None`` for global rules.

    Thread safety:  ✅ frozen=True — immutable, hashable-ish value object.
    Async safety:   ✅ Pure value; no I/O.

    Edge cases:
        - ``subject_attrs`` / ``object_attrs`` default to empty mappings —
          a pure-RBAC engine simply ignores them.
        - The mapping fields are plain ``dict``\\s (not frozen) for engine
          ergonomics; treat them as read-only — do not mutate after creation.

    Example::

        EnforcementRequest(subject="u1", object="posts:42", action="update",
                           object_attrs={"owner_id": "u1"})
    """

    subject: str
    object: str
    action: str
    subject_attrs: Mapping[str, Any] = field(default_factory=dict)
    object_attrs: Mapping[str, Any] = field(default_factory=dict)
    domain: str | None = None


# ── PolicyEngine / PolicyManagement (Feature 1) ───────────────────────────────


class PolicyEngine(ABC):
    """
    Hot-path authorization engine — answers one ``EnforcementRequest``.

    The single method backends must implement to participate in the
    authorization layer.  Kept deliberately tiny so it can sit on the
    critical path of every service call without dragging in management
    concerns.

    Implementations (see ``varco_casbin.CasbinPolicyEngine``) typically hold a
    long-lived enforcer plus a policy store, and must be **shared singletons**
    — a per-call engine would reload policy on every request.

    Thread safety:  ⚠️ Implementation-defined — a shared engine holds mutable
                    enforcer state; document the contract on the subclass.
    Async safety:   ✅ ``enforce`` is ``async def`` — backends may do I/O.

    Edge cases:
        - ``enforce`` returns a decision; it does NOT raise on denial.  The
          raising contract belongs to ``PolicyEngineAuthorizer`` so the engine
          stays a pure predicate (composable, testable).
    """

    @abstractmethod
    async def enforce(self, request: EnforcementRequest) -> bool:
        """
        Decide whether ``request`` is allowed by the active policy.

        Args:
            request: The engine-neutral authorization question.

        Returns:
            ``True`` if the policy permits the request, ``False`` otherwise.

        Raises:
            Implementations should not raise on a *denial* (return ``False``).
            They may raise on infrastructure failure (engine not started,
            store unreachable) — callers treat that as fail-closed.
        """
        ...


class PolicyManagement(ABC):
    """
    Cold, admin-only surface for editing policy rules and role assignments.

    Drives the REST management router (``varco_casbin.CasbinPolicyRouter``) and
    any programmatic policy administration.  Split from ``PolicyEngine`` so
    high-traffic code never sees these mutators.

    Casbin vocabulary mapping:
        - "policy rule" / ``p`` line  → ``add_policy`` / ``remove_policy`` /
          ``list_policies`` (e.g. ``p, alice, posts, read``).
        - "role assignment" / ``g`` line → ``add_role_for_user`` /
          ``remove_role_for_user`` / ``roles_for_user``
          (e.g. ``g, alice, admin``).

    Thread safety:  ⚠️ Mutators change shared enforcer state — implementations
                    must serialize writes (see the lazy-lock rule).
    Async safety:   ✅ All methods are ``async def`` — backends persist via I/O.

    Edge cases:
        - ``add_policy`` of a duplicate rule returns ``False`` (no-op) rather
          than raising — idempotent administration.
        - ``reload`` re-reads the durable store, discarding any unsaved
          in-memory edits — call after out-of-band store changes.
    """

    @abstractmethod
    async def add_policy(self, *values: str, ptype: str = "p") -> bool:
        """
        Add a policy rule (a single ``ptype`` line).

        Args:
            values: The rule tokens in policy-definition order, e.g.
                    ``("alice", "posts", "read")`` for ``p = sub, obj, act``.
            ptype:  Policy type — ``"p"`` for a permission rule (default),
                    ``"p2"`` etc. for additional policy sections.

        Returns:
            ``True`` if the rule was added; ``False`` if it already existed.
        """
        ...

    @abstractmethod
    async def remove_policy(self, *values: str, ptype: str = "p") -> bool:
        """
        Remove a previously-added policy rule.

        Args:
            values: The exact rule tokens to remove (see ``add_policy``).
            ptype:  Policy type to remove from.  Defaults to ``"p"``.

        Returns:
            ``True`` if a rule was removed; ``False`` if no match existed.
        """
        ...

    @abstractmethod
    async def list_policies(self, ptype: str = "p") -> list[tuple[str, ...]]:
        """
        List all policy rules of a given type.

        Args:
            ptype: Policy type to list.  Defaults to ``"p"``.

        Returns:
            A list of rule-token tuples, one per stored rule.  Empty when no
            rules of ``ptype`` exist.
        """
        ...

    @abstractmethod
    async def add_role_for_user(
        self,
        user: str,
        role: str,
        domain: str | None = None,
    ) -> bool:
        """
        Grant ``role`` to ``user`` (a ``g`` grouping line).

        Args:
            user:   Subject the role is granted to.
            role:   Role being granted.
            domain: Optional domain / tenant scope for domain-aware RBAC.

        Returns:
            ``True`` if the assignment was added; ``False`` if it existed.
        """
        ...

    @abstractmethod
    async def remove_role_for_user(
        self,
        user: str,
        role: str,
        domain: str | None = None,
    ) -> bool:
        """
        Revoke ``role`` from ``user``.

        Args:
            user:   Subject to revoke from.
            role:   Role being revoked.
            domain: Optional domain / tenant scope.

        Returns:
            ``True`` if an assignment was removed; ``False`` if none matched.
        """
        ...

    @abstractmethod
    async def roles_for_user(
        self,
        user: str,
        domain: str | None = None,
    ) -> list[str]:
        """
        List the roles assigned to ``user``.

        Args:
            user:   Subject to query.
            domain: Optional domain / tenant scope.

        Returns:
            Role names assigned to the user.  Empty when the user has none.
        """
        ...

    @abstractmethod
    async def reload(self) -> None:
        """
        Reload policy from the durable store, discarding unsaved in-memory edits.

        Returns:
            ``None``.

        Edge cases:
            - Safe to call repeatedly; the operation is idempotent.
            - Must restore the enforcer to the store's current state even if a
              prior in-memory mutation failed to persist.
        """
        ...


# ── RequestMapper (Feature 2) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class RequestMapper:
    """
    Translate a varco ``(AuthContext, Action, Resource)`` triple into an
    ``EnforcementRequest``.

    This is the single place that encodes *how* a varco resource becomes a
    policy-engine subject/object — apps override one method (e.g. to key on
    roles instead of ``user_id``, or to extract a tenant ``domain``) without
    touching engine or service code.

    Defaults:
        - ``subject`` = ``ctx.user_id`` or ``"anonymous"``.
        - ``object``  = ``_default_resource_key`` → ``"posts"`` / ``"posts:42"``
          (the exact convention ``GrantBasedAuthorizer`` already uses, so
          token grants and engine rules share one key namespace).
        - ``action``  = ``str(action)``.
        - attributes  = ``attributes_of_context`` / ``attributes_of``.
        - ``domain``  = ``None``.

    Attributes:
        object_attr_excludes: Field names stripped from ``object_attrs`` — use
            for sensitive columns that must never reach a policy expression.

    Thread safety:  ✅ frozen=True — stateless mapper, safe to share.
    Async safety:   ✅ ``to_request`` is pure and synchronous.

    Edge cases:
        - Collection-level ``Resource`` (``entity is None``) → ``object_attrs``
          is ``{}``; ABAC matchers referencing object fields will not match,
          which correctly denies attribute-gated collection access.

    Example::

        class TenantRequestMapper(RequestMapper):
            # Scope every decision to the caller's tenant.
            def domain_for(self, ctx, action, resource) -> str | None:
                return ctx.metadata.get("tenant_id")
    """

    # Sensitive object fields never exposed to policy expressions / audit.
    object_attr_excludes: frozenset[str] = frozenset()

    def subject_for(self, ctx: AuthContext) -> str:
        """Return the engine subject for ``ctx`` (default: user_id/anonymous)."""
        return ctx.user_id if ctx.user_id is not None else "anonymous"

    def object_for(self, action: Action, resource: Resource) -> str:
        """Return the engine object key for ``resource`` (default convention)."""
        # Reuse the exact key derivation GrantBasedAuthorizer uses so that
        # token-encoded grants and engine policy rules share one namespace.
        return _default_resource_key(resource.entity_type, resource.entity)

    def domain_for(
        self,
        ctx: AuthContext,
        action: Action,
        resource: Resource,
    ) -> str | None:
        """Return the domain/tenant scope, or ``None`` for global (default)."""
        return None

    def to_request(
        self,
        ctx: AuthContext,
        action: Action,
        resource: Resource,
    ) -> EnforcementRequest:
        """
        Build an ``EnforcementRequest`` from the authorization triple.

        Args:
            ctx:      Caller identity / grants.
            action:   Action being attempted.
            resource: What is being acted upon (collection- or instance-level).

        Returns:
            A fully-populated ``EnforcementRequest`` ready for ``enforce``.
        """
        return EnforcementRequest(
            subject=self.subject_for(ctx),
            object=self.object_for(action, resource),
            # str() round-trips both Action members and custom StrEnum verbs.
            action=str(action),
            subject_attrs=attributes_of_context(ctx),
            object_attrs=attributes_of(
                resource.entity, exclude=self.object_attr_excludes
            ),
            domain=self.domain_for(ctx, action, resource),
        )


# ── PolicyEngineAuthorizer (Feature 2) ────────────────────────────────────────


class PolicyEngineAuthorizer(AbstractAuthorizer):
    """
    ``AbstractAuthorizer`` that delegates every decision to a ``PolicyEngine``.

    Binding this as the application ``AbstractAuthorizer`` makes every existing
    ``AsyncService`` policy-engine-driven with **zero service-code changes** —
    services already inject ``AbstractAuthorizer`` and call
    ``authorize(ctx, action, resource)``.

    It is the *raising* adapter around the engine's pure predicate: a ``False``
    decision becomes ``ServiceAuthorizationError`` (→ HTTP 403), with the
    denied subject/object/action recorded in the internal ``reason`` (logged
    server-side, never returned to clients).

    Note: this class is NOT decorated with ``@Singleton`` — binding it is
    **opt-in**.  Backends expose it through a ``@Configuration`` (e.g.
    ``varco_casbin.CasbinAuthorizationConfiguration``) so it never silently
    shadows an app's own authorizer just by being importable.

    Thread safety:  ✅ Stateless besides its injected engine + frozen mapper.
    Async safety:   ✅ ``authorize`` awaits the engine; no shared mutable state.

    Edge cases:
        - Engine infrastructure failure (store down) propagates out of
          ``authorize`` — fail-closed; the service call aborts rather than
          granting access.
        - A custom ``RequestMapper`` is injected once at construction; per-call
          overrides are not supported (use a mapper subclass instead).

    Example::

        authorizer = PolicyEngineAuthorizer(engine)            # default mapper
        authorizer = PolicyEngineAuthorizer(engine, TenantRequestMapper())
    """

    __slots__ = ("_engine", "_mapper")

    def __init__(
        self,
        engine: PolicyEngine,
        mapper: RequestMapper | None = None,
    ) -> None:
        """
        Args:
            engine: The policy engine to evaluate decisions against.  In DI
                    this is ``Inject[PolicyEngine]``.
            mapper: How to map ``(ctx, action, resource)`` → ``EnforcementRequest``.
                    Defaults to the convention-based ``RequestMapper``.
        """
        self._engine = engine
        # Default mapper mirrors GrantBasedAuthorizer's key convention.
        self._mapper = mapper if mapper is not None else RequestMapper()

    async def authorize(
        self,
        ctx: AuthContext,
        action: Action,
        resource: Resource,
    ) -> None:
        """
        Allow iff the policy engine permits the mapped request.

        Args:
            ctx:      Caller identity / grants.
            action:   Action being attempted.
            resource: What is being acted upon.

        Returns:
            ``None`` when the engine permits the request.

        Raises:
            ServiceAuthorizationError: The engine denied the request.  The
                public message hides the cause; ``reason`` carries the denied
                subject/object/action for server-side logging.
        """
        request = self._mapper.to_request(ctx, action, resource)
        if await self._engine.enforce(request):
            return
        raise ServiceAuthorizationError(
            str(action),
            resource.entity_type,
            # Internal-only — logged server-side, never surfaced to clients.
            reason=(
                f"policy denied: subject={request.subject!r} "
                f"object={request.object!r} action={request.action!r}"
                + (f" domain={request.domain!r}" if request.domain else "")
            ),
        )

    def __repr__(self) -> str:
        return f"PolicyEngineAuthorizer(engine={type(self._engine).__name__})"


__all__ = [
    "EnforcementRequest",
    "PolicyEngine",
    "PolicyManagement",
    "RequestMapper",
    "PolicyEngineAuthorizer",
    "attributes_of",
    "attributes_of_context",
]
