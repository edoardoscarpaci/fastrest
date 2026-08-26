# Plan 004 — OTel auto-captured parameters, global attributes, and database-auditing docs

## Goal

Three deliverables in one change:

- **(A)** Every `@span` (and `TracingServiceMixin` / `TracingRepositoryMixin` / `create_span`)
  automatically records the decorated function's **arguments** as span attributes
  (`param.<name>`), with redaction, truncation and a global + per-decorator kill switch.
- **(B)** A process-wide **global attribute registry** (`varco_core.observability.attributes`)
  whose entries are stamped on **every span** and **every metric measurement**
  (counter / up-down counter / histogram / observable gauge), supporting static values,
  env-var-sourced values, and **callable providers** for values not known at bootstrap.
  The existing `OtelConfig.extra_resource_attrs` (SDK `Resource`) stays the recommended
  home for static process identity — the plan documents which concern belongs to which layer.
- **(C)** Documentation-only: a guide explaining how to enable **database-operation auditing**
  with `varco_sa` and `varco_beanie` (the code already exists and is undocumented).

After this plan: `VARCO_OTEL_GLOBAL_ATTRS="k8s.pod.name=$(POD_NAME),service.release=$(HELM_RELEASE)"`
plus zero code changes gives pod-labelled spans **and** metrics, and a trace for
`OrderService.create` shows `param.order_id`, `param.tenant_id`, `param.password=[REDACTED]`.

## Non-goals

- ❌ No new OTel exporters, no `MeterProvider`/`Meter` subclassing, no auto-instrumentation
  of third-party libraries (their instruments bypass our factories — documented limitation).
- ❌ No return-value capture. Only inputs. (Return values are frequently large domain objects;
  a separate opt-in feature if ever requested.)
- ❌ No change to `AuditEntry` / `AuditLogMixin` / `AuditConsumer` / `SAAuditRepository` /
  `BeanieAuditRepository` **code**. Deliverable (C) is docs only.
- ❌ No pydantic `BaseSettings` for the new configuration (project pitfall: providify cannot
  inject pydantic's `**values` ctor). Plain frozen dataclass + `from_env()`.
- ❌ No sampling / tail-based sampling changes.
- ❌ No breaking change: every existing `@span` / `@counter` / `@histogram` / `Metric` /
  `create_span` / `create_counter` call site keeps working with an unchanged signature.

---

## Design

### Layer map (what is added where)

```
                       ┌───────────────────────────────────────────┐
                       │ varco_core/observability/params.py  (NEW) │
                       │  ParamCaptureConfig, CapturePlan,          │
                       │  sanitize_value(), redaction, truncation   │
                       └──────────────┬────────────────────────────┘
                                      │ used by
   ┌──────────────────────────────────┼───────────────────────────────────┐
   │ span.py     mixin.py     repository_mixin.py     helpers.create_span │
   └──────────────────────────────────┼───────────────────────────────────┘
                                      │ also merge
                       ┌──────────────┴────────────────────────────┐
                       │ varco_core/observability/attributes.py(NEW)│
                       │  GlobalAttributes registry (static +       │
                       │  env-sourced + callable providers)         │
                       │  wrap_instrument()  ← metric interception  │
                       └──────────────┬────────────────────────────┘
                                      │ used by
   ┌──────────────────────────────────┴───────────────────────────────────┐
   │ metrics.py (@counter/@histogram)   metric.py (Metric, register_gauge) │
   │ helpers.py (create_counter/create_histogram)                          │
   └───────────────────────────────────────────────────────────────────────┘

  config.py: OtelConfig gains capture/global-attr fields
  di.py:     OtelConfiguration seeds the registry + capture defaults at bootstrap
```

Import direction is strictly one-way — `params.py` and `attributes.py` import **only stdlib +
OTel API**, never other observability modules. No cycles.

---

### (A) Parameter capture

#### Where the work happens (decoration time vs. call time)

The killer constraint: **decorators run at import time**, before `OtelConfiguration` is
installed and before `set_param_capture_defaults()` could ever be called. So:

| Concern | Resolved | Why |
|---|---|---|
| Signature introspection → `CapturePlan` | **first call**, memoised on the wrapper | `inspect.signature()` is expensive (~10 µs); doing it per call is unacceptable, doing it at decoration time freezes a config that isn't loaded yet |
| `enabled` flag | **every call** (module-level `bool` read) | must honour a runtime kill switch; a bool read is ~30 ns |
| structural config (prefix/limits/redaction) | snapshotted into the plan on first call | changing it later requires `reset_param_capture_state()` (test helper) — documented |

```python
# per-call fast path, no inspect.signature()
if _capture_enabled():
    plan = _plan or _build_plan_now()  # memoised closure cell
    attrs = plan.extract(args, kwargs)  # zip + dict lookups only
```

`CapturePlan.extract` never calls `Signature.bind` — the plan precomputes an ordered tuple of
positional parameter names (with `None` for skipped slots such as `self`/`cls`/excluded names)
plus a keyword allow-set, so extraction is a `zip()` over `args` and a filtered pass over
`kwargs`.

#### `ParamCaptureConfig`

```python
@dataclass(frozen=True)
class ParamCaptureConfig:
    enabled: bool | None = None  # None → inherit process default
    prefix: str = "param."
    include: tuple[str, ...] = ()  # allow-list; empty = "all params"
    exclude: tuple[str, ...] = ()  # deny-list; applied after include
    value_mode: Literal["scalars", "repr"] = "scalars"
    max_value_length: int = 256
    max_params: int = 32
    max_sequence_items: int = 10
    capture_varargs: bool = False  # *args / **kwargs extras
    capture_self: bool = False  # self / cls
    redact_patterns: tuple[str, ...] = DEFAULT_REDACT_PATTERNS
    redaction_placeholder: str = "[REDACTED]"
```

`DEFAULT_REDACT_PATTERNS = ("password", "passwd", "secret", "token", "authorization",
"auth", "api_key", "apikey", "credential", "private_key", "cookie", "session_id", "otp",
"pin", "ssn")` — case-insensitive **substring** match on the parameter name. Redaction is
applied even to explicitly `include`d names (fail-closed).

#### Value rendering rules (`sanitize_value`)

| Input | `value_mode="scalars"` (default) | `value_mode="repr"` |
|---|---|---|
| `str` | truncated to `max_value_length`, `…` suffix when cut | same |
| `int` / `float` / `bool` | native OTel scalar (no `str()`) | same |
| `None` | `"None"` | `"None"` |
| `UUID`, `Decimal`, `datetime`, `Enum`, `Path` | `str(value)`, truncated | same |
| `list`/`tuple`/`set` of scalars, `len ≤ max_sequence_items` | native OTel sequence | same |
| longer / mixed sequence | `"<list len=1000>"` | `repr()` truncated |
| `dict`, Pydantic model, dataclass, any other object | `"<OrderCreateDTO>"` | `repr()` truncated |

**DESIGN: `"scalars"` default instead of `repr()` everywhere**
- ✅ A DTO carrying an email/IBAN/address never leaks into the trace backend by accident.
- ✅ Native scalar types keep backends' numeric filters (`param.limit > 100`) working.
- ✅ Bounded cost — no `repr()` of a 10 MB payload on the hot path.
- ❌ `param.dto=<OrderCreateDTO>` is not very informative; users must opt into
  `value_mode="repr"` (or set explicit attributes in the body) when they want the payload.

`sanitize_value` is wrapped in `try/except Exception` per parameter — an object whose
`__repr__`/`__str__` raises yields `"<unrepresentable>"` and never propagates.
`extract()` itself is wrapped in `try/except Exception` and returns `{}` on failure, logging
once at `DEBUG`. **Instrumentation must never break the application.**

`max_params` truncates the captured set (deterministic: declaration order) and adds
`param._truncated = True` so the operator knows attributes were dropped.

Defaults are **not** applied (`Signature.apply_defaults()` is not used) — only arguments the
caller actually passed are captured. ✅ lower noise/cardinality, ✅ makes "was this passed?"
visible in the trace; ❌ a parameter left at its default shows no attribute (documented).

#### Enabling / disabling

Precedence, most specific first:

1. `SpanConfig.capture_params: bool | None` (quick per-decorator toggle)
2. `SpanConfig.param_capture: ParamCaptureConfig | None` → its `.enabled`
3. process default — `set_capture_enabled(bool)` / `VARCO_OTEL_CAPTURE_PARAMS`
   (default **`true`**) / `OtelConfig.capture_params`

```python
@span(SpanConfig(name="payment.charge", capture_params=False))          # off here only
@span(SpanConfig(param_capture=ParamCaptureConfig(value_mode="repr")))  # verbose here only
```

**DESIGN: default ON**
- ✅ Exactly what was requested — traces are useful for debugging with zero opt-in effort.
- ✅ Safe-by-construction defaults (scalars-only + name redaction + truncation).
- ❌ A parameter named `data` holding a PII payload renders as `<dict>` — safe — but a
  parameter named `email` holding a string **is** captured. The redaction list cannot know
  every PII field name. Mitigation: documented `VARCO_OTEL_CAPTURE_PARAMS=false` kill switch,
  `VARCO_OTEL_CAPTURE_PARAMS_EXCLUDE`, and an explicit "PII" section in the feature doc.
- Reviewer escape hatch: flipping the default to `false` is a **one-line change** in
  `params.py` (`_DEFAULT_ENABLED`) if the team prefers opt-in.

---

### (B) Global attributes

#### Resource attributes vs. per-emission attributes — the decision

| | OTel **Resource** (`OtelConfig.extra_resource_attrs`) | **Global attribute registry** (new) |
|---|---|---|
| Data model | identity of the *process* producing telemetry; exported once per batch | a label on *each* span / each measurement |
| Cost | free — no per-emission work, **no metric series multiplication inside a process** | dict merge per emission; **each key becomes a metric label ⇒ one series per distinct value per metric** |
| Known when? | must be known at `TracerProvider`/`MeterProvider` construction (bootstrap) | can be registered/updated at any time; providers evaluated lazily |
| Prometheus pull | surfaces as `target_info` / `job`+`instance`, **not** as a label on each series | a real label on each series |
| Queryable in Tempo/Jaeger | yes (resource attrs are first-class) | yes |

**Decision — both layers, with explicit guidance:**

- *Static process identity* (`k8s.pod.name`, `k8s.namespace.name`, `service.instance.id`,
  `deployment.environment`, Helm release) → **Resource attributes**. `OtelConfig.extra_resource_attrs`
  already does this and stays the recommended path.
- *Values not known at bootstrap, mutable during process lifetime, or that the backend must
  filter/group by as a label* (canary flag, feature-flag cohort, config generation, a pod name
  injected by a sidecar after start, a Prometheus-pull deployment where `target_info` joins are
  impractical) → **global attribute registry**.

**DESIGN: registry does NOT auto-copy into the Resource, and Resource does NOT auto-copy into
the registry.** Two independent knobs.
- ✅ No silent double-labelling (the same key appearing both as a resource attr and a per-series
  label doubles storage and confuses `group by` queries).
- ✅ The user chooses cardinality cost explicitly.
- ❌ Someone who wants both must list the key in both places. Mitigated: `OtelConfig` gets a
  `promote_global_attrs_to_resource: bool = False` convenience that merges the *static* part of
  the registry into the Resource at bootstrap, and the feature doc's copy-paste k8s recipe shows
  the recommended split.

#### API

```python
# varco_core/observability/attributes.py

AttributeValue = str | bool | int | float
AttributeProvider = Callable[[], Mapping[str, AttributeValue] | None]

class GlobalAttributes:
    def set(self, mapping: Mapping[str, AttributeValue] | None = None, /, **attrs) -> None
    def add(self, key: str, value: AttributeValue) -> None
    def remove(self, key: str) -> None
    def register_provider(self, provider: AttributeProvider, *, name: str,
                          cache_ttl: float | None = None) -> None
    def unregister_provider(self, name: str) -> None
    def clear(self) -> None
    def snapshot(self) -> Mapping[str, AttributeValue]      # merged + cached, read-only

# module-level singleton + free functions (the public surface)
def global_attributes() -> GlobalAttributes: ...
def set_global_attributes(mapping=None, /, **attrs) -> None: ...
def register_global_attribute_provider(provider, *, name, cache_ttl=None) -> None: ...
def current_global_attributes() -> Mapping[str, AttributeValue]: ...
def clear_global_attributes() -> None: ...                   # tests
def load_global_attributes_from_env(environ: Mapping[str, str] | None = None) -> None: ...
def configure_global_attributes(*, apply_to_spans: bool | None = None,
                                apply_to_metrics: bool | None = None) -> None: ...
```

Semantics:

- **Copy-on-write snapshot.** Mutations take a module-level `threading.Lock` and rebuild an
  immutable `MappingProxyType`; readers take no lock and read one attribute. (`threading.Lock`,
  not `asyncio.Lock` — this is sync code called from both threads and coroutines. The project's
  "create locks lazily" rule targets `asyncio.Lock` only; a module-level `threading.Lock` needs
  no event loop.)
- **Providers.** `cache_ttl=None` (default) → evaluated **once**, memoised forever (correct for
  pod name / release). `cache_ttl=0.0` → evaluated on every snapshot rebuild. `cache_ttl=30.0`
  → re-evaluated when older than 30 s. A provider that raises is logged **once** (per provider
  name, at `WARNING`) and then skipped; it never breaks the emission path.
- **`None` values** returned by a provider are dropped (OTel forbids `None` attribute values).
- **Snapshot cache** invalidated by a `_generation` counter (bumped on any mutation) and by the
  earliest provider TTL deadline. Steady state = one integer compare + one attribute read.

**DESIGN: callable providers instead of "static dict only"**
- ✅ Covers "value not known at bootstrap" (downward API injected late, sidecar-provided
  metadata, a config value fetched asynchronously and then published).
- ✅ Covers values that change (deployment colour flip, feature-flag cohort).
- ❌ A provider on the hot path can be slow. Mitigated by `cache_ttl=None` default (once) and a
  loud doc warning that providers must be non-blocking and never do I/O.

#### Env-var bootstrap (no code required)

- `VARCO_OTEL_GLOBAL_ATTRS="k8s.pod.name=orders-7d9,service.release=blue"` — literal
  `key=value` pairs, comma-separated. In Kubernetes, `$(POD_NAME)` expansion in the container
  `env:` value works, so a Downward-API pod name lands here with no code.
- `VARCO_OTEL_GLOBAL_ATTR_ENV="k8s.pod.name=POD_NAME,k8s.node.name=NODE_NAME"` — the value is
  the **name of another env var**, read lazily at first snapshot. Covers env vars that are
  populated after import, and avoids shell-expansion quirks.
- `VARCO_OTEL_GLOBAL_ATTRS_SPANS` / `VARCO_OTEL_GLOBAL_ATTRS_METRICS` (default `true` both) —
  per-signal kill switches.

`load_global_attributes_from_env()` is invoked lazily on the first `snapshot()` (idempotent,
guarded by a flag) **and** eagerly by `OtelConfiguration` at bootstrap, so both DI and
no-DI users get it.

Parse failures (a token with no `=`) are logged at `WARNING` and skipped — a malformed env var
must not crash the process.

#### Interception point for metrics

Instruments are created lazily and cached in `metrics._instrument_cache` and handed out raw by
`helpers.create_counter()` / `create_histogram()`. Options:

| Option | Verdict |
|---|---|
| (a) Merge at each call site in `@counter`/`@histogram`/`Metric.add` | ❌ misses `create_counter()` callers holding the raw instrument, misses observable gauges; 5 duplicated merge sites |
| (b) **Wrap the instrument in a proxy at creation time** (single choke point: instrument-creation factories) | ✅ **chosen** — covers every path incl. raw-instrument holders; one merge implementation |
| (c) OTel SDK `View` | ❌ views can drop/rename/aggregate attributes, they cannot **inject** them |
| (d) Custom `MeterProvider`/`Meter` wrapper installed via DI | ❌ must re-implement the whole Meter API; only works on the DI path; still misses `otel_metrics.get_meter()` direct callers |

```python
class GlobalAttrInstrument:
    """Transparent proxy that merges global attributes into every measurement."""

    __slots__ = ("_inner",)

    def add(self, amount, attributes=None, context=None):
        g = current_global_attributes()
        if g:
            attributes = {**g, **(attributes or {})}  # caller wins on conflict
        self._inner.add(amount, attributes=attributes, context=context)

    def record(self, value, attributes=None, context=None): ...  # same shape
    def unwrap(self):
        return self._inner

    def __getattr__(self, item):
        return getattr(self._inner, item)
```

`wrap_instrument(instrument)` returns the **raw** instrument when
`apply_to_metrics is False` → literally zero overhead for users who opt out. When the registry
is empty the proxy short-circuits on one `if g:` (~100 ns).

Observable gauges (`register_gauge`) take callbacks returning `Observation(value, attributes)` —
handled by a separate `wrap_gauge_callback(cb)` that merges into each yielded `Observation`.

**❌ Known drawback (documented):** `isinstance(create_counter(...), opentelemetry.metrics.Counter)`
becomes `False`. `.unwrap()` is provided; `__getattr__` delegation keeps duck-typed use working.

#### Interception point for spans

In `span.py`, `mixin.py`, `repository_mixin.py`, `helpers.create_span` the merged attribute dict
is passed to `tracer.start_as_current_span(name, attributes=merged, record_exception=False)`
rather than being `set_attribute()`-ed after start.

- ✅ Attributes present at span start participate in the **sampling decision** (a sampler can
  drop/keep on `param.tenant_id`); post-start `set_attribute` is invisible to the sampler.
- ✅ One SDK call instead of N.
- ❌ Behaviour change: attributes exist from `t=0`. Existing tests assert on the *finished*
  span's attributes, so they are unaffected — verified as an explicit regression step.

Merge order for spans (later wins):
`global_attrs → captured params → SpanConfig.attributes → correlation_id`.
Rationale: explicit per-decorator config beats process-wide defaults; `correlation_id` is a
framework invariant and must never be shadowed.

---

### New `OtelConfig` fields (all defaulted — backwards compatible)

```python
capture_params: bool | None = None  # None → env/default (true)
param_capture: ParamCaptureConfig | None = None  # full structural override
global_attributes: dict[str, str] = field(default_factory=dict)
global_attributes_on_spans: bool = True
global_attributes_on_metrics: bool = True
promote_global_attrs_to_resource: bool = False  # also merge statics into the Resource
```

`OtelConfiguration` (di.py) applies them in a new `@Provider(singleton=True)`
`observability_attributes(self, config: Inject[OtelConfig]) -> GlobalAttributes` that:
1. `load_global_attributes_from_env()`,
2. `set_global_attributes(**config.global_attributes)` (config wins over env — explicit code
   beats ambient env),
3. `configure_global_attributes(apply_to_spans=…, apply_to_metrics=…)`,
4. seeds `set_capture_enabled(...)` / `set_param_capture_defaults(...)`,
and is `Inject`-depended-on by `tracer_provider()` and `meter_provider()` so ordering is
deterministic. `_build_resource()` additionally merges the static registry when
`promote_global_attrs_to_resource=True`.

### Alternatives considered

- **Put parameter capture in a separate `@span_params` decorator** — ❌ rejected: the request is
  "automatically"; a second decorator is opt-in-by-another-name and doubles the wrapper cost.
- **Use `Signature.bind()` per call** — ❌ rejected: ~5–15 µs/call on a hot path that currently
  costs ~1 µs. The precomputed `CapturePlan` is ~0.5 µs.
- **`repr()` everything by default** — ❌ rejected: PII + unbounded size on the hot path.
  Available via `value_mode="repr"`.
- **Global attributes only as Resource attributes** — ❌ rejected: cannot express values unknown
  at bootstrap, and Prometheus-pull users cannot filter series by them.
- **Global attributes only as per-emission labels** — ❌ rejected: pays per-emission cost and
  metric-series multiplication for values (`service.name`, `service.version`) the Resource
  already carries for free.
- **`contextvars`-based request-scoped attributes** (tenant, user) instead of a process-wide
  registry — ❌ rejected *for this plan* (different lifetime, different cardinality story,
  belongs with the existing `varco_core.tracing` correlation machinery). The registry's provider
  API can read a ContextVar if a user wants it, so nothing is foreclosed.
- **OTel `Span` processor that injects attributes on start** — ✅ would cover third-party spans
  too, ❌ but only works when the varco `TracerProvider` is installed (breaks the "no DI" path
  that decorators support today) and has no metrics equivalent. Rejected for asymmetry; noted in
  the feature doc as a future enhancement.

---

## Steps

TDD-ordered. Each step is independently verifiable.

### Phase 1 — parameter capture (A)

1. [ ] `varco_core/tests/test_observability_params.py` — **new, failing** unit tests for the
   pure helpers (no OTel needed): `sanitize_value` per row of the rendering table;
   truncation marker; `None` → `"None"`; sequence summarisation; `__repr__` raising →
   `"<unrepresentable>"`; redaction by substring (`user_password`, `X-Authorization`,
   `api_key`) incl. redaction winning over `include`; `build_capture_plan` skipping
   `self`/`cls`, honouring `include`/`exclude`, skipping `*args`/`**kwargs` unless
   `capture_varargs=True`; `max_params` truncation adding `param._truncated`.
2. [ ] `varco_core/varco_core/observability/params.py` — **new**: `ParamCaptureConfig`,
   `DEFAULT_REDACT_PATTERNS`, `CapturePlan` (+`extract`), `build_capture_plan`,
   `sanitize_value`, `capture_enabled()`, `set_capture_enabled()`,
   `param_capture_defaults()`, `set_param_capture_defaults()`,
   `param_capture_from_env()`, `reset_param_capture_state()`. `from __future__ import
   annotations`, frozen dataclasses, `DESIGN:` blocks per decision above.
3. [ ] `varco_core/tests/test_observability_params.py` — add env-parsing tests via
   `monkeypatch.setenv` for every `VARCO_OTEL_CAPTURE_PARAMS*` var, including invalid
   values falling back to the default with a logged warning.

### Phase 2 — global attribute registry (B)

4. [ ] `varco_core/tests/test_observability_global_attrs.py` — **new, failing**: static
   `set`/`add`/`remove`/`clear`; snapshot is read-only (`TypeError` on mutation) and
   identity-stable between mutations; provider with `cache_ttl=None` called exactly once;
   `cache_ttl=0.0` called every snapshot; TTL expiry re-evaluates (monkeypatched clock);
   raising provider is skipped and logged once; `None` values dropped; env parsing for
   `VARCO_OTEL_GLOBAL_ATTRS` and `VARCO_OTEL_GLOBAL_ATTR_ENV` (incl. malformed token
   skipped, not raised); `configure_global_attributes` toggles.
5. [ ] `varco_core/varco_core/observability/attributes.py` — **new**: type aliases,
   `GlobalAttributes`, module singleton + free functions, `load_global_attributes_from_env`,
   `configure_global_attributes`, `apply_to_spans()`/`apply_to_metrics()` readers,
   `wrap_instrument()`, `GlobalAttrInstrument`, `wrap_gauge_callback()`.
6. [ ] `varco_core/tests/test_observability_global_attrs.py` — add `GlobalAttrInstrument`
   tests with a fake instrument: merge order (caller key wins), empty-registry short circuit
   passes `attributes` through **unchanged** (identity, not a copy), `unwrap()`,
   `__getattr__` delegation, and `wrap_instrument` returning the raw object when
   `apply_to_metrics=False`.

### Phase 3 — wire spans

7. [ ] `varco_core/tests/test_observability.py` — add failing cases using the existing
   `span_exporter` fixture: bare `@span` on `async def f(a, b=2)` produces `param.a`/`param.b`;
   method call does **not** produce `param.self`; `SpanConfig(capture_params=False)` produces
   no `param.*`; `set_capture_enabled(False)` disables globally; a `password=` kwarg is
   redacted; `set_global_attributes(pod="p1")` appears on the span; `configure_global_attributes(
   apply_to_spans=False)` removes it; `SpanConfig.attributes` still wins over a same-named
   global attr; `correlation_id` still present (regression); **all pre-existing assertions in
   this file still pass unchanged**.
8. [ ] `varco_core/varco_core/observability/span.py` — extend `SpanConfig` with
   `capture_params: bool | None = None` and `param_capture: ParamCaptureConfig | None = None`;
   in `_make_wrapper`, build the memoised plan lazily and pass the merged dict via
   `start_as_current_span(..., attributes=merged)` in both the async and sync wrappers.
   Keep exception handling identical.
9. [ ] `varco_core/varco_core/observability/helpers.py` — `create_span` gains
   `params: Mapping[str, Any] | None = None` (sanitised through `sanitize_value`) and merges
   global attributes. Signature stays backwards compatible.
10. [ ] `varco_core/varco_core/observability/mixin.py` + `repository_mixin.py` — route their
    span creation through the same merge helper so CRUD/repository spans get params
    (`pk`, `dto`, `params`) and global attributes; keep the `{ClassName}.{operation}` naming
    and the async-generator handling in `repository_mixin` intact. Add mixin cases to
    `test_observability.py`.

### Phase 4 — wire metrics

11. [ ] `varco_core/tests/test_observability.py` — failing cases with the `metric_reader`
    fixture: global attrs present on `@counter`, `@histogram`, `Metric.add/sub/record` for all
    three kinds, and on `register_gauge` observations; caller attribute wins on key conflict;
    `configure_global_attributes(apply_to_metrics=False)` removes them; attributes registered
    **after** the instrument was first created still appear (late registration).
12. [ ] `varco_core/varco_core/observability/metrics.py` — wrap in `_get_counter` /
    `_get_histogram` via `wrap_instrument(...)` before caching.
13. [ ] `varco_core/varco_core/observability/metric.py` — wrap in `Metric._get_instrument`
    (shares `_instrument_cache`, so wrap exactly once at creation) and wrap the callbacks in
    `register_gauge`.
14. [ ] `varco_core/varco_core/observability/helpers.py` — `create_counter` / `create_histogram`
    return the wrapped instrument (same cache ⇒ automatically consistent with step 12).

### Phase 5 — config, DI, exports

15. [ ] `varco_core/tests/test_observability.py` — failing cases: `OtelConfig` new fields exist
    with the documented defaults; installing `OtelConfiguration` with
    `global_attributes={"k8s.pod.name": "p"}` makes it appear on a span and a counter;
    `promote_global_attrs_to_resource=True` puts it on the exported Resource;
    `capture_params=False` in config disables capture process-wide.
16. [ ] `varco_core/varco_core/observability/config.py` — add the six fields + docstring
    entries (Args / Edge cases / the resource-vs-label guidance).
17. [ ] `varco_core/varco_core/observability/di.py` — add the
    `observability_attributes` provider; make `tracer_provider` / `meter_provider` depend on it;
    extend `_build_resource` for `promote_global_attrs_to_resource`.
18. [ ] `varco_core/varco_core/observability/__init__.py` — export `ParamCaptureConfig`,
    `set_capture_enabled`, `set_param_capture_defaults`, `GlobalAttributes`,
    `set_global_attributes`, `register_global_attribute_provider`,
    `current_global_attributes`, `clear_global_attributes`, `configure_global_attributes`;
    update the module docstring; keep `__all__` alphabetically grouped as today.
19. [ ] `varco_core/tests/test_observability.py` — assert the public import surface
    (`from varco_core.observability import <each new name>`).

### Phase 6 — varco_fastapi passthrough (optional, drop if scope pressure)

20. [ ] `varco_fastapi/varco_fastapi/app.py` — `create_varco_app(..., global_attributes:
    Mapping[str, str] | None = None, capture_params: bool | None = None)`; both default `None`
    (no behaviour change) and simply call the `varco_core.observability` setters before the
    middleware stack is built. Docstring + `Args:` entry.
21. [ ] Inspect the tracing middleware (`varco_fastapi/varco_fastapi/middleware/tracing.py`) —
    if it calls `tracer.start_as_current_span` directly, route it through the shared merge
    helper so the **server span** also carries global attributes. Add a test in
    `varco_fastapi/tests/` asserting a request span carries a registered global attribute.

### Phase 7 — documentation

22. [ ] `technical_docs/features/observability-attributes.md` — **new**. Sections: what changed;
    the parameter-capture rendering table; redaction + PII guidance + kill switches; the
    **resource-attributes vs. per-emission-attributes decision table**; cardinality warning
    (a `k8s.pod.name` **metric label** creates one series per metric per pod — with HPA churn
    this is the #1 way to blow up a Prometheus TSDB; prefer a resource attribute unless you
    must `group by` it); callable providers (and the "never do I/O in a provider" rule);
    full `VARCO_OTEL_*` env-var table; copy-paste Kubernetes Downward-API recipe:

    ```yaml
    env:
      - name: POD_NAME
        valueFrom: { fieldRef: { fieldPath: metadata.name } }
      - name: VARCO_OTEL_GLOBAL_ATTR_ENV
        value: "k8s.pod.name=POD_NAME,k8s.node.name=NODE_NAME"
    ```
    plus the `isinstance` caveat on `create_counter()` and the third-party-instrument limitation.
23. [ ] `technical_docs/features/database-auditing.md` — **new** (deliverable C). Content:
    - What you get: append-only trail of create/update/delete with `entry_id`, `entity_type`,
      `entity_id`, `action`, `actor_id`, `diff`, `occurred_at`, `correlation_id`, `tenant_id`
      (`varco_core/varco_core/service/audit.py:90`).
    - Flow diagram: `Service(+AuditLogMixin)` → `AuditEvent` (`__event_type__="varco.audit"`)
      on channel `"varco.audit"` → `AbstractEventBus` → `AuditConsumer` → `AuditRepository` → DB.
    - **Step 1** — compose `AuditLogMixin` (left of `AsyncService` in the MRO; it overrides
      `_after_create` / `_after_update` / `_after_delete` and chains via `super()`), and note it
      requires the service's injected `AbstractEventProducer`.
    - **Step 2a — varco_sa**: `SAAuditRepository(session_factory)`; table `varco_audit_log` via
      `AuditEntryModel`; Alembic wiring
      `from varco_sa.audit import audit_metadata; target_metadata = [Base.metadata,
      outbox_metadata, audit_metadata]`; dev-only `create_all` snippet.
    - **Step 2b — varco_beanie**: register `AuditDocument` in
      `init_beanie(database=db, document_models=[..., AuditDocument])`; collection
      `varco_audit_log`; indexes declared in `AuditDocument.Settings`; `BeanieAuditRepository`.
    - **Step 3** — wire `AuditConsumer(audit_repo=...)` and call `register_to(bus)` from a
      `@PostConstruct` method (project rule: never subscribe in `__init__`), with a providify
      sketch.
    - **Step 4** — reading the trail: `await audit_repo.list_for_entity("Order", str(id), limit=50)`.
    - **Consistency section — "eventually consistent via events"**: the audit row is written
      *after* the domain transaction commits, by a consumer that may run in another process.
      Failure modes: `InMemoryEventBus` → the event dies with the process; Kafka/Redis →
      at-least-once, so duplicates are possible. `AuditConsumer.on_audit_event` currently
      declares `@listen(AuditEvent, channel="varco.audit")` **without** `retry_policy`/`dlq` —
      document that adding resilience means subclassing `AuditConsumer` and re-declaring the
      handler with `retry_policy=` + `dlq=`.
    - **Relation to the outbox**: for compliance-grade "must not lose an audit record", save the
      `AuditEvent` as an `OutboxEntry` inside the same UoW transaction as the entity write and
      let `OutboxRelay` publish it → at-least-once end-to-end. ⚠️ **Implementer: read
      `SAAuditRepository.save` before writing this paragraph** — it imports
      `sqlalchemy.dialects.postgresql.insert`, which suggests an `ON CONFLICT` upsert on
      `entry_id` (i.e. replay-idempotent). State the actual behaviour, and state the Beanie
      behaviour separately (`varco_beanie/varco_beanie/audit.py` does plain append-only inserts).
    - Pitfalls sub-table (mirrors the CLAUDE.md rows in step 26).
24. [ ] `technical_docs/index.md` — add both new guides.
25. [ ] `README.md` — extend the Observability section (~L2385–2451) with parameter capture and
    global attributes + the full `VARCO_OTEL_*` env-var table; add an "Auditing" subsection
    linking to the new guide.
26. [ ] `CLAUDE.md` — (a) extend the observability description with the two new modules and the
    resource-vs-label rule; (b) add a new **"Database auditing (varco_core.service.audit)"**
    section with the wiring snippet; (c) add pitfalls-table rows:

    | Pitfall | Symptom | Root cause | Fix |
    |---|---|---|---|
    | Secret in a span attribute | A password/token value visible in the trace UI | Param capture is on and the param name isn't in the redact list | Add it to `VARCO_OTEL_CAPTURE_PARAMS_EXCLUDE`/`redact_patterns`, or `capture_params=False` on that `@span` |
    | Metric series explosion after adding a global attribute | Prometheus TSDB churn / OOM after a deploy | `k8s.pod.name` was added as a *per-measurement* attribute, so every pod creates its own series for every metric | Put static process identity in `OtelConfig.extra_resource_attrs` (Resource), not in the global attribute registry |
    | Global attribute never appears | Registry set, spans/metrics unlabelled | `configure_global_attributes(apply_to_spans/metrics=False)` or the corresponding env var is `false` | Check `VARCO_OTEL_GLOBAL_ATTRS_SPANS` / `_METRICS` |
    | Provider called on every measurement | Latency regression on the hot path | Provider registered with `cache_ttl=0.0` | Use the default `cache_ttl=None` (evaluate once) for immutable values |
    | `isinstance(create_counter(...), Counter)` is False | Type check fails after upgrade | The instrument is wrapped in `GlobalAttrInstrument` | Use duck typing, or `.unwrap()`, or `apply_to_metrics=False` |
    | Audit entries never written | Service emits, DB table stays empty | `AuditConsumer.register_to(bus)` never called | Call it from a `@PostConstruct` method |
    | `relation "varco_audit_log" does not exist` | Consumer raises on first audit event | `audit_metadata` not in the Alembic `target_metadata` | Add `from varco_sa.audit import audit_metadata` to `env.py` |
    | `CollectionWasNotInitialized` on audit save | Beanie raises when the consumer persists | `AuditDocument` missing from `init_beanie(document_models=...)` | Register it at startup |
    | Audit record lost on broker outage | Domain write committed, no audit row | Audit is emitted post-commit as a plain event | Emit the `AuditEvent` through the transactional outbox |

27. [ ] `ARCHITECTURE.md` — add `observability/params.py` and `observability/attributes.py` to
    the module map and the `service/audit.py` + backend audit repositories to the type hierarchy.

---

## Edge cases

- No `TracerProvider`/`MeterProvider` installed → OTel no-op objects; capture and merge still run
  but cost nothing observable. Functions execute normally.
- `@span` on a `lambda` / `functools.partial` / a C-implemented callable →
  `inspect.signature()` raises `ValueError`/`TypeError`; `build_capture_plan` catches it, returns
  an empty plan, logs once at `DEBUG`. Span still created.
- Decorated function called with wrong arity (`TypeError` from the callee) → `extract()` must not
  raise first; extra positional args beyond the plan are ignored.
- Bound method vs. plain function vs. `@staticmethod`/`@classmethod` → `self`/`cls` detected by
  *first-parameter name* (`self`, `cls`) rather than by descriptor type, because the decorator
  sees the plain function at class-definition time. Documented; overridable via `capture_self`.
- A parameter named `correlation_id` → captured as `param.correlation_id`; the framework's own
  `correlation_id` attribute is untouched (different key).
- Global attribute key collides with a `SpanConfig.attributes` key → the `SpanConfig` value wins.
- Global attribute key collides with a caller's metric attribute → the **caller** wins.
- Registry mutated after instruments were created/cached → new values still appear (snapshot is
  read per measurement).
- Provider returns a non-mapping → skipped + logged once.
- `VARCO_OTEL_GLOBAL_ATTR_ENV` points at an unset env var → the key is simply absent.
- `max_params=0` → capture effectively disabled (no attributes, no `_truncated` marker noise
  — assert this).
- Very large `str` param (10 MB) → truncated to `max_value_length` before any copy of the whole
  value is made (slice only; never `repr()` the whole thing first).
- Two threads racing on the first call of the same decorated function → both build a plan;
  last write wins; both plans are equivalent (same cache semantics as the existing
  `_instrument_cache`).

## Verification

```bash
# Phase-local (fast loop)
uv run pytest varco_core/tests/test_observability_params.py -q
uv run pytest varco_core/tests/test_observability_global_attrs.py -q
uv run pytest varco_core/tests/test_observability.py -q

# Regression — nothing else in core moved
uv run pytest varco_core/tests/ -q

# Backends + fastapi (mixins, middleware passthrough, audit repos)
uv run pytest varco_sa/tests/ varco_beanie/tests/ varco_fastapi/tests/ -q

# Import-surface smoke
uv run python -c "from varco_core.observability import (ParamCaptureConfig, set_global_attributes, register_global_attribute_provider, current_global_attributes, configure_global_attributes); print('ok')"

# Docs sanity: every new env var in the plan appears in README + the feature doc
grep -o 'VARCO_OTEL_[A-Z_]*' README.md technical_docs/features/observability-attributes.md | sort -u
```

No lint or type-check command is configured in this repo (per CLAUDE.md) — do not invent one.

Manual perf sanity (not a committed test): time 100 000 calls of a `@span`-decorated no-op with
capture on and off; expected delta ≲ 2 µs/call. If it exceeds ~5 µs, revisit `CapturePlan.extract`
before merging.

## Risks

- **PII leakage** — capture defaults to ON. Invariant: name-based redaction + `value_mode="scalars"`
  + truncation are all active by default, and the feature doc leads with the PII section.
  Rollback: `VARCO_OTEL_CAPTURE_PARAMS=false` (runtime, no redeploy of code) or flip
  `_DEFAULT_ENABLED` in `params.py`.
- **Metric cardinality** — a global attribute becomes a label on every series. Invariant:
  registry is **empty by default**; nothing changes until a user opts in. Doc + pitfall row carry
  the warning; `VARCO_OTEL_GLOBAL_ATTRS_METRICS=false` is the runtime rollback.
- **Instrument proxy breaks identity/`isinstance`** — invariant: `__getattr__` delegates
  everything, `.unwrap()` exists, and `apply_to_metrics=False` returns the raw instrument.
- **Span attributes now set at start rather than after** — invariant: the *finished* span carries
  the same key/value set as before plus the new keys; step 7 explicitly re-runs the pre-existing
  assertions in `test_observability.py` unchanged.
- **Hot-path regression** — invariant: with capture disabled and an empty registry the added cost
  is two boolean/dict-empty checks. Guarded by the manual perf sanity check.
- **Global mutable state in tests** — the registry and the capture defaults are process-wide.
  Every new test must use an autouse fixture calling `clear_global_attributes()` +
  `reset_param_capture_state()` in teardown, or unrelated tests will flake. Add that fixture in
  the same step that adds the first test.
- **Thread safety** — mutations under a module-level `threading.Lock`; readers lock-free over an
  immutable snapshot. Invariant: `snapshot()` never returns a partially-built mapping.
- **Deliverable (C) documenting behaviour that isn't there** — the audit idempotency paragraph
  must be written *after* reading `SAAuditRepository.save` and `BeanieAuditRepository.save`;
  do not copy the claim from this plan without verifying it.
