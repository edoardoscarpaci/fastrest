"""
varco_fastapi.router.a2a
=========================
A2A (Agent2Agent) protocol v1.0.0 surface.

Splits the pre-v1.0 ``SkillAdapter`` monolith into:

- ``source``        — ``SkillSource`` protocol, ``SkillDefinition``, ``AgentMetadata``.
                       The adapter's *subject*, decoupled from ``VarcoRouter`` introspection.
- ``router_source``  — ``RouterSkillSource``, today's ``VarcoRouter``-introspection
                       behaviour, extracted verbatim.
- ``card``           — the v1.0.0 Agent Card shape (nested ``capabilities``, no
                       top-level ``id``).
- ``jsonrpc``        — the JSON-RPC 2.0 envelope + dispatch table for the v1.0.0
                       transport (``message/send``, ``tasks/get``, ...).

See ``varco_fastapi.router.skill.SkillAdapter`` for the mounting surface, and
``technical_docs/features/a2a-surface.md`` for the full protocol reference.
"""

from __future__ import annotations

from varco_fastapi.router.a2a.card import build_agent_card_v1
from varco_fastapi.router.a2a.jsonrpc import JsonRpcDispatcher
from varco_fastapi.router.a2a.router_source import RouterSkillSource
from varco_fastapi.router.a2a.source import AgentMetadata, SkillDefinition, SkillSource

__all__ = [
    "AgentMetadata",
    "SkillDefinition",
    "SkillSource",
    "RouterSkillSource",
    "build_agent_card_v1",
    "JsonRpcDispatcher",
]
