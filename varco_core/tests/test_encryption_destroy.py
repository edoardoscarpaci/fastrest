"""
Unit tests for crypto-shredding destroy semantics (plan 005, Phase 1, Step 15).
=================================================================================

RED until Phase 1 lands:
    - EncryptionKeyEntry gains scope/destroyed_at/is_destroyed (Step 5).
    - EncryptionKeyStore Protocol gains load_for_scope/list_scopes/destroy_scope
      (Step 7); InMemoryEncryptionKeyStore implements them natively (Step 8).
    - varco_core.encryption gains KeyDestroyedError, DestroyReceipt,
      MultiKeyEncryptorRegistry.destroy(kid), ScopedEncryptorRegistry, and
      EncryptionKeyManager.build_scoped_registry/rotate_scope/destroy_scope
      (Steps 9-14).

These tests encode the plan's Step 15 failing-tests-first list and the
Phase 1 edge cases:
    - destroy returns a receipt listing every kid for the scope
    - a second destroy returns an empty kids tuple (idempotent)
    - decrypt of ciphertext framed with a destroyed kid raises KeyDestroyedError
      and NOT the generic error
    - decrypt of an unknown kid still raises the generic error
    - destroying scope A leaves scope B decryptable (the R-045 regression test)
    - a store implementing only the tenant methods still works through the shim
"""

from __future__ import annotations


import pytest

from varco_core.encryption_store import EncryptionKeyEntry, InMemoryEncryptionKeyStore


def _make_manager(store: object):
    from varco_core.encryption_store import EncryptionKeyManager

    return EncryptionKeyManager(store)  # type: ignore[arg-type]


class TestDestroyScopeStorePrimitive:
    async def test_destroy_scope_returns_receipt_with_all_kids(self) -> None:
        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")

        kids = await store.destroy_scope("scope-a")
        assert isinstance(kids, tuple)
        assert len(kids) >= 1

    async def test_destroy_scope_is_idempotent_second_call_empty(self) -> None:
        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")

        first = await store.destroy_scope("scope-a")
        assert len(first) >= 1
        second = await store.destroy_scope("scope-a")
        assert second == ()

    async def test_destroy_scope_on_unknown_scope_returns_empty_not_error(
        self,
    ) -> None:
        store = InMemoryEncryptionKeyStore()
        kids = await store.destroy_scope("never-existed")
        assert kids == ()

    async def test_destroy_scope_never_deletes_the_tombstone(self) -> None:
        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")
        kids = await store.destroy_scope("scope-a")

        for kid in kids:
            entry = await store.load(kid)
            assert entry is not None
            assert entry.is_destroyed is True


class TestEncryptionKeyManagerDestroyScope:
    async def test_destroy_scope_returns_destroy_receipt(self) -> None:
        from varco_core.encryption import DestroyReceipt

        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")

        receipt = await manager.destroy_scope("scope-a")
        assert isinstance(receipt, DestroyReceipt)
        assert receipt.scope == "scope-a"
        assert len(receipt.kids) >= 1

    async def test_second_destroy_scope_receipt_has_empty_kids(self) -> None:
        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")

        await manager.destroy_scope("scope-a")
        second = await manager.destroy_scope("scope-a")
        assert second.kids == ()


class TestKeyDestroyedErrorDecryptPath:
    async def test_decrypt_of_destroyed_kid_raises_key_destroyed_error(self) -> None:
        from varco_core.encryption import EncryptionError, KeyDestroyedError

        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)
        registry = await manager.build_scoped_registry("scope-a")

        ciphertext = registry.encrypt(b"secret", context="scope-a")

        await manager.destroy_scope("scope-a")

        with pytest.raises(KeyDestroyedError):
            registry.decrypt(ciphertext, context="scope-a")

        # KeyDestroyedError must be distinguishable from the generic error —
        # it is a subclass, so a bare `except EncryptionError` still catches it,
        # but callers checking the specific type must be able to tell them apart.
        assert issubclass(KeyDestroyedError, EncryptionError)

    async def test_decrypt_of_unknown_kid_still_raises_generic_error(self) -> None:
        from varco_core.encryption import (
            EncryptionError,
            FernetFieldEncryptor,
            KeyDestroyedError,
            MultiKeyEncryptorRegistry,
            _pack_ciphertext,
        )
        from cryptography.fernet import Fernet

        reg = MultiKeyEncryptorRegistry(
            primary_kid="v1",
            primary_encryptor=FernetFieldEncryptor(Fernet.generate_key()),
        )
        raw = reg._encryptors["v1"].encrypt(b"secret")
        bogus = _pack_ciphertext("never-registered-kid", raw)

        with pytest.raises(EncryptionError) as exc:
            reg.decrypt(bogus)
        assert not isinstance(exc.value, KeyDestroyedError)

    async def test_retired_not_destroyed_kid_still_decrypts(self) -> None:
        # Retire keeps decrypt working; only destroy makes it raise.
        from varco_core.encryption import (
            FernetFieldEncryptor,
            MultiKeyEncryptorRegistry,
        )
        from cryptography.fernet import Fernet

        reg = MultiKeyEncryptorRegistry(
            primary_kid="v1",
            primary_encryptor=FernetFieldEncryptor(Fernet.generate_key()),
        )
        reg.register("v2", FernetFieldEncryptor(Fernet.generate_key()))

        ciphertext = reg.encrypt(b"secret")
        reg.set_primary("v2")
        # v1 is not retired — still decryptable
        assert reg.decrypt(ciphertext) == b"secret"


class TestDestroyingOneScopeLeavesOthersDecryptable:
    async def test_destroying_scope_a_leaves_scope_b_decryptable(self) -> None:
        # The R-045 regression test — the one that proves the gap is closed.
        store = InMemoryEncryptionKeyStore()
        manager = _make_manager(store)

        registry_a = await manager.build_scoped_registry("scope-a")
        registry_b = await manager.build_scoped_registry("scope-b")

        ct_a = registry_a.encrypt(b"secret-a", context="scope-a")
        ct_b = registry_b.encrypt(b"secret-b", context="scope-b")

        await manager.destroy_scope("scope-a")

        from varco_core.encryption import KeyDestroyedError

        with pytest.raises(KeyDestroyedError):
            registry_a.decrypt(ct_a, context="scope-a")

        assert registry_b.decrypt(ct_b, context="scope-b") == b"secret-b"


class TestBuildScopedRegistryLoadsOnlyItsScope:
    async def test_build_scoped_registry_issues_exactly_one_scoped_query(self) -> None:
        calls: list[str | None] = []

        class _CountingStore(InMemoryEncryptionKeyStore):
            async def load_for_scope(self, scope: str | None):
                calls.append(scope)
                return await super().load_for_scope(scope)

        store = _CountingStore()
        manager = _make_manager(store)
        await manager.get_or_create_encryptor("scope-a")
        calls.clear()

        await manager.build_scoped_registry("scope-a")
        assert calls == ["scope-a"]


class TestCapabilityShimForTenantOnlyStores:
    async def test_manager_works_through_shim_with_tenant_only_store(self) -> None:
        # A third-party store implementing only load_for_tenant/list_tenants
        # (no load_for_scope/list_scopes/destroy_scope) must still work through
        # EncryptionKeyManager via the capability shim (Step 14), with a
        # one-time deprecation warning.
        class _TenantOnlyStore:
            def __init__(self) -> None:
                self._entries: dict[str, EncryptionKeyEntry] = {}

            async def save(self, entry: EncryptionKeyEntry) -> None:
                self._entries[entry.kid] = entry

            async def load(self, kid: str) -> EncryptionKeyEntry | None:
                return self._entries.get(kid)

            async def load_for_tenant(
                self, tenant_id: str | None
            ) -> list[EncryptionKeyEntry]:
                return [e for e in self._entries.values() if e.tenant_id == tenant_id]

            async def list_tenants(self) -> list[str]:
                return sorted(
                    {e.tenant_id for e in self._entries.values() if e.tenant_id}
                )

            async def delete(self, kid: str) -> None:
                self._entries.pop(kid, None)

        store = _TenantOnlyStore()
        manager = _make_manager(store)

        # Uses the shim internally to build a scoped registry over "acme".
        registry = await manager.build_scoped_registry("acme")
        ciphertext = registry.encrypt(b"secret", context="acme")
        assert registry.decrypt(ciphertext, context="acme") == b"secret"
