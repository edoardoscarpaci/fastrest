"""
varco_fastapi.router.a2a.source
=================================
``SkillSource`` — the A2A adapter's *subject*, decoupled from ``VarcoRouter``.

Plan 005, Phase 7 (U-3 + U-4). Before this module, ``SkillAdapter`` could only
expose a ``VarcoRouter`` subclass — the skill list, dispatch, and Agent Card were
all derived by introspecting routes. ``SkillSource`` is the seam that lets a
``SkillAdapter`` expose *any* subject (a router, a hand-written skill catalogue,
a wrapper around a non-HTTP backend) through the same A2A surface.

``RouterSkillSource`` (``varco_fastapi.router.a2a.router_source``) is the
default/backward-compatible implementation, wrapping today's introspection
behaviour verbatim.

DESIGN: SkillSource as a runtime_checkable Protocol, not an ABC
    ✅ A caller's existing class (e.g. one that already has ``skills()``-shaped
       methods) satisfies the interface structurally — no forced inheritance.
    ✅ Mirrors ``AsyncCache`` (varco_core.cache) — the repo's established pattern
       for a duck-typed integration seam.
    ❌ Structural typing means a near-miss (wrong method signature) fails at
       call time, not at class-definition time — acceptable here since the
       three methods are small and stable.

Thread safety:  ✅ Implementations are expected to be read-only after construction
                   (mirrors ``SkillAdapter``'s own contract).
Async safety:   ✅ Only ``invoke()`` is async; ``skills()``/``agent_metadata()`` are
                   pure, synchronous, no-I/O calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from varco_core.auth.base import AuthContext

    from varco_fastapi.router.introspection import ResolvedRoute

_DEFAULT_INPUT_MODES: tuple[str, ...] = ("application/json",)
_DEFAULT_OUTPUT_MODES: tuple[str, ...] = ("application/json",)


@dataclass(frozen=True)
class SkillDefinition:
    """
    Immutable descriptor for a single A2A skill.

    Historically derived exclusively from a ``ResolvedRoute`` (``route`` was
    always set). Since Phase 7, author-supplied ``SkillDefinition`` instances
    (via ``SkillAdapter(..., skills=[...])``) or non-router ``SkillSource``
    implementations may construct one with ``route=None`` — there is no
    ``VarcoRouter`` route behind it.

    Attributes:
        id:           Unique skill identifier within the agent.
        name:         Human-readable display name.
        description:  Natural-language description of what the skill does.
        input_modes:  MIME types the skill accepts.
        output_modes: MIME types the skill returns.
        route:        Source ``ResolvedRoute`` for traceability, or ``None``
                      for a hand-authored / non-router skill.

    Thread safety:  ✅ frozen=True — immutable.
    Async safety:   ✅ Pure value object.
    """

    id: str
    name: str
    description: str
    input_modes: tuple[str, ...] = _DEFAULT_INPUT_MODES
    output_modes: tuple[str, ...] = _DEFAULT_OUTPUT_MODES
    route: "ResolvedRoute | None" = None


@dataclass(frozen=True)
class AgentMetadata:
    """
    Immutable descriptor for the agent identity a ``SkillSource`` exposes.

    Distinct from ``SkillAdapter``'s own ``agent_name``/``agent_description``
    constructor kwargs (which remain the source of truth for the Agent Card —
    see ``SkillAdapter.__init__``): ``AgentMetadata`` lets a ``SkillSource`` be
    self-describing when used standalone, outside a ``SkillAdapter``.

    Attributes:
        name:        Agent display name.
        description: Agent description.
        extra:       Free-form additional metadata a custom source wants to
                     surface (e.g. contact info, provider). Not consumed by
                     the built-in Agent Card builder.

    Thread safety:  ✅ frozen=True — immutable.
    Async safety:   ✅ Pure value object.
    """

    name: str
    description: str
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SkillSource(Protocol):
    """
    Protocol for the subject a ``SkillAdapter`` exposes over A2A.

    Implementations are expected to be read-only / immutable after
    construction, mirroring ``SkillAdapter``'s own thread-safety contract.

    Methods:
        skills():          Return every A2A skill this source exposes.
        agent_metadata():  Return this source's self-description.
        invoke():           Execute one skill and return its raw result.

    The ``ctx`` keyword on ``invoke()`` is U-3's per-request auth passthrough:
    the adapter surfaces the verified caller's ``AuthContext`` (when auth
    middleware is installed — see ``varco_fastapi.context.get_auth_context_or_none``)
    so a ``SkillSource`` implementation can distinguish the three caller
    classes A2A expects to audit — an end user, another agent, or an
    integrating platform — instead of treating every caller identically.

    Edge cases:
        - ``ctx=None`` means no auth middleware ran (or auth middleware ran but
          the caller was anonymous) — this is NOT itself an authorization
          decision; a ``SkillSource`` that requires authentication must check
          ``ctx`` explicitly and raise.
        - ``invoke()`` raising is expected and normal (e.g. unknown skill,
          validation failure, downstream error) — the JSON-RPC dispatcher maps
          any raised exception to a JSON-RPC error envelope, never a bare 500.

    Thread safety:  ✅ Implementations should be read-only after construction.
    Async safety:   ✅ Only ``invoke()`` performs I/O.
    """

    def skills(self) -> list[SkillDefinition]:
        """Return every A2A skill this source exposes."""
        ...

    def agent_metadata(self) -> AgentMetadata:
        """Return this source's self-description."""
        ...

    async def invoke(
        self,
        skill_id: str,
        payload: dict[str, Any],
        *,
        ctx: "AuthContext | None" = None,
    ) -> Any:
        """
        Execute one skill and return its raw result.

        Args:
            skill_id: The skill identifier to invoke (must be one returned by
                      ``skills()``).
            payload:  The extracted request payload for this skill call.
            ctx:      The verified caller's ``AuthContext``, or ``None`` when
                      no auth middleware populated it.

        Returns:
            The raw skill result (any JSON-serialisable value, or a Pydantic
            model — callers are responsible for serialisation).

        Edge cases:
            - Any exception may be raised on failure (unknown skill,
              validation error, downstream failure) — callers must not
              assume a particular exception type; the JSON-RPC dispatcher
              catches broadly.
        """
        ...


__all__ = ["SkillDefinition", "AgentMetadata", "SkillSource"]
