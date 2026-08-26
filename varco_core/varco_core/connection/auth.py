"""
varco_core.connection.auth
==========================
Authentication configuration models for connection settings.

Three immutable ``pydantic.BaseModel`` classes cover the most common
authentication patterns across Postgres, Redis, Kafka, and HTTP backends:

- ``BasicAuthConfig``  — username/password (HTTP Basic Auth, Redis AUTH, SASL PLAIN)
- ``OAuth2Config``     — Bearer token or client-credentials OAuth2 flow
- ``SaslConfig``       — Kafka/LDAP SASL (PLAIN, SCRAM-SHA-256, SCRAM-SHA-512, GSSAPI)

All three have a ``type`` discriminator field (``"basic"``, ``"oauth2"``,
``"sasl"``) so they can be used in a pydantic discriminated union::

    auth: Annotated[
        BasicAuthConfig | OAuth2Config | None,
        Field(discriminator="type"),
    ] = None

This allows pydantic to cleanly round-trip auth objects from dicts (e.g.
``{"type": "basic", "username": "admin", "password": "secret"}``) without
ambiguity.

DESIGN: discriminated union via literal ``type`` field
    ✅ Pydantic can deserialise from env vars / dicts without manual dispatch.
    ✅ The ``type`` field has a default value matching its literal — callers do
       NOT need to pass it: ``BasicAuthConfig(username="u", password="p")``.
    ❌ Adds a ``type`` field that never appears in driver kwargs — stripped via
       ``model_dump(exclude={"type"})`` where needed.

Thread safety:  ✅ Frozen — all models are immutable after construction.
Async safety:   ✅ No I/O; pure data objects.
"""

from __future__ import annotations

import base64
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

# ── BasicAuthConfig ───────────────────────────────────────────────────────────


class BasicAuthConfig(BaseModel):
    """
    Username/password authentication.

    Used for:
    - HTTP Basic Auth (``Authorization: Basic <base64>``)
    - Redis AUTH / ACL (``AUTH username password``)
    - asyncpg DSN credentials
    - Kafka SASL PLAIN when forwarded via ``SaslConfig``

    Attributes:
        type:     Discriminator literal (``"basic"``).  Set automatically;
                  callers do not need to pass it.
        username: Authentication username.
        password: Authentication password / secret.

    Thread safety:  ✅ Frozen — safe to share across threads.
    Async safety:   ✅ No I/O.

    Edge cases:
        - Empty ``username`` or ``password`` are accepted — some backends allow
          password-only auth.  Validate at the point of use if stricter rules apply.

    Example::

        auth = BasicAuthConfig(username="admin", password="s3cret")
        header = auth.to_header()        # "Basic YWRtaW46czNjcmV0"
        user, pw = auth.to_tuple()       # ("admin", "s3cret")
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["basic"] = "basic"
    """Discriminator field — always ``"basic"``."""

    username: str
    """Authentication username."""

    password: str
    """Authentication password."""

    def to_header(self) -> str:
        """
        Return the HTTP ``Authorization: Basic`` header value.

        Returns:
            ``"Basic <base64(username:password)>"`` — ready to set as a header value.

        Example::

            headers["Authorization"] = auth.to_header()
        """
        encoded = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Basic {encoded}"

    def to_tuple(self) -> tuple[str, str]:
        """
        Return ``(username, password)`` as a tuple.

        Useful for libraries that accept a credentials tuple (e.g. ``httpx.BasicAuth``,
        ``requests.auth.HTTPBasicAuth``).

        Returns:
            ``(username, password)``
        """
        return (self.username, self.password)


# ── OAuth2Config ──────────────────────────────────────────────────────────────


class OAuth2Config(BaseModel):
    """
    OAuth2 / Bearer token authentication configuration.

    Two modes:

    1. **Static token** — set ``token``; use ``to_bearer_header()`` directly.
    2. **Client credentials** — set ``token_url`` + ``client_id`` + ``client_secret``;
       exchange credentials for a token at connection time.

    At least one of these modes must be configured — construction raises
    ``ValidationError`` if neither ``token`` nor the client credentials trio
    are present.

    Attributes:
        type:           Discriminator literal (``"oauth2"``).
        token:          Static Bearer token.  When set, ``to_bearer_header()``
                        returns it directly.
        token_url:      OAuth2 token endpoint URL for client credentials flow.
        client_id:      OAuth2 client ID.
        client_secret:  OAuth2 client secret.
        scope:          Optional space-separated OAuth2 scope string.

    Thread safety:  ✅ Frozen — safe to share across threads.
    Async safety:   ✅ No I/O; token refresh is the caller's responsibility.

    Edge cases:
        - ``token_url`` without ``client_id``/``client_secret`` → ValidationError.
        - Both ``token`` and client credentials set → ``token`` takes precedence
          in ``to_bearer_header()``; client credentials can be used for token refresh.
        - Token refresh / expiry are NOT managed by this class — it is a pure
          configuration object.  Implement refresh logic in HTTP middleware.

    Example::

        # Static token (simplest case)
        auth = OAuth2Config(token="eyJhbGciOiJSUzI1NiJ9...")
        headers["Authorization"] = auth.to_bearer_header()

        # Client credentials (exchange at connect time)
        auth = OAuth2Config(
            token_url="https://auth.example.com/token",
            client_id="my-service",
            client_secret="client-secret",
            scope="read:orders",
        )
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["oauth2"] = "oauth2"
    """Discriminator field — always ``"oauth2"``."""

    token: str | None = None
    """Static Bearer token.  Takes precedence over client credentials in ``to_bearer_header()``."""

    token_url: str | None = None
    """OAuth2 token endpoint for client credentials exchange."""

    client_id: str | None = None
    """OAuth2 client ID."""

    client_secret: str | None = None
    """OAuth2 client secret."""

    scope: str | None = None
    """Space-separated OAuth2 scope string (optional)."""

    @model_validator(mode="after")
    def _validate_credentials(self) -> Self:
        """
        Ensure at least one authentication mode is configured.

        Raises:
            ValueError: If neither ``token`` nor the complete client-credentials
                        triple (``token_url``, ``client_id``, ``client_secret``)
                        are provided.
            ValueError: If ``token_url`` is given without ``client_id`` or
                        ``client_secret``.
        """
        has_token = self.token is not None
        has_client_creds = all(
            v is not None for v in (self.token_url, self.client_id, self.client_secret)
        )
        partial_client_creds = (
            any(
                v is not None
                for v in (self.token_url, self.client_id, self.client_secret)
            )
            and not has_client_creds
        )

        if partial_client_creds:
            missing = [
                name
                for name, val in (
                    ("token_url", self.token_url),
                    ("client_id", self.client_id),
                    ("client_secret", self.client_secret),
                )
                if val is None
            ]
            raise ValueError(
                f"OAuth2Config: partial client credentials — missing: {missing}. "
                "Provide all three (token_url, client_id, client_secret) or none."
            )

        if not has_token and not has_client_creds:
            raise ValueError(
                "OAuth2Config: must provide either 'token' (static Bearer token) "
                "or the client credentials triple "
                "('token_url', 'client_id', 'client_secret')."
            )
        return self

    def to_bearer_header(self) -> str | None:
        """
        Return the HTTP ``Authorization: Bearer`` header value.

        Returns:
            ``"Bearer <token>"`` when ``token`` is set; ``None`` otherwise
            (client-credentials mode — token must be fetched first).

        Example::

            header = auth.to_bearer_header()
            if header:
                headers["Authorization"] = header
        """
        if self.token is not None:
            return f"Bearer {self.token}"
        return None


# ── SaslConfig ────────────────────────────────────────────────────────────────


class SaslConfig(BaseModel):
    """
    SASL authentication configuration — used primarily with Kafka (aiokafka),
    but also applicable to LDAP and other SASL-enabled protocols.

    Attributes:
        type:      Discriminator literal (``"sasl"``).
        mechanism: SASL mechanism name.  Common values:
                   ``"PLAIN"``, ``"SCRAM-SHA-256"``, ``"SCRAM-SHA-512"``,
                   ``"GSSAPI"`` (Kerberos).
        username:  SASL username (required for PLAIN/SCRAM).
        password:  SASL password (required for PLAIN/SCRAM).

    Thread safety:  ✅ Frozen — safe to share across threads.
    Async safety:   ✅ No I/O.

    Edge cases:
        - ``GSSAPI`` mechanism does not use ``username``/``password`` —
          they will be ignored by the Kerberos layer.
        - Case-sensitivity: aiokafka expects uppercase mechanism names;
          ``to_aiokafka_kwargs()`` returns the value as-is.

    Example::

        # PLAIN over SSL
        sasl = SaslConfig(mechanism="PLAIN", username="alice", password="secret")
        kwargs = sasl.to_aiokafka_kwargs()
        # {"sasl_mechanism": "PLAIN", "sasl_plain_username": "alice",
        #  "sasl_plain_password": "secret"}

        # SCRAM-SHA-256
        sasl = SaslConfig(mechanism="SCRAM-SHA-256", username="bob", password="pw")
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["sasl"] = "sasl"
    """Discriminator field — always ``"sasl"``."""

    mechanism: str = "PLAIN"
    """
    SASL mechanism.  Common: ``"PLAIN"``, ``"SCRAM-SHA-256"``,
    ``"SCRAM-SHA-512"``, ``"GSSAPI"``.
    """

    username: str | None = None
    """SASL username (PLAIN / SCRAM)."""

    password: str | None = None
    """SASL password (PLAIN / SCRAM)."""

    def to_aiokafka_kwargs(self) -> dict[str, str | None]:
        """
        Return kwargs for ``AIOKafkaProducer``/``AIOKafkaConsumer``.

        Maps fields to aiokafka's naming convention:
        - ``sasl_mechanism``
        - ``sasl_plain_username``  (used for both PLAIN and SCRAM in aiokafka)
        - ``sasl_plain_password``

        Returns:
            Dict of aiokafka-compatible SASL kwargs.

        Edge cases:
            - ``None`` values are included in the dict; aiokafka treats them as
              absent.  Callers can filter with ``{k: v for k, v in d.items() if v}``.
            - For ``GSSAPI``, only ``sasl_mechanism`` is meaningful; username/password
              are ``None`` and may be filtered out.
        """
        return {
            "sasl_mechanism": self.mechanism,
            "sasl_plain_username": self.username,
            "sasl_plain_password": self.password,
        }


__all__ = [
    "BasicAuthConfig",
    "OAuth2Config",
    "SaslConfig",
]
