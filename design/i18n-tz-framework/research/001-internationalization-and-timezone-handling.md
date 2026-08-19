# Research 001 — Internationalization & Timezone Handling as Framework Features

**Date:** 2026-08-19 · **Freshness matters:** Yes (RFC 9457/9557 finalized 2024, Python 3.9+ zoneinfo standard, JS Temporal Stage 4 in 2024)

## Question

What does a backend framework (async Python, 2026) owe on internationalization/localization and timezone handling?
Specifically:
1. **i18n/l10n scope**: What translation, content negotiation, and error-message localization capabilities are standard? Message format choices (gettext/ICU/Fluent)?
2. **Timezone handling**: Store UTC vs. wall-time-+IANA-zone; per-user/tenant timezone context; DST-safe scheduling; data coercion rules?
3. **What is framework responsibility vs. app responsibility**?

## Findings

### I18n/L10n: Scope and Components

**Backend frameworks all implement three pillars:**
- Text extraction + message catalogs (translation storage)
- Content negotiation (Accept-Language, explicit language selection)
- Per-request language/locale context (middleware/decorator pattern)

Django (6.0), Rails, Spring, ASP.NET Core, and NestJS all follow this shape. — [Django i18n documentation](https://docs.djangoproject.com/en/stable/topics/i18n/), [Django API with DRF: Effortless Internationalization](https://blog.devgenius.io/django-api-with-drf-effortless-internationalization-227b1b92f697?gi=273fe67a466e)

**Key finding**: There is no universal "backend i18n" vs. "frontend i18n" split. Backends localize:
- System messages (errors, validation)
- Stored content (article titles, product descriptions — via separate translation tables)
- Rendered formats (dates, numbers, currency)

— [Advanced Django internationalization - Lokalise Blog](https://lokalise.com/blog/advanced-django-internationalization/), [A comprehensive guide to multi-timezone support in Django](https://oluwatobi.dev/blog/a-comprehensive-guide-to-multi-timezone-support-in-django/)

### Content Negotiation: RFC 4647, RFC 9110, Precedence Chain

**Language-tag matching standard**: RFC 4647 (Matching of Language Tags) defines two algorithms — filtering (returns all matching tags) and lookup (returns one best match). Both are applied to Accept-Language headers per RFC 9110. — [RFC 4647: Matching of Language Tags](https://www.rfc-editor.org/rfc/rfc4647.html), [Accept-Language - Expert Guide to HTTP headers](https://http.dev/accept-language)

**BCP 47 language tags** (e.g., `en-US`, `pt-BR`, `de-AT`) are the standard; lookup fails gracefully (en-US → en → fallback). — [BCP 47 subseries](https://www.rfc-editor.org/info/bcp47/)

**Precedence hierarchy (accepted practice, confirmed by Django + Auth0 patterns)**:
1. Explicit user choice (query param `?lang=fr`, stored user profile)
2. Query-parameter hint (`?ui_locales=fr-CA+fr` — space-delimited, first is preferred)
3. Accept-Language HTTP header (browser's declared preference; matched via RFC 4647 lookup)
4. Tenant/application default language
5. Fallback (typically `en`)

— [Universal Login Internationalization - Auth0 Docs](https://auth0.com/customize/internationalization-and-localization/universal-login-internationalization), [MDN Accept-Language header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language)

### Error-Message Localization: RFC 9457 Strategy

**RFC 9457 Problem Details (HTTP error standard) allows two strategies**:

1. **Server-side localization via content negotiation** — `title` and `detail` fields are localized using Accept-Language. Spec: "the language used for human-readable strings can be negotiated using the Accept-Language request header." Response includes `Content-Language: en` header.

2. **Stable machine-readable error codes + client-side localization** — `code`/`type` fields (e.g., `VARCO_INVALID_FORMAT`) are stable; client translates the message. RFC cautions: "Consumers SHOULD NOT parse the 'detail' member for information" — it is for human eyes only, not machine extraction of structured data.

**Key insight**: RFC 9457 does NOT mandate server-side translation. The spec supports both approaches; choice is architectural. — [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.pdf), [Problem Details (RFC 9457): Doing API Errors Well](https://swagger.io/blog/problem-details-rfc9457-doing-api-errors-well/)

Varco's current approach (stable error codes + message) aligns with RFC 9457's acceptance of client-side localization. Extending this requires only an optional `accept-language` request scope + a message catalog registration hook.

### Message Catalog Formats: gettext vs. ICU MessageFormat vs. Fluent

| Format | Strengths | Weaknesses | Status |
|--------|-----------|-----------|--------|
| **gettext** | Mature, universal, simple, .po/.pot files | No pluralization/gender/date formatting; custom plural rules per language | Legacy standard (30+ years), still widely used but deprecated for new projects |
| **ICU MessageFormat** | Handles plurals, gender, number/date/time formatting; CLDR-backed; standardized | Complex syntax; more moving parts; newer MessageFormat 2.0 (Technical Preview) | Industry standard for complex localization; adopted by major i18n libraries (i18next, etc.) |
| **Fluent** | Mozilla project; intuitive syntax; CLDR/ICU/ECMA402 integration; handles all cases gettext misses | Newer ecosystem (fewer integrations); requires dedicated parser | Recommended over gettext for new multilingual software; gaining adoption |

**Python maturity**: gettext + Babel (extraction/compilation) is standard. ICU via `babel` or `icu` package (C library binding). Fluent via `fluent.runtime` (limited ecosystem). — [I18N in the Multiverse of Formats - Locize Blog](https://www.locize.com/blog/i18n-formats-javascript), [Fluent vs gettext · projectfluent/fluent Wiki](https://github.com/projectfluent/fluent/wiki/Fluent-vs-gettext), [ICU Message Format Guide - Crowdin Blog](https://crowdin.com/blog/icu-guide)

**Recommendation for varco**: Start with **gettext + Babel** (minimal, proven). Upgrade to **ICU MessageFormat** only if date/time/number localization or gender agreement becomes a requirement. Fluent is overkill unless Mozilla-grade i18n is a selling point.

### Data Localization: Translatable Content in the Database

**Two patterns are standard**:

1. **Separate translation table** — Main entity lives in one table; translations in a `translations` table keyed by entity ID + locale. Example: `Article` + `ArticleTranslation(article_id, locale, title, body)`. — Rails Globalize, Django-parler. — [Storing Translations in the Database with Rails and Globalize - Bomberbot](https://www.bomberbot.com/rails/storing-translations-inside-database-globalize/), [GitHub - globalize/globalize](https://github.com/globalize/globalize), [Django Multi-Language Support: i18n, django-parler Guide 2026](https://anomixlabs.com/en/insights/django-multilingual-i18n-parler-guide/)

2. **Denormalized locale-specific columns** — `Article.title_en`, `Article.title_fr`, etc. Simpler queries but schema sprawl. Rarely used in modern frameworks.

**Framework responsibility**: Provide an ABC for translatable entity patterns + optional mixin for Rails Globalize-style transparent fallback loading. App defines which fields are translatable via metadata.

### Timezone Handling: Truth Model and Storage

**Consensus best practice (backed by Temporal API design, RFC 9557, real-world scheduler experience)**:

**For immediate/past events**: Store as UTC instant (unambiguous). When rendering, convert to user's timezone for display.

**For future scheduled events** (job run times, recurring reminders, conference scheduling): Store **wall-clock time + IANA timezone name** (e.g., `2026-12-25 08:00:00 America/New_York`), NOT UTC. Reason: If you store only UTC, you lose the user's intent. When DST rules change (tzdata updates), the same instant shifts to a different local time, confusing end users. The wall-clock time + zone is the source of truth. — [Instant vs Local – When UTC Helps and When It Hurts - DEV Community](https://dev.to/bwi/instant-vs-local-when-utc-helps-and-when-it-hurts-5d7p), [Why we should use IANA Time Zones, Not Just Offsets - Medium](https://medium.com/@rongalinaidu/why-we-should-use-iana-time-zones-not-just-offsets-b3e19d005cc7)

**Critical rule**: Never store a UTC offset alone (e.g., `-05:00`). Offsets are meaningless across DST transitions and tzdata updates. Always use IANA zone IDs (e.g., `America/New_York`). — [The Complete Guide to Time Zones - ToolPop](https://toolpop.org/en/blog/timezone-conversion-guide/), [The IANA Time Zone Database - Time.so](https://time.so/articles/iana-time-zone-database/)

### Timezone Representation: RFC 9557 (IXDTF)

**RFC 9557 (finalized October 2024)** standardizes optional timezone suffixes on RFC 3339 timestamps.

Format: `1996-12-19T16:39:57-08:00[America/Los_Angeles]` — the `[America/Los_Angeles]` suffix is optional, backward-compatible, and standardizes what was previously done ad-hoc. — [RFC 9557: Date and Time on the Internet](https://www.rfc-editor.org/rfc/rfc9557.pdf), [RFC 9557 - Timestamps with Additional Information | RFC Editor](https://www.rfc-editor.org/info/rfc9557/)

**Impact**: Enables serialization of zoned datetimes without losing the original timezone context. JavaScript Temporal API (Stage 4, 2024) depends on RFC 9557 for interop. — [Temporal.ZonedDateTime - MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Temporal/ZonedDateTime), [JavaScript Temporal API after 9 Years - Groundy](https://groundy.com/articles/javascript-s-date-problem-finally-fixed-temporal-api-after/)

### Python: zoneinfo (PEP 615) vs. pytz Deprecation

**PEP 615 (Python 3.9+)** introduced `zoneinfo` module to the standard library, providing first-class IANA timezone support. — [PEP 615 – Support for the IANA Time Zone Database](https://peps.python.org/pep-0615/)

**pytz is deprecated**:
- Uses non-standard `tzinfo` workarounds for DST ambiguity
- Python 3.6+ added the `fold` attribute (PEP 495) to cleanly disambiguate overlapping times during DST fall-back
- `zoneinfo` builds on `fold`, making pytz's workarounds obsolete
- Major projects (Fedora, Django contributors) actively migrate to `zoneinfo`

— [pytz-deprecation-shim - PyPI](https://pypi.org/project/pytz-deprecation-shim/), [Proposal: Drop dependency on pytz in favor of zoneinfo](https://groups.google.com/g/django-developers/c/PtIyadoC-fI), [Changes/DeprecatePytz - Fedora Project Wiki](https://fedoraproject.org/wiki/Changes/DeprecatePytz)

**tzdata packaging**: `zoneinfo` reads system tzdata (Unix/Linux) or falls back to PyPI `tzdata` package. Container images typically include `tzdata` OS package. For Python-only environments, `pip install tzdata` is required.

### Per-User/Tenant Timezone as a Framework Feature

**Standard pattern (Django, Rails, ASP.NET Core)**:

Django: `django.utils.timezone.activate(tz)` sets a thread-local (or request-scoped) context. Middleware fetches user's timezone from profile and calls `activate()`. All `datetime.now()` calls thereafter use the activated zone. — [Time zones | Django documentation](https://docs.djangoproject.com/en/6.0/topics/i18n/timezones/), [Django Friday Tips: Timezone per user](https://blog.ovalerio.net/archives/1029)

Rails: `Time.zone` is request-scoped; set via `around_action` or middleware. — [How to Set the Timezone in Django](https://thelinuxcode.com/how-to-set-the-timezone-in-django-practical-modern-and-safe/)

**Async/contextvars concern** (relevant to varco): A request-scoped timezone context in async code must use `contextvars.ContextVar`, not thread-local storage. Django's async support handles this; varco can follow the same pattern. No known hazards beyond the standard contextvars complexity.

### Timezone-Aware Scheduling: DST and Best Practices

**Quartz Scheduler (Java)**: Supports timezone-aware CronTriggers. When configured with an IANA timezone, it automatically adjusts for DST. However, "CronTriggers are subject to oddities when DST transitions occur" — a job scheduled for 1:05 AM may run zero, one, or two times on a DST transition day. **Best practice**: Avoid scheduling fixed-time jobs during DST transition windows (1:00–3:00 AM in US). Schedule jobs at 4:00 AM or later, or in UTC. — [Integrating Quartz Scheduler with Daylight Savings Support - Medium](https://rajasekar-sambandam.medium.com/integrating-quartz-scheduler-with-daylight-savings-support-ddeb7ac19014), [Best Practices - Quartz Scheduler](https://www.quartz-scheduler.org/documentation/quartz-2.5.x/best-practices.html)

**Temporal.io (workflow engine)**: The underlying Cron library does NOT handle DST specially. Temporal recommends **avoiding schedules that fall within DST transition periods**. If a schedule hits a DST boundary, it may skip or repeat. — [Improve DST handling in schedules · Issue #8205 - temporalio/temporal](https://github.com/temporalio/temporal/issues/8205), [Temporal Cron Job | Temporal Documentation](https://docs.temporal.io/cron-job)

**Universal rule**: Always store future event times as **wall-clock time + IANA zone**, never UTC. When the scheduled instant arrives, convert to UTC for comparison. This ensures user intent survives tzdata updates. For recurring events, use library support (APScheduler's `CronTrigger` with timezone, Quartz with timezone) or implement the semantics carefully.

### JavaScript Temporal API: Standardization of ZonedDateTime (Stage 4, 2024)

**Temporal API reached Stage 4 in June 2024**, becoming part of ECMAScript 2026 specification.

**Key types**:
- `Temporal.Instant` — Exact moment in UTC, no timezone (nanosecond precision)
- `Temporal.PlainDateTime` — Local date/time with no timezone (e.g., "2024-03-10 02:05:00" in America/New_York could be ambiguous)
- `Temporal.ZonedDateTime` — Local time + IANA timezone + UTC offset (the proper type for user times)

**DST semantics**:
- Spring forward ("gap"): 2024-03-10 02:05:00 does not exist in America/New_York. ZonedDateTime normalizes it to the next valid time.
- Fall back ("overlap"): 2024-11-03 01:05:00 occurs twice. ZonedDateTime uses the `offset` parameter to disambiguate (or the `fold` attribute in Python).

**Interop with RFC 9557**: Temporal serializes ZonedDateTime as `2024-11-03T01:05:00-04:00[America/New_York]` (RFC 9557 format). — [Temporal API JavaScript: 9-Year Journey to Stage 4 - byteiota](https://byteiota.com/temporal-api-javascript-9-year-journey-to-stage-4/), [Temporal.ZonedDateTime - MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Temporal/ZonedDateTime), [JavaScript's Date Problem Is Finally Fixed - Groundy](https://groundy.com/articles/javascript-s-date-problem-finally-fixed-temporal-api-after/)

### Query Coercion and Timezone-Aware Filtering

**Current state**: varco's `QueryApplicator` does not coerce datetimes to user timezone for filter predicates. A query like `?created_at__gte=2024-01-01T00:00:00` is interpreted literally (UTC), not in the user's timezone.

**Framework responsibility**: If per-user timezone is enabled, query filters should:
- Accept RFC 3339 datetimes without timezone (implicitly user's timezone)
- OR accept explicit timezone via RFC 9557 suffix
- Convert to UTC before applying the SQL/Mongo predicate

This is an **opt-in feature** (not on by default) because it changes filter semantics.

## Options Compared

### I18n/L10n: Server-Side Translation vs. Stable Error Codes + Client Translation

| Aspect | Server-Side Localization | Stable Error Codes + Client Translation |
|--------|--------------------------|------------------------------------------|
| **i18n responsibility** | Backend owns all translation strings | Backend provides codes; client owns translation |
| **RFC 9457 alignment** | Supported (via content negotiation on `title`/`detail`) | Supported (via stable `code`/`type` fields) |
| **Latency** | Catalog lookup per error response | Zero latency (codes are static strings) |
| **Flexibility** | Server must ship new translations to change messages | Client can update translations without server redeploy |
| **Precedent** | Django, Rails, Spring (all localize on server) | API gateways, microservices (stable codes) |
| **Varco fit** | Good if varco owns user-facing error UX | Better if client is the translation owner |

**Recommendation for varco**: Hybrid approach — Always return stable error codes (e.g., `VARCO_VALIDATION_FAILED`) + a machine-readable `message_key` (e.g., `field.required`). Optionally support server-side localization via a registered `MessageCatalog` (pluggable) for teams that want it, but do NOT require it. This keeps the framework lightweight while enabling both camps.

### Timezone Storage: UTC Instant vs. Wall-Clock + IANA Zone

| Aspect | UTC Instant Only | Wall-Clock + IANA Zone |
|--------|------------------|------------------------|
| **Immediate events** | ✅ Unambiguous | ✅ Requires conversion; extra storage |
| **Future scheduled events** | ❌ Loses intent; breaks on tzdata changes | ✅ Survives tzdata updates; preserves user intent |
| **DST edge cases** | N/A (instant is fixed) | ✅ Handled correctly; no ambiguity |
| **Query range predicates** | ✅ Simple (direct UTC comparison) | ❌ Requires per-user tz conversion |
| **Storage overhead** | Minimal (single column) | +1 string column per datetime |

**Recommendation for varco**: Always store **both** (the standard practice):
- `run_at_utc`: UTC instant (for queries, comparisons, exact wall-clock reconstruction)
- `run_at_tz`: IANA zone name (for DST-aware future scheduling, user intent)

For non-scheduled datetimes (e.g., `created_at`, `updated_at`), UTC-only is sufficient. For anything a user can schedule into the future or that recurs, include the zone.

## Version/Compatibility Notes

- **Python i18n**: `zoneinfo` added in Python 3.9 (PEP 615). varco targets 3.12+, so no compatibility issue. pytz is deprecated as of 2024; do not use in new code.
- **RFC standards**: RFC 4647 (language matching, 2006), RFC 9110 (HTTP semantics, 2022), RFC 9457 (Problem Details, 2023), RFC 9557 (IXDTF, 2024). All are current stable RFCs.
- **Django i18n**: Available in all modern Django versions (4.x+, 6.x current). No breaking changes expected.
- **Temporal API**: Stage 4 as of June 2024 (TC39 plenary March 2026 formal announcement); shipping in modern browsers and Node.js 22+. Python equivalent: use `zoneinfo` + `datetime.fold`.
- **tzdata**: System package or `pip install tzdata`. Container images should include OS `tzdata` package. Fallback to PyPI `tzdata` is automatic in `zoneinfo`.

## Evidence Gaps

1. **Multi-platform i18n best practice**: Research surveyed Django, Rails, Spring, ASP.NET Core conceptually. Detailed feature-by-feature comparison (e.g., exact ICU MessageFormat integration paths in each framework) not fully sourced. Worth a separate brief: "i18n Backend Implementation Patterns 2026."

2. **Varco-specific async contextvars hazards**: Theory says `contextvars.ContextVar` works for request-scoped timezone context in async code; not verified against varco's specific EventConsumer/middleware stacking. Needs spike: "Async Context Propagation in Varco Event System."

3. **Query coercion semantics**: No existing backend framework seems to have "timezone-aware filtering" as a standard feature. Unclear if this is a genuine gap or simply an "app-level concern." Worth research: "Timezone-Aware Predicates in ORM Query Builders."

4. **Translatable entity patterns**: Rails Globalize and Django-parler documented; Hibernate behavior not fully sourced. No survey of whether the pattern generalizes to varco's `DomainModel` + `SAModelFactory` flow.

5. **Error message catalogs in async services**: Varco's service layer uses injection + abstract producer/consumer pattern. How error catalogs (pluggable MessageCatalog ABC) wire into exception handling + event emission is unproven. Needs a spike.

6. **Multitenancy + per-tenant i18n**: Does "tenant language" (separate from user language) make sense? Current brief assumes per-user. Tenancy brief covers isolation; i18n tenancy overlap is untested.

## Librarian's Note

**What the evidence favors:**

**I18n/L10n**: Backend frameworks universally adopt the trilogy of text extraction → message catalogs → per-request language context. varco should offer:
1. **Optional `MessageCatalog` ABC** (pluggable, defaults to None/"no translation"). When registered, error responses localize via Accept-Language negotiation. Keeps the framework neutral on message format (gettext, ICU, Fluent, or custom).
2. **Built-in `Accept-Language` + `?lang=` content negotiation** (RFC 4647 lookup, simple precedence chain). Standard middleware for request-scoped language context.
3. **Error responses stay machine-readable** — always return stable error codes (e.g., `VARCO_VALIDATION_FAILED`) + optional `message_key` (e.g., `field.required`). Human-readable `message` is secondary and localizable via catalog.
4. **Translatable entity pattern** — optional mixin or example (Rails Globalize-style or separate translation table) in docs. Do NOT bake into the framework; too app-specific.

**Timezone handling**: Evidence strongly favors RFC 9557 semantics + `zoneinfo`:
1. **Store UTC for past/immediate, wall-clock+IANA zone for scheduled** (forward-compatible, DST-safe).
2. **Request-scoped timezone context via `contextvars`** — optional per-user/tenant timezone that affects `datetime.now()` and query predicates (if enabled).
3. **`Job.run_at` should accept both** — UTC instant (default) OR wall-clock+zone (when provided, stored in separate column). `JobScheduler` normalizes both to UTC for comparison.
4. **Query coercion opt-in** — timezone-aware predicates only when `per_user_timezone=True` on the service. Default is UTC-only (simpler, no surprises).
5. **Exclude DST-buggy times from default scheduling** — document the gotcha, suggest 4:00 AM+ or UTC for recurring jobs.

**Framework responsibility (clear line)**:
- ✅ Content negotiation, request-scoped language/timezone context, error code standardization
- ❌ Message authoring, translatable entity design, scheduling algorithm

Decision readiness: **I18n is ~70% clear** (hybrid codes + optional localization is safe and proven). **Timezone is ~90% clear** (wall-clock+zone + `zoneinfo` is the modern standard; only query coercion details need a spike).

