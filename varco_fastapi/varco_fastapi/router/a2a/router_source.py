"""
varco_fastapi.router.a2a.router_source
========================================
``RouterSkillSource`` — the default ``SkillSource`` implementation, wrapping a
``VarcoRouter`` subclass.

Plan 005, Phase 7, Step 78. This module extracts today's ``SkillAdapter``
route-introspection behaviour **verbatim** — ``introspect_routes()``,
``_auto_skill_id()``, ``_resolve_description()`` and the skill-list-building
loop are unchanged from ``varco_fastapi/varco_fastapi/router/skill.py`` prior
to this plan. ``varco_fastapi/tests/milestone_f/test_skill_adapter.py`` stays
green, unmodified, against this extraction — if it needed edits, the
extraction was not verbatim.

``varco_fastapi.router.skill`` imports the helper functions back from here
(rather than duplicating them) so both modules share one implementation.

Thread safety:  ✅ ``RouterSkillSource`` is read-only after construction — the
                   skill list is pre-computed once, mirroring ``SkillAdapter``.
Async safety:   ✅ ``invoke()`` is the only method performing I/O.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from varco_fastapi.router.a2a.source import (
    _DEFAULT_INPUT_MODES,
    _DEFAULT_OUTPUT_MODES,
    AgentMetadata,
    SkillDefinition,
)
from varco_fastapi.router.introspection import ResolvedRoute, introspect_routes

if TYPE_CHECKING:
    from varco_core.auth.base import AuthContext

    from varco_fastapi.client.base import AsyncVarcoClient

# ── Helpers (verbatim from pre-Phase-7 skill.py) ────────────────────────────────


def _resource_name(router_cls: type) -> str:
    """
    Derive a snake_case resource name from a router class name.

    Strips common suffixes (``Router``, ``Controller``, ``View``) and converts
    CamelCase to snake_case.

    Args:
        router_cls: The ``VarcoRouter`` subclass.

    Returns:
        Lower-case snake_case resource name (e.g. ``"order"`` for ``OrderRouter``).

    Edge cases:
        - Class named exactly ``"Router"`` → returns ``"resource"`` as fallback.
        - No recognised suffix → whole class name is snake_cased.
    """
    name = router_cls.__name__
    for suffix in ("Router", "Controller", "View", "Handler"):
        if name.endswith(suffix) and name != suffix:
            name = name[: -len(suffix)]
            break
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return snake or "resource"


def _auto_skill_id(route: ResolvedRoute, resource: str) -> str:
    """
    Generate a skill ID when no explicit ``skill_id`` override is set.

    Convention mirrors MCP tool naming:
        - CRUD: ``{crud_action}_{resource}``  e.g. ``create_order``
        - Custom: ``{method_name}``            e.g. ``ship_order``

    Args:
        route:    The ``ResolvedRoute`` to name.
        resource: Snake-case resource name.

    Returns:
        Skill ID string suitable for the Agent Card.
    """
    if route.is_crud and route.crud_action:
        return f"{route.crud_action}_{resource}"
    return route.name


def _title_case_id(skill_id: str) -> str:
    """
    Convert a snake_case skill ID to a Title Case display name.

    ``"create_order"``  →  ``"Create Order"``

    Args:
        skill_id: Snake-case skill ID string.

    Returns:
        Title-cased display name string.
    """
    return " ".join(word.capitalize() for word in skill_id.split("_"))


def _resolve_description(
    skill_desc: str | None,
    summary: str | None,
    description: str | None,
    auto: str,
) -> str:
    """
    Apply the skill description fallback chain.

    Priority: ``skill_description`` → ``summary`` → ``description`` → auto-sentence.

    Args:
        skill_desc:  Explicit override.
        summary:     OpenAPI summary.
        description: OpenAPI description.
        auto:        Auto-generated fallback.

    Returns:
        Resolved description string (never empty).
    """
    return skill_desc or summary or description or auto


async def _dispatch_route(
    client: AsyncVarcoClient,
    route: ResolvedRoute,
    body: dict[str, Any],
) -> Any:
    """
    Route a skill task to the correct ``AsyncVarcoClient`` method.

    Extracted from the pre-Phase-7 ``SkillAdapter._dispatch`` — the legacy
    ``handle_task()``/``/tasks/send`` path keeps its own copy unchanged (see
    ``varco_fastapi.router.skill.SkillAdapter._dispatch``) so the milestone_f
    suite stays green against a byte-identical implementation. This copy backs
    ``RouterSkillSource.invoke()`` for the new v1.0.0 JSON-RPC surface.

    Args:
        client: The ``AsyncVarcoClient`` to dispatch through.
        route:  The matched ``ResolvedRoute``.
        body:   Arguments extracted from the skill payload.

    Returns:
        Client method result (Pydantic model or dict).

    Edge cases:
        - CRUD routes use ``"id"`` as the primary key path param.
        - Custom routes pass all body fields as the request body.
    """
    action = route.crud_action
    entity_id = body.pop("id", None)

    if action == "create":
        return await client.create(body)  # type: ignore[attr-defined]
    elif action == "read":
        return await client.read(entity_id)  # type: ignore[attr-defined]
    elif action == "update":
        return await client.update(entity_id, body)  # type: ignore[attr-defined]
    elif action == "patch":
        return await client.patch(entity_id, body)  # type: ignore[attr-defined]
    elif action == "delete":
        return await client.delete(entity_id)  # type: ignore[attr-defined]
    elif action == "list":
        return await client.list(  # type: ignore[attr-defined]
            q=body.get("q"),
            sort=body.get("sort"),
            limit=body.get("limit", 50),
            offset=body.get("offset", 0),
        )
    else:
        # BUG (surfaced by RL-6's mypy gate, plans/017): this called
        # `client.request(method=, path=, json=)` — a method/kwarg shape
        # that has never existed on AsyncVarcoClient (only the private
        # `_request(method, path, *, body=, path_params=, ...)` does, with a
        # different kwarg name and unformatted-path-plus-path_params
        # contract, not a pre-`.format()`-ed path). Any custom (non-CRUD)
        # A2A skill route would have raised AttributeError at dispatch time.
        # No test exercises this branch (grep for "router_source" under
        # varco_fastapi/tests/ finds no custom-route dispatch case), which
        # is why it went undetected.
        path_params = {p: body.pop(p) for p in route.path_params if p in body}
        return await client._request(
            method=route.method,
            path=route.path,
            path_params=path_params,
            body=body or None,
        )


class RouterSkillSource:
    """
    ``SkillSource`` implementation wrapping a ``VarcoRouter`` subclass.

    Today's (pre-v1.0.0) ``SkillAdapter`` behaviour, extracted verbatim: the
    skill list is derived from ``introspect_routes()`` filtered to
    ``skill_enabled`` routes, honouring the same ``skill_id``/``skill_name``/
    ``skill_description``/mode overrides and the same fallback chains.

    Args:
        router_cls:      The ``VarcoRouter`` subclass to expose.
        enabled_routes:  Explicit allowlist of route names (``None`` = all
                         ``skill_enabled`` routes).
        client:          Optional ``AsyncVarcoClient`` used by ``invoke()``.
                         ``SkillAdapter`` passes its own resolved client when
                         wrapping a ``router_cls``. May be left ``None`` when
                         only ``skills()``/``agent_metadata()`` are needed
                         (e.g. introspection-only usage).

    Thread safety:  ✅ Skill list is pre-computed at construction — read-only after.
    Async safety:   ✅ Only ``invoke()`` performs I/O.
    """

    def __init__(
        self,
        router_cls: type,
        *,
        enabled_routes: set[str] | None = None,
        client: AsyncVarcoClient | None = None,
    ) -> None:
        self.router_cls = router_cls
        self._resource = _resource_name(router_cls)
        self._client = client
        self._skills: list[SkillDefinition] = self._build_skills(
            router_cls, self._resource, enabled_routes
        )
        self._skill_by_id: dict[str, SkillDefinition] = {s.id: s for s in self._skills}

    @staticmethod
    def _build_skills(
        router_cls: type,
        resource: str,
        enabled_routes: set[str] | None,
    ) -> list[SkillDefinition]:
        all_routes = introspect_routes(router_cls)
        skills: list[SkillDefinition] = []
        for route in all_routes:
            if not route.skill_enabled:
                continue
            if enabled_routes is not None and route.name not in enabled_routes:
                continue

            skill_id = route.skill_id or _auto_skill_id(route, resource)
            skill_name = route.skill_name or _title_case_id(skill_id)
            auto_desc = f"Perform the '{route.crud_action or route.name}' operation on {resource}."
            description = _resolve_description(
                route.skill_description,
                route.summary,
                route.description,
                auto_desc,
            )
            input_modes = route.skill_input_modes or _DEFAULT_INPUT_MODES
            output_modes = route.skill_output_modes or _DEFAULT_OUTPUT_MODES

            skills.append(
                SkillDefinition(
                    id=skill_id,
                    name=skill_name,
                    description=description,
                    input_modes=input_modes,
                    output_modes=output_modes,
                    route=route,
                )
            )
        return skills

    def skills(self) -> list[SkillDefinition]:
        """Return every A2A skill derived from ``skill_enabled`` routes."""
        return list(self._skills)

    def agent_metadata(self) -> AgentMetadata:
        """Self-description derived from the wrapped router's resource name."""
        return AgentMetadata(
            name=self._resource,
            description=f"Agent exposing {self._resource} operations.",
        )

    async def invoke(
        self,
        skill_id: str,
        payload: dict[str, Any],
        *,
        ctx: AuthContext | None = None,
    ) -> Any:
        """
        Dispatch a skill call through the wrapped ``AsyncVarcoClient``.

        Args:
            skill_id: One of the IDs returned by ``skills()``.
            payload:  Extracted request payload.
            ctx:      Verified caller's ``AuthContext`` — accepted for protocol
                      conformance; ``RouterSkillSource`` does not itself use it
                      (auth is enforced by ``AsyncVarcoClient``'s own auth
                      strategy, not re-checked here).

        Returns:
            The dispatched client call's result.

        Raises:
            KeyError: Unknown ``skill_id``.
            RuntimeError: No ``client`` was supplied at construction.
        """
        skill = self._skill_by_id.get(skill_id)
        if skill is None:
            raise KeyError(
                f"Unknown skill '{skill_id}'. Available: {list(self._skill_by_id)}."
            )
        if self._client is None:
            raise RuntimeError(
                "RouterSkillSource has no client — pass client= to dispatch skills."
            )
        assert skill.route is not None  # router-derived skills always carry a route
        return await _dispatch_route(self._client, skill.route, dict(payload))


__all__ = ["RouterSkillSource", "_dispatch_route"]
