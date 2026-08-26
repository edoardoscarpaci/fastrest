"""
Unit tests for varco_nats.connection
=====================================
``NatsConnectionSettings`` — structured connection/security config, no broker.
"""

from __future__ import annotations

import pytest
from varco_core.connection.auth import BasicAuthConfig

from varco_nats import NatsConnectionSettings

# ── Defaults ──────────────────────────────────────────────────────────────────


class TestNatsConnectionDefaults:
    def test_defaults(self) -> None:
        conn = NatsConnectionSettings()
        assert conn.servers == "nats://localhost:4222"
        assert conn.port == 4222
        assert conn.host == "localhost"
        assert conn.token is None
        assert conn.auth is None
        assert conn.ssl is None

    def test_frozen(self) -> None:
        conn = NatsConnectionSettings()
        with pytest.raises(Exception):
            conn.servers = "nats://other:4222"  # type: ignore[misc]


# ── to_servers_list ───────────────────────────────────────────────────────────


class TestToServersList:
    def test_splits_comma_separated(self) -> None:
        conn = NatsConnectionSettings(servers="nats://a:4222,nats://b:4222")
        assert conn.to_servers_list() == ["nats://a:4222", "nats://b:4222"]

    def test_synthesises_from_host_when_servers_default(self) -> None:
        # When only host/port are customised, a single URL is synthesised.
        conn = NatsConnectionSettings(host="my-nats", port=4333)
        assert conn.to_servers_list() == ["nats://my-nats:4333"]

    def test_explicit_servers_wins_over_host(self) -> None:
        # An explicit servers value takes precedence over host/port.
        conn = NatsConnectionSettings(host="my-nats", servers="nats://explicit:4222")
        assert conn.to_servers_list() == ["nats://explicit:4222"]


# ── to_nats_kwargs ────────────────────────────────────────────────────────────


class TestToNatsKwargs:
    def test_anonymous_connection(self) -> None:
        conn = NatsConnectionSettings(servers="nats://a:4222")
        kwargs = conn.to_nats_kwargs()
        assert kwargs == {"servers": ["nats://a:4222"]}

    def test_basic_auth_maps_to_user_password(self) -> None:
        conn = NatsConnectionSettings(
            servers="nats://a:4222",
            auth=BasicAuthConfig(username="alice", password="secret"),
        )
        kwargs = conn.to_nats_kwargs()
        assert kwargs["user"] == "alice"
        assert kwargs["password"] == "secret"

    def test_token_used_when_no_auth(self) -> None:
        conn = NatsConnectionSettings(servers="nats://a:4222", token="t0ken")
        kwargs = conn.to_nats_kwargs()
        assert kwargs["token"] == "t0ken"

    def test_auth_wins_over_token(self) -> None:
        # When both are set, user/password auth takes precedence over token.
        conn = NatsConnectionSettings(
            servers="nats://a:4222",
            token="t0ken",
            auth=BasicAuthConfig(username="alice", password="secret"),
        )
        kwargs = conn.to_nats_kwargs()
        assert kwargs["user"] == "alice"
        assert "token" not in kwargs

    def test_tls_omitted_without_ssl(self) -> None:
        conn = NatsConnectionSettings(servers="nats://a:4222")
        assert "tls" not in conn.to_nats_kwargs()
