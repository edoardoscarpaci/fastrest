# Crypto-shredding — arbitrary-principal scoping + destroy semantics

Plan 005, Phase 1 (gaps U-1, U-2). Closes: "the only per-tenant key granularity
varco ships is `tenant_id`, and there is no way to make a data subject's
ciphertext permanently unreadable without deleting the row."

## The scope model

`EncryptionKeyEntry` gained a `scope: str | None` field alongside (not
replacing) `tenant_id`. `scope` defaults to `tenant_id` **verbatim** at the
Python level: `EncryptionKeyEntry.__post_init__`/`from_dict` sets
`scope = tenant_id` whenever a loaded row has no `scope` value, so any code
path that reads a row directly (e.g. `load_for_tenant`, which still filters
on the `tenant_id` column) behaves identically before and after this
feature.

⚠️ **`load_for_scope`/`destroy_scope`/`list_scopes` do need a one-time
backfill on persisted stores.** All three backends filter on the `scope`
column/index itself (`SAEncryptionKeyStore`: `WHERE scope == :scope`;
`RedisEncryptionKeyStore`: a per-scope Set index populated only at write
time; `BeanieEncryptionKeyStore`: `find({"scope": scope})`) — none of them
fall back to the Python-level `tenant_id` default for a row whose `scope`
column is still `NULL`/unindexed. A pre-Phase-1 row is invisible to
`load_for_scope(tenant_id)` until you run the backfill:

```sql
-- SAEncryptionKeyStore (varco_encryption_keys)
UPDATE varco_encryption_keys SET scope = tenant_id WHERE scope IS NULL;
```

For Redis/Beanie, backfill by re-`save()`-ing each existing entry (this
populates the scope Set / `scope` field) or by writing an equivalent one-off
script per backend. Until the backfill runs, `load_for_tenant`/
`build_tenant_registry()` are unaffected (they never read `scope`).

```python
# Tenant-scoped key (today's usage, unchanged)
enc = await manager.get_or_create_encryptor("acme")

# Per-data-subject key — pick your own scope convention; varco never parses it
registry = await manager.build_scoped_registry(f"acme:subject:{subject_id}")
ciphertext = registry.encrypt(pii_bytes, context=f"acme:subject:{subject_id}")
```

⚠️ Varco does **not** parse scope strings. Do not embed personal data in a
scope value itself (e.g. an email address) — use a pseudonymous id
(`subject_id`, not the subject's name/email).

`build_scoped_registry(scope)` loads **only that scope's keys** — unlike
`build_tenant_registry()`, which eagerly loads every tenant at startup. This
matters at volume: per-subject keys mean the store grows with data subjects,
and an eager all-keys load is fine at 50 tenants and fatal at 50 000 subjects.

## The destroy/retire distinction

Two verbs, two contracts, both on `MultiKeyEncryptorRegistry`:

| | `retire(kid)` | `destroy(kid)` |
|---|---|---|
| Removes key from primary rotation | ✅ | ➖ (leaves it registered) |
| Decrypt of existing ciphertext | ✅ still works | ❌ raises `KeyDestroyedError` |
| Use case | "re-encrypt everything, then drop the old key" | "this data subject's data must become permanently unreadable" |
| Reversible | N/A — key material is gone | No — the whole point |

Destruction is a **tombstone**, not a delete: `EncryptionKeyStore.destroy_scope`
blanks `key_material` and sets `destroyed_at` on every matching entry, but
keeps the row. A deleted row is indistinguishable from a never-existed row —
the tombstone is what lets a subsequent decrypt raise a distinguishable
`KeyDestroyedError` (a subclass of `EncryptionError`) instead of the generic
"unknown kid" error, so callers can render "this data was erased" instead of
"corrupt data".

```python
receipt = await manager.destroy_scope("acme:subject:42", actor="admin@acme.com")
# receipt.kids -> every kid that was tombstoned; () on a second call (idempotent)

try:
    registry.decrypt(ciphertext, context="acme:subject:42")
except KeyDestroyedError:
    ...  # render "erased", not "corrupt"
```

Destroying one scope never affects another scope's decryptability (the R-045
regression test in `varco_core/tests/test_encryption_destroy.py`).

## `EncryptionKeyStore` capability shim

`EncryptionKeyStore` is a `runtime_checkable` **Protocol**. Widening it with
`load_for_scope`/`list_scopes`/`destroy_scope` would silently break
third-party implementations at `isinstance()` time if `EncryptionKeyManager`
called the new methods directly. It never does — it always goes through a
capability shim (`getattr(store, "load_for_scope", None)`) that falls back to
`load_for_tenant`/`list_tenants` (treating `scope == tenant_id`) and a
tenant-driven tombstone loop for `destroy_scope`, logging a **one-time**
warning naming the store class. A store implementing only the tenant methods
keeps working, unmodified.

## Operator obligation (stated verbatim, per U-2)

**Key-store backups must not outlive the erasure window, or destruction is
not destruction.** Tombstoning the live row is necessary but not sufficient —
if a backup snapshot taken before `destroy_scope()` retains the original
`key_material`, restoring from that backup un-shreds the data. Align key-store
backup retention with your data-subject erasure SLA.

## Key-volume guidance

Per-subject keys scale with data subjects, not tenants. Do not call
`build_tenant_registry()`-equivalent eager loading for a per-subject scope —
always use `build_scoped_registry(scope)`, which issues exactly one scoped
store query.

## Backends

`SAEncryptionKeyStore`, `RedisEncryptionKeyStore`, `BeanieEncryptionKeyStore`
all implement the three scope methods natively. `varco_sa` adds an indexed
`scope` column and a nullable `destroyed_at` column to `varco_encryption_keys`
— both nullable, so the schema change itself is additive and safe against a
live table. The `scope = tenant_id` **data** backfill described above is
still required before `load_for_scope(tenant_id)` finds pre-existing rows on
any backend — the nullable column only makes the migration safe to apply,
it does not populate it.

⚠️ This repository does not ship an Alembic environment for its own
infrastructure tables (`varco_encryption_keys`, `varco_jobs`, `varco_outbox`)
— downstream applications generate their own revision via `autogenerate`
against the updated `Table`/`Document` metadata (see
`varco_sa.alembic_helpers`). Because every new column is nullable, that
autogenerated `ALTER TABLE ADD COLUMN` is additive and safe to run against a
live table.

Redis's `destroy_scope` is intentionally **not** Lua-atomic across entries
(see the `DESIGN:` block in `varco_redis/varco_redis/encryption_store.py`) —
each per-entry tombstone write is itself idempotent, so a crash mid-destroy
is safe to retry.

## Pitfalls

| Pitfall | Symptom | Root Cause | Fix |
|---|---|---|---|
| **Destroyed key renders as corrupt data** | Decrypt of a crypto-shredded record raises a generic-looking error | `KeyDestroyedError` was caught by a bare `except EncryptionError` and treated the same as tampered data | Catch `KeyDestroyedError` specifically (it's a subclass) and render "erased", not "corrupt" |
| **Per-subject registry built with `build_tenant_registry`** | Startup loads every key in the store, even ones for scopes not yet needed | `build_tenant_registry()` is eager-all; there is no per-scope equivalent by that name | Use `manager.build_scoped_registry(scope)` — loads exactly one scope's keys |
