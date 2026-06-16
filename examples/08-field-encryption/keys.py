"""
keys.py
=======
Key generation and management helpers for the ``08-field-encryption`` example.

This module provides convenience functions for creating ``FernetFieldEncryptor``
instances and optionally layering ``EncryptionKeyManager`` over an
``SAEncryptionKeyStore`` for full key lifecycle management.

Two usage modes
---------------

1. **Ephemeral** (tests / demos) — generate a fresh key in memory each time::

       encryptor = generate_ephemeral_encryptor()

2. **Persistent** (production-like) — keys stored in the DB via
   ``SAEncryptionKeyStore`` + ``EncryptionKeyManager``::

       encryptor = await build_persistent_encryptor(engine)

   Keys survive pod restarts and can be rotated without downtime.

DESIGN: Ephemeral for tests, Persistent for production
    ✅ Tests avoid Docker for just the key store — a single container (PostgreSQL)
       covers both the patient table and the encryption key table.
    ✅ Persistent path demonstrates the full production-ready lifecycle.
    ✅ Both paths produce a ``FieldEncryptor`` with the same interface — the
       service / mapper code is identical regardless of key origin.
    ❌ Ephemeral keys are lost on restart — data encrypted with them becomes
       permanently unreadable.  Never use in production.

Thread safety:  ✅ ``generate_ephemeral_encryptor()`` is stateless — safe to
                call from multiple threads (each gets its own encryptor).
Async safety:   ✅ ``build_persistent_encryptor()`` is async — call with ``await``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.fernet import Fernet

from varco_core.encryption import FernetFieldEncryptor, FieldEncryptor
from varco_core.encryption_store import EncryptionKeyManager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def generate_ephemeral_encryptor() -> FernetFieldEncryptor:
    """
    Generate a fresh ``FernetFieldEncryptor`` backed by a random key.

    The key exists only in memory and is discarded when the process exits.
    Intended for tests and short-lived demos where key persistence is not needed.

    Returns:
        A ready-to-use ``FernetFieldEncryptor`` with a fresh random key.

    Edge cases:
        - The returned encryptor is NOT registered with any key store.
          Calling ``build_persistent_encryptor()`` in the same process will
          use a different key — do not mix the two patterns for the same data.

    Example::

        enc = generate_ephemeral_encryptor()
        ct  = enc.encrypt(b"secret")
        pt  = enc.decrypt(ct)
        assert pt == b"secret"
    """
    # Fernet.generate_key() uses os.urandom — cryptographically secure
    key = Fernet.generate_key()
    return FernetFieldEncryptor(key)


async def build_persistent_encryptor(
    engine: AsyncEngine,
    *,
    tenant_id: str | None = None,
    master_encryptor: FieldEncryptor | None = None,
) -> tuple[FieldEncryptor, EncryptionKeyManager]:
    """
    Build (or recover) an encryptor backed by the SA ``varco_encryption_keys`` table.

    On first call: generates a fresh DEK, stores it in the DB, returns the encryptor.
    On subsequent calls: loads the existing primary DEK from the DB and returns it.

    This pattern supports zero-downtime key rotation and multi-replica deployments —
    all pods share the same key store and read the same DEK on startup.

    Args:
        engine:           An ``AsyncEngine`` pointing at the target database.
                          The same engine used for the patient table can be reused —
                          the key store lives in its own ``varco_encryption_keys``
                          table, separate from application MetaData.
        tenant_id:        Optional tenant namespace for per-tenant key isolation.
                          ``None`` means a single global key (suitable for
                          single-tenant deployments).
        master_encryptor: Optional KEK for envelope encryption of stored DEKs.
                          When set, raw key bytes are wrapped before being written
                          to the DB — providing defence-in-depth if the DB is
                          compromised.  The KEK must be loaded from a secure source
                          (Vault, env var, HSM) on every startup.

    Returns:
        ``(encryptor, manager)`` tuple:
        - ``encryptor`` — active ``FieldEncryptor`` for this tenant.
        - ``manager``   — ``EncryptionKeyManager`` for rotation / audit.

    Raises:
        Exception: DB connection error or store write failure.

    Edge cases:
        - If the ``varco_encryption_keys`` table does not exist, ``ensure_table()``
          creates it automatically (idempotent DDL).
        - Concurrent first-calls from multiple pods may generate multiple DEKs.
          This is harmless — ``get_or_create_encryptor`` will always return the
          same encryptor once the cache is warm.  For strict single-key guarantees,
          call this function once at startup before accepting requests.

    Thread safety:  ⚠️ The first call may race — see ``EncryptionKeyManager`` docs.
    Async safety:   ✅ All DB operations are awaited.

    Example::

        enc, manager = await build_persistent_encryptor(engine, tenant_id="clinic-a")
        # Later — rotate the key
        new_enc = await manager.rotate("clinic-a")
    """
    from varco_sa.encryption_store import SAEncryptionKeyStore

    store = SAEncryptionKeyStore(engine)

    # Create the key table if it does not yet exist — idempotent DDL
    await store.ensure_table()

    manager = EncryptionKeyManager(store, master_encryptor=master_encryptor)

    # get_or_create_encryptor: returns cached key on warm path, generates on
    # first call.  This is the canonical startup pattern from the docstring.
    encryptor = await manager.get_or_create_encryptor(tenant_id)

    return encryptor, manager


__all__ = [
    "generate_ephemeral_encryptor",
    "build_persistent_encryptor",
]
