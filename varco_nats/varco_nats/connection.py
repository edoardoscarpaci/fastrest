"""
varco_nats.connection
=====================
``NatsConnectionSettings`` — structured, env-var loadable configuration for a
NATS connection via nats-py.

Environment variables (prefix ``NATS_``)
----------------------------------------
::

    NATS_SERVERS=nats://nats1:4222,nats://nats2:4222

    # TLS (optional)
    NATS_SSL__CA_CERT=/etc/ssl/nats-ca.pem
    NATS_SSL__CLIENT_CERT=/etc/ssl/client.crt
    NATS_SSL__CLIENT_KEY=/etc/ssl/client.key

    # User/password authentication (optional)
    NATS_AUTH__TYPE=basic
    NATS_AUTH__USERNAME=alice
    NATS_AUTH__PASSWORD=secret

    # Or a static token (optional, alternative to user/password)
    NATS_TOKEN=s3cr3t-token

Construction patterns::

    # From env (production)
    conn = NatsConnectionSettings.from_env()

    # Explicit
    conn = NatsConnectionSettings(servers="nats://nats1:4222,nats://nats2:4222")

    # Build nats.connect() kwargs
    nc = await nats.connect(**conn.to_nats_kwargs())

DESIGN: Separate from NatsEventBusSettings
    ✅ NatsConnectionSettings handles the connection/security layer.
    ✅ NatsEventBusSettings remains focused on stream/subject/consumer routing.
    ✅ Both coexist; they serve different concerns.
    ❌ Two NATS config classes exist — users choose the appropriate one.

DESIGN: BasicAuthConfig only (no SaslConfig / OAuth2Config)
    ✅ NATS authentication is user/password, token, nkey, or JWT/creds — it
       has no SASL concept, so ``SaslConfig`` would be misleading here.
    ✅ ``BasicAuthConfig`` maps cleanly to NATS user/password auth.
    ✅ The ``token`` field covers the second common case (static token auth).
    ❌ nkey / JWT credential-file auth is not modelled — pass it through
       ``NatsEventBusSettings.connect_kwargs`` if needed (e.g.
       ``user_credentials="/etc/nats/app.creds"``).

Thread safety:  ✅ frozen=True — immutable after construction.
Async safety:   ✅ No I/O; all methods are synchronous.

📚 Docs
- 🔍 https://nats-io.github.io/nats.py/ — nats-py connect() options
- 🔍 https://docs.nats.io/using-nats/developer/connecting/tls — NATS TLS
"""

from __future__ import annotations

from typing import Any

from pydantic_settings import SettingsConfigDict

from varco_core.connection.auth import BasicAuthConfig
from varco_core.connection.base import ConnectionSettings


# ── NatsConnectionSettings ────────────────────────────────────────────────────


class NatsConnectionSettings(ConnectionSettings):
    """
    Immutable NATS connection configuration.

    Reads from environment variables with the ``NATS_`` prefix.  Nested SSL
    and auth config use the ``__`` delimiter::

        NATS_SERVERS=nats://broker:4222
        NATS_SSL__CA_CERT=/etc/ssl/ca.pem
        NATS_AUTH__TYPE=basic
        NATS_AUTH__USERNAME=alice
        NATS_AUTH__PASSWORD=secret

    Attributes:
        host:    Single broker hostname (used to synthesise ``servers`` when the
                 latter is left at its default).  Env: ``NATS_HOST``.
        port:    Single broker port.  Env: ``NATS_PORT``.
        servers: Comma-separated list of ``nats://host:port`` URLs.  Takes
                 precedence over ``host``/``port``.  Env: ``NATS_SERVERS``.
        token:   Optional static authentication token.  Mutually exclusive with
                 ``auth`` — if both are set, ``auth`` (user/password) wins.
                 Env: ``NATS_TOKEN``.
        ssl:     Optional ``SSLConfig`` for TLS.  Populated from ``NATS_SSL__*``.
        auth:    Optional ``BasicAuthConfig`` (user/password).  Populated from
                 ``NATS_AUTH__*``.

    Thread safety:  ✅ frozen=True — immutable.
    Async safety:   ✅ No I/O.

    Edge cases:
        - ``servers`` defaults to ``"nats://localhost:4222"``.  When only
          ``host``/``port`` are changed from defaults, a validator is NOT used
          here — instead ``to_servers_list()`` falls back to ``host``/``port``
          when ``servers`` is still the default.  This keeps the model simple.
        - When both ``token`` and ``auth`` are provided, ``to_nats_kwargs()``
          prefers ``auth`` (user/password) and ignores ``token``.
    """

    model_config = SettingsConfigDict(
        env_prefix="NATS_",
        env_nested_delimiter="__",
        frozen=True,
    )

    # NATS default client port — overrides the ConnectionSettings default of 0.
    port: int = 4222
    """Single broker port.  Used to synthesise ``servers``.  Env: ``NATS_PORT``."""

    servers: str = "nats://localhost:4222"
    """
    Comma-separated ``nats://host:port`` URLs.  Overrides ``host``/``port``.
    Env: ``NATS_SERVERS``.
    """

    token: str | None = None
    """Optional static auth token.  Env: ``NATS_TOKEN``.  Ignored when ``auth`` is set."""

    auth: BasicAuthConfig | None = None
    """
    Optional user/password authentication config.
    Populated from ``NATS_AUTH__TYPE=basic``, ``NATS_AUTH__USERNAME``,
    ``NATS_AUTH__PASSWORD``.
    """

    # ── Conversion methods ────────────────────────────────────────────────────

    def to_servers_list(self) -> list[str]:
        """
        Return the NATS server URLs as a list.

        ``servers`` is split on commas.  When ``servers`` is still the default
        and ``host``/``port`` were customised, a single URL is synthesised from
        ``host``/``port`` instead.

        Returns:
            A non-empty list of ``nats://host:port`` URL strings.

        Edge cases:
            - Whitespace around each comma-separated entry is stripped.
            - An entry without a scheme is returned verbatim — nats-py accepts
              bare ``host:port`` and prepends ``nats://`` itself.
        """
        _default_servers = "nats://localhost:4222"
        _default_host = "localhost"

        # Synthesise from host/port only when servers was never customised.
        # This mirrors KafkaConnectionSettings' single-broker convenience.
        if self.servers == _default_servers and self.host != _default_host:
            return [f"nats://{self.host}:{self.port}"]

        return [part.strip() for part in self.servers.split(",") if part.strip()]

    def to_nats_kwargs(self) -> dict[str, Any]:
        """
        Build kwargs for ``nats.connect()``.

        The returned dict is ready to unpack into the nats-py connect call::

            nc = await nats.connect(**conn.to_nats_kwargs())

        Auth precedence:
            1. ``auth`` (user/password) — wins when set.
            2. ``token`` — used only when ``auth`` is ``None``.
            3. Neither — anonymous connection.

        Returns:
            Dict of nats-py-compatible kwargs (``servers``, optionally
            ``user``/``password`` or ``token``, optionally ``tls``).

        Edge cases:
            - ``tls`` is set to a built ``ssl.SSLContext`` only when ``ssl`` is
              configured — otherwise the key is omitted (plaintext connection).
            - ``token`` and ``auth`` together → ``auth`` wins (see precedence).
        """
        kwargs: dict[str, Any] = {"servers": self.to_servers_list()}

        # Auth: user/password takes precedence over a static token.
        if self.auth is not None:
            kwargs["user"] = self.auth.username
            kwargs["password"] = self.auth.password
        elif self.token is not None:
            kwargs["token"] = self.token

        # TLS — only included when explicitly configured.
        if self.ssl is not None:
            kwargs["tls"] = self.ssl.build_ssl_context()

        return kwargs


__all__ = ["NatsConnectionSettings"]
