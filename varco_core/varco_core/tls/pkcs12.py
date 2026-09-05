"""
varco_core.tls.pkcs12
=======================

PKCS#12/``.pfx`` ingestion for ``TrustStore`` (Plan 027 / T6b, §D-T6-pkcs12) — zero new
dependency, built entirely on ``cryptography``, already a hard ``varco_core`` dependency
(``varco_core/pyproject.toml:33-34``) used by ``varco_core.jwk``.

DESIGN: temp file, because stdlib ``ssl`` has no in-memory client-cert API (§D-T6-pkcs12)
    ✅ ``cryptography`` decodes a PKCS#12 bundle
       (``cryptography.hazmat.primitives.serialization.pkcs12.load_key_and_certificates``)
       into a private key + leaf cert + CA chain, all in memory — free, since the dependency
       is already there.
    ✅ Brief 001 §5 states plainly that PKCS#12 "is not natively supported" by stdlib ``ssl``
       and that the ecosystem answer is third-party shims (``httpx-pkcs12``,
       ``requests-pkcs12``) — a first-party implementation on top of an existing dependency
       is the differentiator the BACKLOG names (``BACKLOG.md:68``).
    ✅ Any intermediate CAs bundled in the ``.p12`` are written into the same PEM chain file,
       which is what ``ssl.SSLContext.load_cert_chain`` expects for a full chain; those same
       CA certs are additionally offered as trust anchors **only if** the caller opts in via
       ``TrustStore.pkcs12_trust_ca=True`` — a client identity bundle's CAs are not
       automatically trust anchors, and silently making them so would widen trust beyond what
       the caller asked for.
    ❌ **The private key touches the filesystem.** ``ssl.SSLContext.load_cert_chain`` takes
       filenames only — there is no ``load_cert_chain_from_memory``. Mitigations, all
       mandatory and all implemented in ``materialize_chain()`` below:
         - Prefer ``/dev/shm`` (tmpfs, typically not swapped to disk) when it exists and is
           writable — legitimate here because this cycle is **Linux-only**
           (``BACKLOG.md:35``); fall back to the system temp dir otherwise (e.g. ``/dev/shm``
           absent, full, or read-only).
         - The chain file is created with ``os.open(..., O_CREAT | O_EXCL | O_WRONLY, 0o600)``
           — never world/group readable, and ``O_EXCL`` refuses to follow or overwrite an
           existing path (no TOCTOU symlink race).
         - The containing directory is a fresh, private ``tempfile.mkdtemp()`` (mode ``0700``
           by default) — so even the directory listing does not leak the key's existence to
           another local user.
         - Both the file and its directory are removed in a ``finally``, on every exit path
           including an exception raised by the caller's own ``load_cert_chain`` call. The
           window during which the plaintext key exists on a filesystem is exactly the
           duration of one ``with materialize_chain(...):`` block — see the feature doc's
           Pitfalls table for the operational implication.
         - The key material is never logged.
    ❌ ``requests-pkcs12`` advertises "no temp files" (brief 001 §5) — it achieves that with a
       custom ``HTTPAdapter`` that bypasses ``load_cert_chain`` entirely, which is not
       available to a client-agnostic ``ssl.SSLContext`` builder. Rejected as out of shape for
       this layer (see Plan 027's "Alternatives considered").

Thread safety:  ✅ ``load_pkcs12_identity()`` and ``materialize_chain()`` hold no shared
                   state; each call is independent.
Async safety:   ✅ Both are synchronous, filesystem-bound helpers — call them from
                   ``TrustStore.build_ssl_context()``, itself synchronous by design.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_DEV_SHM = Path("/dev/shm")


class Pkcs12LoadError(ValueError):
    """
    Raised when a PKCS#12 bundle cannot be decoded — wrong password, corrupt bundle, or a
    trust-only bundle with no private key. Never lets a raw ``cryptography`` traceback (or a
    bare ``TypeError``/``ValueError`` from a mismatched password) escape to the caller.
    """


@dataclass(frozen=True)
class Pkcs12Identity:
    """
    A decoded PKCS#12 client identity, held entirely as PEM ``bytes`` in memory.

    Attributes:
        key_pem: The private key, PEM-encoded, unencrypted (this object's lifetime is
            already memory-only and process-private; encrypting it again here would only
            move the password problem, not solve it).
        cert_pem: The leaf certificate, PEM-encoded.
        ca_pems: Any CA certificates bundled alongside the leaf, each PEM-encoded. Empty if
            the bundle carried no chain.
    """

    key_pem: bytes
    cert_pem: bytes
    ca_pems: tuple[bytes, ...]


def load_pkcs12_identity(
    path: Path, password: str | bytes | Callable[[], str | bytes] | None
) -> Pkcs12Identity:
    """
    Decode a PKCS#12/``.pfx`` bundle into PEM-encoded key/cert/CA material, entirely in memory.

    Args:
        path: Path to the ``.p12``/``.pfx`` file.
        password: The bundle's decryption password — ``str``, ``bytes``, a zero-argument
            callable returning one, or ``None``/``b""`` for a bundle serialised with no
            encryption. ``cryptography`` distinguishes ``None`` from ``b""``; both are passed
            through unchanged.

    Returns:
        A ``Pkcs12Identity`` — never touches the filesystem beyond reading ``path`` itself.

    Raises:
        Pkcs12LoadError: The bundle cannot be decoded (wrong password, corrupt bundle), or
            the bundle carries no private key (a trust-only bundle — use ``ca_cert``/
            ``ca_folders`` instead of ``pkcs12_file`` for that case).

    Edge cases:
        - ``password`` as a callable is invoked exactly once, here, not cached.
        - A bundle with a key but no leaf certificate is accepted by ``cryptography`` (some
          tools produce these) — ``cert_pem`` would then be empty ``bytes`` for an absent
          cert; a materialized chain built from that will fail at ``load_cert_chain`` time
          with a stdlib ``ssl.SSLError``, not here.
    """
    resolved_password: bytes | None
    if callable(password):
        raw = password()
        resolved_password = raw.encode() if isinstance(raw, str) else raw
    elif isinstance(password, str):
        resolved_password = password.encode()
    else:
        resolved_password = password

    try:
        data = path.read_bytes()
        key, cert, additional_certs = pkcs12.load_key_and_certificates(data, resolved_password)
    except OSError as exc:
        raise Pkcs12LoadError(f"Could not read PKCS#12 bundle at {path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — cryptography raises assorted types on bad input
        raise Pkcs12LoadError(
            f"Could not decode PKCS#12 bundle at {path}: wrong password or corrupt bundle ({exc})"
        ) from exc

    if key is None:
        raise Pkcs12LoadError(
            f"PKCS#12 bundle at {path} carries no private key — it looks like a trust-only "
            "bundle. Use TrustStore(ca_cert=...) / TrustStore(ca_folders=...) instead of "
            "pkcs12_file for CA-only material."
        )

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM) if cert is not None else b""
    ca_pems = tuple(c.public_bytes(serialization.Encoding.PEM) for c in (additional_certs or []))

    return Pkcs12Identity(key_pem=key_pem, cert_pem=cert_pem, ca_pems=ca_pems)


@contextmanager
def materialize_chain() -> Iterator[Path]:
    """
    Reserve a private (``0700`` dir + ``0600`` file, ``/dev/shm``-preferred) empty temp file,
    yield its path for the caller to fill with PEM bytes, and unlink it (and its containing
    directory) on the way out — success **or** failure (§D-T6-pkcs12).

    DESIGN: the file lifecycle is deliberately separate from PKCS#12 decoding.
        ✅ Decoding (``load_pkcs12_identity()``) happens *inside* the caller's ``with``
           block, after this generator has already yielded — so a decode failure (wrong
           password, corrupt bundle) is raised from *inside* the block, guaranteeing this
           function's own ``finally`` still runs and the temp material is removed on that
           path exactly the same way as on the success path. Doing the decode *before*
           yielding would mean a decode failure aborts ``__enter__()`` itself — no temp
           material would ever have been created, which sounds safer but actually makes the
           two failure paths (decode failure vs. ``load_cert_chain`` failure) behave
           differently under test and in a future refactor. One code path, one guarantee.

    Yields:
        The path of a freshly created, empty, ``0600`` file the caller writes PEM chain
        bytes into (key immediately followed by the leaf cert — what
        ``ssl.SSLContext.load_cert_chain(certfile=...)`` expects with no separate
        ``keyfile=``). CA certs are not part of this file; the caller loads those
        separately via ``load_verify_locations`` if ``pkcs12_trust_ca=True``.

    Raises:
        OSError: The chosen temp location cannot be written to at all (both ``/dev/shm`` and
            the system temp dir are unusable) — surfaces as-is, not wrapped.

    Edge cases:
        - ``/dev/shm`` present but full or read-only → falls back to ``tempfile.mkdtemp()``'s
          default location; never fails the handshake because of a tmpfs problem.
        - The directory is created with mode ``0700`` (``tempfile.mkdtemp()``'s default); the
          file itself is additionally forced to ``0600`` via ``os.open``'s mode argument,
          because the umask can otherwise widen a file's permissions even inside a ``0700``
          directory.
        - Removal happens in ``finally`` — any exception raised *inside* the ``with`` block
          (a decode failure, or ``load_cert_chain`` rejecting a malformed cert) still
          triggers cleanup before propagating.

    Thread safety: ✅ Each call gets its own private directory — no shared state, safe to call
                      concurrently from multiple threads.
    """
    base = _DEV_SHM if (_DEV_SHM.is_dir() and os.access(_DEV_SHM, os.W_OK)) else None
    directory = Path(tempfile.mkdtemp(prefix="varco-tls-pkcs12-", dir=base))
    chain_path = directory / "chain.pem"
    try:
        # O_EXCL: refuse to follow/overwrite an existing path (no TOCTOU symlink race).
        # 0o600: never group/world readable regardless of umask.
        fd = os.open(chain_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)
        os.close(fd)  # reserve the file now; the caller writes its actual PEM content
        yield chain_path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


__all__ = ["Pkcs12Identity", "Pkcs12LoadError", "load_pkcs12_identity", "materialize_chain"]
