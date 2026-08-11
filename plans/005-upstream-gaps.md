# Plan 005 — Upstream gaps (AG Builder gap register)

## Goal

Close the actionable gaps filed in `UPSTREAM-GAPS.md` so a downstream product can adopt the
`varco_*` packages as its base layer without carrying local wrappers. After this plan:
per-data-subject crypto-shredding is possible, JWT verification fails closed, the outbox relay
and the job store both have bounded retry with a dead-letter path, jobs have a time dimension
and an enforced lease with fencing, Postgres locking is safe behind a transaction pooler, and
the A2A surface matches spec v1.0.0.

## Non-goals

- **U-9** (graph-shaped durable execution) — filer explicitly decided against; recorded as
  "no change requested".
- **U-10** (MCP client) — filer closed it as "correct, wrong direction"; the adapter is a
  server, by design. Only its *documentation* is touched (Phase 0).
- **U-14** (composed auth middleware / policy decorator) — filer reports the layer asymmetry is
  deliberate and asks for no change.
- **U-15** (pagination envelope, idempotency keys, API versioning) — filer states a generic
  abstraction would fit worse than their own 200 lines; no request made.
- **U-12** (conformance check at bind time) — lives in the separate `providify` repo, not this
  workspace.
- Not built here even though adjacent: a durable DLQ was declared "ours to build" by the filer.
  We ship one anyway (Phase 3, Step 3.7) but only because varco's *own* relay needs it — see the
  scope note there. Recurrence/cron for jobs is explicitly not requested and not built.
- No delete-path / per-store erasure classification / receipt replay (filer's ADR-017 says those
  are theirs). We ship the shred primitive only.

---

## Source corrections — do not propagate the gap file's wording

The gap register is a downstream artifact and four of its claims do not survive contact with
this repo's source. The plan below is written against source, not against the register.

1. **U-11 is "add a lease dimension", not "honour an existing parameter".** The register says
   `try_claim` "accepts a TTL and ignores it". In source
   `varco_core/varco_core/job/base.py:517` is `async def try_claim(self, job_id: UUID) -> Job | None`
   — **there is no `ttl` parameter at all**. The accepted-and-ignored TTL is on a different class:
   `SAAdvisoryLock.try_acquire(key, *, ttl)` at `varco_sa/varco_sa/advisory_lock.py:166-186`
   ("accepted for API compatibility but is NOT enforced at the database level"). Phase 4 therefore
   *introduces* the lease parameters; it does not wire up dormant ones.

2. **U-8: the documentation is the wrong artifact, not the code.** `ARCHITECTURE.md:1206` says
   A2A tasks are synchronous and `/tasks/{task_id}` is "echo-back, no history stored".
   `varco_fastapi/varco_fastapi/router/skill.py:264-266` accepts `job_runner`, `job_store` and
   `conversation_store`, and its own docstring at `:278-289` documents async submission with
   `state: working`, polling, and `GET /tasks/{task_id}/history`. **Async A2A already works.**
   Fix the doc; do not "add" async support.

3. **U-7's rate-limiter leg is already shipped.** The register (and the scout) say no
   `RedisRateLimiter` exists. It does: `varco_redis/varco_redis/rate_limit.py:169`
   `class RedisRateLimiter(RateLimiter)` — distributed sliding window over Redis sorted sets with
   an atomic Lua script — exported at `varco_redis/varco_redis/__init__.py:70,96`. CLAUDE.md's
   reference to it is correct. Only U-7's *second* sentence (distributed **concurrency** limiting
   is a different primitive) is still open.

4. **`request_token` is load-bearing, so U-19 cannot simply stop populating it.**
   `varco_fastapi/varco_fastapi/job/runner.py:784-786` forwards `job.request_token` as
   `Authorization: Bearer` on the completion callback, and `router/base.py:1567-1582` auto-populates
   it from `request_token_var` on every async-capable route. Removing or blanking it by default
   would break callback auth. U-19 is therefore *additive reference fields + an opt-out flag*,
   never a default change.

---

## Design

### Sequencing and why

```
Phase 0  docs truth pass (U-8, U-7 record)        ── no code, unblocks nothing, stops
   │                                                  future consumers mis-filing gaps
   ├── Phase 1  encryption scope + destroy (U-1, U-2)      P0, independent
   ├── Phase 2  fail-closed JWT (U-13)                     P1, independent
   │
   └── Phase 3  shared DLQ concept + relay resilience (U-6)     P1
            │      ▲ designs DeadLetterEntry ONCE for:
            │        relay · consumers · job store
            ▼
        Phase 4  job store: time + lease + fencing (U-17 + U-11 + U-18/U-19 columns)   P1
            │      ▲ ONE varco_sa migration, ONE claim-query rewrite
            ▼
        Phase 5  SAXactAdvisoryLock (U-16)              P1, varco_sa, pairs with Phase 4's poller
            ▼
        Phase 6  job hygiene APIs (U-18 delete_where, U-19 reference fields)   P2/P1-report
            ▼
        Phase 7  A2A v1.0.0 + SkillSource (U-3 + U-4 — ONE piece)              P2, largest
            ▼
        Phase 8  reports & small additions (U-5 RLS report, U-7 concurrency)   P2
```

Two couplings are mandatory and are the reason for the phase order:

- **One DLQ concept, three producers.** U-6 asks for a dead-letter path on `OutboxRelay`; U-17 §4
  asks for one on the job store and explicitly says "one DLQ concept serving both consumers and
  jobs is the better outcome than two". `DeadLetterEntry`
  (`varco_core/varco_core/event/dlq.py:82-149`) is currently event-shaped — it carries a
  `DomainEvent`. The relay must dead-letter an `OutboxEntry` whose payload may have failed to
  deserialize, and the job store must dead-letter a `Job`. **Generalise `DeadLetterEntry` once, in
  Phase 3, before anything else consumes it.** Phase 4 then reuses it with zero new concepts.

- **One job-table migration.** U-11 (lease/epoch/owner), U-17 (`run_at`, attempt counters), U-18
  (`expires_at`) and U-19 (issuer/subject/hash) are all columns on `varco_sa`'s `varco_jobs`
  (`varco_sa/varco_sa/job_store.py:83-108`), and U-11 + U-17 both rewrite the same `try_claim`
  `WHERE` clause (`:423-446`). Ship **one** Alembic revision in Phase 4 adding every column for all
  four gaps, even though U-18's and U-19's *code* lands in Phase 6. Operators run one migration,
  not three; the claim query is rewritten once, not twice.

### Compatibility posture (these are published PyPI packages)

Default rule: **additive and default-preserving**. Every new parameter is keyword-only with a
default that reproduces today's behaviour byte-for-byte. Two deliberate exceptions, both in
Phase 2, both security defaults:

| Change | Old default | New default | Escape hatch |
|---|---|---|---|
| `TrustedIssuerRegistry.verify()` enforces `iss` | not checked | checked | `enforce_issuer=False` / `VARCO_JWT_ENFORCE_ISS=false` |
| `JwtBearerAuth` requires an audience | warn + proceed | refuse to construct | `allow_any_audience=True` / `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` |

Both escape-hatch names state the risk, per the filer's ask. Version all changed packages
`1.1.x → 1.2.0`; the Phase 2 items get a prominent **BREAKING (security default)** CHANGELOG entry.

`EncryptionKeyStore` is a `runtime_checkable` **Protocol**
(`varco_core/varco_core/encryption_store.py:223`), so adding methods to it silently breaks
third-party implementations at `isinstance` time. Phase 1 therefore adds the new methods to the
Protocol *and* has `EncryptionKeyManager` call them through a capability shim that falls back to
`load_for_tenant`/`list_tenants` with a one-time deprecation log. `AbstractJobStore` and
`OutboxRepository` are ABCs, so new methods there get concrete default implementations rather
than being `@abstractmethod`.

### Alternatives considered

- **Bump the major version and change signatures freely (`load_for_tenant` → `load_for_scope`,
  `try_claim(job_id, ttl)`).** ❌ Rejected: every consumer including the filer's own interim code
  is written against the current shapes, and the filer's stated migration path for U-17 is "a
  column rename and a binding switch", not a rewrite. ✅ Additive keeps every existing deployment
  on a `pip install -U` upgrade path with no code edits; the cost is a permanently wider surface
  (`load_for_tenant` and `load_for_scope` both exist), which we pay in docstrings.

- **Model erasure as `store.delete(kid)` plus an external receipt log** (no tombstone).
  ❌ Rejected: U-2 §3 needs decrypt of a shredded `kid` to raise a *distinguishable*
  `KeyDestroyedError` so callers can render "erased" rather than "corrupt". A deleted row is
  indistinguishable from a never-existed row. ✅ A tombstone entry (`destroyed_at` set,
  `key_material` blanked) gives the distinguishable read path for free and is itself the audit
  record. ❌ It keeps one row per destroyed scope forever — acceptable, it holds no key material
  and no personal data.

- **Put `run_at`/lease handling in a new scheduler component.** ❌ Rejected: the filer's own
  sizing is "one nullable column and one predicate in an existing query", and `SAJobStore.try_claim`
  is already an atomic `SELECT … FOR UPDATE SKIP LOCKED`. A scheduler would be a subsystem where a
  column suffices. ✅ Extending the existing claim query keeps the correct primitive and makes the
  change reviewable.

- **A second, job-specific DLQ abstraction.** ❌ Rejected explicitly by U-17 §4. ✅ Generalising
  `DeadLetterEntry` with a `source` discriminator costs three optional fields and gives operators
  one place to look for poison messages.

- **Rewrite the A2A surface in place, dropping the pre-v1.0 paths.** ❌ Rejected: anyone using
  varco's A2A today breaks on upgrade. ✅ Mount both, `legacy_paths=True` for one minor release,
  flip the default in the next.

---

## Steps

### Phase 0 — Documentation truth pass (U-8, U-7 record) — no code

Cheapest item in the plan and the one that prevents the next consumer from filing a phantom gap.
The filer notes this is the **third** gap sourced from `ARCHITECTURE.md` that source contradicted.

1. [ ] `ARCHITECTURE.md` (~lines 1196-1231) — rewrite the A2A section: remove "v1 tasks are
       synchronous" and "echo-back, no history stored". State that `SkillAdapter` accepts
       `job_runner` + `job_store` (`router/skill.py:264-266`), that `POST /tasks/send` returns
       `state: working` when they are set, and that `GET /tasks/{task_id}/history` returns turns
       when a `conversation_store` is set. Add a "⚠️ surface predates A2A v1.0.0 — see Phase 7"
       banner naming the mounted paths.
2. [ ] `ARCHITECTURE.md` — MCP section: state plainly that `MCPAdapter` exposes varco routes **as
       an MCP server** and is not an MCP client, per U-10.
3. [ ] `ARCHITECTURE.md` + `varco_redis/README.md` — record that `RedisRateLimiter`
       (`varco_redis/varco_redis/rate_limit.py:169`) exists and is exported; CLAUDE.md's existing
       reference to it is correct and needs no change.
4. [ ] `UPSTREAM-GAPS.md` — append a short "maintainer response" section recording the four source
       corrections above, so the register and the source stop diverging.

**Migration:** none. **Verification:** `rg "echo-back" ARCHITECTURE.md` returns nothing.

---

### Phase 1 — Encryption: arbitrary-principal scoping + destroy semantics (U-1, U-2) — **P0**

Depends on: nothing. Blocks: nothing else in this plan (run it first because it is P0).

**Design.** Generalise the scoping dimension from tenant to an opaque `scope: str`. `scope`
defaults to the `tenant_id` string **verbatim** — so `load_for_scope(t) == load_for_tenant(t)` for
every existing row and **no data migration of existing values is needed**. Downstream picks its own
convention (`f"{tenant}:subject:{sid}"`); varco does not parse scope strings.

Destruction is a **tombstone**, not a delete: the entry's `key_material` is blanked and
`destroyed_at` is set. That is what makes the U-2 §3 read path possible.

5. [ ] `varco_core/varco_core/encryption_store.py` — `EncryptionKeyEntry` gains
       `scope: str | None = None` and `destroyed_at: datetime | None = None`, plus an
       `is_destroyed` property. `__post_init__` (via `object.__setattr__`, the dataclass is frozen)
       defaults `scope` to `tenant_id` when unset. `to_dict()` emits both keys; `from_dict()` reads
       `data.get("scope")` and falls back to `tenant_id` — **old persisted rows deserialize
       unchanged** (this is the back-compat hinge; test it explicitly).
6. [ ] `varco_core/tests/test_encryption_store.py` — failing tests first: `from_dict` on a payload
       with no `"scope"` key yields `scope == tenant_id`; `to_dict`/`from_dict` round-trip with an
       explicit scope; a tombstone entry round-trips with `destroyed_at` and `is_destroyed is True`.
7. [ ] `varco_core/varco_core/encryption_store.py` — `EncryptionKeyStore` Protocol gains three
       methods, alongside (not replacing) the tenant ones:
       ```python
       async def load_for_scope(self, scope: str | None) -> list[EncryptionKeyEntry]: ...
       async def list_scopes(self) -> list[str]: ...
       async def destroy_scope(self, scope: str) -> tuple[str, ...]: ...   # returns destroyed kids
       ```
       `destroy_scope` is the store-level primitive: tombstone every entry for `scope`, return their
       kids. It must be idempotent (a second call returns `()`), and must never delete the tombstone.
8. [ ] `varco_core/varco_core/encryption_store.py` — `InMemoryEncryptionKeyStore` implements all
       three natively.
9. [ ] `varco_core/varco_core/encryption.py` — add:
       ```python
       class KeyDestroyedError(EncryptionError): ...       # near EncryptionError, line ~156

       @dataclass(frozen=True)
       class DestroyReceipt:
           scope: str
           kids: tuple[str, ...]
           destroyed_at: datetime
           actor: str | None = None
           def to_dict(self) -> dict[str, object]: ...
       ```
       ⚠️ The register sketches `DestroyReceipt` as a `NamedTuple`; use a frozen dataclass — repo
       convention (CLAUDE.md: "Frozen `@dataclass(frozen=True)` for all value objects") wins over
       the filer's sketch. Record the deviation in the `DESIGN:` block.
10. [ ] `varco_core/varco_core/encryption.py` — `MultiKeyEncryptorRegistry.destroy(kid)`, distinct
        from the existing `retire(kid)`: `retire` removes a key from primary rotation but **keeps
        decrypt working**; `destroy` records the kid as destroyed so `_unpack_ciphertext` →
        decrypt raises `KeyDestroyedError`. Document the pair in one `DESIGN:` block — the
        distinction is the whole point of U-2.
11. [ ] `varco_core/varco_core/encryption.py` — decrypt path (`_unpack_ciphertext` at `:297-345`
        and its caller): when the unpacked `kid` resolves to a destroyed entry, raise
        `KeyDestroyedError(kid=..., scope=...)`; when it resolves to nothing at all, keep raising
        today's error. These two must stay distinguishable.
12. [ ] `varco_core/varco_core/encryption.py` — `ScopedEncryptorRegistry`, sibling to
        `TenantAwareEncryptorRegistry` (`:526-672`), whose `_resolve(context)` maps context → scope
        instead of context → tenant. `TenantAwareEncryptorRegistry` is left untouched and keeps
        working.
13. [ ] `varco_core/varco_core/encryption.py` — `EncryptionKeyManager` gains:
        ```python
        async def build_scoped_registry(self, scope: str) -> ScopedEncryptorRegistry: ...
        async def rotate_scope(self, scope: str) -> FieldEncryptor: ...
        async def destroy_scope(self, scope: str, *, actor: str | None = None) -> DestroyReceipt: ...
        ```
        `build_scoped_registry` **must load only that scope's keys** — U-1's volume note: per-subject
        keys mean the store grows with data subjects, and an eager all-keys load is fine at 50
        tenants and fatal at 50 000 subjects. Add a test asserting the store sees exactly one
        scoped query.
14. [ ] `varco_core/varco_core/encryption_store.py` — capability shim: `EncryptionKeyManager`
        resolves `load_for_scope`/`list_scopes` via `getattr(store, "load_for_scope", None)` and
        falls back to `load_for_tenant`/`list_tenants` with a **one-time** `logging.warning` naming
        the store class. This is what keeps third-party Protocol implementations working despite
        the widened Protocol (see Compatibility posture).
15. [ ] `varco_core/tests/test_encryption_destroy.py` (new) — failing tests first: destroy returns a
        receipt listing every kid for the scope; a second destroy returns an empty `kids` tuple
        (idempotent); decrypt of ciphertext framed with a destroyed kid raises `KeyDestroyedError`
        and **not** the generic error; decrypt of an unknown kid still raises the generic error;
        destroying scope A leaves scope B decryptable (**the R-045 regression test — this is the
        one that proves the gap is closed**); a store implementing only the tenant methods still
        works through the shim.
16. [ ] `varco_sa/varco_sa/encryption_store.py` — add `scope` (String, **indexed**) and
        `destroyed_at` (DateTime(timezone=True), nullable) columns; implement the three new methods.
        `destroy_scope` is a single `UPDATE … SET key_material='', destroyed_at=now() WHERE scope=:s
        AND destroyed_at IS NULL RETURNING kid`.
17. [ ] `varco_sa` Alembic revision `xxxx_encryption_key_scope` — add both columns nullable, create
        the `scope` index, backfill `UPDATE … SET scope = tenant_id WHERE scope IS NULL`. Existing
        deployments: run the migration; no application change required, behaviour identical until
        `load_for_scope` is called with a non-tenant value.
18. [ ] `varco_redis/varco_redis/encryption_store.py` — implement the three methods; add a scope
        index set (`{prefix}:scope:{scope}` → set of kids) mirroring the existing tenant index.
        `destroy_scope` must be atomic across the entries — use the Lua pattern already established
        in `varco_redis/varco_redis/lock.py`.
19. [ ] `varco_beanie/varco_beanie/encryption_store.py` — add the two document fields, an index on
        `scope`, and the three methods.
20. [ ] Per-backend tests — `varco_sa/tests/test_sa_encryption_store.py`,
        `varco_redis/tests/test_redis_encryption_store.py`,
        `varco_beanie/tests/test_beanie_encryption_store.py`: scope filtering, `list_scopes`,
        tombstone persistence, destroy idempotence, and reading a pre-migration row (scope column
        NULL) yields `scope == tenant_id`. Mark the Redis/Beanie/Postgres round-trips
        `@pytest.mark.integration`.
21. [ ] `technical_docs/features/crypto-shredding.md` (new) — scope model, the tenant-as-a-scope
        default, the destroy/retire distinction, `KeyDestroyedError` handling, key-volume guidance,
        and **the operator obligation U-2 asks for verbatim: key-store backups must not outlive the
        erasure window, or destruction is not destruction.** Also state that varco does not parse
        scope strings, so callers must not embed personal data in a scope (use a pseudonymous id).
22. [ ] `CLAUDE.md` — extend the encryption/CLAUDE section and add pitfall rows: "destroyed key
        renders as corrupt data" → catch `KeyDestroyedError`; "per-subject registry built with
        `build_tenant_registry`" → loads every key, use `build_scoped_registry`.
        `ARCHITECTURE.md` + `varco_core/README.md` — new types in the hierarchy.

**Migration:** yes, `varco_sa` encryption key table (two nullable columns + one index + a backfill).
Redis/Beanie need no migration; the Redis scope index is built lazily on write, so add a documented
`reindex_scopes()` one-shot helper for stores with pre-existing keys.

---

### Phase 2 — Fail-closed JWT verification (U-13) — **P1, security default change**

Depends on: nothing. Independent of every other phase — land it early and separately so the
breaking-default CHANGELOG entry is not buried.

**Design.** Two independent fail-open holes, fixed at their own layers.

23. [ ] `varco_core/tests/test_trusted_issuer_registry.py` — failing tests first: a token signed by
        registered issuer **A** but carrying `iss` of issuer **B** is **rejected**; a token whose
        `iss` matches its resolving issuer is accepted; `enforce_issuer=False` restores today's
        behaviour; `VARCO_JWT_ENFORCE_ISS=false` does the same via env.
24. [ ] `varco_core/varco_core/authority/registry.py` — the kid→key lookup (`get_key`, `:409`)
        currently discards which issuer matched. Add an internal
        `_resolve_key(kid) -> tuple[JsonWebKey, TrustedIssuerEntry] | None` and have `get_key`
        delegate to it (public signature unchanged). `TrustedIssuerEntry.iss` already exists
        (`:260`) and is currently documented as "not enforced here".
25. [ ] `varco_core/varco_core/authority/registry.py` — `verify()` (`:519`) gains
        `enforce_issuer: bool | None = None` (None → `JwtVerificationSettings`, default **True**).
        After signature verification, compare the token's `iss` against the resolved
        `TrustedIssuerEntry.iss`; on mismatch raise the existing invalid-token error type with a
        message naming both values. Delete the "Does NOT enforce the `iss` claim — that is the
        caller's responsibility" paragraph at `:532-534` and `:560-561` and replace it with the new
        contract.
26. [ ] `varco_core/varco_core/jwt/config.py` — `JwtVerificationSettings` gains
        `enforce_issuer: bool = True` (env `VARCO_JWT_ENFORCE_ISS`) and
        `allow_any_audience: bool = False` (env `VARCO_JWT_ALLOW_ANY_AUDIENCE`).
27. [ ] `varco_fastapi/tests/test_server_auth.py` — failing tests first: constructing
        `JwtBearerAuth` with no `audience` and no `VARCO_JWT_AUDIENCE` **raises `ValueError`** whose
        message names both the env var and the opt-out; `allow_any_audience=True` constructs and
        logs one warning; `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` does the same.
28. [ ] `varco_fastapi/varco_fastapi/auth/server_auth.py:157-176` — replace the
        "log a warning and proceed" branch with a `ValueError`, gated by `allow_any_audience`.
        Keep the single-warning behaviour for the opt-out path.
29. [ ] `CLAUDE.md` — update the `VARCO_JWT_*` table: `VARCO_JWT_AUDIENCE` default changes from
        "`None` = not enforced (opt-in hardening)" to "**required** unless
        `VARCO_JWT_ALLOW_ANY_AUDIENCE=true`"; add the two new vars. Update the two pitfall rows
        ("Token from another service accepted") to say the failure is now a startup error.
        `ARCHITECTURE.md` + `technical_docs/features/token-profiles.md` cross-references.
30. [ ] `varco_core/CHANGELOG.md` + `varco_fastapi/CHANGELOG.md` — **BREAKING (security default)**
        entries with the exact env var to set for a one-line rollback, and the rationale: "a service
        that forgets one environment variable accepts a token minted for any audience by any
        registered issuer".

**Migration:** none (no schema). **Deployment note:** a deployment that upgrades without setting
`VARCO_JWT_AUDIENCE` **fails to start**. That is the intent — a startup failure is the control a
log warning was not. Document the rollback env var prominently in the release notes.

---

### Phase 3 — Shared DLQ concept + outbox relay resilience (U-6) — **P1**

Depends on: nothing. **Blocks Phase 4** (the job store reuses the generalised `DeadLetterEntry`).

**Design.** The DLQ machinery exists and is correctly scoped per-subscription
(`varco_core/varco_core/event/consumer.py:268-277` already takes `retry_policy=` and `dlq=`) — the
register's re-scope is right. What is missing is (a) the relay leg, (b) safe-by-default wiring for
`AuditConsumer`, (c) a `DeadLetterEntry` shape that can carry a non-event failure.

31. [ ] `varco_core/tests/test_dlq.py` — failing tests first: a `DeadLetterEntry` constructed with
        today's exact keyword set still works (back-compat); a relay-sourced entry with
        `event=None` and a raw `payload` round-trips; `source` defaults to `CONSUMER`.
32. [ ] `varco_core/varco_core/event/dlq.py:82-149` — generalise `DeadLetterEntry`, **additively**:
        ```python
        class DeadLetterSource(StrEnum):
            CONSUMER = "consumer"
            OUTBOX_RELAY = "outbox_relay"
            JOB = "job"

        # DeadLetterEntry gains, all defaulted:
        source: DeadLetterSource = DeadLetterSource.CONSUMER
        source_ref: str | None = None      # outbox entry_id / job_id, as str
        payload: bytes | None = None       # raw bytes when `event` could not be deserialized
        ```
        and `event: DomainEvent` becomes `DomainEvent | None`. Every existing construction site and
        `InMemoryDeadLetterQueue` (`:298-451`) keep working untouched.
        Re-state the ABC contract in the docstring: **`push()` must never raise** (CLAUDE.md rule) —
        now doubly load-bearing because the relay and the job runner also cannot recover from a DLQ
        failure.
33. [ ] `varco_core/varco_core/resilience/retry.py` — **verify then act**: read the jitter
        implementation (~`:150-223`) and document the exact formula in `RetryPolicy.jitter`'s
        docstring (U-6 §4 — "does not say *which* formula"). If it is not Full Jitter
        (`sleep = random(0, backoff)`, AWS's published recommendation), add
        `jitter_strategy: Literal["full", "equal", "none"]` **defaulting to the current behaviour**
        so nobody's timing changes; if it already is Full Jitter, only the docstring changes.
34. [ ] `varco_core/varco_core/resilience/retry.py` — add a named preset:
        ```python
        @classmethod
        def durable_delivery(cls) -> RetryPolicy:   # max_attempts=20, base_delay=15.0,
            ...                                     # max_delay=3600.0, jitter=True
        ```
        U-6 §3 says the shipped `max_attempts=3` default (≈7 s total) is poor for durable delivery
        but is **"report, do not request"** — so **do not change the global default**. The preset
        gives the relay and `AuditConsumer` a sane default without touching anyone else, and its
        docstring carries the Oban-20/Sidekiq-25 comparison.
35. [ ] `varco_core/tests/test_outbox.py` — failing tests first: with no `retry_policy`, relay
        behaviour is **byte-identical to today** (entry left in place, logged, retried next tick);
        with a policy, `attempts` increments and `next_attempt_at` moves forward; an entry whose
        `next_attempt_at` is in the future is skipped; after `max_attempts` the entry is pushed to
        the DLQ **and deleted** so the stream unblocks; constructing `OutboxRelay` with
        `max_attempts` but no `dlq` raises `ValueError`; a repository that does not implement
        `mark_failed` degrades to today's behaviour with one warning.
36. [ ] `varco_core/varco_core/service/outbox.py:167-182` — `OutboxEntry` gains
        `attempts: int = 0`, `last_error: str | None = None`,
        `next_attempt_at: datetime | None = None`. All defaulted; `from_event()` unchanged.
37. [ ] `varco_core/varco_core/service/outbox.py` — `OutboxRepository` gains a **concrete** (not
        abstract) method:
        ```python
        async def mark_failed(self, entry_id: UUID, *, attempts: int,
                              next_attempt_at: datetime | None, error: str) -> None:
        ```
        Default implementation logs one warning per repository class ("does not support attempt
        tracking; relay falls back to unbounded retry") and returns. This is what keeps external
        `OutboxRepository` subclasses working.
38. [ ] `varco_core/varco_core/service/outbox.py:583-654` — `OutboxRelay.__init__` gains
        `retry_policy: RetryPolicy | None = None`, `dlq: AbstractDeadLetterQueue | None = None`,
        `max_attempts: int | None = None`. `_relay_entry()`:
        - the existing "failed to deserialize → delete" branch (`:600-621`) now **dead-letters
          first** (`source=OUTBOX_RELAY`, `event=None`, `payload=entry.payload`) when a `dlq` is
          wired, then deletes. Today it deletes silently — that is a real, unreported loss.
        - the publish-failure branch (`:625-640`) increments `attempts`, computes
          `next_attempt_at = now + policy.backoff(attempts)` and calls `mark_failed`; when
          `attempts >= max_attempts`, pushes to the DLQ and **deletes the entry**.
        - `_relay_once()` (`:549`) client-side-filters entries whose `next_attempt_at > now` — a
          universal fallback that needs no `get_pending` signature change and therefore works with
          every existing repository.
        - `ValueError` at construction if `max_attempts` is set without a `dlq`: deleting a poison
          entry with nowhere to put it is silent data loss, so refuse the configuration.
        `DESIGN:` block must state why dead-lettering **deletes**: per-tenant FIFO means a single
        poison row stops the whole stream, which is the failure U-6 §1 names.
39. [ ] `varco_core/varco_core/event/consumer.py` — `EventConsumer.register_to(bus)` gains
        `retry_policy: RetryPolicy | None = None`, `dlq: AbstractDeadLetterQueue | None = None`,
        applied **only to subscriptions that declared neither** in their `@listen`. Rationale: the
        `@listen` decorator binds at class-definition time, so an instance cannot otherwise supply a
        policy; `register_to` is already the imperative wiring seam (CLAUDE.md layer rule).
40. [ ] `varco_core/varco_core/service/audit.py` — `AuditConsumer` gains a class attribute
        `_default_retry_policy = RetryPolicy.durable_delivery()` and `__init__(..., dlq=None)`;
        `register_to` passes both unless the caller overrode them. U-6 §2: "safe-by-default is the
        right polarity for an audit trail", with fire-and-forget as the explicit opt-out
        (`retry_policy=None` passed deliberately). Update the class docstring — CLAUDE.md currently
        says `AuditConsumer` "ships with no `retry_policy`/`dlq`"; that line changes.
41. [ ] `varco_core/tests/test_audit.py` — failing test: a handler that raises twice then succeeds
        is retried by default; a handler that always raises lands in the DLQ with
        `source=CONSUMER`; passing `retry_policy=None` explicitly restores fire-and-forget.
42. [ ] **(Optional, deferrable — deliberate scope addition.)** `varco_sa/varco_sa/dlq.py` (new) —
        `SADeadLetterQueue(AbstractDeadLetterQueue)` over a `varco_dead_letters` table
        (`entry_id`, `source`, `source_ref`, `channel`, `handler_name`, `event_type`, `payload`,
        `error_type`, `error_message`, `attempts`, `first_failed_at`, `last_failed_at`), plus its
        Alembic revision, and `push()` that swallows-and-logs per the ABC contract.
        ⚠️ **Scope note:** U-6 explicitly says a durable DLQ is *not* an upstream ask — the filer
        builds their own over the ABC. We ship one anyway because varco's own relay (Step 38) now
        needs somewhere durable to put poison entries, and the only shipped implementation is a
        `deque(maxlen=10_000)` lost on restart (`dlq.py:298-451`). ✅ Makes Step 38 useful out of
        the box and does not change what the filer asked for. ❌ New table, new migration, ~150
        lines. **If the release needs to be smaller, cut this step** — nothing else depends on it.
43. [ ] `technical_docs/features/dead-letter-queues.md` (new) — the one-DLQ-three-sources model,
        the relay's retry/dead-letter path, `durable_delivery()` and why `max_attempts=3` is wrong
        for durable delivery, and the `push()` must-never-raise contract.
        `CLAUDE.md` — update the DLQ and outbox sections and the `AuditConsumer` sentence; add
        pitfall rows: "poison outbox row silently stops a stream" → wire `retry_policy` + `dlq`;
        "`max_attempts` without a `dlq`" → `ValueError` by design.
        `ARCHITECTURE.md` + `varco_core/README.md`.

**Migration:** only if Step 42 is taken (new `varco_dead_letters` table). The `OutboxEntry` fields
are in-memory only unless the SA outbox table is extended — extend it in the same optional revision:
`attempts INT NOT NULL DEFAULT 0`, `last_error TEXT NULL`, `next_attempt_at TIMESTAMPTZ NULL`.
Without that revision, `mark_failed` no-ops and the relay behaves as it does today.

---

### Phase 4 — Job store: time dimension, lease, fencing (U-17 + U-11) — **P1**

Depends on: **Phase 3** (`DeadLetterSource.JOB`, `RetryPolicy.durable_delivery`).
**One migration** covering U-17, U-11 **and** the columns Phase 6 needs for U-18/U-19.

**Design.** Both gaps rewrite the same `WHERE` clause
(`varco_sa/varco_sa/job_store.py:423-446`) and add columns to the same table (`:83-108`). Doing
them separately means two migrations and two rewrites of the most safety-critical query in the
package. Every new field is nullable or defaulted so that `run_at IS NULL` claims immediately,
`lease_ttl=None` takes no lease, and `max_attempts=1` fails terminally — i.e. **an unchanged
caller gets today's behaviour exactly**.

⚠️ There are **four** `AbstractJobStore` implementations, all of which must follow:
`varco_sa/varco_sa/job_store.py:388`, `varco_redis/varco_redis/job_store.py:377`,
`varco_beanie/varco_beanie/job_store.py:498`, and `varco_fastapi/varco_fastapi/job/store.py:154`
(`InMemoryJobStore`).

44. [ ] `varco_core/tests/test_job.py` — failing tests first: `JobStatus.DEAD.is_terminal is True`;
        `Job.as_retry(next_run_at)` returns PENDING with `run_at` set and `attempt` incremented;
        `Job.as_dead(error)` is terminal; a `Job` constructed with no new kwargs is field-for-field
        equal to today's.
45. [ ] `varco_core/varco_core/job/base.py:81-108` — `JobStatus` gains `DEAD = "dead"`; add it to
        `is_terminal`. Update the state-machine diagram in the docstring.
46. [ ] `varco_core/varco_core/job/base.py:114-197` — `Job` gains, all defaulted:
        ```python
        run_at: datetime | None = None            # U-17 §1
        attempt: int = 0                          # U-17 §3
        max_attempts: int = 1                     # U-17 §3 — 1 == today's terminal-on-failure
        owner_id: str | None = None               # U-11
        lease_expires_at: datetime | None = None  # U-11
        lease_epoch: int = 0                      # U-11 fencing token
        expires_at: datetime | None = None        # U-18 (column now, API in Phase 6)
        request_issuer: str | None = None         # U-19 (column now, API in Phase 6)
        request_subject: str | None = None        # U-19
        request_token_hash: str | None = None     # U-19
        ```
        plus transitions `as_retry(next_run_at)` and `as_dead(error)`.
47. [ ] `varco_core/varco_core/job/base.py:517` — `try_claim` gains keyword-only
        `owner_id: str | None = None`, `lease_ttl: float | None = None`. **This is an addition, not
        the activation of a dormant parameter** — see Source correction 1. Document that external
        `AbstractJobStore` subclasses must add the kwargs before enabling leases, and that callers
        that never pass them are unaffected.
48. [ ] `varco_core/varco_core/job/base.py` — new `AbstractJobStore` methods, **concrete on the ABC**
        (not `@abstractmethod`) so external subclasses keep importing:
        ```python
        async def claim_next(self, *, owner_id: str | None = None,
                             lease_ttl: float | None = None,
                             now: datetime | None = None) -> Job | None: ...
            # claims the oldest eligible PENDING row honouring
            #   AND (run_at IS NULL OR run_at <= now)
            # default impl: list_by_status(PENDING) + try_claim loop (correct, slower)

        async def renew(self, job_id: UUID, *, owner_id: str, epoch: int,
                        lease_ttl: float) -> Job | None: ...
            # heartbeat; returns None when the epoch is stale (fenced out)
            # default impl: raise NotImplementedError("<cls> does not support leases")

        async def reap_expired_leases(self, *, now: datetime | None = None,
                                      limit: int = 100) -> list[Job]: ...
            # RUNNING rows whose lease_expires_at <= now → PENDING, lease_epoch += 1
            # default impl: raise NotImplementedError("<cls> does not support leases")
        ```
        `renew`/`reap_expired_leases` raise rather than silently degrade: there is no correct
        fallback for a lease, and a silent no-op heartbeat is worse than an error.
49. [ ] `varco_core/varco_core/job/base.py` — fencing on writes: `update`/completion methods gain
        keyword-only `expected_epoch: int | None = None`; when supplied and the stored
        `lease_epoch` differs, the write is refused. Add `StaleLeaseError` to the job module's
        exceptions. This is the Kleppmann point U-11 §3 makes: a claimant that stalls past its
        window and resumes must be rejected **at the point of write**, not at claim time.
50. [ ] `varco_core/varco_core/job/base.py:594-600` — `AbstractJobRunner.enqueue` gains keyword-only
        `run_at: datetime | None = None` and `delay: timedelta | None = None` (mutually exclusive;
        `ValueError` if both) — U-17 §2, a pass-through to the `Job` field.
51. [ ] `varco_sa/tests/test_sa_job_store.py` — failing tests first: a job with
        `run_at = now + 60s` is **not** claimed; the same job is claimed after the clock passes;
        `try_claim(lease_ttl=30)` sets `lease_expires_at` and returns `lease_epoch`;
        `renew` with the current epoch extends the lease; `renew` with a stale epoch returns `None`;
        a write with `expected_epoch` stale raises `StaleLeaseError`; `reap_expired_leases` moves an
        expired RUNNING row to PENDING with `lease_epoch` incremented; a job with all new fields
        defaulted claims exactly as it does today.
52. [ ] `varco_sa/varco_sa/job_store.py:83-108` — add the ten columns from Step 46, and **three
        indexes that do not exist today**:
        - `ix_varco_jobs_claim (status, run_at, created_at)` — supports the new claim predicate.
          ⚠️ Note there is currently **no index at all** on `status`, so today's claim query is a
          sequential scan; this is a free performance fix riding the same migration.
        - `ix_varco_jobs_lease (status, lease_expires_at)` — for `reap_expired_leases`.
        - `ix_varco_jobs_expires (expires_at)` — for Phase 6's retention sweep.
53. [ ] `varco_sa/varco_sa/job_store.py:388-457` — rewrite `try_claim` / add `claim_next`:
        add `AND (run_at IS NULL OR run_at <= :now)` to the existing
        `SELECT … FOR UPDATE SKIP LOCKED`; set `owner_id`, `lease_expires_at = now + lease_ttl`,
        `lease_epoch = lease_epoch + 1` in the UPDATE when `lease_ttl` is given. Keep the existing
        Postgres/SQLite branch split intact (SQLite keeps plain SELECT+UPDATE; the lease columns
        work identically there). Add `renew` and `reap_expired_leases` as single atomic UPDATEs.
54. [ ] `varco_sa` Alembic revision `xxxx_job_lease_schedule_retention` — **the one migration**: all
        ten columns nullable or server-defaulted (`lease_epoch` default `0`, `attempt` default `0`,
        `max_attempts` default `1`), plus the three indexes. No backfill needed — existing rows are
        valid as-is. **Existing deployments:** run the migration and nothing changes, because every
        new behaviour requires a caller to opt in (pass `lease_ttl`, set `run_at`, set
        `max_attempts > 1`). Build the indexes `CONCURRENTLY` on Postgres for a live table and note
        that in the revision's docstring.
55. [ ] `varco_redis/varco_redis/job_store.py:377` + `varco_beanie/varco_beanie/job_store.py:498`
        + `varco_fastapi/varco_fastapi/job/store.py:154` — serialise the new fields and implement
        `claim_next`/`renew`/`reap_expired_leases`. Redis: extend the existing atomic claim script
        (Lua, per `varco_redis/varco_redis/lock.py`'s pattern) with the time predicate and lease
        write; add a sorted set keyed by `run_at` so `claim_next` does not scan. Beanie: a
        `find_one_and_update` with the time predicate. InMemory: under the existing `asyncio.Lock`.
56. [ ] `varco_redis/tests/test_redis_job_store.py`, `varco_beanie/tests/test_beanie_job_store.py`,
        `varco_fastapi/tests/` — the Step 51 test matrix per backend. Mark the Redis/Beanie/Postgres
        concurrency tests `@pytest.mark.integration`; the decisive one is **two concurrent claimers,
        exactly one wins, the loser's `expected_epoch` write is refused**.
57. [ ] `varco_fastapi/varco_fastapi/job/runner.py` — retry binding (U-17 §3): on job failure, if
        `attempt + 1 < max_attempts`, transition to PENDING with
        `run_at = now + policy.backoff(attempt)` via `Job.as_retry`; otherwise terminal `FAILED`,
        or `DEAD` + DLQ push (`source=JOB`, `source_ref=str(job_id)`) when a `dlq` is wired.
        `JobRunner.__init__` gains `retry_policy: RetryPolicy | None = None` and
        `dlq: AbstractDeadLetterQueue | None = None` — `None` reproduces today's terminal-FAILED
        behaviour exactly. **This reuses `varco_core.resilience.RetryPolicy` rather than inventing a
        second retry model**, which is U-17 §3's explicit ask.
58. [ ] `varco_fastapi/varco_fastapi/job/poller.py` — `JobPoller` gains `lease_aware: bool = True`.
        When the store supports `reap_expired_leases`, detect death by **lease expiry** and return
        reaped jobs to PENDING (with the epoch bumped, fencing the stale owner) instead of marking
        them FAILED by wall-clock age. Fall back to today's age threshold when the store raises
        `NotImplementedError`. This is U-11 §2: wall-clock age is "correct for short jobs, wrong for
        anything whose legitimate duration can exceed the threshold".
59. [ ] `varco_fastapi/tests/` — `JobPoller` reaps an expired lease to PENDING and does not touch a
        RUNNING job with a live lease, however old it is (**the regression test for the wall-clock
        bug**); a store without lease support still uses the age threshold.
60. [ ] `technical_docs/features/job-scheduling-and-leases.md` (new) — `run_at`/delay, the retry
        binding, the lease/heartbeat/fencing model, and **the TTL-vs-heartbeat guidance U-11 asks
        for verbatim: TTL ≥ 3× heartbeat interval plus 2× worst-case pause, renewal jittered at
        50–75% of remaining TTL.** Include the "recurrence is expressible as re-enqueue with
        `run_at = now + interval`" recipe and state that cron is deliberately not shipped.
61. [ ] `CLAUDE.md` — new job section; pitfall rows: "long job killed at 5 minutes" → enable leases;
        "stalled worker resumes and overwrites" → pass `expected_epoch`; "external `AbstractJobStore`
        breaks on `lease_ttl`" → add the kwargs.
        `ARCHITECTURE.md` + `varco_core/README.md` + `varco_sa/README.md` (migration note).

**Migration:** **yes — one Alembic revision, and it is the only job-table migration in this plan.**
Phase 6's U-18/U-19 code lands against columns already added here.

---

### Phase 5 — Transaction-scoped Postgres advisory lock (U-16) — **P1**

Depends on: nothing (independent of Phase 4, but pairs naturally with it — both are `varco_sa`
correctness work and can ship in one release).

**Design.** `SAAdvisoryLock` uses the **session-level** pair `pg_try_advisory_lock` /
`pg_advisory_unlock` (`varco_sa/varco_sa/advisory_lock.py:6-32`, `:166-200`) and its own design note
at `:47` states the assumption: *"each process holds its own advisory lock via its own connection"*
— direct connections, not a pooler. Behind PgBouncer in transaction mode, `release()` runs on a
different server connection, silently returns false, and the lock leaks. Add the transaction-scoped
sibling; do not change the existing class's behaviour.

62. [ ] `varco_sa/tests/test_sa_xact_advisory_lock.py` (new, `@pytest.mark.integration` — advisory
        locks are Postgres-only) — failing tests first: two sessions contend and exactly one
        acquires; the lock is released **at commit with no `release()` call**; released at rollback;
        `xact()` re-entered on the same session behaves per Postgres semantics (document what that
        is); `try_acquire`/`release` via the ABC round-trips.
63. [ ] `varco_sa/varco_sa/advisory_lock.py` — add `SAXactAdvisoryLock` in the **same module**
        (discoverability: anyone reading the session-scoped class sees the sibling), reusing
        `_key_to_int64` (`:78`) unchanged:
        ```python
        @asynccontextmanager
        async def xact(self, key: str, session: AsyncSession) -> AsyncIterator[bool]: ...
            # SELECT pg_try_advisory_xact_lock(:key_int) on the CALLER's session;
            # yields True/False; released by the caller's COMMIT/ROLLBACK. Primary API.
        ```
        Also implement `AbstractDistributedLock` (`varco_core/varco_core/lock.py:218`) so the ABC
        can be bound and downstream can delete its local call — the filer's stated goal.
        `try_acquire(key, *, ttl)` opens and holds its own transaction; `release(key, token)` commits
        it. ❌ That pins one pooled connection for the lock's lifetime — **document this cost
        explicitly** and point callers at `xact()` as the default. `ttl` is meaningless here (the
        transaction bounds the lock): say so in the docstring rather than silently ignoring it, which
        is the exact defect U-11 flagged on the sibling class.
64. [ ] `varco_sa/varco_sa/advisory_lock.py:6-32, :47, :179-186` — **documentation-only change to
        the existing class**: a prominent warning naming **transaction pooling (PgBouncer
        `pool_mode=transaction`) as an unsupported topology**, spelling out the four-step failure
        (acquire on A → A returned to pool → `release()` routed to B → returns false, lock leaks on
        A, next borrower of A inherits it), and pointing at `SAXactAdvisoryLock`. Also correct the
        `ttl` docstring at `:179-186` to state the ignored-parameter fact up front. The filer's own
        assessment: this alone "converts a silent leak into a known constraint".
        **No runtime warning** — it would be noise for the many correctly-deployed direct-connection
        users.
65. [ ] `varco_sa/varco_sa/di.py` — register `SAXactAdvisoryLock` so `AbstractDistributedLock` can be
        bound to it; keep `SAAdvisoryLock` as the existing binding to avoid changing behaviour on
        upgrade, and document how to override (per CLAUDE.md: `provide()` before `install()`/`scan()`,
        or `@Provider(priority=100)`).
66. [ ] `CLAUDE.md` pitfall row: "`release()` returns false and the lock leaks" → session-scoped lock
        behind a transaction pooler, use `SAXactAdvisoryLock`.
        `ARCHITECTURE.md` + `varco_sa/README.md`;
        `technical_docs/features/distributed-locks.md` (new) — the session-vs-transaction table, the
        pooling matrix, and why `RedisLock` is not an option under an air-gapped/no-Redis constraint.

**Migration:** none.

---

### Phase 6 — Job hygiene: retention + token reference shape (U-18, U-19) — **P2 / P1-report**

Depends on: **Phase 4** (all columns already exist; this phase is API + behaviour only, **no
migration**).

Both are filed as reports rather than blocking requests. U-19 in particular is "report, not
request" — plan it as a **small self-contained addition**, not a redesign of the auth snapshot.

67. [ ] `varco_core/tests/test_job_store_retention.py` (new) — failing tests first: `delete_where`
        with `status=COMPLETED, completed_before=T` removes exactly the matching rows and returns
        the count; `limit=N` deletes at most N and returns N (so callers can loop); no predicate at
        all raises `ValueError` (refuse to truncate the table by accident).
68. [ ] `varco_core/varco_core/job/base.py:503-514` — add alongside `delete(job_id)`:
        ```python
        async def delete_where(self, *, status: JobStatus | Sequence[JobStatus] | None = None,
                               completed_before: datetime | None = None,
                               expires_before: datetime | None = None,
                               limit: int | None = None) -> int: ...
        ```
        Concrete on the ABC with a portable default over `list_by_status` + `delete` (correct, just
        slower) so external stores keep working. `limit` exists specifically for the pool-pressure
        finding the filer added in their 2026-08-09 update: id-at-a-time enumeration pins a server
        connection for the whole sweep under transaction pooling. Document the **chunked-sweep
        recipe** — loop `delete_where(..., limit=1000)` in bounded transactions until it returns 0.
69. [ ] `varco_sa/varco_sa/job_store.py:373-386` — native `delete_where` as a single
        `DELETE … WHERE … ` (with `ctid IN (SELECT … LIMIT n)` on Postgres for the `limit` form,
        which is the index-friendly shape given `ix_varco_jobs_expires`).
        Mirror in `varco_redis`, `varco_beanie`, `InMemoryJobStore`.
70. [ ] `varco_fastapi/varco_fastapi/job/poller.py` — optional `expires_at` sweep: when
        `retention_sweep=True`, each tick calls `delete_where(expires_before=now, limit=...)`.
        Default `False` — no deployment starts deleting rows on upgrade.
71. [ ] `varco_core/varco_core/job/base.py:138, :188` — U-19: document `request_token` as
        **discouraged** (docstring only, no `DeprecationWarning`, no removal scheduled — matching
        how `JwtUtil.SYSTEM_ISSUER` was handled), citing the OWASP/NIST finding that a JWT is
        base64-encoded, not encrypted, so any PII in its claims is readable at rest. Point at the
        `request_issuer` / `request_subject` / `request_token_hash` reference fields (Phase 4,
        Step 46) as the recommended shape.
72. [ ] `varco_core/varco_core/job/base.py:704-727` — the `Job` factory gains
        `store_raw_token: bool = True`. When `False`, populate the three reference fields
        (`request_token_hash` = sha256 hex of the raw token) and leave `request_token` unset.
        ⚠️ **The default must stay `True`** — see Source correction 4:
        `varco_fastapi/varco_fastapi/job/runner.py:784-786` forwards `job.request_token` as
        `Authorization: Bearer` on the completion callback, so flipping the default would silently
        break callback auth.
73. [ ] `varco_fastapi/varco_fastapi/router/base.py:1567-1582` and
        `varco_fastapi/varco_fastapi/job/runner.py:335-382` — thread `store_raw_token` through the
        auto-population path, defaulting `True`. Document that setting it `False` requires the
        callback to authenticate with a service credential instead of replaying the caller's token —
        which also removes a token-replay surface.
74. [ ] `varco_core/tests/` — `store_raw_token=False` leaves `request_token` None and populates the
        three reference fields; the hash is stable and does not contain the token; the default path
        is unchanged.
75. [ ] `technical_docs/features/job-scheduling-and-leases.md` — add retention (chunked sweep
        recipe, the pool-pressure rationale) and the credential-at-rest section.
        `CLAUDE.md` pitfall rows: "retention sweep starves the pool" → chunk with `limit=`;
        "raw JWT readable in the jobs table" → `store_raw_token=False` + service-credential
        callbacks. `ARCHITECTURE.md`.

**Migration:** none — Phase 4's revision already added `expires_at`, `request_issuer`,
`request_subject`, `request_token_hash`.

---

### Phase 7 — A2A v1.0.0 surface + `SkillSource` (U-3 + U-4 as ONE piece) — **P2**

Depends on: Phase 0 (docs). The filer is explicit: *"U-3 and U-4 are one piece of work upstream,
not two"* — the redesign must be done **against the v1.0 surface**, not retrofitted afterwards.
Neither blocks the filer any more (they adopted `a2a-sdk` directly), so this is roadmap work — but
U-4 is a correctness bug against a released spec affecting **anyone** using varco's A2A surface today.

**Design.** Two changes that must land together: (a) decouple the adapter's *subject* from
`VarcoRouter` introspection, (b) move the *protocol* to v1.0.0. Both parts of the current surface
stay mounted for one minor release.

76. [ ] `varco_fastapi/tests/test_a2a_v1.py` (new) — failing tests first: `GET
        /.well-known/agent-card.json` returns a card with capability flags **nested inside a
        `capabilities` object** and **no top-level `id`**; JSON-RPC dispatch for `message/send`,
        `tasks/get`, `tasks/list`, `tasks/cancel`; JSON-RPC error envelopes for unknown method and
        bad params; the legacy paths still answer; a custom `SkillSource` reaches `invoke` with the
        verified caller's `AuthContext`.
77. [ ] `varco_fastapi/varco_fastapi/router/a2a/source.py` (new) — `SkillDefinition`,
        `AgentMetadata`, and:
        ```python
        @runtime_checkable
        class SkillSource(Protocol):
            def skills(self) -> list[SkillDefinition]: ...
            def agent_metadata(self) -> AgentMetadata: ...
            async def invoke(self, skill_id: str, payload: dict[str, Any], *,
                             ctx: AuthContext | None = None) -> Any: ...
        ```
        `ctx` is U-3's per-request auth passthrough: the adapter must surface the verified caller
        identity so the three caller classes (end user / another agent / integrating platform) are
        distinguishable in the audit trail.
78. [ ] `varco_fastapi/varco_fastapi/router/a2a/router_source.py` (new) — `RouterSkillSource`,
        today's behaviour extracted **verbatim** from `skill.py:326` (`introspect_routes`,
        `_auto_skill_id`, `_resolve_description`). No behaviour change; the existing
        `test_skill_adapter.py` must stay green against it.
79. [ ] `varco_fastapi/varco_fastapi/router/a2a/card.py` (new) — the v1.0.0 Agent Card model:
        capability flags nested under `capabilities`, no top-level `id`, per the spec citation in
        U-4.
80. [ ] `varco_fastapi/varco_fastapi/router/a2a/jsonrpc.py` (new) — JSON-RPC 2.0 envelope +
        dispatch for `message/send`, `message/stream`, `tasks/get`, `tasks/list`, `tasks/cancel`,
        `tasks/resubscribe`, mapping onto the existing async machinery (`job_runner`/`job_store`
        already wired at `skill.py:264-266`) and the v1 task states
        (`submitted`/`working`/`completed`/`failed`/`canceled`).
81. [ ] `varco_fastapi/varco_fastapi/router/skill.py:254-267` — `SkillAdapter.__init__` gains
        `source: SkillSource | None = None`; exactly one of `router_cls` / `source` is required
        (`ValueError` otherwise). `router_cls` stays **positional and supported**, wrapped into a
        `RouterSkillSource`; `.router_class` (`:368-370`) keeps working and returns `None` for a
        non-router source. Add `skills=` to accept **author-supplied `SkillDefinition` objects
        verbatim** — U-3's R-039 ask: hand-written skill text must reach the Agent Card unaltered,
        not be regenerated from route names.
82. [ ] `varco_fastapi/varco_fastapi/router/skill.py:693-857` — `mount()` gains
        `legacy_paths: bool = True`. Mount the v1 surface (`GET /.well-known/agent-card.json` +
        the JSON-RPC endpoint) always; mount `GET /.well-known/agent.json`, `POST /tasks/send`,
        `GET /tasks/{task_id}`, `GET /tasks/{task_id}/history` only when `legacy_paths=True`.
        Flip the default to `False` in the following minor release; log one deprecation warning per
        mount when the legacy paths are served.
83. [ ] `technical_docs/features/a2a-surface.md` (new) — v1.0.0 paths and methods, the
        `SkillSource` protocol with a non-router example, the auth-passthrough contract, the async
        task lifecycle (which **already worked** — Source correction 2), and the legacy-path
        deprecation timeline.
        `ARCHITECTURE.md` — second pass over the A2A section (Phase 0 fixed the false claims; this
        rewrites it for the new surface). `CLAUDE.md` scenario: "expose a non-router subject over
        A2A". `varco_fastapi/README.md`.

**Migration:** none (HTTP surface only). **Deployment note:** existing A2A clients keep working for
one minor release; the release notes must name the flip.

---

### Phase 8 — Reports and small additions (U-5, U-7) — **P2**

Both are filed as reports. U-5 is explicitly "report, not request — we build it ourselves";
U-7's main leg is **already shipped**. Keep both small and self-contained; do not redesign the
tenancy layer.

84. [ ] `technical_docs/features/postgres-rls.md` (new) — the report, which the filer says is now
        more valuable than when filed. Must contain, in this order:
        1. 🔑 **The InitPlan finding.** `current_setting()` is volatile and **not LEAKPROOF**, so the
           obvious policy form `USING (tenant_id = current_setting('rls.tenant_id')::uuid)` blocks
           index usage and forces a sequential scan — one documented query went **8 100 ms → 94 ms**
           purely by wrapping it: `USING (tenant_id = (SELECT current_setting('rls.tenant_id', true)::uuid))`.
           **Any varco RLS helper must emit the `(SELECT …)` form.** Invisible at test data volumes,
           catastrophic in production.
        2. `SET LOCAL` vs `SET`: transaction-scoped `set_config(..., local => true)` is
           PgBouncer-transaction-mode safe; session-scoped is not — the same defect class as
           U-16's session-scoped lock.
        3. The `search_path` hazard: `varco_sa/varco_sa/connection.py:236` sets `search_path`
           **once at connection init** from a deployment-wide setting. Correct for one schema per
           install; unsafe as a schema-per-tenant routing mechanism, because `search_path` is
           session state on a pooled connection. If varco ever adds schema-per-tenant it needs
           `SET LOCAL search_path` or SQLAlchemy's `schema_translate_map` (no `schema_translate_map`
           usage exists anywhere in the codebase today).
        4. `TenantAwareService._scoped_params` (`varco_core/varco_core/service/tenant.py:424`)
           **fails open** — any query path bypassing the mixin returns cross-tenant rows. State it
           plainly; it is the whole point.
85. [ ] `varco_sa/varco_sa/rls.py` (new, small and self-contained — helpers only, no wiring):
        ```python
        def enable_rls_ddl(table: str, *, tenant_column: str = "tenant_id",
                           setting: str = "rls.tenant_id") -> list[str]: ...
            # returns the DDL strings for use in an Alembic revision;
            # MUST emit the (SELECT current_setting(..., true)) InitPlan form
        async def set_tenant_local(session: AsyncSession, tenant_id: str) -> None: ...
            # SELECT set_config(:setting, :value, true)  — transaction-scoped
        ```
        Nothing is applied to generated tables by default — RLS stays opt-in per table.
86. [ ] `varco_sa/tests/test_rls.py` — unit: `enable_rls_ddl` output **contains the literal
        `(SELECT `** (the regression test for the 150× cliff) and the `, true` missing-ok flag.
        Integration (`-m integration`, Postgres): with the policy applied, a session that has not
        called `set_tenant_local` sees **zero** rows; after `set_tenant_local(t)` it sees exactly
        tenant `t`'s rows; the setting does not survive the transaction.
87. [ ] **(Optional, deferrable.)** `varco_sa` `SchemaGuard` — add `check_rls_coverage()`: report
        tables that have a `tenant_id` column but no RLS policy. Drift check only, never fails the
        boot by default.
88. [ ] `varco_redis/varco_redis/bulkhead.py` (new) — U-7's only remaining leg. `RedisBulkhead`
        implementing the existing `Bulkhead` interface as a distributed semaphore: a Redis sorted
        set of holders scored by acquisition time, with TTL-based eviction so a crashed holder's
        slot is reclaimed; acquire/release atomic via Lua, mirroring
        `varco_redis/varco_redis/lock.py`. Concurrency limiting is a **different primitive** from
        rate limiting, which is exactly what U-7's second paragraph says.
89. [ ] `varco_redis/tests/test_redis_bulkhead.py` (`-m integration`) — N+1 concurrent acquirers,
        the (N+1)th waits or fails per config; a holder that dies without releasing has its slot
        reclaimed after the TTL; export and DI registration resolve.
90. [ ] `CLAUDE.md` — add `RedisBulkhead` alongside the existing `RedisRateLimiter` guidance
        ("`InMemoryRateLimiter` in multi-pod" pitfall row gets a concurrency sibling); note that
        the rate-limiter leg of U-7 was already shipped.
        `ARCHITECTURE.md` + `varco_redis/README.md` + `varco_sa/README.md` (RLS helpers).

**Migration:** none. RLS policies are opt-in per table, applied by the *application's* Alembic
revisions using the helper, never by varco.

---

## Edge cases

**Phase 1 — encryption**
- Persisted key row written before the migration (no `scope` key in its dict) → `from_dict` yields
  `scope == tenant_id`; `load_for_scope(tenant_id)` finds it.
- `tenant_id=None` (global/app-level key) → `scope` stays `None`; `list_scopes()` excludes it, as
  `list_tenants()` already excludes `None`.
- `destroy_scope` on an unknown scope → `DestroyReceipt` with `kids=()`, not an error.
- `destroy_scope` called twice → second receipt has `kids=()`; tombstones are never re-destroyed.
- Decrypt of ciphertext whose kid was **retired** (not destroyed) → still decrypts. Only `destroy`
  makes it raise. This is the distinction Step 10 exists for.
- Decrypt of a kid that never existed → today's generic error, **not** `KeyDestroyedError`.
- A third-party store implementing only `load_for_tenant` → shim path, one warning, works.

**Phase 2 — JWT**
- Token signed by registered issuer A carrying `iss` of registered issuer B → **rejected**.
- Two registered issuers sharing a `kid` (misconfiguration) → first match wins for the key, and the
  `iss` check now catches the mismatch that previously passed silently.
- Locally registered authority (`register_authority`) → its `iss` is known, enforcement is a no-op
  in practice.
- Upgrade with no `VARCO_JWT_AUDIENCE` set → **the process fails to start.** Intended; the release
  note names the one-line rollback.

**Phase 3 — DLQ / relay**
- `OutboxRelay` with no `retry_policy` → today's behaviour exactly (unbounded retry, no attempt
  counter). Assert this with a test.
- `max_attempts` set, `dlq` omitted → `ValueError` at construction.
- Entry whose payload cannot be deserialized → dead-lettered with `event=None` and raw `payload`,
  then deleted. Today it is deleted silently; the new path is strictly more information.
- DLQ `push()` itself raises → swallowed and logged per the ABC contract; the relay must not lose
  the entry as a result of a DLQ failure, so do not delete when `push()` reports failure.
- Repository without `mark_failed` → one warning per class, unbounded-retry fallback.

**Phase 4 — jobs**
- `run_at = None` → claimed immediately (today's behaviour, and the reason the column is nullable).
- `run_at` in the future → skipped; claimed on the first tick after it passes.
- Clocks skewed between workers → the predicate uses the **database's** `now()`, not the worker's.
  State this in the docstring.
- `lease_ttl=None` → no lease taken, `lease_expires_at` stays NULL, `reap_expired_leases` ignores
  the row: a store can run leased and unleased jobs side by side.
- Worker pauses past its lease, lease is reaped, worker resumes and writes → refused by
  `expected_epoch`. **The Kleppmann case, and the reason fencing is at write time.**
- `renew` on a job already reaped → returns `None`; the worker must abort, not continue.
- `max_attempts=1` (default) → terminal `FAILED` on first failure, exactly as today.
- `attempt` exhausted with no `dlq` wired → `FAILED`, not `DEAD`. `DEAD` means "handed to a DLQ".
- SQLite backend → no `SKIP LOCKED`; the existing plain SELECT+UPDATE branch stays, lease columns
  behave identically, and the concurrency guarantee is unchanged (i.e. still weaker — already
  documented).

**Phase 5 — locks**
- `xact()` on a session with no open transaction → SQLAlchemy opens one; the lock releases at the
  implicit commit. Document it.
- `try_acquire` via the ABC → holds a transaction (and a pooled connection) until `release`;
  documented cost, `xact()` is the recommended path.
- `ttl` passed to the xact lock → accepted and documented as meaningless (the transaction is the
  bound), never silently ignored.

**Phase 6 — retention**
- `delete_where()` with no predicate at all → `ValueError`. Refusing to truncate by accident.
- `limit=N` → returns the number actually deleted so callers can loop until 0.
- `store_raw_token=False` + a `callback_url` → the callback has no Bearer token; documented that it
  must use a service credential.

**Phase 7 — A2A**
- Both `router_cls` and `source` given → `ValueError`.
- `.router_class` on a non-router source → `None` (property kept for back-compat).
- Legacy path hit while `legacy_paths=False` → 404, with the deprecation named in the release notes.
- `SkillSource.invoke` raising → mapped to a JSON-RPC error envelope, not a bare 500.

---

## Verification

Per phase, from the workspace root:

```bash
# Phase 1
uv run pytest varco_core/tests/test_encryption_store.py varco_core/tests/test_encryption_destroy.py
uv run pytest varco_sa/tests/test_sa_encryption_store.py
uv run pytest varco_redis/tests/test_redis_encryption_store.py -m integration
uv run pytest varco_beanie/tests/test_beanie_encryption_store.py -m integration

# Phase 2
uv run pytest varco_core/tests/test_trusted_issuer_registry.py varco_fastapi/tests/test_server_auth.py

# Phase 3
uv run pytest varco_core/tests/test_outbox.py varco_core/tests/test_dlq.py varco_core/tests/test_audit.py

# Phase 4
uv run pytest varco_core/tests/test_job.py varco_sa/tests/test_sa_job_store.py
uv run pytest varco_sa/tests/test_sa_job_store.py -m integration          # concurrent claim + fencing
uv run pytest varco_redis/tests/test_redis_job_store.py varco_beanie/tests/test_beanie_job_store.py -m integration
uv run pytest varco_fastapi/tests/                                        # poller + runner retry

# Phase 5
uv run pytest varco_sa/tests/test_sa_xact_advisory_lock.py -m integration

# Phase 6
uv run pytest varco_core/tests/test_job_store_retention.py varco_sa/tests/test_sa_job_store.py

# Phase 7
uv run pytest varco_fastapi/tests/test_a2a_v1.py varco_fastapi/tests/test_skill_adapter.py

# Phase 8
uv run pytest varco_sa/tests/test_rls.py
uv run pytest varco_sa/tests/test_rls.py varco_redis/tests/test_redis_bulkhead.py -m integration
```

Whole-workspace gate before each release (must be green):

```bash
uv run pytest varco_core/tests/ varco_kafka/tests/ varco_redis/tests/ \
              varco_sa/tests/ varco_beanie/tests/ varco_casbin/tests/ varco_fastapi/tests/
```

DI health, per the CLAUDE.md pitfall about a green suite with a container that will not bootstrap —
every package whose singletons change (`varco_sa`, `varco_redis`, `varco_fastapi`) must keep its
`container.scan(pkg); container.validate_bindings()` test passing:

```bash
uv run pytest varco_redis/tests/test_redis_di.py varco_fastapi/tests/test_di_binding_health.py
```

Migrations (Phases 1 and 4), against a scratch Postgres:

```bash
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```

There is no configured lint or type-check command in this repo (CLAUDE.md) — do not invent one.

---

## Risks

- **Widening a `runtime_checkable` Protocol silently breaks external implementations.**
  `EncryptionKeyStore` is a Protocol, not an ABC; adding methods makes `isinstance()` fail for
  third-party stores. *Invariant:* `EncryptionKeyManager` must never call a new store method
  directly — always through the Step 14 capability shim. A test must construct a tenant-only store
  and drive the manager through it.

- **Phase 2 makes services fail to start.** A deployment upgrading without `VARCO_JWT_AUDIENCE`
  will not boot. *Invariant:* both escape hatches (`allow_any_audience=`, `VARCO_JWT_ALLOW_ANY_AUDIENCE`)
  must be tested and named in the release notes, and the error message itself must state the fix.

- **`try_claim` is the safety-critical primitive in the package.** Phase 4 rewrites its `WHERE`
  clause. *Invariant:* under concurrency, exactly one claimer wins — assert it with a real-Postgres
  integration test at N=20 concurrent claimers before and after, and keep the Postgres/SQLite branch
  split intact.

- **Adding `lease_ttl`/`owner_id` kwargs breaks external `AbstractJobStore` subclasses** the moment
  a caller passes them. *Invariant:* nothing in varco passes the lease kwargs unless the operator
  opts in; `renew`/`reap_expired_leases` raise a clearly-worded `NotImplementedError` rather than
  no-opping, so a half-migrated store fails loudly.

- **The Phase 4 migration adds three indexes to a potentially large live `varco_jobs` table.**
  *Invariant:* build them `CONCURRENTLY` on Postgres and say so in the revision docstring; all
  columns nullable/defaulted so no table rewrite and no backfill is required.

- **Dead-lettering deletes the outbox entry.** If the DLQ silently drops it, the event is lost —
  and for an audit trail that is the exact failure mode U-6 is trying to prevent. *Invariant:*
  never delete when `push()` reports failure; `ValueError` at construction when `max_attempts` is
  set without a `dlq`; a durable DLQ (Step 42) is strongly recommended before enabling
  `max_attempts` in production.

- **Changing `AuditConsumer`'s defaults changes behaviour on upgrade.** Retries where there were
  none. *Invariant:* it only affects the failure path (a succeeding handler is untouched), and
  `retry_policy=None` explicitly restores fire-and-forget. Call it out in the CHANGELOG.

- **Phase 7 touches a live HTTP contract.** *Invariant:* `legacy_paths=True` by default for one
  minor release, and `varco_fastapi/tests/test_skill_adapter.py` must stay green unmodified — if it
  needs edits, the extraction in Step 78 was not verbatim.

- **The RLS helper is the one place where getting it wrong is invisible in tests.** The
  non-InitPlan form passes every functional test and costs 150× in production. *Invariant:* the
  unit test asserting the literal `(SELECT ` in `enable_rls_ddl`'s output is non-negotiable.

- **Scope creep against the filer's explicit boundaries.** U-1/U-2 must not grow to cover the
  delete path, per-store classification, extended receipts or restore-time replay; U-17 must not
  grow a cron/scheduler. *Invariant:* if a step starts adding a component rather than a column, a
  method or a document, stop and re-read the Non-goals.
