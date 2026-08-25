# Upstream gaps — `varco_*` and `providify`

**Purpose:** everything AG Builder needs from the mandated internal libraries that they do
not yet provide. Per the working rule in [requirements/agbuilder.md](requirements/agbuilder.md):
*if a feature or bug is needed inside `varco_*` or `providify`, do not work around it — ask
for the fix.*

This is a **living document**, updated as each design decision lands. Implement these in the
library repos before (or alongside) starting the platform.

- Repos: `/home/edoardo/projects/varco`, `/home/edoardo/projects/providify`
- Design ledger: [design/agbuilder/workspace.md](design/agbuilder/workspace.md)
- Last updated: 2026-08-25 · **U-20 CLOSED** — providify 2.0.0 ships `@Provider(returns=…)` /
  `container.provide(fn, returns=…)` (`providify/container.py:989`, `providify/decorator/scope.py:493`),
  exactly the ask filed below. The interim `varco_core` compat shim (a hand-rolled
  patch-then-register helper) has been deleted; every former call site now uses the native
  override (Plan 016 / RL-2).
- Previously: 2026-08-23 · adds **U-20 (P2, request)** — `container.provide()`/`@Provider` has no
  supported way to register a factory whose interface is a runtime-computed generic alias, found
  while consolidating six independent workarounds in `varco_core`'s Plan 014 into one internal
  compat helper. Not blocking; filed so the workaround isn't reinvented a seventh time.
- Previously: 2026-08-04 · after **T5.3 part 2 (D-70/ADR-074)** — **U-6 RE-SCOPED downward after a
  source sweep**: its retry/DLQ mechanism exists and is already per-subscription; the real gap is the
  **relay** leg, and the DLQ-persistence leg leaves U-6 entirely (ours to build over an existing ABC).
  **U-17's DLQ leg unblocked**, its `run_at` leg untouched. ⚠️ Third gap filed off `ARCHITECTURE.md`
  that failed source verification — **treat any entry without a `✅ verified in source` marker as
  unverified.**
- Previously: 2026-08-04 · after **T5 payload-shape (D-68/ADR-072)** —
  adds **U-18 (P2, report)**, **U-19 (P1, report)** from the job-payload-body-handling thread
- Previously: 2026-08-03 · after **P2 round 5 / thread T3** (D-49 … D-56a, ADR-055 … ADR-061) —
  adds **U-13 (P1, request)**, U-14, U-15 from the builder-API + authz thread
- Verification: items marked **✅ verified in source** were read directly in the library
  code, not inferred from documentation. Items marked **⚠️ unverified** need a check before
  you act on them.
- **Last reconciled:** 2026-08-25 against providify 2.0.0 (Plan 016 / RL-4). Convention from this
  reconciliation forward: every entry's **Status** line must name the **file:line** it was
  verified in — never a `CLAUDE.md`/`README.md` claim, per the register's own U-8 lesson
  (`UPSTREAM-GAPS.md` §"Maintainer response — source corrections"). Three entries this pass
  found genuinely stale — verified as still-open weeks/months ago, but the underlying gap has
  since been closed by varco's own later work: **U-11 CLOSED** (fencing/lease/renew shipped,
  Plan 005 Phase 4), **U-13 CLOSED** (`aud`/`iss` fail-closed by default shipped), **U-17
  CLOSED** (`run_at`/`retry_policy` binding shipped, Plan 005 Phase 4 + Plan 011 T2). **U-1/U-2
  CLOSED** (`ScopedEncryptorRegistry`/`destroy_scope()`/`KeyDestroyedError` shipped). **U-3
  CLOSED** (`SkillSource`/`source=` decoupling shipped) — no longer merely "downgraded, gap
  still real"; varco's own source now satisfies the ask, independent of AG Builder no longer
  needing it. See each entry below for file:line evidence.

## Summary

| ID | Library | Gap | Blocks | Priority |
|----|---------|-----|--------|----------|
| [U-1](#u-1) | `varco_core` | ~~Encryption keys are tenant-scoped; need arbitrary-principal (data-subject) scoping~~ | R-045 GDPR erasure | **✅ CLOSED** — `ScopedEncryptorRegistry`/`load_for_scope`/`rotate_scope` shipped (was P0) |
| [U-2](#u-2) | `varco_core` | ~~No hard-delete / destroy semantics on key retirement~~ | R-045 | **✅ CLOSED** — `destroy()`/`destroy_scope()`/`DestroyReceipt`/`KeyDestroyedError` shipped (was P0) |

> ⚠️ **U-1/U-2 scope note — D-12 / [ADR-017](design/agbuilder/architecture/decisions/ADR-017-erasure-execution-model.md), 2026-08-02 (historical; superseded 2026-08-25).**
> Erasure is now **delete-first, shred the residue**: rows are hard-deleted wherever deletion is
> possible, and key destruction covers only residue (backups, WAL, replicas, append-only audit).
> This note originally read "U-1 and U-2 remain P0 and their asks are unchanged" — as of the
> 2026-08-25 Plan 016/RL-4 reconciliation both entries are **CLOSED**, verified against
> `varco_core/encryption.py` / `encryption_store.py` source (see each entry's body). What remains
> **ours to build**, not varco asks: the delete path, the per-store delete/shred classification,
> the extended receipt (per-store outcomes), and restore-time receipt replay.
| [U-3](#u-3) | `varco_fastapi` | ~~`SkillAdapter` subject is a `VarcoRouter` class; needs an arbitrary agent subject with a hand-authored Agent Card~~ | R-014, R-039 | **✅ CLOSED** — `SkillSource`/`source=`/`ctx=` shipped (was P2, downgraded from P0 by D-9g) |
| [U-4](#u-4) | `varco_fastapi` | A2A protocol surface **confirmed stale** against A2A v1.0.0 | R-014 | **P1 — reported, no longer blocking** (was P0-verify; now verified) |
| [U-5](#u-5) | `varco_core` / `varco_sa` | No Postgres Row-Level Security support in the tenancy layer | R-022 fail-closed | **P2 — report, not request** (was P1; downgraded 2026-08-03 by [ADR-053](design/agbuilder/architecture/decisions/ADR-053-two-layer-tenant-isolation-schema-per-tenant-over-row-level-security.md) — we build it, and the report now carries the LEAKPROOF/InitPlan finding) |
| [U-6](#u-6) | `varco_core` | ~~`AuditConsumer` ships with no retry policy, DLQ, or per-stream policy scope~~ **RE-SCOPED 2026-08-04:** the retry/DLQ mechanism **exists and is already per-subscription**; the real gap is that **`OutboxRelay` has no attempt tracking or dead-letter path at all** | R-027, R-048 | **P1 — high** (ask is *smaller and more precise*; the DLQ-persistence leg left U-6 entirely — it is ours to build) |
| [U-7](#u-7) | `varco_core` | No distributed rate limiter (`InMemoryRateLimiter` only) | R-057 | **P2 — medium** |
| [U-8](#u-8) | `varco` | `ARCHITECTURE.md` is stale on `SkillAdapter` async support | docs correctness | **P2 — low** |
| [U-9](#u-9) | `varco_core` | Graph-shaped durable execution (sagas are linear only) | R-002, ADR-003 | **P3 — decided against** |
| [U-10](#u-10) | `varco_fastapi` | `MCPAdapter` exposes routes *as* an MCP server; R-009/R-010 need an MCP **client** — different products, not a defect | — (no upstream change requested) | **P3 — evaluated, no request** |
| [U-11](#u-11) | `varco_core` / `varco_sa` | ~~`try_claim` ignores its TTL; no heartbeat/renew, no fencing token; `JobPoller` detects death by wall-clock age~~ | NFR-6, ADR-032/033 run ownership | **✅ CLOSED** — `lease_ttl`/`lease_epoch`/`renew`/`reap_expired_leases`/`StaleLeaseError` shipped (was P1) |
| [U-12](#u-12) | `providify` | No interface-**conformance** check at `bind`/registration time — `validate()` covers only wiring resolvability (missing/ambiguous binding, cycles, scope, unresolved annotation), not "does the implementation satisfy the Protocol/ABC" | — (working substitute: static typing + contract tests) | **P2 — report, still open** (re-verified against providify 2.0.0 source; NOT closed by `validate()`) |
| [U-13](#u-13) | `varco_fastapi` / `varco_core` | ~~**JWT validation fails open**: `aud` checked only if an env var is set, `iss` never enforced~~ | R-052, R-054, R-022 | **✅ CLOSED** — `aud` required by default (`ValueError` unless `allow_any_audience=True`), `iss` enforced by default (`VARCO_JWT_ENFORCE_ISS=true`) (was P1) |
| [U-16](#u-16) | `varco_sa` | **`SAAdvisoryLock` is session-scoped**: behind a transaction pooler, `release()` runs on a different connection, silently no-ops, and the lock leaks | R-045, NFR-9, R-016/R-017 | **P1 — report *and* request** (correctness defect against a supported topology, U-4/U-13's class; interim built in-platform) |
| [U-17](#u-17) | `varco_core` / `varco_sa` | ~~Job store has **no time dimension** — no `run_at`, delay, or retry-after; `RetryPolicy` exists but is unreachable from `AbstractJobStore`~~ | R-045, NFR-9, R-027, R-048 | **✅ CLOSED** (items 1–3) — `run_at`/`enqueue(run_at=/delay=)`/`RetryPolicy` binding shipped (was P1; item 4's DLQ leg overlaps still-open U-6) |
| [U-14](#u-14) | `varco_fastapi` / `varco_core` | Auth ergonomics: no composed auth→role→policy middleware, no route-level policy decorator, no resource hierarchy in `PolicyEngine`, no refresh flow, no introspection | — (AG Builder unblocked) | **P3 — report only** (re-verified 2026-08-25 — absences confirmed still hold) |
| [U-15](#u-15) | `varco_fastapi` | HTTP conventions absent: no pagination envelope, no idempotency-key handling, no API versioning | — (AG Builder unblocked) | **P3 — report only** (re-verified 2026-08-25 — absences confirmed still hold) |
| [U-18](#u-18) | `varco_core` / `varco_sa` | Job store has no bulk/predicate delete, no TTL, no `expires_at` — retention is id-at-a-time | — (demoted from R-045 by ADR-072 §3.7) | **P2 — hygiene** (was a D-67 GDPR candidate; demoted) |
| [U-19](#u-19) | `varco_core` | `request_token` stores the raw undecoded Bearer JWT at rest | — (mitigated locally by ADR-072 §3.6) | **P1 — report, not request** |
| [U-20](#u-20) | `providify` | `container.provide()`/`@Provider` cannot register a factory whose interface is a runtime-computed generic alias — six sites in `varco_core` mutate `__annotations__` by hand to work around it | — (fixed upstream: `@Provider(returns=…)` / `container.provide(fn, returns=…)`, providify 2.0.0) | **✅ CLOSED** |

---

## P0 — blockers

### U-1 · Encryption key scoping is tenant-only; ADR-001 needs per-data-subject keys {#u-1}

**Requirement:** R-045 (erase all personal data for *an identified data subject*)
**Decision that depends on it:** [ADR-001](design/agbuilder/architecture/decisions/ADR-001-crypto-shredding-for-gdpr-erasure.md)
**Status:** ✅ CLOSED — verified in source 2026-08-25 (Plan 016 / RL-4, re-verifying a stale
2026-08-02 "still P0" claim against the register's own U-8 lesson). `varco_core/encryption.py:811`
(`ScopedEncryptorRegistry`, "sibling of `TenantAwareEncryptorRegistry`... keyed by the opaque
`scope` dimension instead of `tenant_id`... e.g. `f"{tenant}:subject:{sid}"` for per-data-subject
keys") and `varco_core/encryption_store.py:799` (`EncryptionKeyManager.build_scoped_registry`),
`:368`/`:382` (`load_for_scope`/`list_scopes` on the `EncryptionKeyStore` Protocol), `:851`
(`rotate_scope`) ship exactly the generalised-scoping-dimension ask below — the previous
`✅ verified in source` status (undated) predates this work and described the gap as still open;
it no longer is.

**What exists.** `varco_core/encryption.py` and `encryption_store.py` are a well-built
envelope-encryption layer:

- `FieldEncryptor` Protocol, `FernetFieldEncryptor`
- `MultiKeyEncryptorRegistry` — `register(kid, …)`, `set_primary(kid)`, `retire(kid)`
- `_pack_ciphertext(kid, raw)` / `_unpack_ciphertext` — **ciphertext is framed with its key
  id**, which is exactly what crypto-shredding needs
- `EncryptionKeyStore` Protocol with `save`, `load(kid)`, `load_for_tenant(tenant_id)`,
  `list_tenants()`, `delete(kid)`
- `EncryptionKeyManager` with `get_or_create_encryptor(...)`, `build_tenant_registry(...)`,
  `rotate(tenant_id)`
- Backends: `InMemory`, `varco_sa`, `varco_redis`, `varco_beanie`

**The gap.** Every scoping affordance is keyed on **tenant**: `TenantAwareEncryptorRegistry`,
`load_for_tenant(tenant_id)`, `list_tenants()`, `rotate(tenant_id)`. Crypto-shredding for
GDPR needs the key scoped to a **data subject** — one natural person inside a tenant.

With tenant-scoped keys, erasing one data subject means destroying the tenant key, which
erases *every* subject in that tenant. That makes the mechanism unusable for R-045.

**What to add.** Generalise the scoping dimension from "tenant" to an opaque **principal**
(or `scope`) string, keeping tenant as the common case:

```python
# today
async def load_for_tenant(self, tenant_id: str | None) -> list[EncryptionKeyEntry]: ...
async def rotate(self, tenant_id: str | None = None) -> FieldEncryptor: ...

# needed (tenant remains a special case of scope)
async def load_for_scope(self, scope: str) -> list[EncryptionKeyEntry]: ...
async def rotate(self, scope: str) -> FieldEncryptor: ...
async def destroy_scope(self, scope: str) -> DestroyReceipt: ...   # see U-2
```

`EncryptionKeyEntry` needs a `scope` field alongside `tenant_id`, and
`TenantAwareEncryptorRegistry` needs a sibling `ScopedEncryptorRegistry` whose `_resolve`
maps `context` → scope. AG Builder would use `scope = f"{tenant_id}:subject:{subject_id}"`.

**Note on key volume.** Per-subject keys mean the key store grows with data subjects, not
tenants. Worth confirming the SA backend indexes `scope` and that `build_*_registry` does
not load all keys eagerly — at a few thousand subjects an eager load would hurt.

**Shipped.** `EncryptionKeyStore.load_for_scope`/`list_scopes` (`encryption_store.py:368,382`),
`EncryptionKeyManager.build_scoped_registry`/`rotate_scope` (`encryption_store.py:799,851`), and
`ScopedEncryptorRegistry` (`encryption.py:811`) match this shape exactly — `scope` generalises
`tenant_id` as asked, `TenantAwareEncryptorRegistry` is untouched (a genuinely separate sibling
class, not a subclass), and per Plan 016/RL-4 re-verification this entry is closed.

---

### U-2 · No destroy semantics — `retire()` and `delete()` are not "provably destroyed" {#u-2}

**Requirement:** R-045
**Status:** ✅ CLOSED — verified in source 2026-08-25 (Plan 016 / RL-4). `varco_core/encryption.py:562`
(`MultiKeyEncryptorRegistry.destroy(kid)` — distinct from `retire()`, idempotent, leaves the
encryptor mapping intact so decrypt fails distinguishably), `:183` (`KeyDestroyedError`, a
subclass of `EncryptionError`), `:227` (`DestroyReceipt`, a frozen dataclass), and
`varco_core/encryption_store.py:891` (`EncryptionKeyManager.destroy_scope(scope, *, actor=None)
-> DestroyReceipt`) match the "what to add" shape below exactly, kid-for-kid. The previous
`✅ verified in source` status (undated) predates this work.

**What exists.** `MultiKeyEncryptorRegistry.retire(kid)` removes a key from the registry;
`EncryptionKeyStore.delete(kid)` removes the stored entry. `EncryptionKeyManager.rotate()`
documents that *"the old key is NOT deleted — callers must re-encrypt all existing ciphertext
before calling `store.delete(old_kid)`"*.

**The gap.** Erasure needs three things the current API does not provide:

1. **Irreversible destruction as a distinct operation** from rotation-and-cleanup — no key
   recovery, no backup key, no escrow. A `delete()` that a backup restore can undo is not
   erasure. *(Corrected 2026-08-02 by D-12: an earlier draft justified this as "EDPB/ICO
   acceptance of crypto-shredding". No regulator endorses crypto-shredding as Art. 17 erasure —
   see [brief 016](design/agbuilder/research/016-crypto-shredding-regulatory-standing.md). The
   requirement stands on its own: reversible destruction is not destruction.)*
2. **An auditable destruction receipt.** The EDPB's Feb 2026 enforcement report explicitly
   penalises unverifiable erasure. We need a durable record — scope, key id, timestamp,
   requester — that contains no personal data.
3. **A defined read path for destroyed keys.** Today, decrypting ciphertext whose `kid` was
   retired presumably raises. It should raise a *distinguishable* error
   (`KeyDestroyedError`) so callers can render "erased" rather than "corrupt" — this matters
   for R-033 (explainable errors).

**What to add.**

```python
class DestroyReceipt(NamedTuple):
    scope: str
    kids: tuple[str, ...]
    destroyed_at: datetime
    actor: str | None

async def destroy_scope(self, scope: str, *, actor: str | None = None) -> DestroyReceipt: ...

class KeyDestroyedError(EncryptionError): ...   # raised on decrypt of a shredded kid
```

Plus documentation stating the operator's obligation: key-store backups must not outlive the
erasure window, or destruction is not destruction.

**Shipped.** All three items above ship as designed: `destroy()`/`destroy_scope()` are
irreversible (no un-destroy call exists), `DestroyReceipt` is the auditable record (no personal
data in its fields — `scope`/`kids`/`destroyed_at`/`actor`), and `KeyDestroyedError` is a
distinguishable `EncryptionError` subclass so callers can render "erased" rather than "corrupt".
Per Plan 016/RL-4 re-verification this entry is closed.

---

### U-3 · `SkillAdapter` cannot expose a deployed agent as an A2A agent {#u-3}

**Requirement:** R-014 (deployed agent reachable over A2A), R-039 (hand-written skills description)
**Status:** ✅ CLOSED — verified in source 2026-08-25 (Plan 016 / RL-4). Not just "downgraded, gap
still real" as previously recorded: varco's own source has since shipped the exact ask, in
`varco_fastapi/varco_fastapi/router/a2a/source.py:16` ("Plan 005, Phase 7 (U-3 + U-4)... the
seam that lets a `SkillAdapter` expose *any* subject... through the same A2A surface") —
`SkillSource` (`:102`, a `runtime_checkable` `Protocol`), `SkillDefinition` (`:44`, now
accepting `route=None` for "author-supplied... or non-router `SkillSource` implementations"),
`AgentMetadata` (`:76`), and `invoke(..., ctx: AuthContext | None = None)` (`:143`, "U-3's
per-request auth passthrough... distinguish the three caller classes A2A expects to audit").
`varco_fastapi/varco_fastapi/router/skill.py:179` confirms `SkillAdapter(..., source=...)` is
now mutually exclusive with `router_cls=`. This closure is independent of D-9g below — it was
recorded purely as "downgraded, no longer blocking AG Builder" before; the gap itself is now
also fixed upstream, for whichever future consumer needs it.

**⬇️ Historical note — DOWNGRADED P0 → P2 on 2026-08-02 by design decision D-9g** (retained
verbatim; this is why AG Builder itself never adopted the fix above):

> **AG Builder no longer depends on this.** D-9g adopts the official **`a2a-sdk` v1.1.2**
> (Linux Foundation) inside the runner, behind an `A2AServerPort` bound in providify — the same
> pattern ADR-007 used for `McpClientPort`. This **overrides the standing working rule**
> ("ask for the fix upstream rather than working around it"), deliberately and with reasons
> recorded in the design ledger: (1) it takes a **P0 blocker on a v1 MUST off the critical path**,
> and against NFR-12's ~6 months the schedule risk we do not control is the one worth removing
> first; (2) U-4 is now verified — varco's A2A surface is **stale against v1.0.0** — so building
> R-014 on varco meant waiting for a redesign *and* a spec migration, not one fix; (3) the gap is
> a **different subject shape**, not a missing feature, so this was always a redesign request
> rather than a patch.
>
> **The gap below is still real and still worth fixing upstream** — it is simply no longer
> blocking us, so it is reported for varco's own roadmap rather than requested for ours.

> **Correction to an earlier note:** `ARCHITECTURE.md` states *"v1 tasks are synchronous"* and
> that `GET /tasks/{task_id}` is *"echo-back, no history stored"*. **The source disagrees** —
> `SkillAdapter.__init__` already accepts `job_runner` and `job_store`; with them,
> `POST /tasks/send` returns immediately with `state: working`, clients poll, and
> `GET /tasks/{task_id}/history` returns the turn list. Async A2A is **not** the gap. See U-8.

**The real gap.** `SkillAdapter` is constructed from a **`VarcoRouter` subclass**
(`SkillAdapter(router_cls, …)`, `.router_class` property), and its skills are *derived* from
routes flagged `skill_enabled=True` via `introspect_routes()`, with auto-generated skill ids
(`_auto_skill_id`) and descriptions (`_resolve_description`).

AG Builder's A2A subject is not a router. It is a **deployed agent** — a graph, executed by
the runner — whose skills come from R-039's *hand-written* description, which drives
discovery and search. There is no CRUD route to introspect.

**What to add.** Decouple the adapter from route introspection by making the skill source a
protocol, with the existing router introspection as one implementation:

```python
class SkillSource(Protocol):
    def skills(self) -> list[SkillDefinition]: ...
    def agent_metadata(self) -> AgentMetadata: ...          # name, description, version
    async def invoke(self, skill_id: str, payload: dict) -> Any: ...

class RouterSkillSource(SkillSource): ...    # today's behaviour, unchanged
# AG Builder supplies its own: skills from the agent definition, invoke → runner
```

`SkillAdapter` then takes a `SkillSource` instead of a `router_cls`. Also needed:

- **Author-supplied skill definitions** — accept explicit `SkillDefinition` objects rather
  than only deriving them, so R-039's hand-written text reaches the Agent Card verbatim
- **Per-request auth context passthrough** — R-055 requires distinguishing three caller
  classes (end user, another agent, integrating platform) and recording them in the audit
  trail; the adapter must surface the verified caller identity to `invoke`

**Shipped.** Every item above is implemented as specified — see the Status line for the
file:line evidence. Per Plan 016/RL-4 re-verification this entry is closed.

---

### U-4 · Verify the A2A protocol surface against the current spec {#u-4}

**Requirement:** R-014 ("an external A2A client discovers the agent's skills and invokes it")
**Status:** ✅ **VERIFIED 2026-08-02 — the surface IS stale. Suspicion confirmed.**

> **Verified against primary sources** (`design/agbuilder/research/012-VERIFICATION.md`, claims 2/3/5/6):
> A2A **v1.0.0 released 2026-03-12** under Linux Foundation governance. Current discovery path is
> **`/.well-known/agent-card.json`**; the canonical data model is Protocol Buffers with three
> bindings (JSON-RPC, gRPC, HTTP+JSON); abstract operations are `SendMessage`,
> `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`. Agent Card
> capability flags are **nested inside a `capabilities` object**, and there is **no top-level `id`**.
>
> varco mounts `GET /.well-known/agent.json`, `POST /tasks/send`, `GET /tasks/{task_id}` — the
> **pre-v1.0 path and a non-JSON-RPC surface**. An unmodified current A2A client will not
> interoperate with it, which is precisely R-014's acceptance hint.
>
> **No longer blocks AG Builder** (D-9g adopts `a2a-sdk` directly), but this is the more valuable
> of the two upstream reports: it is a correctness bug against a released spec affecting anyone
> using varco's A2A surface today, not a feature request. **Report it upstream with the spec
> citation.** Note U-3's redesign should be done against the v1.0 surface, not the current one —
> so U-3 and U-4 are one piece of work upstream, not two.

`skill.py` mounts `GET /.well-known/agent.json`, `POST /tasks/send`,
`GET /tasks/{task_id}`. My recollection is that the A2A specification moved to a JSON-RPC
surface with `message/send` and `/.well-known/agent-card.json`, and that the project is now
under Linux Foundation governance — but **I have not verified this against the current spec**
and my knowledge may be stale.

This matters a lot: R-014's acceptance hint is that an *unmodified external A2A client*
discovers and invokes the agent. If the mounted surface predates the current spec, real
clients will not interoperate, and U-3's work should be done against the correct surface
rather than retrofitted afterwards.

**Action:** check [the A2A spec](https://a2a-protocol.org) and confirm the current
discovery path, method names and transport. If varco's surface is stale, that is a separate
upstream fix and should be sized before U-3.

---

## P1 — high

### U-5 · No Postgres Row-Level Security support in the tenancy layer {#u-5}

**Requirement:** R-022 ("cross-tenant access attempts **fail closed**")
**Decision:** [ADR-002](design/agbuilder/architecture/decisions/ADR-002-modular-monolith-three-deployables.md)
**Status:** ✅ verified — `TenantAwareService` is application-level scoping

`TenantAwareService._scoped_params` injects `tenant_id` into queries through the service MRO.
That is correct and useful, but it **fails open**: any query path that bypasses the mixin —
a raw SQL call, a new repository method, a reporting query, a developer mistake — returns
cross-tenant rows. R-022 requires failing *closed*.

Postgres RLS is the database-level backstop that makes the requirement true rather than
aspirational. What `varco_sa` would need:

- A helper to emit `ALTER TABLE … ENABLE ROW LEVEL SECURITY` + a tenant policy for
  generated ORM tables (alongside the existing `SchemaGuard` / Alembic helpers)
- Per-transaction context setting — `set_config('rls.tenant_id', …, local => true)` — bound
  to the same scope `TenantAwareService` already uses
- A `SchemaGuard`-style drift check: *"table X has `tenant_id` but no RLS policy"*
- Documented interaction with connection pooling (transaction-scoped `set_config` is
  PgBouncer-transaction-mode safe; session-scoped is not)

This is generically useful to any multi-tenant varco app, not just AG Builder.

#### ⚠️ Amended 2026-08-03 by D-46 → [ADR-053](design/agbuilder/architecture/decisions/ADR-053-two-layer-tenant-isolation-schema-per-tenant-over-row-level-security.md)

**Downgraded from a *request* to a *report*. We are building this ourselves; nothing here blocks us.**
Reasons, narrow by design: unlike U-1/U-2 this is **not a capability only varco can provide** — it is
session/transaction plumbing in our own data-access layer — and blocking the whole platform data layer
on an upstream release is disproportionate at NFR-12's ~6 months. Recorded in ADR-053 §3 as an
explicit, narrow override of the working rule.

**The report is still worth sending, and is now more valuable than when it was filed.** Three findings
from brief [029](design/agbuilder/research/029-postgres-rls-under-connection-pooling.md), verified in
varco source:

1. 🔑 **The policy helper above would hit a 150× performance cliff if written the obvious way.**
   `current_setting()` is **volatile and not LEAKPROOF**, so the natural form
   `USING (tenant_id = current_setting('rls.tenant_id')::uuid)` blocks index usage and forces a
   sequential scan. One documented real query went **8,100 ms → 94 ms** purely by wrapping it in an
   InitPlan:
   ```sql
   USING (tenant_id = (SELECT current_setting('rls.tenant_id', true)::uuid))
   ```
   **Any varco RLS helper must emit the `(SELECT …)` form.** This is the single most useful thing in
   this report — it is invisible in testing at small data volumes and catastrophic in production.

2. **Schema-per-tenant is absent, and the existing `search_path` handling is unsafe for it.**
   `varco_sa/connection.py:236` sets `search_path` **once at connection init** from a deployment-wide
   `POSTGRES_SCHEMA_NAME`. That is fine for one schema per *install*, but `search_path` is **session
   state** — routing tenants that way on a pooled connection leaks across tenants by exactly the
   mechanism session-scoped `set_config` does. If varco ever adds schema-per-tenant it needs
   `SET LOCAL search_path` or SQLAlchemy's `schema_translate_map`, never connection init. No
   `schema_translate_map` usage exists anywhere in the codebase today.
   *(For AG Builder this is not a request — `TenantUoWProvider`'s per-tenant provider routing is a
   perfectly good seam to build on.)*

3. **`TenantAwareService` fails open — restated because it is the whole point.** Confirmed in source at
   `varco_core/service/tenant.py:424`. Any query path bypassing the mixin returns cross-tenant rows.
   Worth telling the maintainers whether or not they act on it.

---

### U-11 · `try_claim` has no enforced lease, no heartbeat, and no fencing token {#u-11}

**Requirement:** R-016/R-017 (one binary, integrated and air-gapped), R-020 (distributed topology),
NFR-6 (runs survive process restart)
**Decision:** [ADR-033](design/agbuilder/architecture/decisions/ADR-033-run-ownership-lease-heartbeat-and-fencing.md)
(with [ADR-032](design/agbuilder/architecture/decisions/ADR-032-orphan-attempt-recovery-policy.md))
**Status:** ✅ CLOSED — re-verified in source 2026-08-25 (Plan 016 / RL-4; the 2026-08-03 status
below was accurate as-of-then but is now stale — the gap it describes has since shipped, "Plan
005 Phase 4"). `varco_sa/varco_sa/job_store.py:591` (`SAJobStore.try_claim(..., owner_id=,
lease_ttl=)` — "the UPDATE also sets `owner_id`, `lease_expires_at = now + lease_ttl` and
increments `lease_epoch` (fencing token)"), `:1033`-adjacent `renew()`/`reap_expired_leases()`,
and `varco_core/varco_core/job/base.py:82` (`StaleLeaseError`), `:682` (`save(job, *,
expected_epoch=)` — refuses a stale write), `:1033` (`AbstractJobStore.renew`), `:1069`
(`reap_expired_leases`) satisfy all three items in "What `varco` would need" below verbatim.
`varco_beanie/varco_beanie/job_store.py:684,833,869` implements the same trio for the Beanie
backend. Historical note (accurate as of 2026-08-03, now superseded):

`AbstractJobStore.try_claim(job_id: UUID) -> Job | None` (`varco_core/varco_core/job/base.py:517`),
implemented by `SAJobStore` (`varco_sa/varco_sa/job_store.py:226`, method at `:388`), does an atomic
`SELECT … FOR UPDATE SKIP LOCKED` PENDING→RUNNING transition. That part is correct and is what AG
Builder uses. Three things around it are missing for **long-running** work:

1. **The TTL parameter is accepted and ignored.** There is no lease enforced at the database, so
   nothing bounds how long a claim is honoured.
2. **No heartbeat or renewal.** Liveness cannot be distinguished from slowness. Dead-owner detection
   is `JobPoller` (`varco_fastapi/varco_fastapi/job/poller.py`), which marks RUNNING jobs older than a
   wall-clock threshold (5 min default) as FAILED — correct for short jobs, wrong for anything whose
   legitimate duration can exceed the threshold.
3. **No fencing token.** `try_claim` returns no monotonic version, so a claimant that stalls past its
   window and resumes cannot be rejected at the point of write — the failure mode
   [Kleppmann describes](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html).

Related: `SAAdvisoryLock` (`varco_sa/varco_sa/advisory_lock.py`) wraps `pg_try_advisory_lock` and is
**session-scoped with no database-level TTL** — a crashed holder keeps the lock until its connection
times out. `RedisLock` does have TTL plus token-checked release, but Redis is unavailable under R-017.

**Why this matters beyond AG Builder:** any varco app running work longer than the stale threshold hits
the same wall — the choice becomes "set the threshold high and reclaim slowly" or "set it low and
reclaim live work". A lease with heartbeat resolves both, and the fencing token is what makes reclaim
safe rather than merely fast.

What `varco` would need:

- `try_claim` honours its TTL, writing `lease_expires_at` and an incrementing `lease_epoch` (or
  equivalent) on the claimed row, and returns the epoch to the caller
- `renew(job_id, owner_id, epoch)` for heartbeating, failing if the epoch is stale
- `JobPoller` detecting death by lease expiry rather than wall-clock age
- Documented guidance on TTL vs heartbeat interval (the widely-cited rule is TTL ≥ 3× heartbeat plus
  2× worst-case pause, renewal jittered at 50–75% of remaining TTL)

**Interim (historical — superseded, see Status above):** AG Builder implements the lease,
heartbeat and epoch itself in the runner, over varco's existing atomic claim (ADR-033). Now that
this has landed upstream, the `RunClaimer` binding can switch to varco's native
`try_claim`/`renew`/`reap_expired_leases`; the `runs` ownership columns stay as they are.

**Shipped.** Per Plan 016/RL-4 re-verification this entry is closed.

---

### U-6 · `OutboxRelay` has no attempt tracking or dead-letter path; `AuditConsumer` doesn't pass the retry/DLQ hooks that exist {#u-6}

**Requirement:** R-027 (every agent action recorded), R-048 (accountability)
**Decision:** [ADR-074](design/agbuilder/architecture/decisions/ADR-074-per-stream-retry-and-a-shared-dead-letter-queue.md) (D-70)
**Status:** ✅ **verified in source 2026-08-04** — `varco_core/event/consumer.py`,
`varco_core/event/dlq.py`, `varco_core/service/outbox.py`, `varco_core/resilience/retry.py`

> ⚠️ **RE-SCOPED 2026-08-04, downward. The original filing was wrong.** It read
> *"`AuditConsumer` ships with no retry policy, DLQ, or per-stream policy scope"* — sourced from
> `ARCHITECTURE.md`, not from code. **In source the mechanism exists and is already scoped the way
> [ADR-064](design/agbuilder/architecture/decisions/ADR-064-one-audit-outbox-two-streams-and-a-narrow-run-stream.md)
> §5 asked for.** This is the **third** gap filed off `ARCHITECTURE.md` that did not survive contact
> with the source (cf. [U-8](#u-8)). The standing lesson applies: verify varco in source.

**What actually exists** (contradicting the original filing):

- `varco_core/event/consumer.py:268-277` — `listen(*event_types, channel=CHANNEL_ALL, filter=None,
  priority=0, **retry_policy=None**, **dlq=None**, deduplicator=None, inbox=None)`. Both are real
  parameters and they are **per-subscription**, i.e. per channel. ADR-064 §5 asked for policy
  *"resolvable per stream — keyed by `@listen` subscription or an explicit stream identifier"*;
  **that is what this signature already is.**
- `varco_core/event/dlq.py:203-291` — `AbstractDeadLetterQueue` is an **ABC**.
- `varco_core/event/dlq.py:82-149` — `DeadLetterEntry` already carries `event, channel,
  handler_name, error_type, error_message, attempts, first_failed_at, last_failed_at`.
- `varco_core/resilience/retry.py:70-223` — `RetryPolicy(max_attempts=3, base_delay=1.0,
  max_delay=60.0, exponential_base=2.0, jitter=True, retryable_on=…)` with `is_retryable(exc)` —
  an **error-classification hook**, which ADR-074 §3.3 makes load-bearing.

**What is genuinely missing — the ask, in priority order:**

1. **`OutboxRelay` has no attempt tracking and no dead-letter path — the real gap, and one the
   original filing never mentioned.** `varco_core/service/outbox.py:632-642` logs *"publish failed
   for entry… will retry on next tick"* and leaves the row. That is correct at-least-once behaviour
   **and** unbounded retry with no attempt counter, no backoff, and no way to dead-letter a
   permanently-undeliverable entry. Because per-tenant FIFO means nothing behind it drains either, a
   single poison row silently stops a stream. The relay leg has none of the machinery the consumer
   leg has, and the asymmetry looks unintentional.
2. **`AuditConsumer` does not *pass* `retry_policy`/`dlq` by default.** The original ask survives here,
   shrunk to its correct size: make outbox routing plus a default retry policy and DLQ the *default*,
   with fire-and-forget opt-in. **Safe-by-default is the right polarity for an audit trail** — every
   varco consumer with a compliance need currently rediscovers this.
3. **`RetryPolicy(max_attempts=3)` is a poor default for durable delivery** — with the shipped
   `base_delay=1.0`/`exponential_base=2.0`, roughly **seven seconds** of retrying before giving up.
   Research brief [039](design/agbuilder/research/039-per-stream-retry-dlq-and-poison-message-handling.md)
   §Q5 finds *no production system uses windows this short* (Oban: 20 attempts, 15 s floor; Sidekiq:
   25). The other three numbers are fine and match Temporal's shape. **Report, do not request** — the
   parameter is settable, and AG Builder passes its own.
4. **Minor / verify:** `jitter: bool = True` does not say *which* formula. Brief 039 Q5 recommends
   **Full Jitter** on AWS's published benchmarks. If varco implements Equal Jitter or a naive
   ±percentage, a note is worth filing. Not checked.

**What is NOT an upstream ask — deliberately.** The only DLQ implementation is
`InMemoryDeadLetterQueue`, a `deque(maxlen=10_000)` (`dlq.py:298-451`) — bounded, in-memory, **lost on
restart**, which for a compliance trail is silent loss. **AG Builder builds a Postgres-backed
implementation itself.** `AbstractDeadLetterQueue` is an ABC, so this **extends the abstraction rather
than forking varco's write path**, passing [ADR-067](design/agbuilder/architecture/decisions/ADR-067-varco-job-substrate-with-upstream-extensions.md)
§3's triage test — the same test that rejected the `tenant_id`-on-job-row ask. Per ADR-074 §3.5 that
one implementation serves the relay, the consumers **and** the job store, which is
[U-17](#u-17) §4's own recommendation ("one DLQ concept serving both consumers and jobs is the better
outcome than two"). **U-17's DLQ leg is thereby unblocked; its `run_at` leg is untouched.**

---

### U-13 · JWT validation fails open — `aud` optional, `iss` never enforced {#u-13}

**Requirement:** R-052 (identity is OIDC-based), R-054 (verification configurable per integration),
R-022 (fail closed)
**Decision that depends on it:** [ADR-057](design/agbuilder/architecture/decisions/ADR-057-fail-closed-jwt-issuer-and-per-deployable-audience.md)
**Status:** ✅ CLOSED — re-verified in source 2026-08-25 (Plan 016 / RL-4; the undated status
below described the state accurately at the time but is now stale). `varco_fastapi/varco_fastapi/auth/server_auth.py:189-235`
— `JwtBearerAuth.__init__` now raises `ValueError` at construction when no `audience`/
`VARCO_JWT_AUDIENCE` is configured, **unless** `allow_any_audience=True` /
`VARCO_JWT_ALLOW_ANY_AUDIENCE=true` is explicitly opted into (a named, auditable escape hatch —
exactly item 1 of "The ask" below). `varco_core/varco_core/authority/registry.py:540-598` —
`TrustedIssuerRegistry.verify(..., enforce_issuer: bool | None = None)` now compares the token's
`iss` against the resolved issuer's registered `iss` **by default** (`VARCO_JWT_ENFORCE_ISS`,
default `True`), raising `jwt.InvalidIssuerError` on mismatch — exactly item 2. Both BREAKING
security defaults are documented in `CLAUDE.md`'s "Two BREAKING security defaults" paragraph.
**Priority (historical, superseded):** was **P1 — report *and* request** ("the same category as
U-4: a defect against a specification, not a feature request... not a blocker for AG Builder
only because we carry a wrapper below").

**What existed at the time this entry was filed (now fixed — see Status).**

- `varco_fastapi/auth/server_auth.py:103-242` — `JwtBearerAuth` read `VARCO_JWT_AUDIENCE`. **If the
  variable was unset it logged a warning and did not check `aud` at all.**
- `varco_core/authority/registry.py:124-275` — `TrustedIssuerRegistry.verify()` resolved the token's
  `kid` against cached keysets from **any** registered issuer and validated the signature. **It never
  compared the `iss` claim.** The caller was expected to check `token.iss` afterwards; nothing in the
  signature, the return type or the docstring said so.

**Why it matters.** Together these mean a service that forgets one environment variable accepts a
token minted for *any* audience by *any* registered issuer — silently, permanently, and in the
configuration most deployments will have on day one. RFC 7519 §4.1.3 exists precisely to prevent this.
A log warning at startup is not a control: nobody reads a warning in a working system.

**The ask.**

1. **Enforce `aud` by default.** Absent configuration, refuse to construct `JwtBearerAuth` rather than
   proceeding unchecked — fail closed, not open. If a permissive mode is genuinely needed, make it an
   explicit opt-in flag whose name states the risk.
2. **Enforce `iss` inside `verify()`**, against the issuer the `kid` resolved to. If callers are meant
   to do it, the return type should make it impossible to forget.
3. Not asked for: refresh-token flow or introspection (see U-14).

**What AG Builder did meanwhile (historical — the wrapper's stated deletion condition has now
been met).** ADR-057 shipped a **thin strict wrapper** that added the `iss` check and refused to
boot without an explicit audience, with **one audience per deployable** so a token minted for the
runner is rejected by the builder API. ⚠️ This was deliberately *not* a working-around under the
standing rule — the wrapper was **written to be deleted** when upstream tightened the default,
and existed only because a fail-open authentication default could not wait on someone else's
release. **Shipped.** Both defaults now match the ask exactly. Per Plan 016/RL-4 re-verification
this entry is closed.

---

### U-16 · `SAAdvisoryLock` is session-scoped and breaks behind a transaction pooler {#u-16}

**Requirement:** R-045 (erasure runs on a schedule), NFR-9 (13-month aggregates must be swept),
R-016/R-017 (one binary, air-gapped — Redis is unavailable, so Postgres locking is the only option)
**Decision:** T5.5 periodic-job serialisation, over
[ADR-053](design/agbuilder/architecture/decisions/ADR-053-two-layer-tenant-isolation-schema-per-tenant-over-row-level-security.md)
**INV-6** (pgbouncer *transaction* pooling is permitted; only statement pooling is forbidden)
**Status:** ✅ verified in source 2026-08-04 — read from `varco_sa/varco_sa/advisory_lock.py`,
corroborated by research brief
[031](design/agbuilder/research/031-background-jobs-and-scheduling-on-postgres.md) **C-3**

`SAAdvisoryLock` implements `AbstractDistributedLock` with the **session-level** advisory-lock pair —
`pg_try_advisory_lock(int8)` to acquire, `pg_advisory_unlock(int8)` to release (file docstring `:6-9`).
The lock is taken on a **borrowed** connection and **held until `release()` is called** (`:180-186`),
which is to say **across transaction boundaries**. The class's own design note states the assumption
plainly (`:47`): *"each process holds its own advisory lock via its own connection"* — **direct
connections, not a pooler.**

Behind PgBouncer in **transaction** pooling mode that assumption does not hold, and the failure is
silent:

1. `acquire()` runs in one transaction on server connection **A** → the lock is held **on A**.
2. The transaction ends → **A is returned to the pool.**
3. `release()` runs later in a different transaction, which PgBouncer may route to server connection
   **B**. `pg_advisory_unlock` on **B** finds no such lock, **returns `false`, and the lock on A
   leaks** until that server session dies.
4. Meanwhile the next client to borrow **A** silently inherits a connection **holding another
   caller's lock**.

This is the **same class of defect as `SET` vs `SET LOCAL`** (cf. U-5): session state outliving the
transaction that created it, on a connection that does not belong to the caller any more. It is
U-4's and U-13's family — a **correctness defect against a supported deployment topology**, not a
feature request.

PostgreSQL already provides the pooling-safe primitive, and brief 031 C-3 confirms it against the
[official docs](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS):
**`pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`** are transaction-scoped and auto-release at
`COMMIT`/`ROLLBACK`. They are correct under transaction pooling *and* they remove the "crashed holder
keeps the lock until its connection times out" problem noted in [U-11](#u-11) — a held lock cannot
outlive its transaction, so there is nothing to time out.

**Why this matters beyond AG Builder:** any varco app deployed behind PgBouncer in transaction mode —
the default recommendation for connection-constrained Postgres — has a distributed lock that leaks and
a `release()` that silently no-ops. Nothing in the API surface signals it; `release()` returning
`false` is indistinguishable from "someone else released it first".

What `varco` would need — **a small addition alongside what is already there, not a redesign:**

- A **transaction-scoped implementation** of the existing `AbstractDistributedLock` ABC using
  `pg_try_advisory_xact_lock`, as a sibling to `SAAdvisoryLock` (`SAXactAdvisoryLock`) or as a
  constructor flag on it. The abstraction, the key-hashing helper (`:78`) and the SQLAlchemy wiring
  all already exist and are reused unchanged.
- Scope is necessarily **the enclosing transaction** — that is the point — so the ABC's
  `acquire`/`release` pair maps to "acquire, then let commit release", with `release()` a no-op.
  If that does not fit `AbstractDistributedLock` cleanly, an async context manager
  (`async with lock.xact(key, session):`) is the natural shape.
- **A documented warning on the existing session-scoped class** naming transaction pooling as
  unsupported. Even without the new class, this alone converts a silent leak into a known constraint.

**Interim:** AG Builder uses `pg_advisory_xact_lock` directly in its own periodic-job serialisation
rather than binding `AbstractDistributedLock`. ⚠️ As with U-13's wrapper, this is **written to be
deleted** — if the transaction-scoped implementation lands upstream, the platform binds the ABC and
deletes its local call. It is not a working-around under the standing rule: a lock that silently fails
to release cannot wait on someone else's release cycle.

---

### U-17 · The job store has no scheduled, delayed, or retry-after execution {#u-17}

**Requirement:** R-045 (erasure executes on a schedule), NFR-9 (13-month aggregates swept daily),
R-027/R-048 (audit delivery must retry)
**Decision:** T5.5 periodic-job inventory — retention sweeps, erasure execution
([ADR-017](design/agbuilder/architecture/decisions/ADR-017-erasure-execution-model.md)), orphan
recovery ([ADR-032](design/agbuilder/architecture/decisions/ADR-032-orphan-attempt-recovery-policy.md)),
lease expiry ([ADR-033](design/agbuilder/architecture/decisions/ADR-033-run-ownership-lease-heartbeat-and-fencing.md)),
warm-pool refill ([ADR-023](design/agbuilder/architecture/decisions/ADR-023-sandbox-warm-pool-and-single-use-isolation.md))
**Status:** ✅ CLOSED (items 1–3) — re-verified in source 2026-08-25 (Plan 016 / RL-4; the
2026-08-04 status below was accurate at the time but is now stale). `varco_core/varco_core/job/base.py:252`
(`Job.run_at: datetime | None`), `:1145-1176` (`AbstractJobRunner.submit(..., run_at=, delay=)`),
`:284` ("with a `retry_policy` on the runner"), `varco_sa/varco_sa/job_store.py:591-680`
(`try_claim` now honours `run_at IS NULL OR run_at <= now`), and
`varco_fastapi/varco_fastapi/job/runner.py:123-186,683-703` (`retry_policy=`/`callback_retry_policy=`,
binding `RetryPolicy.compute_delay(attempt)` into `Job.as_retry(run_at=...)`) satisfy items 1–3 of
"What `varco` would need" below exactly. Item 4 (terminal `DEAD` state / DLQ hand-off) also
shipped — `JobStatus.DEAD` (`job/base.py:127`) and `as_dead()` (`:513`) — but the item explicitly
says it "overlaps U-6's DLQ ask", and **U-6 itself remains open** (`OutboxRelay` still has no
attempt tracking or dead-letter path) — this entry's closure does not imply U-6 is closed.

The job lifecycle is `PENDING → RUNNING → COMPLETED | FAILED | CANCELLED` with **no intermediate
states and no time dimension**. There is no `run_at`, no `next_retry_at`, no `delay`, no `interval`,
and no cron concept anywhere in `varco_core.job`. `try_claim` claims the oldest eligible `PENDING`
row; "eligible" cannot currently mean "and not before time T".

Every varco app that needs a retry with backoff, a delayed job, or anything periodic has to build the
same thing outside the job store — while `varco_core.resilience` **already models exactly this**:
`RetryPolicy` is a frozen dataclass and `retry(policy)` is applicable at runtime (verified in this
project's A-3). The retry semantics exist; they are simply not reachable from `AbstractJobStore`.

**Why this is a small ask.** The claiming primitive is already correct — `SAJobStore.try_claim` does an
atomic `SELECT … FOR UPDATE SKIP LOCKED` (`varco_sa/varco_sa/job_store.py:423`). Adding a time
dimension is **one nullable timestamp column and one predicate in an existing query**, not a new
component:

What `varco` would need:

1. **`run_at: datetime | None` on the job model**, and `AND (run_at IS NULL OR run_at <= now())` added
   to `try_claim`'s existing `WHERE`. Indexed alongside the status column. Null keeps every current
   caller's behaviour identical, so this is backwards-compatible.
2. **`enqueue(..., run_at=...)` / `submit(..., delay=...)`** on `AbstractJobRunner` — a pass-through to
   (1).
3. **Bind the existing `RetryPolicy`** to the job store: on failure, instead of terminal `FAILED`,
   set `PENDING` with `run_at = now() + policy.backoff(attempt)` until attempts are exhausted. This
   reuses `varco_core.resilience` rather than introducing a second retry model, and gives job retry
   the same semantics `@listen` consumers already have.
4. **Optional, and the natural follow-on:** a terminal `DEAD` state or DLQ hand-off when attempts are
   exhausted, reusing the per-driver `dlq.py` concept the event-bus drivers already carry. This
   overlaps [U-6](#u-6)'s DLQ ask — **one DLQ concept serving both consumers and jobs is the better
   outcome than two**, and both should be designed together.

**Recurrence is deliberately NOT requested.** Given (1), a periodic job is expressible as "on
completion, enqueue the next occurrence with `run_at = now() + interval`" — a handful of lines in the
caller, with no scheduler component, no leader election and no cron parser upstream. Asking varco for
cron would be asking for a subsystem when a column suffices. AG Builder builds recurrence on top of
`run_at` and does not consider that a gap.

**Interim (historical — superseded, see Status above):** AG Builder carries `run_at` in its own
periodic-job table and applies the predicate in its own claim query, keeping the shape above so
the migration to upstream `run_at` is a column rename and a binding switch — now that it has
landed upstream, that switch is available.

**Shipped.** Per Plan 016/RL-4 re-verification, items 1–3 of this entry are closed.

---

### U-19 · `request_token` stores the raw undecoded Bearer JWT at rest {#u-19}

**Requirement:** adjacent to [ADR-061](design/agbuilder/architecture/decisions/ADR-061-credential-resolution-at-the-model-call-boundary.md)
(credential handling); backed by research brief 037's OWASP/NIST finding
**Decision:** [ADR-072](design/agbuilder/architecture/decisions/ADR-072-no-personal-data-in-a-job-row-references-only.md)
§3.6 (D-68) — mitigated locally, not requested as a fix
**Status:** ✅ verified in source — `varco_core/job/base.py:187-188`; the anti-pattern classification
is a research finding, not source inspection: [brief 037](design/agbuilder/research/037-job-payload-body-handling-and-erasure.md)
cites OWASP / NIST

The job model's `request_token` field (`varco_core/job/base.py:187-188`) stores the **raw undecoded
Bearer JWT at rest** in the queue table. Per research brief 037, this is a recognized anti-pattern
per OWASP / NIST: JWTs are base64-encoded, not encrypted, so any PII carried in claims is directly
readable at rest. This is **both** a credential-at-rest problem and a data-retention problem, adjacent
to ADR-061's credential-resolution work. The standard mitigation the research names is to store a
reference (issuer + subject id) or a hash, rather than the token itself.

**Priority reasoning — record this explicitly.** Filed **P1** because the research backing is strong —
a named anti-pattern with OWASP/NIST citations, not a guess — **but it is a *report*, not a
dependency**. AG Builder's own mitigation is local and cheap: ADR-072 §3.6 has the enqueue wrapper
decline to populate the column at all, and workers mint their own service credentials under their
[ADR-069](design/agbuilder/architecture/decisions/ADR-069-fail-closed-worker-tenant-context-by-scope-not-by-call.md)
scope binding instead of replaying the enqueuer's token. That mitigation also removes a token-replay
surface — a security consequence beyond the retention one. Nothing in AG Builder is blocked on varco
changing this field.

**What varco would need:** replace or supplement `request_token` with a reference shape (issuer +
subject id, or a hash) rather than the raw token, per the mitigation the research names above.

**Interim:** none needed beyond ADR-072 §3.6 — the column is simply left unset by AG Builder's enqueue
wrapper; workers authenticate under their own ADR-069 scope binding instead.

**References:** ADR-072 §3.6, §4.4 · brief 037 · ADR-061 · ADR-069.

---

## P2 — medium

### U-7 · No distributed rate limiter {#u-7}

**Requirement:** R-057 (per-tenant rate and concurrency limits — SHOULD, not a launch blocker)
**Status:** ✅ verified in source

`varco_core/resilience/rate_limit.py` provides `RateLimitConfig`, a `RateLimiter` ABC,
`InMemoryRateLimiter`, a `rate_limit` decorator and `RateLimitExceededError`. The abstraction
is right; only the in-memory backend exists.

Per-tenant limits across replicas need a shared backend — a `RedisRateLimiter` in
`varco_redis` (token bucket or sliding window via Lua, mirroring how `RedisLock` already does
atomic Lua operations). Concurrency limiting (N in-flight runs per tenant) is a different
primitive from rate limiting and may warrant its own abstraction.

Low urgency: R-057 is a SHOULD, and ADR-002's single-replica target means the in-memory
limiter is adequate at launch. It becomes a blocker when the platform scales past one replica.

---

### U-8 · `ARCHITECTURE.md` is stale on `SkillAdapter` {#u-8}

**Status:** ✅ verified — documentation contradicts source

`ARCHITECTURE.md` says A2A tasks are synchronous-only and that `/tasks/{task_id}` is
"echo-back, no history stored". `skill.py` supports `job_runner` + `job_store`, async
submission with `state: working`, polling, and `/tasks/{task_id}/history`.

This cost real time during design — I recorded a blocking gap that did not exist. Worth a doc
pass over the A2A and MCP sections generally.

---

### U-18 · Job store has no bulk/predicate delete, TTL, or `expires_at` — retention is id-at-a-time {#u-18}

**Requirement:** none currently — demoted from an R-045 (GDPR erasure) dependency; see Priority
reasoning below
**Decision:** [ADR-072](design/agbuilder/architecture/decisions/ADR-072-no-personal-data-in-a-job-row-references-only.md)
§3.7 (D-68), constrained by [ADR-071](design/agbuilder/architecture/decisions/ADR-071-job-envelope-in-job-metadata-typed-at-write-lenient-at-read.md)
§1.4
**Status:** ✅ verified in source (recorded via ADR-072 §1.4, corroborating ADR-071 §1.4) —
`delete(job_id)` only: **no bulk delete, no predicate delete, no TTL, no `expires_at`**; also no size
check, compression, or truncation anywhere on the write path

`AbstractJobStore` exposes a single-id `delete(job_id)`. There is no bulk delete, no predicate delete
(e.g. "delete all COMPLETED rows older than T"), no TTL, and no `expires_at` column anywhere in
`varco_core` / `varco_sa`'s job model. A completed-job retention sweep therefore has to enumerate ids
one at a time.

**Priority reasoning — record this explicitly, it is the point of the entry.** This gap started as a
D-67 candidate that looked GDPR-blocking: a completed job row with no bulk-expire mechanism, sitting
behind R-045. **ADR-072 §3.7 demoted it.** Under D-68's "no personal data in a job row" rule
(INV-21), a completed job row carries no subject content, so retention over it carries **no
compliance clock** — it is an operational/storage concern only. **It is therefore P2 hygiene, not a
P0/P1 compliance dependency.**

**What varco would need:** a predicate or bulk delete (e.g. `delete_where(status=..., completed_before=...)`)
and/or an `expires_at` column honoured by a background sweep — the shape brief 037 found as the field
standard in Oban and Sidekiq (River: future work; Celery: manual tasks; Faktory: undocumented).
`delete(job_id)`-only is anomalous, not unique.

**Interim:** a platform-side periodic pruner deletes completed job rows id-at-a-time. Its window and
scheduling are owed to **T5.5's periodic-job inventory** — not yet decided, so not asserted here.

**Update 2026-08-09 (T5.5.2 / D-81 / [ADR-084](design/agbuilder/architecture/decisions/ADR-084-inv-17-per-job-placement-two-axis-classification.md) §Note 3):**
the classification pass gave this gap a **second, non-compliance cost** that the P2 demotion did not
account for. The pruner (inventory row 14) is TICK · platform `--mode worker` · **loop-safe** — the N
round-trips are all awaited, so INV-17 is satisfied. But under **INV-6**'s transaction pooling a server
connection is pinned for the whole transaction, making this the sharpest instance of ADR-068's **V-3**
finding (pool starvation, not event-loop contention, is the real NFR-3 threat). Mitigated platform-side
by chunking into bounded transactions against the worker's separate pool budget, so it stays an interim
work-around rather than a block. **Priority unchanged at P2** — the argument for the fix is now
"id-at-a-time converts a hygiene sweep into pool pressure", not merely enumeration ugliness, which
strengthens the case for `delete_where(...)` without promoting the entry.

**References:** ADR-072 §1.4, §3.7, §4.4 · ADR-071 §1.4 · ADR-084 §Note 3.

---

## P3 — evaluated, no change requested

### U-10 · MCP client (capability-map gap G-2) {#u-10}

**Status:** ✅ evaluated — **no upstream change requested**, gap closed as "correct, wrong direction"

`varco_fastapi.router.mcp`'s `MCPAdapter` exposes varco routes *as* an MCP server. R-009/R-010 need
an MCP **client**. These are different products, not a defect: the adapter is the natural vehicle
for **W-001** (MCP proxy, post-v1) and should stay as it is.

[ADR-007](design/agbuilder/architecture/decisions/ADR-007-mcp-client-on-official-sdk.md) adopts the
official `mcp` v2.x SDK (MIT) behind an `McpClientPort`. Nothing in D-6 asks varco for anything.

⚠️ One knock-on that is **ours, not varco's**, tracked as OQ-13 in the design ledger: D-6b spawns
stdio MCP servers inside the gVisor sandbox executor, so our own `SandboxDriver` contract
([ADR-004](design/agbuilder/architecture/decisions/ADR-004-sandbox-driver-port-with-gvisor.md))
must grow a long-lived bidirectional stream mode alongside one-shot exec.

Note U-8's suggested doc pass should cover the MCP section too — the adapter's purpose (server-side,
W-001) is not obvious from the current docs.

---

### U-9 · Graph-shaped durable execution {#u-9}

**Status:** deliberately **not** requested — recorded so the reasoning is not relitigated

`SagaOrchestrator` persists state after every step and resumes from `completed_steps`, but a
saga is a **linear step list**, not a graph with branching, and its compensation model does
not match agent semantics.

[ADR-003](design/agbuilder/architecture/decisions/ADR-003-custom-event-sourced-execution-engine.md)
decided AG Builder builds its own event-sourced graph interpreter, reusing varco's
`SAJobStore.try_claim()`, outbox and distributed locks underneath. Generalising
`SagaOrchestrator` into a graph engine would put product-specific semantics (node fallback
policy, stop conditions, agent recursion) into a general-purpose framework where they do not
belong.

**No upstream change needed.** If the interpreter later grows a genuinely reusable core, that
is a candidate for extraction into varco — but not before it has proven itself here.

---

### U-14 · Auth ergonomics — the two layers ship unwired {#u-14}

**Decisions that surfaced it:** [ADR-055](design/agbuilder/architecture/decisions/ADR-055-idp-asserted-groups-mapped-to-a-fixed-internal-role-vocabulary.md),
[ADR-056](design/agbuilder/architecture/decisions/ADR-056-two-layer-authorization-enforcement.md)
**Status:** ✅ re-verified in source 2026-08-25 (Plan 016 / RL-4, Step 40) — `varco_fastapi/varco_fastapi/auth/guard.py:61`
(`class RouteGuard`) and `varco_core/varco_core/auth/policy.py:548` (`class
PolicyEngineAuthorizer`) both still exist as described, with no combined middleware or
route-level policy decorator added since, and no resource-hierarchy/role-hierarchy/refresh/
introspection additions found. The described absences **still hold**; nothing to close.
**P3 — report only, no request** (unchanged).

varco ships two authorization layers and no path between them:

- `RouteGuard` (`varco_fastapi/auth/guard.py:60-142`) — route-build-time; scopes, roles, grants,
  predicate. **No entity access.**
- `PolicyEngineAuthorizer` (`varco_core/auth/policy.py:547-638`) — service-layer, entity-aware.

There is **no combined "extract auth → check role → check policy" middleware and no route-level policy
decorator**, so every consuming product re-derives the wiring. Also absent: resource hierarchy in the
`PolicyEngine` abstraction (child inherits parent permissions), role-hierarchy and deny rules at the
abstraction level (both backend-dependent), refresh-token flow, and token introspection.

**No request is made.** ADR-056 wires the two layers *deliberately* — the route guard states a
**necessary** condition and may never authorize, while the service layer is authoritative. That
asymmetry is a design choice, not a workaround, and a pre-composed middleware would likely obscure it.
Reported as a recurring integration cost other consumers will also pay, and as context should varco
ever offer a composed helper: **it should preserve the ability to keep the layers asymmetric.**

---

### U-15 · HTTP conventions absent from `varco_fastapi` {#u-15}

**Decision that surfaced it:** [ADR-059](design/agbuilder/architecture/decisions/ADR-059-builder-api-cross-cutting-contract.md)
**Status:** ✅ re-verified in source 2026-08-25 (Plan 016 / RL-4, Step 40) — a repo-wide search of
`varco_fastapi/varco_fastapi/` for pagination-envelope classes, `idempotency`/`IdempotencyKey`
handling, and API-version support found none; `varco_core/varco_core/query/` (the AST/pagination
system) also has no envelope class. The described absences **still hold**; nothing to close.
**P3 — report only, no request** (unchanged).

varco supplies error-to-HTTP mapping (`varco_core/exception/http.py:88-295` — `ErrorMessage`,
`error_code_for()` MRO walk, `register_error_code`), which AG Builder adopts as-is. It supplies **no**
pagination envelope (in/out params only), **no** idempotency-key handling, and **no** API versioning
support. Each consuming product builds its own.

**No request is made** — AG Builder's needs here are small and specific (cursor pagination on
append-only collections, offset elsewhere, idempotency on exactly one endpoint), and a generic
upstream abstraction would probably not fit them better than 200 lines of our own. Reported so the
pattern is visible if several consumers converge on the same shape.

---

## `providify`

Constructor and class-annotation injection, sync/async resolution, scopes, `@PostConstruct`
lifecycle hooks, `@Configuration` modules, `scan()` and `install()` cover what the design
needs. Two gaps identified from inside `varco` itself (below), plus U-12.

`providify` carries unusual weight in this architecture: R-060 requires every deferred W-item
to have an extension seam, and [ADR-003](design/agbuilder/architecture/decisions/ADR-003-custom-event-sourced-execution-engine.md)
expresses those seams as **protocols bound in the DI container** — adding a feature becomes
registering a binding rather than editing call sites. Standalone vs platform runner mode
(R-017) is likewise a wiring profile, not a code fork.

Two things to watch as the design proceeds:

- **Runtime rebinding.** R-031 installs new node types from JSON-schema plugins with no code
  change. If plugins register `NodeExecutor` implementations after container validation, we
  need either a registry that is itself a singleton (likely fine — no `providify` change) or
  post-validation registration support (would be a gap). Confirm during D-7.
- **Named/qualified bindings.** Multiple implementations of one protocol selected by
  configuration — e.g. several `PayloadStore` backends. Worth confirming `providify` supports
  qualifier-based resolution; if not, this becomes U-10.

---

## Gaps deliberately NOT requested

Recorded so they are not relitigated.

### D-63 · the worker process model (2026-08-04) — **ours, not varco's; no upstream request**

The varco survey run for T5.1 found that jobs execute as `asyncio.Task` **inside the HTTP server
process** — `JobRunner` (`varco_fastapi/varco_fastapi/job/runner.py:36`) spawns a task per job, and
all three background pollers (`OutboxRelay`, `InboxPoller`, `JobPoller`) are in-process tasks. varco
has **no worker daemon, no separate entrypoint, and no multi-process job execution.**

**This is not being asked for upstream, and the reason is not effort — it is ownership.** The
standing rule asks varco for capabilities that belong in varco. How many processes AG Builder
deploys, and which module runs in which one, is decided by
[ADR-002](design/agbuilder/architecture/decisions/ADR-002-modular-monolith-three-deployables.md)
(platform · runner · sandbox · builder SPA). A worker process is a **topology decision about our
system**, and it was never varco's to make. Asking varco for "a worker deployment model" would be
asking a library to decide our architecture.

The distinction against [U-17](#u-17) is the test the user set for this round: *is it a simple
addition or extension of something already present?* `run_at` extends an existing claim query, so it
goes upstream. A worker process is not an extension of anything varco has — there is no entrypoint
abstraction, no daemon, no supervision concept to extend.

⚠️ **What this leaves open:** whether AG Builder's background jobs run inside the existing platform
deployable (varco's in-process model, no new deployable) or in a **fifth deployable**, is a live
question against ADR-002 and NFR-3's 300 ms budget — a retention or erasure sweep sharing an event
loop with the builder API is a latency risk. **Owed by T5.1's follow-on**, and it is an AG Builder
decision either way. If the answer is a separate worker, varco's in-process `JobRunner` is simply not
the component used, and still no upstream ask arises.

### D-17 · platform↔runner transport (2026-08-02) — **event bus evaluated and declined; no upstream request**

[ADR-018](design/agbuilder/architecture/decisions/ADR-018-platform-runner-transport.md) decided
platform↔runner communication is **REST commands (`AsyncVarcoClient`) + an SSE run-step stream
replayed from ADR-003's append-only log by cursor — no message broker in the v1 topology.**

**varco's event bus was evaluated, in source, and declined.** The working rule put it first in
line (`AbstractEventBus`, backend-plural: Kafka, NATS JetStream, Redis Streams, Redis Pub/Sub,
in-memory). Three findings from the source killed it for this seam:

1. **No request-reply.** `AbstractEventBus` is `publish`/`subscribe` only, purely fire-and-forget.
   Two of the four platform↔runner interactions ("start a run, get a run id"; "cancel a run") are
   request/reply by nature; building them on the bus means reinventing correlation-id +
   reply-topic RPC on top of pub/sub.
2. **`varco_core` is 1.1.3, Development Status :: Alpha** — not thread-safe (single event loop
   only), orders per-partition only (not globally), and ships no Kafka DLQ (already tracked as
   U-6). Concentrating the platform's most critical seam on the least-mature dependency in the
   stack was rejected on that basis alone.
3. **A hard broker dependency contradicts R-017.** The air-gapped standalone runner has no broker
   to reach, so a bus-based control path would still need a REST path built and tested — i.e. the
   project would ship and maintain two transports to arrive where the REST-only option starts.

**A user offer to implement request-reply in `varco` was made and declined, with the reasons
recorded verbatim in the ADR** (not merely "not needed now"): request-reply does not solve the
constraint that actually decided this — R-017's broker-less air gap — so even a fully-featured
request-reply bus would still require the same REST control plane for standalone mode; separately,
request-reply-over-a-bus reintroduces the caller-blocks temporal coupling that HTTP already gives
natively, while keeping full broker operational complexity, and it would place the system's
highest-consequence seam on an Alpha library's newest, least-exercised feature.

**The upstream ask was redirected, not dropped**, to the two items that actually gate delivery:
**U-1** (subject-scoped encryption keys) and **U-2** (key destruction semantics), both against
ADR-001/ADR-017's crypto-shredding erasure model. Those are locked-requirement P0 blockers;
request-reply-on-the-bus blocks nothing. **U-1/U-2 remain the only P0 upstream blockers and their
priority is unchanged by this decision.**

Hybrid bus fan-out (Option C in the ADR) stays reachable at no cost — a bus publisher would be
purely additive over the same run-step log — so this is a "not now, not never" decision, not a
foreclosure.

### D-10 · builder frontend stack (2026-08-02) — **no upstream request**

[ADR-015](design/agbuilder/architecture/decisions/ADR-015-builder-frontend-stack.md) settled the
whole builder frontend: React 19 + React Flow, RJSF v6 schema forms, a separately deployed SPA, a
hand-rolled SSE client, and hybrid edit-time type checking.

**Neither `varco_*` nor `providify` has a surface here.** D-10 is entirely TypeScript and
browser-side; the libraries are Python. Nothing in the decision was shaped by a library limitation,
so the standing working rule ("ask for the fix upstream rather than working around it") never
engaged. The one server-side consequence — the run-event stream the SPA consumes — was already
decided in [ADR-013](design/agbuilder/architecture/decisions/ADR-013-agent-serving-surfaces-and-the-openai-compatible-endpoint.md)
/ [ADR-014](design/agbuilder/architecture/decisions/ADR-014-a2a-surface-caller-authentication-and-the-run-event-stream.md)
and its upstream implications are already tracked as U-3/U-4.

---

### U-12 · `providify` — no interface conformance check at registration (P2) {#u-12}

**Raised by:** D-42 / [ADR-049](design/agbuilder/architecture/decisions/ADR-049-the-node-executor-contract-and-the-providify-boundary.md),
P2 round 3, 2026-08-03.
**Status:** ⚠️ STILL OPEN — re-verified against **providify 2.0.0's own installed source**
2026-08-25 (Plan 016 / RL-4, Step 39):
`.venv/lib/python3.12/site-packages/providify/validation.py:58-91` (`class IssueKind(StrEnum)`:
`MISSING_BINDING`, `MISSING_BINDING_DEFAULTED`, `MISSING_BINDING_DEFERRED`, `AMBIGUOUS_BINDING`,
`CIRCULAR_DEPENDENCY`, `SCOPE_LEAK`, `LIVE_REQUIRED`, `UNRESOLVED_ANNOTATION`) and
`.venv/lib/python3.12/site-packages/providify/container.py:5290-5330` (`DIContainer.validate()`
docstring: *"Nothing is instantiated... additionally detects missing bindings, ambiguous
bindings, and static circular dependencies"*).

**Verdict.** Every `IssueKind` member is a **wiring-resolvability** check (can the graph be
built: is there a candidate, is it unambiguous, is there a cycle, does an annotation resolve, is
a scope/`Live[T]` rule violated). **None of them check interface *conformance*** — whether a
registered implementation's `__mro__`/method set actually satisfies the Protocol/ABC it is bound
against. `validate()`'s own docstring is explicit that it walks "the ENTIRE declared dependency
graph" for exactly the three defect classes named above, with **no fourth "does the
implementation structurally satisfy its interface" pass** — `bind(Interface, Implementation)`
still accepts an `Implementation` missing a method the `Interface` Protocol declares, and the
failure still surfaces at the first call to that method, not at `validate()`/`scan()` time. **The
gap this entry describes is NOT closed by providify 2.0.0.**

**Restated ask, in 2.0.0 terms.** A ninth `IssueKind` (e.g. `INTERFACE_NONCONFORMANT`), opt-in
(to avoid breaking duck-typed registrations that work today), that `validate()` raises when a
binding's implementation does not structurally satisfy a `runtime_checkable` `Protocol` (or fails
an explicit signature comparison against an ABC) it is registered against. This is additive to
the existing three-tier check (graph → scope → conformance), not a replacement for any of them.
**Verified in source** at `/home/edoardo/projects/providify/` (pre-2.0.0, D-42 filing) and now
also at the installed 2.0.0 wheel above — per the standing rule that produced U-8: read the
source, not `ARCHITECTURE.md`.

**What providify does today.** Registration is decorator-based (`@Component`, `@Singleton`,
`@Provider`) stamping metadata at import time, plus programmatic `container.bind()` / `register()` /
`provide()`, plus `container.scan("module", recursive=True)`. Validation at registration checks for
decorator metadata (`ClassBinding._has_own_metadata`) and a provider's return type hints; the
validation phase at first resolution detects circular dependencies, scope violations and abstract
methods. **There is no check that an implementation satisfies the interface it is bound against.**

**Why we want one.** ADR-049 defines `NodeExecutor` as a `Protocol` and ADR-009 §1 calls the kernel
set a providify-bound extension point. Conformance therefore rests entirely on static typing plus a
per-kernel contract test; the container will happily register a class that does not implement
`execute`, and the failure surfaces at run time on the hop rather than at startup.

**The ask.** An opt-in conformance check at `bind(Interface, Implementation)` / registration time —
`runtime_checkable` Protocol structural check, or an explicit signature comparison — that fails fast
at container build. Opt-in matters: making it mandatory would break duck-typed registrations that
work today.

**Priority P2, not P0.** We have a working substitute (static typing + contract tests, already an
ADR-020 obligation), so this blocks nothing. It would convert a class of run-time failure into a
startup failure, which is worth having but is not on the delivery path.

⚠️ **Related but NOT an upstream ask — INV-3 is ours to enforce.** `container.scan()` loading code
from an arbitrary importable module is correct behaviour for a DI container and we are not asking for
it to change. What ADR-049's **INV-3** forbids is *us* pointing it at an operator- or tenant-writable
path, now that D-40c has established tenant-scoped plugin installation. That is a rule for our
codebase and ADR-020's addendum, not a providify defect.

📌 **Maturity note, for the same register as ADR-018's (updated 2026-08-25, Plan 016):**
`providify` is now **2.0.0** — the CHANGELOG's own stated purpose for the 1.x → 2.x jump is
"purely to escape the Alpha classifier" (`providify/CHANGELOG.md:20`), not a breaking-change
signal. The DI surface ADR-049 depends on is deliberately small — decorator registration and
async resolution — so an upstream break stays contained.

---

### U-20 · `container.provide()` has no way to register a factory whose interface is known only at call time {#u-20}

**Raised by:** `varco_core`'s own Plan 014 (DI settings + provider-helper refactor), 2026-08-23,
while consolidating six independently hand-rolled copies of the same workaround into one shared
internal helper (since deleted — Plan 016).
**Status:** ✅ **CLOSED — fixed upstream in providify 2.0.0** (Plan 016 / RL-2, 2026-08-25). Verified
in source: `DIContainer.provide(self, fn, *, returns: Any = None)` (`providify/container.py:989`),
`Provider(..., returns: Any = None)` (`providify/decorator/scope.py:493`); precedence is call-site
`returns=` > `@Provider(returns=…)` > resolved return annotation (`container.py:995-998`). This is
exactly the shape requested below. The interim `varco_core` compat shim
has been deleted; every one of its former call sites (`varco_ws/di.py`, `varco_fastapi/di.py`,
`varco_fastapi/router/skill.py`, `varco_fastapi/router/mcp.py`, `varco_sa/di.py`,
`varco_beanie/di.py`) now calls `container.provide(Provider(...)(factory), returns=…)` /
`Provider(returns=…)(factory)` directly. See CHANGELOG §2.0.0 lines 199-214 for the upstream
changelog entry.

**Status prior to closure (kept for history):** ✅ verified in source — `providify/binding.py:456-505`
(`ProviderBinding.__init__`), `providify/container.py:658-672` (`DIContainer.provide`),
`providify/decorator/scope.py:489-570` (`Provider`).

**What providify does today.** `@Provider` stamps registration metadata on a function and returns
it unchanged (`scope.py:538-566` — never reads `__annotations__`). The interface a provider binds
under is derived later, exactly once, when `container.provide(fn)` constructs a `ProviderBinding`:
it reads `fn`'s **raw, static return annotation** (`_raw_annotations(fn)["return"]`) and resolves it
against `fn.__globals__` (`binding.py:496-505`). `_resolve_return_annotation()` already handles a
`str` annotation under PEP 563 correctly (`_eval_annotation` + `get_type_hints`, `binding.py:337-425`)
— quoted forward references and nested generics both work. **Neither `@Provider` nor
`DIContainer.provide()` accepts an explicit interface override** (`scope.py:479-486`,
`container.py:658`) — the return annotation is the *only* channel providify offers for stating what
a factory produces.

**Why this is a real gap, not a PEP-563 quirk.** The annotation-resolution machinery works exactly
as documented for every *statically expressible* return type. It cannot work for a factory whose
target interface is a **parameterised generic alias computed at runtime** — e.g. one `AsyncRepository[D]`
provider built per domain-model class inside a loop (`varco_sa.di`, `varco_fastapi.client.bind_clients_from`),
or a factory built inside `varco_ws.di`/`varco_fastapi.router.mcp`/`router.skill` where the concrete
class is a constructor argument, not a name in the closure's own signature. No annotation string could
name `D` before the loop iteration exists to bind it — this isn't a case `_resolve_return_annotation`
declined to support, it's a case with no annotation to write in source at all.

**The workaround this forced, independently, six times.** Every one of those call sites reached
past the documented API into `factory.__annotations__["return"] = <computed type>`, mutating the
closure's `__annotations__` dict by hand immediately before calling `container.provide(factory)` —
relying on the fact (verified, not assumed) that neither `@Provider`'s decorator body nor
`container.provide()` itself reads the annotation before `ProviderBinding.__init__` does. Each site
carried its own copy-pasted `DESIGN:` comment justifying the ordering; one (`varco_fastapi.di`, prior
to this cleanup) had the reasoning **factually wrong** about why the ordering mattered. `varco_core`
has now collapsed six of the seven sites into one internal helper
(since deleted — Plan 016) precisely so there is one place to delete when this
lands upstream — but every one of the six is still reaching into a private attribute
(`__annotations__`) that providify's public API never promised as a registration mechanism.

**The ask.** Give `container.provide()` (and/or `@Provider`) a supported, explicit way to state the
interface, bypassing annotation derivation entirely:

```python
# one shape that would work:
container.provide(factory, returns=AsyncRepository[User])

# or, mirroring @Provider's own kwarg style:
@Provider(returns=lambda: AsyncRepository[User])   # deferred — evaluated at provide() time
def _repo_factory(uow: Inject[IUoWProvider]) -> Any: ...
```

This removes the only reason any varco call site currently mutates `__annotations__` on someone
else's function object, and removes the trap where the *ordering* of decorate-vs-patch is
load-bearing but invisible in the type signature of either `@Provider` or `provide()`.

**Priority: P2 — hygiene / API-surface completeness, not a blocker** (as originally filed — now moot,
see Status above).

**Interim (historical, deleted in Plan 016):** a `varco_core` internal helper — one shared,
documented, tested function that did the annotation-patch-then-register dance, explicitly named
and positioned (module docstring) as a shim to be deleted the day this landed. It landed; the shim is
gone.

---

## Maintainer response — source corrections (2026-08-11)

Filed against plan `plans/005-upstream-gaps.md`. Four claims in this register did not survive
contact with source; recorded here so the register and the source stop diverging.

1. **U-11 is "add a lease dimension", not "honour an existing parameter".** `try_claim` at
   `varco_core/varco_core/job/base.py:517` takes no `ttl` parameter at all — there is nothing
   dormant to activate. The accepted-and-ignored `ttl` the register describes belongs to a
   different class, `SAAdvisoryLock.try_acquire(key, *, ttl)`
   (`varco_sa/varco_sa/advisory_lock.py:166-186`), which is a separate gap (U-16). Plan Phase 4
   *introduces* lease parameters on the job store; it does not wire up an existing one.

2. **U-8: the documentation was the wrong artifact, not the code.** Async task submission
   already works — `SkillAdapter.__init__` (`router/skill.py:264-266`) accepts `job_runner` +
   `job_store`, and `POST /tasks/send` already returns `state: working` with polling support.
   `ARCHITECTURE.md` said otherwise ("v1 tasks are synchronous... echo-back, no history
   stored"); that has been corrected in Phase 0 of the plan. No code changed for U-8 itself.

3. **U-7's rate-limiter leg is already shipped.** `RedisRateLimiter`
   (`varco_redis/varco_redis/rate_limit.py:169`) exists, is exported, and implements a
   distributed sliding window over Redis sorted sets with an atomic Lua script. Only U-7's
   second leg (distributed **concurrency** limiting, a different primitive from rate limiting)
   remains open — tracked as `RedisBulkhead` in Phase 8.

4. **U-19 cannot simply stop populating `request_token` by default.**
   `varco_fastapi/varco_fastapi/job/runner.py:784-786` forwards `job.request_token` as the
   completion callback's `Authorization: Bearer` header, and `router/base.py:1567-1582`
   auto-populates it on every async-capable route. Removing or blanking it by default breaks
   callback auth. U-19 lands as additive reference fields
   (`request_issuer`/`request_subject`/`request_token_hash`) plus an opt-out flag
   (`store_raw_token=False`), never a default change.
