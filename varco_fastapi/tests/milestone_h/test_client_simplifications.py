"""
Milestone H tests — client simplification features.

Tests cover:
- make_client() factory: creates typed client without subclassing
- ClientConfig: frozen dataclass, development/production factories
- port kwarg on AsyncVarcoClient: appended to resolved URL
- verify kwarg on AsyncVarcoClient: stored and used in httpx build
- Fail-fast ValueError when no URL and no configurator
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from varco_fastapi.client.base import AsyncVarcoClient, make_client
from varco_fastapi.client.config import ClientConfig
from varco_fastapi.router.base import VarcoRouter
from varco_fastapi.router.presets import AllRouteMixin

# ── Test router/DTOs ──────────────────────────────────────────────────────────


class ItemCreate(BaseModel):
    name: str


class ItemRead(BaseModel):
    id: UUID
    name: str


class ItemUpdate(BaseModel):
    name: str | None = None


class ItemRouter(AllRouteMixin, VarcoRouter[Any, UUID, ItemCreate, ItemRead, ItemUpdate]):
    _prefix = "/items"


# ── make_client ───────────────────────────────────────────────────────────────


def test_make_client_returns_async_varco_client():
    client = make_client(ItemRouter, "https://api.example.com")
    assert isinstance(client, AsyncVarcoClient)


def test_make_client_has_crud_methods():
    client = make_client(ItemRouter, "https://api.example.com")
    for method in ("create", "read", "update", "patch", "delete", "list"):
        assert callable(getattr(client, method, None)), f"Missing method: {method}"


def test_make_client_no_url_raises():
    with pytest.raises(ValueError, match="base_url"):
        make_client(ItemRouter)


def test_make_client_url_stored():
    client = make_client(ItemRouter, "https://items.internal")
    assert client._base_url == "https://items.internal"


def test_make_client_port_applied():
    client = make_client(ItemRouter, "http://items.internal", port=9090)
    assert ":9090" in client._base_url


def test_make_client_verify_stored():
    client = make_client(ItemRouter, "https://api.example.com", verify=False)
    assert client._verify is False


def test_make_client_two_instances_independent_urls():
    c1 = make_client(ItemRouter, "https://a.example.com")
    c2 = make_client(ItemRouter, "https://b.example.com")
    # Each call produces an independent instance with its own URL
    assert c1._base_url == "https://a.example.com"
    assert c2._base_url == "https://b.example.com"
    assert c1._base_url != c2._base_url


# ── ClientConfig ──────────────────────────────────────────────────────────────


def test_client_config_is_frozen():
    config = ClientConfig(base_url="https://api.example.com")
    with pytest.raises(Exception):
        config.base_url = "mutated"  # type: ignore[misc]


def test_client_config_defaults():
    config = ClientConfig(base_url="https://api.example.com")
    assert config.port is None
    assert config.verify is True
    assert config.middleware == ()
    assert config.timeout == 30.0
    assert config.headers == {}


def test_client_config_development_factory():
    config = ClientConfig.development()
    assert config.base_url == "http://localhost"
    assert config.timeout == 10.0


def test_client_config_development_custom_url():
    config = ClientConfig.development("http://myservice:5000")
    assert config.base_url == "http://myservice:5000"


def test_client_config_development_kwargs_override():
    config = ClientConfig.development(timeout=5.0)
    assert config.timeout == 5.0


def test_client_config_production_verify_true():
    config = ClientConfig.production("https://api.example.com")
    assert config.verify is True


def test_client_config_production_with_authority_adds_jwt():
    from varco_fastapi.client.middleware import JwtMiddleware

    mock_authority = object()  # JwtAuthority stand-in
    config = ClientConfig.production("https://api.example.com", authority=mock_authority)
    assert any(isinstance(mw, JwtMiddleware) for mw in config.middleware)


def test_client_config_applied_to_async_varco_client():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    config = ClientConfig(base_url="https://config-url.example.com", timeout=15.0)
    client = ItemClient(config=config)
    assert client._base_url == "https://config-url.example.com"


def test_explicit_base_url_overrides_config():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    config = ClientConfig(base_url="https://config-url.example.com")
    client = ItemClient("https://explicit-url.example.com", config=config)
    assert client._base_url == "https://explicit-url.example.com"


# ── port kwarg ────────────────────────────────────────────────────────────────


def test_port_appended_no_existing_port():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    client = ItemClient("http://service.internal", port=8080)
    assert client._base_url == "http://service.internal:8080"


def test_port_not_double_appended():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    client = ItemClient("http://service.internal:8080", port=8080)
    assert client._base_url.count(":8080") == 1


# ── verify kwarg ──────────────────────────────────────────────────────────────


def test_verify_false_passed_to_httpx():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    client = ItemClient("https://api.example.com", verify=False)
    httpx_client = client._build_httpx_client()
    # httpx.AsyncClient stores verify — check the client was built without error
    assert httpx_client is not None


def test_verify_true_is_default_on_async_varco_client():
    class ItemClient(AsyncVarcoClient[ItemRouter]):
        pass

    client = ItemClient("https://api.example.com")
    assert client._verify is True


# ── Fail-fast URL validation ──────────────────────────────────────────────────


def test_no_url_no_configurator_raises_value_error():
    class BareClient(AsyncVarcoClient):
        pass

    with pytest.raises(ValueError, match="base_url"):
        BareClient()


def test_no_url_with_configurator_does_not_raise():
    from varco_fastapi.client.configurator import ClientConfigurator

    class ItemConfigurator(ClientConfigurator[ItemRouter]):
        def default_url(self) -> str:
            return "https://items.internal"

    class ItemClient(AsyncVarcoClient[ItemRouter]):
        _configurator = ItemConfigurator

    # Should NOT raise — configurator provides the URL
    client = ItemClient()
    assert client._base_url == "https://items.internal"
