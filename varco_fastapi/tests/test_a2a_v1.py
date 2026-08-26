"""
Unit tests for the A2A v1.0.0 surface + SkillSource (plan 005, Phase 7, Step 76).
====================================================================================

RED until Steps 77-82 land:
    - ``varco_fastapi.router.a2a.source`` (new) — ``SkillDefinition``,
      ``AgentMetadata``, ``SkillSource`` Protocol.
    - ``varco_fastapi.router.a2a.router_source`` (new) — ``RouterSkillSource``
      (today's introspection behaviour extracted verbatim).
    - ``varco_fastapi.router.a2a.card`` (new) — the v1.0.0 Agent Card model:
      capability flags nested under ``capabilities``, no top-level ``id``.
    - ``varco_fastapi.router.a2a.jsonrpc`` (new) — JSON-RPC 2.0 envelope +
      dispatch for ``message/send``/``tasks/get``/``tasks/list``/``tasks/cancel``.
    - ``SkillAdapter.__init__`` gains ``source=``/``skills=``; exactly one of
      ``router_cls``/``source`` required.
    - ``SkillAdapter.mount()`` gains ``legacy_paths: bool = True``; the v1
      surface (``GET /.well-known/agent-card.json`` + JSON-RPC endpoint) is
      always mounted; legacy paths (``GET /.well-known/agent.json``,
      ``POST /tasks/send``, ``GET /tasks/{task_id}``,
      ``GET /tasks/{task_id}/history``) only when ``legacy_paths=True``.

Existing ``test_skill_adapter.py`` (milestone_f) must stay green and
unmodified — this file only adds coverage for the new v1 surface.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from varco_fastapi.router.base import VarcoRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.mixins import CreateMixin, ReadMixin
from varco_fastapi.router.skill import SkillAdapter


class _OrderRouter(CreateMixin, ReadMixin, VarcoRouter):
    _prefix = "/orders"
    _create_skill = True
    _read_skill = True

    @route("GET", "/ping", skill=True)
    async def ping(self) -> dict: ...


def _make_adapter(**kwargs) -> SkillAdapter:
    mock_client = MagicMock()
    return SkillAdapter(
        _OrderRouter,
        agent_name="OrderAgent",
        agent_description="Manages orders",
        client=mock_client,
        **kwargs,
    )


# ════════════════════════════════════════════════════════════════════════════════
# SkillSource protocol module (Step 77)
# ════════════════════════════════════════════════════════════════════════════════


class TestSkillSourceModuleExists:
    def test_skill_source_protocol_importable(self) -> None:
        from varco_fastapi.router.a2a.source import SkillSource

        assert SkillSource is not None

    def test_skill_definition_importable(self) -> None:
        from varco_fastapi.router.a2a.source import SkillDefinition

        assert SkillDefinition is not None

    def test_agent_metadata_importable(self) -> None:
        from varco_fastapi.router.a2a.source import AgentMetadata

        assert AgentMetadata is not None


# ════════════════════════════════════════════════════════════════════════════════
# RouterSkillSource extraction (Step 78) — must be behaviourally verbatim
# ════════════════════════════════════════════════════════════════════════════════


class TestRouterSkillSourceExtraction:
    def test_router_skill_source_importable(self) -> None:
        from varco_fastapi.router.a2a.router_source import RouterSkillSource

        assert RouterSkillSource is not None

    def test_router_skill_source_wraps_router_cls_and_lists_skills(self) -> None:
        from varco_fastapi.router.a2a.router_source import RouterSkillSource

        source = RouterSkillSource(_OrderRouter)
        skills = source.skills()
        assert len(skills) >= 1


# ════════════════════════════════════════════════════════════════════════════════
# Agent Card v1.0.0 shape (Step 79) — nested capabilities, no top-level id
# ════════════════════════════════════════════════════════════════════════════════


class TestAgentCardV1Endpoint:
    def test_agent_card_v1_path_returns_200(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200

    def test_agent_card_v1_has_nested_capabilities_object(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/.well-known/agent-card.json").json()
        assert isinstance(body.get("capabilities"), dict)

    def test_agent_card_v1_has_no_top_level_id(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        body = resp.json()
        assert "id" not in body


# ════════════════════════════════════════════════════════════════════════════════
# JSON-RPC dispatch (Step 80)
# ════════════════════════════════════════════════════════════════════════════════


def _jsonrpc_request(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": id_}


class TestJsonRpcDispatch:
    def test_message_send_dispatches(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/a2a",
            json=_jsonrpc_request(
                "message/send",
                {"skill_id": "create", "input": {}},
            ),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("jsonrpc") == "2.0"

    def test_tasks_get_dispatches(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/a2a",
            json=_jsonrpc_request("tasks/get", {"task_id": "unknown-id"}),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body or "error" in body

    def test_tasks_list_dispatches(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/a2a", json=_jsonrpc_request("tasks/list"))
        assert resp.status_code == 200

    def test_tasks_cancel_dispatches(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/a2a",
            json=_jsonrpc_request("tasks/cancel", {"task_id": "unknown-id"}),
        )
        assert resp.status_code == 200

    def test_unknown_method_returns_json_rpc_error_envelope(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/a2a", json=_jsonrpc_request("bogus/method"))
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body

    def test_bad_params_returns_json_rpc_error_envelope(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/a2a", json=_jsonrpc_request("message/send", {"not_a_skill_field": 1})
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body


# ════════════════════════════════════════════════════════════════════════════════
# Legacy paths still answer while legacy_paths=True (default)
# ════════════════════════════════════════════════════════════════════════════════


class TestLegacyPathsStillAnswer:
    def test_legacy_agent_json_still_answers_by_default(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_legacy_paths_false_returns_404_for_agent_json(self) -> None:
        adapter = _make_adapter()
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com", legacy_paths=False)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════════
# SkillAdapter construction contract — router_cls XOR source, skills= verbatim
# ════════════════════════════════════════════════════════════════════════════════


class TestSkillAdapterSourceConstruction:
    def test_both_router_cls_and_source_raises_value_error(self) -> None:
        from varco_fastapi.router.a2a.router_source import RouterSkillSource

        with pytest.raises(ValueError):
            SkillAdapter(
                _OrderRouter,
                source=RouterSkillSource(_OrderRouter),
                agent_name="X",
                agent_description="X",
                client=MagicMock(),
            )

    def test_neither_router_cls_nor_source_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            SkillAdapter(
                None,
                agent_name="X",
                agent_description="X",
                client=MagicMock(),
            )

    def test_router_class_property_is_none_for_non_router_source(self) -> None:
        from varco_fastapi.router.a2a.source import AgentMetadata

        class _CustomSource:
            def skills(self) -> list:
                return []

            def agent_metadata(self):
                return AgentMetadata(name="Custom", description="A non-router agent")

            async def invoke(self, skill_id, payload, *, ctx=None):
                return {}

        adapter = SkillAdapter(
            None,
            source=_CustomSource(),
            agent_name="X",
            agent_description="X",
            client=MagicMock(),
        )
        assert adapter.router_class is None


# ════════════════════════════════════════════════════════════════════════════════
# Custom SkillSource receives the verified caller's AuthContext (U-3)
# ════════════════════════════════════════════════════════════════════════════════


class TestCustomSkillSourceReceivesAuthContext:
    def test_invoke_receives_ctx(self) -> None:
        from varco_core.auth.base import AuthContext
        from varco_fastapi.router.a2a.source import AgentMetadata

        received: list = []

        class _AuditingSource:
            def skills(self) -> list:
                from varco_fastapi.router.a2a.source import SkillDefinition

                return [
                    SkillDefinition(
                        id="echo",
                        name="Echo",
                        description="Echoes input",
                        input_modes=("application/json",),
                        output_modes=("application/json",),
                        route=None,  # type: ignore[arg-type]
                    )
                ]

            def agent_metadata(self) -> AgentMetadata:
                return AgentMetadata(name="Auditor", description="Records ctx")

            async def invoke(self, skill_id, payload, *, ctx=None):
                received.append(ctx)
                return {"ok": True}

        adapter = SkillAdapter(
            None,
            source=_AuditingSource(),
            agent_name="Auditor",
            agent_description="Records ctx",
            client=MagicMock(),
        )
        app = FastAPI()
        adapter.mount(app, base_url="https://example.com")

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/a2a",
            json=_jsonrpc_request("message/send", {"skill_id": "echo", "input": {}}),
        )

        assert len(received) == 1
        assert received[0] is None or isinstance(received[0], AuthContext)
