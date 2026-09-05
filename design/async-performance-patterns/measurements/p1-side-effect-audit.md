# P1a — import-time side-effect audit for `varco_core`

**Plan 028 / Phase 0, Steps 1, 2 and 8.** This file is the *gating* artifact of the phase:
per §D-P1-sideeffects, no line of the lazy `varco_core/__init__.py` may be written before it
exists. It records what was swept, what was found, and the verdict for each finding.

Environment: Linux 6.18 (WSL2), CPython 3.12 via `uv run python`, working tree at
`3.1.0-plans`, commit `18c4ef0` (`feat(tls): add PKCS#12 ingestion, …`).

---

## 1. Sweep A — module-scope calls that are not `def`/`class`

§D-P1-sideeffects' first `rg` invocation, run verbatim:

```bash
rg -n "^[A-Za-z_][A-Za-z_0-9.]*\(" varco_core/varco_core --glob '*.py' \
  | rg -v "^\S+:\d+:(def|class) "
```

| Hit | Verdict |
|---|---|
| `tenancy/fanout.py:17` — `activated(tid)``/``on_tenant_deactivated(tid)` | **False positive.** A wrapped line *inside a module docstring*, not a statement. Harmless. |
| `profiling/backends/__init__.py:24` — `register_cpu_backend("cprofile", CProfileCpuBackend)` | **Real side effect, but out of P1's blast radius** — see §3. |
| `profiling/backends/__init__.py:25` — `register_memory_backend("tracemalloc", TracemallocMemoryBackend)` | Same as above. |

Three hits, two of them the same registration pair. Nothing else in `varco_core` performs a
module-scope call whose *purpose* is a side effect.

## 2. Sweep B — module-scope `CONSTANT = call()`

```bash
rg -n "^_?[A-Z_]+\s*=\s*\w+\(" varco_core/varco_core --glob '*.py'
```

Roughly ninety hits, every one of which falls into one of four harmless families. The
distinguishing property is that **all of them bind a value into their own module's namespace and
mutate nothing outside it** — they are constants, not registrations, so a lazily-imported module
producing them later is indistinguishable from producing them earlier.

| Family | Examples | Verdict |
|---|---|---|
| `TypeVar` / `ParamSpec` declarations | `D = TypeVar("D", bound=DomainModel)` (~70 hits across `service/*`, `cache/*`, `observability/*`, `resilience/*`) | Harmless — a local type variable. |
| Sentinel objects | `_UNSET = object()` (`event/consumer.py:138`, `tenancy/control/readiness.py:64`, `tenancy/control/consumer.py:93`), `_MISSING = object()` (`cache/decorator.py:124`) | Harmless — module-local identity sentinels. Identity is per-module and unaffected by *when* the module loads. |
| Frozen/immutable constants | `_SYSTEM_FIELDS = frozenset({...})` (`mapper.py:53`), `_DEV_SHM = Path("/dev/shm")` (`tls/pkcs12.py:71`), `_EMPTY_PLAN_SENTINEL = CapturePlan(...)` (`observability/params.py:488`) | Harmless — pure values, no I/O (`Path()` does not touch the filesystem). |
| Lazy metric handles | `_CACHE_HITS = _PatchableMetric(...)` and eight siblings (`observability/cache.py:98-133`) | Harmless — `_PatchableMetric` is a *deferred* handle by construction; it resolves the real OTel instrument on first use, precisely so import order does not matter. |
| Stateless singleton instance | `IDENTITY = IdentityClaimTransformer()` (`jwt/transform/protocol.py:86`) | Harmless — the object is stateless and is itself an `__all__` export, so it is reachable through `_LAZY`. |

## 3. Sweep C — module-scope decorator applications

Not in §D-P1-sideeffects' listed commands, added because a decorator *is* a module-scope call:

```bash
rg -n "^@" varco_core/varco_core --glob '*.py'    # 154 hits
```

All but a handful are `@dataclass(frozen=True)`, `@runtime_checkable`, `@asynccontextmanager`
and similar — pure class/function transformations with no external effect. The interesting
family is providify's `@Singleton(...)`:

- `event/memory.py:87`, `event/producer.py:146`, `event/dlq.py:524`, `event/serializer.py:116`,
  `event/deduplication.py:157`, `cache/memory.py:145`, `auth/authorizer.py:65`.

**Verdict: harmless, and specifically *not* an argument for `_EAGER`.** These bindings become
visible to a container through `container.scan("varco_core", recursive=True)`, and `scan()`
imports every module in the package tree itself — it has never relied on `import varco_core`
having pre-imported them. Every one of these classes is also an `__all__` export
(`InMemoryEventBus`, `BusEventProducer`, `InMemoryDeadLetterQueue`, `JsonEventSerializer`,
`InMemoryCache`, …), so it is reachable through `_LAZY` regardless.

## 4. `register_framework_metadata` — explicitly confirmed `varco_sa`-only

Required by Step 1 in as many words. `rg -l register_framework_metadata` over the whole tree
returns, excluding docs/plans and the `api-surface` snapshot, **only** `varco_sa` files:

```
varco_sa/varco_sa/metadata.py          # the definition (:55)
varco_sa/varco_sa/__init__.py
varco_sa/varco_sa/{audit,conversation,deduplication,dlq,encryption_store,inbox,
                   job_store,outbox,saga}.py
varco_sa/varco_sa/tenancy/models.py    # the call site cited by the plan (:53)
varco_sa/tests/test_framework_metadata.py
```

Zero hits under `varco_core/`. `varco_sa` imports its own modules eagerly from its own
`__init__.py`, which P1 does not touch. **Out of P1's blast radius, verified rather than
assumed.**

## 5. The `sys.modules` differential — the invariant that actually matters

The Risks section's invariant: *the set of modules imported after touching every `__all__` name
equals the set imported eagerly today.* Measured directly rather than reasoned about:

1. Parse today's eager `from varco_core.X import (...)` block with `ast` → the name→module map
   that becomes `_LAZY` (238 bindings; see §6).
2. Process A: `import varco_core`, snapshot `{m for m in sys.modules if m.startswith("varco_core")}`.
3. Process B: `importlib.import_module()` on every *distinct target module* of that map — i.e.
   exactly what touching every `__all__` name does — and take the same snapshot.

```
eager count: 175   reachable count: 175
EAGER-ONLY (side-effect suspects): []
REACHABLE-ONLY:                    []
```

**The set difference is empty in both directions.** No `varco_core` module is imported today
that is not reachable through an `__all__` name, so there is no module being imported for its
side effect. `_EAGER` is therefore **empty**, and the invariant holds by measurement.

Corroborating detail: `varco_core.profiling*` does **not** appear in `sys.modules` after
`import varco_core` today (`[m for m in sys.modules if "profiling" in m] == []`). The §1
`register_cpu_backend`/`register_memory_backend` pair is thus already lazy on `main` — P1
cannot regress it, because nothing about `import varco_core` ever triggered it.

## 6. Two names bound today that are *not* in `__all__`

The `ast` map yields 238 bindings against 235 `__all__` entries. The three extras:

| Name | Disposition |
|---|---|
| `annotations` | `from __future__ import annotations`. Not a name, an artifact of the parse. |
| `MigrationPlan` (`varco_core.migrator`) | Imported today with `# noqa: F401`, deliberately absent from `__all__` (the AB-2 collision note at `__init__.py:242-249`), and **asserted by an existing test** — `varco_core/tests/test_deprecated_aliases.py:174,184,196`. Must keep resolving. |
| `StepSpec` (`varco_core.migrator`) | Same shape, `# noqa: F401`, no test but equally an existing observable attribute. |

They cannot go in `_LAZY`/`_EAGER`: `test_lazy_init.py` asserts
`set(_LAZY) | set(_EAGER) == set(__all__)`, and `set(TYPE_CHECKING names) == set(__all__)`.
**Resolution:** a third, separately-named map `_LAZY_UNEXPORTED` consulted by the same
`__getattr__`. `__dir__()` still returns `sorted(__all__)`, exactly as today's behaviour — these
two names were never in `__all__` and so were never in `dir()`'s advertised surface either.

## 7. Step 2 — the starting measurement, reproduced

Methodology: `python -X importtime -c "import <target>"` in a fresh subprocess, summing the
**self** column across every line (the true total), best-of-5, minus a same-methodology
`import sys` baseline. This is what `scripts/import_budget.py` implements.

| Target | Total | Baseline | **Delta** |
|---|---|---|---|
| `varco_core` | 294.2 ms | 4.6 ms | **289.6 ms** |
| `varco_fastapi` | 442.6 ms | 4.6 ms | 438.0 ms |
| `varco_redis` | 337.1 ms | 4.6 ms | 332.5 ms |

`BACKLOG.md:53-57` recorded **419 ms against a 7 ms baseline** on the scout's machine. This
machine measures 290 ms against 4.6 ms — a faster box, same order of magnitude, same structural
shape (no hot leaf; the cost is the ~700-module eager graph). **The premise is unchanged**, so
per Step 2 the plan proceeds without re-reading.

## 8. Step 8 — after the conversion

Same methodology, same session, same machine (baseline re-measured at 4.0 ms in this run — the
run-to-run drift on the baseline itself is ~0.6 ms, which is why the metric is a *delta*).

| Target | Before (delta) | After (delta) | Change |
|---|---|---|---|
| `varco_core` | 289.6 ms | **6.6 ms** | **−98%** |
| `varco_fastapi` | 438.0 ms | 342.9 ms | −22% |
| `varco_redis` | 332.5 ms | 256.0 ms | −23% |

`varco_core` is the measured win the phase exists for: **289.6 ms → 6.6 ms**, and the four
measured contributors (`lark`, `jwt`, `psutil`, `opentelemetry.sdk`) are absent from a cold
`import varco_core` — asserted, not just measured, by `test_lazy_init.py`'s cold-set tests.

`varco_fastapi` and `varco_redis` are recorded as **observations for §D-P1-scope**, not as
targets of this plan. They improve by ~22% *for free*, because their own eager
`from varco_core import X` lines now pull only the submodules they actually name instead of the
whole framework. They remain expensive in absolute terms because each pulls its own dominant
third-party dependency (FastAPI/Starlette, `redis`) regardless — which is the data a future
"lazy `varco_fastapi`" row starts from, and it argues the remaining ceiling on such a row is low.

Post-conversion deltas for all ten distributions, which is what
`design/async-performance-patterns/measurements/import-budget.json` commits as `measured_ms`:

| Target | Delta |
|---|---|
| `varco_core` | 6.6 ms |
| `varco_ws` | 119.4 ms |
| `varco_kafka` | 143.9 ms |
| `varco_nats` | 207.9 ms |
| `varco_memcached` | 207.9 ms |
| `varco_redis` | 256.0 ms |
| `varco_beanie` | 260.5 ms |
| `varco_sa` | 308.9 ms |
| `varco_fastapi` | 342.9 ms |
| `varco_casbin` | 398.9 ms |

## 9. Verdict

- `_EAGER` is **empty**, on measured evidence (§5), and the `_EAGER` mechanism is retained in the
  file anyway so a future finding has a documented home.
- `_LAZY` covers all 235 `__all__` names; `_LAZY_UNEXPORTED` covers the two legacy non-`__all__`
  attributes (§6).
- No `varco_core` module is imported for a side effect.
- Step 7's rule stands: if `make test` ever fails after this conversion, the fix is a new
  `_EAGER` entry **and a new row in this file** — never a test edit.
