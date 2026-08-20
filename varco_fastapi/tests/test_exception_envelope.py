"""
Red-mode tests for Plan 011 Phase 1, step 18 — RD-1's I1 proof at the HTTP
layer (half 2).

Plan line (step 18): "default body for a built-in vs. an out-of-tree
exception; the kill switch end-to-end through the app; the problem+json
shape and media type when enabled; the 404 and 500 handlers produce the
same envelope shape as a raised ServiceException."
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from varco_core.exception.service import ServiceException, ServiceNotFoundError
from varco_fastapi.exceptions import add_exception_handlers


class SomeEntity:
    pass


class OutOfTreeException(ServiceException):
    def __init__(self) -> None:
        super().__init__("out of tree failure")


def make_app() -> FastAPI:
    app = FastAPI()
    add_exception_handlers(app)

    @app.get("/not-found")
    async def not_found():
        raise ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)

    @app.get("/out-of-tree")
    async def out_of_tree():
        raise OutOfTreeException()

    return app


async def test_builtin_exception_body_over_http_contains_message_key() -> None:
    app = make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/not-found")
    body = response.json()
    assert body.get("message_key") == "varco.error.not_found"


async def test_out_of_tree_exception_body_over_http_has_no_message_key() -> None:
    app = make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/out-of-tree")
    body = response.json()
    assert "message_key" not in body


async def test_kill_switch_env_var_restores_pre_plan_body(monkeypatch) -> None:
    monkeypatch.setenv("VARCO_ERROR_INCLUDE_MESSAGE_KEY", "false")
    monkeypatch.setenv("VARCO_ERROR_INCLUDE_PARAMS", "false")
    app = make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/not-found")
    body = response.json()
    assert "message_key" not in body
    assert "params" not in body


async def test_problem_details_mode_emits_problem_json_media_type(monkeypatch) -> None:
    monkeypatch.setenv("VARCO_ERROR_PROBLEM_DETAILS", "true")
    app = make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/not-found")
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert "title" in body
    assert "type" in body
