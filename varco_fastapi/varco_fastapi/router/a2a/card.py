"""
varco_fastapi.router.a2a.card
===============================
The A2A v1.0.0 Agent Card shape.

Plan 005, Phase 7, Step 79 (U-4). The pre-v1.0.0 Agent Card served at
``GET /.well-known/agent.json`` already nests capability flags under a
``capabilities`` object and never carried a top-level ``id`` (see
``SkillAdapter.agent_card()``), so this module's job is narrower than it
sounds: it gives the v1.0.0 surface (``GET /.well-known/agent-card.json``) its
own explicit builder, independent of the legacy dict literal in
``skill.py``, so the two can diverge safely once the legacy path is retired.

DESIGN: a plain builder function, not a Pydantic model
    ✅ Matches the existing ``agent_card()`` convention (a JSON-serialisable
       dict) — no new response-model machinery to keep in sync with the A2A
       spec's evolution.
    ✅ Zero-cost: no schema validation overhead on every discovery request.
    ❌ No compile-time guarantee the dict matches the spec shape — mitigated
       by the two card-shape unit tests in ``test_a2a_v1.py``.

Async safety:   ✅ Pure — no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from varco_fastapi.router.a2a.source import SkillDefinition


def build_agent_card_v1(
    *,
    name: str,
    description: str,
    version: str,
    base_url: str,
    skills: list["SkillDefinition"],
    async_mode: bool = False,
    multi_turn: bool = False,
) -> dict[str, Any]:
    """
    Build the A2A v1.0.0 Agent Card.

    Served at ``GET /.well-known/agent-card.json``. Capability flags are
    nested under a ``capabilities`` object; there is **no top-level ``id``
    field** — both per the v1.0.0 spec citation in U-4.

    Args:
        name:         Agent display name.
        description:  Agent description.
        version:      Semantic version string.
        base_url:     Public base URL (no trailing slash) — used to build the
                      JSON-RPC endpoint URL.
        skills:       The agent's skill list.
        async_mode:   ``True`` when a ``job_runner`` is wired — advertises
                      ``stateTransitionHistory``/``asyncTaskExecution``.
        multi_turn:   ``True`` when a ``conversation_store`` is wired.

    Returns:
        A JSON-serialisable dict — the v1.0.0 Agent Card.

    Edge cases:
        - Empty ``skills`` → ``"skills": []`` — the card is still valid; the
          agent will reject every task request.

    Async safety:   ✅ Pure — no I/O.
    """
    return {
        "name": name,
        "description": description,
        "version": version,
        # JSON-RPC 2.0 endpoint — the v1.0.0 transport, replacing /tasks/send
        "url": f"{base_url.rstrip('/')}/a2a",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": async_mode,
            "asyncTaskExecution": async_mode,
            "multiTurnConversation": multi_turn,
        },
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "inputModes": list(skill.input_modes),
                "outputModes": list(skill.output_modes),
            }
            for skill in skills
        ],
    }


__all__ = ["build_agent_card_v1"]
