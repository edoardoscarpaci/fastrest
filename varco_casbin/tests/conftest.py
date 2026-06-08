"""
Shared fixtures for varco_casbin tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_casbin.router import build_policy_router
from varco_core.auth import AuthContext
from varco_core.exception.service import ServiceAuthorizationError
from varco_fastapi.auth import AbstractServerAuth


class StaticAuth(AbstractServerAuth):
    """Test auth strategy that always yields a fixed AuthContext."""

    def __init__(self, ctx: AuthContext) -> None:
        self._ctx = ctx

    async def __call__(self, request: Request) -> AuthContext:
        return self._ctx


def make_app(engine: CasbinPolicyEngine, ctx: AuthContext) -> FastAPI:
    """
    Build a FastAPI app mounting the policy router with a fixed identity.

    Registers the ServiceAuthorizationError → 403 handler that a real varco app
    installs, so guard denials surface as HTTP 403 in tests.
    """
    app = FastAPI()
    app.include_router(build_policy_router(engine, server_auth=StaticAuth(ctx)))

    @app.exception_handler(ServiceAuthorizationError)
    async def _forbidden(
        request: Request, exc: ServiceAuthorizationError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    return app


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[CasbinPolicyEngine]:
    """A started in-memory RBAC engine, stopped after the test."""
    eng = CasbinPolicyEngine(CasbinSettings(model_preset="rbac"))
    await eng.start()
    try:
        yield eng
    finally:
        await eng.stop()


def client_for(app: FastAPI) -> AsyncClient:
    """An httpx AsyncClient bound to the app via ASGI transport."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
