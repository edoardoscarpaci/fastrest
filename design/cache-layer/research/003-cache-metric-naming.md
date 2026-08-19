# Research 003 — Cache Observability Metric Naming

Date: 2026-08-19 · Freshness matters: **yes** — OpenTelemetry semconv versions change, de facto community practice shifts, and vendor implementations evolve (Micrometer, .NET, Prometheus)

## Question

**What names, types, units and attributes should varco use for its cache observability pack (item C3: hit/miss ratio, latency, eviction counters), given it emits through OpenTelemetry?**

## Findings

### 1. Current Status of OpenTelemetry Semantic Conventions for Cache Metrics

**No stable, experimental, or in-progress semconv for application-level cache metrics exists yet.** As of August 2026, OpenTelemetry semantic conventions (v1.44.0) define conventions for Database, HTTP, FaaS, RPC, Messaging, and System metrics—but not for application caches (the in-process or distributed caching layer above the HTTP/DB boundary).

- **Issue #1747** ([Semantic Conventions for Cache Operations](https://github.com/open-telemetry/semantic-conventions/issues/1747)) proposes **span attributes** for cache tracing (e.g., `cache.hit`, `cache.key`, `cache.ttl`), not metrics. The proposal remains open; no metrics semconv has been accepted.
- **No OTEP (OpenTelemetry Enhancement Proposal)** for cache metrics has been filed or merged.
- Infrastructure-level Redis/Memcached receiver metrics (exported by their native exporters) exist and are documented, but these are **server-side**, not application-level client instrumentation—a fundamentally different measurement point. — [Semantic Conventions v1.44.0](https://opentelemetry.io/docs/specs/semconv/) (August 2026)

### 2. De Facto Community Shapes (When Semconv Lacks Guidance)

#### Micrometer (Java ecosystem, most aligned with OTel spirit)

**Metric name:** `cache.gets` (singular noun with plural unit)  
**Type:** Counter  
**Attributes:** `result` tag with values `"hit"` or `"miss"`  
**Example:** `cache.gets{result=hit}`, `cache.gets{result=miss}`

Other metrics tracked: `cache.puts`, `cache.evictions`, `cache.size`, `cache.removals`. — [Micrometer Cache Instrumentations](https://docs.micrometer.io/micrometer/reference/reference/cache.html) (Micrometer 1.13+) and [CacheMeterBinder API](https://www.javadocs.dev/io.micrometer/micrometer-core/1.12.5/io/micrometer/core/instrument/binder/cache/GuavaCacheMetrics.html)

#### Prometheus + Java (Caffeine, Guava, Cache2k)

**Metric names** follow the Prometheus `{namespace}_{metric}_{unit}` pattern:
- `caffeine_cache_hit_total` (Counter, Prometheus _total suffix)
- `caffeine_cache_miss_total` (Counter)
- `caffeine_cache_requests_total` (Counter, derived from hits + misses)
- `caffeine_cache_eviction_total` (Counter)
- `caffeine_cache_estimated_size` (Gauge)
- `caffeine_cache_load_duration_seconds` (Histogram, with implicit _count and _sum suffixes)
- `caffeine_cache_loads_total`, `caffeine_cache_load_failure_total` (for loader-backed caches)

**Attributes:** `cache` label identifying the cache instance. — [CacheMetricsCollector (Guava)](https://prometheus.github.io/client_java/api/io/prometheus/metrics/instrumentation/guava/CacheMetricsCollector.html) and [Micrometer PR #78 (Caffeine initial support)](https://github.com/micrometer-metrics/micrometer/pull/78)

#### Community Recommendation (Uptrace, 2026)

Uptrace recommends:
- **Observable Counters** (read via periodic callback, not incremented inline):
  - `cache.hits` — Int64ObservableCounter, unit `"1"`
  - `cache.misses` — Int64ObservableCounter, unit `"1"`
  - `cache.errors` — Int64ObservableCounter, unit `"1"` (optional)
- **Or consolidated** via one counter `cache.stats` with a `type` attribute (`"hits"`, `"misses"`, `"errors"`).
- **Derived metrics** (computed at query time): Cache hit rate = `cache.hits / (cache.hits + cache.misses)`.

— [Monitoring cache stats using OpenTelemetry Go Metrics](https://uptrace.dev/blog/opentelemetry-go-metrics-cache-stats) (Uptrace, 2026)

#### Microsoft .NET / HybridCache (2025)

HybridCache (Microsoft.Extensions.Caching.Hybrid, .NET 9+) emits `System.Diagnostics.Metrics` but the specific metric names are not fully documented in public releases. Microsoft's guidance is to use `System.Diagnostics.Metrics` and let exporters translate to OTel semconv — naming is delegated to the exporter, not standardized in HybridCache itself. — [HybridCache in ASP.NET Core](https://learn.microsoft.com/en-us/aspnet/core/performance/caching/hybrid?view=aspnetcore-10.0) (Microsoft Learn, 2025)

### 3. OpenTelemetry General Naming Guidance (Governing Rules)

**Pluralization:** Metric names should be **pluralized only when the unit itself is a countable thing** (e.g., `{hits}`, `{misses}`, `{operations}`). Examples:
- ✅ `system.paging.faults` (unit is `{faults}`)
- ✅ `system.disk.operations` (unit is `{operations}`)
- ✅ `cache.hits` (unit is `{hits}`)
- ❌ `system.filesystems.utilization` (should be singular; utilization is a ratio, not a countable unit)

— [OpenTelemetry Naming Conventions](https://opentelemetry.io/docs/specs/semconv/general/naming/) (OTel Spec v1.44.0, August 2026)

**Units:** UCUM (Unified Code for Units of Measure) format, case-sensitive. Examples: `s` (seconds), `ms` (milliseconds), `By` (bytes), `1` (dimensionless/unitless). **Units belong in the instrument definition, not the metric name.** Avoid `cache.latency_ms`; use `cache.duration` with `.WithUnit("ms")` instead. — [Metrics Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/) (OTel Spec v1.44.0, August 2026)

**Instrument choice:** 
- **Counter:** Monotonically increasing (e.g., total hits, total misses, evictions).
- **Histogram:** Distribution of durations/sizes (e.g., `cache.duration`, `cache.item_size`).
- **UpDownCounter:** Can go up and down (e.g., current cache size, active lease count).
- **Observable** (gauge-like, read-only): When stats are already tracked elsewhere (e.g., polled from cache stats object), not incremented inline.

— [Metrics Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/)

**Attributes vs. separate metrics:** Do not embed attribute values in metric names. Use tags/attributes for differentiation. Example:
- ❌ `cache.hits` and `cache.misses` (two separate metrics)
- ✅ `cache.operations{result=hit|miss}` (one metric, one attribute, more composable)
- ✅ `cache.hits` and `cache.misses` (acceptable under OTel naming rules, since the unit itself changes; see Micrometer and Prometheus precedent)

— [OpenTelemetry Metrics: Concepts, Types & Instruments](https://www.checklyhq.com/blog/opentelemetry-metrics/) (Checkly, 2025) citing OTel spec guidance on metric composition

### 4. Attribute/Label Cardinality Guidance

**High-cardinality attributes are forbidden** in metrics. Each unique attribute value combination creates a separate time series (TSDB series), consuming memory and storage.

**Safe attributes** (bounded cardinality, <<100 unique values):
- Cache name / instance id (enumerable, pre-configured)
- Operation type (e.g., `"get"`, `"put"`, `"evict"`)
- Cache layer / tier (e.g., `"L1"`, `"L2"`, `"distributed"`)
- Backend type (e.g., `"redis"`, `"memcached"`, `"in-memory"`)

**Forbidden attributes** (unbounded/high cardinality):
- Cache key / record id (millions of unique keys)
- Tenant id (if also in traces/logs; if in metrics, reserved for single-tenant deployments)
- User id / session id (millions of unique identities)
- Request id / correlation id (unique per request)
- URL path / any user-supplied string

**Cardinality limits:** OpenTelemetry metrics SDK enforces a default **cardinality limit of 2000 unique attribute combinations per metric stream** (configurable via View). Exceeding this limit drops subsequent combinations silently or via explicit overflow handling. — [Metric cardinality limits in OpenTelemetry: a practical guide](https://opentelemetry.io/blog/2026/cardinality-limits-in-opentelemetry/) (OpenTelemetry, August 2026)

**Prometheus guidance:** Every label (attribute) should be answerable by enumeration. If you cannot list all possible values at design time and that list is stable, it is not a label. High-cardinality labels are described as a "cardinality bomb." — [How to Create Prometheus Label Best Practices](https://oneuptime.com/blog/post/2026-01-30-prometheus-label-best-practices-1/) (OneUptime, January 2026)

## Options Compared

| Aspect | Option A: Separate Counters | Option B: One Counter + Attributes | Option C: Observable Counters |
|--------|---|---|---|
| **Metric Names** | `cache.hits`, `cache.misses`, `cache.evictions` (one name per result type) | `cache.operations{result=hit\|miss\|evict}` (one name, split by attribute) | `cache.stats{type=hits\|misses\|evictions}` (polled, not incremented) |
| **Instrument Type** | Counter (synchronous) | Counter (synchronous) | ObservableCounter (asynchronous, callback-based) |
| **Unit** | `"1"` per instrument | `"1"` per instrument | `"1"` per instrument |
| **Attribute Cardinality** | Very low (no dimensions) | Low (3–5 fixed values per attribute) | Low (same as B) |
| **Advantages** | ✅ Simple, intuitive naming. ✅ Each metric tells one story. ✅ Micrometer precedent. ✅ Prometheus ecosystem convention. | ✅ More composable for queries. ✅ Aligns with OTel guidance on attributes vs. names. ✅ Reduces metric count in UI. | ✅ Zero overhead if stats already computed elsewhere (no inline counter increments). ✅ Reflects how caches often expose stats (get total, hit count from a `stats()` call). ✅ Reduces lock contention on hot path. |
| **Disadvantages** | ❌ Metric proliferation (many names in UI). ❌ Deriving hit rate requires post-processing. ❌ Harder to add new result types later. | ❌ Less familiar to ops teams trained on separate-metric pattern. ❌ Slightly more complex queries (must group by attribute). | ❌ Less common in application instrumentation. ❌ Requires callback infrastructure. ❌ Familiarity gap if team expects synchronous counters. |
| **OTel Alignment** | ✅ Aligns with pluralization rule (unit is `{hits}`, `{misses}`). ✅ Precedent in semconv (e.g., `system.paging.faults`, `system.disk.operations`). | ✅ Aligns with guidance on attributes-not-values. ✅ Cleaner metric namespace overall. | ✅ Aligns with OTel metric types. ❌ No guidance either way; less aligned with semconv patterns. |
| **Evidence** | ✅ Prometheus/Caffeine/Guava (Java ecosystem). ✅ Micrometer. | ✅ Micrometer **also supports** this via function-tracking (one `cache.gets` counter, split by `result` tag in some versions). ✅ OTel naming theory. | ✅ Uptrace 2026 recommendation. ✅ Community best practice when stats already tracked. |

## Version/Compatibility Notes

- **OpenTelemetry semantic conventions:** v1.44.0 (August 2026). No cache metrics semconv exists. When one lands (future release), varco should re-evaluate names to align.
- **Micrometer:** v1.13+ (Java). Supports both separate-metric and one-metric-with-attributes patterns.
- **Prometheus client (Java):** Caffeine/Guava/Cache2k support via `CacheMetricsCollector` (stable naming for years).
- **Microsoft .NET:** HybridCache, .NET 9+ (2025). Metric names delegated to exporters, not standardized.
- **OpenTelemetry Go:** OTel Go SDK v1.0+ (2023). Observable instruments stable.
- **OpenTelemetry Python:** OTel Python SDK v1.0+ (2023). Observable instruments stable.

## Evidence Gaps

1. **No OTel semconv cache metrics OTEP** filed or merged as of August 2026. The gap is documented but unfilled.
2. **Microsoft HybridCache public metric names** are not fully documented—implementation details hidden, exporter-delegated.
3. **Whether varco should emit latency as a Histogram or UpDownCounter for duration** is not settled by semconv. Consensus (Prometheus/Java/OTel) favors Histogram + implicit `_bucket`, `_count`, `_sum` suffixes, but specific guidance is sparse.
4. **Cache eviction semantics** (synchronous vs. background, per-cache vs. global) are not clarified in de facto practice. Most implementations count evictions as one counter; none distinguish explicit vs. TTL expiry vs. capacity-driven eviction.
5. **Stampede coalescing metrics** (waiting coroutines on a shared cache miss, request coalescing) are not defined in any standard or vendor implementation surveyed.

## Librarian's Note

**What the sources indicate:**

Varco should **adopt Option A (separate counters) as the primary pack**, aligned with Prometheus/Caffeine/Guava/Micrometer precedent and OTel pluralization rules. Metric names:
- `cache.hits` (Counter, unit `"1"`, no attributes)
- `cache.misses` (Counter, unit `"1"`, no attributes)
- `cache.evictions` (Counter, unit `"1"`, optional attribute: `cache.layer` ∈ {`"L1"`, `"L2"`, …})
- `cache.size` (UpDownCounter, unit `By` if size-measured, `"1"` if item-count)
- `cache.duration` (Histogram, unit `"ms"` or `"us"`, optional attribute: `cache.operation` ∈ {`"get"`, `"put"`, `"delete"`})

**Rationale:**
1. **Proven in production** across three major ecosystems (Prometheus, Micrometer, Guava).
2. **OTel-compliant** under pluralization rules (the unit itself is `{hits}`, `{misses}`, not a singular counter).
3. **Low cardinality** if attributes are strictly bounded (cache layer, operation type, backend).
4. **Simple mental model** for operators: one metric = one story.
5. **Semconv migration path** clear: if/when OTel stabilizes cache metrics, realign names; varco's attribute names are already semconv-adjacent.

**Migration note:** Should OTel release a cache metrics semconv in a future release, varco must re-evaluate and potentially rename. This brief should be revisited once OTel #1747 or a successor OTEP reaches Approved status.

