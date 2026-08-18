"""
tests.test_peer_registry
==========================
Plan 009, Phase 11 (C4) — varco_fastapi.client.peer.PeerRegistry.

RED until ``varco_fastapi/client/peer.py`` lands.

Covers RD-5 (peer env vars carry references, never secrets — literal-secret
detection).
"""

from __future__ import annotations

import pytest

from tests.fixtures.routers import OrderRouter


class TestPeerRegistryFromEnv:
    def test_from_env_parses_every_suffix(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {
            "VARCO_PEER_ORDERS_URL": "http://orders.internal:8000",
            "VARCO_PEER_ORDERS_TIMEOUT": "5.0",
            "VARCO_PEER_ORDERS_VERIFY": "false",
            "VARCO_PEER_ORDERS_PROFILE": "internal",
            "VARCO_PEER_ORDERS_TOKEN_REF": "ORDERS_SVC_TOKEN",
        }
        registry = PeerRegistry.from_env(environ=environ)
        cfg = registry.config("orders")

        assert cfg.name == "orders"
        assert cfg.url == "http://orders.internal:8000"
        assert cfg.timeout == 5.0
        assert cfg.verify is False
        assert cfg.profile_name == "internal"
        assert cfg.token_ref == "ORDERS_SVC_TOKEN"

    def test_from_env_with_zero_peer_vars_is_empty_registry(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        registry = PeerRegistry.from_env(environ={})
        assert registry.names() == ()

    def test_missing_url_but_timeout_present_raises_value_error(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {"VARCO_PEER_ORDERS_TIMEOUT": "5.0"}
        with pytest.raises(ValueError, match="URL|url"):
            PeerRegistry.from_env(environ=environ)


class TestPeerRegistryLiteralSecretDetection:
    def test_token_ref_looking_like_jwt_raises_value_error(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {
            "VARCO_PEER_ORDERS_URL": "http://orders.internal:8000",
            "VARCO_PEER_ORDERS_TOKEN_REF": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        }
        with pytest.raises(ValueError, match="RD-5|literal|secret"):
            PeerRegistry.from_env(environ=environ)

    def test_token_ref_over_200_chars_raises_value_error(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {
            "VARCO_PEER_ORDERS_URL": "http://orders.internal:8000",
            "VARCO_PEER_ORDERS_TOKEN_REF": "x" * 201,
        }
        with pytest.raises(ValueError):
            PeerRegistry.from_env(environ=environ)

    def test_allow_literal_secret_escape_hatch(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {
            "VARCO_PEER_ORDERS_URL": "http://orders.internal:8000",
            "VARCO_PEER_ORDERS_TOKEN_REF": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        }
        registry = PeerRegistry.from_env(environ=environ, allow_literal_secret=True)
        assert registry.config("orders").token_ref is not None

    def test_ordinary_reference_name_is_accepted(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        environ = {
            "VARCO_PEER_ORDERS_URL": "http://orders.internal:8000",
            "VARCO_PEER_ORDERS_TOKEN_REF": "ORDERS_SVC_TOKEN",
        }
        registry = PeerRegistry.from_env(environ=environ)
        assert registry.config("orders").token_ref == "ORDERS_SVC_TOKEN"


class TestPeerRegistryClient:
    def test_client_for_unknown_peer_raises_key_error_listing_known_peers(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        registry = PeerRegistry.from_env(
            environ={"VARCO_PEER_ORDERS_URL": "http://orders.internal:8000"}
        )
        with pytest.raises(KeyError, match="orders"):
            registry.client("billing", OrderRouter)

    def test_client_reuses_shared_circuit_breaker_across_calls(self) -> None:
        from varco_fastapi.client.peer import PeerRegistry

        registry = PeerRegistry.from_env(
            environ={"VARCO_PEER_ORDERS_URL": "http://orders.internal:8000"}
        )
        client_a = registry.client("orders", OrderRouter)
        client_b = registry.client("orders", OrderRouter)
        breaker_a = getattr(client_a, "_circuit_breaker", None) or getattr(
            registry, "_breaker_for", lambda *_: None
        )("orders")
        breaker_b = getattr(client_b, "_circuit_breaker", None) or getattr(
            registry, "_breaker_for", lambda *_: None
        )("orders")
        assert breaker_a is not None
        assert breaker_a is breaker_b
