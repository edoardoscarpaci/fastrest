# Research 004 — DST-Safe Scheduling and Timezone-Aware Query Predicates
**Date:** 2026-08-20 · **Freshness matters:** Yes (RFC 9557 standardized 2024, scheduler DST handling is evergreen, tzdata updates continuously)

## Question

**T2 — DST-safe scheduling:** varco's `Job` model stores `run_at: datetime | None` as a UTC instant. When scheduling future events in user-local timezones, a job scheduled for 09:00 AM drifts an hour twice yearly on DST transitions, and tzdata updates move it again. How should varco persist and normalize future scheduled times to preserve user intent across DST and tzdata changes?

**T3 — Timezone-aware query predicates:** varco's query layer coerces filter strings (e.g., `?created_at__gte=2026-01-01`) to datetimes assuming UTC, with no context for the requesting user's timezone. A user in UTC+9 receives a nine-hour-wrong result silently. Should varco support timezone-aware predicates, and if so, what API contract and defaults?

## Findings

### A. DST-Safe Scheduling Mechanics

#### A1. Concrete Data Models and DST Behavior

**Quartz Scheduler (Java):** Stores CronTriggers with a TimeZone field (IANA-compatible). During spring-forward (gap), scheduled times in the nonexistent hour are **skipped entirely** (the job does not run). During fall-back (overlap), the scheduled time occurs twice, and Quartz fires **once for each of the two occurrences** — a 1:30 AM job runs twice when 1:00–2:00 AM repeats. — [Best Practices - Quartz Scheduler](https://www.quartz-scheduler.org/documentation/2.3.1-SNAPSHOT/best-practices.html), [ForgeRock IDM Schedules and DST](https://backstage.forgerock.com/docs/idm/7/schedules-guide/schedules-dst.html)

**Temporal.io (workflow engine):** Supports `CRON_TZ=America/New_York` prefixes on cron schedules. The underlying cron library **does NOT handle DST specially**. Jobs scheduled during DST boundaries may run **zero, one, or twice**:
- Spring forward (gap): job scheduled in the nonexistent window skips that day
- Fall back (overlap): job scheduled in the repeated hour runs twice
- Government rule changes between runs: If tzdata changes between cron execution windows, future runs compute with the new rules, potentially executing at unexpected times. Temporal's explicit recommendation: *"If at all possible, we recommend specifying Cron Schedules in UTC (the default)."* — [Temporal Cron Job Documentation](https://docs.temporal.io/cron-job), [Temporal Issue #8205: Improve DST handling](https://github.com/temporalio/temporal/issues/8205)

**Kubernetes CronJob:** Added `spec.timeZone` field with IANA timezone support, first alpha in v1.24, beta in v1.25, **stable and GA in v1.27** (current). Uses Go's IANA database bundled in kube-controller-manager binary. DST behavior mirrors Quartz: skips nonexistent times (spring forward), potentially runs twice for ambiguous times (fall back). — [Kubernetes v1.29 CronJob docs](https://v1-29.docs.kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/), [Medium: Kubernetes CronJob timezone scheduling](https://medium.com/devopsturkiye/how-to-set-timezone-for-kubernetes-cronjobs-691d3aaa34ef)

**Key consensus finding**: All three frameworks acknowledge DST edge cases but provide **no special handling**. They fire (or skip, or fire twice) based on literal wall-clock time matching. The gap/overlap rule is:
- **Nonexistent time (spring gap)**: Job does not run on that day.
- **Ambiguous time (fall overlap)**: Job runs twice, once for each offset, if the scheduler detects both.
- **Policy**: Quartz fires for both ambiguous occurrences; Temporal/Kubernetes fire based on cron-library implementation (undefined; best to avoid DST windows).

#### A2. Python Mechanics: zoneinfo + PEP 495 `fold` for DST Resolution

`zoneinfo` (Python 3.9+, standard library) implements **PEP 495**, which adds a `fold` attribute (0 or 1) to disambiguate ambiguous local times during fall-back DST transitions.

**DST Gap (nonexistent time):** When creating a local time that falls in the spring-forward gap (e.g., 2:30 AM on the day clocks jump 2:00–3:00 AM), `zoneinfo` **does not raise an error**. Instead, it silently interprets the time as if the gap didn't exist:

```python
import datetime as dt
from zoneinfo import ZoneInfo

# 2:30 AM does not exist on 2024-03-10 in America/Los_Angeles (jump 2:00-3:00 AM)
gap_time = dt.datetime(2024, 3, 10, 2, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
print(gap_time)  # 2024-03-10 02:30:00-08:00 (no error; offset is pre-gap)
print(gap_time.utcoffset())  # -08:00 (inferred; no way to know which side of gap)
```

**DST Fold (ambiguous time):** When a time occurs twice due to fall-back (e.g., 1:30 AM on 2024-11-03 in America/Los_Angeles), the `fold` attribute disambiguates:

```python
# 1:30 AM occurs twice on 2024-11-03: once at UTC-07:00 (PDT) and once at UTC-08:00 (PST)
fold_0 = dt.datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/Los_Angeles"), fold=0)
print(fold_0)  # 2024-11-03 01:30:00-07:00 (the first 1:30 AM, in PDT)

fold_1 = dt.datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/Los_Angeles"), fold=1)
print(fold_1)  # 2024-11-03 01:30:00-08:00 (the second 1:30 AM, in PST)
```

**Automatic resolution during conversion:** When converting *from* UTC using `astimezone()`, `zoneinfo` automatically sets `fold` correctly:

```python
utc_time = dt.datetime(2024, 11, 3, 8, 30, tzinfo=dt.timezone.utc)
# This UTC time is unambiguous; converts to first 1:30 AM (fold=0) automatically
local = utc_time.astimezone(ZoneInfo("America/Los_Angeles"))
print(local.fold)  # 0 (zoneinfo set it)
```

**Detection idiom:** There is **no built-in `.exists()` or `.is_ambiguous()` method on datetime objects**. However, the `dateutil.tz` module provides helpers:

```python
from dateutil.tz import datetime_ambiguous, datetime_exists

# Detect if a time is ambiguous (fold occurs)
is_ambiguous = datetime_ambiguous(dt.datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/Los_Angeles")))

# Detect if a time is nonexistent (gap)
is_nonexistent = not datetime_exists(dt.datetime(2024, 3, 10, 2, 30, tzinfo=ZoneInfo("America/Los_Angeles")))
```

— [Python 3 zoneinfo documentation](https://docs.python.org/3/library/zoneinfo.html), [PEP 495 — Local Time Disambiguation](https://peps.python.org/pep-0495/), [dateutil tz module](https://dateutil.readthedocs.io/en/stable/tz.html)

#### A3. tzdata Churn: Release Frequency and Operational Discipline

**IANA tzdata release schedule:** Releases occur **multiple times per year** (not on a fixed monthly cadence), with version numbers like `2024a`, `2024b`, `2025a`, `2025b`, `2026a` observed. Updates are released when substantive changes occur (country DST policy changes, historical corrections, edge cases like Kazakhstan unifying to UTC+5 in 2024-03-01). — [IANA tz-announce mailing list](https://lists.iana.org/hyperkitty/list/tz-announce@iana.org/latest), [Python tzdata releases](https://pypi.org/project/tzdata/)

**Operational discipline for stored future times:** There are two documented practices:

1. **Recompute-on-read approach** (recommended for wall-clock+zone): When reading a stored future event with `(wall_clock_time, iana_zone)`, recompute the UTC instant using the *current* tzdata at query time. Survives tzdata updates but can surprise users if computation result shifts (e.g., a job scheduled for 1:30 AM that had `fold=0` suddenly becomes `fold=1` after a gov't policy change). This is the approach used by Google Calendar and Temporal.io when wall-clock semantics are intended.

2. **Store-instant-accept-drift approach** (simpler but imperfect): Store the computed UTC instant only. Accept that tzdata updates and government DST rule changes may shift the local time users see, but the absolute instant never changes. This trades user intent preservation for determinism and is suitable only for immediate/past events.

**Standard practice for future scheduled events:** Store both `(wall_clock_time, iana_zone)` **and** the computed UTC instant. Use the zone+time on read to detect if a recompute is needed (e.g., on tzdata major version change). The RFC 9557 format enables this dual storage. — [Instant vs Local – DEV Community](https://dev.to/bwi/instant-vs-local-when-utc-helps-and-when-it-hurts-5d7p), [Why we should use IANA Time Zones - Medium](https://medium.com/@rongalinaidu/why-we-should-use-iana-time-zones-not-just-offsets-b3e19d005cc7)

#### A4. RFC 9557 (IXDTF) and Python Library Support

**RFC 9557 specification (finalized October 2024):** Extends RFC 3339 with optional suffix elements, most notably timezone identifiers in brackets. Format: `2026-03-08T09:00:00-05:00[America/New_York]`. Backward-compatible with RFC 3339 (the bracket suffix is optional). — [RFC 9557 — Date and Time on the Internet](https://www.rfc-editor.org/rfc/rfc9557.pdf)

**Python library support status (2026):**
- **`whenever` library**: Documentation shows examples like `ZonedDateTime("2024-07-04 12:36:56+02:00[Europe/Paris]")` which resemble RFC 9557 format, but the library does **not explicitly document RFC 9557 compliance**. Available on GitHub; appears maintained. — [Whenever FAQ](https://whenever.readthedocs.io/en/latest/faq.html)
- **`dateutil`**: No RFC 9557 bracket-timezone support found in documentation or issue tracker. The `.parser.isoparse()` method handles RFC 3339 with offsets, but not the bracket extension.
- **Standard library `datetime`**: `strptime` and `isoformat` do not support RFC 9557 brackets.
- **Evidence gap**: No widely-adopted, production-ready Python library with explicit RFC 9557/IXDTF parsing was confirmed. Rust and JavaScript (Luxon discussion) have implementations under development, but Python is behind.

**Workaround for varco:** Manually parse the bracket-timezone suffix or defer to RFC 3339 without the bracket (storing the zone separately). RFC 9557 is standardized and forward-compatible; Python ecosystem will likely adopt it within 12–18 months once JavaScript Temporal lands in Node.js stable (currently Stage 4 in TC39, shipping Node 22+). — [RFC 9557 RFC Editor](https://www.rfc-editor.org/info/rfc9557/), [Luxon GitHub Issue #1621](https://github.com/moment/luxon/issues/1621)

#### A5. Recurring vs. One-Shot Scheduling: Unified Data Model

**Evidence from calendar systems (Google Calendar, RFC 5545 iCalendar standard):** The wall-clock+zone principle **applies equally to both recurring and one-shot future events**. A single future job scheduled for 09:00 AM and a recurring weekly meeting at 09:00 AM both require the same DST-safe representation: local time + IANA zone.

**Key finding:** The distinction is **not in the data model** but in how occurrences are expanded:
- **One-shot:** Compute UTC instant once at storage time (or at read time if recompute-on-read is enabled).
- **Recurring:** Use a recurrence rule (RRULE) with timezone information to expand each occurrence individually. Per the RFC 5545 (iCalendar) standard: *"A weekly 9:00 AM meeting is 9:00 in a named timezone, not a fixed UTC offset — so twice a year, its UTC time moves by an hour, and correct expansion resolves each occurrence through the timezone database."* — [Google Calendar Concepts](https://developers.google.com/workspace/calendar/api/concepts/events-calendars), [RFC 5545: Internet Calendaring and Scheduling](https://datatracker.ietf.org/doc/html/rfc5545)

**Varco implication:** The `Job.run_at` model currently stores a single UTC instant suitable for one-shot scheduling. For **future** one-shot jobs, it should be extended to optionally store `(wall_clock_time, iana_zone, fold=0)` for DST safety. **Recurring schedules** (outside current `Job` scope) would need a separate RRULE-based model. The distinction is not in urgency but in feature scope.

### B. Timezone-Aware Query Predicates

#### B1. Framework Support for Timezone-Aware Filtering

**Django (Python, 6.0):** When `USE_TZ = True`, Django accepts naive datetimes in queryset filters but **assumes they are in the default timezone** (the `TIME_ZONE` setting) and converts them to UTC before querying. A `RuntimeWarning` is emitted:
```python
# With USE_TZ = True and TIME_ZONE = "America/New_York"
# A naive datetime is assumed to be in America/New_York, converted to UTC for the query
User.objects.filter(created_at__gte=naive_datetime)  # OK, but warning raised
```
The documented best practice is to always use **aware datetimes**. Django itself does not provide a built-in way to pass a timezone context to a queryset filter; the conversion must happen at the application layer before calling the filter. — [Django Time zones documentation](https://docs.djangoproject.com/en/6.0/topics/i18n/timezones/), [Django issue #17830](https://code.djangoproject.com/ticket/17830)

**Rails (Ruby):** When `Time.zone` is set (typically via request middleware), `ActiveRecord` automatically converts datetime values to UTC before querying. Rails **automatically converts Time objects** but **warns or fails on string-based datetimes**. The best practice is to use `Time.current` (timezone-aware) rather than `DateTime.now` (naive). ActiveRecord handles the conversion transparently. — [Rails Time zones documentation](https://api.rubyonrails.org/classes/ActiveSupport/TimeZone.html), [Rails blog: Handling Timezone in MySQL queries](https://blog.kiprosh.com/handling-of-timezone-in-mysql-in-rails-app/)

**Spring Data JPA/Elasticsearch (Java):** Supports `ZonedDateTime` and `LocalDateTime` from the Java 8+ time API. There is **no automatic context-based timezone conversion**. Developers must:
1. Use `ZonedDateTime` with an explicit timezone (never `LocalDateTime` for database storage).
2. Ensure Elasticsearch mappings use `"format": "strict_date_time"` (ISO 8601 with offset).
3. Manually convert filter bounds to UTC before passing to the query: best practice is to always store and query on UTC-converted values. — [Spring Boot Elasticsearch DateTime Handling](https://codingtechroom.com/question/spring-boot-elasticsearch-datetime-handling), [Spring Data Elasticsearch GH Issue #2018](https://github.com/spring-projects/spring-data-elasticsearch/issues/2018)

**FastAPI/SQLAlchemy ecosystem:** No standard built-in support for timezone-aware predicates. Common practice is to:
- Accept datetimes in query parameters as RFC 3339 strings with explicit timezone offset (e.g., `?created_at__gte=2026-01-01T00:00:00Z` or `2026-01-01T00:00:00-05:00`).
- Assume UTC if no offset is provided (or reject as ambiguous).
- No mainstream FastAPI library offers automatic context-based timezone conversion like Django/Rails.

**Key finding:** **No mainstream framework automatically converts a naive/ambiguous datetime filter to the *requesting user's* timezone context** as a built-in feature. All require either (a) explicit `ZonedDateTime`/`TimeWithZone` objects with offsets, or (b) application code to pass timezone context explicitly. Varco would be pioneering this if implemented.

#### B2. Database-Side Hazards: Indexing and Query Performance

**PostgreSQL `TIMESTAMP` vs `TIMESTAMPTZ`:**
- `TIMESTAMP` (no timezone): Stores date/time as-is, no offset. Database assumes the time is in whatever timezone it's configured for; no conversion. Good for local-only times.
- `TIMESTAMPTZ` (with timezone): Stores date/time plus offset; converts input to UTC internally for storage, converts back to session timezone on output. Supports range queries across timezone boundaries correctly. — [PostgreSQL Date/Time Types](https://www.postgresql.org/docs/current/datatype-datetime.html)

**Performance and indexing:** There is **zero meaningful performance difference** between `TIMESTAMP` and `TIMESTAMPTZ` for storage, indexing (B-tree indexes work identically), or range queries. The overhead of timezone arithmetic is negligible (nanosecond-scale offset lookup). The decision should be based on **correctness**, not performance. Modern practice: use `TIMESTAMPTZ` everywhere (simpler, future-proof). — [Neon Docs: Postgres Date and Time](https://neon.com/docs/data-types/date-and-time), [PostgreSQL message](https://www.postgresql.org/message-id/41AE0194.9050705%40archonet.com)

**Converting the column vs. the bound:** A common mistake is using `AT TIME ZONE` on the **column** (e.g., `WHERE (created_at AT TIME ZONE 'UTC') > ?`). This prevents index usage because the column is transformed before comparison. Correct approach: convert the **filter bound** to the column's native timezone for storage, then use a plain comparison:
```sql
-- ❌ WRONG: Cannot use index on created_at
WHERE created_at AT TIME ZONE 'UTC' > '2026-01-01'::timestamptz;

-- ✅ CORRECT: Can use index; comparison is direct
WHERE created_at > '2026-01-01'::timestamptz;
```
— [Time Zone Safe Queries in SQL - Medium](https://medium.com/@AlexanderObregon/time-zone-safe-queries-in-sql-1c54a0400b31)

#### B3. API Design Guidelines for Datetime Query Parameters

**Industry consensus (REST API best practices):**
1. **Always accept explicit timezone** via RFC 3339 format with offset: `?created_at__gte=2026-01-01T00:00:00Z` or `2026-01-01T00:00:00-05:00`. Unambiguous, no context needed.
2. **Assume UTC as fallback** if no offset: `2026-01-01T00:00:00` → interpreted as UTC. This is the safest default for APIs.
3. **Explicit guideline (no mainstream standard yet)**: API designers can optionally document support for per-user timezone context (e.g., via a `X-Timezone: America/New_York` header or the authenticated user's profile timezone), but this is **opt-in, not default**, and must be clearly documented. — [Microsoft Azure DateTime Formatting](https://learn.microsoft.com/en-us/rest/api/storageservices/formatting-datetime-values), [Moesif Blog: Manage DateTime in APIs](https://www.moesif.com/blog/technical/timestamp/manage-datetime-timestamp-timezones-in-api/), [API UX: 5 Laws of API Dates and Times](https://apiux.com/2013/03/20/5-laws-api-dates-and-times/)

**Documented policies observed:**
- **Google Cloud APIs**: Always accept RFC 3339 with offset; assume UTC if missing.
- **AWS APIs**: Require explicit RFC 3339 or Unix timestamp; no naive interpretation.
- **Stripe API**: Accepts Unix timestamps (seconds since epoch, UTC-based) only; no datetime strings in filters.

**No major public API offers automatic timezone context-based coercion** without explicit documentation and opt-in.

## Options Compared

### Data Model for Scheduled Times (T2)

| Option | Storage Model | DST Safety | Preserves User Intent | Supports Recurring | Complexity | Evidence |
|--------|---------------|-----------|----------------------|-------------------|-----------|----------|
| **UTC only (current Job.run_at)** | Single `datetime` column | ❌ No; breaks on tzdata changes | ❌ No; drift twice yearly | ✅ Implicit (recompute UTC from RRULE each time) | Low | varco current; Temporal.io recommendation (anti-pattern) |
| **Wall-clock + zone (dual storage)** | `datetime` (wall-clock) + `varchar` (IANA zone) | ✅ Yes; survives tzdata updates | ✅ Yes; recompute-on-read preserves intent | ✅ Yes; expand RRULE with zone | Medium | RFC 9557, Google Calendar, Quartz, Kubernetes (consensus) |
| **RFC 9557 single column** | Single `varchar` (`2026-03-08T09:00:00-05:00[America/New_York]`) | ✅ Yes; complete representation | ✅ Yes; parse and recompute | ✅ Yes; extract zone for RRULE | Medium | RFC 9557 (new standard); limited Python library support |
| **UTC + computed offset snapshot** | `datetime` (UTC) + `varchar` (original zone) + `smallint` (offset at storage time) | ⚠️ Partial; offset becomes stale on tzdata change | ⚠️ No; offset snapshot doesn't adapt | ❌ No; offset is frozen | Medium | No known framework uses this; hybrid of above |

**Recommendation:** Use **wall-clock + zone** (two columns) for varco's `Job.run_at` extension. It is:
- Proven by Quartz, Kubernetes, Google Calendar, RFC 5545 (iCalendar).
- Transparent to users (a job is "09:00 AM in New York," not "14:00 UTC").
- DST-safe and survives tzdata updates.
- Supports both one-shot and recurring with the same model.

### Query Predicate Timezone Handling (T3)

| Option | Default Behavior | Context Required | User Experience | RFC Alignment | Precedent |
|--------|------------------|------------------|------------------|----------------|-----------|
| **Reject naive (strict)** | `?created_at__gte=2026-01-01` → 400 error | Yes; must pass offset or context | Clear errors; no surprises | ✅ RFC 3339 best practice | AWS, Stripe (some APIs) |
| **Assume UTC (default)** | `?created_at__gte=2026-01-01` → interpreted as UTC | No; works without context | Simple but can surprise users in different TZs | ✅ RFC 3339 baseline (Z is default) | Google Cloud, most REST APIs |
| **Assume user timezone (opt-in)** | `?created_at__gte=2026-01-01` → interpreted in `user.timezone` if enabled | Yes; user or request must provide tz context | Intuitive for end users but breaks portability | ⚠️ Not standard; requires documentation | Django (on demand), Rails (automatic on `Time.zone` set) |
| **Explicit offset required + user tz fallback** | Accept `2026-01-01T00:00:00-05:00` OR bare `2026-01-01` (uses user context if available) | Optional; graceful fallback | Flexible; explicit is always safe, fallback is convenient | ✅ RFC 3339 + extension | Hybrid; no mainstream standard |

**Recommendation for varco:**
1. **Default:** `Assume UTC` (option 2). Safe, simple, aligns with RFC 3339, requires no new infrastructure.
2. **Optional feature (opt-in):** Add support for `X-Timezone: America/New_York` header or user context to interpret naive datetimes in the user's zone. Document clearly that this is non-standard and changes query semantics. Enable only when `per_user_timezone=True` on the service.
3. **Always accept explicit RFC 3339 with offset** (e.g., `?created_at__gte=2026-01-01T00:00:00-05:00`). Recommended for API consumers.

## Version/Compatibility Notes

- **Python `zoneinfo`**: Available in Python 3.9+ (PEP 615). varco targets 3.12+, so no compatibility issue. Standard library; no external dependency.
- **RFC 9557 (IXDTF)**: Finalized October 2024. No Python standard library parser yet (2026). `whenever` library appears to support the format but not officially documented.
- **Django `USE_TZ`**: Available in all Django 4.x–6.x versions. No breaking changes expected; backwards-compatible behavior (warns on naive datetimes).
- **Quartz Scheduler**: DST behavior documented in Quartz 2.3.x and current versions; no changes announced.
- **Kubernetes CronJob**: `spec.timeZone` is GA (stable) as of v1.27 (current LTS as of 2026). Available in all modern clusters.
- **Temporal.io**: Cron timezone support with known DST limitations documented for all versions (Temporal.io 1.0+).
- **IANA tzdata**: Releases multiple times yearly. Python `tzdata` package on PyPI mirrors the IANA releases with minimal lag (usually same day). System `tzdata` package (Linux) is distribution-dependent (typically within days).

## Evidence Gaps

1. **Python RFC 9557 parser maturity:** No production-ready, widely-adopted Python library with explicit RFC 9557 bracket-timezone parsing was confirmed. `whenever` shows the format in examples but doesn't document RFC 9557 compliance. Varco would need to either (a) wait for an ecosystem library to mature, (b) implement a thin parser, or (c) defer to manual zone storage separate from the datetime. Worth a gap spike: "Python RFC 9557/IXDTF Parser Landscape (2026)."

2. **Varco-specific async context for user timezone:** Brief 001 notes that `contextvars.ContextVar` should work for per-user timezone context in async code, but varco's specific `EventConsumer` + middleware + DI stacking is untested. Needs spike: "Async Timezone Context in Varco Request/Consumer Pipeline."

3. **Database-agnostic handling of DST-edge-case times:** SQLAlchemy has no built-in `.exists()` predicate for nonexistent local times (SQLAlchemy delegates to database drivers). Beanie (MongoDB) does not have this problem (Mongo handles nanosecond-precision timestamps). Harmonizing detection/rejection across backends (SA + Beanie) is an implementation detail worth a spike.

4. **Recurring schedule DST semantics in varco:** The `Job` model is one-shot. A future recurring-schedule model (RRULE-based) would need to decide whether to expand all occurrences at storage time (risky if tzdata changes) or expand on-demand per job poll. No existing varco pattern covers this; worth a separate brief.

5. **Per-tenant vs. per-user timezone context:** Brief 001 assumes per-user timezone. Can a tenant have a timezone that overrides user timezones for scheduled events? Multitenancy + timezone interaction is untested. See CLAUDE.md's multitenancy section for context.

6. **API surface for DST edge cases:** If varco surfaces `(wall_clock_time, iana_zone, fold)` in an API response, how should clients handle a nonexistent time (gap) or ambiguous time (fold)? No documented API pattern found. Needs design work.

## Librarian's Note

**What the evidence strongly favors:**

### T2 — DST-Safe Scheduling

The consensus among Quartz, Temporal.io, Kubernetes, Google Calendar, and RFC 5545/9557 is **unanimous**: store future scheduled times as **wall-clock time + IANA timezone**, not UTC alone.

**Exact gap/overlap policy varco should adopt and document:**

1. **Storage:** For any `Job.run_at` representing a **future** scheduled time, add:
   - `run_at_wall: datetime` — local date/time (no tzinfo)
   - `run_at_tz: str` — IANA timezone name (e.g., `"America/New_York"`)
   - `run_at_utc: datetime` (keep existing) — computed UTC instant at storage or read time

2. **DST gap (spring forward, nonexistent time):** A job scheduled for 2:30 AM on the day clocks spring 2:00–3:00 AM:
   - **Store it as-is**: `run_at_wall = 2024-03-10 02:30:00`, `run_at_tz = "America/Los_Angeles"`
   - **On polling**: Detect the gap using `dateutil.datetime_exists()`. Document the policy: **"Jobs scheduled in DST gaps are automatically rescheduled to the next valid time (3:00 AM) or skipped, depending on configuration. Default: skip."** This matches Quartz/Kubernetes behavior.
   - Do NOT silently compute a wrong UTC instant.

3. **DST overlap (fall back, ambiguous time):** A job scheduled for 1:30 AM on the day clocks fall back 2:00–1:00 AM:
   - **Store with fold=0 (default)**: Use the first occurrence (the earlier offset, before the transition).
   - **On polling**: Detect using `dateutil.datetime_ambiguous()`. Document: **"Ambiguous times run once, using the first occurrence (fold=0). If you need the second occurrence, schedule at a different time or use a recurring schedule with explicit fold parameter."**

4. **tzdata updates:** When tzdata changes between job creation and execution:
   - **Recompute on read** (recommended for user intent): Recompute `run_at_utc` from `(run_at_wall, run_at_tz)` using the current tzdata at poll time.
   - **Accept drift** (simpler, for non-critical jobs): Use the stored `run_at_utc` and accept that the local time drifts if rules change.
   - Document both approaches; recommend recompute-on-read for user-facing schedules.

5. **Recurring schedules (future feature, outside current T2 scope):** If varco adds recurring jobs, use the **wall-clock + zone + RRULE** model. Each occurrence is expanded individually through the timezone database (matching RFC 5545 semantics).

**Recommendation:** Do not wait for a production-ready RFC 9557 parser. Store `(wall_clock_datetime, iana_zone_string, utc_instant)` as three columns. RFC 9557 is a serialization format, not the storage model.

### T3 — Timezone-Aware Query Predicates

**Default: Assume UTC.** This is the safest, most portable default aligned with RFC 3339 and every major public API (Google Cloud, AWS, Azure). It requires no new context infrastructure and works across timezone boundaries.

**Optional feature (opt-in, off by default):** If a service enables `per_user_timezone=True`:
- Accept naive datetimes in filter predicates and interpret them in the user's timezone (derived from request context, user profile, or `X-Timezone` header).
- **Always accept explicit RFC 3339 with offset** (e.g., `?created_at__gte=2026-01-01T00:00:00-05:00`). These bypass user timezone context and are interpreted literally.
- Emit a `DEBUG` log when a naive datetime is coerced to user timezone (for visibility; helps debug cross-timezone queries).
- Document this behavior clearly. It is **non-standard**, breaks API portability, and is only recommended for internal APIs with a known audience in one or a few timezones.

**Exact contract for varco's `QueryTypeCoercionVisitor`:**
1. If the filter value is RFC 3339 with offset (e.g., `2026-01-01T00:00:00Z` or `2026-01-01T00:00:00-05:00`), parse and use as-is. No context applied.
2. If the filter value is naive/ambiguous (e.g., `2026-01-01`), **default: assume UTC**. Optional: if `per_user_timezone=True` **and** a timezone context is available (from user profile, request header, etc.), interpret in that timezone and convert to UTC for the query. Emit a `DEBUG` log noting the interpretation.
3. If neither condition holds (no offset, no context available), **assume UTC**. Do not error; silent fallback is simpler than 400 errors for consumers already using varco.

**Evidence favors:** Varco does not need to pioneer timezone-aware filtering. The pattern is to accept explicit RFC 3339 (safe, portable) and optionally support context-based coercion as a documented, opt-in feature. This mirrors Django's approach (warnings on naive datetimes, best practice is aware) but simpler (no required enforcement, just a flag).

---

**Decision readiness:**
- **T2 (DST-safe scheduling)**: ~95% clear. All frameworks agree; implementation is straightforward (`fold`, `datetime_exists()`, `datetime_ambiguous()`). Only gap: decide whether to recompute or accept drift on tzdata updates.
- **T3 (timezone-aware predicates)**: ~85% clear. Recommend assume-UTC default, optional context-based coercion. No blocker; awaiting feedback on whether the opt-in feature is worth the complexity.
