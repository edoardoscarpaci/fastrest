# BACKLOG

Feature backlog produced by `/discover` (focus: caching, internationalization/localization,
time zones).

**Stated priority** (user, this session): **B** — close feature gaps to make varco competitive
as a published framework; then **A** — harden what's shipped for real multi-pod production;
then **C** — respond to concrete needs from apps built on top.

**Release shape** (user, this session): **two releases**. R1 is a near-term *correctness /
hardening* cut against already-shipped code. R2 is the *feature* cut that delivers the
feature-matrix win. Ordering within each bucket is by severity, then complexity ascending.

**Research briefs backing this backlog:**

- `design/cache-layer/research/001-mature-async-cache-2026.md` — what a mature async Python
  caching layer is expected to ship in 2026 (aiocache, cashews, Django, Spring/Caffeine,
  groupcache/ristretto, .NET HybridCache, Redis client-side caching, OTel semconv status).
- `design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md` —
  backend-framework i18n obligations (Django, Rails, Spring, ASP.NET Core, NestJS) and
  timezone/temporal best practice (RFC 9457, RFC 9557, RFC 4647, PEP 615, Quartz, K8s
  CronJob `spec.timeZone`).

---

## R1 — Hardening (correctness on already-shipped code)

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| C2 | Singleflight / stampede protection — coalesce concurrent misses on the same key into one recompute | 🔴 must | M | Today N concurrent requests for a cold hot key all reach the database. Ranked the single most critical gap in the cache brief. | [cache brief](design/cache-layer/research/001-mature-async-cache-2026.md) — standard in Go groupcache, .NET HybridCache, Spring; **absent from every Python async cache library** (aiocache, cashews) |
| C1 | `LayeredCache` L1 coherence backplane — cross-node L1 invalidation | 🔴 must | L | Not a missing feature but a latent correctness bug in shipped code: under multi-pod each pod's L1 silently serves stale entries after another pod invalidates. Discussion: a known stale-read bug damages a published framework's reputation more than a missing feature does. | [cache brief](design/cache-layer/research/001-mature-async-cache-2026.md) — .NET HybridCache treats the backplane as first-class; Redis 6/7 client-side caching + RESP3 invalidation push is the transparent option, Python adoption lags |
| C3 | Cache observability pack — hit/miss ratio, latency, eviction counters through the existing OTel layer | 🟡 should | S | You cannot tune C1/C2 without measuring them; wires into the existing global-attribute registry. | [cache brief](design/cache-layer/research/001-mature-async-cache-2026.md) — Micrometer's `cache.hits`/`misses`/`evictions` is the de-facto shape. ⚠️ **No OTel semantic convention for cache metrics exists yet** — varco would be setting its own names |
| C4 | Stale-while-revalidate (soft/hard TTL) + TTL jitter + negative caching | 🟡 should | M | Same problem family as C2 ("what happens on a miss under load") — designing them apart usually means redoing one. Jitter prevents synchronized expiry cliffs; negative caching stops repeated misses hammering the DB. | [cache brief](design/cache-layer/research/001-mature-async-cache-2026.md) — SWR is bundled industry practice (Spring, Fastly, CDN layer); negative caching ranked the #2 gap |

## R2 — Features (the feature-matrix win)

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| X1 | Request-scoped context primitive — generic ambient context with the precedence chain, composing with `tenant_context()` | 🔴 must | S | Extracted as its own item so I2 and T1 are thin consumers rather than two divergent copies of the same precedence chain. Blocks I2 and T1; keeps the tz track from being blocked on I2's catalog-format decision. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) — request-scoped ambient context is the shared shape across Django, Rails, and Spring; brief flags async/`contextvars` hazards |
| I1 | Localizable error taxonomy — `message_key` + structured `params` on every varco exception, alongside the existing stable `VARCO_XXXX` code | 🔴 must | M | Highest-leverage i18n move: makes client-side localization possible with zero catalog infrastructure, and nudges the error envelope toward RFC 9457. Foundation I2 renders from. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) — modern consensus is stable code + params with the human `detail` secondary; RFC 9457 supports both postures |
| I2 | `MessageCatalog` ABC + `Accept-Language` negotiation middleware + locale resolution | 🔴 must | L | User chose full server-side rendering over codes-only: a framework claiming i18n support without a catalog reads as thin against Django/Rails/Spring/ASP.NET. ⚠️ **Catalog format is deliberately still open** (Babel/gettext vs ICU MessageFormat vs Fluent) — first thing `/plan` must settle, and the main reason this is an L not an M. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) — extract → catalog → per-request language context is universal across all five surveyed frameworks; RFC 4647 lookup-matching; precedence: explicit param > user profile > tenant default > `Accept-Language` > fallback |
| T1 | Per-user / per-tenant timezone resolution — IANA zone resolved once per request, ambient via X1 | 🟡 should | M | Datetimes are aware-UTC everywhere today but nothing is tz-*aware*: there is no per-tenant or per-user zone concept at all. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) — Django `timezone.activate`, Rails `Time.zone`, Spring `LocaleContextHolder` all ship this; `zoneinfo`/PEP 615 is the standard, pytz deprecated |
| T2 | DST-safe scheduling — store wall-clock time + IANA zone for future scheduled events, not a bare UTC instant | 🟡 should | M | "Store UTC" is wrong for *future* events: a job scheduled for 09:00 local silently drifts an hour twice a year, and a tzdata update moves it again. ⚠️ Touches `Job.run_at` and therefore the framework `varco_jobs` table — needs a revision in the framework Alembic branch, and the semantics of an existing field shift even if the change is additive. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) — ranked ~90% decision-ready; RFC 9557 (IXDTF, Oct 2024) standardizes the format; Quartz, Temporal.io, and K8s CronJob `spec.timeZone` all reached the same conclusion |
| T3 | Query-layer datetime coercion contract — declare the tz assumption for filter strings | 🟢 nice | S | `?created_at__gte=2026-01-01` silently means UTC today; a user in UTC+9 gets a nine-hour-wrong window with no error. | [i18n/tz brief](design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md) flags query coercion as the one item needing a spike — exact shape is `intuition` |
| C5 | Bulk `get_many`/`set_many` + pluggable serializer | 🟢 nice | S | Round-trip amplification on list endpoints; serialization is backend-hardcoded today. Deferred out of R1 as the least urgent cache gap. | [cache brief](design/cache-layer/research/001-mature-async-cache-2026.md) — table stakes in aiocache/Django/Spring, ranked medium priority |

---

## Parked

| Feature | Why parked |
|---------|------------|
| **Per-locale content storage** — translated *data* (per-locale entity fields / translation tables on `DomainModel`) | App-level, not framework-level. The i18n/tz brief flags this explicitly: Rails and Django both leave it to third-party gems (globalize, django-parler) rather than core. At XL it would dominate the release, and it is a different problem from UI/message localization. Revisit only if a concrete app (priority C) actually needs it. |
| **Locale context only, no messages** (i18n option considered) | Rejected in favour of full server-side rendering (I1 + I2). Ship the seam *and* the content. |
| **Codes-only i18n** (i18n option considered) | Rejected for the same reason — small and additive, but reads as incomplete support to anyone comparing feature matrices, which conflicts with the stated priority B. |
| **Merging locale + timezone into one `RequestContext` item** | Rejected in favour of "shared foundation first" (X1). One combined item would have blocked the tz track behind I2's catalog-format decision. |

---

## Open questions for `/plan`

- **I2 catalog format**: Babel/gettext vs ICU MessageFormat vs Fluent. The brief carries evidence
  on all three including Python tooling maturity. Settle this first — it is what makes I2 an L.
- **C1 backplane mechanism**: application-level pub/sub invalidation vs Redis RESP3 client-side
  caching push. The latter is more transparent but implies a Redis version floor, which changes
  varco's dependency story.
- **C3 metric names**: no OTel cache semconv exists, so varco picks its own. Aligning with
  Micrometer's names now is cheaper than renaming after a semconv lands.
- **T2 migration path**: how `Job.run_at`'s existing UTC-instant semantics coexist with the new
  wall-clock + zone representation without breaking in-flight jobs.
