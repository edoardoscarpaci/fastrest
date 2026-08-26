"""
Milestone H tests — GenericClient.

Tests cover:
- Constructor: base_url required, port appended, verify stored
- HTTP verb methods: get/post/put/patch/delete dispatch to _request()
- headers kwarg: prepended as HeadersMiddleware
- Middleware pipeline: custom middleware is invoked
- Context manager passthrough
- __repr__
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from varco_fastapi.client.generic import GenericClient
from varco_fastapi.client.middleware import AbstractClientMiddleware, PreparedRequest

# ── Fixtures ──────────────────────────────────────────────────────────────────


class ItemRead(BaseModel):
    id: int
    name: str


def _mock_response(status: int = 200, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.raise_for_status = MagicMock()
    return r


def _patched_client(client: GenericClient, response: MagicMock) -> GenericClient:
    """Inject a mock httpx client so no real network call is made."""
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=response)
    return client


# ── Construction ──────────────────────────────────────────────────────────────


def test_generic_client_instantiates():
    client = GenericClient("https://api.example.com")
    assert client._base_url == "https://api.example.com"


def test_port_appended_to_base_url():
    client = GenericClient("http://localhost", port=8080)
    assert client._base_url == "http://localhost:8080"


def test_port_not_appended_when_already_in_url():
    # URL already has a port — should not double-append
    client = GenericClient("http://localhost:9000", port=9000)
    assert ":9000" in client._base_url
    assert client._base_url.count(":9000") == 1


def test_verify_false_stored():
    client = GenericClient("https://api.example.com", verify=False)
    assert client._verify is False


def test_verify_ca_bundle_path_stored():
    client = GenericClient("https://api.example.com", verify="/etc/certs/ca.pem")
    assert client._verify == "/etc/certs/ca.pem"


def test_verify_true_is_default():
    client = GenericClient("https://api.example.com")
    assert client._verify is True


def test_missing_base_url_raises():
    with pytest.raises((ValueError, TypeError)):
        # Empty string → fail-fast from AsyncVarcoClient
        GenericClient("")


# ── HTTP verb methods ─────────────────────────────────────────────────────────


async def test_get_dispatches_correctly():
    client = GenericClient("https://api.example.com")
    _patched_client(client, _mock_response(200, {"id": 1, "name": "thing"}))

    with patch.object(client, "_request", new=AsyncMock(return_value={"id": 1})) as mock_req:
        await client.get("/items")
        mock_req.assert_called_once()
        call = mock_req.call_args
        assert call.args[0] == "GET"
        assert call.args[1] == "/items"


async def test_post_uses_201_default():
    client = GenericClient("https://api.example.com")
    with patch.object(client, "_request", new=AsyncMock(return_value={})) as mock_req:
        await client.post("/items", body={"name": "x"})
        mock_req.assert_called_once()
        assert mock_req.call_args.kwargs["expected_status"] == 201


async def test_put_dispatches_correctly():
    client = GenericClient("https://api.example.com")
    with patch.object(client, "_request", new=AsyncMock(return_value={})) as mock_req:
        await client.put("/items/1", body={"name": "y"})
        args = mock_req.call_args.args
        assert args[0] == "PUT"


async def test_patch_dispatches_correctly():
    client = GenericClient("https://api.example.com")
    with patch.object(client, "_request", new=AsyncMock(return_value={})) as mock_req:
        await client.patch("/items/1", body={"name": "z"})
        assert mock_req.call_args.args[0] == "PATCH"


async def test_delete_uses_204_default():
    client = GenericClient("https://api.example.com")
    with patch.object(client, "_request", new=AsyncMock(return_value=None)) as mock_req:
        await client.delete("/items/1")
        assert mock_req.call_args.kwargs["expected_status"] == 204


async def test_get_with_response_model():
    client = GenericClient("https://api.example.com")
    with patch.object(
        client, "_request", new=AsyncMock(return_value=ItemRead(id=1, name="hi"))
    ) as mock_req:
        result = await client.get("/items/1", response_model=ItemRead)
        assert mock_req.call_args.kwargs["response_model"] is ItemRead
        assert result.name == "hi"


async def test_get_passes_query_params():
    client = GenericClient("https://api.example.com")
    with patch.object(client, "_request", new=AsyncMock(return_value={})) as mock_req:
        await client.get("/items", params={"active": "true"})
        assert mock_req.call_args.kwargs["query_params"] == {"active": "true"}


# ── headers kwarg wires HeadersMiddleware ─────────────────────────────────────


def test_static_headers_become_middleware():
    from varco_fastapi.client.middleware import HeadersMiddleware

    client = GenericClient("https://api.example.com", headers={"X-Tenant": "acme"})
    profile = client._resolved_profile
    assert any(isinstance(mw, HeadersMiddleware) for mw in profile.middleware)


def test_empty_headers_no_extra_middleware():
    client_no_h = GenericClient("https://api.example.com")
    client_with_h = GenericClient("https://api.example.com", headers={"X-A": "B"})
    assert len(client_with_h._resolved_profile.middleware) > len(
        client_no_h._resolved_profile.middleware
    )


# ── Custom middleware is invoked ──────────────────────────────────────────────


async def test_middleware_is_called():
    called: list[bool] = []

    class SpyMiddleware(AbstractClientMiddleware):
        async def __call__(self, request: PreparedRequest, next: Any) -> Any:
            called.append(True)
            return await next(request)

    client = GenericClient(
        "https://api.example.com",
        middleware=(SpyMiddleware(),),
    )
    mock_resp = _mock_response(200, {})
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=mock_resp)

    await client.get("/ping")
    assert called == [True]


# ── Context manager ───────────────────────────────────────────────────────────


async def test_context_manager_enters_and_exits():
    async with GenericClient("https://api.example.com") as client:
        assert isinstance(client, GenericClient)


# ── __repr__ ──────────────────────────────────────────────────────────────────


def test_repr_contains_url():
    client = GenericClient("https://api.example.com")
    r = repr(client)
    assert "api.example.com" in r


def test_repr_contains_verify():
    client = GenericClient("https://api.example.com", verify=False)
    r = repr(client)
    assert "False" in r
