# Plan 029 — `Idempotency-Key` middleware (D1) and the MCP v2 migration (N1)

Covers the two 🔴 **must** rows of BACKLOG's *"3.1 — API surface & interop (discover,
2026-09-04)"* cycle: **D1** (`Idempotency-Key` HTTP middleware, S) and **N1** (MCP v2 migration,
M–L). These are the only two rows the cycle cannot ship without (`BACKLOG.md:33`).

## Scope and siblings

This plan is **one of four** covering that backlog cycle. It does not cover the other cycle
("3.1 — trust store, hot reload & performance", rows T1–T7/P1–P4) at all — that work is plans
025–028.

| Plan | Rows | Severity | Depends on |
|---|---|---|---|
| **029 (this)** | D1, N1 | 🔴 must | — |
| 030 | N2 (CloudEvents), N3 (AsyncAPI), D5 (SBOM/CRA) | 🟡 should | — (independent of 029) |
| 031 | D4 (outbound webhooks) | 🟡 should | **029's D1 store ABC** (§D-D1-home) — 031 reuses it for consumer-side dedup guidance only, not as a hard import |
| 032 | D7 (flags seam), D6 (schedules), D8 (testkit) | 🟢 nice | — (droppable in full) |

**Ordering:** 029 first, because it is the release gate. 030/031/032 may proceed in parallel
with it; only 031 references anything 029 defines, and only in documentation.

**Research briefs backing this plan:**
- `design/research/003-mcp-python-sdk-v2-migration.md` — the whole of N1.
- `design/research/005-idempotency-webhooks-and-cloudevents.md` §1 — the whole of D1.

## Goal

A retried `POST`/`PATCH` carrying a repeated `Idempotency-Key` replays the first response instead
of executing twice, with correct behaviour under concurrency and payload mismatch. `MCPAdapter`
builds and mounts against MCP Python SDK v2.x, which is what `pip install mcp` resolves to today.

## Non-goals

- **No client-side idempotency.** D1 is a *server* middleware. `varco_fastapi.client` sending an
  `Idempotency-Key` on outbound calls is a separate row nobody has filed.
- **No idempotency for `GET`/`PUT`/`DELETE`.** Already idempotent per RFC 9110; the draft targets
  `POST`/`PATCH` only (brief 005 §1) and widening the scope only creates surprising 409s.
- **No dual mcp v1/v2 support.** Rejected on evidence (§D-N1-pin).
- **No MCP protocol features new in 2026-07-28** — Multi-Round-Trip Requests, `inputRequests`,
  `x-mcp-header`. Migration is repair to parity, not feature work (§D-N1-parity).
- **No change to `AsyncCache`.** CLAUDE.md's decision tree forbids adding a method to it; this is
  the constraint that shapes §D-D1-atomic, not an oversight.

---

## Design

### Phase order

```
P0  D1a  🔴 S  varco_core.idempotency — ABC, record, fingerprint, in-memory impl
P1  D1b  🔴 S  IdempotencyMiddleware in varco_fastapi + cache/repo-backed stores
P2  N1a  🔴 M  mcp>=2,<3 pin bump + to_mcp_server() decorator → on_* rewrite
P3  N1b  🔴 S  mount() / SSE transport reconcile + tests + docs
```

D1 sorts first because it is S-sized and touches nothing N1 touches; N1's blast radius is one
optional extra and one file.

---

## §D-D1-home — `varco_core.idempotency` for the contract, `varco_fastapi` for the middleware

The ABC, the record value object, the fingerprint function, and the in-memory implementation live
in a new `varco_core/varco_core/idempotency/`. Only `IdempotencyMiddleware` lives in
`varco_fastapi/varco_fastapi/middleware/idempotency.py`.

This is the same seam rule CLAUDE.md already states three times — `varco_fastapi.tenancy` imports
only `varco_core.tenancy`; `varco_fastapi` imports only `varco_core.migration`; TLS trust lives in
`varco_core.tls` and `varco_fastapi` may import it, never the reverse.

⚠️ **`varco_core/varco_core/service/inbox.py` is NOT the home and NOT reusable.** Verified: its
`InboxEntry`/`InboxRepository`/`InboxPoller` solve the *bus→handler* gap (`inbox.py:1-60`) — an
event is persisted before a handler runs so an `InboxPoller` can re-publish it after a crash.
HTTP idempotency is the opposite shape: it stores a *response* to suppress re-execution, and has
no poller and no bus. Sharing a table between them would couple two unrelated retention policies.

DESIGN: a new `varco_core.idempotency` package over extending `service.inbox`
  ✅ Keeps the `varco_fastapi` → `varco_core` seam intact; a non-HTTP caller (a job runner
     deduplicating a command) can use the store without importing FastAPI.
  ✅ Independent retention: inbox entries are deleted once processed; idempotency records must
     *survive* processing for the full TTL — that is their entire purpose.
  ✅ New top-level package, so `__all__` additions are purely additive under SemVer.
  ❌ A second "we have seen this before" concept in the codebase. Mitigated by a docstring in each
     that names the other and states the distinction in one line.

### §D-D1-atomic — the store ABC owns an atomic `reserve()`, because `AsyncCache` cannot

**This is the load-bearing decision of D1.** Verified: `AsyncCache` (`varco_core/varco_core/cache/base.py:86-152`)
exposes `get`/`set`/`delete`/`exists`/`clear`/`delete_prefix` and **no atomic set-if-absent**.
`exists()` then `set()` is a race: two concurrent retries both observe "absent" and both execute.

CLAUDE.md's decision tree forecloses the obvious fix — *"a bulk/batch capability? → `BulkCache`
with a portable `CacheBackend` default, NEVER a new method on `AsyncCache` (breaks `isinstance()`
for out-of-tree caches, Plan 011 D-11)"*. The same reasoning applies verbatim to an `add()`.

So the atomicity requirement is pushed **up** into the new ABC:

```python
class AbstractIdempotencyStore(ABC):
    async def reserve(self, key: str, fingerprint: str, *, ttl: float) -> ReserveOutcome: ...
    async def complete(self, key: str, record: IdempotencyRecord) -> None: ...
    async def get(self, key: str) -> IdempotencyRecord | None: ...
```

`reserve()` is the single atomic primitive and returns one of three outcomes — `ACQUIRED` (first
caller, proceed), `IN_FLIGHT` (reserved, no stored response yet → 409), `REPLAY` (a completed
record exists → replay it, or 422 if its fingerprint differs).

Per-implementation atomicity, each using its backend's own native primitive:

| Implementation | Package | Atomic primitive |
|---|---|---|
| `InMemoryIdempotencyStore` | `varco_core` (default, tests) | a lazily-created `asyncio.Lock` (CLAUDE.md: never at module level or `__init__`) |
| `RedisIdempotencyStore` | `varco_redis` | `SET key value NX PX ttl` |
| `SAIdempotencyStore` | `varco_sa` | `INSERT` against a `UNIQUE(key)` index, catching `IntegrityError` |
| `BeanieIdempotencyStore` | `varco_beanie` | unique index + `DuplicateKeyError` |

DESIGN: `reserve()` on the new ABC over an `add()` on `AsyncCache`
  ✅ Correct under concurrency, which an `exists`+`set` store is not — and concurrency is exactly
     the case idempotency exists to handle (a client retrying because it did not see a response
     *while the first request is still running*).
  ✅ Obeys the Plan 011 D-11 rule without needing an exception to it.
  ✅ Each backend uses the primitive it already has; nothing is emulated.
  ❌ Four implementations instead of one cache-backed adapter. Accepted: three of them are ~30
     lines, and the alternative is a correctness bug.
  ❌ A cache-only deployment (no Redis, no DB) gets only the in-memory store, which is per-process
     and therefore wrong behind a load balancer. **Must be documented as such**, and the in-memory
     store's docstring must say it is for single-process and test use — the same warning
     `InMemoryRateLimiter` already carries.
  Rejected — **a `CacheIdempotencyStore` over the `AsyncCache` Protocol**: ❌ unfixably racy given
  the Protocol's method set, and the fix is the forbidden one.
  Rejected — **`varco_core/lock.py`'s distributed lock around a plain cache store**: ❌ two round
  trips and a lock lifetime to tune where the backends already offer a one-round-trip primitive;
  a lock that expires mid-request reintroduces the race it was added to close.

### §D-D1-fingerprint — what is bound, and what a mismatch means

Brief 005 §1: the draft recommends the server generate a fingerprint from *method, target URI and
body contents*, and Stripe (the de-facto standard the draft defers to) rejects a reused key with a
different payload.

Fingerprint = `sha256(method || "\n" || raw_path || "\n" || sorted_query || "\n" || sha256(body))`,
hex-encoded. The raw body bytes are hashed, not a parsed-and-re-serialized form, so key ordering
and whitespace cannot produce a spurious mismatch in either direction.

Outcomes, all from brief 005 §1's status-code table:

| Situation | Response |
|---|---|
| No `Idempotency-Key` on a `POST`/`PATCH` | Pass through untouched (see §D-D1-optin) |
| Key present, never seen | Execute; store; return the real response |
| Key seen, **completed**, fingerprint matches | Replay the stored response + `Idempotency-Replayed: true` |
| Key seen, **completed**, fingerprint differs | **422 Unprocessable Content** |
| Key seen, **reserved but not completed** | **409 Conflict** |
| Malformed key (empty, or over `max_key_length`) | **400 Bad Request** |

All four error responses are emitted as RFC 9457 `application/problem+json` through varco's
existing error taxonomy, not hand-built dicts — three new `ServiceException` subclasses with
`code` + `message_key` per CLAUDE.md's error-taxonomy rule.

DESIGN: 409-on-in-flight over blocking until the first request completes
  ✅ What the draft recommends (brief 005 §1 "Concurrency Handling") and what a caller can act on.
  ✅ No held connection, no server-side wait ceiling to tune, no thread/task pileup under a retry
     storm — which is precisely when this path gets hot.
  ❌ A client that retries aggressively sees 409s rather than the answer. Mitigated: the 409 body
     carries `Retry-After`.

### §D-D1-replay — store the status, the body, and an allowlist of headers

Brief 005 §1 "Response Replay Storage": store status, body, and *critical* headers; do **not**
replay headers that change with time.

Replayed: `Content-Type`, `Location`, `Content-Language`, and any header whose name matches a
configurable `replay_header_allowlist`.
**Never replayed**, hard-coded: `Date`, `Set-Cookie`, `Cache-Control`, `Age`, `Expires`, `ETag`,
`Server`, `Content-Length` (recomputed), and every varco-generated correlation header — replaying
a correlation id would make two distinct requests indistinguishable in the trace backend, which
is worse than losing the header.

**Streaming responses are not captured.** The draft is silent (brief 005 §1) and buffering an
arbitrary `StreamingResponse` to store it is an unbounded-memory hazard. A streaming response
passes through and the reservation is **released**, so a retry re-executes. Documented as a known
limitation with the reasoning, not silently.

A `max_stored_body_bytes` ceiling (default 1 MiB) applies to non-streaming responses too; over it,
the same release-and-pass-through path runs.

### §D-D1-scope — the storage key is scoped, and it fails closed

Storage key = `idempotency:{tenant}:{subject}:{key}`, where `tenant` comes from `current_tenant()`
(CLAUDE.md: the single source of truth, never `RequestContext`) and `subject` from the ambient
`AuthContext` when one exists.

⚠️ **Fails closed, deliberately.** When tenancy is enabled and `current_tenant()` is unset, the
middleware raises rather than falling back to a global key — the same rule and the same reasoning
as `tenancy_cache_key()` and `localization_cache_key()` (CLAUDE.md, RD-6). A cross-tenant
idempotency collision would replay one tenant's response to another tenant, which is a data leak,
not a cache miss. When tenancy is disabled the tenant segment is a literal `-`.

### §D-D1-optin — opt-in per route, never global-by-default

The middleware is **not** added by `create_varco_app()` by default, and is registered like every
other middleware through `install_middleware_stack` (`varco_fastapi/varco_fastapi/middleware/__init__.py:75`),
which takes an outermost-first list and accepts `(cls, kwargs)` entries.

Placement: **inside** `ErrorMiddleware` (so its 409/422 render through the normal error path) and
**inside** `RequestContextMiddleware` (so `current_tenant()` and the auth subject are populated
before §D-D1-scope reads them). This ordering is a correctness requirement, not a preference, and
gets an explicit test.

`require_key: bool = False` by default — a `POST` without the header is executed normally. Setting
it to `True` makes the header mandatory and returns 400 without it (brief 005 §1). Per-path
control via `include_paths`/`exclude_paths`, matching `RequestLoggingMiddleware`'s existing
`skip_paths` convention.

### §D-D1-ttl — 24 hours, following Stripe

Default `ttl = 86400.0`. Brief 005 §1: the draft leaves retention as a SHOULD-publish and Stripe
retains for 24 hours. Configurable via `IdempotencySettings` — a pydantic `BaseSettings`
registered with `@Provider`, never `@Singleton` (CLAUDE.md's providify rule).

⚠️ **The draft is expired** (brief 005 §1: `draft-ietf-httpapi-idempotency-key-header-07`, expired
2026-04-18, never published as an RFC). Every place the draft is silent, this plan follows Stripe
and says so. The docs must state that varco implements an expired draft plus de-facto practice,
not a standard — claiming conformance to an RFC that does not exist would be false.

---

## §D-N1-pin — bump to `mcp>=2,<3`; dual support is rejected

`mcp` is an **optional extra**, not a hard dependency (`varco_fastapi/pyproject.toml:49`,
`mcp = ["mcp>=1.28.1,<2"]`). Blast radius: users who installed `varco-fastapi[mcp]`. Nobody else
sees a resolver change, which is what makes a dependency major-bump acceptable inside a minor
release.

Brief 003 §6 is unambiguous that one codebase cannot cleanly serve both: v1 registers handlers by
decorator post-construction, v2 by constructor argument, and the choice is made at instantiation.
The ecosystem precedent it cites (IBM `mcp-context-forge`, `langchain-mcp-adapters`) is "pin `<2`,
then migrate wholesale" — which is exactly what varco did and is now completing.

Also note: brief 003 §1 says `pip install mcp` **already resolves to 2.x**, and v1.x is
maintenance-only with security fixes alone. The current pin does not protect users; it strands
them.

DESIGN: hard bump over a compatibility shim
  ✅ The already-shipped code anticipated exactly this — `to_mcp_server()`'s DESIGN block states
     the low-level `Tool`-with-schema path is "portable across mcp v1 and v2 … only handler
     *registration* differs, so a future v2 migration touches only the registration lines"
     (`varco_fastapi/varco_fastapi/router/mcp.py:722-726`). This plan is that migration, and the
     prediction holds.
  ✅ Deletes two `# type: ignore[untyped-decorator]` suppressions (`mcp.py:778`, `:782`) — the M1
     suppression-debt gauge moves the right way.
  ❌ A user pinned to mcp 1.x cannot take varco 3.1's `varco-fastapi[mcp]`. Accepted and
     CHANGELOG-flagged under a **BREAKING (optional extra)** heading.

### §D-N1-rewrite — the mechanical change

Per brief 003 §2/§3, `to_mcp_server()` becomes:

```python
from mcp.server import Server           # was: from mcp.server.lowlevel import Server
from mcp.types import CallToolRequestParams

mcp_tools = _to_mcp_tools(self._tools)

async def _list_tools(ctx: Any) -> list[Any]:
    return mcp_tools

async def _call_tool(ctx: Any, params: CallToolRequestParams) -> Any:
    from mcp.types import TextContent
    result = await self.execute(params.name, params.arguments or {})
    return CallToolResult(content=[TextContent(type="text", text=_json.dumps(result, default=str))])

server = Server(
    name=f"{self._router_cls.__name__}MCP",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)
```

Three substantive deltas beyond moving the handlers:
1. **`ctx: ServerRequestContext` is now the first argument of every handler** (brief 003 §2).
2. **`call_tool` receives a `CallToolRequestParams` object**, not `(name, arguments)` positionals
   — `self.execute()`'s own signature does not change, only its call site.
3. `_to_mcp_tools` is **untouched**. It builds `Tool(name=…, description=…, inputSchema={…})`,
   which brief 003 §3 confirms is still the v2 shape. This is the payoff of the original
   low-level-`Server` decision (`mcp.py:712-721`).

### §D-N1-parity — the wire changes are the SDK's problem, with one thing to verify

Brief 003 §5 lists real 2026-07-28 protocol changes: statelessness (no `initialize` handshake, no
`Mcp-Session-Id`), a required `resultType` on all results, and `ttlMs`/`cacheScope` required on
list-shaped results via `CacheableResult`.

varco does not emit protocol frames — it returns SDK types and the SDK serializes them. So the
default position is that these are transparent. **But `tools/list` is exactly a list-shaped result
that now requires `ttlMs` and `cacheScope`**, and varco's `on_list_tools` returns a bare
`list[Tool]`. Step 11 exists to determine, against the installed SDK, whether v2 defaults those
fields or whether `_list_tools` must return a `ListToolsResult` carrying them. **This is an
open question resolved by experiment, not by reading — see Risks.**

`mount()` needs no structural change: brief 003 §4 confirms `SseServerTransport` still exists and
varco's hand-built Starlette recipe works as-is, and that `root_path` handling for sub-path
mounting is automatic. Two things to verify at Step 12: the SDK's httpx→**httpx2** switch (brief
003 §4) — varco does not construct an `http_client` for the server path, so this should be inert —
and that HTTP+SSE being *deprecated* in favour of Streamable HTTP (brief 003 §5) is recorded as a
follow-up row rather than fixed here.

---

## Steps

### Phase 0 — D1a: the `varco_core.idempotency` contract

1. [x] `varco_core/varco_core/idempotency/__init__.py`, `record.py`, `base.py`, `memory.py`,
       `fingerprint.py`, `settings.py`. `IdempotencyRecord` is `@dataclass(frozen=True)`
       (status, body bytes, headers mapping, fingerprint, created_at). `ReserveOutcome` is an
       enum of `ACQUIRED`/`IN_FLIGHT`/`REPLAY`.
2. [x] `AbstractIdempotencyStore` per §D-D1-atomic — `reserve`/`complete`/`get`/`release`/`delete_expired`.
       Docstrings carry `Args:`/`Returns:`/`Raises:`/`Edge cases:`/`Async safety:`, and the class
       docstring states the §D-D1-atomic contract: **`reserve()` MUST be atomic**; a store that
       cannot offer it is not a valid implementation.
3. [x] `InMemoryIdempotencyStore` with a lazily-created `asyncio.Lock`. Docstring warns
       single-process only.
4. [x] `compute_fingerprint()` per §D-D1-fingerprint.
5. [x] `IdempotencySettings(BaseSettings)` — `enabled`, `ttl_seconds=86400`, `require_key=False`,
       `max_key_length=255`, `max_stored_body_bytes=1048576`, `replay_header_allowlist`.
       env prefix `VARCO_IDEMPOTENCY_`.
6. [x] Three exceptions in `varco_core/exception`: `IdempotencyKeyConflictError` (409),
       `IdempotencyFingerprintMismatchError` (422), `IdempotencyKeyInvalidError` (400) — each with
       `code` and `message_key` per CLAUDE.md's taxonomy rule.
7. [x] Unit tests: fingerprint stability/sensitivity; all three `reserve()` outcomes; a genuine
       concurrency test (`asyncio.gather` of N reservations asserting exactly one `ACQUIRED`).
8. [x] ⚠️ Run `uv run python scripts/import_budget.py --check --warn-only`. The new package must
       **not** be eagerly imported by `varco_core/__init__.py` — it is PEP 562 lazy as of 3.1 and
       must stay that way (CLAUDE.md's import-budget rule).
9. [x] Regenerate `design/api-freeze-and-standards/measurements/api-surface.json` — **mandatory**,
       `--check` gates `make lint` and CI.

⛔ **CHECKPOINT** — `make lint`, `make type-check`, `make test PKG=varco_core` green before Phase 1.

### Phase 1 — D1b: the middleware and the durable stores

10. [x] `varco_fastapi/varco_fastapi/middleware/idempotency.py` — `IdempotencyMiddleware` per
        §D-D1-replay/§D-D1-scope/§D-D1-optin. Export from `middleware/__init__.py`; document its
        required position in that module's docstring stack diagram.
11. [x] `RedisIdempotencyStore` (`varco_redis`, `SET NX PX`), `SAIdempotencyStore` (`varco_sa`,
        unique index + `IntegrityError`), `BeanieIdempotencyStore` (`varco_beanie`, unique index +
        `DuplicateKeyError`).
12. [x] `varco_sa` migration revision for the idempotency table + `register_framework_metadata()`
        (`varco_sa/varco_sa/metadata.py:55`).
13. [x] Tests: replay round-trip; 409 while in flight; 422 on fingerprint mismatch; 400 on a
        malformed key; header allowlist honoured and `Date`/`Set-Cookie` dropped; streaming
        response passes through and releases; over-ceiling body passes through; **middleware
        ordering test** (inside `ErrorMiddleware`, inside `RequestContextMiddleware`);
        **fail-closed test** — tenancy on, no ambient tenant, raises.
14. [x] Integration tests (`-m integration`) for the Redis and SA stores, including a concurrent
        `reserve()` race against the real backend. Per-test namespacing with a `uuid4().hex[:8]`
        run id (CLAUDE.md's shared-container rule).
15. [x] `testkit/varco_conformance/` — either add an `idempotency_store.py` suite subclassed by all
        four implementations, **or** add a row to `COVERAGE.md` justifying its absence. CLAUDE.md
        makes this a rule for any new ABC with multiple implementations; the suite is the right
        call here because `reserve()`'s atomicity contract is precisely what a conformance suite is
        for.
16. [x] Docs: README section; `technical_docs/features/idempotency-key.md` with a Pitfalls table
        (in-memory store behind a load balancer; the streaming limitation; the expired-draft
        status; middleware ordering). CLAUDE.md gets a one-line pointer and a Decision-Tree branch
        only.
17. [x] Regenerate the API surface snapshot; re-run the import budget.

⛔ **CHECKPOINT** — full `make test` + `make lint` + `make type-check` green. D1 is shippable
independently of N1; if N1 slips, this is still a complete release increment.

### Phase 2 — N1a: the SDK bump and the rewrite

18. [x] `varco_fastapi/pyproject.toml:49` → `mcp = ["mcp>=2,<3"]`; update the comment block above
        it (currently explaining the `<2` pin) to record the migration and cite brief 003.
        `uv sync --all-packages --all-extras`.
19. [x] Rewrite `to_mcp_server()` per §D-N1-rewrite (`varco_fastapi/varco_fastapi/router/mcp.py:706-789`).
        Remove both `# type: ignore[untyped-decorator]` suppressions if v2's typing permits;
        keep them with a comment if not.
20. [x] Update the `to_mcp_server()` DESIGN block: the "❌ Pins onto mcp's maintenance-only v1.x
        branch" drawback is now resolved and must be rewritten, not deleted — record that the
        v1→v2 portability claim it made was tested and held.
21. [x] **Resolve §D-N1-parity's open question by experiment.** Write a test that runs a real
        `tools/list` against the built server and asserts on the emitted result shape. If
        `ttlMs`/`cacheScope` are required and not defaulted, return a `ListToolsResult` carrying
        them (`cacheScope="private"`, `ttlMs` from a new `MCPAdapter` parameter defaulting to
        60_000). Record the finding in the plan's evidence directory.

### Phase 3 — N1b: transport, tests, docs

22. [x] Verify `mount()` against v2 — `SseServerTransport` import path, `connect_sse` recipe,
        `root_path` sub-path mounting (brief 003 §4). Fix only what breaks.
23. [x] Update every MCP test in `varco_fastapi/tests/` for the v2 handler shape.
24. [x] Docs: README MCP section; CHANGELOG under **BREAKING (optional extra)** naming the pin
        change and linking brief 003; a follow-up BACKLOG row for *"HTTP+SSE deprecated in the
        2026-07-28 spec — move `mount()` to Streamable HTTP"* (brief 003 §5), which is new scope,
        not repair, and does not belong in this plan.
25. [x] Regenerate the API surface snapshot if any `__all__` changed.

⛔ **CHECKPOINT** — `make lint`, `make type-check`, `make test`, and the MCP integration path all
green.

---

## Parked

| Item | Why | Un-park trigger |
|---|---|---|
| Client-side `Idempotency-Key` emission in `varco_fastapi.client` | Not in D1's scope; the server half is the adoption blocker | A consumer asks, or D4's dispatcher (plan 031) wants it for its own outbound calls |
| MCP Streamable HTTP transport | HTTP+SSE is deprecated but functional (brief 003 §5); replacing a working transport is new scope in a repair plan | A client drops HTTP+SSE support, or 3.2 |
| MCP Multi-Round-Trip Requests / elicitation | Feature work behind a repair | A consumer needs interactive tools |
| Idempotency for `GET`/`PUT`/`DELETE` | Already idempotent per RFC 9110 | Never, absent evidence |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| ⚠️ **ASSUMPTION** — that v2 defaults `resultType`/`ttlMs`/`cacheScope` rather than requiring varco to emit them. Brief 003 §5 establishes the fields are *required by the spec* but does **not** state whether the SDK defaults them | High — a wrong guess ships a protocol-invalid server | **Step 21 resolves it by experiment against the installed SDK.** Not resolvable by reading; do not skip |
| `mcp` major bump inside a minor release | Medium | It is an optional extra (`pyproject.toml:49`), `pip install mcp` already resolves to 2.x, v1.x is security-only (brief 003 §1). CHANGELOG BREAKING note |
| In-memory idempotency store used behind a load balancer | High if it happens | Docstring + Pitfalls-table warning; durable stores shipped in the same phase so the correct option always exists |
| Fail-closed tenant scoping breaks an existing app that enables the middleware with tenancy half-configured | Medium | It is opt-in and off by default; the raise is loud and names the cause |
| ⚠️ **ASSUMPTION** — that no MCP client varco users run depends on the removed `initialize` handshake. Brief 003 §1 says v2 servers serve earlier protocol revisions transparently, but we have not tested a v2025 client against a v2 varco server | Medium | Note it in the CHANGELOG; a real client test is a follow-up row |
| Buffering response bodies raises memory under load | Low | `max_stored_body_bytes` ceiling, streaming excluded entirely |

## Open questions

1. **ANSWERED at Step 12 — Should `SAIdempotencyStore`'s table be tenant-partitioned under
   `TenantIsolation.SCHEMA`?** **No.** Verified precedent:
   `varco_sa/varco_sa/tenancy/router.py:98` states outright that "global tables and framework
   tables... carry no symbolic token" — meaning every existing framework table (outbox, jobs, dlq,
   dedup log, audit log, encryption keys, the tenant catalog itself) is deliberately excluded from
   `schema_translate_map` routing and stays in the connection's single shared/default schema
   regardless of which tenant is active, the same way `fanout_framework_tables` defaults to
   `False` for `DATABASE` isolation (`varco_core/varco_core/tenancy/settings.py:117`). `varco_idempotency`
   (`varco_sa/varco_sa/idempotency.py`) follows this exact convention — it is registered via
   `register_framework_metadata()` like every other framework table and carries no symbolic
   schema token, so it is never routed per-tenant. Isolation for idempotency records is provided
   entirely by the storage *key* (§D-D1-scope's `idempotency:{tenant}:{subject}:{key}` prefix,
   already a plain string column value in one shared table), not by the schema the row lives in —
   consistent with, not a new mechanism alongside, how every other framework table already works.
2. **ANSWERED at Step 10 — Does the 409 need `Retry-After` in seconds or a date?** **Seconds, a
   fixed conservative value (`1`), not derived from the reservation's remaining TTL.** Recorded as
   `IdempotencyKeyConflictError.retry_after_seconds: int = 1`
   (`varco_core/varco_core/exception/idempotency.py`), read by `ErrorMiddleware._service_error_response()`
   via `getattr(exc, "retry_after_seconds", None)` and emitted as the `Retry-After` response
   header only when present (`varco_fastapi/varco_fastapi/middleware/error.py`) — every other
   `ServiceException`, including out-of-tree subclasses predating this attribute, renders
   byte-identically. Seconds (not an HTTP-date) is simpler for a client to act on. A fixed value
   rather than the true remaining TTL was chosen because exposing the real remaining TTL would
   require widening `AbstractIdempotencyStore.reserve()`'s return type beyond the plain
   `ReserveOutcome` enum §D-D1-atomic deliberately keeps minimal — not justified for a single
   header's precision.
