# Varco Backlog — Reliability & Service Integration

> Produced by `/discover` on 2026-08-17. Scope set by interview:
> **(1)** reinforce and simplify the Audit/DLQ reliability mechanisms;
> **(2)** make consuming another varco service trivial — no hand-written clients.
>
> **Shipping constraint (decided):** one coherent release, **breaking changes allowed**.
> ABC signature changes and client front-door collapse are in scope; a single
> migration note covers the upgrade.
>
> **Client topology (decided):** must support **both** in-process (monorepo, shared
> import) and cross-repo (no shared code) — with the *same* typed surface either way.
>
> Ordered by severity, then complexity ascending.

---

## Backlog

| ID | Feature | Sev | Cx | Rationale |
|----|---------|-----|----|-----------|
| **R2** | **Reliability metrics pack** — counters/gauges on DLQ push & depth, outbox lag & failure, audit writes, job lease reaps | 🔴 | S | "Nobody notices it died" — zero instrumentation exists on any of these paths today, so DLQ depth and outbox lag are unalertable. Uses the `@counter`/`register_gauge` layer already shipped. |
| **R3** | **Retention & pruning** — `delete_where(older_than=, limit=)` on `AbstractDeadLetterQueue` + `AuditRepository` | 🔴 | S | Both tables grow unbounded forever. `AbstractJobStore.delete_where` (Plan 005 Phase 6) already proves the chunked-sweep contract — this copies a working primitive to two more places. |
| **C1** | **Collapse the client front door** — one documented `client_for(...)`; demote `make_client`/`GenericClient`/`OpenAPIClient`/`ClientConfigurator`/`generate_client` to advanced; `bind_clients_from(*routers)` DI one-liner | 🔴 | S | The literal complaint: "there is some code right now, but it's very complex to use." 11 modules / ~4,550 LOC with 5 overlapping entry points. Mostly a facade + docs, little new code — highest value-per-effort in the release. |
| **R1** | **DLQ redrive** — `redrive(entry_id)` / `redrive_batch()` on the ABC + all backends + `varco dlq redrive` CLI | 🔴 | M | "Nobody can get it back." `pop_batch()` is read-only; there is no supported path to re-inject a dead letter onto the bus. The DLQ is currently a graveyard, not a retry buffer — the single biggest hole in the reliability story. |
| **R4** | **Tenant-scoped Audit + DLQ** — `tenant_id` filter on both repos, RLS policies on `varco_audit_log` / `varco_dead_letters` | 🔴 | M | `AuditEntry` carries `tenant_id` but `list_for_entity()` ([audit.py:204](varco_core/varco_core/service/audit.py#L204)) never filters on it — isolation is unenforced under `TenantIsolation.SHARED`. Hardening a real gap, verified in-session. |
| **R5** | **`ReliabilityPreset`** — one config object bundling retry policy + DLQ + audit + outbox wiring | 🔴 | M | "Make easier to use them." Opting into durability today means getting 5 separate wirings right (DLQ instance → `@listen` → `OutboxRelay` → `AuditLogMixin` → `AuditConsumer.register_to`); people skip it, so the safety net is missing exactly when needed. |
| **C2** | **Typed custom-route client methods** — replace generated `**kwargs: Any` with synthesized signatures + emitted `.pyi` stubs | 🔴 | M | `_VarcoClientMeta` already generates custom `@route` methods ([base.py:585](varco_fastapi/varco_fastapi/client/base.py#L585)) — but as `async def m(self, **kwargs: Any) -> Any`. Custom routes are the half of an API you'd otherwise hand-write a client for, and they're precisely the untyped half. |
| **C3** | **Portable contract export** — `varco export-contract app.routers:OrderRouter -o order.contract.json`, consumed by a different repo to build a fully typed client | 🔴 | L | The actual microservice unlock, and required by the "both topologies" decision. Makes `introspect_routes()` the single producer with two consumers (in-process class, exported descriptor) so the typed surface is identical either way — no shared Python import, no live server needed. |
| **R7** | **Beanie DLQ backend** — `varco_beanie/dlq.py` | 🟡 | S | Kafka, Redis, NATS and SA each ship a DLQ backend; Beanie does not. A Mongo-only deployment cannot persist dead letters at all. (Note: `varco_beanie/audit.py` **does** exist — the initial scan was wrong about that.) |
| **R6** | **Audit + DLQ REST admin & query surface** — `build_audit_router()` / `build_dlq_router()`; audit gains `list(actor=, action=, from=, to=, tenant=)` | 🟡 | M | `list_for_entity(type, id, limit)` is the *only* audit query — you cannot ask "what did this actor do last week." Follows the shipped `build_policy_router()` / `build_tenant_router()` precedent, and gives R1's redrive an operator UI. |
| **C4** | **Peer-service registry** — declare peers once (`VARCO_PEER_ORDERS_URL=…`), inject `Client[OrderRouter]` anywhere with auth-forward + resilience pre-wired | 🟡 | M | Removes per-call-site URL/profile/middleware wiring; reduces "integrate another varco service" to one env var plus one inject. Depends on C1's front door landing first. |
| **R8** | **Audit tamper-evidence** — hash-chain each entry to its predecessor (`prev_hash`) + `verify_chain()` | 🟢 | M | Turns the audit log from "a table anyone with UPDATE can edit" into evidence. Valuable for a compliance story (SOC2/HIPAA), but no in-session pain pointed at it — genuinely nice-to-have. |

---

## Dependencies

```
C1 (front door) ──┬─► C2 (typed custom routes)
                  ├─► C3 (portable contract)   ── C3 and C2 share one descriptor format
                  └─► C4 (peer registry)

R1 (redrive) ─────┬─► R6 (admin REST — needs something to drive)
R3 (retention) ───┘

R2, R4, R5, R7 are independent — parallelizable.
```

**Suggested first cut:** R2 + R3 + C1 (all S, all independent) land the fastest visible
wins; C3's descriptor format should be designed *before* C2 so both consume one schema.

---

## Parked

Ideas raised during discovery and deliberately not scheduled — recorded so they
are not re-litigated.

| Idea | Why parked |
|------|-----------|
| **DLQ auto-redrive scheduler** (exponential-backoff automatic retry from the DLQ) | Superseded in practice by R1 + `RetryPolicy.durable_delivery()` on the producing side. Automatic redrive of a genuinely poisoned message is a loop generator; keep redrive operator-triggered until R1 has real usage data. |
| **Read-auditing** (record who *read* what, not just mutations) | Different problem with a different cost profile — a read audit trail is 10–1000× the write volume and needs sampling/aggregation design. Revisit only if a compliance requirement names it. |
| **Beanie index-drift reconciliation** (`index_guard.py` is check-only) | Real gap, but belongs to the migrations feature area (Plan 006), not this release's reliability/integration scope. |
| **Online schema-change utilities** (zero-downtime add/rename/retype: double-write → backfill → flip → cleanup) | Same reason — migrations scope, and a large feature in its own right. Not reliability of *messages*. |
| **Per-endpoint circuit breaker on the client** | `varco_core.resilience` already provides `@circuit_breaker` and the client middleware stack can host it. Wire it as a `ClientProfile` recipe under C1 rather than as new machinery. |
| **OPA policy-engine backend** | Previously deferred in the authorization release; unchanged and unrelated to this scope. |
