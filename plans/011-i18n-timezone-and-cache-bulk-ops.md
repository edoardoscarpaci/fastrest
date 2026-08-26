# Plan 011 — I18n, Timezones, and Cache Bulk Ops (R2)

> Executes the **R2 "Features"** cut of `BACKLOG.md`: **X1** (request-scoped
> ambient context primitive), **I1** (localizable error taxonomy), **I2**
> (`MessageCatalog` + `Accept-Language` negotiation), **T1** (per-user /
> per-tenant timezone resolution), **T2** (DST-safe scheduling), **T3**
> (query-layer datetime coercion contract), **C5** (bulk `get_many`/`set_many`
> + pluggable cache serializer).
>
> **Authoritative inputs** (read before implementing any phase):
> - `design/i18n-tz-framework/research/001-internationalization-and-timezone-handling.md` — brief 001 (survey: RFC 4647/9110/9457/9557, PEP 615, precedence chain, UTC vs wall-clock)
> - `design/i18n-tz-framework/research/002-python-message-catalog-formats-2026.md` — brief 002 (**settles I2's catalog format**)
> - `design/i18n-tz-framework/research/003-rfc9457-localizable-error-envelope.md` — brief 003 (**settles I1's wire format**)
> - `design/i18n-tz-framework/research/004-dst-safe-scheduling-and-tz-aware-queries.md` — brief 004 (**settles T2 and T3**)
>
> **Posture: features, but off by default.** Same convention as
> `TenancySettings()` → `SHARED` (Plan 007), `MigrationSettings.mode="off"`
> (Plan 006), `reliability=None` (Plan 009), `CachePolicy()` (Plan 010). Every
> item below states its exact no-configuration behaviour, and RD-1 tabulates
> them. There is **one** deliberate wire-format delta in the whole plan (D-4),
> with a named kill switch.

## Goal

After this plan:

1. There is **one** ambient request-scoped context primitive
   (`varco_core.context`), with one precedence-resolution helper, that I2 and T1
   are thin consumers of rather than two divergent copies. It composes with —
   and deliberately does not duplicate — `tenant_context()` and
   `correlation_context()`.
2. Every built-in varco exception carries a stable, legible `message_key`
   (`varco.error.not_found`) plus structured `params`, **alongside** the
   existing stable numeric `code`. Clients can localize with zero server-side
   catalog infrastructure. The envelope moves toward RFC 9457 additively.
3. A pluggable `MessageCatalog` ABC with a zero-new-dependency stdlib-`gettext`
   default renders `title`/`detail` server-side, driven by an RFC 4647 Lookup
   negotiation over the precedence chain
   `?lang=` → user profile → tenant default → `Accept-Language` → fallback.
4. A per-request IANA timezone is resolved once, ambient via X1, and available
   to services, handlers, and the query layer.
5. A future scheduled `Job` can be stored as wall-clock + IANA zone with an
   explicit, documented DST gap/overlap policy — **without** changing the
   semantics of the existing `run_at` claim predicate.
6. The query layer's datetime coercion has a **declared** timezone contract
   instead of an accidental one, with the safe default being byte-identical to
   today's behaviour.
7. `AsyncCache` implementations can serve list endpoints with one round trip
   (`get_many`/`set_many`/`delete_many`) and a swappable `Serializer`, composing
   with Plan 010's `read_through()`, `Singleflight`, `CacheEnvelope`, and
   `LayeredCache` backplane rather than bypassing them.

## Non-goals

- **Per-locale content storage** (translatable entity tables /
  `ArticleTranslation` / django-parler-style mixins). Explicitly **Parked** in
  the backlog; brief 001's Librarian's note also puts it on the app side of the
  framework/app line ("❌ Message authoring, translatable entity design").
- **Codes-only i18n** (ship stable codes, refuse to render server-side).
  Explicitly **Parked** in the backlog — the backlog chose full server-side
  rendering for I2. The hybrid in D-3 keeps the codes-only *client* path fully
  supported; what is out of scope is *only* shipping that.
- **Recurring / RRULE scheduling.** Brief 004 §A5 is explicit: the wall-clock +
  zone model is the same for one-shot and recurring, but recurrence needs a
  separate RRULE expansion model outside the current `Job` scope. T2 is one-shot
  only. See D-7.
- **A recurring-schedule store, a cron parser, or `Schedule`/`Trigger` entities.**
- **Message/catalog authoring, extraction tooling, or shipped translations.**
  varco ships the *machinery* and zero `.po` files. `pybabel extract` is
  documented, not vendored.
- **PyICU / MessageFormat 2.0 / Fluent implementations.** All three are
  documented extension points behind the `MessageCatalog` ABC; none ship. See
  D-1.
- **RFC 9557 (IXDTF) parsing.** No production-ready Python parser exists
  (brief 004 §A4 + Evidence Gap 1). varco ships an RFC 9557 *emitter* helper
  only. See D-9.
- **A media-type switch to `application/problem+json` by default.** Brief 003's
  migration section is unambiguous that this is the breaking move; it stays
  opt-in. See D-3.
- **Renaming the `FASTREST_*` error code strings.** See D-5.
- **Rewriting `_current_tenant` / `_correlation_id` onto the new X1 primitive.**
  They keep their exact current implementation; X1 documents them as the
  precedent it generalizes. A later consolidation is a pure refactor with no
  user-visible change and is not worth coupling to this plan.
- **Distributed / cross-pod anything.** No new backplane verbs, no new
  distributed primitives.
- **Localizing log lines, span attributes, or metric labels.** Localization is a
  response-rendering concern only. Locale must never become a metric attribute
  (cardinality — Plan 010 D-2's deny-list rule still binds).

---

## Decisions

The backlog handed `/plan` three open questions (I2 catalog format, T2 migration
path, T3 exact shape). All three are settled below, each citing the brief that
settles it, so the implementer does not re-litigate them. Two further decisions
(D-5, D-10) correct claims in the backlog that turned out to be wrong about the
current codebase — both were verified directly against the source.

### D-1 — I2 catalog format: **stdlib `gettext` at runtime, Babel as the authoring toolchain**, behind a pluggable ABC

**Settled — this is the backlog's first open question.**

Ship `MessageCatalog` (ABC) + three implementations in `varco_core`:
`NullMessageCatalog` (default, returns nothing → English fallback),
`DictMessageCatalog` (in-memory, the test/small-app default), and
`GettextMessageCatalog` (production default, **stdlib `gettext` only**).

- **Fluent is not viable.** `fluent.runtime` is at **0.4.0 (March 2023)**,
  classified Alpha, and supports **Python 3.6–3.9 only** — varco requires
  `>=3.12`. Brief 002 §"Fluent (Python)": *"Not viable as varco's default due to
  Python 3.12+ incompatibility and stalled maintenance."* Rejected outright.
- **MessageFormat 2.0 is standardized but immature in Python.** The spec is
  stable (Unicode CLDR TC, UTR-35) but the only Python implementation
  (`messageformat2`) is at **0.1.x**, and **no major Python framework has
  adopted MF2** — brief 002 §"MessageFormat 2.0". Adopting it as the *default*
  would bet a published framework's message format on a 0.1.x library.
  Rejected as default; kept as a documented ABC implementation the day it
  matures.
- **PyICU is viable but a heavy default.** Wheels for 3.12/3.13 exist
  (2.15.2+, March 2026) and it is the only route to gender/ordinal/complex
  selectors — brief 002 §"ICU in Python". But it drags `libicu` into every
  varco install for a capability most services never use. Brief 002's
  Librarian's note puts it exactly here: *"Should be offered as a documented
  alternative (not in varco core…)"*. Rejected as default; documented as the
  first ABC implementation to write when you need it.
- **Babel is the ecosystem's answer, but only half of it is a runtime concern.**
  Brief 002's Librarian's note: *"Default MessageCatalog implementation: Babel +
  gettext is the clear choice"* — mature, active (2.18.0, Feb 2026, CLDR 47),
  and what the entire FastAPI i18n ecosystem (`fastapi-babel`, `fastapi-i18n`)
  and Django already use. **But** the runtime lookup is pure stdlib
  `gettext.GNUTranslations`; Babel's contribution is `pybabel
  extract/init/compile` — a *build-time* tool. So `GettextMessageCatalog` reads
  `.mo` files with the standard library and adds **zero runtime dependencies to
  `varco_core`**, while the docs point at `pybabel` for authoring. This satisfies
  brief 002's recommendation and CLAUDE.md's "`varco_core` gets contracts, zero
  new third-party deps" rule simultaneously.
- **Thread/async safety is designed around, not assumed.** Brief 002 §"stdlib
  gettext" is clear that `.mo` loading is one-time and post-load lookup is a
  dict read; the reported hazard is *Flask-Babel's `force_locale` leaking across
  requests* (issue #117), i.e. a **process-global "active locale"** pattern.
  varco therefore has **no global `activate()`** — the locale lives only in X1's
  request-scoped `ContextVar`, and the catalog object is immutable after
  `start()`. Brief 002's Evidence Gaps 1 & 2 (Babel `Locale` concurrency, async
  catalog sharing) are discharged by a required concurrency test (step 30) and
  are listed in Risks as ⚠️ ASSUMPTION.
- **`.mo` loading is blocking file I/O** and therefore happens in
  `I18nLifecycle.start()`, never lazily on the first request inside the event
  loop.

### D-2 — RFC 4647 **Lookup** is hand-rolled, no new dependency

Brief 002 §"RFC 4647 Language Matching": **no standard Python library implements
Lookup (§3.4)**. `language_tags` does BCP 47 validation, not matching; WebOb
implements Basic *Filtering* (§3.3.1), not Lookup, and is a heavyweight HTTP
utility library to pull in for one function. Brief 002's Librarian's note:
*"hand-roll the simple Lookup variant (~40 lines) … No need to bring in a new
dependency for this."*

`varco_core/i18n/negotiation.py` therefore ships ~60 lines: parse
`Accept-Language` (comma split, `q=` extraction, invalid-`q` tolerance,
descending stable sort), then per candidate progressively truncate at `-`
boundaries (`fr-CA-x-foo` → `fr-CA` → `fr`), skipping single-character
subtags per RFC 4647 §3.4, and return the first tag present in
`supported_locales`. `*` matches the default locale. Empty/absent header → no
match (fall through to the next precedence step, not to `en` directly).

- ✅ Zero new dependency in a package whose whole value proposition is a thin
  contract layer.
- ✅ Testable against the RFC's own worked examples.
- ❌ We own the correctness. Mitigated by a table-driven test suite (step 27)
  seeded from RFC 4647 §3.4's examples.

### D-3 — I1 wire format: **additive hybrid RFC 9457**, `message_key` + `params` as inline extension members

**Settled.** Brief 003's Librarian's note, adopted essentially verbatim:

1. Always-present, backward-compatible: existing `code` and `message` stay
   exactly as they are.
2. New inline extension members: `message_key: str | None`,
   `params: dict[str, Any]`.
3. RFC 9457 members (`type`, `title`, `detail`, `instance`, `status`) are
   emitted **only** when problem-details mode is switched on.
4. Field-level validation: an `errors: [ {field, message, message_key, params} ]`
   array — brief 003 §"Validation Errors" shows this shape is the de-facto
   standard across Spring Boot 3, ASP.NET Core `ValidationProblemDetails`, and
   `fastapi-problem-details`. RFC 9457 itself standardizes nothing here.
5. Media type stays `application/json`; `application/problem+json` is an opt-in
   flag.

Why inline rather than nested under a `localization` / `error_info` object:
brief 003 §"Message Key & Params Naming" compares all three and recommends
Option 1 — *"simplest, most RFC 9457-compliant (direct extension members),
matches Spring's precedent."*

Why additive rather than a media-type switch: brief 003 §"Backward
Compatibility" + §"Spring Boot 3 & ASP.NET Core … Migration Experience".
Spring Boot shipped `ProblemDetail` behind `spring.mvc.problemdetails.enabled`
defaulting to **`false`** precisely to avoid breaking existing clients, and
brief 003's Options table scores full adoption as **⚠️ High** breaking-change
risk vs **✅ Low** for additive. varco mirrors Spring exactly:
`VARCO_ERROR_PROBLEM_DETAILS=false` by default.

RFC 9457 explicitly permits all of this: `type`/`instance` must stay stable,
`title`/`detail` **may** be localized via `Accept-Language`, and *"Clients
consuming problem details MUST ignore any extensions they don't recognize"* —
brief 003 §"RFC 9457: Normative Members".

### D-4 — the **one** deliberate wire delta, and its kill switch

Everything else in this plan is byte-identical by default. I1 is not, and
pretending otherwise would be dishonest.

For a **built-in** varco exception, the JSON error body gains up to two keys:
`message_key` and `params`. Nothing is removed, renamed, or reordered.

Bounded precisely:

- `message_key` and `params` are emitted **only when non-empty**
  (`exclude_none` on `message_key`, `if params` on `params`). An out-of-tree
  `ServiceException` subclass that does not set `message_key` gets a
  **byte-identical** body — the new fields never appear.
- `ErrorEnvelopeSettings(include_message_key=True, include_params=True)`
  (`VARCO_ERROR_INCLUDE_MESSAGE_KEY=false` / `VARCO_ERROR_INCLUDE_PARAMS=false`)
  restores the pre-plan body exactly, for every exception, in one env var.

Why default them **on** rather than off, breaking this plan's own convention:
I1's entire stated value is *"makes client-side localization possible with zero
catalog infrastructure."* A `message_key` that must be switched on delivers none
of that to anyone who doesn't read the release notes, and brief 003 is explicit
that extension members are the safe, spec-sanctioned way to add exactly this.
The cost is bounded to "a test that asserts an exact-equality error body must be
updated", which is precisely why the kill switch exists and why this decision is
called out here, in Risks, and in the CLAUDE.md pitfall table rather than buried
in a changelog line.

### D-5 — the error codes are **`FASTREST_001`…`FASTREST_500`**, and they are **not renamed**

⚠️ **The backlog's `VARCO_XXXX` naming does not exist.** Verified directly:
`varco_core/varco_core/exception/codes.py:141-175` defines
`code="FASTREST_001"` … `code="FASTREST_500"` on an enum literally named
`FastrestErrorCodes` (`codes.py:106`). The Plan 003 fastrest→varco naming
refactor left them behind. `varco_fastapi/varco_fastapi/exceptions.py:113` even
documents the response shape as `{"code": "VARCO_XXXX", ...}` — that docstring
is **wrong today** and is fixed by this plan.

**Decision: the `code` *string values* do not change.**

- `ErrorCode.code` is documented in three places as *"the stable i18n
  translation key — never change it after release"* (`codes.py:63`,
  `codes.py:91`, `http.py:98`). Renaming a value whose entire contract is
  stability, in order to make it prettier, is exactly the change the contract
  forbids. Brief 003 §"RFC 9457: Normative Members" puts the same rule on the
  wire: the machine-readable identifier *"Must remain stable; never localized"*.
- Any client keying a translation table or an alerting rule off `FASTREST_001`
  breaks silently on rename — and silently is the operative word, because a
  renamed code produces a valid-looking response with an unrecognized value.
- The pretty, legible, *namespaced* identifier that people actually want is
  **`message_key`** — `varco.error.not_found` — and I1 is precisely the feature
  that adds it. The numeric code keeps being the stable machine identifier; the
  dotted key becomes the i18n key. This resolves the naming complaint without
  breaking anything, and it corrects the mis-statement in `codes.py:63`/`:183`
  that the *numeric* code is the i18n key (it is a poor one — opaque, and
  `FastrestErrorCodes("FASTREST_001")` does not even resolve, per the documented
  edge case at `codes.py:133`).

Three cosmetic, non-breaking moves are in scope:

- `VarcoErrorCodes = FastrestErrorCodes` — a module-level **alias to the same
  enum object** in `varco_core/exception/codes.py`, exported from
  `varco_core.exception` and `varco_core`. Because it is the identical class
  object, `isinstance`, identity comparison, `list(...)`, and every existing
  import keep working. `FastrestErrorCodes` is **not** deprecated at runtime (no
  `DeprecationWarning`), matching how `JwtUtil.SYSTEM_ISSUER` was handled in
  Plan 002.
- Docstring corrections in `codes.py` and `http.py`: `code` is the *stable
  machine identifier*; `message_key` is the *i18n key*.
- `varco_fastapi/exceptions.py:113`'s `"VARCO_XXXX"` example is corrected to a
  real body.

Rejected alternative — **an `ErrorCodeStyle` setting emitting either
`FASTREST_001` or `VARCO_001`**: ✅ lets an app opt into the nicer name.
❌ makes the *stable identifier* configuration-dependent, so two deployments of
the same framework version emit different codes for the same condition, and any
shared client-side mapping is now ambiguous. That is strictly worse than either
renaming or not. Rejected.

### D-6 — X1 shape: **one generic `AmbientVar[T]` primitive + one aggregate `RequestContext`**; tenant stays where it is

X1 ships two things, not N context variables:

1. `AmbientVar[T]` — a ~70-line generic wrapping `ContextVar[T | None]` with
   `.get()`, `.scope(value)` (sync `@contextmanager`, token-reset in `finally`),
   and `.ascope(value)` (`@asynccontextmanager`). This is the *generalization*
   of the two implementations that already exist and agree:
   `tenant_context()` (`service/tenant.py:163-198`, sync CM, token reset) and
   `correlation_context()` (`tracing.py:128-165`, async CM, token reset).
2. `RequestContext` — a `@dataclass(frozen=True)` with `locale: str | None`,
   `timezone: ZoneInfo | None`, and `extras: Mapping[str, str]`, held in exactly
   **one** `AmbientVar[RequestContext]`, with `current_request_context()` and a
   `request_context(**overrides)` CM that **merges** with the enclosing context
   (setting a locale must not blank an already-resolved timezone).

Why one aggregate rather than one `ContextVar` per concern:

- ✅ One middleware pass, one token, one reset. Two independent vars means two
  tokens whose reset order must be right, and four states to test instead of
  one.
- ✅ Merge-on-nest semantics are defined once, in one place, and are what makes
  "the timezone middleware runs after the locale middleware" a non-event.
- ✅ Adding a third request-scoped concern later (currency? unit system?) is a
  field, not a new module-level global.
- ❌ A consumer that only wants the locale still reads the whole record.
  Irrelevant — it is a frozen dataclass attribute read.

**Tenant is deliberately absent from `RequestContext`.** `current_tenant()`
stays the single source of truth. Two places to ask "who is the tenant" is how
they diverge, and `tenant_context()` is already load-bearing across
`TenantAwareService`, `tenancy_cache_key()`, RLS, the DLQ tenant stamp, and the
audit trail. `RequestContext`'s docstring states this and points at
`current_tenant()`. Composition is by *ordering* (the tenant middleware runs
before the localization middleware, so tenant-default lookup works), not by
containment.

`precedence.py` ships `resolve_precedence(candidates) -> Resolved[T] | None`,
a pure function over an ordered `Sequence[tuple[str, T | None]]` returning the
first non-`None` value **plus the name of the source that supplied it**. The
source name is not decoration: it is what turns "why is this user getting
German?" from a debugging session into one DEBUG log line, and it is what the
locale and timezone resolvers both emit. This is the "thin consumers rather than
two divergent copies of the same precedence chain" the backlog asked for.

---

### D-7 — T2 storage: **three additive columns on `Job`**; `run_at` keeps its exact current meaning; no RRULE

**Settled — this is the backlog's second open question.**

`Job` gains three defaulted fields, next to the Plan 005 Phase 4 block at
`varco_core/varco_core/job/base.py:249-280`:

```python
run_at_wall: datetime | None = None  # naive local wall-clock, no tzinfo
run_at_tz: str | None = None  # IANA zone name, e.g. "America/New_York"
run_at_fold: int = 0  # PEP 495 fold, disambiguates the overlap
```

`run_at: datetime | None` (`job/base.py:249`) **keeps its exact current
semantics**: the materialized UTC instant that is the claim predicate, compared
against the *database's* `now()`. Nothing about `claim_next`, `try_claim`,
`JobPoller`, or any store's SQL changes.

This is the whole migration answer, and it is worth stating as a rule:

> **`run_at` is materialized, not replaced.** `(run_at_wall, run_at_tz,
> run_at_fold)` is the *intent*; `run_at` is the *materialization* of that
> intent under the tzdata available when it was computed. A row with
> `run_at_tz IS NULL` is byte-identical to today in every respect.

Consequences, all of them good:

- **In-flight jobs are unaffected.** Every existing row has
  `run_at_tz IS NULL`, so no code path behaves differently.
- **Both rolling-deploy directions are safe** — unlike Plan 010's cache
  envelope (D-5 there). A *new* pod writing a zoned job also writes a correct
  `run_at`; an *old* pod reading that row sees a normal `run_at` and claims it
  at exactly the right instant, simply without the ability to re-materialize.
  There is no two-step deploy requirement for T2.
- **No new index.** The claim predicate is unchanged, so the existing
  `run_at`/status index is still the right one. (Called out explicitly because
  "additive columns" usually implies "and an index", and here it does not.)

Brief 004's evidence for wall-clock + zone is unanimous — its Options table
scores "Wall-clock + zone (dual storage)" ✅ on DST safety and ✅ on preserving
user intent, against ❌/❌ for UTC-only, backed by Quartz, Kubernetes CronJob
(`spec.timeZone`, GA in v1.27), Google Calendar, and RFC 5545. Its
recommendation is literal: *"Store `(wall_clock_datetime, iana_zone_string,
utc_instant)` as three columns."*

**Recompute-on-read** (brief 004 §A3, the recommended discipline for preserving
intent across tzdata churn) cannot be a SQL predicate. It ships as an **opt-in
component**: `ScheduleRematerializer` (`varco_core/job/reschedule.py`) sweeps
pending zoned jobs inside a bounded horizon, recomputes `run_at` from
`(wall, tz, fold)` under *current* tzdata, and writes back **only when the value
actually changed**, fenced with `save(expected_epoch=…)` (Plan 005 Phase 4).
Default interval `0.0` = **not started** → byte-identical. Without it, varco is
brief 004's "store-instant-accept-drift" model, which is what varco already is.

**Additive columns on `Job`, not a separate `Schedule` concept.** ✅ Reuses the
entire existing lease/fencing/retry/DLQ apparatus, which a parallel concept
would have to duplicate or bridge. ✅ The claim path stays one predicate on one
column. ❌ `Job` grows to a wide dataclass. Accepted — brief 004 §A5 says
explicitly that *"the distinction is not in the data model"* between one-shot
and recurring; what recurrence needs is an *expansion* model (RRULE), and that
is a Non-goal here. A separate `Schedule` entity is the right home for RRULE
**later**, and it will produce `Job` rows exactly like these ones.

### D-8 — DST gap/overlap policy: hand-rolled detection, and a **deliberate deviation** from brief 004 on the gap default

`varco_core/tz/schedule.py` ships:

```python
def datetime_exists(wall: datetime, zone: ZoneInfo) -> bool: ...
def datetime_ambiguous(wall: datetime, zone: ZoneInfo) -> bool: ...
def resolve_zoned(
    wall, zone, *, fold=0, gap=GapPolicy.NEXT_VALID, overlap=OverlapPolicy.FIRST
) -> datetime: ...
```

**No `dateutil` dependency.** Brief 004 §A2 demonstrates detection with
`dateutil.tz.datetime_exists` / `datetime_ambiguous`, but both are ~8 lines over
stdlib `zoneinfo`: a time is *ambiguous* iff `utcoffset(fold=0) !=
utcoffset(fold=1)`; a time is *nonexistent* iff round-tripping it through UTC
and back does not reproduce it. Adding a runtime dependency to `varco_core` for
sixteen lines violates the package's stated contract-layer-with-no-third-party-
deps rule. Same reasoning as D-2.

**Overlap (fall back): run once at `fold=0`.** Adopted verbatim from brief 004's
Librarian's note §3 — *"Store with fold=0 (default): use the first occurrence."*
`OverlapPolicy.LAST` (`fold=1`) is available. Note the contrast recorded in
brief 004 §A1: Quartz fires for **both** ambiguous occurrences. Firing a
one-shot job twice is a duplicate side effect; firing it once is not. Once wins.

**Gap (spring forward): default `NEXT_VALID`, not `SKIP` — deviating from brief
004.** Brief 004's Librarian's note §2 recommends *"skip"* as the default,
matching Quartz/Kubernetes. That recommendation is correct **for a recurring
occurrence**, where skipping one 02:30 firing out of 365 is a rounding error and
the job runs again tomorrow. For a **one-shot** `Job` — which is all T2 covers
(D-7) — "skip" means the job is *never executed and never fails*, i.e. it sits
`PENDING` forever or vanishes: silent data loss, in a framework whose
`OutboxRelay(max_attempts=…)` refuses to construct without a `dlq=` specifically
to avoid silent data loss.

So: `GapPolicy.NEXT_VALID` (default) rolls a nonexistent wall time forward to
the first valid instant after the gap (03:00 in brief 004's worked example) and
logs one WARNING naming the job, the zone, and both times.
`GapPolicy.PREVIOUS_VALID` rolls backward. `GapPolicy.SKIP` is available and,
when chosen, transitions the job to a **terminal** state with a named
`ScheduleGapError` rather than leaving it pending — skipping is allowed, but
never silently. `GapPolicy.ERROR` refuses at enqueue time.

This deviation is recorded in the feature doc next to the brief-004 citation so
it reads as a decision rather than a misreading.

### D-9 — RFC 9557 is an **output format**, never a storage model or a parser

Brief 004 §A4 + Evidence Gap 1: **no production-ready Python RFC 9557/IXDTF
parser was found.** `whenever` shows the shape in examples without documenting
compliance; `dateutil.isoparse` and stdlib `datetime` do not support the bracket
suffix. Brief 004's Librarian's note is direct: *"Do not wait for a
production-ready RFC 9557 parser… RFC 9557 is a serialization format, not the
storage model."*

Therefore:

- Storage is the three columns of D-7. Full stop.
- `varco_core/tz/format.py` ships `format_rfc9557(instant, zone) -> str`
  producing `2026-03-08T09:00:00-05:00[America/New_York]` — a ~10-line
  f-string over `isoformat()`, for API responses and logs.
- **No parser ships.** An input that arrives with a bracket suffix is rejected
  by the datetime coercer with a legible error naming the two supported inputs
  (RFC 3339 with offset, or a separate `tz=` field). Writing our own parser for
  an unadopted format, in a plan that already owns six other items, is how a
  plan grows a seventh.
- The feature doc carries a "when a parser lands" note: it becomes an additive
  branch in the coercer, not a storage change — which is the point of keeping
  the storage model independent of the wire format.

### D-10 — T3 default is **`assume="naive"`** (today's exact behaviour), `"utc"` is recommended, `"context"` is opt-in

**Settled — this is the backlog's third open question, and it needs a
correction first.**

⚠️ **The backlog says `?created_at__gte=2026-01-01` "silently means UTC today."
It does not.** Verified: `coerce_datetime()`
(`varco_core/varco_core/query/visitor/type_coercion.py:79-106`) returns
`datetime.fromisoformat(value)` **unchanged** — for a naive input string it
returns a **naive** `datetime`, with no `tzinfo` attached at all. The UTC
interpretation is not varco's; it is whatever the *database session* decides
(Postgres coerces a naive literal against a `TIMESTAMPTZ` column using the
session `TimeZone`, which is usually but not necessarily UTC). The user-visible
symptom the backlog describes is real; the mechanism is one layer lower than
stated, and that changes what a safe default is.

The contract ships as a frozen `DatetimeCoercionPolicy`:

```python
assume: Literal["naive", "utc", "context"] = "naive"
log_naive: bool = True  # one DEBUG line per coerced naive bound
```

- **`"naive"` (default) — byte-identical to today.** The value is returned
  exactly as `fromisoformat` produced it. Chosen as the default because
  `"utc"` is *not* a no-op: attaching `tzinfo=UTC` changes what the driver
  sends, and asyncpg **rejects an aware datetime against a `TIMESTAMP WITHOUT
  TIME ZONE` column**. Making `"utc"` the default would turn a working query
  into a runtime error for every app with a naive timestamp column — the exact
  class of silent-upgrade breakage this repo's default-off convention exists to
  prevent.
- **`"utc"` — the recommended setting**, and what the feature doc, the CLAUDE.md
  section, and the `DatetimeCoercionPolicy` docstring all tell you to turn on.
  Brief 004 §B3 and its Options table both put "assume UTC" as the correct
  API-layer default (Google Cloud, AWS, Azure), and brief 004's Librarian's note
  for T3 opens with *"Default: Assume UTC."* varco agrees with the *destination*
  and disagrees only about whether a published framework may arrive there
  without the operator's consent.
- **`"context"` — opt-in per-user timezone**, reading X1's
  `current_timezone()`. Brief 004 §B1 is emphatic that **no mainstream framework
  does this** (*"Varco would be pioneering this if implemented"*) and its
  Librarian's note gates it behind an explicit flag with a DEBUG log per
  coercion. Both conditions are honoured. With no ambient timezone resolved,
  `"context"` falls back to `"utc"` and logs.

Two rules that hold under **every** policy, both from brief 004 §B2/§B3:

1. **An explicit offset always wins.** If the parsed value is already aware
   (`2026-01-01T00:00:00Z`, `…-05:00`), it is used verbatim and no policy is
   applied. This is the portable, always-correct client behaviour and the docs
   recommend it above all the above.
2. **Convert the bound, never the column.** varco emits
   `WHERE created_at > <utc bound>`, never `WHERE created_at AT TIME ZONE 'UTC'
   > …` — the latter defeats the index (brief 004 §B2). Nothing in this plan
   generates an `AT TIME ZONE` expression; the feature doc states this as an
   invariant so a future contributor does not "fix" the coercer by moving the
   conversion into SQL.

Date-only bounds get an explicit documented semantic: `2026-01-01` is midnight
at the start of that day in the assumed zone, so `__lte=2026-01-01` excludes
almost all of January 1st. Named in the Edge cases table because it is the
single most common datetime-filter bug in every API.

### D-11 — C5: a **separate `BulkCache` Protocol**, not new methods on `AsyncCache`; reuse the existing `Serializer`

`AsyncCache` (`varco_core/varco_core/cache/base.py:66-173`) is
`@runtime_checkable`. A `runtime_checkable` Protocol's `isinstance()` check
tests **method presence**, so adding `get_many`/`set_many`/`delete_many` to it
would make `isinstance(some_third_party_cache, AsyncCache)` start returning
`False` for every out-of-tree implementation — a silent, action-at-a-distance
break in code nobody edited. That is a worse defect than the one C5 fixes.

So:

- `AsyncCache` is **unchanged**. Not one line.
- New `BulkCache` Protocol (`runtime_checkable`) in `cache/base.py` with
  `get_many` / `set_many` / `delete_many`.
- `CacheBackend` (the ABC every shipped backend subclasses,
  `cache/base.py:179`) gains the three methods as **concrete, portable
  defaults** that loop over `get`/`set`/`delete`. Every shipped backend
  therefore satisfies `BulkCache` immediately, correctly, and with today's
  performance; each backend then *overrides* with its native batch command
  (`MGET`/pipelined `SET`, `get_multi`/`set_multi`) as an optimization, not a
  correctness fix.
- Call sites (`read_through_many`, `CacheServiceMixin.list()`) choose the batch
  path via `isinstance(cache, BulkCache)`, falling back to the loop. This is the
  same "portable default vs. concrete-but-raising" discipline Plan 009 applied
  to `AbstractDeadLetterQueue`/`AbstractJobStore` — here every method has a
  correct portable default, so **nothing is concrete-but-raising**.
- **The serializer is `varco_core.serialization.Serializer`**
  (`serialization.py:54-122`) — the existing `runtime_checkable` Protocol with
  `serialize(value) -> bytes` / `deserialize(data, type_hint=None) -> T`, whose
  docstring already names *"`JsonSerializer` for cache values"* as its motivating
  case. Inventing a second serializer protocol for the cache would be the
  "implement the same interface twice" mistake CLAUDE.md's pre-implementation
  checklist exists to catch. `CacheBackend.__init__` takes
  `serializer: Serializer[Any] | None = None`; each backend's default preserves
  its current behaviour exactly (`RedisCache` → `JsonSerializer`,
  `MemcachedCache` → its current bytes codec, `InMemoryCache` →
  `NoOpSerializer`, i.e. raw Python objects).

### D-12 — C5 × Plan 010: `set_many` publishes **N per-key backplane messages**; the wire format is not extended

`LayeredCache.set_many()` under a wired `CacheBackplane` obeys Plan 010's rule 2
verbatim — **authoritative (last) layer first, then faster layers and publish**
— and emits one `InvalidationMessage(kind="key", …)` per key.

Rejected alternative — **a new `kind="keys"` carrying a list**: ✅ one Pub/Sub
message instead of N. ❌ Plan 010 froze that wire format *deliberately*
("adding a field to it later would be a second, avoidable rolling-deploy
hazard", Plan 010 D-5), and a Plan-010-era subscriber receiving `kind="keys"`
drops it as undecodable — meaning a mixed-version fleet silently loses
invalidations, which is precisely the bug Plan 010 C1 was written to fix.
N cheap messages beat a coherence regression. Recorded in the feature doc so a
future batched-invalidation change is a deliberate versioned rollout.

`read_through_many()` reuses the **same** `Singleflight` instance as
`read_through()`, with one in-flight slot **per key**, so a bulk read and a
single read of the same key coalesce with each other rather than racing. The
envelope is unwrapped per key, negative entries short-circuit per key, and the
loader is invoked once with **only** the missing keys. Plan 010's tenant rule
still binds and is retested here: the coalescing key is the final,
already-namespaced cache key.

---

## Requirement decisions

### RD-1 — the default-off matrix

With no configuration, no new env vars, and no new constructor arguments, every
item is inert. This table is the acceptance criterion for "byte-identical by
default" and each row has a named test in the Steps.

| Item | No-configuration behaviour | Proof |
|---|---|---|
| **X1** | Nothing constructed, no middleware added. `current_request_context()` returns an empty `RequestContext`; `current_locale()`/`current_timezone()` return `None`. Existing `tenant_context()`/`correlation_context()` untouched. | step 6 |
| **I1** | `ErrorCode` gains a defaulted trailing field (positional construction unaffected). Built-in exceptions gain a `message_key`/`params` **class attribute**, no `__init__` change. Body gains ≤2 keys for built-ins; **byte-identical** for any exception without a `message_key`. Kill switch: `VARCO_ERROR_INCLUDE_MESSAGE_KEY=false`. **This is the one delta — D-4.** | steps 14, 18 |
| **I2** | `I18nSettings.enabled=False`. No catalog constructed, no middleware, no lifecycle component, no `.mo` read, no `Content-Language` header. `error_message_for()` with no resolver returns `default_message`, exactly as today. | step 32 |
| **T1** | `TimezoneSettings.enabled=False`. No resolution, `current_timezone()` is `None`, no header read, `datetime.now(timezone.utc)` everywhere unchanged. | step 39 |
| **T2** | Three `None`/`0`-defaulted `Job` fields; three nullable columns. `run_at_tz IS NULL` → identical claim behaviour. `ScheduleRematerializer` interval `0.0` → never started. No new index. | steps 45, 53 |
| **T3** | `DatetimeCoercionPolicy(assume="naive")` → `coerce_datetime()` returns exactly what it returns today. | step 58 |
| **C5** | `AsyncCache` unchanged; `BulkCache` is new and additive; `CacheBackend`'s bulk defaults are loops over today's methods; every backend's default serializer reproduces its current bytes. `read_through`/`CacheServiceMixin` take the batch path only when a caller opts in. | steps 63, 70 |

### RD-2 — **no tenant-catalog schema change** for locale/timezone defaults

`TenantDescriptor` (`varco_core/varco_core/tenancy/catalog.py:41-63`) has
`tenant_id`/`schema`/`database`/`dsn_ref`/`status` and no locale or timezone.
Adding fields there means changing `varco_tenants` — the tenth framework table
(Plan 007) — and therefore an Alembic revision, a Beanie document change, and a
migration obligation for every existing deployment, for a value most tenants
will never set.

Instead: `TenantDefaultsProvider` — a `runtime_checkable` Protocol in
`varco_core/context/defaults.py` with
`async def defaults_for(tenant_id: str) -> TenantLocalizationDefaults` (a frozen
dataclass of `locale: str | None`, `timezone: str | None`). Ships with
`NullTenantDefaults` (returns both `None`, the default) and
`StaticTenantDefaults(mapping)`. Apps that keep tenant preferences in their own
table implement it in ten lines; apps that don't pay nothing. Resolution is
wrapped in the app's own cache if it needs one — varco does not cache it
implicitly, because an implicit per-tenant cache with no invalidation is a
support ticket.

The feature doc explains how to back it with the tenant catalog *if* an app
wants to, and why varco doesn't do it for them. Brief 003 Evidence Gap 5 and
brief 001 Evidence Gap 6 both flag per-tenant i18n as unresearched — a Protocol
is the right amount of commitment to an unresearched question.

### RD-3 — **one** `LocalizationMiddleware`, two independent toggles

I2 and T1 both need "read the request, resolve a value, put it in
`RequestContext`, unset it after". Two middlewares means two ASGI passes, two
`ContextVar` tokens whose nesting must be right, and two places for the same
`?lang=`-vs-header precedence bug.

`varco_fastapi/middleware/localization.py::LocalizationMiddleware` resolves
locale and/or timezone in one pass, sets **one** merged `RequestContext` token,
and resets it in `finally`. Each half is independently switched by
`I18nSettings.enabled` / `TimezoneSettings.enabled`; with both off the
middleware is not added to the stack at all.

It is inserted **after** `TenantResolutionMiddleware` in request order, because
the tenant-default step of both precedence chains needs `current_tenant()` to be
populated. Concretely, in `create_varco_app`'s reverse-order `add_middleware`
block (`varco_fastapi/varco_fastapi/app.py:60-68`), the resulting request order
becomes:

```
CORS → Error → Tracing → Metrics → RequestLogging → RequestContext
     → Session → [TenantResolution] → [Localization] → handler
```

`ErrorMiddleware` stays **outside** it, which is correct and load-bearing: the
error renderer reads the locale from the `ContextVar`, and a `ContextVar` set by
an inner middleware is visible to an outer one **only** if the inner one has not
yet reset it. Since the exception propagates out through
`LocalizationMiddleware`'s `finally`, the token is already reset by the time
`ErrorMiddleware` formats the body. Therefore: `LocalizationMiddleware`
**stashes the resolved `RequestContext` on `request.state`** as well as in the
`ContextVar`, and the error path reads `request.state` first,
`current_request_context()` second. This is a real ordering hazard, it is the
kind of thing that produces "errors are localized in the handler but not in the
404 handler", and it gets a dedicated test (step 34).

### RD-4 — layer boundaries

- `varco_core.context`, `varco_core.i18n`, `varco_core.tz` are **contracts +
  stdlib only**: `contextvars`, `zoneinfo`, `gettext`, `dataclasses`, `pydantic`
  (already a `varco_core` dependency via `VarcoSettings`). **No new third-party
  runtime dependency is added to any package by this plan.**
- `varco_fastapi` imports **only** `varco_core.context` / `.i18n` / `.tz` /
  `.exception` — never `varco_sa`, `varco_beanie`, `babel`, or `PyICU`. Same
  seam as `AbstractEventBus`, `AbstractMigrator`, and `varco_core.tenancy`.
- T2's persistence lands in `varco_sa` (Alembic revision + model columns) and
  `varco_beanie` (document fields); the *policy* (`resolve_zoned`, the gap and
  overlap enums, `ScheduleRematerializer`) is in `varco_core`.
- C5's bulk contract is in `varco_core.cache`; the native batch commands are in
  `varco_redis` / `varco_memcached`.

### RD-5 — a store must **declare** zoned-schedule support; enqueueing a zone into a store that can't hold it is refused

`AbstractJobStore` gains `supports_zoned_schedules: ClassVar[bool] = False`.
`SAJobStore`/`BeanieJobStore`/`InMemoryJobStore` set it `True` in the phase that
adds their columns/fields. `AbstractJobRunner.enqueue(..., tz=…)` and
`Job`-construction helpers raise `ValueError` naming the store class when a
zone is supplied to a store that has not declared support.

This is the out-of-tree-subclass answer, and it is the same failure mode Plan
005 Phase 4 hit with `try_claim(owner_id=, lease_ttl=)` (CLAUDE.md pitfall:
*"External `AbstractJobStore` subclass breaks on `lease_ttl`"*). An out-of-tree
store that maps columns explicitly will **silently not persist**
`run_at_wall`/`run_at_tz` — the job still runs at the right instant (because
`run_at` was materialized at enqueue) but re-materialization silently no-ops, so
the DST safety the caller asked for quietly isn't there. Failing closed at
enqueue turns a silent degradation into a startup-time error naming the exact
class to fix. A store that splats the dataclass into fixed columns will raise on
the unknown columns, which is loud and fine.

### RD-6 — locale and timezone are **never** implicit cache-key components

A response body rendered in `fr` cached under a key that does not mention `fr`,
and then served to an `en` client, is the i18n analogue of the cross-tenant
cache leak in CLAUDE.md's pitfall table — and it is *easier* to hit, because
localization is applied at render time, far from the cache call.

varco does **not** silently namespace cache keys by locale (that would change
every existing key and cold-start every cache). Instead:

- `varco_core/i18n/cache_key.py::localization_cache_key(base, *, locale=True,
  timezone=False)` composes the ambient locale/timezone into a key, mirroring
  `tenancy_cache_key()`'s shape and failing closed the same way (a `locale=True`
  request with no ambient locale raises `RuntimeError`, it does not silently
  omit the segment).
- A pitfall row and a paragraph in both feature docs.
- The rule is stated where it bites: **cache the unlocalized representation and
  localize at render time** wherever possible; namespace by locale only when the
  cached artifact is itself localized.

### RD-7 — framework responsibility line

Straight from brief 001's and brief 002's Librarian notes, and enforced by the
Non-goals: varco owns the `MessageCatalog` ABC + a default implementation,
content negotiation, request-scoped locale/timezone context, error-code
stability, and the `message_key` taxonomy. varco does **not** own message
authoring, catalog authoring, translation management, or translatable-entity
design.

---

## Design

### The spine: one ambient context, five consumers

```
                    ┌──────────────────────────────────────────┐
                    │  varco_core.context  (X1 — Phase 0)      │
                    │                                          │
                    │   AmbientVar[T]  ── ContextVar + scope()  │
                    │   RequestContext(locale, timezone, extras)│
                    │   resolve_precedence([(src, val), …])     │
                    │        → Resolved(value, source)          │
                    └───────┬───────────────────────┬──────────┘
                            │                       │
        ┌───────────────────┴────┐        ┌─────────┴───────────────┐
        │ I2 locale (Phase 2)    │        │ T1 timezone (Phase 3)   │
        │  ?lang=                │        │  ?tz= / X-Timezone      │
        │  user profile (JWT)    │        │  JWT `zoneinfo` claim   │
        │  tenant default        │        │  tenant default         │
        │  Accept-Language       │        │  settings default       │
        │    (RFC 4647 Lookup)   │        │  UTC                    │
        │  settings fallback     │        └─────────┬───────────────┘
        └───────────┬────────────┘                  │
                    │                     ┌─────────┴───────────────┐
                    │                     │ T3 query coercion       │
                    │                     │  assume="context"       │
                    │                     └─────────────────────────┘
                    │
        ┌───────────┴──────────────────────────────────────────┐
        │ I1 error envelope (Phase 1) — message_key + params    │
        │   error_message_for(exc, message_resolver=…)          │
        │        ↳ MessageCatalog.format_message(key, loc, prm) │
        └───────────────────────────────────────────────────────┘

   Independent of the spine:  T2 (Phase 4, job scheduling)
                              C5 (Phase 6, cache bulk ops)
```

`tenant_context()` sits **beside** this box, not inside it (D-6): the tenant
middleware runs first and both precedence chains read `current_tenant()` for
their tenant-default step.

### X1 — `AmbientVar` and the precedence resolver

```python
class AmbientVar(Generic[T]):
    def __init__(self, name: str, *, default: T | None = None) -> None: ...
    def get(self) -> T | None: ...
    def set_for_task(self, value: T) -> Token[T | None]: ...  # explicit token API
    @contextmanager
    def scope(self, value: T) -> Iterator[T]: ...
    @asynccontextmanager
    async def ascope(self, value: T) -> AsyncIterator[T]: ...
```

- The `ContextVar` is created **at module/`__init__` scope**, which is correct
  and is *not* the CLAUDE.md lazy-lock rule: `ContextVar` construction requires
  no running event loop (unlike `asyncio.Lock`), and PEP 567 in fact requires
  module-level creation for `ContextVar`s to be usable across tasks. The
  docstring says so explicitly, because "everything must be lazy" is the wrong
  lesson to generalize from the lock rule.
- `scope()`/`ascope()` always `reset(token)` in `finally`, matching
  `tenant_context()` and `correlation_context()` exactly.
- ASGI note in the docstring: a value set inside a middleware is visible to
  everything *inside* it and is gone once its `finally` runs — see RD-3's
  `request.state` mirror.

```python
@dataclass(frozen=True)
class Resolved(Generic[T]):
    value: T
    source: str

def resolve_precedence(candidates: Sequence[tuple[str, T | None]]) -> Resolved[T] | None
```

Pure, synchronous, no I/O, no logging — the *caller* logs
`Resolved.source`. Async sources (a tenant-default lookup) are awaited by the
caller before building the candidate list; keeping the resolver sync means it is
trivially testable and usable from the error-rendering path, which must not
await anything.

### I1 — where `message_key` lives, and why nothing's constructor changes

```
ServiceException                      ErrorCode (frozen)
  message_key: ClassVar[str|None]       code / http_status / default_message
  error_params() -> dict[str, Any]      + message_key: str | None = None   ← new, trailing, defaulted
        ▲                                        ▲
        │  overridden by                         │  set on each FastrestErrorCodes member
        │                                        │
  ServiceNotFoundError                    NOT_FOUND       → "varco.error.not_found"
    "varco.error.not_found"               UNAUTHORIZED    → "varco.error.unauthorized"
    {"entity": "Post", "entity_id": "42"} CONFLICT        → "varco.error.conflict"
  ServiceAuthorizationError               VALIDATION_ERROR→ "varco.error.validation_failed"
    "varco.error.unauthorized"            INTERNAL_ERROR  → "varco.error.internal"
    {"operation": "delete", "entity": …}
```

**No `__init__` signatures change.** `message_key` is a `ClassVar` and
`error_params()` is a method returning `{}` on the base. An out-of-tree
`ServiceException` subclass compiles, runs, and serializes exactly as before,
and gets no new JSON keys (D-4). This is the whole reason the design is a
class attribute rather than a constructor parameter — `ServiceException`
subclasses forward `*args, **kwargs` to `Exception.__init__`
(`service.py:71`, `:128`), which accepts no keywords, so any new constructor
keyword would be a real breaking change for out-of-tree subclasses that forward
positionally.

`error_params()` deliberately excludes anything sensitive:
`ServiceAuthorizationError.reason` is **not** in its params, preserving the
documented rule at `service.py:98` that `reason` never reaches a client. A test
asserts this (step 16) because a params dict is exactly the kind of thing
someone helpfully dumps `vars(exc)` into.

Key resolution order, in `error_message_for()`:
`type(exc).message_key` → `error_code.message_key` → `None`.

`error_message_for()` gains **one keyword-only parameter**:

```python
def error_message_for(
    exc, *,
    translator: Callable[[str], str] | None = None,          # unchanged, still works
    message_resolver: MessageResolver | None = None,         # new
) -> ErrorMessage
```

`MessageResolver` is `Callable[[str, Mapping[str, Any]], str | None]` —
`(message_key, params) -> rendered | None`. Returning `None` means "no
translation available", and the caller falls back to `default_message`, so a
missing catalog entry can never produce an empty error message. `translator=`
(which takes the *code*, per `http.py:213`) keeps working with no
`DeprecationWarning`, documented as superseded — same treatment as
`JwtUtil.SYSTEM_ISSUER`.

`ErrorMessage` (`http.py:88`) gains, all defaulted and all omitted from the
serialized body when empty:

| Field | Type | Emitted when |
|---|---|---|
| `message_key` | `str \| None = None` | non-`None` **and** `include_message_key` |
| `params` | `dict[str, Any] = {}` | non-empty **and** `include_params` |
| `errors` | `list[FieldError] = []` | non-empty |
| `type` / `title` / `detail` / `instance` | RFC 9457 members | `problem_details=True` |

`detail` already exists and already carries `str(exc)`; under problem-details
mode it keeps that meaning, which happens to be exactly RFC 9457's ("explanation
specific to this occurrence").

### I2 — the catalog ABC and the negotiation chain

```python
class MessageCatalog(abc.ABC):
    @abc.abstractmethod
    def get_message(self, key: str, locale: str) -> str | None: ...
    def format_message(
        self, key: str, locale: str, params: Mapping[str, Any] | None = None
    ) -> str | None:
        """Concrete default: get_message() + str.format_map with a
        missing-key-tolerant mapping. Override for gettext plurals / ICU."""

    def available_locales(self) -> frozenset[str]: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Brief 002's Librarian's note asks for **both** patterns (template return and
structured-params formatting). This gets both with one abstract method: the
template path is `get_message`, the formatting path is `format_message` with a
working default, and a formatter-backed implementation overrides
`format_message`. Simple implementations write one method.

`format_map` uses a `dict` subclass whose `__missing__` returns
`"{" + key + "}"` — a missing interpolation parameter leaves the placeholder
visible instead of raising `KeyError` inside an exception handler, which is
where this code runs. Rendering an error must never raise.

Implementations:
- `NullMessageCatalog` — `get_message` returns `None`. The default. Zero I/O.
- `DictMessageCatalog(mapping: Mapping[str, Mapping[str, str]])` — `{locale:
  {key: template}}`. The unit-test and small-app catalog; also what the
  feature doc's first example uses, so nobody needs `pybabel` to try the
  feature.
- `GettextMessageCatalog(directory, domain="messages", locales=…)` — stdlib
  `gettext.translation()` per locale, all loaded in `start()`, stored in an
  immutable dict. `format_message` uses `ngettext` when
  `params` contains an integer `count`, giving CLDR plural forms for free
  (brief 002 §"stdlib gettext": *"Works for all CLDR languages (200+ plural
  forms)"*). No process-global `install()`, no `activate()`, no mutable state
  after `start()` — see D-1's Flask-Babel note.

`negotiate_locale(header, supported, *, default)` implements D-2's Lookup.
`resolve_locale()` composes the chain via X1's `resolve_precedence`:

| # | Source name | Where it comes from |
|---|---|---|
| 1 | `query_param` | `?lang=fr-CA` (name configurable) |
| 2 | `user_profile` | `AuthContext`'s `locale` claim (OIDC Core 5.1 standard claim) via `extra_claims` |
| 3 | `tenant_default` | `TenantDefaultsProvider.defaults_for(current_tenant())` (RD-2) |
| 4 | `accept_language` | RFC 4647 Lookup over the header |
| 5 | `fallback` | `I18nSettings.default_locale` (`"en"`) |

This is the backlog's stated chain. It differs from brief 002's Librarian
ordering (which lists stored preference before `?lang=`) and follows brief 001
§"Precedence hierarchy", which groups explicit user choice first: an explicit
`?lang=` is a deliberate, per-request override — a user switching the language
picker must not be overruled by a stale stored profile. Recorded in the feature
doc so the deviation is visible.

Only locales in `supported_locales` are ever returned; an unsupported explicit
`?lang=` falls through to the next step and logs one DEBUG line (it does not
400 — a bad language hint must not fail a request).

`Content-Language` is set on every response when i18n is enabled, including when
the resolved locale is the fallback — brief 003 §"Content Negotiation": *"Always
return a `Content-Language` response header identifying the language of the
response (even if it's the default fallback)."*

### T1 — timezone resolution

Same machinery, different chain:

| # | Source name | Where it comes from |
|---|---|---|
| 1 | `query_param` | `?tz=America/New_York` |
| 2 | `header` | `X-Timezone` (brief 004 §B3 names exactly this header) |
| 3 | `user_profile` | `AuthContext`'s **`zoneinfo`** claim (OIDC Core 5.1 standard claim name — *not* `tz`) |
| 4 | `tenant_default` | `TenantDefaultsProvider` (RD-2) |
| 5 | `fallback` | `TimezoneSettings.default_timezone` (`"UTC"`) |

Every candidate passes `validate_iana_zone()` (`ZoneInfo(name)` inside a
`try`, `KeyError`/`ZoneInfoNotFoundError` → reject) **before** entering the
candidate list, so a garbage `?tz=Mars/Olympus` falls through to the next source
with one WARNING rather than 500-ing. `ZoneInfo` objects are cached by the
stdlib, so resolution is a dict lookup after first use.

Helpers, all pure: `current_timezone() -> ZoneInfo | None`,
`to_user_tz(instant) -> datetime` (UTC-aware in, ambient-zone-aware out;
identity when no zone is resolved), `now_local() -> datetime`.

**varco does not change what it *stores*.** Everything keeps being written as
aware-UTC (`datetime.now(timezone.utc)`). T1 is a *rendering and interpretation*
context, per brief 001's rule that immediate/past events are UTC instants. The
feature doc states this plainly so nobody starts writing local times into
`created_at`.

`tzdata` packaging (brief 001 §"Python: zoneinfo"): `zoneinfo` reads the OS
tzdata and falls back to the PyPI `tzdata` package. Slim container images
(`python:*-slim`, distroless, Alpine) frequently have neither. `TimezoneSettings`
validation at startup resolves `default_timezone` and raises a legible error
naming `pip install tzdata` if the database is absent — a startup failure, never
a per-request one. `tzdata` is added as an **optional extra**
(`varco-core[tz]`), not a hard dependency, since most deployments already have
system tzdata.

### T2 — the scheduling model, end to end

```
 enqueue(run_at_wall=2026-03-08 02:30, tz="America/Los_Angeles")
        │
        │  resolve_zoned(wall, zone, fold=0,
        │                gap=NEXT_VALID, overlap=FIRST)     ← D-8
        │      gap detected → 03:00 local, WARNING logged
        ▼
 Job(run_at_wall=2026-03-08 02:30,     ← intent, stored verbatim
     run_at_tz="America/Los_Angeles",
     run_at_fold=0,
     run_at=2026-03-08 11:00Z)          ← materialization, the claim predicate
        │
        │  (unchanged) claim_next / try_claim compare run_at to DB now()
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ ScheduleRematerializer  — OPT-IN, interval 0.0 = off         │
 │   every N seconds, for pending jobs with run_at_tz IS NOT    │
 │   NULL and run_at within `horizon` (default 48h):            │
 │     new = resolve_zoned(wall, tz, fold, …)  under CURRENT    │
 │           tzdata                                             │
 │     if new != run_at:  save(job, expected_epoch=…)  + INFO   │
 └─────────────────────────────────────────────────────────────┘
```

The horizon exists so the sweep is bounded: re-materializing a job scheduled for
2030 every 60 seconds for four years is pointless, and the interesting window
for a tzdata change is "jobs about to fire". `list_pending_zoned(before, limit)`
is a new `AbstractJobStore` method with a **portable default** implemented over
`list_by_status(PENDING)` + an in-Python filter, overridden by `SAJobStore` with
a real `WHERE run_at_tz IS NOT NULL AND run_at < :before LIMIT :limit`. Portable
default, not concrete-but-raising — because a correct (if unindexed) fallback
genuinely exists, unlike `renew()`/`reap_expired_leases()` in Plan 005.

Writes are fenced with `expected_epoch=` and a `StaleLeaseError` is caught and
skipped: a job that got claimed between the read and the write is being executed
right now and must not have its schedule rewritten underneath the worker.

### C5 — bulk read-through composed onto Plan 010

```
  CacheServiceMixin.list()  /  @cached(..., bulk=True)  /  direct call
              │
              ▼
  read_through_many(cache, keys, loader, policy, *, type_hint, singleflight)
      1. bulk_get(keys)            → isinstance(cache, BulkCache) ? get_many : loop
      2. per key: unwrap envelope  → fresh / soft-stale / negative / absent
      3. fresh + negative          → returned immediately
      4. soft-stale                → returned NOW + one spawn_refresh per key
                                     through the SAME Singleflight slot
      5. missing/hard-expired      → Singleflight.do(key) per key; the elected
                                     leaders' keys are batched into ONE
                                     loader(missing_keys) -> dict call
      6. wrap + bulk_set(fresh)    → set_many when available
                                     (LayeredCache: authoritative layer first,
                                      then publish N key messages — D-12)
```

Step 5 is the only genuinely new mechanic: `Singleflight.do()` is per key, so
the batch loader must be invoked once for the *set* of keys whose slot this call
leads. `read_through_many` elects leadership per key first, collects the keys it
leads, issues **one** loader call, then fulfils each led future from the result
dict; keys it does not lead are awaited as followers (`asyncio.shield`, per Plan
010). A key absent from the loader's returned dict resolves to `None` — and is
negative-cached iff `policy.negative_ttl` is set (Plan 010 D-4's rule, unchanged
and per key).

### Alternatives considered

- **Two independent `ContextVar`s for locale and timezone (X1)** — ✅ marginally
  simpler each. ❌ Two tokens, two resets, two nesting semantics, and the merge
  bug ("setting the timezone cleared the locale") is invisible until a
  middleware ordering changes. Rejected — D-6.
- **Putting `tenant_id` into `RequestContext`** — ✅ one place to look. ❌ Two
  sources of truth for the single most security-load-bearing ambient value in
  the framework, with `TenantAwareService`, RLS, `tenancy_cache_key()`, the DLQ
  tenant stamp, and the audit trail all reading the *other* one. Rejected — D-6.
- **`message_key` as a constructor parameter on `ServiceException`** — ✅ per
  instance flexibility. ❌ `ServiceException` subclasses forward `**kwargs` to
  `Exception.__init__`, which accepts none; every out-of-tree subclass would
  need updating. A `ClassVar` + `error_params()` override needs zero. Rejected.
- **Full RFC 9457 adoption with `application/problem+json` by default** — ✅
  spec-pure, one format. ❌ Brief 003 scores it **⚠️ High** breaking-change risk
  and documents that Spring Boot shipped it default-`false` for exactly this
  reason. Rejected — D-3; available behind one flag.
- **Renaming `FASTREST_*` → `VARCO_*`** — see D-5.
- **Babel as a runtime dependency of `varco_core`** — ✅ CLDR-backed number/date
  formatting for free. ❌ A hard third-party dependency in the contracts package
  for a capability the *catalog lookup* does not need; the lookup is stdlib
  `gettext`. Babel stays a documented build-time tool. Rejected — D-1.
- **`pydantic-i18n` for validation-error localization** (brief 003 §"Python/
  FastAPI Ecosystem") — ✅ ready-made pydantic message catalogs. ❌ A second
  catalog mechanism with its own format sitting beside `MessageCatalog`, for one
  error family. Instead, pydantic error `type`s are mapped to
  `varco.validation.<type>` keys and rendered through the **same** catalog.
  Rejected as a dependency, adopted as a naming convention.
- **A `Schedule`/`Trigger` entity for T2 instead of `Job` columns** — ✅ clean
  home for future RRULE. ❌ Duplicates or bridges the entire lease/fencing/
  retry/DLQ apparatus for a feature whose Non-goal is recurrence. Rejected —
  D-7; it is the right home for RRULE *later*, producing `Job` rows exactly like
  these.
- **Replacing `run_at` with `(wall, tz)` as the source of truth** — ✅ one
  representation. ❌ The claim predicate would have to evaluate zone arithmetic
  in SQL, per row, on the hot polling path, on both Postgres and MongoDB, and
  every existing store and index would break. Rejected — D-7.
- **`assume="utc"` as the T3 default** — ✅ matches brief 004's stated default
  and every public API. ❌ Attaching `tzinfo=UTC` is not a no-op: asyncpg
  rejects an aware datetime against `TIMESTAMP WITHOUT TIME ZONE`, so a working
  query becomes a runtime error on upgrade. Rejected as *default*, adopted as
  *recommendation* — D-10.
- **Rejecting naive datetime filters with a 400** (brief 004's "strict" option)
  — ✅ zero ambiguity. ❌ Breaks every existing varco client's URLs on upgrade.
  Available as `assume="reject"`? **No** — deliberately not shipped; three
  policies is already the size of this decision, and `"utc"` + an explicit
  offset covers the correctness need. Recorded so it isn't re-proposed as an
  oversight.
- **Adding `get_many`/`set_many` to the `AsyncCache` Protocol** — see D-11.
- **A cache-specific serializer protocol** — ❌ `varco_core.serialization.
  Serializer` already exists and its docstring already names cache values as its
  use case. Rejected — D-11.
- **A batched `kind="keys"` backplane message** — see D-12.

---


## Phases

Eight phases. The dependency graph is shallow on purpose:

```
Phase 0 ── X1 (varco_core.context)
   │
   ├──► Phase 1 ── I1 (message_key + params)   ← independent of X1, but
   │                                              Phase 2 needs both
   ├──► Phase 2 ── I2 (MessageCatalog + negotiation)     [needs 0 and 1]
   └──► Phase 3 ── T1 (timezone resolution)              [needs 0]
                        │
                        └──► Phase 5 ── T3 (only `assume="context"` reads T1)

   Phase 4 ── T2 (DST-safe scheduling)    ← decoupled from the i18n track
   Phase 6 ── C5 (bulk cache + serializer) ← decoupled from everything
   Phase 7 ── cross-cutting guards + docs sweep
```

**What can land independently, and where a shortened release should cut:**

- **C5 (Phase 6) is fully decoupled.** It touches `varco_core.cache`,
  `varco_redis`, `varco_memcached` and nothing else — no wire delta, no
  migration, no behaviour-changing env var. Safest thing to be holding if the
  release is cut short; can be merged first, last, or in parallel.
- **T2 (Phase 4) is decoupled from the i18n track.** It needs only the
  *schedule* half of `varco_core.tz` (`schedule.py`, `format.py`), which Phase 4
  creates itself; it does **not** need Phase 3's resolution chain. It is,
  however, the highest-risk phase — a shipped framework table — and gets its own
  migration section below.
- **T3 (Phase 5) is decoupled except for one opt-in branch.** `"naive"` (the
  default, D-10) and `"utc"` need nothing from Phase 3; only `assume="context"`
  reads `current_timezone()`. It is ordered after Phase 3 for coherence, not
  necessity.
- **Phase 1 before Phase 2 is mandatory** — I2 renders the keys I1 introduces;
  shipping the catalog first gives it nothing to look up.
- **Phase 0 before Phases 2 and 3 is mandatory.** Both are, by construction,
  "thin consumers" of `AmbientVar`/`resolve_precedence` (D-6). Writing either
  one's own `ContextVar` first is exactly the divergence X1 exists to prevent.

---

### Phase 0 — X1: the ambient request-context primitive (`varco_core.context`)

Closes **X1**. Zero behaviour change: nothing constructed, no middleware, no
touched code path. Per D-6 this ships `AmbientVar[T]` plus **one** aggregate
`RequestContext`; `_current_tenant` (`service/tenant.py`) and `_correlation_id`
(`tracing.py`) are **not** rewritten onto it — they keep their exact current
implementation and are cited by the new module docstring as the precedent being
generalized.

1. [ ] **create** `varco_core/varco_core/context/ambient.py` (`varco_core`) —
   `AmbientVar(Generic[T])`: `__init__(name, *, default=None)` creating the
   `ContextVar` eagerly, `get()`, `set_for_task()` returning the raw `Token`,
   `scope()` (`@contextmanager`), `ascope()` (`@asynccontextmanager`), both
   `reset(token)` in `finally`. `DESIGN:` block states **why module-scope
   construction is correct and is not a violation of CLAUDE.md's lazy-
   `asyncio.Lock` rule** (PEP 567 requires it; no running loop needed), plus the
   ASGI note that a value set inside a middleware is gone once that middleware's
   `finally` runs (forward-reference to RD-3's `request.state` mirror).
2. [ ] **create** `varco_core/tests/test_context_ambient.py` — failing tests
   first: nested `scope()` restores the outer value; `ascope()` across an
   `await`; a value set inside a spawned `asyncio.Task` is **invisible** to the
   parent (copy-on-spawn, asserted so nobody later "fixes" it); an exception
   inside `scope()` still resets; `get()` with no value returns the constructor
   default; two same-named `AmbientVar`s are independent.
3. [ ] **create** `varco_core/varco_core/context/precedence.py` (`varco_core`) —
   `Resolved(Generic[T])` (`frozen`: `value`, `source`) and
   `resolve_precedence(candidates: Sequence[tuple[str, T | None]]) ->
   Resolved[T] | None`. Pure, sync, **no logging** — the caller logs
   `Resolved.source` (D-6). Docstring: async candidate sources are awaited by
   the caller *before* building the list, precisely so this stays callable from
   the error-rendering path, which must not await.
4. [ ] **create** `varco_core/tests/test_context_precedence.py` — first non-`None`
   wins and reports its source; all-`None` and empty return `None`; a
   falsy-but-not-`None` value (`""`, `0`) **is** selected — the `or`-chain bug
   this function exists to avoid, asserted explicitly.
5. [ ] **create** `varco_core/varco_core/context/request.py` (`varco_core`) —
   `RequestContext` (`frozen`: `locale: str | None = None`, `timezone: ZoneInfo |
   None = None`, `extras: Mapping[str, str]`), the single module-level
   `AmbientVar[RequestContext]`, `current_request_context()` (returns an empty
   `RequestContext`, never `None`), `current_locale()`, `current_timezone()`, and
   `request_context(**overrides)` — a CM that **merges** with the enclosing
   context via `dataclasses.replace`, so setting a locale cannot blank an
   already-resolved timezone (D-6). Docstring states tenant is **deliberately
   absent** and points at `current_tenant()`.
6. [ ] **create** `varco_core/tests/test_context_default_off.py` — **RD-1's X1
   proof.** Nothing configured → `current_request_context()` is empty,
   `current_locale()`/`current_timezone()` are `None`; importing
   `varco_core.context` does not touch `_current_tenant`/`_correlation_id`
   (asserted by identity on the existing `ContextVar` objects);
   `tenant_context()` and `correlation_context()` behave unchanged while a
   `request_context()` is active and vice versa — the D-6 composition test.
7. [ ] **create** `varco_core/varco_core/context/defaults.py` (`varco_core`) —
   **RD-2**: `TenantLocalizationDefaults` (`frozen`: `locale`, `timezone`, both
   `str | None`), `TenantDefaultsProvider` (`runtime_checkable Protocol`,
   `async def defaults_for(tenant_id) -> TenantLocalizationDefaults`),
   `NullTenantDefaults` (the default binding) and `StaticTenantDefaults(mapping)`.
   Docstring carries RD-2's rationale: **no `TenantDescriptor` / `varco_tenants`
   change**, and no implicit caching (an uninvalidated per-tenant cache is a
   support ticket).
8. [ ] **create** `varco_core/varco_core/context/__init__.py`; **modify**
   `varco_core/varco_core/__init__.py` — export `AmbientVar`, `RequestContext`,
   `Resolved`, `resolve_precedence`, `current_request_context`,
   `request_context`, `current_locale`, `current_timezone`,
   `TenantDefaultsProvider`, `TenantLocalizationDefaults`, `NullTenantDefaults`,
   `StaticTenantDefaults`. **Docs, same change:** `ARCHITECTURE.md` gains a
   `varco_core.context` module-map entry; `CLAUDE.md` gains an "Ambient request
   context" subsection whose one rule is *`RequestContext` never holds the
   tenant — `current_tenant()` stays the single source of truth*, plus the
   `ContextVar`-is-not-a-lock note.

---

### Phase 1 — I1: localizable error taxonomy (`message_key` + `params`)

Closes **I1**. Contains the plan's **only** wire delta (D-4) and its kill
switch. Implements D-3 (additive hybrid RFC 9457) and D-5 (`FASTREST_*` values
are **not** renamed).

9. [ ] **modify** `varco_core/varco_core/exception/codes.py` (`varco_core`) —
   `ErrorCode` gains a **trailing, defaulted** `message_key: str | None = None`
   (positional construction unaffected); every `FastrestErrorCodes` member gets
   its `varco.error.*` key; add module-level `VarcoErrorCodes =
   FastrestErrorCodes` (**the identical class object** — no subclass, no
   `DeprecationWarning`, D-5). Correct the docstrings at `codes.py:63`/`:91`:
   `code` is the *stable machine identifier*, `message_key` is the *i18n key*.
10. [ ] **create** `varco_core/tests/test_error_codes_message_key.py` — the
    **anti-rename guard**: a frozen literal table asserting every member's `code`
    string is still `FASTREST_nnn`; every member has a non-`None`, unique,
    `varco.error.`-prefixed `message_key`; `VarcoErrorCodes is
    FastrestErrorCodes`; `isinstance` and `list()` over the alias behave
    identically.
11. [ ] **modify** `varco_core/varco_core/exception/service.py` (`varco_core`) —
    `ServiceException.message_key: ClassVar[str | None] = None` and
    `error_params(self) -> dict[str, Any]` returning `{}`; overrides on
    `ServiceNotFoundError` (`{"entity", "entity_id"}`),
    `ServiceAuthorizationError` (`{"operation", "entity"}` — **never** `reason`),
    `ServiceValidationError`, `ServiceConflictError`. **No `__init__` signature
    changes anywhere** (Design §I1: subclasses forward positionally to
    `Exception.__init__`).
12. [ ] **modify** `varco_core/varco_core/exception/http.py` (`varco_core`) —
    `ErrorMessage` gains `message_key`, `params`, `errors: list[FieldError]`, and
    the RFC 9457 members `type`/`title`/`instance` (all defaulted, all omitted
    when empty); new `FieldError` (`frozen`: `field`, `message`, `message_key`,
    `params`); new `MessageResolver = Callable[[str, Mapping[str, Any]], str |
    None]`; `error_message_for(exc, *, translator=None, message_resolver=None)`
    with key resolution `type(exc).message_key → error_code.message_key → None`
    and a `None` result falling back to `default_message`. `translator=` keeps
    working with no warning (superseded, documented).
13. [ ] **create** `varco_core/varco_core/exception/settings.py` (`varco_core`) —
    `ErrorEnvelopeSettings` (pydantic `BaseSettings`, prefix `VARCO_ERROR_`):
    `include_message_key=True`, `include_params=True`, `problem_details=False`,
    `problem_type_base: str | None = None`. Docstring names D-4 and the exact
    env vars that restore the pre-plan body.
14. [ ] **create** `varco_core/tests/test_error_envelope.py` — **the D-4 delta
    and its kill switch** (RD-1's I1 proof, half 1). A built-in exception's body
    gains exactly `message_key` + `params` and nothing else, key-for-key against
    a literal expected dict; an out-of-tree `ServiceException` subclass with no
    `message_key` produces a **byte-identical** body; `include_message_key=False`
    + `include_params=False` restores the pre-plan body for **every** built-in;
    `params={}` is omitted, not emitted as `{}`.
15. [ ] **create** `varco_core/tests/test_error_message_resolver.py` — resolver
    returning `None` falls back to `default_message` (never an empty message); a
    resolver rendering with params; a resolver that **raises** is swallowed and
    falls back (rendering an error must never raise); `translator=` still
    receives the *code*, not the key.
16. [ ] **create** `varco_core/tests/test_error_params_no_secrets.py` — the
    Design's named guard: `ServiceAuthorizationError.reason` is absent from
    `error_params()` and from the serialized body; a parametrized sweep asserts
    no built-in's params contain `reason`/`password`/`token`/`secret` keys. This
    exists because a params dict is exactly what someone helpfully fills with
    `vars(exc)`.
17. [ ] **modify** `varco_fastapi/varco_fastapi/exceptions.py` (`varco_fastapi`)
    — serialize the new fields honouring `ErrorEnvelopeSettings`; under
    `problem_details=True` emit `type`/`title`/`detail`/`instance`/`status` and
    the `application/problem+json` media type; the validation handler emits the
    `errors: [...]` array with `varco.validation.<pydantic error type>` keys
    (D-3 item 4, and the `pydantic-i18n`-as-convention decision). Fix the wrong
    `{"code": "VARCO_XXXX", ...}` docstring at `:113` to a real body (D-5).
18. [ ] **create** `varco_fastapi/tests/test_exception_envelope.py` — **RD-1's I1
    proof at the HTTP layer** (half 2): default body for a built-in vs. an
    out-of-tree exception; the kill switch end-to-end through the app; the
    `problem+json` shape and media type when enabled; the 404 and 500 handlers
    produce the same envelope shape as a raised `ServiceException`.
19. [ ] **create** `technical_docs/features/error-taxonomy-and-i18n.md` — the I1
    half: the additive-hybrid table, why `FASTREST_*` is not renamed (D-5), the
    `message_key` catalogue, `error_params()`'s no-secrets rule, and the D-4
    delta with its kill switch stated in the first screenful.
20. [ ] **modify** `CLAUDE.md`, `ARCHITECTURE.md`, `README.md` — new "Error
    taxonomy" subsection; the D-4 and D-5 pitfall rows (text in Part 3's Docs
    obligations); exception-module map entries.

---

### Phase 2 — I2: `MessageCatalog` + `Accept-Language` negotiation

Closes **I2**. Implements D-1 (stdlib `gettext` at runtime behind the ABC; Babel
is authoring-only, **no new runtime dependency**), D-2 (hand-rolled RFC 4647
Lookup) and RD-3 (one `LocalizationMiddleware`, two toggles).

21. [ ] **create** `varco_core/varco_core/i18n/catalog.py` (`varco_core`) —
    `MessageCatalog` ABC (abstract `get_message`; concrete `format_message`,
    `available_locales`, `async start`/`stop`), `NullMessageCatalog` (the
    default), `DictMessageCatalog(mapping)`, and the `__missing__`-tolerant dict
    used by `format_map` so a missing interpolation parameter leaves `{name}`
    visible instead of raising inside an exception handler.
22. [ ] **create** `varco_core/tests/test_i18n_catalog.py` — failing first: ABC
    requires only `get_message`; the `format_message` default interpolates;
    a missing placeholder renders `{name}` and **never** raises; an unknown key
    returns `None` (not `""`); `available_locales()` is a `frozenset`.
23. [ ] **create** `varco_core/varco_core/i18n/gettext_catalog.py` (`varco_core`)
    — `GettextMessageCatalog(directory, domain="messages", locales=…)`: stdlib
    `gettext.translation()` per locale, **all loaded in `start()`** (blocking
    file I/O never on the event loop — D-1), stored in a dict that is immutable
    afterwards. `format_message` uses `ngettext` when `params` carries an integer
    `count`. **No `install()`, no `activate()`, no process-global active locale**
    — D-1's Flask-Babel `force_locale` note.
24. [ ] **create** `varco_core/tests/test_i18n_gettext_catalog.py` — a `.mo`
    fixture generated in-test with `msgfmt`-equivalent bytes (no `pybabel`
    dependency in the test suite); plural selection for `count=0/1/2`; a missing
    domain/locale is skipped with a WARNING rather than raising at `start()`.
25. [ ] **create** `varco_core/varco_core/i18n/negotiation.py` (`varco_core`) —
    D-2's ~60 lines: `parse_accept_language(header)` (comma split, `q=`
    extraction, invalid-`q` tolerance, descending **stable** sort) and
    `negotiate_locale(header, supported, *, default)` implementing RFC 4647
    §3.4 Lookup (progressive truncation at `-`, skipping single-character
    subtags; `*` → default; empty/absent → `None`, **not** `"en"`).
26. [ ] **create** `varco_core/varco_core/i18n/settings.py` (`varco_core`) —
    `I18nSettings` (prefix `VARCO_I18N_`): `enabled=False`, `default_locale="en"`,
    `supported_locales: tuple[str, ...] = ("en",)`, `query_param="lang"`,
    `catalog_dir: str | None = None`, `domain="messages"`,
    `set_content_language=True`.
27. [ ] **create** `varco_core/tests/test_i18n_negotiation.py` — **D-2's
    table-driven suite seeded from RFC 4647 §3.4's own worked examples**, plus:
    `q`-ordering including equal-`q` stability, `q=0` exclusion, malformed
    header tolerance, `zh-Hant-TW` truncation skipping the `x`/single-char
    subtag, and `*`.
28. [ ] **create** `varco_core/varco_core/i18n/resolve.py` (`varco_core`) —
    `async def resolve_locale(...) -> Resolved[str] | None` composing the
    five-source chain (`query_param` → `user_profile` → `tenant_default` →
    `accept_language` → `fallback`) over `resolve_precedence`, awaiting the
    `TenantDefaultsProvider` before building the candidate list. Only locales in
    `supported_locales` are returned; an unsupported explicit `?lang=` falls
    through with one DEBUG line and **never** 400s.
29. [ ] **create** `varco_core/tests/test_i18n_resolve.py` — the precedence table
    row by row, each asserting the returned `Resolved.source`; unsupported
    `?lang=` falls through; the deviation from brief 002's ordering (explicit
    `?lang=` beats a stored profile) is asserted, so it reads as intent.
30. [ ] **create** `varco_core/tests/test_i18n_concurrency.py` — **D-1's required
    spike, discharging brief 002 Evidence Gaps 1 & 2.** 200 concurrent
    `asyncio.gather` tasks each rendering under a different locale scope see only
    their own locale (no cross-task leak); one shared `GettextMessageCatalog` is
    read concurrently from both the loop and a `run_in_executor` thread with no
    corruption; a per-request scope never mutates catalog state.
31. [ ] **create** `varco_core/varco_core/i18n/cache_key.py` (`varco_core`) —
    **RD-6**: `localization_cache_key(base, *, locale=True, timezone=False)`,
    mirroring `tenancy_cache_key()`'s shape and **failing closed** the same way
    (a `locale=True` request with no ambient locale raises `RuntimeError`; it
    never silently omits the segment).
32. [ ] **create** `varco_core/tests/test_i18n_default_off.py` — **RD-1's I2
    proof.** `I18nSettings()` → `enabled is False`; no catalog constructed, no
    `.mo` read (asserted with a patched `gettext.translation` that fails the test
    if called), no middleware added, no `Content-Language` header;
    `error_message_for()` with no `message_resolver` returns `default_message`
    exactly as today.
33. [ ] **create** `varco_fastapi/varco_fastapi/middleware/localization.py`
    (`varco_fastapi`) — **RD-3**: one `LocalizationMiddleware` resolving locale
    and/or timezone in a single pass, setting **one** merged `RequestContext`
    token, resetting in `finally`, **and mirroring the resolved context onto
    `request.state`** so `ErrorMiddleware` (which is outside it) can still read
    it. Each half independently gated by `I18nSettings.enabled` /
    `TimezoneSettings.enabled`; with both off the middleware is not added at all.
34. [ ] **create** `varco_fastapi/tests/test_localization_middleware_ordering.py`
    — **RD-3's named hazard.** An exception raised in a handler is rendered with
    the resolved locale even though the `ContextVar` token was already reset by
    the time `ErrorMiddleware` formats the body (the `request.state` path); a
    404 from the router (never reaching a handler) is localized identically; the
    middleware sits **after** `TenantResolutionMiddleware` in request order so
    the tenant-default step sees `current_tenant()`.
35. [ ] **create** `varco_fastapi/varco_fastapi/i18n.py`; **modify**
    `varco_fastapi/varco_fastapi/app.py` (`varco_fastapi`) — `I18nLifecycle`
    (`start()` loads catalogs, `stop()` releases), `create_varco_app(i18n=None)`
    registering nothing by default, the `Content-Language` response header, and a
    `@Provider`-registered `I18nSettings` (**never `@Singleton`** — CLAUDE.md's
    pydantic-`BaseSettings` pitfall) binding `MessageCatalog →
    NullMessageCatalog` at framework-default priority.
36. [ ] **create** `varco_fastapi/tests/test_i18n_app.py` and **modify**
    `varco_fastapi/tests/test_di_binding_health.py` — end-to-end: `?lang=fr`
    renders a `DictMessageCatalog` message and sets `Content-Language: fr`; the
    binding-health scan resolves `MessageCatalog`, `I18nSettings`,
    `TenantDefaultsProvider` (the unquoted-`@Provider`-annotation pitfall applies
    to all three).
37. [ ] **create** `technical_docs/features/i18n-and-localization.md`; **modify**
    `CLAUDE.md`, `ARCHITECTURE.md`, `README.md` — the catalog ABC, the
    `pybabel extract/init/compile` authoring recipe (documented, not vendored),
    the precedence table, RD-6's cache rule, and the PyICU/MF2/Fluent extension
    points with D-1's reasons each is not the default.

---

### Phase 3 — T1: per-user / per-tenant timezone resolution

Closes **T1**. Same machinery as Phase 2, different chain; **varco does not
change what it stores** — everything keeps being written aware-UTC.

38. [ ] **create** `varco_core/varco_core/tz/__init__.py`,
    `varco_core/varco_core/tz/zones.py`, `varco_core/varco_core/tz/settings.py`
    (`varco_core`) — `validate_iana_zone(name) -> ZoneInfo | None`
    (`ZoneInfo(name)` in a `try`; `KeyError`/`ZoneInfoNotFoundError` → `None`),
    `TimezoneSettings` (prefix `VARCO_TZ_`): `enabled=False`,
    `default_timezone="UTC"`, `query_param="tz"`, `header="X-Timezone"`. Settings
    validation resolves `default_timezone` **at startup** and raises a legible
    error naming `pip install tzdata` when the database is absent — a startup
    failure, never a per-request one. Add the `varco-core[tz]` **optional extra**
    in `varco_core/pyproject.toml` (not a hard dependency).
39. [ ] **create** `varco_core/tests/test_tz_default_off.py` — **RD-1's T1
    proof.** `TimezoneSettings()` → `enabled is False`; `current_timezone()` is
    `None`; no header or query param is read; `to_user_tz()` is the identity with
    no ambient zone; every existing `datetime.now(timezone.utc)` call site is
    untouched (asserted by a grep-style source guard over `varco_core`).
40. [ ] **create** `varco_core/varco_core/tz/resolve.py` (`varco_core`) —
    `async def resolve_timezone(...) -> Resolved[ZoneInfo] | None` over the
    five-source chain (`query_param` → `header` → `user_profile` (the OIDC
    **`zoneinfo`** claim, not `tz`) → `tenant_default` → `fallback`), each
    candidate passing `validate_iana_zone()` **before** entering the list so
    `?tz=Mars/Olympus` falls through with one WARNING. Helpers `current_timezone`
    (re-exported from `context.request`), `to_user_tz(instant)`, `now_local()`.
41. [ ] **create** `varco_core/tests/test_tz_resolve.py` — the chain row by row
    with source names; an invalid zone at each position falls through, warns, and
    does not 500; `to_user_tz` on a naive input raises (it is a UTC-aware-in
    contract); `now_local()` with no ambient zone returns aware-UTC.
42. [ ] **modify** `varco_fastapi/varco_fastapi/middleware/localization.py` and
    `varco_fastapi/varco_fastapi/app.py` (`varco_fastapi`) — activate the
    timezone half of the single RD-3 middleware; `create_varco_app(timezone=None)`
    registers nothing; `TimezoneSettings` via `@Provider`.
43. [ ] **create** `varco_fastapi/tests/test_timezone_middleware.py` —
    `?tz=`/`X-Timezone`/JWT-`zoneinfo` end-to-end; both toggles off → the
    middleware is genuinely absent from `app.user_middleware`; locale-on /
    timezone-off resolves only the locale and leaves `timezone=None` in the same
    merged `RequestContext` (the D-6 merge test at the HTTP layer).
44. [ ] **create** `technical_docs/features/timezone-handling.md`; **modify**
    `CLAUDE.md`, `ARCHITECTURE.md` — the resolution chain, the
    **store-UTC-render-local** rule stated plainly so nobody starts writing local
    times into `created_at`, the `tzdata`-in-slim-images note, and RD-6's
    "timezone is never an implicit cache-key component".

---

### Phase 4 — T2: DST-safe scheduling

Closes **T2**. Implements D-7 (three additive `Job` columns, `run_at` unchanged),
D-8 (gap/overlap policy, incl. the deliberate `NEXT_VALID` deviation from brief
004), D-9 (RFC 9557 emitter only) and RD-5 (a store must *declare* zoned
support). **This is the only phase that touches a shipped framework table** — see
the migration section that follows.

45. [ ] **create** `varco_core/tests/test_job_zoned_defaults.py` — **RD-1's T2
    proof, half 1, written first.** A `Job(...)` constructed exactly as today is
    field-for-field identical, with `run_at_wall is None`, `run_at_tz is None`,
    `run_at_fold == 0`; `AbstractJobStore.supports_zoned_schedules is False` on
    the ABC; `InMemoryJobStore` round-trips a zoned job and an unzoned one
    identically on the claim path.
46. [ ] **modify** `varco_core/varco_core/job/base.py` (`varco_core`) — the three
    defaulted `Job` fields next to the Plan 005 Phase 4 block (`base.py:249-280`),
    each with a docstring stating **`run_at` is materialized, not replaced**;
    `AbstractJobStore.supports_zoned_schedules: ClassVar[bool] = False`;
    `list_pending_zoned(before, limit)` as a **portable default** over
    `list_by_status(PENDING)` + an in-Python filter (a correct fallback exists,
    so this is *not* concrete-but-raising, unlike `renew()`).
47. [ ] **create** `varco_core/tests/test_tz_schedule.py` — failing first, the
    DST table from brief 004's worked examples: `2026-03-08 02:30` in
    `America/Los_Angeles` (gap) under each `GapPolicy`; `2026-11-01 01:30`
    (overlap) under `fold=0`/`fold=1` and each `OverlapPolicy`; a normal time is
    unaffected; a zone with no DST; a southern-hemisphere zone.
48. [ ] **create** `varco_core/varco_core/tz/schedule.py` (`varco_core`) —
    `datetime_exists`, `datetime_ambiguous` (both stdlib `zoneinfo` only — **no
    `dateutil`**, D-8), `GapPolicy` (`NEXT_VALID` default, `PREVIOUS_VALID`,
    `SKIP`, `ERROR`), `OverlapPolicy` (`FIRST` default, `LAST`),
    `ScheduleGapError`, and `resolve_zoned(...)`. A gap resolution logs one
    WARNING naming the job, zone, requested and resolved times; `SKIP`
    transitions to a **terminal** state with `ScheduleGapError` rather than
    leaving the job pending — skipping is allowed, never silent (D-8).
49. [ ] **create** `varco_core/varco_core/tz/format.py` +
    `varco_core/tests/test_tz_format.py` (`varco_core`) — **D-9**:
    `format_rfc9557(instant, zone) -> str` emitting
    `2026-03-08T09:00:00-05:00[America/New_York]`. **No parser.** The docstring
    carries the "when a parser lands it is an additive branch in the coercer, not
    a storage change" note.
50. [ ] **create** `varco_core/tests/test_job_enqueue_zoned.py` — **RD-5's
    refusal test**, written first: enqueueing with `tz=` into a store whose
    `supports_zoned_schedules` is `False` raises `ValueError` **naming the store
    class**; the same enqueue against `InMemoryJobStore` succeeds and materializes
    a correct `run_at`; `run_at=` and `run_at_wall=`+`tz=` together raise.
51. [ ] **modify** `varco_core/varco_core/job/base.py` and
    `varco_core/varco_core/job/runner.py` (`varco_core`) —
    `AbstractJobRunner.enqueue(..., run_at_wall=None, tz=None, fold=0,
    gap=GapPolicy.NEXT_VALID, overlap=OverlapPolicy.FIRST)`; the RD-5 guard; the
    materialization call to `resolve_zoned`. Every new parameter defaulted so an
    unchanged caller is byte-identical.
52. [ ] **create** `varco_core/varco_core/job/reschedule.py` (`varco_core`) —
    `ScheduleRematerializer(store, *, interval=0.0, horizon=timedelta(hours=48))`.
    `interval=0.0` → **never started**. Sweeps `list_pending_zoned(before=now +
    horizon)`, recomputes under *current* tzdata, writes back **only when the
    value actually changed**, fenced with `save(expected_epoch=…)`, catching and
    **skipping** `StaleLeaseError` (a job claimed between read and write is
    executing right now and must not have its schedule rewritten underneath the
    worker). One INFO per actual change.
53. [ ] **create** `varco_core/tests/test_schedule_rematerializer.py` — **RD-1's
    T2 proof, half 2.** `interval=0.0` → `start()` spawns no task at all
    (asserted on the task set, not on a sleep); the horizon genuinely bounds the
    query; an unchanged `run_at` produces **zero** writes; a simulated tzdata
    change produces exactly one fenced write; `StaleLeaseError` is skipped, not
    raised; a `run_at_tz IS NULL` job is never touched.
54. [ ] **modify** `varco_sa/varco_sa/job_store.py` (`varco_sa`) — three columns
    on the `varco_jobs` `Table` (`run_at_wall DateTime(timezone=False)` nullable,
    `run_at_tz String(64)` nullable, `run_at_fold Integer NOT NULL server_default
    "0"`), `supports_zoned_schedules = True`, and a real `list_pending_zoned`
    override (`WHERE run_at_tz IS NOT NULL AND run_at < :before LIMIT :limit`).
    **No new index** — the claim predicate is unchanged (D-7).
55. [ ] **create** `varco_sa/varco_sa/migrations/versions/0004_job_zoned_schedule.py`
    (`varco_sa`) — `revision = "0004_job_zoned_schedule"`, `down_revision =
    "0003_audit_hash_chain"`, `branch_labels = None` (the `("varco",)` label
    lives on `0001` and labels the whole branch). Same **idempotent
    column-exists guard** as `0002`/`0003`. See the migration section below.
56. [ ] **create** `varco_sa/tests/test_job_store_zoned.py` (`varco_sa`) — unit:
    the columns exist in `framework_metadata()`; `list_pending_zoned` SQL shape.
    `@pytest.mark.integration`: a database built by `0001` alone already has the
    columns (the dynamic-baseline case) and `0004` is a **no-op** against it;
    a database stamped at `0003` gains them; `downgrade` drops them; a pre-plan
    row with `run_at_tz IS NULL` claims identically before and after.
57. [ ] **modify** `varco_beanie/varco_beanie/job_store.py` +
    **create** `varco_beanie/tests/test_job_store_zoned.py` (`varco_beanie`) —
    three defaulted document fields, `supports_zoned_schedules = True`, a
    `list_pending_zoned` query override; tests that an existing document with the
    fields absent deserializes to the defaults (Mongo's implicit-null path — no
    migration required). **Docs, same change:** extend
    `technical_docs/features/job-scheduling-and-leases.md` with a "Zoned
    schedules" section carrying D-7's materialization rule, D-8's deviation next
    to its brief-004 citation, RD-5's declaration contract, and the
    `ScheduleRematerializer` recipe; add the T2 pitfall rows to `CLAUDE.md`.

---

### Phase 5 — T3: query-layer datetime coercion contract

Closes **T3**. Implements D-10 — `assume="naive"` is the default because it is
byte-identical to today, `"utc"` is the *recommendation*, `"context"` is opt-in.

58. [ ] **create** `varco_core/tests/test_query_datetime_coercion.py` — **RD-1's
    T3 proof**, written first. With no policy, `coerce_datetime()` returns
    exactly what `datetime.fromisoformat(value)` returns — naive stays naive,
    `tzinfo is None` — for the full existing input table; an already-aware input
    (`…Z`, `…-05:00`) is returned verbatim under **every** policy.
59. [ ] **create** `varco_core/varco_core/query/policy.py` (`varco_core`) —
    `DatetimeCoercionPolicy` (`frozen`: `assume: Literal["naive","utc","context"]
    = "naive"`, `log_naive: bool = True`). Docstring recommends `"utc"` in its
    first paragraph and states D-10's asyncpg reason for not defaulting to it.
60. [ ] **modify** `varco_core/varco_core/query/visitor/type_coercion.py`
    (`varco_core`) — `coerce_datetime(value, *, policy=None)` and
    `TypeCoercionVisitor(..., policy=None)`; `"context"` reads
    `current_timezone()` and falls back to `"utc"` with a DEBUG line when no zone
    is resolved; a bracket-suffixed RFC 9557 input is **rejected** with a legible
    error naming the two supported inputs (D-9). Module docstring records the
    invariant **convert the bound, never the column** — nothing here may ever
    generate `AT TIME ZONE`.
61. [ ] **create** `varco_core/tests/test_query_datetime_policy.py` — `"utc"`
    attaches `tzinfo=UTC`; `"context"` uses the ambient zone and falls back with
    a DEBUG when absent; the bracket-suffix rejection message names both accepted
    forms; **the `AT TIME ZONE` guard** — the compiled SQL for a datetime filter
    contains no `AT TIME ZONE` under any policy; the date-only semantic
    (`__lte=2026-01-01` excludes almost all of Jan 1st) is asserted so the
    documented behaviour cannot drift.
62. [ ] **modify** `technical_docs/features/timezone-handling.md`, `CLAUDE.md` —
    the T3 section: the three policies, why the default is not the
    recommendation, the explicit-offset-always-wins rule, the date-only trap, and
    the `AT TIME ZONE` invariant addressed to a future contributor tempted to
    "fix" the coercer by moving conversion into SQL.

---

### Phase 6 — C5: bulk cache operations + pluggable serializer

Closes **C5**. Implements D-11 (a **separate** `BulkCache` Protocol; reuse
`varco_core.serialization.Serializer`) and D-12 (N per-key backplane messages;
the Plan 010 wire format is not extended). Fully independent of Phases 0–5.

63. [ ] **create** `varco_core/tests/test_cache_bulk_contract.py` — **RD-1's C5
    proof, half 1, written first.** A frozen literal set of `AsyncCache`'s
    Protocol members asserts it is **unchanged** (the D-11 regression guard —
    adding a member here silently breaks `isinstance()` for every out-of-tree
    cache); every shipped backend satisfies `isinstance(backend, BulkCache)`; the
    portable defaults are loops with the same per-key semantics as `get`/`set`/
    `delete`.
64. [ ] **modify** `varco_core/varco_core/cache/base.py` (`varco_core`) — new
    `BulkCache` (`runtime_checkable Protocol`: `get_many`, `set_many`,
    `delete_many`); `CacheBackend` gains the three as **concrete portable
    defaults** looping over today's methods, plus `serializer: Serializer[Any] |
    None = None` on `__init__`. **`AsyncCache` is not touched — not one line.**
65. [ ] **modify** `varco_core/varco_core/cache/readthrough.py` (`varco_core`) —
    `read_through_many(cache, keys, loader, policy, *, type_hint=None,
    singleflight=None)` implementing the Design's six steps: bulk get →
    per-key envelope unwrap → fresh/negative returned immediately → soft-stale
    returned now with one `spawn_refresh` per key **through the same
    `Singleflight` slot** → per-key leadership election with **one** batched
    `loader(missing_keys)` call for the led keys, followers `asyncio.shield`ed →
    wrap + `set_many`. A key absent from the loader's dict resolves to `None` and
    is negative-cached iff `policy.negative_ttl` is set.
66. [ ] **create** `varco_core/tests/test_cache_readthrough_many.py` — the loader
    is called **once** with exactly the missing keys; a single `read_through()`
    for one of those keys becomes a follower of the *same* slot rather than
    racing; mixed fresh/soft-stale/negative/absent in one call; a follower
    cancelled mid-wait does not kill the batch (Plan 010's `shield` rule); a
    loader raising fails all led keys and clears their slots.
67. [ ] **create** `varco_core/tests/test_cache_bulk_tenancy.py` — Plan 010's
    tenant landmine, retested for the bulk path: the same pks under two
    `tenant_context()` blocks produce two batched loader calls, and every
    coalescing key carries the `tenant:{id}:` segment. Also asserts **RD-6**: no
    locale or timezone segment appears in any key produced by the bulk path.
68. [ ] **modify** `varco_core/varco_core/cache/mixin.py` and
    `varco_core/varco_core/cache/decorator.py` (`varco_core`) —
    `CacheServiceMixin.list()` takes the batch path only when the cache satisfies
    `BulkCache` **and** the caller opted in; `@cached(..., bulk=True)`. With no
    opt-in the existing bodies run verbatim.
69. [ ] **modify** `varco_core/varco_core/cache/layered.py` (`varco_core`) —
    `get_many`/`set_many`/`delete_many` obeying Plan 010 rule 2 (**authoritative
    layer first**, then faster layers, then publish) and emitting **N per-key
    `kind="key"` messages** (D-12). A `DESIGN:` block records why a batched
    `kind="keys"` is refused, so a future change is a deliberate versioned
    rollout rather than a silent coherence regression in a mixed-version fleet.
70. [ ] **create** `varco_core/tests/test_cache_bulk_default_off.py` — **RD-1's
    C5 proof, half 2.** Each backend's default serializer produces the **exact
    bytes** it produces today (a literal byte-string table per backend); no batch
    path is taken without an explicit opt-in; `LayeredCache.set_many()` with no
    backplane publishes nothing; with a backplane, exactly `len(keys)` messages
    are published, all `kind="key"`, after the authoritative write.
71. [ ] **modify** `varco_redis/varco_redis/cache.py` (`varco_redis`) — native
    `MGET`, pipelined `SET` (one round trip), `UNLINK`-based `delete_many`
    overrides, and `serializer=` plumbing whose default stays `JsonSerializer`.
72. [ ] **create** `varco_redis/tests/test_redis_bulk.py` (`varco_redis`) — unit
    against the existing `FakeRedis` double; `@pytest.mark.integration` against
    Docker Redis for the real `MGET`/pipeline path, empty-key-list, partial-miss,
    and per-key TTL correctness.
73. [ ] **modify** `varco_memcached/varco_memcached/cache.py` (`varco_memcached`)
    — `get_multi`/`set_multi` overrides plus `serializer=`, with the default
    reproducing today's bytes codec exactly.
74. [ ] **create** `varco_memcached/tests/test_memcached_bulk.py`
    (`varco_memcached`) — unit + `@pytest.mark.integration`; the key-length and
    illegal-character limits Memcached imposes are asserted to surface as a
    legible error rather than a silent partial write.
75. [ ] **modify** `technical_docs/features/cache-hardening.md`, `CLAUDE.md`,
    `ARCHITECTURE.md`, `README.md` — a C5 section: why `BulkCache` is separate
    (D-11's `runtime_checkable` argument), the serializer table per backend,
    D-12's N-messages decision, and the RD-6 "cache the unlocalized
    representation, localize at render time" rule.

---

### Phase 7 — cross-cutting guards and the docs sweep

76. [ ] **create** `varco_fastapi/tests/test_layer_boundaries.py`
    (`varco_fastapi`) — **RD-4's guard**: importing every `varco_fastapi` module
    added by this plan leaves `varco_sa`, `varco_beanie`, `babel`, `icu`, and
    `dateutil` absent from `sys.modules`. Same seam rule as `AbstractEventBus` /
    `AbstractMigrator` / `varco_core.tenancy`.
77. [ ] **create** `varco_core/tests/test_no_new_runtime_deps.py` (`varco_core`)
    — asserts the modules created by this plan import only stdlib + pydantic
    (D-1, D-2, D-8's stdlib-only detection, D-9's no-parser rule), and that
    `varco-core`'s `[project.dependencies]` is unchanged with `tz` present only
    as an optional extra.
78. [ ] **modify** `CLAUDE.md`, `ARCHITECTURE.md`, `README.md` — the
    consolidation pass listed in Part 3's **Docs obligations**: the new
    architecture subsections, the pitfall rows, and the Decision Tree branches.
    Per repo rule this is part of the same change as the code, never a follow-up.

---

## T2 migration & compatibility

The riskiest item in the plan, and the only one touching a **shipped framework
table** (`varco_jobs`, one of the ten). Everything here follows from D-7's rule:
**`run_at` is materialized, not replaced.**

### The Alembic revision

`varco_sa/varco_sa/migrations/versions/0004_job_zoned_schedule.py`:

```python
revision = "0004_job_zoned_schedule"
down_revision = "0003_audit_hash_chain"
branch_labels = None  # the ("varco",) label is on 0001 and labels the branch
depends_on = None
```

It adds three nullable/defaulted columns to `varco_jobs` behind the **same
idempotent column-exists guard** `0002_dlq_audit_tenant_id` and
`0003_audit_hash_chain` already use (`sa.inspect(bind)` → `get_columns`), and
`downgrade()` drops them behind the same guard.

**Why the guard is mandatory, not stylistic.** `0001_varco_framework_baseline`
is **dynamic** — it iterates `varco_sa.metadata.framework_metadata().tables` and
creates whatever the *installed wheel* declares. So the moment step 54 adds the
columns to the `varco_jobs` `Table` object, a **fresh** database created by
`0001` already has them, and `0004` must then be a no-op. A database stamped at
`0003` before upgrading does **not** have them, and `0004` adds them. Both paths
converge on the same schema; step 56's integration test asserts exactly these two
paths.

No index is created. The claim predicate is unchanged, so the existing
`run_at`/`status` index is still the right one — stated explicitly because
"additive columns" usually implies "and an index", and here it deliberately does
not (D-7).

### The Beanie / MongoDB side

**No migration.** The three fields are added to the job document with the same
defaults as the dataclass. A document written by the previous version simply has
the keys absent, and Beanie deserializes them to `None`/`None`/`0` — which is
precisely the "unzoned job" state. `BeanieMigrator`'s `IndexReconciler` is
untouched: no new index, so `varco migrate index` reports nothing new and
`index_mode="check"` (the default) stays quiet.

### In-flight jobs written by the previous version

Every pre-upgrade row has `run_at_tz IS NULL`, and every code path keys off that:

| Path | Behaviour on a pre-upgrade row |
|---|---|
| `claim_next` / `try_claim` | Identical — the predicate is still `run_at <= now()` on the same column and index. |
| `JobPoller` / lease reap | Identical — untouched by this phase. |
| `ScheduleRematerializer` | Skips it (`run_at_tz IS NOT NULL` is part of `list_pending_zoned`'s filter). |
| Retry / DLQ | Identical. |

**Both rolling-deploy directions are safe** — unlike Plan 010's cache envelope
(that plan's D-5). A **new** pod writing a zoned job also writes a correct
`run_at`; an **old** pod reading that row sees a perfectly ordinary `run_at` and
claims it at exactly the right instant — it simply cannot re-materialize. **T2
requires no two-step deploy.** The only column an old pod cannot write is
`run_at_fold`, which has a `server_default` of `"0"`.

### An out-of-tree `AbstractJobStore` subclass — and RD-5

This is the same failure mode Plan 005 Phase 4 hit with `try_claim(owner_id=,
lease_ttl=)` (CLAUDE.md pitfall: *"External `AbstractJobStore` subclass breaks on
`lease_ttl`"*), and RD-5 is the answer.

- A third-party store that **splats the dataclass** into fixed columns raises on
  the unknown columns. Loud, immediate, fine.
- A third-party store that **maps columns explicitly** silently drops
  `run_at_wall`/`run_at_tz`/`run_at_fold`. The job still fires at the right
  instant (because `run_at` was materialized at enqueue) — but
  re-materialization silently no-ops, so the DST safety the caller asked for
  quietly is not there. That silent degradation is the whole hazard.

**RD-5 turns it into a refusal.** `AbstractJobStore.supports_zoned_schedules:
ClassVar[bool] = False` means every out-of-tree store is `False` **until its
author opts in**, and `enqueue(..., tz=…)` against such a store raises
`ValueError` naming the store class and the flag. An unchanged store that is
never handed a zone is entirely unaffected — the flag is read only on the zoned
enqueue path. `list_pending_zoned()` needs no action either: its portable default
over `list_by_status(PENDING)` works on any store, correctly if unindexed.

The upgrade instruction for a third-party store author is therefore exactly two
lines: persist the three fields, then set `supports_zoned_schedules = True`.

### `varco migrate adopt` / `ensure_table()` interaction

Unchanged, and worth restating because this phase is where someone will hit it.

- A deployment on **`ensure_table()`** (no Alembic) gets the three columns only
  if it drops and recreates `varco_jobs`, which it must not do. The correct move
  is CLAUDE.md's documented one: **`varco migrate adopt` first, then `varco
  migrate upgrade heads`.** `adopt` stamps `varco@head` without executing DDL
  against the already-built table; `upgrade` then runs `0002`/`0003`/`0004`,
  each of which is column-exists-guarded and therefore safe against a table that
  may already have some of them.
- **Order matters and is directional**: adopt, then upgrade. The reverse
  (Alembic `CREATE TABLE` against an `ensure_table()`-built table) fails.
- **`heads`, plural, always.** The framework branch is a second head; `upgrade
  head` would silently leave one branch behind. Every `varco migrate` verb
  already defaults to `heads`.
- Recommended production posture is unchanged: `VARCO_MIGRATE_MODE=check` on the
  pods, `varco migrate upgrade` in a pre-deploy job.

---


## Edge cases

| Input / state | Expected behaviour |
|---|---|
| No configuration at all | Every item inert per RD-1's matrix, **except** the D-4 delta on built-in exceptions. Guarded by steps 6, 14, 32, 39, 45, 58, 63, 70. |
| `AmbientVar.scope()` value read from a spawned `asyncio.Task` | Invisible to the parent after the task ends; the child sees a copy. Asserted (step 2) so it is a contract, not an accident. |
| Two `request_context()` blocks nested, inner sets only `locale` | Outer `timezone` survives — merge semantics, not replace (D-6). |
| `resolve_precedence` given `("query_param", "")` | `""` is selected — a falsy value is still a value. Step 4. |
| Out-of-tree `ServiceException` subclass, no `message_key` | Body **byte-identical** to pre-plan. Step 14. |
| Built-in exception, `VARCO_ERROR_INCLUDE_MESSAGE_KEY=false` | Pre-plan body exactly, for every exception. Step 14. |
| `message_key` set, catalog has no entry | `default_message` is used. A missing entry can never produce an empty message. Step 15. |
| `MessageResolver` raises | Swallowed; falls back to `default_message`. Rendering an error must never raise. Step 15. |
| `format_message` with a missing interpolation param | Renders the literal `{name}`; no `KeyError` inside an exception handler. Step 22. |
| `?lang=xx-YY` not in `supported_locales` | Falls through to the next precedence source with one DEBUG line. **Never a 400.** Step 29. |
| `Accept-Language` absent or empty | No match — falls to the next step, **not** directly to `"en"`. Step 27. |
| `Accept-Language: *` | Matches `default_locale`. Step 27. |
| i18n enabled, resolved locale is the fallback | `Content-Language` is still set (brief 003's rule). Step 36. |
| Exception raised in a handler, locale resolved | Localized — via the `request.state` mirror, because the `ContextVar` token is already reset by the time `ErrorMiddleware` renders. Step 34. |
| Router-level 404 (handler never runs) | Localized identically. Step 34. |
| `?tz=Mars/Olympus` | Rejected by `validate_iana_zone`, falls through with one WARNING, request succeeds. Step 41. |
| tzdata absent from the image | **Startup** failure naming `pip install tzdata`; never a per-request 500. Step 38. |
| `to_user_tz()` with no ambient zone | Identity. Step 39. |
| `2026-03-08 02:30` `America/Los_Angeles`, default gap policy | `NEXT_VALID` → 03:00 local, one WARNING naming job/zone/both times (D-8). |
| Same, `GapPolicy.SKIP` | Job moves to a **terminal** state with `ScheduleGapError`. Skipping is allowed, never silent. |
| `2026-11-01 01:30` `America/Los_Angeles` (ambiguous) | Runs **once**, at `fold=0`. Contrast Quartz, which fires both (D-8). |
| Pre-upgrade job row (`run_at_tz IS NULL`) | Claimed identically; skipped by the rematerializer. |
| Old pod reads a new pod's zoned job row | Claims it at the right instant; simply cannot re-materialize. **No two-step deploy.** |
| `enqueue(tz=…)` into a store with `supports_zoned_schedules = False` | `ValueError` naming the store class (RD-5). |
| `ScheduleRematerializer(interval=0.0)` | Never started — no task object created. Step 53. |
| Rematerializer hits a job claimed between read and write | `StaleLeaseError` caught and **skipped** — the worker owns it. Step 53. |
| `?created_at__gte=2026-01-01`, default policy | Naive `datetime`, returned exactly as today. The database session still decides. Step 58. |
| Same, `assume="utc"` | `tzinfo=UTC` attached. ⚠️ asyncpg rejects this against `TIMESTAMP WITHOUT TIME ZONE` — the reason it is not the default (D-10). |
| `assume="context"` with no ambient timezone | Falls back to `"utc"` and logs one DEBUG. Step 61. |
| Already-aware input under any policy | Used verbatim; no policy applied. Step 58. |
| `?created_at__lte=2026-01-01` | Midnight at the **start** of Jan 1 — excludes almost all of that day. Documented and asserted (step 61). |
| Input with an RFC 9557 bracket suffix | Rejected with an error naming the two supported forms. **No parser ships** (D-9). |
| `localization_cache_key(..., locale=True)` with no ambient locale | `RuntimeError` — fails closed, exactly like `tenancy_cache_key()` (RD-6). |
| Adding a method to `AsyncCache` | Caught by step 63's frozen member-set guard — it would silently break `isinstance()` for every out-of-tree cache (D-11). |
| `read_through_many` where the loader omits a requested key | Resolves to `None`; negative-cached only if `policy.negative_ttl` is set. |
| Bulk read + single read of the same key, concurrently | One recompute — they share the same `Singleflight` slot (D-12). |
| `LayeredCache.set_many(n keys)` with a backplane | Authoritative layer first, then faster layers, then **n** `kind="key"` messages. Never a batched `kind="keys"` (D-12). |
| `set_many([])` / `get_many([])` | No round trip, no message, returns empty. |
| Memcached key too long / illegal characters | Legible error, never a silent partial write. Step 74. |

---

## Testing

### Matrix

| Package | New / touched test files | Kind |
|---|---|---|
| `varco_core` | `test_context_ambient.py`, `test_context_precedence.py`, `test_context_default_off.py` | unit |
| `varco_core` | `test_error_codes_message_key.py`, `test_error_envelope.py`, `test_error_message_resolver.py`, `test_error_params_no_secrets.py` | unit |
| `varco_core` | `test_i18n_catalog.py`, `test_i18n_gettext_catalog.py`, `test_i18n_negotiation.py`, `test_i18n_resolve.py`, `test_i18n_concurrency.py`, `test_i18n_default_off.py` | unit |
| `varco_core` | `test_tz_default_off.py`, `test_tz_resolve.py`, `test_tz_schedule.py`, `test_tz_format.py` | unit |
| `varco_core` | `test_job_zoned_defaults.py`, `test_job_enqueue_zoned.py`, `test_schedule_rematerializer.py` | unit |
| `varco_core` | `test_query_datetime_coercion.py`, `test_query_datetime_policy.py` | unit |
| `varco_core` | `test_cache_bulk_contract.py`, `test_cache_readthrough_many.py`, `test_cache_bulk_tenancy.py`, `test_cache_bulk_default_off.py` | unit |
| `varco_core` | `test_no_new_runtime_deps.py` | guard |
| `varco_fastapi` | `test_exception_envelope.py`, `test_localization_middleware_ordering.py`, `test_i18n_app.py`, `test_timezone_middleware.py` | unit (ASGI, `httpx.ASGITransport`) |
| `varco_fastapi` | `test_di_binding_health.py` (extended), `test_layer_boundaries.py` | guard |
| `varco_sa` | `test_job_store_zoned.py` | unit **+ `@pytest.mark.integration`** (Docker Postgres — the `0001`-fresh vs `0003`-stamped migration paths, and `downgrade`) |
| `varco_beanie` | `test_job_store_zoned.py` | unit **+ `@pytest.mark.integration`** (Docker Mongo — a pre-upgrade document missing the three keys) |
| `varco_redis` | `test_redis_bulk.py` | unit (`FakeRedis`) **+ `@pytest.mark.integration`** (Docker Redis — real `MGET`/pipeline, per-key TTL) |
| `varco_redis` | `test_redis_di.py` (unchanged, must stay green) | guard |
| `varco_memcached` | `test_memcached_bulk.py` | unit **+ `@pytest.mark.integration`** (Docker Memcached — `get_multi`/`set_multi`, key limits) |

Repo conventions bind: every test is `async def` with no `@pytest.mark.asyncio`
(auto mode); `InMemoryEventBus` + `drain()` wherever event ordering matters;
`InMemoryDeadLetterQueue` / `InMemoryJobStore` / `InMemoryCache` are the unit
doubles; integration tests are skipped by default and run with `-m integration`.
A flaky timing-sensitive test gets a larger sleep margin, **never** an `xfail`.

### Guard tests this plan must add

Written in the spirit of `varco_core/tests/test_cache_singleflight_tenancy.py`
and `varco_fastapi/tests/test_di_binding_health.py` — each one exists because a
regression in it is silent.

1. **Default-off is byte-identical** — `test_context_default_off.py` (6),
   `test_i18n_default_off.py` (32), `test_tz_default_off.py` (39),
   `test_job_zoned_defaults.py` (45), `test_query_datetime_coercion.py` (58),
   `test_cache_bulk_default_off.py` (70). Collectively these are RD-1's
   acceptance criterion. Each asserts against *literal* expected values, not
   against "whatever the code currently does".
2. **The one deliberate delta is bounded** — `test_error_envelope.py` (14).
   Asserts the built-in body gains exactly two keys **and** that an out-of-tree
   subclass gains none **and** that one env var restores the old body. If the
   delta ever grows, this test is what says so.
3. **Locale/timezone never leak into cache keys (RD-6)** —
   `test_cache_bulk_tenancy.py` (67) asserts no locale/timezone segment appears
   in any key produced by the cache path, alongside re-asserting Plan 010's
   `tenant:{id}:` rule for the new bulk path. A regression here serves a
   `fr`-rendered body to an `en` client — the i18n analogue of a cross-tenant
   leak, and easier to hit because localization happens far from the cache call.
4. **`varco_fastapi` does not import `varco_sa`/`varco_beanie` (RD-4)** —
   `test_layer_boundaries.py` (76). Also asserts `babel`, `icu` and `dateutil`
   stay out of `sys.modules`.
5. **No new runtime dependency (D-1, D-2, D-8, D-9)** —
   `test_no_new_runtime_deps.py` (77). Asserts `varco-core`'s declared
   dependencies are unchanged and that `tz` exists only as an optional extra.
6. **The error codes are not renamed (D-5)** —
   `test_error_codes_message_key.py` (10). A frozen literal table of
   `FASTREST_nnn` strings. This is the entire enforcement of "never change it
   after release".
7. **`AsyncCache`'s Protocol surface is frozen (D-11)** —
   `test_cache_bulk_contract.py` (63). Adding a member to a
   `runtime_checkable` Protocol silently flips `isinstance()` to `False` for
   every out-of-tree implementation.
8. **Catalog concurrency (D-1's required spike)** —
   `test_i18n_concurrency.py` (30). Discharges brief 002's Evidence Gaps 1 & 2.
9. **DI binding health for the new bindings** — extended
   `test_di_binding_health.py` (36). `MessageCatalog`, `I18nSettings`,
   `TimezoneSettings`, `TenantDefaultsProvider` all resolve; catches the
   quoted-`@Provider`-return-annotation pitfall, which poisons *every* binding in
   the container, not just the offending one.
10. **The `AT TIME ZONE` invariant** — `test_query_datetime_policy.py` (61).
    Compiled SQL for a datetime filter contains no `AT TIME ZONE` under any
    policy; converting the column instead of the bound defeats the index.

### Verification

```bash
# X1 + I1
uv run pytest varco_core/tests/test_context_ambient.py \
              varco_core/tests/test_context_precedence.py \
              varco_core/tests/test_context_default_off.py
uv run pytest varco_core/tests/test_error_codes_message_key.py \
              varco_core/tests/test_error_envelope.py \
              varco_core/tests/test_error_message_resolver.py \
              varco_core/tests/test_error_params_no_secrets.py

# I2 + T1
uv run pytest varco_core/tests/test_i18n_catalog.py \
              varco_core/tests/test_i18n_gettext_catalog.py \
              varco_core/tests/test_i18n_negotiation.py \
              varco_core/tests/test_i18n_resolve.py \
              varco_core/tests/test_i18n_concurrency.py \
              varco_core/tests/test_i18n_default_off.py
uv run pytest varco_core/tests/test_tz_default_off.py varco_core/tests/test_tz_resolve.py

# T2 + T3
uv run pytest varco_core/tests/test_tz_schedule.py varco_core/tests/test_tz_format.py \
              varco_core/tests/test_job_zoned_defaults.py \
              varco_core/tests/test_job_enqueue_zoned.py \
              varco_core/tests/test_schedule_rematerializer.py
uv run pytest varco_core/tests/test_query_datetime_coercion.py \
              varco_core/tests/test_query_datetime_policy.py

# C5
uv run pytest varco_core/tests/test_cache_bulk_contract.py \
              varco_core/tests/test_cache_readthrough_many.py \
              varco_core/tests/test_cache_bulk_tenancy.py \
              varco_core/tests/test_cache_bulk_default_off.py

# FastAPI surface + guards
uv run pytest varco_fastapi/tests/test_exception_envelope.py \
              varco_fastapi/tests/test_localization_middleware_ordering.py \
              varco_fastapi/tests/test_i18n_app.py \
              varco_fastapi/tests/test_timezone_middleware.py \
              varco_fastapi/tests/test_di_binding_health.py \
              varco_fastapi/tests/test_layer_boundaries.py
uv run pytest varco_core/tests/test_no_new_runtime_deps.py

# Regression sweep — nothing else may move
uv run pytest varco_core/tests/ varco_fastapi/tests/ \
              varco_sa/tests/ varco_beanie/tests/ \
              varco_redis/tests/ varco_memcached/tests/

# Docker-backed
uv run pytest varco_sa/tests/ -m integration
uv run pytest varco_beanie/tests/ -m integration
uv run pytest varco_redis/tests/ -m integration
uv run pytest varco_memcached/tests/ -m integration

# Gates
make lint
make type-check
```

---

## Rollout

varco is a **published framework**: the audience is app authors upgrading a
pinned version, not a fleet we control. Ordering is therefore about what an
upgrade does to someone who reads no release notes.

### 1. The one wire delta (D-4) and its kill switch

Upgrading gives a **built-in** varco exception's JSON body up to two new keys,
`message_key` and `params`. Nothing is removed, renamed, or reordered, and an
out-of-tree `ServiceException` subclass that sets no `message_key` is
byte-identical. Per RFC 9457, clients *must* ignore unrecognized extension
members (brief 003).

The kill switch is one env var, and it is the **first line** of the release note
and of the feature doc:

```bash
VARCO_ERROR_INCLUDE_MESSAGE_KEY=false
VARCO_ERROR_INCLUDE_PARAMS=false      # both, for the exact pre-plan body
```

The realistic breakage is a test asserting exact-equality on an error body. That
is a one-line fix or one env var — which is precisely why the delta was judged
acceptable (D-4) rather than hidden.

### 2. No two-step deploy is required anywhere in this plan

Explicitly contrasted with Plan 010's `CacheEnvelope`, which **did** need one
because an old pod reading a new pod's envelope returns the raw wrapper dict
(Plan 010 D-5).

- **T2** is safe in both directions: a new pod writing a zoned job also writes a
  correct `run_at`; an old pod reading that row claims it at the right instant
  (D-7). The new columns are nullable / `server_default`-ed.
- **I1** adds keys to a response body; there is no pod-to-pod contract.
- **I2 / T1 / T3 / X1** are off by default and change no persisted shape.
- **C5** changes no cache value shape. It composes with Plan 010's envelope,
  which retains its own two-step rule — if an app is *also* turning on
  envelope-requiring policy fields (`soft_ttl`/`negative_ttl`/`stale_if_error`),
  Plan 010's recipe still applies unchanged: roll the new version everywhere
  first, then enable the policy fields. C5 neither adds to nor relaxes that.
- **D-12** is a rollout decision in itself: `set_many` emits N per-key messages
  precisely so a Plan-010-era subscriber in a mixed-version fleet keeps
  receiving invalidations it can decode.

### 3. Recommended adoption order for an existing app

1. **Upgrade, change nothing.** Everything is inert except the D-4 delta. Run
   the suite; fix or kill-switch any exact-equality error-body assertion.
2. **Adopt C5** if list endpoints are hot — set a `serializer=` if you want a
   non-default codec, then opt into the bulk path. No env var, no migration.
3. **Migrate the schema for T2** even if you will not use zoned schedules yet:
   `varco migrate adopt` (once, if you were on `ensure_table()`), then
   `varco migrate upgrade heads` in a pre-deploy job with
   `VARCO_MIGRATE_MODE=check` on the pods. Three nullable columns, no index, no
   backfill.
4. **Turn on T3's recommendation**: `assume="utc"`. Do this *before* enabling
   `"context"`, and check for `TIMESTAMP WITHOUT TIME ZONE` columns first —
   asyncpg rejects an aware datetime against one (D-10).
5. **Turn on T1** (`VARCO_TZ_ENABLED=true`) and confirm `tzdata` is present in
   the image; the startup check will tell you if it is not.
6. **Turn on I2 last** (`VARCO_I18N_ENABLED=true`) with a `DictMessageCatalog`
   and one non-English locale to validate the precedence chain, then move to
   `GettextMessageCatalog` + `pybabel` once the chain behaves.
7. **`VARCO_ERROR_PROBLEM_DETAILS=true` is the final, optional step** — it
   changes the media type to `application/problem+json`. Treat it as a versioned
   API change on your side, not a framework upgrade (D-3).

---

## Risks

- ⚠️ **ASSUMPTION — Babel / stdlib-`gettext` async and thread safety under
  concurrent requests.** Brief 002 flagged this as a required spike (Evidence
  Gaps 1 & 2) and reported Flask-Babel's `force_locale` leaking across requests
  (issue #117). D-1's design avoids the reported cause — **no process-global
  `activate()`**, locale lives only in X1's `ContextVar`, catalogs immutable
  after `start()` — but stdlib `GNUTranslations` internals have not been
  independently audited here. *Mitigation:* step 30 is a blocking gate for Phase
  2, exercising 200 concurrent locale scopes plus a thread-pool read against one
  shared catalog. If it fails, the fallback is one catalog instance per locale
  held in an immutable dict (already the shape) with any mutable lookup path
  copied per call.
- ⚠️ **ASSUMPTION — no production-ready Python RFC 9557 (IXDTF) parser exists.**
  Brief 004 §A4 / Evidence Gap 1: `whenever` shows the shape without documenting
  compliance; `dateutil.isoparse` and stdlib `datetime` reject the bracket
  suffix. Not re-verified at implementation time. *Mitigation:* D-9 ships an
  **emitter only**; the coercer rejects a bracketed input with a legible error
  naming the two supported forms. Because storage (D-7's three columns) is
  deliberately independent of the wire format, a parser landing later is an
  additive branch in one function, not a storage change. Re-check before Phase 5
  only to decide whether to *also* accept the format, never to change storage.
- ⚠️ **ASSUMPTION — `MessageCatalog` wiring into the FastAPI exception handlers
  and providify DI is unproven.** Brief 003 flagged this spike. Two concrete
  hazards: (a) the `ContextVar`-reset-before-`ErrorMiddleware` ordering problem
  (RD-3), mitigated by the `request.state` mirror and step 34, which is the
  gate for Phase 2's middleware work; (b) providify's quoted-return-annotation
  pitfall, which poisons **every** binding in the container, mitigated by step
  36 extending `test_di_binding_health.py`. If the exception-handler wiring
  proves intractable, the documented fallback is codes-only rendering at the
  handler and client-side localization — I1 alone already delivers that, which
  is exactly why Phase 1 is ordered before Phase 2.
- ⚠️ **ASSUMPTION — asyncpg's naive-vs-aware datetime behaviour, on which
  D-10's default rests.** The claim is that asyncpg rejects an aware `datetime`
  against a `TIMESTAMP WITHOUT TIME ZONE` column, which is why `"utc"` is not
  the default. Not re-verified against this workspace's pinned asyncpg.
  *Mitigation:* the decision is conservative in the safe direction — if the
  claim is wrong, `"naive"` is still byte-identical to today and the only cost
  is that the recommended setting stays one opt-in away. Verify with a
  `@pytest.mark.integration` Postgres case in step 61 before writing the docs
  paragraph; if asyncpg is tolerant, say so in the doc rather than changing the
  default mid-plan.
- ⚠️ **ASSUMPTION — tzdata version drift between pods computing the same zoned
  schedule.** Two pods on different base images can hold different tzdata and
  materialize different `run_at` values for the same `(wall, tz, fold)`. This is
  inherent to the model, not introduced by it. *Mitigation:* `run_at` is written
  **once**, at enqueue, by whichever pod enqueued — so drift changes nothing
  after the fact; only the opt-in `ScheduleRematerializer` can rewrite it, and it
  writes only on an actual change, fenced with `expected_epoch=`. The operator
  obligation (pin tzdata in the base image, or run the rematerializer on a
  single designated deployment) goes in the feature doc next to D-8.
- ⚠️ **ASSUMPTION — `varco_fastapi`'s middleware ordering block
  (`app.py:60-68`) is the only place ordering is established.** RD-3's insertion
  point depends on it. *Mitigation:* read the block in full before step 33; if
  routers or lifespans add middleware elsewhere, insert there too rather than
  leaving `LocalizationMiddleware` outside `TenantResolutionMiddleware` — the
  tenant-default precedence step silently degrades to "no tenant default" if the
  order is wrong, which is a *quiet* wrong answer, not an error.
- **The `params` dict is a new exfiltration surface.** `error_params()` is
  exactly the kind of method someone later fills with `vars(exc)`. The invariant
  that must hold: **no built-in's params ever contains `reason`, a credential, or
  a raw identifier that the message does not already contain.** Guarded by step
  16 and stated in the feature doc; the pitfall row makes it visible to
  out-of-tree subclass authors too.
- **`GapPolicy.NEXT_VALID` deviates from brief 004's recommendation.** If the
  plan is later extended to recurring schedules (a Non-goal now), the default is
  wrong for that case — brief 004 is right that recurrences should skip.
  *Mitigation:* the deviation is recorded in D-8 and repeated in the feature doc
  next to the brief-004 citation, with the explicit note that an RRULE expansion
  model should default to `SKIP`.
- **Phase 4 is the only phase that can leave a database half-migrated.** The
  invariant: `0004` must be idempotent under **both** the dynamic-`0001` path and
  the stamped-`0003` path. Step 56 tests both; if either is dropped, a fresh
  install and an upgraded install diverge silently.
- **Six backlog items in one plan is a scope risk.** *Mitigation:* the phase
  graph is deliberately shallow — C5 (Phase 6) and T2 (Phase 4) are independently
  shippable, and Phase 1 alone delivers I1's stated value without I2. A
  shortened release cuts from the end: drop Phase 6, then Phase 5, then Phase 2.

---

## Docs obligations

Same change as the code, never a follow-up (repo rule).

### `technical_docs/features/` files

| File | Status | Contents |
|---|---|---|
| `error-taxonomy-and-i18n.md` | **create** (Phase 1) | The additive-hybrid envelope table; the `message_key` catalogue; why `FASTREST_*` is not renamed (D-5) and that `VarcoErrorCodes` is the same object; `error_params()`'s no-secrets rule; **the D-4 delta and its kill switch in the first screenful**; the `problem+json` opt-in and why it is not the default. |
| `i18n-and-localization.md` | **create** (Phase 2) | `MessageCatalog` ABC and the three implementations; the `pybabel extract/init/compile` authoring recipe (documented, not vendored — RD-7); the RFC 4647 Lookup behaviour; the five-source precedence table and the deliberate deviation from brief 002's ordering; RD-2's `TenantDefaultsProvider` and how to back it with the tenant catalog *if you want to*; RD-6's cache rule; PyICU / MessageFormat 2.0 / Fluent as extension points with D-1's reason each is not the default. |
| `timezone-handling.md` | **create** (Phase 3, extended in Phase 5) | T1's resolution chain; the **store-UTC-render-local** rule; the `tzdata`-in-slim-images startup check and the `varco-core[tz]` extra; T3's three policies, why the default is not the recommendation (D-10), the explicit-offset-always-wins rule, the date-only trap, and the **convert-the-bound-never-the-column** invariant; D-9's emitter-only position and the "when a parser lands" note. |
| `job-scheduling-and-leases.md` | **modify** (Phase 4) | New "Zoned schedules" section: D-7's *`run_at` is materialized, not replaced*; the three columns; D-8's gap/overlap table **with the `NEXT_VALID` deviation stated next to its brief-004 citation**; RD-5's declaration contract and the two-line upgrade instruction for third-party stores; the `ScheduleRematerializer` recipe with its tzdata-pinning operator note; the adopt-then-upgrade ordering. |
| `cache-hardening.md` | **modify** (Phase 6) | New C5 section: why `BulkCache` is a separate Protocol (D-11's `runtime_checkable` argument); the per-backend serializer defaults table; D-12's N-per-key decision and why a batched `kind="keys"` is refused; how `read_through_many` shares `Singleflight` slots with `read_through`. |

### `CLAUDE.md` — architecture subsections

- **New: "Ambient request context (varco_core.context)"** after the observability
  section. `AmbientVar` / `RequestContext` / `resolve_precedence`; the rule that
  `RequestContext` **never** holds the tenant (`current_tenant()` is the single
  source of truth); the note that module-scope `ContextVar` construction is
  correct and is *not* an exception to the lazy-`asyncio.Lock` rule.
- **New: "Internationalization (varco_core.i18n)"** — the ABC, the default-off
  posture, the precedence chain, `VARCO_I18N_*`, and RD-7's framework/app line.
- **New: "Timezones (varco_core.tz)"** — resolution chain, store-UTC-render-local,
  the schedule policies, `VARCO_TZ_*`.
- **Modify the error/exception coverage** — `message_key` + `params`, the D-4
  delta and `VARCO_ERROR_*` kill switches, and the correction that `code` is the
  *machine* identifier while `message_key` is the *i18n* key.
- **Modify "Background jobs — time, lease, fencing"** — the three additive
  columns, RD-5's `supports_zoned_schedules`, `ScheduleRematerializer`.
- **Modify "Query system"** — one paragraph on `DatetimeCoercionPolicy`, with
  `"utc"` named as the recommendation.
- **Modify "Cache system"** — `BulkCache`, the serializer seam, D-12.

### `CLAUDE.md` — new Common Pitfalls rows (verbatim text for the top five)

| **Error body gained `message_key`/`params` after upgrade** | An exact-equality assertion on an error response body fails after a version bump | Plan 011 / D-4 — the one deliberate wire delta: built-in varco exceptions now emit `message_key` (`varco.error.not_found`) and non-empty `params` as RFC 9457 extension members. An out-of-tree `ServiceException` with no `message_key` is unaffected | Assert on the keys you care about instead of the whole dict, or restore the exact pre-plan body with `VARCO_ERROR_INCLUDE_MESSAGE_KEY=false` / `VARCO_ERROR_INCLUDE_PARAMS=false` |

| **`tenant_id` expected in `RequestContext`** | `AttributeError`, or two disagreeing answers to "who is the tenant" | `RequestContext` deliberately holds only `locale`/`timezone`/`extras` (Plan 011 / D-6) — `TenantAwareService`, RLS, `tenancy_cache_key()`, the DLQ stamp and the audit trail all read `current_tenant()`, and a second source of truth is how they diverge | Call `current_tenant()`; compose by *ordering* (the tenant middleware runs before the localization middleware), never by containment |

| **Localized response cached and served to the wrong locale** | A `fr` body is returned to an `en` client | The cache key did not mention the locale — the i18n analogue of the cross-tenant cache leak, and easier to hit because localization is applied at render time, far from the cache call. varco never namespaces keys by locale implicitly (that would cold-start every cache) | Cache the **unlocalized** representation and localize at render time; where the cached artifact is itself localized, build the key with `localization_cache_key(base, locale=True)`, which fails closed (`RuntimeError`) with no ambient locale exactly like `tenancy_cache_key()` |

| **`enqueue(tz=...)` raises `ValueError` naming the store** | A zoned schedule is refused at enqueue time by a third-party `AbstractJobStore` | Plan 011 / RD-5 — a store must **declare** `supports_zoned_schedules = True`. An explicit-column store would otherwise silently drop `run_at_wall`/`run_at_tz`: the job still fires at the right instant, but re-materialization silently no-ops, so the DST safety asked for quietly isn't there (same class as the `lease_ttl` pitfall above) | Persist the three fields in your store, then set `supports_zoned_schedules = True`. Failing closed at enqueue is deliberate — it turns a silent degradation into a named error |

| **`assume="utc"` breaks a working datetime filter** | `asyncpg` raises on a query that worked before the policy was changed | asyncpg rejects an **aware** datetime against a `TIMESTAMP WITHOUT TIME ZONE` column — which is exactly why `"naive"` (today's behaviour) is the default and `"utc"` is only the *recommendation* (Plan 011 / D-10) | Migrate the column to `TIMESTAMPTZ`, or leave the policy at `"naive"` and have clients send an explicit offset (`2026-01-01T00:00:00Z`) — an explicit offset wins under every policy |

Further rows, same table, shorter treatment: *`?lang=` unsupported silently
ignored* (falls through by design, never a 400); *`Content-Language` missing*
(i18n disabled); *tzdata absent in a slim image* (startup error naming `pip
install tzdata`); *`GapPolicy.SKIP` job never runs* (terminal + `ScheduleGapError`
by design, not lost); *`__lte=2026-01-01` excludes Jan 1* (date-only is midnight
at the start of the day); *bracketed RFC 9557 input rejected* (emitter only, D-9);
*adding a method to `AsyncCache`* (breaks `isinstance()` for out-of-tree caches —
use `BulkCache`, D-11).

### `CLAUDE.md` — Decision Tree branches

- **New top-level branch**: *"Request-scoped ambient value (locale, timezone,
  anything else per-request)?"* → `varco_core.context` (`AmbientVar` +
  `RequestContext` + `resolve_precedence`); ↳ tenant? → **no**, use
  `current_tenant()`; ↳ HTTP resolution? → `varco_fastapi.middleware.
  LocalizationMiddleware` (one middleware, two toggles — RD-3).
- **New top-level branch**: *"Internationalization / localized output?"* →
  `varco_core.i18n` (`MessageCatalog` ABC + negotiation); ↳ a new catalog
  format (ICU, MF2, Fluent)? → implement the ABC, do **not** add a runtime
  dependency to `varco_core`; ↳ translatable entity data? → **app side**, a
  Non-goal (RD-7).
- **New top-level branch**: *"Timezone / scheduling?"* → `varco_core.tz`;
  ↳ per-request user zone? → `tz/resolve.py`; ↳ DST-safe one-shot schedule? →
  `tz/schedule.py` + the three `Job` columns (D-7); ↳ recurring/RRULE? →
  **Non-goal**, a future `Schedule` entity that produces `Job` rows exactly like
  these.
- **Modify the "ORM/database feature" branch** — note that a new framework-table
  *column* is a guarded revision in `varco_sa/migrations/versions/` plus the
  `Table` definition, and that `0001` is dynamic so the revision must be
  idempotent.
- **Modify the "Cache feature" branch** — a bulk/batch capability goes on
  `BulkCache` with a portable `CacheBackend` default, **never** on `AsyncCache`.

### `ARCHITECTURE.md` / `README.md`

`ARCHITECTURE.md`: module-map entries for `varco_core.context` (`ambient.py`,
`precedence.py`, `request.py`, `defaults.py`), `varco_core.i18n` (`catalog.py`,
`gettext_catalog.py`, `negotiation.py`, `resolve.py`, `settings.py`,
`cache_key.py`), `varco_core.tz` (`zones.py`, `settings.py`, `resolve.py`,
`schedule.py`, `format.py`), `varco_core.job.reschedule`,
`varco_core.query.policy`, `varco_fastapi.middleware.localization`,
`varco_fastapi.i18n`, and the `BulkCache`/serializer additions to the
`varco_core.cache` map. `README.md`: one bullet each for i18n, timezone/DST-safe
scheduling, and bulk cache operations, each linking its feature doc.
