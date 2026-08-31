# Measurement — fail-open defaults across the ten packages

**Plan 022 / Phase 0, Step 4.** Design section §D-FAILOPEN.

## Method (and one correction to the method the plan prescribed)

The plan says: *"introspect every `pydantic_settings.BaseSettings` subclass in
the ten packages (`__mro__` walk, not `rg`, so subclasses of subclasses are
caught)"*.

That is what was done, in three passes:

1. `bash scripts/packages.sh` → the ten distribution packages.
2. `pkgutil.walk_packages()` over each, importing **every** submodule
   (0 import failures, so the enumeration is complete rather than
   best-effort).
3. Scan `sys.modules` for every class whose `__mro__` contains
   `pydantic_settings.BaseSettings`, then read `cls.model_fields` for each
   field's declared default. No `rg`, exactly as required — subclasses of
   subclasses (`KafkaEventBusSettings`, MRO depth 6) are caught by
   construction.

**Result: 25 `BaseSettings` subclasses.**

### ⚠️ The prescribed method cannot see the class the plan names as known

§D-FAILOPEN lists `TenancySettings` (`isolation`, `enforce_rls`,
`fanout_framework_tables`) as "known going in". A `BaseSettings.__mro__` walk
**does not find it**, measured:

```
TenancySettings.__mro__  == (TenancySettings, object)
MigrationSettings.__mro__ == (MigrationSettings, object)
BeanieSettings.__mro__    == (BeanieSettings, object)
```

They are frozen dataclasses with a `from_env()` classmethod, not
`BaseSettings`. varco has **two** settings idioms, not one, and the plan's
enumeration rule silently covers only the first. A second pass was therefore
run over all classes named `*Settings` / `*Config` / `*Preset` that are **not**
`BaseSettings` subclasses: **30 more classes**. Both populations are reported
below. Recording this rather than quietly widening the method, per U-8.

## Exclusions, by name

| Excluded | Why |
|---|---|
| `JwtVerificationSettings.allow_any_audience = False` | Plan Non-goals: already hardened (`VARCO_JWT_ALLOW_ANY_AUDIENCE`), not relitigated. It is fail-**closed** today anyway. |
| `JwtVerificationSettings.enforce_issuer = True` | Same Non-goal (`VARCO_JWT_ENFORCE_ISS`). Also already fail-closed. |

## Findings — defaults that disable a safety / isolation / durability / verification property

Ranked by severity. `AB-n` IDs are assigned in `../api-break-candidates.md`.

### F1 🔴 `CORSConfig.allow_origins = ("*",)` **with** `allow_credentials = True`

`varco_fastapi/varco_fastapi/middleware/cors.py:65` (`allow_origins`) and `:81`
(`allow_credentials`); `from_env()`'s fallbacks repeat them at `:109-112` and
`:126`. Reached **unconditionally** on every app:
`varco_fastapi/varco_fastapi/app.py:529-530` calls
`install_cors(app, CORSConfig.from_env())` with no opt-out, so an app that sets
no `VARCO_CORS_*` env var ships this policy.

**Measured behaviour (Starlette 1.0.0, the pinned version), not inferred** — a
`TestClient` against `install_cors(app, CORSConfig())`:

```
GET /x            Origin: https://evil.example
 -> access-control-allow-origin: https://evil.example
    access-control-allow-credentials: true

OPTIONS /x        Origin: https://evil.example
 -> access-control-allow-origin: https://evil.example
    access-control-allow-credentials: true
    access-control-allow-headers: ..., Authorization, ...
```

Any origin can make credentialed cross-origin requests, with `Authorization`
allowed on preflight.

**The two mitigations the code claims do not exist.** `CORSConfig`'s docstring
(`cors.py:49-51`, `:56-58`) states that `("*",)` + credentials is *"Incompatible
… browsers will block it"* and *"Starlette will raise at app startup"*. Neither
happens on Starlette 1.0.0: `CORSMiddleware.__init__` computes
`preflight_explicit_allow_origin = not allow_all_origins or allow_credentials`
and **reflects** the request `Origin` instead of raising. The docstring is
factually wrong and is itself part of the defect.

Category: `fail-open`. Prior: **change the default** to `()`
(`CORSConfig.restrictive()` already exists and is documented as "a safe default
in production"). Breaking, and therefore a freeze-window item.

### F2 🟠 `ErrorEnvelopeSettings.include_params = True`

`varco_core/varco_core/exception/settings.py:49`. CLAUDE.md's error-taxonomy
section explicitly warns: *"⚠️ `error_params()` … treat it as a **new
exfiltration surface**"*. The kill switch exists (`VARCO_ERROR_INCLUDE_PARAMS`)
but defaults to on, so the exfiltration surface is opt-**out**.

Category: `fail-open` (information disclosure). Prior: **leave** — Plan 011 D-4
made this an explicit, documented wire delta with a named kill switch, and
`ServiceAuthorizationError` already excludes `reason` from its params by
construction. Recorded so the checkpoint can overrule.

### F3 🟠 `TenancySettings.enforce_rls = False`

`varco_core/varco_core/tenancy/settings.py:111` (frozen dataclass — **not** found
by the prescribed `BaseSettings` walk; see the method correction above).
Row exists because §D-FAILOPEN names it as "the arguable one".

Category: `fail-open` (isolation). Prior: **leave** — CLAUDE.md documents
"Default is byte-identical to pre-Plan-007 behaviour" as a deliberate contract,
and RLS requires Postgres-specific DDL the framework cannot assume has been
applied. Flipping it would break every non-Postgres and every
already-provisioned deployment.

### F4 🟡 `TenancySettings.isolation = SHARED`, `fanout_framework_tables = False`

Same file, `:110` and `:117`. Category: `fail-open` by the letter of the rule (weakest isolation
strategy is the default). Prior: **leave**, and §D-FAILOPEN already argues it:
`SHARED` is a *deployment strategy* default, not a security failure, and
`SCHEMA`/`DATABASE` require infrastructure varco cannot provision implicitly.

### F5 🟡 `ConnectionSettings.ssl = None` (plaintext) — 6 classes

`varco_core/varco_core/connection/base.py:105` documents it: *"`ssl=None` means
no TLS is configured; the driver connects in plaintext."* Inherited by
`RedisConnectionSettings`, `KafkaConnectionSettings`, `NatsConnectionSettings`,
`PostgresConnectionSettings`, and `HttpConnectionSettings` — the last of which
defaults `port = 443` while defaulting `ssl = None`, an internally inconsistent
pair.

Category: `fail-open` (transport confidentiality). Prior: **leave** for the five
broker/DB classes (every host default is `localhost`, i.e. a dev default, and
TLS needs a CA path varco cannot invent); the `HttpConnectionSettings`
`port=443` + `ssl=None` mismatch is worth its own **docs** note, not a break.

### F6 🟡 `BeanieSettings.transactional = False` / `BeanieConfig.transactional = False`

`varco_beanie/varco_beanie/config.py:78`, `varco_beanie/varco_beanie/bootstrap.py`.
Category: `fail-open` (durability — the UoW does not wrap operations in a
transaction). Prior: **leave**, and the code already argues it at
`config.py:71`: `transactional=True` **raises at runtime** on a standalone
MongoDB node, so a `True` default would make the framework unusable against the
most common dev topology. Note this field is also one half of AB-4's duplicate
value object.

### F7 🟡 `CasbinSettings.adapter = "memory"`

`varco_casbin/varco_casbin/config.py:123`. Category: `fail-open` (durability —
policy changes are lost on restart). Prior: **leave** — CLAUDE.md already
states "the default `memory` adapter is non-durable", and the durable adapters
require an optional extra (`varco-casbin[sqlalchemy]` / `[beanie]`) plus a DSN
that has no default.

### F8 🟡 `RedisEventBusSettings.use_streams = False`

`varco_redis/varco_redis/config.py:103`. Pub/sub is fire-and-forget: a subscriber
that is down when a message is published never receives it. Streams
(`use_streams=True`) give consumer groups and replay.

Category: `fail-open` (durability). Prior: **leave** — flipping it changes the
wire representation and requires consumer-group management, i.e. it is a
different product, not a hardened default.

## Not findings (checked, and deliberately not filed as `fail-open`)

| Default | Why it is not a fail-open |
|---|---|
| `I18nSettings.enabled=False`, `TimezoneSettings.enabled=False`, `ProfilingSettings.enabled=False`, `MigrationSettings.mode="off"` | Off-by-default *features*, not disabled safety properties. Each is documented as a zero-overhead default; enabling them adds behaviour rather than restoring a guarantee. |
| `ErrorEnvelopeSettings.problem_details=False` | Media-type opt-in (RFC 9457). No safety property. |
| `ReliabilityPreset(outbox=False, audit=False, retry_policy=None, dlq=None)` | It is the *empty* preset — a builder, not a policy. The whole object exists to be constructed with values. |
| `JwtTransformSettings.strict=False` and the seven `*_required=False` | These govern a claim **transformer**, not verification. Signature/`aud`/`iss` checks are `JwtVerificationSettings`' job and are already fail-closed (and excluded by Non-goals). |
| `ClientConfig.verify=True`, `PeerConfig.verify=True`, `TenancySettings.global_writable=False`, `ProfilingSettings.attach_headers=False`, `CacheMetricsConfig.include_tenant=False` | Already fail-**closed**. Listed to show they were checked. |
| `KafkaEventBusSettings.enable_auto_commit=True` | Looks like a durability fail-open and is **not**: `varco_kafka/bus.py:286`/`:293` hard-override it to `False` for both delivery semantics, with a `# WHY` block at `:269-280`. `config.py:136` documents the field as "Retained for backward compatibility only". It is a **vestigial, misleading default**, not a fail-open — filed as an appendix note below rather than an AB row. |
| `NatsEventBusSettings.auto_create_stream=True` | Fail-*open* in the convenience sense only; disabling it would make first boot fail. No safety property. |
| `PostgresConnectionSettings.password=""` | A localhost dev default alongside `host="localhost"`, `username="postgres"`. An empty password is not *accepted* by anything; it simply fails to connect. |

## Explicit negative result, as the plan's Risks section requires

> "Step 4 must record 'none found beyond X' explicitly rather than quietly
> producing a short table."

**Not the case here.** The enumeration found **one finding materially more
severe than anything the plan anticipated** (F1, the default-permissive CORS
policy with credentials, reached unconditionally by `create_varco_app()` and
verified by execution), plus five further durability/confidentiality defaults
(F5–F8, F2) beyond the `TenancySettings` fields the plan knew about. F3/F4 are
the known `TenancySettings` rows and both keep their **leave** prior.

## Appendix — vestigial default, not a break candidate

`KafkaEventBusSettings.enable_auto_commit` is read by nobody: the bus
overrides it to `False` unconditionally. It remains a public, documented,
settable field that silently does nothing. Removing it is a (tiny) API break
and would be a legitimate AB row; it is **not** filed as one because it is not
a fail-open and §D-RANK's budget argument applies — recorded here so a later
plan does not have to re-derive it.
