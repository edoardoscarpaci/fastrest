# Plan 008 — Tenant control plane: entry-point convergence, fleet fan-out, and readiness

## Goal

Make the two tenant-onboarding entry points (REST and bus) converge on **one** code path
that owns the catalog transition, so an event-onboarded tenant is routable; then add a
**distinct broadcast API** (`request_provision` / `request_deprovision`) for fleet fan-out
whose event graph is acyclic *by construction*; then add a **fleet-readiness coordinator**
that flips a tenant to `ACTIVE` only once every declared store has provisioned it.

Phase 1 is a standalone defect fix and is shippable on its own.

---

## The defect (Phase 1)

The two entry points do different things:

```
REST   → TenantControlService.provision()
           catalog.get/add(PENDING) → provisioner.provision() → catalog.update_status(ACTIVE)
           → emit TenantCatalogChanged                                     ✅ routable

EVENT  → TenantProvisionConsumer.on_provision_requested()
           provisioner.provision()                                          ❌ catalog untouched
```

`varco_core/varco_core/tenancy/control/consumer.py:127` calls
`self._provisioner.provision(event.tenant_id)` directly; the consumer holds only
`_provisioner` and `_dlq` (`consumer.py:75-82`) and has no catalog reference at all.

Meanwhile the routing path is a catalog lookup:

- `varco_core/varco_core/tenancy/routing.py:96-99` — `catalog.get(tenant_id)`;
  `TenantNotFoundError` is coerced to `TenantStatus.DELETED`, and `routing.py:70` maps
  `DELETED → 404`.
- `varco_fastapi/varco_fastapi/middleware/tenant_resolution.py:64-78` — the identical
  pattern in the middleware.

**Result:** a tenant onboarded purely over the bus gets its schema/database created and is
then permanently unroutable. Storage exists; the catalog does not know about it; every
request 404s. The event path silently produces a half-tenant.

`on_deprovision_requested` (`consumer.py:130-150`) has the **mirror-image** defect, and it
is worse: it calls `provisioner.deprovision(confirm_destroy=True)` directly, so the
destructive DDL runs while the catalog still says `ACTIVE` (tenant stays routable with no
storage → 500s instead of the intended 410), the `TenantFanoutSupervisor` child is never
stopped, and the pool entry is never evicted — all three of which
`TenantControlService.deprovision()` does *before* destructive DDL
(`service.py:104-119`).

### Not implementer drift — a gap in Plan 007

`plans/007-multitenancy-isolation-strategies.md:852-864` (Phase 5, steps 4-5) specifies
retry, DLQ, `RetryPolicy.durable_delivery()`, and inbox dedup for
`TenantProvisionConsumer` and **never mentions the catalog**. The implementation is a
faithful rendering of an underspecified plan. This plan amends 007 by reference
(see *Amendment to Plan 007* at the end).

---

## Non-goals

- **Durable, restart-surviving readiness state.** The Phase 3 coordinator's partial
  readiness map is in-memory. Persisting it would mean an eleventh framework table plus a
  migration, to protect a window measured in seconds, when the documented recovery
  (`request_provision` again — idempotent at every layer) is cheaper. See RD-18.
- **Node/pod discovery or a service registry.** The expected fleet is *declared*, never
  discovered. See RD-17.
- **Auto-activating a tenant on a timeout.** A readiness timeout logs, alerts, and leaves
  the tenant `PENDING` (→ 503). It never flips `ACTIVE`. See RD-17.
- **Cross-tenant/cross-store distributed transactions.** Unchanged from 007's non-goals.
- **A new `TenantStatus` value.** `PENDING → 503` already means exactly "provisioning in
  progress, not yet routable"; the fleet-provisioning window needs no new state.
- **Changing `TenantIsolation.SHARED` behaviour.** Under the default strategy there is no
  per-node DDL, so nothing in Phase 2/3 is needed or wired.
- **A new dedup mechanism.** RD-1 (Plan 007) still holds: idempotency stays
  status-check + provisioner `IF NOT EXISTS` + the existing inbox primitives.

---

## Resolved decisions

**RD numbering continues Plan 007's sequence** (007 resolved RD-1 … RD-10; this plan
starts at RD-11). Same namespace deliberately: these decisions constrain and amend 007's,
and a second namespace would make cross-references ambiguous.

- **RD-11 — the consumer routes through `TenantControlService`; there is exactly one
  catalog writer.**
  The consumer takes `control_service=`, not `provisioner=`. Both entry points then share
  one transition (`catalog.add(PENDING) → provisioner → update_status(ACTIVE) → emit
  TenantCatalogChanged`).
  - ✅ Closes the unroutable-tenant defect for provision *and* deprovision at once.
  - ✅ Fan-out supervisor stop + pool eviction now happen on the event path too — they
    live in `TenantControlService.deprovision()` and were unreachable from the bus.
  - ✅ One place to change when the transition grows (migrations, readiness, audit).
  - ❌ Breaking constructor change. Mitigated by the shim in RD-12; near-zero real cost
    because the whole 007 tenancy tree is still uncommitted/unreleased at time of writing.

- **RD-12 — `provisioner=` survives one minor release as a shim that *requires* a
  catalog; `provisioner=` alone is a `ValueError`, not a warning.**
  `TenantProvisionConsumer(provisioner=…, catalog=…, producer=…)` builds a
  `TenantControlService` internally and raises `DeprecationWarning`. `provisioner=` with
  no `catalog=` raises `ValueError` naming the two-line fix.
  - ✅ Fails at construction/startup, not at 3 a.m. when the first bus-onboarded tenant
    404s. The only behaviour a bare `provisioner=` can produce is the defect itself; there
    is no correct thing to do with it.
  - ✅ Keeps the deprecation window honest — the shim is a *working* path, not a
    "warn and stay broken" path.
  - ❌ A hard break for anyone on a `main` checkout. Accepted: unreleased code, and the
    error message contains the fix verbatim.
  - Rejected: `DeprecationWarning` + keep calling the provisioner directly ❌ preserves a
    data-correctness bug behind a warning nobody reads.

- **RD-13 — the loop is prevented by an acyclic *event-type* graph, not by idempotency.**
  Commands (`TenantProvisionRequested`, `TenantDeprovisionRequested`) may produce facts
  (`TenantCatalogChanged`, `TenantNodeReady`). Facts may produce **nothing**. No handler
  anywhere may emit a command. `provision()` therefore never emits
  `TenantProvisionRequested` — broadcasting is a separate method (RD-14).
  - ✅ Structural and *testable*: a Phase-2 test asserts that no fact-event handler in
    `varco_core.tenancy` produces a command, and that `provision()`/`deprovision()` emit
    only facts. Acyclicity is a property of the type graph, not of a status check.
  - ✅ Removes the accidental safety Phase 1 destroys. Today no loop exists *only* because
    the consumer bypasses the control service; once RD-11 closes that, "provision emits
    the command it handles" would become a genuine cycle terminated only by idempotency.
  - ❌ Two verbs on the service instead of one clever one. That is the point.

- **RD-14 — `request_provision()` / `request_deprovision()` are broadcast-only and
  explicitly do NOT include the caller.**
  They emit the command and do nothing else: no catalog write, no local DDL. A node that
  must also provision itself calls `provision()` **first** (so a local failure surfaces to
  the REST caller before the fleet is told), then broadcasts.
  - ✅ Matches RD-4/RD-9: the standalone control plane holds the admin DSN and is not an
    app pod, so "ask the fleet" and "do it here" are genuinely different operations.
  - ✅ Ordering (local first, broadcast second) gives the operator a synchronous error.
  - ❌ A bundled node that calls only `request_provision()` never provisions itself.
    Mitigated by origin-skip semantics being documented on both methods and by the
    router exposing both actions distinctly.

- **RD-15 — commands carry `origin`; a consumer skips its own broadcasts.**
  `TenantProvisionRequested`/`TenantDeprovisionRequested` gain `origin: str | None = None`
  (wire-compatible: `Event` is a frozen pydantic model, the field defaults). A consumer
  whose `control_service.node_id` equals `event.origin` returns immediately (one DEBUG
  log).
  - ✅ "Don't subscribe to your own broadcasts" becomes a property of the *event*, not a
    deployment rule in a README. Provenance is carried, so skipping is exact, not
    heuristic.
  - ✅ Works identically in both 007 shapes: standalone (external publisher has
    `origin=None`, never skipped) and bundled (the local node acted synchronously already).
  - ❌ One more field on the wire, and an operator can defeat it by giving two processes
    the same `node_id`. Documented; `node_id` defaults to a process-stable value.
  - Rejected: *refuse to construct a consumer over a control service holding a
    cluster-admin provisioner* ❌ that is precisely the primary standalone topology
    (RD-1: onboarding "via queue/pub-sub" is handled by the control plane's own consumer).
    A guard that breaks the main use case is worse than no guard.
  - Rejected: *separate command and fact channels* ❌ the cycle was never channel-shaped;
    two channels add wiring without adding an invariant.

- **RD-16 — under fan-out the catalog has one authority; workers do not write it.**
  `TenantControlService(catalog_authority: bool = True)`. `True` (default) = today's
  behaviour, byte-identical. `False` = a worker node: `provision()` runs local DDL, emits
  `TenantNodeReady(tenant_id, node_id, store_id)`, and performs **no** catalog write.
  - ✅ Kills the premature-`ACTIVE` race that Phase 1 alone would introduce in a
    multi-consumer fleet: with N workers each writing the shared catalog, the *first* one
    to finish flips `ACTIVE` and the middleware starts routing to pods that have no schema
    yet. The request did not consider this.
  - ✅ A worker still *reads* the catalog and refuses to provision a `DELETED`/
    `DEPROVISIONING` tenant — a replayed old command cannot resurrect a deleted tenant.
  - ❌ Worker idempotency no longer comes from the status check; it comes from the
    provisioner's own `IF NOT EXISTS` semantics plus the consumer's event dedup. Stated in
    the docstring, asserted in a test.
  - ❌ `catalog_authority=False` with no coordinator and no manual terminator leaves the
    tenant `PENDING` forever. Mitigated by shipping *both* terminators in this plan
    (Phase 3 coordinator, and `POST …/activate` for anyone who orchestrates externally),
    and by a WARNING at construction when `catalog_authority=False`.

- **RD-17 — the expected fleet is a declared set of *stores*, not a count of pods, and a
  timeout never activates.**
  `TenantReadinessCoordinator(expected_stores: frozenset[str], …)`; each node reports
  `store_id` (`VARCO_TENANCY_STORE_ID`, defaulting to the service name). No default, no
  auto-discovery: constructing without `expected_stores` raises `ValueError`.
  - ✅ **This is the resolution of "the hard part."** Readiness is per *store*, not per
    *process*: ten `orders` pods provision the same orders database, so the first one
    makes `orders` ready and the other nine are idempotent no-ops. The set of stores is
    static deploy-time config that changes only when you add a service — a change you are
    already making by hand. Pod count, which changes on every autoscale event, never
    enters the calculation.
  - ✅ Under `TenantIsolation.SHARED` the set is a singleton and the coordinator is
    unnecessary — documented, not wired.
  - ❌ Adding a service means updating `expected_stores`; forgetting it makes tenants
    activate one store early. Mitigated: the coordinator logs the full expected/received
    set at every partial step and exposes `GET …/readiness`.
  - Rejected: `expected_nodes: int` ❌ wrong on the first autoscale or rolling deploy.
  - Rejected: node self-registration/heartbeat ❌ a distributed registry with a TTL-sizing
    problem and split-brain during rolling deploys — Plan 005's lease lesson, re-learned
    for no benefit.
  - Rejected: activate after `timeout_s` ❌ activates a fleet known to be incomplete. The
    timeout logs at ERROR, emits nothing, and leaves the tenant `PENDING` (503).

- **RD-18 — readiness state is in-memory; recovery is re-broadcast, not persistence.**
  A coordinator restart loses partial readiness. Recovery is `request_provision(tenant_id)`
  again: every layer is idempotent (event dedup → catalog status → provisioner
  `IF NOT EXISTS`), and each worker re-emits `TenantNodeReady`.
  - ✅ No eleventh framework table, no migration, no new durable contract.
  - ✅ The recovery command is the same command as the happy path — one operator verb.
  - ❌ A tenant mid-onboarding when the control plane restarts stays `PENDING` until
    someone re-broadcasts. Documented as an operational runbook step, surfaced by
    `GET /tenancy/tenants?status=pending`.

- **RD-19 — `TenantNodeReady` is a new event; `TenantCatalogChanged` cannot serve.**
  - ✅ `TenantCatalogChanged(tenant_id)` is a *cache-invalidation* fact about one shared
    catalog row (consumed by `CachedTenantCatalog.on_catalog_changed`,
    `cached_catalog.py:112-116`). N nodes writing that shared row emit N *identical* facts
    about the *same* row — aggregating them counts nothing. The request's framing
    ("aggregate N `TenantCatalogChanged` into ready fleet-wide") is not implementable as
    stated; readiness needs a per-node/per-store signal that does not exist today.
  - ❌ A fourth event type in the tenancy vocabulary. Accepted — it carries `store_id`,
    which is the only thing the coordinator can count.

---

## Design

```
                        ┌─────────────────── commands (may produce facts) ────────┐
REST  /tenancy/tenants ─┤ provision()          local: catalog + DDL   [authority] │
                        │ request_provision()  broadcast only, no local effect    │
BUS   TenantProvision-  │                                                          │
      Requested  ───────┤ TenantProvisionConsumer ── control_service.provision()   │
       (origin=…)       └──────────────────────────────────────────────────────────┘
                                          │
                                          ▼   facts (produce nothing)
                        TenantCatalogChanged      → CachedTenantCatalog invalidation
                        TenantNodeReady(store_id) → TenantReadinessCoordinator
                                                        │  all expected_stores seen
                                                        ▼
                                              control.mark_active(tenant_id)
                                                        → TenantCatalogChanged
```

Acyclicity: every arrow points from a command to a fact or from a fact to a terminal
action. No handler emits a command (RD-13, asserted by test).

### Topologies

| Topology | `catalog_authority` | Onboarding call | Coordinator |
|---|---|---|---|
| Single control plane (standalone, RD-9 default) | `True` | `provision()` (REST) or a bus command from an external system | not needed |
| Bundled control plane + app pod, `SHARED` isolation | `True` | `provision()` | not needed |
| Fleet fan-out, `SCHEMA`/`DATABASE` isolation | control plane `True`, every app service `False` | control plane `request_provision()` | required (or manual `POST …/activate`) |

---

## Steps

### Phase 1 — Entry-point convergence (the defect fix). Shippable alone.

1. [ ] `varco_core/tests/test_tenant_event_path_routability.py` — **the regression test
   that pairs the event path with the routing lookup** (no such pairing exists today,
   which is why this shipped). Failing tests:
   - publish `TenantProvisionRequested(tenant_id="acme")` on `InMemoryEventBus` →
     `bus.drain()` → `await catalog.get("acme")` succeeds **and**
     `routing_decision_for_status(descriptor.status).routable is True` and
     `.http_status == 200` (today: `TenantNotFoundError` → 404);
   - the provisioner ran exactly once (counting fake);
   - a `TenantCatalogChanged` fact was produced on `CHANNEL_TENANCY` exactly once;
   - **deprovision symmetry**: `TenantDeprovisionRequested(confirm=True)` → catalog is
     `DELETED` (→ 404, `routable is False`), the supervisor's `on_tenant_deactivated` and
     the pool's `evict` were both called **before** the destructive provisioner call
     (ordering asserted with a shared call-log), and `confirm=False` still DLQs and calls
     nothing;
   - a provisioner failure leaves the status `PENDING` (never `ACTIVE`) and the event
     lands in the DLQ with `source_ref == "acme"`.
2. [ ] `varco_fastapi/tests/test_tenant_event_path_middleware.py` — the same closure one
   layer up: onboard `"acme"` purely over the bus, then a `TestClient` request carrying
   `X-Tenant-Id: acme` through `TenantResolutionMiddleware` returns **200, not 404**, and
   `pool.ensure("acme")` was called exactly once. Imports only `varco_core.tenancy`
   (seam rule).
3. [x] `varco_core/varco_core/tenancy/control/consumer.py` — new signature:
   ```python
   def __init__(
       self,
       *,
       control_service: TenantControlService | None = None,
       dlq: AbstractDeadLetterQueue | None = None,
       # deprecated shim (RD-12) — removed one minor release after this lands
       provisioner: AbstractTenantProvisioner | None = None,
       catalog: AbstractTenantCatalog | None = None,
       producer: AbstractEventProducer | None = None,
   ) -> None: ...
   ```
   - `on_provision_requested` → `await self._control.provision(event.tenant_id)`.
   - `on_deprovision_requested` → keep the explicit `confirm` check (better message, and
     it fires before dedup marking), then `await self._control.deprovision(
     event.tenant_id, confirm=True)`. Note in the docstring that
     `TenantControlService.deprovision` re-checks — belt and braces, deliberate.
   - Shim: `provisioner=` + `catalog=` → build `TenantControlService` internally +
     `DeprecationWarning`; `provisioner=` alone → `ValueError` whose text contains the
     replacement call. `producer=` omitted under the shim → a private `_NullProducer`
     (no-op `_produce`) plus **one** WARNING stating that `TenantCatalogChanged` will not
     be emitted, so other pods' `CachedTenantCatalog` entries go stale until
     `catalog_ttl_s` (the existing TTL backstop, `cached_catalog.py`).
   - Neither `control_service` nor `provisioner` → `ValueError`.
   - `DESIGN:` block with ✅/❌ covering RD-11 + RD-12.
4. [x] `varco_core/varco_core/tenancy/control/consumer.py` — bound `_processed_event_ids`.
   It is an unbounded `set[Any]` today (`consumer.py:84`) that grows for the process's
   lifetime — a slow leak on a long-lived control plane. Replace with a bounded
   insertion-ordered structure (`collections.OrderedDict` used as an LRU,
   `max_tracked_event_ids: int = 4096`), documenting that cross-restart / beyond-window
   idempotency is the durable inbox's job (RD-1), not this cache's.
5. [x] `varco_core/varco_core/tenancy/control/service.py` — narrow the exception on the
   catalog read. `provision()` currently wraps `catalog.get()` in
   `except Exception` (`service.py:76`), so a catalog **outage** is indistinguishable from
   an unknown tenant and silently becomes `catalog.add(PENDING)`. Catch
   `TenantNotFoundError` only; every other error propagates (→ retry → DLQ).
6. [ ] `varco_core/tests/test_tenant_control_service.py` — add: a catalog `get()` raising
   `RuntimeError` propagates and **does not** call `add()` or the provisioner.
7. [ ] `varco_core/tests/test_tenant_provision_consumer.py` — migrate the four existing
   tests to `control_service=`; add three shim tests: `provisioner=` alone raises
   `ValueError` naming `control_service`; `provisioner=`+`catalog=` warns
   `DeprecationWarning` **and still updates the catalog**; the no-producer shim logs
   exactly one WARNING.
8. [ ] `varco_core/tests/test_tenant_provision_consumer.py` — add the RD-13 guard early:
   after a full provision over the bus, **no** `TenantProvisionRequested` was produced
   (i.e. `provision()` did not re-emit its own command). This test is what keeps a future
   "just emit from provision()" change from turning Phase 1 into a cycle.
9. [x] `technical_docs/features/multitenancy.md` — rewrite the event-driven onboarding
   subsection: both entry points converge on `TenantControlService`; a "0.x → 0.y
   migration" box with the before/after wiring snippet; an explicit statement that a
   pre-Phase-1 bus-onboarded tenant is **unroutable** and the repair is one
   `POST /tenancy/tenants` (or `provision()`) per affected tenant, which is idempotent
   against already-created storage.
10. [x] `CLAUDE.md` — multitenancy section: `TenantProvisionConsumer` takes a
    `TenantControlService`; add two pitfall-table rows (bus-onboarded tenant 404s /
    consumer constructed with `provisioner=`).
11. [x] `CHANGELOG.md` — **Fixed** (unroutable bus-onboarded tenant; deprovision leaving a
    routable tenant with no storage) + **Changed/Deprecated** (constructor).

**Acceptance:** `uv run pytest varco_core/tests/ varco_fastapi/tests/` green; the event
path and the REST path produce byte-identical catalog state (asserted by comparing the
two resulting `TenantDescriptor`s in one test); the middleware answers 200 for a
bus-onboarded tenant.

### Phase 2 — Broadcast API, origin provenance, and the command/fact DAG

1. [ ] `varco_core/tests/test_tenant_broadcast.py` — failing tests: `request_provision()`
   emits exactly one `TenantProvisionRequested` on `CHANNEL_TENANCY` with
   `origin == service.node_id`, and performs **no** catalog write and **no** provisioner
   call (counting fakes on both); `request_deprovision(confirm=False)` raises
   `DestructiveOperationRefused` and emits nothing (refusing to broadcast a command that
   would only DLQ fleet-wide); `request_deprovision(confirm=True)` emits with
   `confirm=True` and `origin` set; a service built without a producer raises
   `RuntimeError` naming the missing producer.
2. [ ] `varco_core/tests/test_tenant_origin_skip.py` — failing tests: a consumer whose
   control service has `node_id="cp-1"` **skips** an event with `origin="cp-1"` (no
   provisioner call, no catalog write) and **handles** `origin=None` and
   `origin="other"`; a full bundled round-trip (control service + its own consumer on one
   bus) performs exactly one DDL when the operator calls `provision()` then
   `request_provision()`.
3. [ ] `varco_core/tests/test_tenant_command_fact_dag.py` — the RD-13 structural test:
   drive `provision`, `deprovision`, `suspend`, `resume`, `mark_active`,
   `CachedTenantCatalog.on_catalog_changed`, and the readiness coordinator's handler over
   a recording bus and assert **no command event type was ever produced by a fact
   handler**, and that the only producers of commands are `request_provision` /
   `request_deprovision`.
4. [x] `varco_core/varco_core/tenancy/control/events.py` — add `origin: str | None = None`
   to both command events (wire-compatible: frozen pydantic `Event`, defaulted field); add
   `TenantNodeReady` (`__event_type__ = "varco.tenancy.node_ready"`; fields `tenant_id`,
   `node_id`, `store_id`). Docstrings state the command/fact classification explicitly.
5. [x] `varco_core/varco_core/tenancy/control/service.py` — add
   `node_id: str | None = None` (defaults to `VARCO_TENANCY_NODE_ID` or a process-stable
   `f"{hostname}:{pid}"`), `store_id: str | None = None`
   (`VARCO_TENANCY_STORE_ID`), `catalog_authority: bool = True`; add
   `request_provision(tenant_id)`, `request_deprovision(tenant_id, *, confirm)`, and
   `mark_active(tenant_id)` (the authority-only terminator used by Phase 3 and by the
   manual route). Under `catalog_authority=False`: `provision()` reads the catalog,
   refuses `DELETED`/`DEPROVISIONING`, runs the provisioner, emits `TenantNodeReady`, and
   writes nothing; one WARNING at construction naming the required terminator.
   `mark_active()` on a non-authority service raises `ValueError`.
6. [x] `varco_core/varco_core/tenancy/control/consumer.py` — origin skip (RD-15), reading
   `node_id` off the control service.
7. [ ] `varco_core/tests/test_tenant_worker_mode.py` — failing tests: `catalog_authority
   =False` never calls `update_status`/`add`; emits `TenantNodeReady` with the configured
   `store_id`; refuses a `DELETED` tenant (replayed-command safety) without calling the
   provisioner; double-provision is a no-op **via the provisioner's own idempotency**
   (asserted with a fake that records `IF NOT EXISTS`-style calls), not via a status check.
8. [ ] `varco_fastapi/tests/test_tenant_router_broadcast.py` — failing tests:
   `POST /tenancy/tenants/{id}/request-provision` → 202 and broadcasts without local DDL;
   `POST /tenancy/tenants/{id}/activate` → 200 and flips `ACTIVE` (the manual terminator);
   `DELETE /tenancy/tenants/{id}?broadcast=true` requires the same explicit confirm field
   as the local delete; every new route is `require_roles(admin_role)`-guarded and a
   non-admin gets 403, not 500.
9. [x] `varco_fastapi/varco_fastapi/tenancy/router.py` — the three routes above.
10. [x] `technical_docs/features/multitenancy.md` — new subsection "Fleet fan-out:
    `provision()` vs `request_provision()`", with the topology table, the command/fact
    diagram, the "the broadcaster is not included — call `provision()` first" rule, and
    the `origin`/`node_id`/`store_id` env-var table.
11. [x] `CLAUDE.md` + `CHANGELOG.md` — the two new verbs, the DAG rule, the new env vars,
    and a pitfall row for "bundled node called only `request_provision()` and never
    provisioned itself".

**Acceptance:** the DAG test passes; a bundled control-plane-plus-consumer on one bus
performs exactly one DDL per provision; `request_*` provably has zero local effect.

### Phase 3 — Fleet-readiness coordinator

Included in this plan rather than deferred: RD-16's `catalog_authority=False` has no
terminator without it, and shipping a mode that strands tenants in `PENDING` would repeat
exactly the class of gap this plan exists to close. Kept deliberately minimal (RD-18).

1. [ ] `varco_core/tests/test_tenant_readiness_coordinator.py` — failing tests:
   - constructing without `expected_stores` raises `ValueError` naming the setting
     (RD-17 — no guessing);
   - `TenantNodeReady` from a subset of stores leaves the tenant `PENDING`
     (`routable is False`, 503) and calls no catalog write;
   - the **last** expected store arriving flips `ACTIVE` exactly once and emits exactly
     one `TenantCatalogChanged`;
   - a duplicate `TenantNodeReady` from an already-seen store is a no-op (ten pods of one
     service ⇒ one store — the RD-17 property, asserted directly);
   - an **unexpected** `store_id` is ignored with one WARNING naming both sets and never
     counts toward completion;
   - `timeout_s` elapses with a store missing → one ERROR naming the missing stores, the
     tenant stays `PENDING`, and **no** `TenantCatalogChanged` is emitted;
   - a coordinator built over a `catalog_authority=False` service raises `ValueError`;
   - readiness for tenant A is independent of tenant B (no cross-tenant bleed).
2. [x] `varco_core/varco_core/tenancy/control/readiness.py` —
   `TenantReadinessCoordinator(EventConsumer)`:
   ```python
   def __init__(
       self,
       *,
       control_service: TenantControlService,
       expected_stores: frozenset[str],
       timeout_s: float | None = 900.0,
       dlq: AbstractDeadLetterQueue | None = None,
   ) -> None: ...


   @listen(TenantNodeReady, channel=CHANNEL_TENANCY)
   async def on_node_ready(self, event: Event) -> None: ...
   ```
   `register_to(bus)` from the host's `@PostConstruct` (never `__init__`), lazily-created
   `asyncio.Lock` around the `dict[str, set[str]]`, `RetryPolicy.durable_delivery()` +
   DLQ default mirroring `TenantProvisionConsumer`. Timeout is a per-tenant
   `asyncio.Task` cancelled on completion and in a `finally`. `readiness(tenant_id)`
   returns a frozen `TenantReadiness(tenant_id, seen, expected, missing, complete)`
   snapshot.
3. [x] `varco_core/varco_core/tenancy/control/__init__.py` — export
   `TenantReadinessCoordinator`, `TenantReadiness`, `TenantNodeReady`.
4. [ ] `varco_fastapi/tests/test_tenant_readiness_route.py` — `GET
   /tenancy/tenants/{id}/readiness` returns the snapshot (seen/expected/missing), 404 for
   an unknown tenant, admin-guarded, and returns `complete: false` with an empty `seen`
   set after a simulated coordinator restart (the RD-18 caveat, made visible rather than
   hidden).
5. [x] `varco_fastapi/varco_fastapi/tenancy/router.py` — that route (optional; omitted
   when no coordinator was passed to `build_tenant_router`).
6. [ ] `varco_core/tests/test_tenancy_di.py` (extend) — `container.scan` +
   `validate_bindings()` still green with the new module present.
7. [x] `technical_docs/features/multitenancy.md` — "Fleet readiness" subsection: why the
   unit is a **store** and not a pod; the worked example (3 services × N pods ⇒ 3 expected
   stores); the restart caveat and the one-verb recovery (`request_provision` again); the
   explicit statement that a timeout **never** activates; and the note that under
   `TenantIsolation.SHARED` none of this is wired.
8. [x] `CLAUDE.md` + `CHANGELOG.md` — coordinator, `TenantNodeReady`, `expected_stores`,
   the three new pitfall rows (missing store in `expected_stores`; counting pods instead
   of stores; expecting readiness to survive a restart).

**Acceptance:** a simulated 3-store fleet activates exactly once on the third distinct
`store_id` and never before; a missing store leaves the tenant at 503 forever with a
named ERROR; ten reports from one store count once.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `TenantProvisionConsumer(provisioner=p)` (no catalog) | `ValueError` at construction naming `control_service=` and showing the replacement wiring |
| `TenantProvisionConsumer(provisioner=p, catalog=c)` | works, `DeprecationWarning`, catalog updated; one WARNING if no producer |
| Bus-onboarded tenant, pre-Phase-1 data | still 404 until repaired; documented repair = one idempotent `provision()` |
| Redelivered `TenantProvisionRequested` (same `event_id`) | no-op (in-process dedup) |
| Redelivered after >4096 other events, or after restart | control-service status check makes it a no-op; durable inbox covers the rest |
| `TenantDeprovisionRequested(confirm=False)` | `DestructiveOperationRefused` → DLQ; nothing executed, no catalog change |
| Catalog store down during `provision()` | error propagates → retry → DLQ; **no** spurious `PENDING` descriptor created |
| Provisioner fails mid-provision | status stays `PENDING` (503, not 404); DLQ entry with `source_ref=tenant_id` |
| Command with `origin == own node_id` | skipped, one DEBUG log |
| Command with `origin=None` (external publisher) | handled normally |
| Two processes sharing a `node_id` | origin-skip over-skips; documented, `node_id` defaults to `host:pid` |
| `request_deprovision(confirm=False)` | raises, emits nothing |
| `catalog_authority=False`, tenant `DELETED` in catalog | provisioner **not** called; command ignored with one WARNING |
| `catalog_authority=False`, no coordinator/terminator | tenant stays `PENDING` (503); WARNING at service construction |
| `TenantNodeReady` for an unexpected `store_id` | ignored, one WARNING, does not count |
| Coordinator restart mid-onboarding | readiness reset; tenant stays `PENDING`; recovery = re-broadcast |
| Readiness timeout with a store missing | ERROR naming the missing stores; tenant stays `PENDING`; never activated |
| `TenantIsolation.SHARED` | Phase 2/3 unwired; Phase 1 behaviour is the whole feature |

---

## Verification

```bash
uv run pytest varco_core/tests/test_tenant_event_path_routability.py \
              varco_core/tests/test_tenant_provision_consumer.py \
              varco_core/tests/test_tenant_control_service.py
uv run pytest varco_core/tests/ varco_fastapi/tests/
uv run pytest varco_sa/tests/ varco_beanie/tests/          # seam regression
uv run pytest varco_sa/tests/ -m integration               # Phase 1/2 only if SA wiring changes
make lint
make type-check
```

Per-phase gate: Phase 1's `varco_core/tests/test_tenant_event_path_routability.py` and
`varco_fastapi/tests/test_tenant_event_path_middleware.py` must fail on `main` before the
fix and pass after — that pairing (event path + catalog/routing lookup) is the artefact
whose absence let the defect ship.

---

## Risks

- **Phase 1 introduces a premature-`ACTIVE` window in an existing multi-consumer fleet.**
  With N consumers on one channel, the first to finish flips `ACTIVE` while others are
  still provisioning. Strictly better than today (permanent 404), but new. Invariant:
  Phase 2's `catalog_authority=False` + Phase 3's coordinator must land before anyone runs
  fan-out under `SCHEMA`/`DATABASE`; documented in Phase 1's doc update, not deferred to
  Phase 2's.
- **Phase 1 removes the accidental cycle-safety** (today the consumer bypasses the control
  service). Invariant: RD-13's DAG — no handler emits a command — enforced by
  `test_tenant_command_fact_dag.py` and by the Phase 1 guard test (step 8).
- **`origin` skip can over-skip** if two processes share a `node_id`. Invariant: `node_id`
  is process-stable and unique by default (`host:pid`); overriding it is an explicit act.
- **`expected_stores` drift.** Adding a service without updating the set activates tenants
  one store early. Invariant: the coordinator logs expected-vs-seen on every partial step
  and exposes `GET …/readiness`; the set lives beside the service list in deployment
  config.
- **Shim removal timing.** `provisioner=`/`catalog=` must be deleted one minor release
  after landing, or it becomes permanent. Invariant: the `DeprecationWarning` text names
  the removal release, and a test asserts that text.

---

## Amendment to Plan 007

`plans/007-multitenancy-isolation-strategies.md`, **Phase 5, steps 4-5** (lines 852-864)
specified retry/DLQ/dedup for `TenantProvisionConsumer` and omitted the catalog
transition entirely, so a bus-onboarded tenant was left unroutable. Add to 007, at the end
of Phase 5:

> **Amended by Plan 008 (Phase 1, RD-11):** step 5's consumer takes a
> `TenantControlService`, not an `AbstractTenantProvisioner`. Both control-plane entry
> points (REST step 7, bus step 5) must converge on the single catalog transition in step
> 2; a consumer that calls the provisioner directly produces storage with no catalog row,
> which `routing.py`/`TenantResolutionMiddleware` render as a permanent 404. Step 4's test
> list is extended with the event-path routability pairing. RD-13/RD-15 (Plan 008)
> additionally forbid any handler from emitting a command event, which constrains 007's
> event vocabulary in step 3.

Also record in 007's *Resolved decisions* that RD-11 … RD-19 continue its numbering in
Plan 008.

---

## What this plan judges the originating request got wrong or under-considered

1. **Deprovision has the same defect, and it is worse.** The request addressed provision
   only. On the bus path, destructive DDL runs while the catalog still says `ACTIVE`
   (routable tenant, no storage → 500s instead of 410), and the fan-out supervisor stop
   and pool eviction in `service.py:107-114` are never reached.
2. **"Aggregate N `TenantCatalogChanged` events into ready fleet-wide" is not
   implementable as stated.** That event is a cache-invalidation fact about one shared
   catalog row; N nodes writing the same row emit N identical facts, and counting them
   counts nothing. Readiness requires a per-store signal that does not exist today —
   hence `TenantNodeReady` (RD-19).
3. **"Knowing the expected fleet size" is only hard if the unit is a pod.** It is a
   *store*: pods of one service share a database, so autoscaling never changes the
   expected set. That reframing turns the hard part into static deploy-time config
   (RD-17).
4. **Fixing the consumer alone (item 1) creates a premature-`ACTIVE` race** in any
   deployment already running more than one consumer. Not a reason to delay the fix — a
   permanent 404 is worse than a short race — but it must be documented in Phase 1 and is
   the reason `catalog_authority` (RD-16) exists at all.
5. **Two latent bugs on the exact path being changed**, neither mentioned: the unbounded
   `_processed_event_ids` set (`consumer.py:84`), and `provision()`'s bare
   `except Exception` around the catalog read (`service.py:76`), which turns a catalog
   outage into a silently-created `PENDING` descriptor.
6. **"Prevent the loop by construction" is best served by typing the events, not by
   guarding the wiring.** The proposed rule ("the control plane must not subscribe to its
   own broadcasts") is a deployment convention; the command/fact DAG (RD-13) plus `origin`
   provenance (RD-15) makes it a property of the message graph, testable in CI. A
   construction guard on "consumer over an admin provisioner" was considered and rejected:
   it would break the primary standalone topology, in which the control plane's own
   consumer is exactly how RD-1's "onboarding via queue/pub-sub" works.
7. **None of Phase 2/3 applies under `TenantIsolation.SHARED`** — the default. Worth
   stating loudly so the majority deployment does not wire a coordinator it does not need.
