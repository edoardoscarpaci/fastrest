"""
varco_core.tls.store
=====================

``TrustStore`` — the unified TLS trust model (Plan 026 / T3a, §D-T3-model), a strict
superset of the two models that predate it: ``varco_core.connection.ssl.SSLConfig`` (a
pydantic settings fragment) and ``varco_fastapi.auth.trust_store.TrustStore`` (a runtime
capability object). This is the runtime-capability half — ``SSLConfig`` stays a settings
fragment and converts losslessly via ``SSLConfig.to_trust_store()`` / ``TrustStore.to_ssl_config()``
(§D-T3-bridge).

DESIGN: frozen dataclass, not a pydantic ``BaseModel`` (§D-T3-model)
    ✅ Matches the type it is replacing (``varco_fastapi.auth.trust_store.TrustStore``) so the
       3.0-semantics deprecation subclass (§D-T3-oq1) is a plain ``@dataclass(frozen=True)``
       subclass with no metaclass conflict a pydantic base would introduce.
    ✅ ``bytes`` ``ca_cert`` is awkward in a pydantic model and trivial in a dataclass;
       ``TrustStore`` is never populated from ``env_nested_delimiter``, which is the entire
       reason ``SSLConfig`` is a ``BaseModel``.
    ✅ Cheaper to construct and to import.
    ❌ No pydantic validators — ``__post_init__`` does the work by hand. Accepted: there are
       exactly two rules and they already exist as hand-written code in both predecessor
       implementations.
    ❌ Two different shapes now model TLS config. Deliberate: ``SSLConfig`` is a *settings
       fragment*; ``TrustStore`` is a *runtime capability object*. Lossless conversion exists
       in both directions.

Validation is **eager**, at construction (``__post_init__``) — stricter than 3.0's
``varco_fastapi.auth.TrustStore``, which deferred the mTLS-pairing check to
``build_ssl_context()``. The 3.0-semantics deprecation subclass (§D-T3-oq1) does NOT inherit
this eager check — it overrides ``__post_init__`` entirely to keep its behaviour frozen.

Thread safety:  ✅ Frozen — safe to share across threads.
Async safety:   ✅ ``build_ssl_context()`` is synchronous (the ``ssl`` module is sync);
                   ``ReloadingTrustStore`` (``varco_core.tls.reload``) is what makes this spec
                   reloadable in an async context.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from varco_core.tls.discovery import CERT_FILE_PATTERNS, iter_cert_files

if TYPE_CHECKING:
    from varco_core.connection.ssl import SSLConfig

# The additive env vars this type reads on top of the existing VARCO_* names (§D-T3-env).
_SSL_CERT_FILE_ENV = "SSL_CERT_FILE"
_SSL_CERT_DIR_ENV = "SSL_CERT_DIR"


@dataclass(frozen=True)
class TrustStore:
    """
    Unified TLS trust configuration — the superset of ``SSLConfig`` and 3.0's
    ``varco_fastapi.auth.TrustStore`` (§D-T3-model).

    Attributes:
        ca_cert: A single CA — ``Path`` to a PEM file, or raw PEM ``bytes`` (loaded via
            ``cadata``). Merged with system CAs (if ``include_system_cas=True``) and any
            ``ca_folders`` entries.
        ca_folders: One ``Path``, a sequence of ``Path``s, or ``None``. Normalised to a
            tuple (or ``None`` if empty) in ``__post_init__`` — the locked "``ca_folder``
            multiplicity" widening (``BACKLOG.md:31``), new on this type only.
        cert_patterns: Patterns used to enumerate ``ca_folders`` via
            ``varco_core.tls.discovery.iter_cert_files``. Defaults to the wider
            ``CERT_FILE_PATTERNS`` set — safe here because this type has no existing
            deployments to widen (unlike ``SSLConfig``/the legacy shim, which keep their own
            narrower 3.0 defaults).
        recursive: Whether ``ca_folders`` are scanned recursively. Defaults to ``True`` —
            the locked "Cert search — recursive by default, on the new type only"
            (``BACKLOG.md:30``).
        client_cert: mTLS client certificate path.
        client_key: mTLS client private key path. Must be set together with ``client_cert``
            or neither — checked eagerly at construction (see class docstring).
        include_system_cas: Whether to include the OS CA bundle.
        verify: Whether to verify the server's certificate chain at all.
            ``check_hostname=True`` requires ``verify=True``.
        check_hostname: Whether to enforce hostname verification.

    Raises:
        ValueError: ``check_hostname=True`` with ``verify=False`` (the ``ssl`` module's own
            requirement, raised early); or exactly one of ``client_cert``/``client_key`` set.

    Edge cases:
        - ``ca_folders=[]`` (empty sequence) → normalised to ``None``; no folder loading, no
          warning.
        - The same certificate present in two folders → both loaded; ``load_verify_locations``
          is idempotent for an identical cert, OpenSSL de-duplicates.
        - ``include_system_cas=False`` with no CA at all → an empty trust store; every TLS
          connection fails. Intentional for strict-pinning scenarios.
        - ``verify=False`` wins over ``include_system_cas=True`` — the base context is
          ``CERT_NONE`` regardless of ``include_system_cas``.

    Example::

        # Standard — system CAs, full verification
        store = TrustStore()

        # Private CA + mTLS, recursive folder scan
        store = TrustStore(
            ca_folders=Path("/etc/ssl/private-ca"),
            client_cert=Path("/etc/ssl/client.crt"),
            client_key=Path("/etc/ssl/client.key"),
        )
        ctx = store.build_ssl_context()
    """

    ca_cert: Path | bytes | None = None
    ca_folders: Path | Sequence[Path] | None = None
    cert_patterns: tuple[str, ...] = CERT_FILE_PATTERNS
    recursive: bool = True
    client_cert: Path | None = None
    client_key: Path | None = None
    include_system_cas: bool = True
    verify: bool = True
    check_hostname: bool = True

    def __post_init__(self) -> None:
        # Frozen dataclass: normalisation/validation mutate via object.__setattr__, same
        # pattern used across the repo (e.g. varco_core.query AST nodes, varco_core.watch's
        # WatchTarget) for post-construction normalisation.
        object.__setattr__(self, "ca_folders", normalize_ca_folders(self.ca_folders))
        validate_trust_store_flags(
            self.verify, self.check_hostname, self.client_cert, self.client_key
        )

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> TrustStore:
        """
        Build a ``TrustStore`` from environment variables — additive, never a system-CA
        replacement (§D-T3-env).

        Reads:
            ``VARCO_TRUST_STORE_DIR`` → an entry in ``ca_folders``
            ``VARCO_CA_CERT``         → ``ca_cert``
            ``VARCO_CLIENT_CERT``     → ``client_cert``
            ``VARCO_CLIENT_KEY``      → ``client_key``
            ``SSL_CERT_FILE``         → ``ca_cert``, only if ``VARCO_CA_CERT`` left it unset
                                        (``ca_cert`` is a single slot; both names target it,
                                        varco's own name wins on conflict).
            ``SSL_CERT_DIR``          → an entry in ``ca_folders`` (``ca_folders`` accepts
                                        multiple entries, so this is additive alongside
                                        ``VARCO_TRUST_STORE_DIR`` rather than a conflict).

        Returns:
            A fully populated ``TrustStore``.

        Edge cases:
            - Missing env vars produce ``None``/default values — system CAs only, byte-
              identical to constructing ``TrustStore()`` directly.
            - **Divergence from OpenSSL semantics, stated loudly**: OpenSSL, ``uv``, and
              ``requests`` treat non-empty ``SSL_CERT_FILE``/``SSL_CERT_DIR`` as *replacing*
              the default trust store entirely. varco does not — both are merged additively
              on top of ``include_system_cas``'s default (``True``), because silently
              dropping the system store because a sidecar exported one env var is a
              production-outage shape in a framework whose every other CA mechanism is
              additive. See ``technical_docs/features/tls-trust-and-hot-reload.md``.
        """
        ca_cert: Path | None = Path(v) if (v := os.environ.get("VARCO_CA_CERT")) else None
        client_cert: Path | None = Path(v) if (v := os.environ.get("VARCO_CLIENT_CERT")) else None
        client_key: Path | None = Path(v) if (v := os.environ.get("VARCO_CLIENT_KEY")) else None

        folders: list[Path] = []
        if v := os.environ.get("VARCO_TRUST_STORE_DIR"):
            folders.append(Path(v))
        if v := os.environ.get(_SSL_CERT_DIR_ENV):
            folders.append(Path(v))
        # SSL_CERT_FILE names a single file, and ca_cert is a single slot — varco's own
        # VARCO_CA_CERT wins if both are set; SSL_CERT_FILE only fills the gap.
        if ca_cert is None and (v := os.environ.get(_SSL_CERT_FILE_ENV)):
            ca_cert = Path(v)

        return cls(
            ca_cert=ca_cert,
            ca_folders=folders or None,
            client_cert=client_cert,
            client_key=client_key,
        )

    def to_ssl_config(self) -> SSLConfig:
        """
        Convert this ``TrustStore`` to a ``varco_core.connection.ssl.SSLConfig``
        (§D-T3-bridge).

        Returns:
            An ``SSLConfig`` with equivalent CA/client-cert configuration.

        Edge cases (the two directions that remain lossy — exactly as 3.0's
        ``varco_fastapi.auth.TrustStore.to_ssl_config()`` already documented them):
            - ``ca_cert`` as ``bytes`` cannot be expressed as a ``Path`` in ``SSLConfig`` —
              the returned config has ``ca_cert=None`` in that case; the bytes-based CA is
              not transferred.
            - ``include_system_cas=False`` is not representable in ``SSLConfig`` (it always
              uses system CAs when ``verify=True``) — this information is lost.
            - Only the *first* ``ca_folders`` entry is transferred — ``SSLConfig.ca_folder``
              is singular. Additional folders are lost; convert manually if more than one
              folder must survive the round trip.
        """
        from varco_core.connection.ssl import SSLConfig  # noqa: PLC0415 — see module docstring

        # __post_init__ always normalises ca_folders to tuple[Path, ...] | None (see
        # normalize_ca_folders below) — mypy only sees the wider constructor-accepted type,
        # so the cast documents that established, tested invariant rather than widening it.
        folders = cast("tuple[Path, ...] | None", self.ca_folders)
        ca_cert_path: Path | None = self.ca_cert if isinstance(self.ca_cert, Path) else None
        ca_folder: Path | None = folders[0] if folders else None

        return SSLConfig(
            ca_cert=ca_cert_path,
            ca_folder=ca_folder,
            client_cert=self.client_cert,
            client_key=self.client_key,
            verify=self.verify,
            check_hostname=self.check_hostname,
        )

    # ── SSL context builder ───────────────────────────────────────────────────

    def build_ssl_context(self) -> ssl.SSLContext:
        """
        Build and return an ``ssl.SSLContext`` from this trust configuration.

        Ordering is the union of both predecessor implementations (§D-T3-model) — they
        already agree on everything they share:

        1. Base context: ``verify and include_system_cas`` → ``ssl.create_default_context()``;
           ``verify and not include_system_cas`` → blank ``PROTOCOL_TLS_CLIENT`` context with
           ``check_hostname=True``/``CERT_REQUIRED``; ``not verify`` → blank context with
           ``check_hostname=False``/``CERT_NONE``.
        2. ``check_hostname=False`` with ``verify=True`` → disable hostname checking.
        3. Every ``ca_folders`` entry, enumerated via ``iter_cert_files()``, loaded.
        4. ``ca_cert``: ``bytes`` → ``load_verify_locations(cadata=...)``; ``Path`` → ``cafile=``.
        5. ``client_cert`` + ``client_key`` → ``load_cert_chain(...)``.

        System CAs stay additive throughout — ``create_default_context()`` is followed by
        additive ``load_verify_locations`` calls, never replaced (``BACKLOG.md:32``).

        Returns:
            A configured ``ssl.SSLContext``.

        Raises:
            FileNotFoundError: A configured path does not exist.
            ssl.SSLError: A certificate fails to load.

        Thread safety:  ✅ Creates a new context per call — no shared mutable state.
        Async safety:   ✅ Synchronous — call at startup, not per-request; use
                           ``ReloadingTrustStore`` for a background-refreshed live context.
        """
        # Step 1: base context
        if self.verify:
            if self.include_system_cas:
                ctx = ssl.create_default_context()
            else:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        # Step 2: disable hostname checking when explicitly requested (still verifying)
        if not self.check_hostname and self.verify:
            ctx.check_hostname = False

        # Step 3: load every ca_folders entry (see the cast() note in to_ssl_config() above —
        # __post_init__ already normalised this to tuple[Path, ...] | None).
        folders = cast("tuple[Path, ...] | None", self.ca_folders)
        if folders:
            for folder in folders:
                for cert_path in iter_cert_files(
                    folder, patterns=self.cert_patterns, recursive=self.recursive
                ):
                    ctx.load_verify_locations(cafile=str(cert_path))

        # Step 4: load explicit ca_cert
        if self.ca_cert is not None:
            if isinstance(self.ca_cert, bytes):
                ctx.load_verify_locations(cadata=self.ca_cert.decode("utf-8"))
            else:
                ctx.load_verify_locations(cafile=str(self.ca_cert))

        # Step 5: mTLS client identity — __post_init__ already guarantees both or neither.
        if self.client_cert is not None and self.client_key is not None:
            ctx.load_cert_chain(certfile=str(self.client_cert), keyfile=str(self.client_key))

        return ctx


# ── Shared normalisation/validation (also used by the 3.0-semantics deprecation shim,
# which must normalise ca_folders the same way but skip the eager validation — §D-T3-oq1) ──


def normalize_ca_folders(value: Path | Sequence[Path] | None) -> tuple[Path, ...] | None:
    """
    Normalise ``ca_folders`` to a tuple, or ``None`` for "nothing configured".

    Args:
        value: ``None``, a single ``Path``, or a sequence of ``Path``s.

    Returns:
        A non-empty tuple of ``Path``s, or ``None`` if ``value`` was ``None`` or an empty
        sequence (§D-T3-model Edge cases: ``ca_folders=[]`` is treated as ``None``).
    """
    if value is None:
        return None
    if isinstance(value, Path):
        return (value,)
    normalized = tuple(Path(p) for p in value)
    return normalized or None


def validate_trust_store_flags(
    verify: bool,
    check_hostname: bool,
    client_cert: Path | None,
    client_key: Path | None,
) -> None:
    """
    Reproduce ``SSLConfig._validate_ssl_flags`` exactly (§D-T3-model).

    Raises:
        ValueError: ``check_hostname=True`` with ``verify=False``, or exactly one of
            ``client_cert``/``client_key`` is set.
    """
    if not verify and check_hostname:
        raise ValueError(
            "TrustStore: 'check_hostname=True' requires 'verify=True'. "
            "The ssl module enforces this — set 'check_hostname=False' "
            "when disabling certificate verification."
        )
    if (client_cert is None) != (client_key is None):
        raise ValueError(
            "TrustStore: 'client_cert' and 'client_key' must both be set "
            "or both be None for mTLS. Got: "
            f"client_cert={client_cert!r}, client_key={client_key!r}"
        )


__all__ = ["TrustStore"]
