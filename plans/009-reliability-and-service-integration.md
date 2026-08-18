# Plan 009 — Reliability & Service Integration

> Executes `BACKLOG.md` ("Varco Backlog — Reliability & Service Integration").
> Breaking changes are in scope; one consolidated migration note at the bottom.
>
> **Item count note:** the backlog prose says "13 items"; the backlog table lists
> **12** IDs (R1–R8 minus none, C1–C4). This plan phases all 12. Nothing is dropped.

## Goal

After this plan: (1) a dead letter is recoverable (`redrive`), prunable
(`delete_where`), tenant-scoped, observable (metrics), operable (REST + CLI), and
persistable on every backend including Mongo; (2) consuming another varco service
is one env var + one inject, with an identical typed surface whether the peer's
router class is importable (monorepo) or only its exported `.contract.json` is
available (cross-repo).

## Non-goals

- Automatic/scheduled DLQ redrive (parked — redrive stays operator-triggered).
- Read-auditing, Beanie index-drift reconciliation, online schema change, OPA
  (all parked in the backlog).
- Rewriting `AsyncVarcoClient`'s middleware/TLS/httpx layer. C1 is a facade over
  it, not a replacement.
- Deleting `make_client` / `GenericClient` / `OpenAPIClient` / `ClientConfigurator`
  / `generate_client`. They are **demoted** (moved out of the front door), not removed.
- Auto-enabling Postgres RLS anywhere. R4 ships helpers + a reviewed-revision
  recipe; nothing in varco turns RLS on at startup (CLAUDE.md pitfall table).

---

## Design

### The spine: one descriptor, two consumers

The single highest-leverage decision in this release. `introspect_routes()`
(`varco_fastapi/router/introspection.py:185`) is already the sole producer of route
metadata. Today its only consumers are `build_router()` and the MCP/A2A adapters;
the client metaclass reaches for `ResolvedRoute` directly and throws away all
parameter typing (`varco_fastapi/client/base.py:585` — `async def custom_method(self, **kwargs: Any)`).

```
                        introspect_routes(router_cls)  →  list[ResolvedRoute]
                                        │
                                        ▼
                          build_contract()  →  ServiceContract        ← Phase 0
                          (frozen value objects + JSON Schema $defs)
                                        │
                    ┌───────────────────┴────────────────────┐
                    ▼                                        ▼
        IN-PROCESS consumer                       CROSS-REPO consumer
        _VarcoClientMeta reads the                ServiceContract.from_json()
        ServiceContract of the imported             ├─ contract_client()  (runtime)
        router class  (Phase 7, C2)                 └─ varco gen-client   (codegen)
                    │                                        │
                    └────────────► identical synthesized ◄───┘
                                  __signature__ / arity /
                                  path-vs-query-vs-body split
```

`ServiceContract` is the **only** thing the two paths share, and both build their
methods through one function (`build_client_method(route_contract, resolver)`), so
"same typed surface either way" is enforced by construction rather than by
discipline.

**Where the descriptor lives — `varco_fastapi/contract/`, not `varco_core`.**
A route contract is HTTP/router-shaped; `varco_core` has no notion of routers,
paths, or HTTP methods, and CLAUDE.md's rule is that `varco_core` holds
*domain/infra seams*. A cross-repo consumer must install `varco-fastapi` anyway —
that is where the client runtime (`AsyncVarcoClient`, middleware, httpx) lives.

**The `varco export-contract` / `varco gen-client` CLI is contributed by
`varco_fastapi` via the `varco.commands` entry-point group**, not written into
`varco_core/cli/` as the scout suggested. `varco_core` cannot import
`varco_fastapi` (dependency graph), and `main.py:42` already implements exactly
this plugin discovery.

#### Alternatives considered — descriptor format

- **Reuse the router's OpenAPI JSON as the descriptor.** ❌ Rejected: OpenAPI is
  lossy in exactly the places we need — it does not carry `crud_action`,
  `async_capable`, `route_order`, or the router's Python method name (the client
  method name). We would be reverse-engineering our own metadata out of a
  document we generated from it. ✅ It would be tool-compatible — mitigated by
  emitting the JSON Schema `$defs` in OpenAPI-compatible form inside our envelope,
  so `datamodel-code-generator` can still be pointed at the `schemas` block.
- **Pickle / import the router class cross-repo.** ❌ Rejected outright: that is
  the shared-Python-import topology the interview explicitly ruled out.
- **Put `ServiceContract` in `varco_core`.** ❌ Rejected: drags HTTP semantics into
  the package whose whole contract is that it is transport-agnostic.

### The DLQ redrive shape — a `DlqRedriver`, not a bus on the ABC

`varco_core/event/dlq.py:42-49` carries an explicit DESIGN block: *"`AbstractDeadLetterQueue`
is independent of `AbstractEventBus` — no circular imports."* The backlog asks for
`redrive(entry_id)` **on the ABC**. Putting it there requires the DLQ to hold an
`AbstractEventBus`, which inverts that documented invariant and forces all five
backends to re-implement the identical publish→ack loop.

**Deviation (justified):** the ABC gains *read/delete primitives* only; a new
`DlqRedriver` (`varco_core/event/redrive.py`) owns the redrive **policy** and is
the single thing driven by the CLI (Phase 4) and the REST router (Phase 10).

```
DlqRedriver(dlq: AbstractDeadLetterQueue, bus: AbstractEventBus)
   .redrive(entry_id)            → dlq.get(entry_id) → bus.publish → dlq.ack(entry_id)
   .redrive_batch(limit=, ...)   → dlq.list_entries()/pop_batch() → publish → ack
```

**The `delete after redrive` problem is solved by `ack()`, which already exists and
is already correct on every backend** (RD-4 below): SA/Redis/Beanie `ack()` deletes
the row; Kafka `ack()` commits the offset (`varco_kafka/dlq.py:444-490`); NATS
`ack()` acks the JetStream message. All five mean "never hand me this entry again"
— which is precisely the post-redrive semantic. No new primitive is needed.

#### Alternatives considered — redrive
- **`redrive()` on the ABC taking `bus=`.** ❌ Every backend duplicates the publish
  loop; the DLQ↔bus decoupling invariant dies. ✅ Matches the backlog's literal
  wording — worth less than the invariant.
- **A `DlqRelay` background task (symmetric with `OutboxRelay`).** ❌ That is the
  parked "auto-redrive scheduler". `DlqRedriver` is a one-shot, operator-driven
  object with no `start()`/`stop()` precisely so it cannot become one by accident.

### Reliability metrics — depth is global, pushes are per-channel

`count()` is the only portable depth primitive and it is global; Kafka's returns
`-1` by documented design (`varco_kafka/dlq.py:492-503`). A per-channel gauge would
require a per-channel count query no backend implements. See RD-3.

---

## Resolved Decisions

**RD-1 — `ServiceContract` lives in `varco_fastapi/contract/`, versioned by
`contract_version` (starts at `"1.0"`).** Consumers reject a major version they do
not know (`ContractVersionError`) and warn on an unknown minor. Rationale above.
The `varco export-contract` / `varco gen-client` subcommands are registered through
the `varco.commands` entry point from `varco_fastapi`, not built into
`varco_core/cli/main.py`.

**RD-2 — Beanie DLQ: collection `varco_dead_letters`, and NO TTL index by
default.** Collection name matches the SA table (`varco_sa/dlq.py:98`) and the
`varco_audit_log` precedent (`varco_beanie/audit.py:167`). A TTL index silently
deletes dead letters — the exact failure mode ("nobody notices it died") this
release exists to fix, and it deletes them *without an operator ever seeing them*.
Retention is explicit: `delete_where()` / `varco retention prune`. `ttl_seconds=`
is opt-in and logs one WARNING at construction naming the data-loss implication.
Indexes are **declared** in `Settings.indexes` but **built** only by
`varco migrate index --create` — never inside the request path or lifespan
(Plan 006's `index_mode="check"` default precedent). Ack semantics mirror
`SADeadLetterQueue`: `pop_batch()` is a non-destructive read, `ack()` deletes.
There is no visibility window (SA has none either) — the single-relay assumption
is documented, not silently assumed.

**RD-3 — DLQ depth gauge is GLOBAL per DLQ instance, tagged `dlq=<name>`; opt-in
per-channel.** `varco.dlq.depth` is an `ObservableGauge` over `count()`, carrying
one attribute: `dlq` (the operator-supplied instance name, defaulting to the class
name). Not per-channel, because `count()` is global and Kafka cannot even answer it.
`ReliabilityMetricsConfig(depth_by_channel=True)` opts into `count_by_channel()`
(concrete-but-raising on the ABC; implemented by SA/Redis/Beanie/InMemory) and then
emits one series per channel — the operator accepts the cardinality explicitly.
**A negative `count()` (Kafka's `-1`) causes the callback to emit NO observation**,
not `-1`: a gap in a depth graph is honest, a `-1` is a lie that breaks every alert
threshold. Push counters (`varco.dlq.pushed`) DO carry `channel` and `source`
(both bounded by deployment topology) and deliberately do NOT carry `event_type`,
`entry_id`, `handler_name`, or `tenant_id` (unbounded or per-tenant → series
explosion; CLAUDE.md's "metric series explosion" pitfall).

**RD-4 — Redrive on stream-backed stores: `ack()` IS the delete, and single-entry
redrive is unsupported there.** Batch redrive works everywhere:
`pop_batch()` → publish → `ack()`. Single-entry `redrive(entry_id)` needs random
access, which a Kafka topic / NATS subject does not offer;
`AbstractDeadLetterQueue.supports_random_access: ClassVar[bool] = False` is the
capability flag, `get()`/`list_entries()`/`delete_where()` are concrete-but-raising
`NotImplementedError` on those backends, and `DlqRedriver.redrive(entry_id)` raises
`DeadLetterNotAddressable` naming the backend and pointing at
`redrive_batch(...)` / `--batch`. The CLI and the REST router both surface this as
a precise 501-style error, never a silent no-op. Retention on those backends is
likewise not a varco concern: `delete_where()` raises with a message naming Kafka
topic retention (`retention.ms`) / JetStream `MaxAge` as the correct mechanism.

**RD-5 — `PeerRegistry` env vars carry references, never secrets.**
`VARCO_PEER_<NAME>_URL` (required), `_TIMEOUT`, `_VERIFY`, `_PROFILE`,
`_CONTRACT` (path to a `.contract.json`), and `_TOKEN_REF`. `_TOKEN_REF` is a
**reference** resolved by an injected `SecretResolver` hook — exactly the `dsn_ref`
rule from Plan 007 RD-2. A value that looks like a literal credential (starts with
`ey`, contains a `:`-delimited 3-segment JWT shape, or is > 200 chars) raises
`ValueError` at registry construction naming RD-5, with `allow_literal_secret=True`
as the explicit test/bootstrap escape hatch. The default resolver reads
`os.environ[ref]`, so `VARCO_PEER_ORDERS_TOKEN_REF=ORDERS_SVC_TOKEN` works with
zero extra wiring while keeping the secret out of the peer-config surface. Peer
names are upper-snake in env and lower-snake in code (`VARCO_PEER_ORDERS_URL` →
peer `"orders"`).

**RD-6 — `ReliabilityPreset` lives in a new `varco_core/reliability/` subpackage,
not in `varco_core/resilience/`.** It composes `varco_core.event` (DLQ),
`varco_core.resilience` (RetryPolicy), and `varco_core.service` (outbox/audit);
putting it in `resilience/` would make `resilience` import `event`, and
`event.consumer` already imports `resilience` — a cycle. `varco_fastapi` imports
`varco_core.reliability` only (a core seam), never a backend.

**RD-7 — Global default preset is opt-in and uses a sentinel.**
`set_default_reliability_preset(preset)` makes bare `@listen(...)` inherit
`retry_policy`/`dlq`. The default is `ReliabilityPreset.off()` so today's behaviour
is byte-identical. `@listen`'s `retry_policy=`/`dlq=` parameters change from
`= None` to `= _UNSET` so an **explicitly passed** `retry_policy=None` still means
"no retry, ignore the global preset" — distinguishable from omission. This is the
only way "opt into durability once" can coexist with per-handler opt-out.

**RD-8 — Audit hash-chaining (R8) is a repository concern, not a consumer
concern, and the chain is global per repository.** The scout proposed computing
`prev_hash` in `AuditConsumer`; that is wrong under concurrency (two consumer
instances would fork the chain silently). The chain link is established inside
`save()` under a backend-level serialization guarantee: SA uses a monotone
`seq BIGSERIAL` + `SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE`; Beanie uses a
dedicated `varco_audit_seq` counter document with `find_one_and_update({$inc})`.
This caps audit write throughput at one serialized write per record — documented
explicitly, and the whole feature is opt-in (`SAAuditRepository(hash_chain=True)`).

**RD-9 — The reliability admin surface is `mount_reliability_admin(app, ...,
acknowledge_bundled_admin=True)`, with no env var, ever.** It can *replay*
messages onto the bus and *delete* audit records; that is at least as privileged as
the tenant control plane. Mirrors Plan 007 RD-9 verbatim, including the
`ValueError` when the acknowledgement kwarg is omitted.

---

## ABC additions — portable default vs. concrete-but-raising

Following the `AbstractJobStore` precedent (`delete_where` has a portable default;
`renew`/`reap_expired_leases` are concrete-but-raising because no correct fallback
for a lease exists).

| New ABC member | Choice | Justification |
|---|---|---|
| `AbstractDeadLetterQueue.supports_random_access` | `ClassVar[bool] = False` | Capability flag, not a method. Conservative default: an out-of-tree backend is assumed stream-shaped until it says otherwise. |
| `AbstractDeadLetterQueue.get(entry_id)` | concrete-but-raising | No portable random access. A `pop_batch()`-scan default would be **destructive** on `InMemoryDeadLetterQueue` (consume-on-pop, `dlq.py:451-478`) and would advance Kafka's in-flight tracking. |
| `AbstractDeadLetterQueue.list_entries(...)` | concrete-but-raising | Same. `pop_batch()` is not a read on every backend. |
| `AbstractDeadLetterQueue.delete(entry_id)` | **portable default** → `await self.ack(entry_id)` | `ack()` already means "never return this entry again" on all five backends. This is the one case with a genuinely correct fallback. |
| `AbstractDeadLetterQueue.delete_where(...)` | concrete-but-raising | Any `pop_batch`-based default would have to `ack()` non-matching entries to reach matching ones = silent data loss. Refusing is strictly safer. |
| `AbstractDeadLetterQueue.count_by_channel()` | concrete-but-raising | `count()` itself is already `-1` on Kafka; a per-channel default is impossible. |
| `AuditRepository.list(...)` | concrete-but-raising | No portable scan primitive exists on the ABC to build one from. |
| `AuditRepository.delete_where(...)` | concrete-but-raising | Same, and destructive. |
| `AuditRepository.verify_chain(entries)` | **portable default** (pure, `@staticmethod`) | Pure recomputation over already-returned value objects — correct for every backend by construction. |
| `OutboxRepository.count_pending()` | concrete-but-raising | A portable default is a full table scan on the hot path. The gauge catches `NotImplementedError` once and disables itself with one INFO log. |
| `OutboxRepository.oldest_pending_at()` | concrete-but-raising | Same. |
| `AuditRepository.list_for_entity(..., tenant_id=)` | **breaking signature change** on an existing `@abstractmethod` | A keyword-only defaulted parameter; external subclasses that do not accept it break loudly at call time rather than silently ignoring the tenant filter (which is the security bug R4 exists to fix). Listed in the migration note. |

---

## Phases

Ordering respects the stated DAG (`C1 → C2/C3/C4`; `R1+R3 → R6`) and honours the
suggested first cut (R2 + R3 + C1) as Phases 1–3.

**Two deliberate deviations, both stated up front:**

1. **C3 is split.** Phase 0 lands only the descriptor *schema + producer*; Phase 8
   lands the *export CLI + cross-repo codegen*. The backlog itself demands the
   descriptor be designed before C2, while the DAG puts C3 after C1. Splitting
   satisfies both: schema (no C1 dependency) first, generation (needs C1's
   `client_for` front door) later.
2. **R7 (Beanie DLQ) moves after R1 and R3, and R5 after R1/R2/R3.** Both are
   nominally independent, but R7 would otherwise implement the pre-R1/R3 ABC and
   then be immediately rewritten to add `get`/`list_entries`/`delete_where`;
   R5's whole value is bundling the retry/DLQ/outbox/audit wiring that R1–R3
   define. Sequencing them later is strictly less work, not more.

---

### Phase 0 — `ServiceContract` descriptor (C3, part 1) — [x] DONE

**New files**

- `varco_fastapi/varco_fastapi/contract/__init__.py`
- `varco_fastapi/varco_fastapi/contract/model.py`
- `varco_fastapi/varco_fastapi/contract/build.py`
- `varco_fastapi/varco_fastapi/contract/schema.py` (JSON Schema `$defs` collection)
- `varco_fastapi/tests/test_contract_model.py`
- `varco_fastapi/tests/test_contract_build.py`

**Modified**

- `varco_fastapi/varco_fastapi/router/introspection.py` — `ResolvedRoute` gains
  `param_specs: tuple[ParamSpec, ...] = ()`; new `ParamSpec` frozen dataclass and
  `_extract_param_specs(fn, path_params)` helper.

**Signatures**

```python
# varco_fastapi/router/introspection.py
@dataclass(frozen=True)
class ParamSpec:
    """One resolved handler parameter, classified for client generation."""
    name: str
    kind: Literal["path", "query", "body", "header"]
    annotation: type | None          # runtime type, None when unresolvable
    required: bool
    default: Any = None
    description: str | None = None

# varco_fastapi/contract/model.py
CONTRACT_VERSION: Final[str] = "1.0"

@dataclass(frozen=True)
class ParamContract:
    name: str
    kind: str                        # "path" | "query" | "body" | "header"
    schema: dict[str, Any]           # JSON Schema fragment (may be a $ref)
    required: bool = True
    default: Any = None
    description: str | None = None

@dataclass(frozen=True)
class RouteContract:
    name: str
    method: str
    path: str
    params: tuple[ParamContract, ...] = ()
    request_schema: dict[str, Any] | None = None    # JSON Schema or $ref
    response_schema: dict[str, Any] | None = None
    status_code: int = 200
    is_crud: bool = False
    crud_action: str | None = None
    async_capable: bool = True
    deprecated: bool = False
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()

@dataclass(frozen=True)
class ServiceContract:
    contract_version: str
    service_name: str
    routes: tuple[RouteContract, ...]
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)  # $defs
    base_path: str = ""
    service_version: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]: ...
    def to_json(self, *, indent: int = 2) -> str: ...
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServiceContract: ...
    @classmethod
    def from_json(cls, raw: str | bytes) -> ServiceContract: ...
    def route(self, name: str) -> RouteContract: ...   # KeyError with route list

class ContractVersionError(ValueError): ...

# varco_fastapi/contract/build.py
def build_contract(
    router_cls: type,
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    enabled_routes: set[str] | None = None,
) -> ServiceContract: ...
```

`schema.py` walks every `request_model`/`response_model`/`ParamSpec.annotation`,
calls `TypeAdapter(t).json_schema(ref_template="#/schemas/{model}")`, and merges
the `$defs` into one flat `schemas` registry — so a model referenced by three
routes is emitted once.

**DESIGN blocks to write**

- *Flat `schemas` registry with `$ref` over inline schemas*
  ✅ One definition per model regardless of route count; codegen emits one class.
  ✅ OpenAPI-shaped — `datamodel-code-generator` can consume the `schemas` block.
  ❌ Consumers must resolve `$ref` (a 15-line resolver, shipped in `schema.py`).
- *`contract_version` as a separate field from `service_version`*
  ✅ The wire format and the service evolve independently.
  ❌ Two version fields to explain — mitigated in the docs table.
- *`ParamSpec.annotation` may be `None`*
  ✅ An unresolvable annotation (string forward-ref to a `TYPE_CHECKING` import)
     degrades to `Any`/`dict[str, Any]` rather than exploding at import time.
  ❌ Silent type loss — mitigated by one DEBUG log per unresolved param and a
     `strict=True` flag on `build_contract` for CI use.

**Edge cases**

- Handler declares `Request` / `Response` / `BackgroundTasks` / `Depends(...)` /
  `ctx` / `auth` / `context` / `self` → excluded from `param_specs` entirely.
- Two params of different `kind` with the same name (path `{id}` + a query `id`) →
  `ValueError` at build time naming the route.
- `response_model` is `None` (delete) → `response_schema=None`, client returns `None`.
- Router with zero routes → a valid contract with `routes=()`, not an error.
- `async_capable=True` route → `response_schema` is emitted as
  `oneOf[<R>, JobAcceptedResponse]` so a `?with_async=true` return is typed.
- Non-Pydantic return annotation (`dict`, `list[str]`, `None`) → best-effort
  `TypeAdapter`; unsupported → `{"type": "object"}` + one DEBUG log.

**Tests** (`async def`, auto mode)

- `test_contract_model.py` — round-trip `to_json`/`from_json` equality;
  `from_dict` on a major-version bump raises `ContractVersionError`; unknown minor
  logs a warning and parses; frozen-ness (`FrozenInstanceError` on mutation).
- `test_contract_build.py` — CRUD-only router produces 6 routes with the right
  `crud_action`s; a custom `@route` with `order_id: UUID` path param + `limit: int`
  query param + a Pydantic body produces 3 `ParamContract`s with the right `kind`;
  a model used by two routes appears once in `schemas`; `GenericRouter` (no type
  args) builds a contract with no CRUD routes; `strict=True` raises on an
  unresolvable annotation.

**Docs** — `technical_docs/features/portable-contracts.md` (new): the format table,
the `$ref` rule, the version policy.

---

### Phase 1 — R2 reliability metrics pack — [x] DONE (core metrics module + count_pending/oldest_pending_at; call-site wiring into push()/outbox/audit/job deferred)

**New files**

- `varco_core/varco_core/observability/reliability.py`
- `varco_core/tests/test_reliability_metrics.py`

**Modified**

- `varco_core/varco_core/event/dlq.py` — increment `varco.dlq.pushed` in the ABC's
  documented contract path (see below), `varco.dlq.push_failures` in the
  swallow-branch of every backend's `push()`.
- `varco_core/varco_core/service/outbox.py` — `OutboxRelay` counters at the publish,
  deserialize-failure, and dead-letter branches (`outbox.py:755`, `:872`, `:886`).
- `varco_core/varco_core/service/audit.py` — `AuditConsumer.on_audit_event` counters.
- `varco_core/varco_core/job/base.py` — `JobPoller` reap counter.
- `varco_core/varco_core/service/outbox.py` — `OutboxRepository` gains
  `count_pending()` / `oldest_pending_at()` (concrete-but-raising).
- `varco_sa/varco_sa/outbox.py`, `varco_beanie/varco_beanie/outbox.py` — implement both.
- `varco_core/varco_core/observability/__init__.py` — export the new names.

**Signatures**

```python
# varco_core/observability/reliability.py
@dataclass(frozen=True)
class ReliabilityMetricsConfig:
    enabled: bool = True
    meter_name: str = "varco"
    depth_by_channel: bool = False      # RD-3 — opt-in cardinality
    include_tenant: bool = False        # RD-3 — off by default
    depth_poll: bool = True             # register the ObservableGauge at all

def install_reliability_metrics(
    *,
    dlq: AbstractDeadLetterQueue | None = None,
    dlq_name: str | None = None,
    outbox_repo: OutboxRepository | None = None,
    config: ReliabilityMetricsConfig | None = None,
) -> None: ...

def record_dlq_push(*, source: str, channel: str, ok: bool) -> None: ...
def record_outbox_published(*, channel: str) -> None: ...
def record_outbox_failure(*, reason: str) -> None: ...     # "deserialize"|"publish"
def record_audit_write(*, action: str, entity_type: str, ok: bool) -> None: ...
def record_job_lease_reap(*, count: int) -> None: ...
```

Metric inventory (all `Metric(...)` instances at module level — safe, instruments
are created lazily, `metric.py:129-132`):

| Name | Kind | Attributes |
|---|---|---|
| `varco.dlq.pushed` | counter | `source`, `channel`, `status` (`ok`/`failed`) |
| `varco.dlq.depth` | observable gauge | `dlq` (+ `channel` iff `depth_by_channel`) |
| `varco.dlq.redriven` | counter | `source`, `status` (Phase 4 wires it) |
| `varco.outbox.published` | counter | `channel` |
| `varco.outbox.failures` | counter | `reason` |
| `varco.outbox.dead_lettered` | counter | — |
| `varco.outbox.pending` | observable gauge | — |
| `varco.outbox.lag_seconds` | observable gauge | — (now − `oldest_pending_at()`) |
| `varco.audit.writes` | counter | `action`, `entity_type`, `status` |
| `varco.job.lease_reaps` | counter | — |

**DESIGN blocks**

- *Recording helpers (`record_*`) over decorating the call sites*
  ✅ The DLQ push path must never raise — a decorator around `push()` would sit
     outside the try/except that guarantees it. Helpers go **inside** it.
  ✅ Call sites stay one line and testable by monkeypatching one function.
  ❌ Six free functions instead of one decorator — accepted for the invariant.
- *Gauge skips rather than reporting a negative depth* (RD-3) — ✅/❌ as in RD-3.
- *`install_reliability_metrics` is imperative, not a `@Configuration`*
  ✅ Metrics need the *live* DLQ/outbox instance, which only the app knows.
  ✅ Scanning a `@Configuration` would auto-activate it (CLAUDE.md pitfall).
  ❌ One extra bootstrap line — folded into R5's preset in Phase 9.

**Edge cases**

- No `MeterProvider` configured → OTel no-op meter; every call site is a no-op.
  Explicitly tested.
- `count()` raises (broker down) → the gauge callback catches, emits nothing, logs
  at DEBUG (not ERROR — a metrics callback must never spam).
- `count()` returns `-1` → no observation (RD-3).
- `count_pending()` raises `NotImplementedError` → the gauge deregisters itself
  after the first call with **one** INFO log naming the repository class.
- `install_reliability_metrics()` called twice → idempotent; the second call
  replaces the gauge callbacks rather than double-registering.
- `record_dlq_push` itself raising must not break `push()` — every `record_*`
  helper wraps its body in `try/except Exception: pass` (silent by design; a
  metrics failure is never worth a dropped dead letter).

**Tests**

- `test_reliability_metrics.py` — with an in-memory OTel `InMemoryMetricReader`:
  push 3 entries to `InMemoryDeadLetterQueue`, assert `varco.dlq.pushed` == 3 with
  `source="consumer"`; assert `varco.dlq.depth` observes 3; assert a DLQ whose
  `count()` returns `-1` produces zero data points; assert `record_*` swallows a
  raising instrument; assert `install_reliability_metrics()` twice is idempotent;
  assert the outbox gauge self-disables on `NotImplementedError` and logs once.
- `varco_sa/tests/test_sa_outbox.py` — `count_pending()` / `oldest_pending_at()`
  against SQLite.
- Integration `@pytest.mark.integration` in `varco_redis/tests/test_redis_dlq.py`
  — depth gauge against a real Redis.

**Docs** — `technical_docs/features/observability.md` gains a "Reliability metrics"
section with the table above and the alerting recipes (`varco.dlq.depth > 0 for 5m`,
`varco.outbox.lag_seconds > 60`).

---

### Phase 2 — R3 retention & pruning — [x] DONE

**New files**

- `varco_core/varco_core/cli/retention.py`
- `varco_core/tests/test_retention_cli.py`

**Modified**

- `varco_core/varco_core/event/dlq.py` — `delete`, `delete_where`, `count_by_channel`.
- `varco_core/varco_core/service/audit.py` — `AuditRepository.delete_where`.
- `varco_sa/varco_sa/dlq.py`, `varco_redis/varco_redis/dlq.py` — implement.
- `varco_kafka/varco_kafka/dlq.py`, `varco_nats/varco_nats/dlq.py` — explicit
  raising overrides with backend-specific guidance (RD-4).
- `varco_sa/varco_sa/audit.py`, `varco_beanie/varco_beanie/audit.py` — implement.
- `varco_core/varco_core/cli/main.py` — register `retention`.

**Signatures**

```python
# AbstractDeadLetterQueue
supports_random_access: ClassVar[bool] = False

async def delete(self, entry_id: UUID) -> None:
    """Portable default: delegates to ``ack(entry_id)``."""
    await self.ack(entry_id)

async def delete_where(
    self,
    *,
    older_than: datetime | None = None,
    source: DeadLetterSource | Sequence[DeadLetterSource] | None = None,
    channel: str | None = None,
    tenant_id: str | None = None,      # populated in Phase 6
    limit: int | None = None,
) -> int:
    """Concrete-but-raising. Raises ValueError with no predicate at all."""

async def count_by_channel(self) -> dict[str, int]:
    """Concrete-but-raising."""

# AuditRepository
async def delete_where(
    self,
    *,
    older_than: datetime | None = None,
    entity_type: str | None = None,
    tenant_id: str | None = None,
    limit: int | None = None,
) -> int:
    """Concrete-but-raising. Raises ValueError with no predicate at all."""
```

Both `delete_where` methods reproduce `AbstractJobStore.delete_where`'s
**no-predicate `ValueError`** verbatim in spirit (`job/base.py:927-933`) — refusing
to silently truncate the table — and both docstrings carry the chunked-sweep recipe.

**CLI**

```
varco retention prune --type {dlq,audit} --before <ISO8601> [--limit N]
                      [--chunk 1000] [--dry-run] --target module:factory
```

`--target` names an importable zero-arg factory returning the
`AbstractDeadLetterQueue` / `AuditRepository` (the CLI cannot know the app's DI
container). `--dry-run` requires `count`/`list` support and prints the count
without deleting. The default behaviour is the chunked sweep: loop
`delete_where(..., limit=chunk)` until it returns `0`.

**DESIGN blocks**

- *Concrete-but-raising over a `pop_batch`-based portable default* — as per the ABC
  table above. ✅ Cannot lose data. ❌ Every backend must opt in explicitly.
- *`delete()` delegating to `ack()`* ✅ The one correct portable default in the set.
  ❌ Slightly surprising naming (two verbs, one behaviour) — the docstring says so
  explicitly and explains that `ack` is the message-semantics name, `delete` the
  storage-semantics name.
- *`--target module:factory` over reading the app container* ✅ Keeps the CLI free
  of any DI/app coupling; identical to `varco migrate`'s config resolution.
  ❌ One more thing to type — mitigated by `VARCO_RETENTION_TARGET` env fallback.

**Edge cases**

- `delete_where()` with no predicate → `ValueError` (both ABCs).
- `older_than` naive datetime → coerced to UTC with a warning (SQLite parity, see
  `varco_sa/dlq.py:128`).
- `limit=0` → `ValueError` (0 is a footgun that reads as "unlimited").
- Kafka/NATS `delete_where` → `NotImplementedError` whose message names
  `retention.ms` / JetStream `MaxAge`, so the operator is told the *right* fix.
- SA sweep under a transaction-mode pooler → each chunk is its own transaction
  (documented, mirrors the U-18 finding).
- Audit `delete_where` with `hash_chain=True` (Phase 12) → pruning breaks the chain
  by construction; the SA/Beanie implementations raise unless
  `allow_chain_break=True` is passed. Documented in Phase 12.

**Tests**

- `varco_core/tests/test_dlq.py` — `InMemoryDeadLetterQueue.delete_where` by
  `older_than`/`source`/`channel`/`limit`; no-predicate `ValueError`; `delete()`
  defaults to `ack()` on a subclass that only implements `ack`.
- `varco_core/tests/test_audit.py` — same for `AuditRepository`.
- `varco_sa/tests/test_sa_dlq.py`, `varco_sa/tests/test_sa_audit.py` — SQLite sweeps.
- `varco_kafka/tests/test_kafka_dlq.py`, `varco_nats/tests/test_nats_dlq.py` —
  assert the raising override's message names the backend's retention mechanism.
- `varco_redis/tests/test_redis_dlq.py` — `@pytest.mark.integration` chunked sweep.
- `varco_core/tests/test_retention_cli.py` — `main(["retention","prune",...])`
  returns 0; `--dry-run` deletes nothing; missing `--before` is a usage error (2).

**Docs** — `dead-letter-queues.md` + `database-auditing.md` gain a "Retention"
section each; `README.md` CLI table gains `varco retention prune`.

---

### Phase 3 — C1 collapse the client front door — [x] DONE

**New files**

- `varco_fastapi/varco_fastapi/client/front_door.py` (`client_for`, `client_class_for`)
- `varco_fastapi/varco_fastapi/client/advanced.py` (re-export shelf for the demoted API)
- `varco_fastapi/tests/test_client_front_door.py`
- `docs/client.md`

**Modified**

- `varco_fastapi/varco_fastapi/client/__init__.py` — `__all__` shrinks to the front
  door + the still-first-class pieces.
- `varco_fastapi/varco_fastapi/di.py` — add `bind_clients_from`; `bind_clients`
  stays (Phase 11 builds on it).
- `varco_fastapi/varco_fastapi/__init__.py` — export `client_for`.
- `README.md`, `CLAUDE.md` — client quick-start.

**Signatures**

```python
# varco_fastapi/client/front_door.py
def client_class_for(router_cls: type) -> type[AsyncVarcoClient]:
    """Return (and memoize) the generated client CLASS for a router."""

def client_for(
    router_cls: type,
    base_url: str | None = None,
    *,
    profile: ClientProfile | None = None,
    timeout: float | None = None,
    verify: bool | str = True,
    middleware: tuple[AbstractClientMiddleware, ...] | None = None,
    headers: Mapping[str, str] | None = None,
) -> AsyncVarcoClient:
    """THE documented way to get a client for a varco service."""

# varco_fastapi/di.py
def bind_clients_from(container: Any, *router_classes: type) -> None:
    """client_class_for() each router, then bind_clients() the results."""
```

`client_class_for` is memoized in a module-level `dict[type, type]` — repeated
`client_for(OrderRouter, ...)` calls must not re-run the metaclass.

**Front door vs. advanced shelf**

| Stays in `varco_fastapi.client.__all__` | Moves to `varco_fastapi.client.advanced` |
|---|---|
| `client_for`, `client_class_for` | `make_client` |
| `AsyncVarcoClient` / `VarcoClient` (for `Inject[VarcoClient[R]]` annotations) | `GenericClient` |
| `SyncVarcoClient` | `OpenAPIClient` |
| `ClientProfile`, `ClientConfig` | `ClientConfigurator` |
| the whole `middleware` module | `generate_client` |
| `JobHandle`, `JobFailedError`, `ClientProtocol` | |

The demoted names remain importable from their own modules **and** from
`varco_fastapi.client.advanced`. Importing them from `varco_fastapi.client`
directly raises `AttributeError` via a `__getattr__` shim whose message names the
new location — a legible break, not a silent one.

**DESIGN blocks**

- *One function returning a live instance, not a class*
  ✅ The complaint is "very complex to use" — the 90% call site wants an object.
  ✅ `client_class_for` is still there for the `Inject[VarcoClient[R]]` case.
  ❌ Two functions instead of one — the second is documented as the DI-only entry.
- *`__getattr__` shim over silent re-export*
  ✅ Every stale import gets a message naming the exact new path.
  ❌ A hard break at import time — intended; the release allows it, and a silent
     re-export would defeat the whole point of collapsing the front door.
- *Rejected: deleting the demoted modules.* ❌ `OpenAPIClient`/`GenericClient` solve
  real problems (third-party APIs, no-router services) that `client_for` cannot.
  Collapsing the *documented surface* achieves the goal without losing capability.

**Edge cases**

- `client_for(SomethingThatIsNotARouter)` → `TypeError` naming `VarcoRouter`.
- `client_for(router)` with neither `base_url` nor a peer/env source → the
  existing deferred-`RuntimeError`-at-first-request behaviour is preserved, but
  the message now names `client_for(..., base_url=)` and (from Phase 11)
  `VARCO_PEER_<NAME>_URL`.
- `client_for` called concurrently for the same router → the memo dict write is
  idempotent (same class recomputed at worst); no lock needed, and explicitly no
  `asyncio.Lock` at module level.
- `GenericRouter` (no type args) → CRUD methods absent, custom routes present.

**Tests** — `test_client_front_door.py`: `client_for` returns a working client
against an ASGI-transport test app; `client_class_for` memoizes (identity check);
`bind_clients_from(container, OrderRouter)` then
`container.get(VarcoClient[OrderRouter])` resolves; every demoted name raises
`AttributeError` from `varco_fastapi.client` with the new path in the message and
imports fine from `varco_fastapi.client.advanced`; `container.validate_bindings()`
passes after `bind_clients_from` (the per-package bootstrap-health test pattern).

**Docs** — `docs/client.md` (quick start + the migration table above);
`README.md` client section rewritten around `client_for`; `CLAUDE.md` gains a
"Scenario: call another varco service" block.

---

### Phase 4 — R1 DLQ redrive — [x] DONE

**New files**

- `varco_core/varco_core/event/redrive.py`
- `varco_core/varco_core/cli/dlq.py`
- `varco_core/tests/test_dlq_redrive.py`
- `varco_core/tests/test_dlq_cli.py`

**Modified**

- `varco_core/varco_core/event/dlq.py` — `get`, `list_entries` (concrete-but-raising).
- `varco_sa/varco_sa/dlq.py`, `varco_redis/varco_redis/dlq.py` — implement both,
  set `supports_random_access = True`.
- `varco_kafka/varco_kafka/dlq.py`, `varco_nats/varco_nats/dlq.py` — leave raising,
  add a docstring section pointing at `redrive_batch`.
- `varco_core/varco_core/cli/main.py` — register `dlq`.
- `varco_core/varco_core/event/__init__.py` — export `DlqRedriver`, `RedriveReport`,
  `DeadLetterNotAddressable`.

**Signatures**

```python
# AbstractDeadLetterQueue
async def get(self, entry_id: UUID) -> DeadLetterEntry | None:
    """Concrete-but-raising — random access is not portable (RD-4)."""

async def list_entries(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    channel: str | None = None,
    source: DeadLetterSource | None = None,
    tenant_id: str | None = None,       # Phase 6
    older_than: datetime | None = None,
    newer_than: datetime | None = None,
) -> list[DeadLetterEntry]:
    """Concrete-but-raising. NON-DESTRUCTIVE read — unlike pop_batch()."""

# varco_core/event/redrive.py
class DeadLetterNotAddressable(RuntimeError): ...

@dataclass(frozen=True)
class RedriveOutcome:
    entry_id: UUID
    published: bool
    acked: bool
    error: str | None = None

@dataclass(frozen=True)
class RedriveReport:
    attempted: int
    succeeded: int
    failed: int
    outcomes: tuple[RedriveOutcome, ...] = ()
    dry_run: bool = False

class DlqRedriver:
    def __init__(
        self,
        dlq: AbstractDeadLetterQueue,
        bus: AbstractEventBus,
        *,
        default_channel: str | None = None,
    ) -> None: ...

    async def redrive(self, entry_id: UUID, *, dry_run: bool = False) -> RedriveOutcome: ...

    async def redrive_batch(
        self,
        *,
        limit: int = 10,
        channel: str | None = None,
        source: DeadLetterSource | None = None,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> RedriveReport: ...
```

`DlqRedriver` is one of the **very few** classes permitted to hold an
`AbstractEventBus` directly — it joins `OutboxRelay` and `EventConsumer.register_to()`
on that list, for the same reason (it is infrastructure, not application logic).
This must be stated in `CLAUDE.md`'s layer-rule paragraph.

**Redrive algorithm**

1. Resolve entries: `get(entry_id)` (random-access path) or `list_entries()` →
   fall back to `pop_batch()` when `list_entries` raises `NotImplementedError`
   (stream path).
2. Reject entries with `event is None` and `payload is not None` — a
   never-deserializable payload cannot be republished. `RedriveOutcome(published=False,
   error="payload-only entry; not republishable")`, and the entry is **not** acked.
3. `await bus.publish(entry.event, channel=entry.channel or default_channel)`.
4. On success: `await dlq.ack(entry.entry_id)` (RD-4).
5. On publish failure: **do not ack**. The entry stays. Record the error.
6. `dry_run=True` → steps 3–4 skipped; the report lists what would have happened.

**DESIGN blocks**

- *Publish-then-ack (never ack-then-publish)*
  ✅ A crash between the two re-delivers the dead letter — at-least-once, which is
     the correct bias for a message you already nearly lost.
  ❌ A duplicate republish is possible; the inbox/dedup primitives already handle it.
- *`DlqRedriver` as a plain object with no `start()`/`stop()`*
  ✅ Structurally cannot become the parked auto-redrive scheduler.
  ❌ No background retry — deliberate.
- *`list_entries()` as a separate, non-destructive read from `pop_batch()`*
  ✅ The REST admin surface (Phase 10) must be able to *look* without consuming.
  ✅ `InMemoryDeadLetterQueue.pop_batch` is consume-on-pop (`dlq.py:451-478`) — a
     browse built on it would delete the operator's evidence.
  ❌ Two read methods on the ABC; the docstrings state the difference in the first line.

**Edge cases**

- Unknown `entry_id` → `RedriveOutcome(published=False, error="not found")`, no raise.
- Stream backend + single-entry redrive → `DeadLetterNotAddressable` naming the
  class and suggesting `redrive_batch`/`--batch` (RD-4).
- `entry.channel == ""` and no `default_channel` → `ValueError` naming the entry.
- `entry.source == JOB` → the "event" is a job payload; the redriver refuses
  (`error="job-sourced entry; re-enqueue via the job store"`) rather than
  publishing a job onto an event channel. Explicitly tested.
- Bus `publish()` raising → caught per entry; the batch continues; the report's
  `failed` count is non-zero and the CLI exits `1`.
- `limit` > available → fewer outcomes, no error.
- Redrive of an entry whose handler is still broken → it comes straight back to the
  DLQ. Documented as expected; this is why `dry_run` and the metrics exist.

**CLI**

```
varco dlq list    --target module:factory [--channel C] [--source S] [--limit N]
varco dlq redrive --target module:factory --bus module:factory
                  (--entry-id UUID | --batch [--limit N] [--channel C] [--source S])
                  [--dry-run]
varco dlq purge   --target module:factory --before ISO8601 [--limit N]   # → R3
```

**Tests**

- `test_dlq_redrive.py` — happy path (`InMemoryDeadLetterQueue` + `InMemoryEventBus`,
  `bus.drain()` after publish); publish failure leaves the entry un-acked; payload-only
  entry refused; job-sourced entry refused; `dry_run` publishes nothing; a stream-shaped
  fake DLQ (`supports_random_access=False`, `get` raising) → `DeadLetterNotAddressable`
  on single, working `redrive_batch`; empty-channel `ValueError`.
- `varco_sa/tests/test_sa_dlq.py` — `get`/`list_entries` filters, `supports_random_access`.
- `varco_redis/tests/test_redis_dlq.py`, `varco_kafka/tests/test_kafka_dlq.py`,
  `varco_nats/tests/test_nats_dlq.py` — `@pytest.mark.integration` round trip
  push → list/pop → redrive → assert the event arrives on the bus and the DLQ drains.
- `test_dlq_cli.py` — exit codes 0/1/2; `--dry-run` non-destructive.

**Docs** — `dead-letter-queues.md` gains "Redrive" (algorithm, at-least-once note,
the stream-backend limitation table); `CLAUDE.md` layer rule updated to list
`DlqRedriver` as a permitted bus holder; `README.md` CLI table.

---

### Phase 5 — R7 Beanie DLQ backend — [x] DONE (unit-tested; integration tests need Docker Mongo, not run)

**New files**

- `varco_beanie/varco_beanie/dlq.py`
- `varco_beanie/tests/test_beanie_dlq.py`

**Modified**

- `varco_beanie/varco_beanie/di.py` — register in `bootstrap()`.
- `varco_beanie/varco_beanie/__init__.py` — export.
- `varco_beanie/varco_beanie/migration/…` — declare the indexes for
  `varco migrate index` reconciliation.

**Signatures**

```python
class DeadLetterDocument(Document):
    entry_id: UUID = Field(default_factory=uuid4)   # also the Mongo _id
    source: str
    source_ref: str | None = None
    channel: str
    handler_name: str
    event_type: str | None = None
    payload: bytes | None = None
    error_type: str
    error_message: str
    attempts: int
    first_failed_at: datetime
    last_failed_at: datetime
    tenant_id: str | None = None                    # Phase 6

    class Settings:
        name = "varco_dead_letters"                 # RD-2
        indexes = [                                 # DECLARED, not built here
            [("channel", 1), ("last_failed_at", -1)],
            [("source", 1), ("last_failed_at", -1)],
            [("tenant_id", 1), ("last_failed_at", -1)],
        ]

class BeanieDeadLetterQueue(AbstractDeadLetterQueue):
    supports_random_access: ClassVar[bool] = True

    def __init__(self, *, ttl_seconds: int | None = None) -> None: ...
    # push / pop_batch / ack / count / get / list_entries / delete_where /
    # count_by_channel — all implemented
```

**DESIGN blocks**

- *No TTL index by default* (RD-2) ✅ dead letters are never silently deleted;
  ❌ operators must run a retention sweep — which is exactly Phase 2's job.
- *Indexes declared in `Settings.indexes` but built by `varco migrate index --create`*
  ✅ Plan 006's precedent; an index build inside a lifespan stalls a rolling deploy.
  ❌ One extra pre-deploy step, documented.
- *Mirror `SADeadLetterQueue` semantics exactly (`pop_batch` reads, `ack` deletes)*
  ✅ One mental model across durable backends.
  ❌ No visibility window → two concurrent relays double-process. Documented, same
     as SA; the single-relay assumption is stated in the class docstring.
- *`entry_id` as `_id`* ✅ `get`/`ack` are `_id` lookups, and a duplicate `push` of
  the same entry is a `DuplicateKeyError` we can treat as "already stored"
  (idempotent on redelivery — better than SA's ON CONFLICT only on Postgres).
  ❌ Requires a UUID-`_id` codec; Beanie handles it.

**Edge cases**

- `push()` on a `DuplicateKeyError` → treated as success, DEBUG log. Never raises
  (the contract).
- `push()` with the collection uninitialized (`CollectionWasNotInitialized`) →
  logged as ERROR and swallowed (the contract) — but the class docstring and the
  `di.py` bootstrap tell you to register `DeadLetterDocument` in `init_beanie`.
- `ttl_seconds` set → a `expireAfterSeconds` index is declared on `last_failed_at`
  and a WARNING is logged once at construction naming the data loss.
- `count()` on a huge collection → uses `count_documents` with the same filter,
  documented as O(n) on unindexed filters.
- Under `TenantIsolation.DATABASE`, the DLQ lives in the tenant's database →
  the `TenantFanoutSupervisor` note applies verbatim (cross-referenced).

**Tests** — `varco_beanie/tests/test_beanie_dlq.py` mirroring
`varco_redis/tests/test_redis_dlq.py` structure, `@pytest.mark.integration`
(needs a Mongo container): push/pop/ack/count; `get` by id; `list_entries` filters;
`delete_where(older_than=)` chunked; duplicate push is idempotent; `push` swallows
an induced write error; a `container.scan("varco_beanie"); validate_bindings()`
unit test (no Docker) for the DI binding health.

**Docs** — `dead-letter-queues.md` backend table gains Beanie; the RD-2 TTL
rationale is stated inline, not just in this plan.

---

### Phase 6 — R4 tenant-scoped audit + DLQ — [x] DONE (tenant_id stamping + filtering + rls_framework.py + 0002_dlq_audit_tenant_id.py revision all landed)

**New files**

- `varco_sa/varco_sa/migrations/versions/000X_dlq_audit_tenant_id.py`
- `varco_sa/varco_sa/rls_framework.py` (the two table names + a one-call helper)
- `varco_core/tests/test_dlq_tenancy.py`
- `varco_sa/tests/test_framework_rls.py`

**Modified**

- `varco_core/varco_core/event/dlq.py` — `DeadLetterEntry.tenant_id: str | None = None`
  (new defaulted field, appended → non-breaking on the dataclass).
- `varco_core/varco_core/event/consumer.py` — the `_make_retry_wrapper` DLQ path
  stamps `tenant_id` from the ambient `varco_core.tenancy.tenant_context()`.
- `varco_core/varco_core/service/outbox.py` — `OutboxRelay`'s dead-letter path
  stamps `tenant_id` likewise.
- `varco_core/varco_core/service/audit.py` — `AuditRepository.list_for_entity`
  gains `tenant_id: str | None = None` (**breaking**, see the ABC table).
- `varco_sa/varco_sa/dlq.py` — `tenant_id` column + filter on `list_entries`/
  `delete_where`/`count_by_channel`.
- `varco_sa/varco_sa/audit.py`, `varco_beanie/varco_beanie/audit.py`,
  `varco_beanie/varco_beanie/dlq.py` — tenant filters.

**Signatures**

```python
# varco_sa/rls_framework.py
FRAMEWORK_RLS_TABLES: Final[tuple[str, ...]] = ("varco_audit_log", "varco_dead_letters")

def framework_rls_upgrade(op: Any, *, tables: Sequence[str] = FRAMEWORK_RLS_TABLES,
                          tenant_column: str = "tenant_id") -> None: ...
def framework_rls_downgrade(op: Any, *, tables: Sequence[str] = FRAMEWORK_RLS_TABLES) -> None: ...
```

These wrap the existing `varco_sa.migration.ops.rls_upgrade` / `varco_sa.rls.enable_rls_ddl`
so the correct `(SELECT current_setting(..., true))` InitPlan form is used
(the documented U-? performance pitfall). **Nothing calls them automatically** —
they are meant to be pasted into a reviewed app revision, per CLAUDE.md's
"RLS enabled by a startup hook" pitfall.

**DESIGN blocks**

- *`tenant_id` on `DeadLetterEntry` sourced from the ambient tenant context, not a
  parameter* ✅ Every producer (consumer wrapper, outbox relay, job runner) gets it
  for free with zero call-site change. ❌ A dead letter produced outside a tenant
  context has `tenant_id=None` and is visible to a global operator only —
  documented as the correct behaviour (a framework-level failure is not a
  tenant's data).
- *RLS as a shipped helper + reviewed revision, never auto-applied* ✅/❌ as above.
- *Breaking `list_for_entity` signature over a second method* ✅ A second
  `list_for_entity_scoped()` guarantees the unsafe one keeps being called — which
  is the bug. ❌ External subclasses break; migration note covers it.
- *Rejected: making `tenant_id` mandatory on `DeadLetterEntry`.* ❌ Framework-level
  entries (outbox deserialize failures at boot) genuinely have no tenant.

**Edge cases**

- Entry pushed with no ambient tenant → `tenant_id=None`; `list_entries(tenant_id="x")`
  does **not** return it (a `None` tenant is not "every tenant").
- `list_entries(tenant_id=None)` → no tenant filter at all (operator/global view).
  This asymmetry is called out in the docstring; a `tenant_id=UNSCOPED` sentinel was
  considered and rejected as over-engineering for an admin-only surface.
- Alembic revision on a table with existing rows → `tenant_id` is nullable, no backfill.
- SQLite (tests) has no RLS → `framework_rls_upgrade` is a documented no-op guard on
  non-Postgres dialects, matching `SAAuditRepository.save`'s dialect fallback.
- RLS enabled but the GUC unset → zero rows, not an error. Documented; use
  `set_tenant_local()`.

**Tests**

- `test_dlq_tenancy.py` — an entry pushed inside `tenant_context("acme")` carries
  `tenant_id="acme"`; `list_entries(tenant_id="acme")` excludes a `None`-tenant entry;
  `delete_where(tenant_id=)` scopes correctly.
- `varco_core/tests/test_audit.py` — `list_for_entity(..., tenant_id=)` filters.
- `varco_sa/tests/test_framework_rls.py` — `@pytest.mark.integration` (Postgres):
  with RLS on and the GUC set to tenant A, a select cannot see tenant B's rows;
  the emitted DDL contains the `(SELECT current_setting(...))` InitPlan form.
- `varco_sa/tests/test_migrations.py` — the new revision upgrades and downgrades.

**Docs** — `database-auditing.md` + `dead-letter-queues.md` gain "Multitenancy"
sections; `multitenancy.md` gains `varco_audit_log`/`varco_dead_letters` to its
RLS table list; `CLAUDE.md` pitfall table gains the `None`-tenant asymmetry.

---

### Phase 7 — C2 typed custom-route client methods — [x] PARTIAL (build_client_method + both resolvers + stubs done and tested incl. load-bearing parity test; _VarcoClientMeta NOT yet rewired to use build_client_method for its custom-route branch — deferred per the plan's own stated high-blast-radius risk)

**New files**

- `varco_fastapi/varco_fastapi/client/method.py` (`build_client_method`)
- `varco_fastapi/varco_fastapi/client/stubs.py` (`.pyi` emitter)
- `varco_fastapi/tests/test_client_typed_routes.py`
- `varco_fastapi/tests/test_client_stubs.py`
- `docs/client-code-generation.md`

**Modified**

- `varco_fastapi/varco_fastapi/client/base.py` — `_VarcoClientMeta` (`base.py:585`)
  now builds every method through `build_client_method(route_contract, resolver)`
  driven by `build_contract(router_cls)`, replacing the local
  `custom_method(**kwargs: Any)` closure.

**Signatures**

```python
# varco_fastapi/client/method.py
class TypeResolver(Protocol):
    def resolve(self, schema: Mapping[str, Any] | None) -> type | None:
        """Map a contract JSON-Schema fragment to a runtime type (or None)."""

class ImportedTypeResolver:      # in-process: schema $ref → the real Pydantic class
    def __init__(self, contract: ServiceContract, router_cls: type) -> None: ...

class SynthesizedTypeResolver:   # cross-repo: schema → pydantic.create_model()
    def __init__(self, contract: ServiceContract) -> None: ...

def build_client_method(
    route: RouteContract,
    resolver: TypeResolver,
    *,
    async_capable_returns_job: bool = True,
) -> Callable[..., Awaitable[Any]]:
    """Synthesize one client method with a real __signature__ + __annotations__."""

# varco_fastapi/client/stubs.py
def render_stub(contract: ServiceContract, *, class_name: str) -> str: ...
def write_stub(contract: ServiceContract, path: Path, *, class_name: str) -> None: ...
```

The generated method's `__signature__` is
`(self, <body_param>?, *, <path params>, <query params>, with_async: bool = False)`
— body positional (it is the one obvious argument), everything else keyword-only.
Runtime dispatch: `sig.bind(*args, **kwargs)` → `apply_defaults()` → split by the
`ParamContract.kind` recorded at build time.

**DESIGN blocks**

- *One `build_client_method` shared by both resolvers* ✅ This is the mechanism that
  makes "same typed surface either way" true rather than aspirational; a divergence
  becomes a test failure, not a doc lie. ❌ An extra indirection in a hot-ish path —
  it runs once per method at class construction, never per call.
- *Keyword-only for everything except the body* ✅ Adding a query param later is
  never a positional-arity break for callers. ❌ Slightly more verbose call sites.
- *`.pyi` stubs over runtime-only typing* ✅ mypy/IDE see real types without the
  service being importable. ❌ A generation step that can drift — mitigated by a
  `varco gen-client-stubs --check` mode for CI (non-zero exit on drift).
- *Rejected: `TypedDict`-per-route kwargs.* ❌ No IDE arity checking, no defaults,
  and it still reads as `**kwargs` at the call site.

**Edge cases**

- Route with zero params → `(self, *, with_async: bool = False)`.
- Path param name colliding with a query param name → already a build-time
  `ValueError` (Phase 0).
- Param named `self` / `with_async` → renamed with a trailing underscore in the
  signature and mapped back at send time; one WARNING logged.
- Unresolvable annotation → the parameter is typed `Any` and still works.
- `async_capable` route called with `with_async=True` → return type is
  `<R> | JobAcceptedResponse`; the stub renders the union.
- Extra kwarg the route does not declare → `TypeError` from `sig.bind`. **This is
  the intended break** vs. today's silent `**kwargs` pass-through.
- `delete` (no response model) → return annotation `None`.

**Tests**

- `test_client_typed_routes.py` — `inspect.signature(Client.cancel)` has the exact
  expected parameters and annotations; calling with a wrong kwarg raises `TypeError`;
  a path param is placed in the URL and a query param in the query string (assert
  against a recording ASGI transport); body model is serialized; `with_async=True`
  returns a `JobAcceptedResponse`-shaped dict; a `SynthesizedTypeResolver`-built
  client produces a signature **equal** to the `ImportedTypeResolver` one for the
  same router (the key cross-topology parity test).
- `test_client_stubs.py` — `render_stub` output parses (`ast.parse`) and mypy-cleans
  in a tmpdir; `--check` mode detects drift.

**Docs** — `docs/client-code-generation.md`; `portable-contracts.md` gains the
"identical surface" parity guarantee and names the parity test as its enforcement.

---

### Phase 8 — C3 part 2: export CLI + cross-repo codegen — [x] DONE

**New files**

- `varco_fastapi/varco_fastapi/contract/cli.py` (`export-contract`, `gen-client`,
  `gen-client-stubs`)
- `varco_fastapi/varco_fastapi/contract/codegen.py`
- `varco_fastapi/varco_fastapi/contract/runtime.py` (`contract_client`)
- `varco_fastapi/tests/test_contract_cli.py`
- `varco_fastapi/tests/test_contract_codegen.py`

**Modified**

- `varco_fastapi/pyproject.toml` — `[project.entry-points."varco.commands"]`
  `export-contract`/`gen-client`/`gen-client-stubs` → `varco_fastapi.contract.cli:register_*`.
- `technical_docs/features/portable-contracts.md`.

**Signatures**

```python
# varco_fastapi/contract/runtime.py
def contract_client(
    contract: ServiceContract | str | Path,
    base_url: str | None = None,
    *,
    profile: ClientProfile | None = None,
    **kwargs: Any,
) -> AsyncVarcoClient:
    """Build a live client from an exported contract — no service import."""

def contract_client_class(contract: ServiceContract, *, name: str | None = None) -> type: ...

# varco_fastapi/contract/codegen.py
def render_client_module(contract: ServiceContract, *, class_name: str) -> str:
    """Emit a standalone .py: Pydantic models from `schemas` + a typed client."""
```

**CLI**

```
varco export-contract app.routers:OrderRouter [-o order.contract.json]
                      [--service-name N] [--service-version V] [--strict]
varco gen-client       -c order.contract.json -o order_client.py [--class-name OrderClient]
varco gen-client-stubs (app.routers:OrderRouter | -c order.contract.json)
                       -o client.pyi [--check]
```

**DESIGN blocks**

- *Two consumption modes (runtime `contract_client` + codegen `gen-client`)*
  ✅ `contract_client` is a one-liner for scripts/notebooks; `gen-client` is the
     "fully typed, checked into the consumer repo" path the interview asked for.
  ✅ Both go through `build_client_method`, so they cannot diverge.
  ❌ Two code paths to test — covered by one parametrized parity test.
- *Hand-rolled JSON-Schema → Pydantic emitter, scoped to what varco emits*
  ✅ No new runtime dependency on `datamodel-code-generator`.
  ✅ Bounded: object/array/scalar/enum/`$ref`/nullable/`oneOf` is the whole set.
  ❌ Anything outside that set degrades to `dict[str, Any]` with an emitted
     `# TODO: unsupported schema` comment — honest and visible, and the `schemas`
     block is OpenAPI-shaped so `datamodel-code-generator` remains a valid escape hatch.
- *Rejected: generating from the live `/openapi.json`.* ❌ Requires a running server
  and loses `crud_action`/`async_capable`/method names (see Phase 0 alternatives).

**Edge cases**

- `app.routers:OrderRouter` that does not import → exit 2 with the import error.
- Target is not a `VarcoRouter` → exit 2 naming the type.
- `-o` omitted → JSON to stdout (pipeable).
- Contract with a major-version mismatch → `ContractVersionError`, exit 1.
- Two schemas with the same `title` but different shapes → `ValueError` at build
  time naming both routes (a genuine service bug, not something to paper over).
- Generated module name colliding with a stdlib module → warning, still written.
- `--check` on a drifted stub → exit 1, unified diff on stderr.

**Tests**

- `test_contract_cli.py` — `main(["export-contract","tests.fixtures.routers:OrderRouter"])`
  writes a parseable contract; exit codes 0/1/2; stdout mode.
- `test_contract_codegen.py` — generated module is `ast.parse`-clean, imports in a
  tmpdir with only `varco-fastapi` importable, and its client's method signatures
  are **equal** to the in-process client's (parity, again); an unsupported schema
  degrades to `dict[str, Any]` with the TODO comment.

**Docs** — `portable-contracts.md` completed (export → commit → generate → call
walkthrough, CI recipe with `--check`); `CLAUDE.md` decision tree gains a
"cross-repo service integration" branch; `README.md`.

---

### Phase 9 — R5 `ReliabilityPreset` — [x] DONE

**New files**

- `varco_core/varco_core/reliability/__init__.py`
- `varco_core/varco_core/reliability/preset.py`
- `varco_core/varco_core/reliability/wiring.py`
- `varco_fastapi/varco_fastapi/reliability.py` (`ReliabilityLifecycle`)
- `varco_core/tests/test_reliability_preset.py`
- `varco_fastapi/tests/test_reliability_wiring.py`
- `technical_docs/features/reliability-preset.md`

**Modified**

- `varco_core/varco_core/event/consumer.py` — `@listen`'s `retry_policy=`/`dlq=`
  default from `None` to a private `_UNSET` sentinel (RD-7); resolution order
  `explicit → default preset → nothing`.
- `varco_fastapi/varco_fastapi/app.py` — `create_varco_app(reliability=None)`.
- `varco_core/varco_core/__init__.py` — export the preset names.

**Signatures**

```python
# varco_core/reliability/preset.py
@dataclass(frozen=True)
class ReliabilityPreset:
    retry_policy: RetryPolicy | None = None
    dlq: AbstractDeadLetterQueue | None = None
    outbox: bool = False
    audit: bool = False
    metrics: ReliabilityMetricsConfig | None = None
    outbox_max_attempts: int | None = None

    def __post_init__(self) -> None:
        """Raises ValueError when outbox_max_attempts is set without a dlq —
        mirrors OutboxRelay's refusal to configure silent data loss."""

    @classmethod
    def off(cls) -> ReliabilityPreset: ...
    @classmethod
    def best_effort(cls, *, dlq: AbstractDeadLetterQueue) -> ReliabilityPreset: ...
    @classmethod
    def durable(cls, *, dlq: AbstractDeadLetterQueue) -> ReliabilityPreset:
        """retry_policy=RetryPolicy.durable_delivery(), outbox+audit+metrics on."""

# varco_core/reliability/wiring.py
def set_default_reliability_preset(preset: ReliabilityPreset) -> None: ...
def get_default_reliability_preset() -> ReliabilityPreset: ...

# varco_fastapi/reliability.py
class ReliabilityLifecycle:
    def __init__(self, preset: ReliabilityPreset, *, container: Any) -> None: ...
    async def startup(self) -> None: ...   # metrics + OutboxRelay + AuditConsumer
    async def shutdown(self) -> None: ...
```

**DESIGN blocks**

- *A frozen config object + one lifespan component, NOT a `@Configuration`*
  ✅ A scanned `@Configuration` auto-activates (CLAUDE.md pitfall) — durability
     silently turning on is as bad as it silently staying off.
  ✅ `create_varco_app(reliability=preset)` is one explicit line.
  ❌ Not injectable-by-scan; that is the point.
- *`_UNSET` sentinel on `@listen`* (RD-7) ✅ per-handler opt-out survives a global
  default. ❌ A private sentinel in a public signature — rendered as `...` in docs.
- *Preset does not construct the DLQ* ✅ The DLQ is backend-specific and
  `varco_core` must not know concrete types; the caller passes an instance.
  ❌ Two lines instead of one — unavoidable given the layer rule.
- *Rejected: `ReliabilityPreset` reading env vars.* ❌ It holds live objects (a DLQ
  instance); env can only name a class, which re-introduces the concrete-type
  knowledge `varco_core` must not have.

**Edge cases**

- `off()` → byte-identical to today's behaviour (asserted by test).
- `outbox_max_attempts` without a `dlq` → `ValueError` at construction (mirrors
  `OutboxRelay.__init__`).
- `set_default_reliability_preset` called **after** classes with `@listen` are
  defined → the decorator stores `_UNSET` and resolves at `register_to()` time, so
  a late preset still applies. Explicitly tested (this is the whole reason the
  resolution is deferred).
- `@listen(retry_policy=None)` explicitly → no retry, preset ignored.
- `audit=True` with no `AuditRepository` in the container → `ReliabilityLifecycle.startup`
  raises with a message naming the missing binding, at startup, not at first event.
- Two presets applied (nested `create_varco_app` in a composite) → each app's
  lifecycle owns its own; the *global default* preset is process-wide and the last
  writer wins with one WARNING.

**Tests**

- `test_reliability_preset.py` — `off()` leaves `@listen` behaviour unchanged;
  `durable()` gives a bare `@listen` handler a retry policy and DLQ; explicit
  `retry_policy=None` wins over a durable default; `outbox_max_attempts` without
  `dlq` raises; late `set_default_reliability_preset` still applies.
- `test_reliability_wiring.py` — `create_varco_app(container, reliability=preset)`
  starts and stops the relay/consumer, installs metrics, and raises on a missing
  `AuditRepository`.

**Docs** — `technical_docs/features/reliability-preset.md` (new);
`CLAUDE.md` gains a "Scenario: opt into durability in one line".

---

### Phase 10 — R6 audit + DLQ REST admin & query surface — [x] DONE

**New files**

- `varco_fastapi/varco_fastapi/admin/__init__.py`
- `varco_fastapi/varco_fastapi/admin/audit_router.py`
- `varco_fastapi/varco_fastapi/admin/dlq_router.py`
- `varco_fastapi/varco_fastapi/admin/mount.py`
- `varco_fastapi/tests/test_audit_router.py`
- `varco_fastapi/tests/test_dlq_router.py`

**Modified**

- `varco_core/varco_core/service/audit.py` — `AuditRepository.list(...)`
  (concrete-but-raising).
- `varco_sa/varco_sa/audit.py`, `varco_beanie/varco_beanie/audit.py` — implement it.

**Signatures**

```python
# varco_core/service/audit.py
async def list(
    self,
    *,
    actor_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    tenant_id: str | None = None,
    correlation_id: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEntry]:
    """Concrete-but-raising — no portable scan primitive exists on the ABC."""

# varco_fastapi/admin/audit_router.py
def build_audit_router(
    audit_repo: AuditRepository,
    *,
    server_auth: Any | None = None,
    admin_role: str = "reliability-admin",
    prefix: str = "/audit",
    allow_delete: bool = False,
) -> APIRouter: ...

# varco_fastapi/admin/dlq_router.py
def build_dlq_router(
    dlq: AbstractDeadLetterQueue,
    *,
    redriver: DlqRedriver | None = None,
    server_auth: Any | None = None,
    admin_role: str = "reliability-admin",
    prefix: str = "/dlq",
) -> APIRouter: ...

# varco_fastapi/admin/mount.py
def mount_reliability_admin(
    app: FastAPI,
    *,
    audit_repo: AuditRepository | None = None,
    dlq: AbstractDeadLetterQueue | None = None,
    redriver: DlqRedriver | None = None,
    acknowledge_bundled_admin: bool = False,   # RD-9 — ValueError if False
    server_auth: Any | None = None,
    admin_role: str = "reliability-admin",
    prefix: str = "/reliability",
    dependencies: Sequence[Any] | None = None,
) -> None: ...
```

Routes:

| Method | Path | Notes |
|---|---|---|
| GET | `/audit/entries` | all `list()` filters as query params |
| GET | `/audit/entries/{entry_id}` | 404 when absent |
| GET | `/audit/entries/{entity_type}/{entity_id}` | `list_for_entity` |
| POST | `/audit/verify-chain` | Phase 12; 501 until then |
| DELETE | `/audit/entries` | retention sweep; **only when `allow_delete=True`** |
| GET | `/dlq/entries` | `list_entries()` filters |
| GET | `/dlq/entries/{entry_id}` | 501 on a stream backend (RD-4) |
| POST | `/dlq/entries/{entry_id}/redrive` | 501 without a `redriver` / on a stream backend |
| POST | `/dlq/redrive` | batch; body carries filters + `dry_run` |
| DELETE | `/dlq/entries/{entry_id}` | `delete()` |
| DELETE | `/dlq/entries` | `delete_where()`; 501 on Kafka/NATS |
| GET | `/dlq/stats` | `count()` + `count_by_channel()` when supported |

**DESIGN blocks**

- *Plain `APIRouter`s, not `VarcoRouter`s* ✅ Exactly the `build_policy_router` /
  `build_tenant_router` precedent — a standalone admin surface with hand-written
  JSON handlers and no service/repository generic. ❌ No CRUD generation; these are
  a dozen bespoke handlers anyway.
- *`mount_reliability_admin` with the acknowledgement kwarg* (RD-9) ✅/❌ as RD-9.
- *`allow_delete=False` by default on the audit router* ✅ An audit log you can
  DELETE over HTTP is not an audit log. Retention belongs to the CLI/sweep job.
  ❌ Operators who want it must pass a flag — intended friction.
- *`NotImplementedError` → HTTP 501 with the backend name in the detail*
  ✅ A capability gap reads as a capability gap, not a 500.
  ❌ One exception-mapping helper — 10 lines, shared by both routers.

**Edge cases**

- `server_auth=None` → routes mount unauthenticated and **one WARNING is logged at
  mount time** naming the risk (matches the tenant admin's behaviour).
- `redriver=None` → the redrive routes are not registered at all (not 501) — an
  absent capability should not appear in the OpenAPI schema.
- `from`/`to` inverted → 422 naming the fields.
- `limit` > 1000 → clamped to 1000, `X-Varco-Clamped: true` response header.
- A tenant-scoped caller (non-admin) → out of scope; this surface is admin-only by
  `admin_role`. Documented explicitly so nobody mounts it as a tenant-facing API.

**Tests**

- `test_audit_router.py` / `test_dlq_router.py` — `httpx.ASGITransport` against a
  test app: each filter narrows results; 404/422/501 paths; `allow_delete=False`
  hides DELETE; `mount_reliability_admin` without the acknowledgement raises
  `ValueError`; `admin_role` denial returns 403; unauthenticated mount logs once.

**Docs** — `dead-letter-queues.md` + `database-auditing.md` gain "REST admin"
sections; `CLAUDE.md` scenario + a pitfall row for the ungated-admin hazard
(mirroring the tenant admin row).

---

### Phase 11 — C4 peer-service registry — [x] DONE

**New files**

- `varco_fastapi/varco_fastapi/client/peer.py`
- `varco_fastapi/tests/test_peer_registry.py`
- `docs/peer-service-integration.md`

**Modified**

- `varco_fastapi/varco_fastapi/di.py` — `bind_peers`.
- `varco_fastapi/varco_fastapi/client/__init__.py` — export `PeerRegistry`, `bind_peers`.

**Signatures**

```python
@dataclass(frozen=True)
class PeerConfig:
    name: str
    url: str
    timeout: float = 30.0
    verify: bool | str = True
    profile_name: str | None = None
    contract_path: str | None = None
    token_ref: str | None = None            # RD-5 — a REFERENCE, never a secret

class SecretResolver(Protocol):
    def resolve(self, ref: str) -> str | None: ...

class PeerRegistry:
    def __init__(
        self,
        peers: Mapping[str, PeerConfig] | None = None,
        *,
        secret_resolver: SecretResolver | None = None,
        profiles: Mapping[str, ClientProfile] | None = None,
        allow_literal_secret: bool = False,
    ) -> None: ...

    @classmethod
    def from_env(cls, *, environ: Mapping[str, str] | None = None, **kw) -> PeerRegistry: ...

    def config(self, name: str) -> PeerConfig: ...
    def client(self, name: str, router_cls: type | None = None) -> AsyncVarcoClient: ...
    def names(self) -> tuple[str, ...]: ...

def bind_peers(container: Any, mapping: Mapping[str, type], *,
               registry: PeerRegistry | None = None) -> None:
    """Bind AsyncVarcoClient[RouterCls] for each peer name → router class."""
```

Default profile for a peer (the "resilience pre-wired" promise):
`AuthForwardMiddleware` → `CorrelationIdMiddleware` → `OTelClientMiddleware` →
`RetryMiddleware(RetryPolicy(max_attempts=3, base_delay=0.2))` →
`TimeoutMiddleware(peer.timeout)`, plus a **shared** `CircuitBreaker` per peer name
(the parked "per-endpoint circuit breaker" idea, landed as a `ClientProfile` recipe
exactly as the backlog says). One breaker instance per peer, held on the registry —
never per call (CLAUDE.md's per-call-breaker pitfall).

**DESIGN blocks**

- *`token_ref` indirection over a `_TOKEN` env var* (RD-5) ✅ credentials never
  appear in the peer-config surface, and the shape matches Plan 007's `dsn_ref`.
  ❌ One extra env var per authenticated peer — the default resolver reading
  `os.environ[ref]` keeps it to exactly one extra line.
- *Registry owns the per-peer `CircuitBreaker` and `ClientProfile`* ✅ shared
  instances by construction. ❌ The registry must be a DI singleton — enforced by
  `bind_peers` registering it as one and documented as a pitfall row.
- *`router_cls` optional on `client()`* ✅ With `contract_path` set, a peer needs no
  importable router at all — the cross-repo topology works through the same call.
  ❌ Neither given → `ValueError` naming both options.
- *Rejected: auto-discovering peers from a service registry (Consul/k8s DNS).*
  ❌ Out of scope and deployment-specific; `PeerConfig.url` accepts a k8s service
  DNS name today, which covers the common case with zero machinery.

**Edge cases**

- `VARCO_PEER_ORDERS_URL` missing but `_TIMEOUT` present → `ValueError` naming the
  missing URL var (a half-configured peer is a deploy bug).
- A `_TOKEN_REF` value that looks like a literal credential → `ValueError` (RD-5)
  unless `allow_literal_secret=True`.
- Peer name with a `-` or `.` → normalized to `_` for env lookup, kept verbatim as
  the peer name.
- `client()` for an unknown peer → `KeyError` listing the known peer names.
- Two peers pointing at the same URL → allowed (different auth/profile is a valid reason).
- `from_env()` with zero `VARCO_PEER_*` vars → an empty registry, not an error
  (a service with no peers is normal).

**Tests** — `test_peer_registry.py`: `from_env` parses every suffix; missing URL
raises; literal-secret detection raises and the escape hatch works; `client("orders")`
returns a client whose base URL and timeout come from env; the same `CircuitBreaker`
object is reused across two `client()` calls for one peer; `bind_peers` +
`container.get(VarcoClient[OrderRouter])` resolves and `validate_bindings()` passes;
auth forwarding puts the caller's bearer token on the outbound request (recording
transport); a contract-only peer (no `router_cls`) produces a client with the same
method signatures as the imported-router one.

**Docs** — `docs/peer-service-integration.md`; `CLAUDE.md` scenario + env-var table;
`README.md`.

---

### Phase 12 — R8 audit tamper-evidence (optional, ships last) — [x] DONE (AuditEntry hash fields + verify_chain in varco_core; SAAuditRepository(hash_chain=True) + 0003_audit_hash_chain.py revision; BeanieAuditRepository(hash_chain=True) implemented but only integration-tested — no Docker Mongo available this session; audit_router verify-chain endpoint now real; delete_where(allow_chain_break=) guard added)

**Modified**

- `varco_core/varco_core/service/audit.py` — `AuditEntry.prev_hash: str | None = None`,
  `AuditEntry.seq: int | None = None`, `AuditEntry.entry_hash() -> str`,
  `AuditRepository.verify_chain(entries)` (portable default, `@staticmethod`).
- `varco_sa/varco_sa/audit.py` — `hash_chain: bool = False` ctor flag, `seq BIGSERIAL`
  + `prev_hash` columns, chained `save()`.
- `varco_beanie/varco_beanie/audit.py` — same via a `varco_audit_seq` counter doc.
- `varco_sa/varco_sa/migrations/versions/000Y_audit_hash_chain.py` (new).
- `varco_fastapi/varco_fastapi/admin/audit_router.py` — `POST /audit/verify-chain`
  goes from 501 to real.

**Hash** — `sha256` over a canonical JSON encoding (sorted keys, no whitespace,
RFC 3339 UTC timestamps) of
`entry_id | occurred_at | action | entity_type | entity_id | actor_id | tenant_id |
correlation_id | diff | prev_hash`. Genesis entry: `prev_hash = None`, hashed as
the JSON literal `null`.

**DESIGN blocks**

- *Chain established in the repository, not the consumer* (RD-8) ✅ correct under
  concurrent consumers. ❌ Serializes audit writes — stated as the documented cost.
- *Opt-in `hash_chain=True`* ✅ existing deployments and throughput-sensitive ones
  are untouched. ❌ Two code paths in `save()` — a single `if`, tested both ways.
- *Rejected: a Merkle tree / periodic anchor.* ❌ Much more machinery for a feature
  the backlog itself rates 🟢 with no in-session pain behind it.

**Edge cases**

- `hash_chain=True` on a table with pre-existing unchained rows → the first chained
  entry's `prev_hash` is `None` and `verify_chain` reports the boundary rather than
  a break. Documented.
- `delete_where` on a chained table → breaks the chain by construction; raises
  unless `allow_chain_break=True` (cross-referenced from Phase 2).
- `verify_chain([])` → `True` (vacuously).
- A gap in `seq` → reported as a specific `ChainGap` finding, distinct from a
  `HashMismatch` (a deleted row and an edited row are different incidents).
- Beanie's counter document missing → created on first write with `upsert=True`.

**Tests** — `varco_core/tests/test_audit_chain.py` (pure hash + `verify_chain`
positive/negative/gap/empty); `varco_sa/tests/test_sa_audit_chain.py` (chained
saves verify; a manual UPDATE is detected; concurrent saves produce a single
unbroken chain — 20 concurrent tasks); `varco_beanie/tests/test_beanie_audit_chain.py`
(`@pytest.mark.integration`).

**Docs** — `database-auditing.md` gains "Tamper evidence" with the throughput
caveat and the retention interaction.

---

## Consolidated migration note

To be written as `technical_docs/migrations/009-reliability-and-integration.md` and
summarized in `README.md`'s changelog section.

| # | Change | Who breaks | Fix |
|---|---|---|---|
| 1 | `AuditRepository.list_for_entity()` gains keyword-only `tenant_id: str \| None = None` | Out-of-tree `AuditRepository` subclasses | Add the parameter to your override and filter on it. Ignoring it is the security bug this fixes. |
| 2 | `AbstractDeadLetterQueue` gains 6 members (`supports_random_access`, `get`, `list_entries`, `delete`, `delete_where`, `count_by_channel`) | Nobody at import time — all are concrete (raising or defaulted) | Implement the ones your operators need. `delete()` already works via `ack()`. |
| 3 | `AuditRepository` gains `list()`, `delete_where()`, `verify_chain()` | Nobody at import time — concrete-but-raising / portable default | Implement `list()` if you mount `build_audit_router`. |
| 4 | `OutboxRepository` gains `count_pending()`, `oldest_pending_at()` | Nobody — concrete-but-raising; the gauge self-disables | Implement for outbox lag alerting. |
| 5 | Client custom-route methods change from `**kwargs: Any` to a synthesized signature | Callers passing undeclared kwargs, or passing declared ones positionally | Everything except the request body is now keyword-only. A wrong kwarg is now a `TypeError` — that is the feature. |
| 6 | `make_client`, `GenericClient`, `OpenAPIClient`, `ClientConfigurator`, `generate_client` removed from `varco_fastapi.client.__all__` | `from varco_fastapi.client import GenericClient` | Import from `varco_fastapi.client.advanced` (or the original module). The `AttributeError` message names the new path. |
| 7 | `@listen`'s `retry_policy=`/`dlq=` defaults become an internal `_UNSET` sentinel | Code introspecting `@listen`'s defaults (very unlikely) | Passing `None` explicitly still means "no retry"; omitting now means "use the global preset", which defaults to `off()`. |
| 8 | `DeadLetterEntry` gains `tenant_id: str \| None = None` (appended, defaulted) | Positional construction beyond the last field (none exists) | None. Backends persisting entries need a `tenant_id` column — the shipped Alembic revision handles `varco_sa`. |
| 9 | New Alembic revisions on the `varco` branch (`tenant_id` on `varco_dead_letters`; optional audit chain columns) | Deployments on `varco migrate` | `varco migrate upgrade` (always `heads`, plural). `ensure_table()` deployments: `varco migrate adopt` first, then upgrade. |
| 10 | `mount_reliability_admin` requires `acknowledge_bundled_admin=True` | New API — nobody | Pass it, or run the admin surface standalone. |

**Nothing in this release changes default runtime behaviour without an explicit
opt-in.** `ReliabilityPreset.off()` is the default preset; metrics install only
when asked; RLS is never auto-enabled; the Beanie DLQ has no TTL index; the admin
surface mounts only via an explicit call with an explicit acknowledgement.

---

## Verification

```bash
# Per phase (run the touched packages)
uv run pytest varco_core/tests/ -q
uv run pytest varco_fastapi/tests/ -q
uv run pytest varco_sa/tests/ varco_beanie/tests/ varco_redis/tests/ \
              varco_kafka/tests/ varco_nats/tests/ -q

# Whole release
make test
make lint            # ruff check
make type-check      # mypy — MUST be clean on the generated stubs too
make docs            # link check on the new feature docs

# Docker-backed
uv run pytest varco_redis/tests/ -m integration
uv run pytest varco_kafka/tests/ -m integration
uv run pytest varco_nats/tests/  -m integration
uv run pytest varco_beanie/tests/ -m integration
uv run pytest varco_sa/tests/ -m integration      # Postgres, for the RLS tests

# DI bootstrap health (the "green suite, dead container" guard)
uv run pytest -k validate_bindings

# Contract parity — the load-bearing cross-topology test
uv run pytest varco_fastapi/tests/test_contract_codegen.py::test_signature_parity
uv run pytest varco_fastapi/tests/test_client_typed_routes.py::test_resolver_parity

# CLI smoke
uv run varco --help
uv run varco dlq --help
uv run varco retention prune --help
uv run varco export-contract --help
```

## Risks

- **Descriptor drift between the two client paths.** Invariant: both paths build
  every method through `build_client_method`. Enforced by the two parity tests
  named above; if either is deleted the guarantee is gone. Treat them as
  load-bearing, not as coverage.
- **`_VarcoClientMeta` rewrite (Phase 7) is the highest-blast-radius change** — it
  regenerates every CRUD method on every client. Mitigation: Phase 7 changes only
  the *custom*-route branch's construction path first, keeping the CRUD branch's
  existing closures, then migrates CRUD in a second commit with the existing client
  test suite green between the two.
- **`@listen` sentinel change (Phase 9)** touches the hottest decorator in the
  framework. Invariant: with `ReliabilityPreset.off()` (the default), the resolved
  `(retry_policy, dlq)` pair must be identical to today's. Assert it directly.
- **Redrive re-poisons the stream.** Redriving an entry whose handler is still
  broken sends it straight back to the DLQ. Mitigation: `dry_run`, the
  `varco.dlq.redriven{status}` counter, and prominent docs. Do not add automatic
  redrive (parked for exactly this reason).
- **Ack-after-publish means at-least-once redelivery.** A crash between publish and
  ack redelivers. This is the deliberate bias; the inbox/dedup primitives handle it.
- **Audit hash chain serializes writes (Phase 12).** Invariant: the feature is
  opt-in (`hash_chain=False` default) so no existing deployment's audit throughput
  changes.
- **Metric cardinality.** Invariant: no metric attribute may be `entry_id`,
  `event_type`, `handler_name`, or `tenant_id` unless the operator explicitly opted
  in (RD-3). A reviewer must check every new `.add(1, attributes=...)` call site
  against that list.
- **Beanie index builds.** Invariant: `Settings.indexes` are *declared*; nothing in
  the request path or lifespan builds them. `varco migrate index --create` is the
  only builder.
