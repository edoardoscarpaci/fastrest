# Plan 014 — Event-bus settings binding shape + the shared `@Provider` annotation-patch helper (Audit 001, Batches C + D)

## Goal
Close the two remaining structural findings of `audits/001-audit-di-wiring.md` —
**F1** (three event-bus settings classes carry `@Singleton` on a pydantic `BaseSettings`
subclass, the pattern CLAUDE.md's own pitfall table and a sibling file in the same package
declare forbidden) and **F8** (the `factory.__annotations__["return"] = ...` workaround is
independently reimplemented six times across four packages with copy-pasted DESIGN blocks).

After this plan:
1. Every one of the three settings classes has a **characterization test that resolves it
   through a real `DIContainer` via `container.get()`** — not just `validate_bindings()` —
   pinning the actual behaviour before anything changes, plus a test proving
   `container.get(AbstractEventBus)` (the documented bootstrap call, which injects those
   settings) works.
2. One shared helper, `varco_core.providify_compat.provide_factory()`, replaces all six
   hand-rolled annotation-patch closures, with a per-call-site regression net proving
   byte-identical DI behaviour at each one.

## Non-goals
- **No other audit finding.** F2 (`_try_resolve_component`'s bare `except Exception`),
  F3 (`install_cache_metrics` lifecycle parity), F4 (`mount_reliability_admin` double-mount
  guard), F7 (`async_bootstrap` contract divergence) are separate batches, not requested.
  F5/F6/F9/F10 are already covered by `plans/013-refactor-di-wiring-docs-tests.md`.
- **No new DI capability.** `provide_factory()` is a *behaviour-preserving* extraction of
  code that already exists six times. It gains no features the six sites do not already use
  (no `qualifier=`, no `priority=`, no `async_=` — see Spot-check 3 for why `async_=` is
  provably unnecessary).
- **No providify version bump, no upstream patch.** The workaround stays a workaround; this
  plan only makes it deletable from one place instead of six.
- **No rename of the three settings classes**, no change to their fields, defaults, env
  prefixes, or `priority=-sys.maxsize`.
- **No change to the ImportError-guard contract** of `bind_websocket_adapter` /
  `bind_sse_adapter` / `bind_mcp_adapter` / `bind_skill_adapter` — "providify absent → log a
  warning and return without registering" must survive byte-identically, proven by the
  existing tests (`varco_ws/tests/test_di.py:351-383`, `:459-482`).
- **No deletion of `varco_core`'s existing providify imports.** See Spot-check 2 — the
  premise that `varco_core` is providify-free is false, and this plan does not try to make
  it true.

---

## Design

Two independent parts. Part A is a *characterization-then-decide* investigation with a
recommended landing; Part B is a mechanical extraction with a pre-built regression net.

```
Part A — F1 (Batch C)                       Part B — F8 (Batch D)
─────────────────────                       ────────────────────
kafka/config.py:113   @Singleton            varco_core/providify_compat.py   (NEW leaf)
nats/config.py:109    @Singleton      →       def provide_factory(container, factory,
redis/config.py:61    @Singleton                                *, returns, singleton=False,
      │                                                          name=None) -> None
      │ characterization test FIRST                     ▲
      ▼                                                 │ imported by
  container.get(XEventBusSettings)          varco_ws/di.py:271        _ws_factory
  container.get(AbstractEventBus)           varco_ws/di.py:381        _sse_factory
      │                                     varco_fastapi/router/mcp.py:850    _mcp_adapter_factory
      ▼                                     varco_fastapi/router/skill.py:1153 _skill_adapter_factory
  branch (a) passes → convert to @Provider  varco_sa/di.py:322        _repo_factory   (sync, DEPENDENT)
  branch (b) raises → conversion mandatory  varco_beanie/di.py:196    _repo_factory   (async, DEPENDENT)
                                            varco_fastapi/di.py:175   _factory        (7th site — bonus)
```

### Part A — F1: `@Singleton` on pydantic `BaseSettings`

The audit (`audits/001-audit-di-wiring.md:8-21`) records a genuine, live contradiction:

> `varco_kafka/varco_kafka/channel.py:132-146` — `kafka_channel_manager_settings()` is a
> `@Provider` factory whose docstring explains *why* `@Singleton` on this exact shape
> (`__init__(self, **values: Any)`) raises `LookupError: Cannot resolve 'values: typing.Any'`
> … i.e. `varco_kafka` documents the forbidden pattern in one file while using it in another.

Verified against current source — all three are exactly where the audit says:

| File:line | Decoration |
|---|---|
| `varco_kafka/varco_kafka/config.py:113-114` | `@Singleton(priority=-sys.maxsize)` on `class KafkaEventBusSettings(EventBusSettings)` |
| `varco_nats/varco_nats/config.py:109-110` | same, `NatsEventBusSettings` |
| `varco_redis/varco_redis/config.py:61-62` | same, `RedisEventBusSettings` |

And all three packages contain **both** patterns simultaneously, so the inconsistency is
intra-package, not cross-package:
- `varco_kafka/varco_kafka/channel.py:131` — `@Provider(singleton=True, priority=-sys.maxsize)` → `KafkaChannelManagerSettings`
- `varco_nats/varco_nats/channel.py:164` — `@Provider(singleton=True, priority=-sys.maxsize)` → `NatsChannelManagerSettings`
- `varco_redis/varco_redis/backplane.py:136` — `@Provider(singleton=True, priority=-sys.maxsize - 1)` → `RedisBackplaneSettings`

**The audit's own instruction, and the user's, is that a characterization test comes first**
(`audits/001-audit-di-wiring.md:127`):

> **Batch C (needs care — verify before touching):** F1 … write the missing regression test
> *first* to establish current actual behavior, then decide fix-vs-document; this is the one
> finding where the "intended" behavior is genuinely ambiguous from the code alone.

The gap the test closes is precise. `varco_redis/tests/test_redis_di.py:19-20` says outright:

> No Redis server is required: only container registration and annotation resolution are
> exercised — nothing is instantiated.

`validate_bindings()` resolves *annotations*; it never calls a constructor. So no test
anywhere proves the three classes can be **built** through the container.

#### The two branches, and which one to expect

Reading the installed providify (`.venv/lib/python3.12/site-packages/providify/`) gives a
strong, source-grounded prediction — recorded here so the implementer is not surprised, but
**the test still decides**, and the plan works either way.

- **`providify/_annotations.py:583-590`** — `resolve_params()` iterates
  `sig.parameters.items()` and does `continue` for `inspect.Parameter.VAR_POSITIONAL` and
  `inspect.Parameter.VAR_KEYWORD`. Pydantic's `BaseSettings.__init__(__pydantic_self__,
  ..., **values: Any)` puts `values` in exactly that skipped bucket.
- **`providify/_annotations.py:591-592`** — `if param_name not in raw: continue`.
  `__pydantic_self__` carries no annotation, so it is skipped too (line 584 only skips the
  literal names `"self"`/`"cls"`, which would otherwise have missed it).
- **`providify/container.py:1994-2000`** — the `LookupError: Cannot resolve '<name>: <hint>'`
  the CLAUDE.md pitfall row quotes fires only for a param that *reached* the loop with no
  default. `values` never reaches it.

⇒ **Branch (a) is the expected outcome**: `container.get(KafkaEventBusSettings)` succeeds
today on `providify>=1.1.0`. Not "accidentally, because every field is defaulted" (the
audit's hypothesis at line 19) but because providify's Phase-7 per-parameter resolver skips
`**kwargs` outright. The `channel.py:137-147` docstring and CLAUDE.md's pitfall row describe
a **historical** providify behaviour that has since been fixed.

That reclassifies F1 but does not dismiss it. Under branch (a) the residual problems are:
1. Three classes use a shape the project's own written rule forbids, while their siblings
   three functions away use the sanctioned one — a contributor cannot tell which is correct.
2. `KafkaEventBus.__init__(config: Inject[KafkaEventBusSettings], ...)`
   (`varco_kafka/varco_kafka/bus.py:167-169`) makes those settings load-bearing for the
   documented happy path `bus = await container.aget(AbstractEventBus)`
   (`varco_redis/varco_redis/di.py:29`). If a future providify ever reverts the VAR_KEYWORD
   skip, all three event buses stop resolving — with no test to catch it.
3. `varco_redis/varco_redis/di.py:6-12` asserts in prose that the `@Singleton`-on-
   `BaseSettings` shape is the intended design, directly contradicting CLAUDE.md.

⚠️ **The audit's suggested follow-up sub-test does not work and must not be written.**
`audits/001-audit-di-wiring.md:19` predicts "a contributor adding one *required* field …
would silently break, reproducing the `LookupError`". A required pydantic field is **not** a
constructor parameter — pydantic collects it through `**values`, which providify skips. The
real failure for a required field is a pydantic `ValidationError` at construction
(well-diagnosed, not a DI mystery), and it is identical under `@Singleton` and `@Provider`.
Step 5 pins that fact instead of chasing the predicted-but-nonexistent `LookupError`.

- **Branch (b)** — the test raises `LookupError`. Then three event buses are unresolvable
  today on the documented bootstrap path, conversion is mandatory and urgent, and Steps 6-8
  land unchanged (only the plan's severity framing changes).

**Recommendation, per the audit's own "Suggested direction" (line 20) and the user's
instruction: convert all three to `@Provider` regardless of branch.** It is mechanical, it
is already the majority pattern in the same three packages, it deletes the contradiction at
the source rather than papering over it with a comment, and Step 3's `container.get()` test
becomes the permanent proof it still resolves.

#### Conversion shape (identical in all three files)

```python
# BEFORE — varco_redis/varco_redis/config.py:61-62
@Singleton(priority=-sys.maxsize)
class RedisEventBusSettings(EventBusSettings):
    ...

# AFTER
class RedisEventBusSettings(EventBusSettings):
    ...


@Provider(singleton=True, priority=-sys.maxsize)
def redis_event_bus_settings() -> RedisEventBusSettings:
    """..."""   # docstring modelled on varco_kafka/channel.py:133-155
    return RedisEventBusSettings()
```

Three invariants the conversion must preserve, each with a Step that asserts it:
- **`priority=-sys.maxsize` exactly** — not `-sys.maxsize - 1`. Changing it would reorder
  these defaults relative to every other framework default (Step 7).
- **`container.get(EventBusSettings)` still resolves.** Both `ClassBinding` and
  `ProviderBinding` register under one `interface`, and `DIContainer._filter()`
  (`providify/container.py:940-958`) matches "bindings whose interface is a subclass of
  *cls*" — so a base-class lookup keeps working. Asserted, not assumed (Step 7).
- **Nothing introspects `binding.implementation` for these classes.** `ProviderBinding` has
  no `.implementation` attribute. `test_redis_di.py:53-56` already uses
  `getattr(b, "implementation", None)` with a default and is safe; Step 6 greps for any
  site that is not.

### Part B — F8: one shared annotation-patch helper

Audit `audits/001-audit-di-wiring.md:91-98`. The duplicated shape is:

```python
@Provider(singleton=True)
def _factory() -> SomeType:      # ← string annotation under PEP 563
    ...
_factory.__annotations__["return"] = SomeType   # ← the workaround
container.provide(_factory)
```

Verified inventory — the audit names 5, current source has **7** (two the audit missed):

| # | Site | Scope | Deps arrive via | `returns=` value | Sets `__name__` | Guarded import |
|---|---|---|---|---|---|---|
| 1 | `varco_ws/varco_ws/di.py:271` `_ws_factory` | singleton | closure over `container` | plain class | no | yes |
| 2 | `varco_ws/varco_ws/di.py:381` `_sse_factory` | singleton | closure | plain class | no | yes |
| 3 | `varco_fastapi/varco_fastapi/router/mcp.py:850` `_mcp_adapter_factory` | singleton | closure | plain class | no | yes |
| 4 | `varco_fastapi/varco_fastapi/router/skill.py:1153` `_skill_adapter_factory` | singleton | closure | plain class | no | yes |
| 5 | `varco_sa/varco_sa/di.py:322` `_repo_factory` | DEPENDENT | **injected param** | generic alias `AsyncRepository[E]` | yes | no (module scope) |
| 6 | `varco_beanie/varco_beanie/di.py:196` `_repo_factory` | DEPENDENT, **async** | injected param | generic alias | yes | no (module scope) |
| 7 | `varco_fastapi/varco_fastapi/di.py:175` `_factory` (`bind_clients`) | singleton | default-arg capture | generic alias `AsyncVarcoClient[R]` | no | no (module scope) |

Sites 4 and 7 are not in the audit's list; site 7 additionally carries a **factually wrong**
DESIGN comment that the extraction lets us delete (see Spot-check 1).

#### The helper

New leaf module `varco_core/varco_core/providify_compat.py`:

```python
def provide_factory(
    container: Any,
    factory: Callable[..., Any],
    *,
    returns: Any,
    singleton: bool = False,
    name: str | None = None,
) -> None:
    """Patch *factory*'s return annotation, stamp @Provider, and register it."""
```

Behaviour, in this exact order (the one canonical ordering, replacing the two contradictory
orderings in use today):
1. `factory.__annotations__["return"] = returns`
2. `if name is not None: factory.__name__ = name`
3. `container.provide(Provider(singleton=singleton)(factory))`

**Why this ordering is safe for every site** — `providify/decorator/scope.py:538-566`:
`Provider`'s decorator body only calls `_set_provider_metadata(fn, ProviderMetadata(...))`
and `return fn`. It never reads `__annotations__`. The return annotation is read exactly
once, later, in `ProviderBinding.__init__` (`providify/binding.py:496-505`), which
`container.provide()` calls (`providify/container.py:672`). So patch-before-`provide()` is
the only real constraint; decorator order is irrelevant.

#### Where the helper lives — `varco_core`, and why the audit's blocker is not real

The audit hedges (line 95, line 97):

> note `varco_core` is otherwise providify-free by design, per
> `varco_fastapi/varco_fastapi/di.py:96-98`, so this would need to land in `varco_fastapi`
> or a new tiny leaf module instead

**This premise is false** (Spot-check 2 below). `varco_core` hard-depends on
`providify>=1.1.0` (`varco_core/pyproject.toml:28`) and imports it at module scope in 16
files. `varco_core` is therefore the only import-graph node all four affected packages
already reach:

```
varco_sa ─┐
varco_beanie ─┤
varco_ws ─┼──→ varco_core  (hard-deps providify)     ← helper lives here
varco_fastapi ─┘
      ▲
      └── varco_sa / varco_beanie / varco_ws do NOT depend on varco_fastapi,
          so varco_fastapi.di is not a reachable home for three of the four.
```

Module name `providify_compat` (the audit's own floated name, line 97) over `di.py`: this is
a compat shim for a specific providify limitation, intended to be **deleted** when providify
resolves PEP-563 annotations on closures. Naming it `varco_core.di` would advertise a
package-level DI entry point (`bootstrap()`, bindings) that it deliberately does not have.
It is **not** re-exported from `varco_core/__init__.py`, for the same reason — a symbol whose
whole purpose is to be deletable should not enter the top-level public namespace.

It declares **no** `@Provider`/`@Singleton` at module scope, so `container.scan("varco_core")`
registers nothing new (Step 16 asserts this).

#### Preserving the ImportError guard at sites 1-4

Sites 1-4 currently probe providify's presence with a guarded
`from providify import Provider`; if that raises, they log a warning and return without
registering. After extraction they no longer reference `Provider` directly, so the probe
must be rewritten without leaving an unused import:

```python
try:
    import providify  # noqa: F401 — presence probe; provide_factory needs it
except ImportError:
    logging.getLogger(__name__).warning(
        "bind_websocket_adapter: providify not installed — "
        "WebSocketEventBus not registered in DI."
    )
    return

from varco_core.providify_compat import provide_factory  # noqa: PLC0415
```

`import providify` triggers `__import__("providify")`, which is exactly what
`varco_ws/tests/test_di.py:365-368` blocks — so the existing guard tests keep passing
unmodified, and they are the acceptance criterion for this step. Deliberately **not**
relying on `from varco_core.providify_compat import ...` to raise: that only happens because
the test's `sys.modules` purge filter (`if "providify" in k`, `test_di.py:371`) incidentally
matches the new module's name, which is a coincidence, not a contract.

Sites 5, 6, 7 already import providify at module scope with no guard
(`varco_sa/varco_sa/di.py:59`, `varco_beanie/varco_beanie/di.py:52`) — they import the
helper at module scope too, no guard added, no guard removed.

### Alternatives considered

**Part A**

- **Document instead of convert** (audit F1 option (a), stop after the characterization
  test): ✅ zero production change, zero risk; the test alone closes the "untested
  assumption presented as documented fact" smell. ❌ leaves three classes contradicting
  CLAUDE.md's pitfall table and their own package siblings, so the next contributor still
  cannot tell which shape is right; and it leaves the app-visible bootstrap path
  (`aget(AbstractEventBus)` → `Inject[KafkaEventBusSettings]`) depending on a providify
  implementation detail (the VAR_KEYWORD skip) that nothing pins. **Rejected** — but Step 4
  keeps the option open by making the decision an explicit, recorded checkpoint.
- **Delete the CLAUDE.md pitfall row instead** (i.e. declare `@Singleton`-on-`BaseSettings`
  fine now that providify skips `**kwargs`): ✅ one-line docs change, no code churn.
  ❌ inverts a security-of-startup default on an undocumented internal of a third-party
  library, and would require *un*-converting `KafkaChannelManagerSettings`,
  `NatsChannelManagerSettings`, `RedisBackplaneSettings`, `CasbinSettings`,
  `MigrationSettings`, … to be self-consistent. **Rejected.**
- **Keep `@Singleton` and add a `__init__` shim** to the three classes: ✅ no binding-shape
  change. ❌ fights pydantic's constructor for no gain. **Rejected.**

**Part B**

- **Helper in `varco_fastapi.di`** (the audit's first suggestion, line 97): ✅ `varco_fastapi`
  already owns two of the seven sites. ❌ `varco_sa`, `varco_beanie` and `varco_ws` do not
  depend on `varco_fastapi`, so three of the four packages could not import it — this is the
  concrete blocker the audit flagged as unresolved. **Rejected.**
- **A new workspace package (`varco_di`)**: ✅ perfectly neutral home. ❌ an eleventh
  publishable package, a new `pyproject.toml`, a new PyPI name, a new dependency edge in
  four packages — for one ~15-line function. **Rejected** (CLAUDE.md: "Do NOT create a new
  backend/package just for convenience").
- **Accept the duplication** (the audit's own fallback: "duplicating the helper is genuinely
  the pragmatic answer if there's no shared dependency home"): ✅ zero blast radius.
  ❌ the shared home *does* exist (Spot-check 2), and the duplication's cost is already
  concrete — `varco_sa/varco_sa/di.py:293-297` and `varco_beanie/varco_beanie/di.py:167-172`
  are word-for-word identical except "sync"/"async". **Rejected.**
- **`_provide_singleton(container, factory, *, returns=, async_=False)`** (the audit's
  literal proposed signature, line 95): ✅ matches the audit. ❌ two defects — the name bakes
  in `singleton`, but sites 5/6 are DEPENDENT-scoped, and `async_=` is dead weight because
  `@Provider` already detects async itself via `inspect.iscoroutinefunction(fn)` at
  decoration time (`providify/decorator/scope.py:549-551`, `:559-561`) and
  `ProviderBinding.__init__` re-detects it at registration
  (`providify/binding.py:510-513`). **Rejected** in favour of
  `provide_factory(..., singleton=False, name=None)`.
- **Do Part A and Part B as two plans**: ✅ smaller units. ❌ they touch overlapping files
  (`varco_redis`/`varco_kafka`/`varco_nats` DI tests, and the same `validate_bindings()`
  safety net) and the audit itself batches both as "structural, verify before touching".
  Kept together, but sequenced: Part A fully lands before Part B starts.

---

### Spot-check findings (recorded so the implementer isn't surprised)

**1. `varco_fastapi/varco_fastapi/di.py:171-174`'s DESIGN comment is factually wrong.**
It reads:

> Patch the return annotation BEFORE decorating: `@Provider` reads the annotation to derive
> the binding interface, so patching afterwards would register under the placeholder `Any`
> instead of the alias.

`@Provider`'s decorator body (`providify/decorator/scope.py:538-566`) sets metadata and
returns `fn` — it never touches `__annotations__`. The interface is derived in
`ProviderBinding.__init__` (`providify/binding.py:496-505`), i.e. at `container.provide()`.
This is why sites 1-4 patch *after* decorating and work fine. The comment must be corrected
or deleted as part of Step 14, not carried into the helper's docstring.

**2. `varco_core` is not providify-free.** The audit's F8 blocker cites
`varco_fastapi/varco_fastapi/di.py:96-98`, which actually reads *"varco_core must stay
providify-free — apply scope decorators here, once, at module import time"* — a comment about
**not decorating two specific `varco_core` classes** (`DefaultTaskSerializer`, `TaskRegistry`)
from inside `varco_core`, not about the package's dependencies. It is contradicted by
`varco_core/pyproject.toml:28` (`"providify>=1.1.0"`, a hard runtime dependency) and by 22
module-scope `from providify import ...` statements across 16 `varco_core` files, including
`event/producer.py:65`, `event/memory.py:66`, `event/dlq.py:69`, `cache/memory.py:33`,
`lock.py:96`, `service/base.py:163`, `observability/di.py:124`. Separately, the "circular
import" DESIGN note the audit points at in `varco_sa/varco_sa/di.py` (lines 121-123) is about
`providers.py → service.base → service/__init__ → service/tenant → providers.py` — nothing to
do with providify. **The helper can live in `varco_core` with no new dependency edge.**

**3. `async_=` is unnecessary** — see the last Part-B alternative above.

**4. The precedent for Part A already exists in-tree, with the exact test shape.**
`varco_kafka/tests/test_kafka_di.py:1-63` documents a settings class that *was* `@Singleton`,
*did* raise `LookupError: Cannot resolve 'values: typing.Any'`, and was converted to
`@Provider` — with tests for resolution, singleton identity, and app-override-wins. Steps 3
and 7 copy that file's structure rather than inventing one.

**5. Plan 013 already added part of the safety net.** `varco_sa/tests/test_sa_di.py` (new,
untracked) and the extended `varco_beanie/tests/test_beanie_di.py` land F9's
`scan + validate_bindings()` coverage for the two packages Part B touches. Do not duplicate
them; Step 11 extends them with `container.get()`-level assertions for the repo providers.

---

## Characterization result

Recorded 2026-08-23, after landing Steps 1-3 and 5-7.

**Branch (a) held** — all characterization tests pass under `@Singleton`,
confirming providify >= 1.1.0's `VAR_KEYWORD`-skip prediction
(`providify/_annotations.py:583-592`):

```
uv run pytest varco_kafka/tests/test_kafka_di.py -v   →  11 passed
uv run pytest varco_nats/tests/test_nats_di.py -v      →  11 passed
uv run pytest varco_redis/tests/test_redis_di.py -v    →   9 passed
```

`container.get(KafkaEventBusSettings|NatsEventBusSettings|RedisEventBusSettings)`
resolves and is a singleton on `@Singleton` today; `container.get(AbstractEventBus,
qualifier=...)` also resolves with the injected settings identity intact.
Step 5's required-field test confirms `pydantic.ValidationError` is raised, never
`LookupError`, correcting the audit's prediction.

One real-world surprise not anticipated by the plan text: `KafkaEventBus`/
`NatsEventBus`/`RedisEventBus`'s `@PostConstruct async def start()` genuinely
opens a network connection (not a no-op), and providify's synchronous
`container.get()` refuses to run an async `@PostConstruct` at all
(`RuntimeError: ... use await container.aget()`). The load-bearing
"event bus resolves with injected settings" test therefore stubs `start()` to a
no-op via `monkeypatch` and resolves through `await container.aget(...)` (the
documented bootstrap call) instead of a bare `container.get()` — no Docker
broker is required either way, and the DI-wiring assertion (constructed with
the right injected settings instance) is unaffected by which resolution call is
used.

**Decision**: proceed with Step 5, then convert all three classes to `@Provider`
(Steps 6-8) per the plan's recommendation — a consistency/robustness fix, not a
bug fix (branch (a) held). Step 6's grep for bare `binding.implementation` over
the full binding list in `varco_kafka`/`varco_nats`/`varco_redis` returned no
hits — every existing site already uses `getattr(b, "implementation", None)`.

---

## Steps

Steps 1-8 are Part A (F1) and must fully land — including the Step 4 decision — before Step 9
begins. Every step is independently runnable.

### Part A — F1

1. [x] `varco_kafka/tests/test_kafka_di.py` — **characterization test, no production change.**
   Add `class TestKafkaEventBusSettingsCharacterization` with
   `test_characterization_settings_resolve_through_the_container()`:
   `DIContainer()` → `container.scan("varco_kafka", recursive=True)` →
   `settings = container.get(KafkaEventBusSettings)` → assert
   `isinstance(settings, KafkaEventBusSettings)` and `settings.bootstrap_servers`.
   Docstring must state this is a characterization test pinning **current** behaviour
   (audit F1, `audits/001-audit-di-wiring.md:19`: no test anywhere calls
   `container.get(KafkaEventBusSettings)`), and that
   `validate_bindings()` cannot cover it because it resolves annotations without
   constructing. Add a sibling `test_characterization_settings_are_a_singleton()`
   (`container.get(...) is container.get(...)`).

2. [x] `varco_nats/tests/test_nats_di.py`, `varco_redis/tests/test_redis_di.py` — same
   characterization pair for `NatsEventBusSettings` / `RedisEventBusSettings`
   (assert on `servers` / `url` respectively). Keep the existing
   `validate_bindings()` classes untouched.

3. [x] `varco_kafka/tests/test_kafka_di.py`, `varco_nats/tests/test_nats_di.py`,
   `varco_redis/tests/test_redis_di.py` — **the load-bearing characterization test.**
   `test_characterization_event_bus_resolves_with_injected_settings()`: scan, then
   `bus = container.get(AbstractEventBus, qualifier="kafka")` (`"nats"` / `"redis"`), assert
   `isinstance(bus, KafkaEventBus)` and `bus._config` is the settings instance. Grounding:
   `varco_kafka/varco_kafka/bus.py:167-169` declares
   `config: Inject[KafkaEventBusSettings]` with no default, so if the settings binding
   cannot be constructed the documented bootstrap call
   (`bus = await container.aget(AbstractEventBus)`, `varco_redis/varco_redis/di.py:29`) is
   broken too. No broker connection is opened — construction only; do **not** call
   `bus.start()` or resolve anything with a connecting `@PostConstruct`
   (`test_kafka_di.py:65-70` documents that rule).

4. [x] **Decision checkpoint — run Steps 1-3 and record the result in this file** under a new
   `## Characterization result` heading (date + `uv run pytest` output summary + which
   branch held). Do not proceed until written down.
   - **Branch (a) — all pass** (expected; grounded in
     `providify/_annotations.py:583-592`'s VAR_KEYWORD skip). Continue to Step 5. The
     conversion in Steps 6-8 is a consistency/robustness fix, not a bug fix; note that in
     the commit message.
   - **Branch (b) — any raise `LookupError`** — three event buses are unresolvable on the
     documented bootstrap path. Skip Step 5 (nothing to pin about a working state), mark
     Steps 6-8 as a bug fix, and add one line to `CHANGELOG.md` under Fixed.

5. [x] *(Branch (a) only)* `varco_kafka/tests/test_kafka_di.py` — pin what a **required**
   field actually does, correcting the audit's prediction.
   `test_characterization_required_field_raises_validation_error_not_lookup_error()`:
   define a module-scope `class _RequiredFieldSettings(EventBusSettings)` with one
   non-defaulted field, decorate `@Singleton(priority=-sys.maxsize)`, register it on a fresh
   `DIContainer` via `container.bind(...)`/`scan` of the test module, and assert
   `container.get(...)` raises `pydantic.ValidationError` — **not** `LookupError`.
   Docstring must state explicitly that `audits/001-audit-di-wiring.md:19`'s predicted
   `LookupError` reproduction does not occur, and why (a pydantic field is not a constructor
   parameter; providify skips `**values`).

6. [x] Pre-conversion safety grep — no code change. Search the whole tree for anything
   introspecting these three classes as a `ClassBinding`:
   `rg -n "implementation" varco_kafka varco_nats varco_redis --glob '*.py'` and confirm
   every hit uses `getattr(b, "implementation", None)` with a default (as
   `varco_redis/tests/test_redis_di.py:53-56` does). Any bare `b.implementation` over the
   full binding list would `AttributeError` once a `ProviderBinding` is present. Record the
   result in the Step 4 section.

7. [x] `varco_kafka/tests/test_kafka_di.py`, `varco_nats/tests/test_nats_di.py`,
   `varco_redis/tests/test_redis_di.py` — **failing tests for the converted shape**, written
   before Step 8. Per package, modelled on `test_kafka_di.py:57-63`:
   - `test_app_supplied_settings_win_over_the_default()` — a module-scope
     `@Provider(singleton=True, priority=100)` override, then assert the override's value is
     what `container.get(XEventBusSettings)` returns (proves `priority=-sys.maxsize` is
     preserved and is still the lowest).
   - `test_settings_resolve_through_their_base_interface()` —
     `isinstance(container.get(EventBusSettings), XEventBusSettings)` (proves the
     `ClassBinding` → `ProviderBinding` swap does not drop base-class lookup;
     `providify/container.py:940-958`).
   These pass under `@Singleton` too — that is intended: they are the invariants the
   conversion must not break, so they must be green before *and* after Step 8.

8. [x] `varco_kafka/varco_kafka/config.py:113`, `varco_nats/varco_nats/config.py:109`,
   `varco_redis/varco_redis/config.py:61` — **the conversion.** Remove the class-level
   `@Singleton(priority=-sys.maxsize)`; add a module-scope
   `@Provider(singleton=True, priority=-sys.maxsize)` factory immediately after each class
   (`kafka_event_bus_settings()` / `nats_event_bus_settings()` /
   `redis_event_bus_settings()`, matching `kafka_channel_manager_settings`'s naming). Each
   factory's docstring reuses the DESIGN block at
   `varco_kafka/varco_kafka/channel.py:137-155` — with one **added, corrected** sentence:
   the `LookupError` it describes is historical (providify < 1.1.0); on current providify
   `**values` is skipped (`providify/_annotations.py:586-590`) and the reason to prefer
   `@Provider` is the project's own documented rule plus consistency with the sibling
   settings factories in the same package. Fix the import lines (`Singleton` → `Provider`,
   or both where still needed). Steps 1-3 and 7 must stay green.

9. [x] `varco_redis/varco_redis/di.py:6-12` — correct the module docstring's claim that
   settings "carry `@Singleton`". Audit F1 cites this prose specifically
   (`audits/001-audit-di-wiring.md:12`). Same one-line check in
   `varco_kafka/varco_kafka/di.py` and `varco_nats/varco_nats/di.py` if they carry the same
   claim.

10. [x] `CLAUDE.md` — under the existing pitfall row **"`@Singleton` on pydantic
    `BaseSettings`"**, append the version nuance discovered in Step 4: the `LookupError` is
    the pre-`providify` 1.1.0 symptom; on ≥1.1.0 `**values` is skipped and the shape
    *appears* to work, which is exactly why the rule is still enforced (the sanctioned shape
    must not depend on a third-party implementation detail). Cross-reference this plan.
    Also add a `CHANGELOG.md` entry.

### Part B — F8

11. [x] `varco_sa/tests/test_sa_di.py`, `varco_beanie/tests/test_beanie_di.py` —
    **characterization tests for sites 5 and 6, before touching them.** Extend the plan-013
    files with, per package: `bind_repositories(container, _User, _Post)` against a **real**
    `DIContainer` (not the existing `MagicMock`), then assert
    `container.get(AsyncRepository[_User])` / `await container.aget(AsyncRepository[_User])`
    resolves, that it is **not** the same object as `AsyncRepository[_Post]`'s, and that
    two resolutions of the same alias return **different** instances (pinning
    `Scope.DEPENDENT` — `varco_sa/varco_sa/di.py:327-329` /
    `varco_beanie/varco_beanie/di.py:201-203` deliberately do not pass `singleton=True`).
    Keep `varco_beanie/tests/test_beanie_di.py:212-217`'s existing `__annotations__` asserts
    — they are exactly the invariant the extraction must preserve.

12. [x] `varco_fastapi/tests/milestone_f/test_mcp_adapter.py`,
    `varco_fastapi/tests/milestone_f/test_skill_adapter_async.py` — **characterization tests
    for sites 3 and 4.** Today `test_mcp_adapter.py:445-456` only covers the *providify-absent*
    path; nothing resolves `MCPAdapter`/`SkillAdapter` from a real container. Add, per file:
    `DIContainer()` → `bind_mcp_adapter(container, SomeRouter, ...)` →
    `adapter = container.get(MCPAdapter)` → assert it is an `MCPAdapter` for the right router,
    and that a second `container.get()` returns the **same** object (pinning
    `singleton=True`). Same for `bind_skill_adapter` / `SkillAdapter`.
    `varco_ws/tests/test_di.py:267-330` is the template; `varco_ws` sites 1-2 already have
    this coverage and need nothing new.

13. [x] `varco_fastapi/tests/test_bind_clients.py` — **characterization test for site 7.**
    Assert `bind_clients(real_container, SomeClient)` then
    `container.get(AsyncVarcoClient[SomeRouter])` resolves and is a singleton, and that two
    different client classes register under two distinct generic aliases.

14. [x] `varco_core/varco_core/providify_compat.py` (**new**) — the helper. Module docstring
    states: (a) what the PEP-563 workaround is and why it exists; (b) that
    `@Provider` never reads `__annotations__` — the interface is read at
    `container.provide()` → `ProviderBinding.__init__` (`providify/binding.py:496-505`) — so
    the only ordering constraint is patch-before-`provide()`; (c) that this module is a
    **compat shim, deletable in one place** when providify resolves closure annotations
    natively; (d) that it declares no bindings and is invisible to `container.scan`.
    Function `provide_factory(container, factory, *, returns, singleton=False, name=None)`
    with a full `Args:`/`Returns:`/`Raises:`/`Edge cases:` docstring and a `DESIGN:` block
    with ✅/❌ (✅ one place to change; ✅ no `async_=` needed — `@Provider` detects async at
    `providify/decorator/scope.py:549-551`; ❌ still invisible to mypy/pyright, unchanged from
    today). Do **not** re-export from `varco_core/__init__.py`.

15. [x] `varco_core/tests/test_providify_compat.py` (**new**) — unit tests for the helper in
    isolation: registers under a plain class; registers under a generic alias
    (`AsyncRepository[X]`-shaped); `singleton=True` → same instance twice; `singleton=False`
    → different instances; `name=` sets `__name__`; an `async def` factory resolves via
    `await container.aget(...)` with no extra argument; a factory whose deps come from an
    injected parameter still gets them injected; calling it twice with two different
    `returns=` produces two independent bindings (the closure-capture bug the sites guard
    against).

16. [x] `varco_core/tests/test_providify_compat.py` — `test_module_registers_no_bindings()`:
    `DIContainer()` → `container.scan("varco_core.providify_compat")` → assert
    `container._bindings` gained nothing. Guards the "helper module must not become a
    scanned binding source" invariant.

17. [x] `varco_ws/varco_ws/di.py:248-273` — replace `_ws_factory`'s patch+provide block with
    `provide_factory(container, _ws_factory, returns=WebSocketEventBus, singleton=True)`.
    Rewrite the guarded import to the `import providify  # noqa: F401` presence-probe shape
    documented in the Design section, and lazily import the helper below it. Replace the
    copy-pasted DESIGN comment (lines 250-258, 268-270) with one sentence pointing at
    `varco_core.providify_compat`. `varco_ws/tests/test_di.py` must pass **unmodified** —
    including `test_bind_websocket_adapter_importerror_guard` (`:351-383`).

18. [x] `varco_ws/varco_ws/di.py:362-383` — same for `_sse_factory` /
    `provide_factory(..., returns=SSEEventBus, singleton=True)`. `test_di.py:459-482`
    unmodified.

19. [x] `varco_fastapi/varco_fastapi/router/mcp.py:830-852` — same for
    `_mcp_adapter_factory` / `returns=MCPAdapter, singleton=True`.
    `test_mcp_adapter.py:445-456` unmodified.

20. [x] `varco_fastapi/varco_fastapi/router/skill.py:1096-1154` — same for
    `_skill_adapter_factory` / `returns=SkillAdapter, singleton=True`.

21. [x] `varco_sa/varco_sa/di.py:285-329` — `_make_repo_provider` no longer returns a stamped
    function; it becomes a `_bind_repo_provider(container, entity_cls)` that calls
    `provide_factory(container, _repo_factory, returns=AsyncRepository[entity_cls],
    singleton=False, name=f"_repo_factory_{entity_cls.__name__}")`, and
    `bind_repositories` (line 281-282) calls it instead of
    `container.provide(_make_repo_provider(...))`. Helper imported at module scope (no guard
    — `varco_sa/varco_sa/di.py:59` already imports providify unguarded). Delete the
    duplicated DESIGN block at lines 293-297; point at `varco_core.providify_compat`.
    ⚠️ If any test imports `_make_repo_provider` directly, keep it as a thin
    `@Provider`-stamping wrapper so the test keeps working, or update the test in the same
    step — check before editing.

22. [x] `varco_beanie/varco_beanie/di.py:160-203` — the async mirror of Step 21. **No
    `async_=` argument** — `@Provider` detects the coroutine function itself.
    `varco_beanie/tests/test_beanie_di.py:212-220`'s `__annotations__` and `__name__`
    assertions must pass unmodified (this is the byte-identical-behaviour proof).

23. [x] `varco_fastapi/varco_fastapi/di.py:160-192` (site 7) — same extraction for
    `bind_clients`'s `_factory`, `returns=client_alias, singleton=True`. **Delete the
    factually wrong DESIGN comment at lines 171-174** (Spot-check 1) — the helper's docstring
    now carries the correct explanation. Keep lines 177-191's DESIGN block about *not* having
    a fallback chain; that one is accurate and still applies.

24. [x] `CLAUDE.md` — one row in the "DI wiring verb taxonomy" table's surrounding prose (the
    section plan 013 adds) or a short note under it: framework code registering a dynamically
    typed binding uses `varco_core.providify_compat.provide_factory()`, never a hand-rolled
    `__annotations__["return"]` patch. Add a `CHANGELOG.md` entry under Changed (internal
    refactor, no public API change).

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `container.get(KafkaEventBusSettings)` after Step 8 | returns a `KafkaEventBusSettings` built from `VARCO_KAFKA_*` env vars; same object on a second call |
| `container.get(EventBusSettings)` after Step 8 | still resolves to the concrete subclass — `_filter()` matches on subclass-of-interface (`providify/container.py:940-958`) |
| App registers `@Provider(priority=100) -> RedisEventBusSettings` | app binding wins; framework default stays at `priority=-sys.maxsize` |
| A settings subclass gains a **required** field | `pydantic.ValidationError` at construction — **not** `LookupError`; identical under `@Singleton` and `@Provider` (Step 5) |
| Code doing bare `binding.implementation` over all bindings in the three packages | would `AttributeError` after Step 8 — Step 6 proves no such site exists |
| providify absent, `bind_websocket_adapter()` called | one WARNING logged, returns, `container.provide` never called — unchanged (Step 17) |
| providify absent, `varco_ws.di` merely imported | must still import cleanly — the helper import is lazy, inside the guarded branch |
| `provide_factory(..., returns=AsyncRepository[User])` and `(..., returns=AsyncRepository[Post])` | two independent bindings; neither shadows the other (Step 15) |
| `provide_factory()` with an `async def` factory | `ProviderBinding.is_async` is `True`; resolves via `await container.aget(...)`, raises the usual "use `awarm_up()`" `RuntimeError` from `container.warm_up()` — unchanged |
| `provide_factory(..., singleton=False)` | `Scope.DEPENDENT`; a new instance per resolution — the current repo-provider behaviour |
| `provide_factory()` called twice with the same factory object | second `container.provide()` appends a second binding at equal priority; first-registered wins, matching `container.provide()`'s documented tie-break — no dedup added |
| `container.scan("varco_core")` after Step 14 | registers nothing new from `providify_compat` (Step 16) |

---

## Verification

```bash
# Part A — characterization first (Steps 1-5), must run and be recorded before Step 8
uv run pytest varco_kafka/tests/test_kafka_di.py -v
uv run pytest varco_nats/tests/test_nats_di.py -v
uv run pytest varco_redis/tests/test_redis_di.py -v

# Part A — full package suites after the conversion (Step 8)
uv run pytest varco_kafka/tests/ varco_nats/tests/ varco_redis/tests/

# Part B — helper unit tests
uv run pytest varco_core/tests/test_providify_compat.py -v

# Part B — the per-call-site regression net (audit F8 "Risk of fixing": the existing
# validate_bindings() suites in each package are the safety net; run all four)
uv run pytest varco_ws/tests/test_di.py -v
uv run pytest varco_sa/tests/test_sa_di.py varco_beanie/tests/test_beanie_di.py -v
uv run pytest varco_fastapi/tests/test_bind_clients.py \
              varco_fastapi/tests/milestone_f/test_mcp_adapter.py \
              varco_fastapi/tests/milestone_f/test_skill_adapter_async.py -v

# Whole-workspace regression + gates
make test
make lint
make type-check
```

Acceptance:
- Steps 17-23 must be landed with the pre-existing tests in each touched file **unmodified**
  (except Step 21's explicitly-flagged `_make_repo_provider` import case). A test that had to
  change to accommodate the extraction means the extraction was not behaviour-preserving.
- `rg -n '__annotations__\["return"\]' varco_*/varco_*/` returns only
  `varco_fastapi/varco_fastapi/router/base.py` (FastAPI `response_model` synthesis — a
  different mechanism, deliberately out of scope) and
  `varco_core/varco_core/providify_compat.py`.

## Completion note (2026-08-23)

All 24 steps landed. One deliberate, documented deviation from the acceptance grep above:
`varco_beanie/varco_beanie/di.py`'s `_make_repo_provider()` (site 6) still contains a live
`_repo_factory.__annotations__["return"] = ...` assignment — it was **not** routed through
`provide_factory()` internally, unlike every other site. Reason: unlike `varco_sa`'s sibling
(`grep` confirmed no test imports `_make_repo_provider` directly there — free to become
`_bind_repo_provider(container, entity_cls)`), `varco_beanie/tests/test_beanie_di.py` imports
`_make_repo_provider` directly and asserts it returns a stamped, unregistered callable with a
patched `__annotations__`/`__name__` (`test_make_repo_provider_produces_callable`,
`test_make_repo_provider_sets_correct_return_annotation`,
`test_make_repo_provider_function_name_includes_entity_name`, and two more). `provide_factory()`
always ends by calling `container.provide()`, so it cannot be reused for a container-less
builder function without either breaking those tests or duplicating `provide_factory()`'s own
patch logic under a different name — the exact "keep it as a thin `@Provider`-stamping wrapper
so the test keeps working" escape hatch Step 21 names for this situation. `_make_repo_provider`'s
docstring was updated to explain this and point at `provide_factory()` for the shared rationale;
`bind_repositories()` itself is unchanged (`container.provide(_make_repo_provider(entity_cls))`).
All pre-existing `varco_beanie` tests pass unmodified. Full verification results are in the
implementer's final report for this session.

---

## Risks

- **Part A branch (b) turns out to be reality** — the three event buses are unresolvable on
  the documented bootstrap path today. Mitigation: Step 4's checkpoint catches it before any
  production edit, and Steps 6-8 fix it. Invariant that must hold either way:
  `container.get(AbstractEventBus, qualifier=...)` resolves after the change (Step 3).
- **A `ProviderBinding` has no `.implementation`.** Any out-of-tree or in-tree code
  introspecting `container._bindings` for these three classes breaks. Mitigation: Step 6's
  grep; `varco_redis/tests/test_redis_di.py:53-56` already uses the safe `getattr(..., None)`
  form. Invariant: no bare `b.implementation` over an unfiltered binding list.
- **Priority drift during conversion.** `-sys.maxsize` vs `-sys.maxsize - 1` are both in use
  in these packages (`channel.py:131` vs `backplane.py:136`); copy-pasting the wrong one
  silently reorders framework defaults. Mitigation: Step 7's
  `test_app_supplied_settings_win_over_the_default` written *before* Step 8. Invariant: the
  framework default must remain strictly lower-priority than any app binding.
- **Part B touches four packages' DI surface** (audit's own "Risk of fixing", line 98:
  *"behavior-preserving refactor, but touches four packages' DI surface; needs the existing
  `validate_bindings()` tests in each package re-run as the safety net"*). Mitigation: Steps
  11-13 build the missing `container.get()`-level net **before** any extraction; Steps 17-23
  land one call site each, independently verifiable.
- **The ImportError guard regressing silently.** If the presence probe is written as
  `from varco_core.providify_compat import provide_factory` instead of `import providify`,
  the guard tests still pass today by coincidence (the test's `sys.modules` purge filter
  `"providify" in k` happens to match the new module name) but would break the moment the
  module is renamed. Mitigation: the probe shape is mandated in the Design section and is
  Step 17's acceptance criterion. Invariant: `bind_*_adapter()` never raises when providify
  is absent.
- **Scope inversion on sites 5/6.** `provide_factory`'s `singleton` defaults to `False`, but
  four of the seven sites are singletons — passing the wrong value would turn a per-request
  repository into a process-wide one (a cross-request/cross-tenant `AsyncSession` leak).
  Mitigation: Step 11's "two resolutions return different instances" assertion and Steps
  12-13's "two resolutions return the same instance" assertion pin both directions before the
  extraction.
- **`varco_core.providify_compat` accidentally becoming a scanned binding source** if a future
  edit adds a decorated symbol to it. Mitigation: Step 16.
