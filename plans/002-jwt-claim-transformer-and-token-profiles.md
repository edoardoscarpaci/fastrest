# Plan 002 — JWT claim transformer + token profiles (env-driven)

## Goal
Make varco able to consume **foreign-shaped JWTs** (Keycloak, Cognito, Auth0, a bespoke
`sofy-roles` claim, …) with **environment-variable configuration only**, by inserting a
claim-transformation layer in front of `AuthContext` construction; and replace the single
`JwtUtil.SYSTEM_ISSUER` class variable with a registry of **named token profiles** so a
deployment can recognise many kinds of special/internal tokens (`system`, `internal`,
`partner`, `service-mesh`, …) and authorize on them at the route layer.

After this plan:

```bash
export VARCO_JWT_TRANSFORM_ROLES_FIELD="sofy-roles,realm_access.roles"
export VARCO_JWT_TRANSFORM_SCOPES_FIELD="scope"          # space-delimited OAuth2 scope
export VARCO_JWT_TRANSFORM_TENANT_FIELD="org.id"
export VARCO_JWT_TRANSFORM_ROLES_STRIP_PREFIX="ROLE_"
export VARCO_JWT_PROFILE__INTERNAL__ISS="mesh-signer"
export VARCO_JWT_PROFILE__INTERNAL__TOKEN_TYPE="system"
export VARCO_JWT_PROFILE__INTERNAL__ROLES="internal"
```
…and **no application code changes**: `JwtParser.parse()`, `TrustedIssuerRegistry.verify()`,
`JwtBearerAuth`, and `PassthroughAuth` all produce a correct `AuthContext`
(`roles`, `scopes`, `grants`, `metadata["tenant_id"]`, `metadata["token_profile"]`), and
`@route(..., requires=require_token_profile("internal"))` works.

## Non-goals
- ❌ Refresh-token grant exchange / rotation / reuse detection (deferred, see §C).
- ❌ Token introspection, revocation lists, `jti` denylists (deferred, see §C).
- ❌ Automatic key-rotation scheduling (`MultiKeyAuthority.rotate()` stays manual).
- ❌ **Outbound** claim renaming — `JsonWebToken.to_claims()` keeps emitting canonical
  names (decision D-4). An inverse mapping helper is provided but is not wired in.
- ❌ Remapping `exp` / `iat` / `nbf` / `aud` / `jti` / `iss` (decision D-2).
- ❌ New fields on `AuthContext` or `JsonWebToken` (decision D-5) — everything extra lands
  in `AuthContext.metadata`.
- ❌ No change to `AbstractAuthorizer`, the policy engine, or service-layer authorization.
- ❌ No new package. All core work lands in `varco_core.jwt`; `varco_fastapi` gets only the
  guard + two small integration points.

---

## Verified starting state (anchors re-read, scout uncertainties resolved)

Confirmed:
- `varco_core/varco_core/jwt/model.py:28-42` — `_RESERVED_CLAIM_KEYS` hardcodes
  `roles`/`scopes`/`grants`; `model.py:236-252` — `to_claims()` emits those exact names.
- `varco_core/varco_core/jwt/parser.py:247-249` — `raw.get("roles", [])`,
  `raw.get("scopes", [])`, `raw.get("grants", [])`; `parser.py:266-271` — `AuthContext(...)`.
- `parser.py:169-218` — `_from_raw_claims()` is the **single funnel**: called by `parse()`
  (`:121`), `parse_unverified()` (`:166`) **and** `TrustedIssuerRegistry.verify()`
  (`registry.py:516`). ⇒ One insertion point covers SEAM 1 **and** SEAM 2.
- `parser.py:258-264` — grants reconstruction does `g["resource"]` / `g["actions"]`
  → raises a bare `KeyError` on a malformed grants claim (poor error message; fixed here).
- `util.py:29,64,122` — `SYSTEM_ISSUER` module constant + `ClassVar` + `is_system()`
  comparing `token.iss == self.SYSTEM_ISSUER`.
- `varco_core/tests/test_jwt.py:440-447` — `monkeypatch.setattr(JwtUtil, "SYSTEM_ISSUER", …)`
  and asserts the *default* `varco/system` is then **not** system. This test must keep
  passing unchanged ⇒ the default `system` profile must derive its issuer from the
  live `ClassVar`, not snapshot it (see §B).
- `varco_fastapi/.../auth/server_auth.py:179-195` — `registry.verify(raw_token)` with
  **no** `audience=`, then `return jwt.auth_ctx` / `AuthContext(user_id=jwt.sub)`.
- `server_auth.py:343-374` — `PassthroughAuth` **duplicates** claim→`AuthContext` parsing
  with its own hardcoded `"roles"`/`"scopes"`/`"grants"` and its own reserved-key set.
  This is the one place that would NOT get the transformer for free; it is refactored.
- `varco_core/varco_core/service/tenant.py:72,420,439` — `TenantAwareService` reads
  `ctx.metadata["tenant_id"]` and raises `ServiceAuthorizationError` when absent.
  **Nothing in varco populates it from a JWT today** ⇒ the `tenant` mapping target closes
  a real, currently-broken path.
- Env-config precedents: `VarcoSettings` + `SettingsConfigDict(env_prefix=...)`
  (`config.py:64-141`) for flat settings; plain `os.environ` label-grouping for dynamic
  multi-entry config (`authority/config.py:127-184`, `FASTREST_AUTHORIZATION__<LABEL>__X`).
- `@Provider` (never `@Singleton`) for pydantic `BaseSettings` — `varco_casbin/di.py:51-64`;
  `VarcoFastAPIModule` (`varco_fastapi/di.py:177-278`) is where framework defaults live.

Scout uncertainties resolved:
1. **JWKS refresh TTL is NOT configurable.** `registry.py:176-177` sets
   `_last_refresh=0.0` / `_min_refresh_interval=10.0` inside `__init__`, both in
   `__slots__`, with no ctor arg and no env var. Refresh happens **only** on a
   kid-miss (`get_key`, `registry.py:387-415`); there is no proactive/background refresh.
2. **Audience validation is NOT enforced at the FastAPI layer.** `JwtBearerAuth.__call__`
   calls `self._registry.verify(raw_token)` (`server_auth.py:180`) with no `audience=`;
   `verify()` only adds `audience` to `decode_kwargs` when non-`None` (`registry.py:508`).
   ⇒ `aud` is never checked in the default varco HTTP path.
3. **Per-issuer mapping keying → key by the `iss` claim value** (recommendation, see D-6),
   with env vars authored by *label* and an automatic fallback to
   `FASTREST_AUTHORIZATION__<LABEL>__ISS` so the two config blocks share one label
   namespace.
4. **`TrustedIssuerRegistry.from_env()` DOES exist** — `registry.py:591-612`, delegating to
   `AuthorizationConfig.from_env().to_registry()`. Scout simply missed it; no work needed.

---

## Design

### Pipeline (one insertion point, two seams covered)

```
raw JWT string
   │ PyJWT decode (signature, exp/nbf, optional aud/leeway)   ← unchanged
   ▼
raw claims dict  ──────────────────────────────────────────────────────────┐
   │                                                                        │
   │  JwtParser._from_raw_claims(raw)          ← THE single funnel          │
   │      1. transformer = explicit arg                                     │
   │                    or resolve_claim_transformer(raw.get("iss"))         │
   │      2. canonical = transformer.transform(raw)   # non-destructive      │
   │      3. sub/iss/aud/exp/iat/nbf/jti  ← read from canonical (RFC claims) │
   │      4. auth_ctx = _build_auth_ctx(canonical)                           │
   │      5. profile   = resolve_token_profile(token) → augment auth_ctx      │
   ▼                                                                        │
JsonWebToken(auth_ctx=…, extra_claims=<originals, incl. "sofy-roles">) ─────┘
   ▲                              ▲                            ▲
   │ JwtParser.parse()            │ registry.verify()          │ parse_unverified()
   │ (library / non-HTTP users)   │ (JwtBearerAuth → SEAM 2)   │ (PassthroughAuth)
```

Because `TrustedIssuerRegistry.verify()` already delegates to
`JwtParser._from_raw_claims` (`registry.py:516`), **`varco_fastapi` needs zero duplicate
transformation logic** — the only `varco_fastapi` change on this axis is refactoring
`PassthroughAuth` (which hand-rolls its own parse) onto `JwtParser.parse_unverified()`.

### Extension point: **both** a Protocol and a config-driven default

```python
@runtime_checkable
class ClaimTransformer(Protocol):
    def transform(self, claims: Mapping[str, Any]) -> Mapping[str, Any]: ...
```

| Implementation | Purpose |
|---|---|
| `IdentityClaimTransformer` (`IDENTITY` singleton) | returns `claims` **unchanged, no copy** — the zero-config hot path, byte-for-byte today's behaviour |
| `MappingClaimTransformer(ClaimMapping)` | the config-driven default; what env vars build |
| user classes | anything (e.g. call an internal user-directory, decrypt a claim) |

`ClaimTransformer` is a `Protocol` (structural) rather than an ABC so a user's existing
adapter class satisfies it without inheriting from varco.

### Value objects (all `@dataclass(frozen=True)`)

```python
class CanonicalClaim(StrEnum):        # the canonical target field set
    USER_ID    = "user_id"            # → AuthContext.user_id   (default source: "sub")
    ROLES      = "roles"              # → AuthContext.roles
    SCOPES     = "scopes"             # → AuthContext.scopes
    GRANTS     = "grants"             # → AuthContext.grants
    TENANT_ID  = "tenant_id"          # → AuthContext.metadata["tenant_id"]
    ACTOR      = "actor"              # → AuthContext.metadata["actor"]  (RFC 8693 `act`)
    TOKEN_TYPE = "token_type"         # → JsonWebToken.token_type

class ValueShape(StrEnum):
    AUTO = "auto"; LIST = "list"; SPACE = "space"; CSV = "csv"
    SCALAR = "scalar"; DICT_KEYS = "dict_keys"; GRANTS = "grants"; RAW = "raw"

@dataclass(frozen=True)
class ClaimPath:                      # "realm_access.roles" → ("realm_access", "roles")
    segments: tuple[str, ...]
    @classmethod
    def parse(cls, spec: str, *, separator: str = ".") -> ClaimPath: ...
    def read(self, claims: Mapping[str, Any]) -> Any | _MISSING: ...

@dataclass(frozen=True)
class ClaimRule:
    target: CanonicalClaim
    sources: tuple[ClaimPath, ...]    # fallback chain, in order
    shape: ValueShape = ValueShape.AUTO
    strip_prefix: str | None = None
    merge: bool = False               # False = first non-empty wins; True = union all
    required: bool = False            # missing → ClaimTransformError

@dataclass(frozen=True)
class ClaimMapping:
    rules: tuple[ClaimRule, ...] = ()
    metadata_fields: tuple[tuple[str, ClaimPath], ...] = ()   # extra → metadata[key]
    separator: str = "."
    strict: bool = False              # shape/type errors raise vs. log-and-skip
    def apply(self, claims: Mapping[str, Any]) -> dict[str, Any]: ...
    def merged_with(self, override: ClaimMapping) -> ClaimMapping: ...   # per-issuer inherit
    def invert(self) -> dict[str, str]: ...  # raises for non-invertible rules
```

**Non-destructive transform**: `apply()` returns `dict(claims)` with canonical keys
added/overwritten. Originals are kept, so `extra_claims` still shows `sofy-roles`
(useful for audit/debug) and a user subclass overriding `_build_auth_ctx` that reads the
foreign name keeps working.

**Canonical fallback is always appended last**: `roles_field="sofy-roles"` produces
sources `("sofy-roles", "roles")`. So a token that already uses canonical names still
parses with foreign-issuer config present. (D-7)

### Value-shape normalization (`ValueShape.AUTO`)

| Input | Result |
|---|---|
| `["a","b"]` | `["a","b"]` |
| `"read write"` (contains space) | `["read","write"]` — the OAuth2 `scope` claim |
| `"a,b"` (comma, no space) | `["a","b"]` |
| `"admin"` | `["admin"]` |
| `{"x":…, "y":…}` | `["x","y"]` (sorted keys) |
| `None` / missing | `[]` (or `ClaimTransformError` if `required=True`) |
| `int` / `bool` / nested list | `strict=False` → `[str(v)]` + `logger.warning`; `strict=True` → `ClaimTransformError` |

`strip_prefix="ROLE_"` is applied per element after shaping (Keycloak/Spring style).
`ValueShape.GRANTS` validates `list[{"resource": str, "actions": list[str]}]` and raises
`ClaimTransformError` with the offending index — replacing today's bare `KeyError`
(`parser.py:259-263`).

### Env var scheme (final names)

The user wrote `JWT_TRANSFORM_ROLES_FIELD`. The repo convention is a `VARCO_` prefix on a
`VarcoSettings` subclass (`config.py:12-19`), so the **final names add the `VARCO_` prefix
and nothing else** — `env_prefix="VARCO_JWT_TRANSFORM_"`:

| Env var | Type | Default | Meaning |
|---|---|---|---|
| `VARCO_JWT_TRANSFORM_ROLES_FIELD` | `str` | `None` | comma-separated fallback chain of claim paths |
| `VARCO_JWT_TRANSFORM_SCOPES_FIELD` | `str` | `None` | idem |
| `VARCO_JWT_TRANSFORM_GRANTS_FIELD` | `str` | `None` | idem |
| `VARCO_JWT_TRANSFORM_USER_ID_FIELD` | `str` | `None` | idem (does **not** change `token.sub`) |
| `VARCO_JWT_TRANSFORM_TENANT_FIELD` | `str` | `None` | → `metadata["tenant_id"]` |
| `VARCO_JWT_TRANSFORM_ACTOR_FIELD` | `str` | `None` | → `metadata["actor"]`; defaults to `act.sub,act` |
| `VARCO_JWT_TRANSFORM_TOKEN_TYPE_FIELD` | `str` | `None` | e.g. `typ` (Keycloak), `token_use` (Cognito) |
| `VARCO_JWT_TRANSFORM_METADATA_FIELDS` | `str` | `None` | `key=path,key2=path2` → `metadata[key]` |
| `VARCO_JWT_TRANSFORM_<T>_SHAPE` | `str` | `auto` | `<T>` ∈ `ROLES,SCOPES,GRANTS,USER_ID,TENANT,ACTOR,TOKEN_TYPE` |
| `VARCO_JWT_TRANSFORM_<T>_STRIP_PREFIX` | `str` | `None` | per-target element prefix strip |
| `VARCO_JWT_TRANSFORM_<T>_REQUIRED` | `bool` | `false` | missing → `ClaimTransformError` |
| `VARCO_JWT_TRANSFORM_MERGE_SOURCES` | `bool` | `false` | `false` = first-non-empty wins; `true` = union the whole chain |
| `VARCO_JWT_TRANSFORM_PATH_SEPARATOR` | `str` | `.` | escape a literal separator with `\.` |
| `VARCO_JWT_TRANSFORM_STRICT` | `bool` | `false` | shape/type violations raise instead of warn-and-skip |
| `VARCO_JWT_LEEWAY_SECONDS` | `float` | `0.0` | clock-skew leeway (§C-1) |
| `VARCO_JWT_AUDIENCE` | `str` | `None` | this service's expected `aud` (§C-2) |

⚠️ **Field lists are declared `str`, not `list[str]`, on purpose.** pydantic-settings parses
a `list[str]` field from a single env var as **JSON**, which would force
`ROLES_FIELD='["sofy-roles","roles"]'`. Declaring `str` and splitting on `,` in a
`@field_validator`/accessor keeps the human-friendly `"sofy-roles,realm_access.roles"` form.

⚠️ `JwtTransformSettings.model_config` **must** set `extra="ignore"` — the per-issuer vars
(`VARCO_JWT_TRANSFORM__GOOGLE__ROLES_FIELD`) share the same prefix and would otherwise be
interpreted as unknown fields.

**Per-issuer overrides** use the double-underscore label grouping already established by
`FASTREST_AUTHORIZATION__<LABEL>__X` (`authority/config.py:62-67`), parsed with plain
`os.environ` (pydantic cannot express dynamic label groups):

```
VARCO_JWT_TRANSFORM__KEYCLOAK__ISS         = https://kc.example.com/realms/sofy
VARCO_JWT_TRANSFORM__KEYCLOAK__ROLES_FIELD = realm_access.roles
VARCO_JWT_TRANSFORM__KEYCLOAK__SCOPES_FIELD= scope
```

`__ISS` is optional: when absent, the loader reads
`FASTREST_AUTHORIZATION__<LABEL>__ISS`, and failing that normalises the label
(`KEYCLOAK` → `keycloak`), exactly as `AuthorizationConfig` does (`config.py:177-180`).

**Precedence (D-6, D-8)**:
```
per-issuer mapping for token.iss   (field-by-field override, inherits the global)
        ↓ falls back to
global  VARCO_JWT_TRANSFORM_*      mapping
        ↓ falls back to
IDENTITY (canonical names only)    ← zero-config = today's behaviour
```

### Runtime resolution without DI

`JwtParser` is all-classmethods and is used outside FastAPI, so the resolved transformer
comes from a **process-global, read-mostly registry** in `varco_core.jwt.transform.runtime`:

```python
def resolve_claim_transformer(iss: str | None) -> ClaimTransformer: ...
def configure_claim_transforms(registry: ClaimTransformerRegistry | None = None) -> None: ...
def reset_claim_transforms() -> None: ...       # test hook
```

`resolve_claim_transformer` lazily builds the registry from `os.environ` on first call and
caches it. `configure_claim_transforms(reg)` replaces it (startup / tests).

```
DESIGN: process-global registry instead of an injected instance
  ✅ JwtParser stays stateless classmethods — no breaking API change, no parser instances
     threaded through JwtBuilder/registry/verify call sites.
  ✅ Works with zero DI (library users, CLI tools, varco_core-only apps) via from_env().
  ✅ Zero-config cost is one dict lookup + IDENTITY (which returns the input dict as-is,
     no copy) — the hot path is unchanged.
  ❌ Mutable module state; two different mappings cannot coexist in one process.
     Mitigated by the explicit `transformer=` parameter on parse()/_from_raw_claims()
     which always wins, and by reset_claim_transforms() in tests (autouse fixture).
  ❌ Configuration is read at first-use, not at import — a late env mutation is picked up
     only until the first parse. Documented; call configure_claim_transforms() to force.
  Alternative considered: make JwtParser instantiable (`JwtParser(transformer=…)`).
  ✅ pure, no globals. ❌ breaks every existing `JwtParser.parse(...)` call site plus
     `registry.py:516`, and forces every non-DI caller to thread an instance. Rejected.
  No asyncio.Lock is used: the registry is set once at startup and only read afterwards;
  dict/attribute assignment is atomic under the GIL and the lazy init is idempotent.
```

DI stays **optional and pure**: `VarcoFastAPIModule` gains `@Provider`s that *return*
`JwtTransformSettings` / `ClaimTransformerRegistry` / `TokenProfileRegistry` (pydantic
settings via `@Provider`, **never** `@Singleton`), and `create_varco_app()` calls
`configure_jwt_from_env()` once at startup so the global matches the injected objects.

### Alternatives considered

- **Transform inside `JwtBearerAuth` (SEAM 2 only)** — ✅ no `varco_core` change, HTTP-local.
  ❌ `JwtParser.parse()`, `registry.verify()`, `PassthroughAuth`, and every non-HTTP
  consumer stay broken; duplicates parsing logic in a second place. **Rejected.**
- **Subclass `JwtParser` and override `_build_auth_ctx`** (already possible today) —
  ✅ zero new code. ❌ not env-driven (the explicit user ask), and `registry.verify()`
  hardcodes `JwtParser._from_raw_claims` so a subclass is never used there. **Rejected**
  as the primary mechanism; kept working as an escape hatch.
- **Rename claims in `AuthContext` itself (add aliases to the dataclass)** — ✅ single place.
  ❌ pollutes the domain value object with transport concerns; `AuthContext` is also built
  from sessions/API keys. **Rejected.**
- **Pydantic model with `AliasChoices` for the whole claim set** — ✅ free fallback chains.
  ❌ aliases must be known at class-definition time, so they cannot come from env, and
  per-issuer variation is impossible. **Rejected.**
- **`ValueShape` inference only (no explicit shapes)** — ✅ less config. ❌ `"a,b"` is
  genuinely ambiguous (one role containing a comma vs. two roles); explicit override is
  required for correctness. **Rejected** (AUTO kept as the default, override available).
- **Keep `SYSTEM_ISSUER` and add `SYSTEM_ISSUERS: set[str]`** — ✅ tiny diff. ❌ still one
  undifferentiated bucket: cannot express "internal tokens must also have
  `token_type=system` and `aud=orders`", cannot name them, cannot grant implied roles.
  **Rejected** in favour of `TokenProfile`.

---

## §A — Env-driven claim mapping (decisions summary)

- **Canonical target set** = `user_id, roles, scopes, grants, tenant_id, actor, token_type`.
  Justification: these are exactly the fields that flow into `AuthContext`
  (+ `metadata["tenant_id"]` which `TenantAwareService` requires, and `metadata["actor"]`
  for audit) plus `token_type`, which real issuers genuinely name differently
  (`typ`, `token_use`). **`iss`/`aud`/`exp`/`iat`/`nbf`/`jti` are deliberately NOT
  remappable** (D-2): they are verified cryptographically/temporally by PyJWT *before*
  this layer runs, and `iss` is the mapping **selector** — remapping it would be circular.
  `sub` is not remapped either; `token.sub` stays the raw RFC claim while
  `user_id` may be sourced elsewhere (D-3).
- **Nested paths**: dotted (`realm_access.roles`), separator configurable, `\.` escapes a
  literal dot. Missing intermediate segment = missing value (not an error, unless
  `required=true`). Reading through a list index is **not** supported (D-9).
- **Fallback chains**: comma-separated; **first non-empty wins** by default,
  `MERGE_SOURCES=true` unions the whole chain (D-10).
- **Round-trip**: `to_claims()` keeps canonical names (D-4). `ClaimMapping.invert()` exists
  for explicit interop and raises `ValueError` (with the offending rule named) for
  non-invertible rules (multi-source / nested / shaped / prefix-stripped).
- **Where it runs**: `JwtParser._from_raw_claims` — the single funnel covering SEAM 1 and
  SEAM 2 (`registry.verify` already routes through it).
- **Extension point**: both — a `ClaimTransformer` Protocol *and* the env-driven
  `MappingClaimTransformer` default.
- **Per-issuer**: keyed by the `iss` claim value, authored by label, inheriting the global
  mapping field-by-field.
- **DI**: optional. `@Provider`s in `VarcoFastAPIModule`; zero-DI users get the same
  behaviour via the lazy `from_env()` path.

---

## §B — Token profiles (replacing the single `SYSTEM_ISSUER`)

```python
@dataclass(frozen=True)
class TokenProfile:
    name: str
    issuers: frozenset[str] = frozenset()          # any-of; empty = any issuer
    token_type: str | None = None                  # exact match when set
    audiences: frozenset[str] = frozenset()        # any-of against token.aud
    required_claims: frozenset[str] = frozenset()  # canonical or raw claim names
    implied_roles: frozenset[str] = frozenset()    # merged into AuthContext.roles
    implied_scopes: frozenset[str] = frozenset()
    def matches(self, token: JsonWebToken) -> bool: ...
    def explain(self, token: JsonWebToken) -> str | None:  # first failing condition
```

```python
class TokenProfileRegistry:
    def register(self, profile: TokenProfile) -> None
    def get(self, name: str) -> TokenProfile            # raises TokenProfileError
    def names(self) -> tuple[str, ...]
    def resolve(self, token: JsonWebToken) -> TokenProfile | None   # first match, reg. order
    def matches(self, name: str, token: JsonWebToken) -> bool
    @classmethod
    def from_env(cls) -> TokenProfileRegistry
```

Env scheme (same label grouping): `VARCO_JWT_PROFILE__<NAME>__{ISS,TOKEN_TYPE,AUD,
REQUIRED_CLAIMS,ROLES,SCOPES}`, all comma-separated lists; `<NAME>` is lowercased to form
the profile name (`INTERNAL` → `"internal"`).

**Integration**
- `_from_raw_claims` resolves the profile after building the token, stores the name in
  `AuthContext.metadata[PROFILE_METADATA_KEY]` (`"token_profile"`), and merges
  `implied_roles` / `implied_scopes` into the context. If the matched profile declares
  implied roles/scopes but the token has **no** auth claims, an `AuthContext(user_id=sub)`
  is **materialised** — that is precisely the "system token with elevated trust" case.
  Otherwise `auth_ctx` stays `None` exactly as today.
- `JwtUtil` gains `matches_profile(name)`, `profile_name()`, `assert_profile(name)`.
- `JwtBuilder` gains `as_profile(profile)` → sets `iss` (first issuer), `token_type`,
  `aud`; it does **not** inject implied roles (those are derived at parse time).
- `RouteGuard` gains a frozen field `token_profiles: tuple[str, ...]` checked between the
  role check and the grant check, plus `require_token_profile(*names)` in
  `varco_fastapi/auth/guard.py`. It reads
  `ctx.metadata.get("token_profile")` — a field (not a closure predicate) so the guard
  stays hashable, comparable and introspectable.

**Back-compat / deprecation policy for `SYSTEM_ISSUER`** (⚠️ no breaking change):
- The module constant `SYSTEM_ISSUER` and the `JwtUtil.SYSTEM_ISSUER` `ClassVar` **stay**
  and keep working.
- `is_system()` becomes: *if* a profile named `"system"` is registered (env or code) →
  `registry.matches("system", token)`; *else* → today's `token.iss == self.SYSTEM_ISSUER`.
  This is what keeps `test_jwt.py:440-447` (the monkeypatch test) green **unchanged** —
  the fallback reads the live `ClassVar` on every call and never snapshots it.
- Deprecation is **documentation-only in this release**: docstring `.. deprecated::` notes
  + a README/CLAUDE.md pointer to `VARCO_JWT_PROFILE__SYSTEM__ISS`. **No** runtime
  `DeprecationWarning` (a `ClassVar` read cannot be intercepted without a metaclass
  descriptor, and the churn is not worth it). Removal is explicitly **not** scheduled (D-11).

---

## §C — Triage of the remaining JWT gaps

| # | Gap | Verdict | Justification (value/effort) |
|---|---|---|---|
| C-1 | **Leeway / clock skew** | ✅ **IN** (Phase 4) | 15-line change, removes a classic cross-host 401 flake. `VARCO_JWT_LEEWAY_SECONDS` (default `0.0` = today's behaviour), `parse(leeway=)`, `verify(leeway=)`, `JwtUtil.is_expired(leeway=)`. Recommend `30`. |
| C-2 | **Enforced audience validation** | ✅ **IN** (Phase 4) | Currently *never* checked in the HTTP path (confirmed) — a token minted for service B is accepted by service A. `JwtBearerAuth(audience=…)` + `VARCO_JWT_AUDIENCE`, threaded into `registry.verify(audience=…)`. Default stays `None` (opt-in) to avoid breaking live deployments; log **one** startup warning when unset. |
| C-3 | **Nested claim access** | ✅ **IN** (Phase 0/1) | It *is* the feature — `realm_access.roles` is the single most common real-world shape. Public helper `read_claim(claims, "a.b")` exported for app code. |
| C-4 | **JWKS TTL / refresh** | ✅ **IN, minimal** (Phase 5) | Make `_min_refresh_interval` a ctor arg + `VARCO_JWKS_MIN_REFRESH_SECONDS`, and add an **age-based proactive reload** inside `get_key()` (`VARCO_JWKS_TTL_SECONDS`, default `0` = disabled). Cheap, no task lifecycle. A real **background refresher task is DEFERRED** — it needs start/stop wiring in the app lifespan and belongs with the health/lifecycle layer. |
| C-5 | **Actor / impersonation (`act`)** | ✅ **IN, read-only** (Phase 1) | One extra canonical target (`actor` → `metadata["actor"]`, default sources `act.sub,act`). Needed for audit trails today. **Delegation semantics** (should the actor's permissions intersect the subject's?) are a *policy* decision → belongs in `varco_core.auth.policy` / Casbin, **not** here. |
| C-6 | **Grants-claim error messages** | ✅ **IN** (Phase 1) | Today a malformed `grants` claim raises a bare `KeyError` from `parser.py:259`. `ValueShape.GRANTS` validation gives an actionable message naming the index and the missing key. |
| C-7 | **`PassthroughAuth` duplicate parsing** | ✅ **IN** (Phase 4) | `server_auth.py:343-374` re-implements claim→`AuthContext` with hardcoded names; without this refactor, gateway-fronted deployments silently *don't* get the transformer. |
| C-8 | **Refresh-token flow** | ❌ **DEFERRED** — agreed with the user | Needs a persisted token store, rotation + reuse detection, and HTTP endpoints. That is a feature in its own right (a `varco_core.jwt.refresh` module + a `varco_redis` store), not a claim-mapping concern. |
| C-9 | **Introspection / revocation** | ❌ **DEFERRED** — agreed with the user | Requires distributed state (a `jti` denylist). Future shape recorded in the feature doc: a `TokenRevocationCheck` Protocol consulted by `JwtBearerAuth`, with an in-memory impl in `varco_core` and a Redis impl in `varco_redis`. Not started here. |
| C-10 | **Key-rotation automation** | ❌ **DEFERRED** | `MultiKeyAuthority.rotate()`/`retire()` already work; scheduling is an ops/scheduler concern and would need a durable "retire after all tokens expired" clock. |
| C-11 | **`iss` enforcement in `verify()`** | ❌ **DEFERRED**, but documented as a **risk** | `registry.verify()` deliberately does not enforce `iss` (`registry.py:20-24`, `:462`). Changing that default is a security-behaviour change deserving its own plan. Mitigation here: `TokenProfile.issuers` gives per-profile `iss` enforcement, and the risk is called out in §Risks. |

---

## Steps

TDD-ordered. Each phase is independently reviewable, mergeable, and ships its own tests
**and** docs. `varco_core/tests/conftest.py` gains one autouse fixture in Phase 1
(`reset_claim_transforms()` + `reset_token_profiles()`) so no test leaks global config.

### Phase 0 — path + shape primitives (pure, no behaviour change)

1. [ ] `varco_core/tests/test_jwt_claim_path.py` (new) — **failing first**:
   - `ClaimPath.parse("roles")` → `("roles",)`; `.parse("realm_access.roles")` →
     `("realm_access","roles")`; `.parse("a\\.b")` → `("a.b",)` (escaped literal dot);
     `.parse("a.b\\.c.d")` → `("a","b.c","d")`.
   - `.parse("")` and `.parse("a..b")` → `ValueError` naming the offending spec.
   - Custom separator `:` via `separator=":"`.
   - `read()`: hit; missing top key → `MISSING`; missing nested key → `MISSING`;
     intermediate value not a mapping (`{"a": 5}`, path `a.b`) → `MISSING` (not `TypeError`).
   - `read_claim(claims, "a.b", default=…)` public helper.
2. [x] `varco_core/varco_core/jwt/transform/path.py` (new) — `ClaimPath`, `MISSING`
   sentinel, `read_claim()`. `from __future__ import annotations`, frozen dataclass,
   full docstrings with Args/Returns/Raises/Edge cases, `DESIGN:` block for
   dotted-path-vs-JSONPath (✅ no dependency, covers 99% of real claims / ❌ no array
   indexing or filters — D-9).
3. [ ] `varco_core/tests/test_jwt_transform.py` (new) — shape tests (failing first):
   every row of the AUTO table above; explicit `SPACE`/`CSV`/`SCALAR`/`DICT_KEYS`/`RAW`;
   `strip_prefix` applied per element; `strict=True` raises `ClaimTransformError` on an
   `int` input while `strict=False` warns and coerces; `GRANTS` shape accepts a valid
   grants list and raises a message containing the bad index for
   `[{"resource":"posts"}]` (missing `actions`).
4. [x] `varco_core/varco_core/jwt/transform/shape.py` (new) — `ValueShape` StrEnum +
   `normalize(value, shape, *, strip_prefix, strict, target)` returning
   `list[str] | str | list[dict] | None`.
5. [x] `varco_core/varco_core/jwt/exceptions.py` (new) — `JwtException(Exception)`,
   `ClaimTransformError(JwtException, ValueError)`, `TokenProfileError(JwtException)`.
   Messages must name the target claim, the source path and the offending value type
   (specific + actionable, per coding-practice).

### Phase 1 — `ClaimMapping` + transformer + parser wiring (still identity by default)

6. [ ] `varco_core/tests/test_jwt_transform.py` — extend (failing first):
   - `ClaimMapping` built in code maps `sofy-roles` → canonical `roles`;
     originals preserved in the output dict.
   - Fallback chain: `("sofy-roles","realm_access.roles","roles")` → first-non-empty wins;
     `merge=True` unions all three.
   - `required=True` + missing → `ClaimTransformError` naming the target and all tried paths.
   - `metadata_fields` promote `org.id` → output `tenant_id`.
   - `IDENTITY.transform(d) is d` (no copy — hot-path guarantee).
   - `merged_with()`: override declares only `roles` → other rules inherited.
   - `invert()` round-trips a simple single-source rule; raises `ValueError` naming the
     rule for a nested/shaped/multi-source rule.
   - **Parser integration**: `JwtParser.parse(signed, secret, transformer=mapping_tf)`
     produces `auth_ctx.roles == {"editor"}` from a `sofy-roles` claim;
     `token.extra_claims["sofy-roles"]` still present; `token.sub` unchanged.
   - **Regression**: `JwtParser.parse()` with no transformer configured is byte-identical
     to today for a canonical token (assert full `JsonWebToken` equality against a
     pre-transform-era expectation).
   - `tenant_id` canonical → `auth_ctx.metadata["tenant_id"]`;
     `actor` canonical → `auth_ctx.metadata["actor"]`.
   - Malformed `grants` → `ClaimTransformError` (not `KeyError`).
7. [x] `varco_core/varco_core/jwt/transform/mapping.py` (new) — `CanonicalClaim`,
   `ClaimRule`, `ClaimMapping` (+`apply`, `merged_with`, `invert`).
8. [x] `varco_core/varco_core/jwt/transform/protocol.py` (new) — `ClaimTransformer`
   `runtime_checkable` Protocol, `IdentityClaimTransformer`, `IDENTITY` singleton.
9. [x] `varco_core/varco_core/jwt/transform/mapper.py` (new) —
   `MappingClaimTransformer(mapping)` implementing `ClaimTransformer`.
10. [x] `varco_core/varco_core/jwt/transform/runtime.py` (new) —
    `resolve_claim_transformer(iss)`, `configure_claim_transforms(registry=None)`,
    `reset_claim_transforms()`. Includes the process-global `DESIGN:` block from above.
    In Phase 1 the lazy builder returns an empty registry (→ `IDENTITY`); Phase 2 swaps in
    `JwtTransformConfig.from_env()`.
11. [x] `varco_core/varco_core/jwt/parser.py` — wire it in:
    - `_from_raw_claims(cls, raw, *, transformer: ClaimTransformer | None = None)`:
      resolve → `canonical = transformer.transform(raw)`; read all RFC claims and
      `token_type` from `canonical`; pass `canonical` to `_build_auth_ctx`; build
      `extra_claims` from **`raw`** (originals) minus `_RESERVED_CLAIM_KEYS`.
    - `parse(..., transformer=None, leeway=None)` and `parse_unverified(token,
      transformer=None)` forward the argument.
    - `_build_auth_ctx` reads canonical `roles`/`scopes`/`grants` **plus** `user_id`
      (falling back to `sub`), `tenant_id`, `actor` → `metadata`. Materialise an
      `AuthContext` when any of roles/scopes/grants/tenant_id/actor is present
      (⚠️ widened trigger — see Edge cases).
    - Grants construction validated through `ValueShape.GRANTS`.
    - Update the module `DESIGN:` block and every docstring's Edge cases.
12. [x] `varco_core/varco_core/jwt/model.py` — extend `_RESERVED_CLAIM_KEYS` with
    `"tenant_id"`, `"actor"`, `"user_id"`, `"act"` **only if** those names are actually
    emitted by `to_claims()`; ⚠️ adding a key here also blocks `JwtBuilder.claim(key)`
    (`builder.py:279-286`). Decision: add `"act"` and `"tenant_id"` to the reserved set
    **and** emit them from `to_claims()` when present in `auth_ctx.metadata`
    (`tenant_id`, `act`) so varco-minted tokens round-trip tenant + actor; do **not**
    reserve `user_id`/`actor` (canonical-only, never emitted). Add a test for the
    round-trip and one for the `JwtBuilder.claim("tenant_id")` `ValueError`.
13. [ ] `varco_core/tests/conftest.py` — autouse fixture calling `reset_claim_transforms()`
    (and, from Phase 3, `reset_token_profiles()`) before/after each test.
14. [x] `varco_core/varco_core/jwt/__init__.py` + `varco_core/varco_core/__init__.py` —
    export `ClaimTransformer`, `ClaimMapping`, `ClaimRule`, `ClaimPath`, `CanonicalClaim`,
    `ValueShape`, `MappingClaimTransformer`, `IdentityClaimTransformer`, `read_claim`,
    `ClaimTransformError`, `configure_claim_transforms`, `resolve_claim_transformer`;
    update both `__all__` blocks and the `jwt/__init__.py` sub-module layout comment.
15. [x] Docs (Phase 1): `varco_core/README.md` — new "JWT claim transformation" section
    (code-configured `ClaimMapping` only; env comes in Phase 2).

### Phase 2 — env-driven configuration + per-issuer mappings + DI

16. [ ] `varco_core/tests/test_jwt_transform_config.py` (new) — failing first, all via
    `monkeypatch.setenv`:
    - `VARCO_JWT_TRANSFORM_ROLES_FIELD="sofy-roles"` → `JwtParser.parse()` (no explicit
      transformer) yields `roles == {"editor"}`. **This is the user's headline scenario.**
    - Comma chain + `MERGE_SOURCES=true/false`.
    - `SCOPES_FIELD="scope"` with `"read write"` → `{"read","write"}`.
    - `TENANT_FIELD="org.id"` → `metadata["tenant_id"]`, and an end-to-end assertion that
      `TenantAwareService`'s `ctx.metadata["tenant_id"]` requirement is now satisfiable.
    - `ROLES_STRIP_PREFIX="ROLE_"`; `ROLES_SHAPE="csv"`; `ROLES_REQUIRED=true` + missing.
    - `PATH_SEPARATOR=":"`; `STRICT=true`.
    - **Per-issuer**: `VARCO_JWT_TRANSFORM__KEYCLOAK__ISS` + `__ROLES_FIELD` applies only
      to tokens with that `iss`; a token from another issuer gets the global mapping.
    - **Per-issuer inheritance**: label declares only `ROLES_FIELD`; global
      `SCOPES_FIELD`/`STRICT` still apply.
    - **`__ISS` fallback**: label with no `__ISS` but with
      `FASTREST_AUTHORIZATION__KEYCLOAK__ISS` set → resolves from there; with neither →
      label normalised (`KEYCLOAK` → `keycloak`).
    - **Unmapped issuer** (`iss` matches no label, no global config) → `IDENTITY`,
      canonical parsing, **no error**.
    - **Conflicting mappings**: two labels declaring the same `__ISS` → `ClaimTransformError`
      at config-load time naming both labels (fail fast, do not pick one silently).
    - **Unknown claim path** (`ROLES_FIELD="nope.nada"`) → empty roles when
      `STRICT=false`; `ClaimTransformError` when `ROLES_REQUIRED=true`.
    - **Extra-env safety**: `VARCO_JWT_TRANSFORM__X__ROLES_FIELD` present does not make
      `JwtTransformSettings()` raise (proves `extra="ignore"`).
    - `configure_claim_transforms()` overrides env; `reset_claim_transforms()` restores lazy.
17. [x] `varco_core/varco_core/jwt/transform/config.py` (new) —
    `JwtTransformSettings(VarcoSettings)` with
    `model_config = SettingsConfigDict(env_prefix="VARCO_JWT_TRANSFORM_", frozen=True,
    extra="ignore")`, all fields `str | None` / `bool` / `float` with defaults, plus
    `to_mapping() -> ClaimMapping`; and `JwtTransformConfig` (frozen: `default: ClaimMapping`,
    `per_issuer: tuple[tuple[str, ClaimMapping], ...]`) with `from_env()` (plain
    `os.environ` label grouping, mirroring `authority/config.py:127-184`) and
    `to_registry() -> ClaimTransformerRegistry`.
18. [x] `varco_core/varco_core/jwt/transform/registry.py` (new) —
    `ClaimTransformerRegistry` with `register(iss, transformer)`,
    `set_default(transformer)`, `for_issuer(iss) -> ClaimTransformer`, `__repr__`.
19. [x] `varco_core/varco_core/jwt/transform/runtime.py` — lazy builder now calls
    `JwtTransformConfig.from_env().to_registry()`; add
    `configure_jwt_from_env()` convenience (transforms + profiles from Phase 3).
20. [x] `varco_fastapi/varco_fastapi/di.py` — add to `VarcoFastAPIModule`:
    `@Provider(singleton=True) def jwt_transform_settings(self) -> JwtTransformSettings:
    return JwtTransformSettings.from_env()` and
    `@Provider(singleton=True) def claim_transformer_registry(self) ->
    ClaimTransformerRegistry: return JwtTransformConfig.from_env().to_registry()`.
    Pure providers (no side effects); update the class docstring's "Registers" list.
    ⚠️ `@Provider`, never `@Singleton` — pydantic `BaseSettings` `**values`.
21. [x] `varco_fastapi/varco_fastapi/app.py` — `create_varco_app()` calls
    `configure_jwt_from_env()` once during startup (before routers are built) so the
    process-global matches what DI hands out. Add a `configure_jwt: bool = True` kwarg to
    let an app opt out and configure the registry itself.
22. [ ] `varco_fastapi/tests/milestone_a/test_server_auth.py` — add
    `test_jwt_bearer_applies_env_claim_transform`: sign a Keycloak-shaped token with a
    local RSA key, register the authority in a `TrustedIssuerRegistry`, set
    `VARCO_JWT_TRANSFORM_ROLES_FIELD="realm_access.roles"`, and assert `JwtBearerAuth`
    returns an `AuthContext` with the mapped roles — proves SEAM 2 needs no extra code.
23. [x] Docs (Phase 2):
    - `technical_docs/features/jwt-claim-transformer.md` (new) — the full env-var table,
      the pipeline diagram, per-issuer precedence, Keycloak/Cognito/Auth0 recipes, and the
      `ClaimTransformer` Protocol escape hatch.
    - `mkdocs.yml` — add `- JWT Claim Transformer: features/jwt-claim-transformer.md`
      under `nav: Features:`.
    - `varco_core/README.md` — env-var quick start.
    - `CLAUDE.md` — new scenario "Consume a foreign-shaped JWT (Keycloak/Cognito)" + a
      Pitfalls row: *"Roles empty although the JWT has them" → claim is named
      `sofy-roles`/`realm_access.roles` → set `VARCO_JWT_TRANSFORM_ROLES_FIELD`.*
    - `ARCHITECTURE.md` — add the `varco_core.jwt.transform` module map.

### Phase 3 — token profiles + guard

24. [ ] `varco_core/tests/test_jwt_profiles.py` (new) — failing first:
    - `TokenProfile.matches()`: issuer-only; issuer+`token_type`; `aud` any-of against both
      the `str` and `frozenset` forms of `token.aud`; `required_claims` present/absent;
      empty `issuers` = any issuer.
    - **Missing required claim** → `matches()` `False` and `explain()` naming the claim.
    - `TokenProfileRegistry.resolve()` = first match in registration order; overlapping
      profiles documented as first-wins.
    - `get("nope")` → `TokenProfileError` listing known names.
    - `from_env()` with `VARCO_JWT_PROFILE__INTERNAL__*`; a label with **no** condition at
      all → `TokenProfileError` (a profile that matches everything is a footgun).
    - Parser integration: matched profile → `auth_ctx.metadata["token_profile"] ==
      "internal"`; `implied_roles` merged; **materialisation** — a token with only
      `sub`+`iss` matching a profile with `implied_roles` gets a non-`None` `auth_ctx`,
      while the same token with a profile declaring no implied roles/scopes keeps
      `auth_ctx is None` (today's behaviour).
    - `JwtUtil.matches_profile/profile_name/assert_profile`.
    - **Back-compat**: the four existing `is_system*` tests in `test_jwt.py:428-447`
      still pass unchanged; plus a new test that
      `VARCO_JWT_PROFILE__SYSTEM__ISS="my-org/internal"` makes `is_system()` true for that
      issuer and false for `varco/system`.
    - `JwtBuilder.as_profile(profile)` sets `iss`/`token_type`/`aud`.
25. [x] `varco_core/varco_core/jwt/profile.py` (new) — `TokenProfile`,
    `TokenProfileRegistry`, `PROFILE_METADATA_KEY`, `resolve_token_profile()`,
    `configure_token_profiles()`, `reset_token_profiles()` (same global pattern + DESIGN
    block cross-referencing `transform/runtime.py`).
26. [x] `varco_core/varco_core/jwt/util.py` — add `matches_profile`, `profile_name`,
    `assert_profile`; rewrite `is_system()` per §B (profile-if-registered, else
    live-`ClassVar` fallback); mark `SYSTEM_ISSUER` deprecated in both docstrings with a
    pointer to `VARCO_JWT_PROFILE__SYSTEM__ISS`.
27. [x] `varco_core/varco_core/jwt/parser.py` — after building the token, resolve the
    profile and augment/materialise `auth_ctx` (`profile_transformer=`-style explicit
    override param `profiles: TokenProfileRegistry | None = None` on `parse`/
    `_from_raw_claims` for testability).
28. [x] `varco_core/varco_core/jwt/builder.py` — `as_profile(profile)`.
29. [x] `varco_fastapi/varco_fastapi/auth/guard.py` — add the frozen field
    `token_profiles: tuple[str, ...] = ()`, check it in `check()` between steps 3 and 4
    (message: `"Token profile 'x' required; token profile is 'y'"`), and add
    `require_token_profile(*names)`; update the module docstring + `__all__`
    (`varco_fastapi/auth/__init__.py` too).
30. [ ] `varco_fastapi/tests/auth/test_token_profile_guard.py` (new) — a `GenericRouter`
    with `@route(..., requires=require_token_profile("internal"))`:
    matching profile → 200; non-matching → 403; anonymous → 403;
    `ctx.metadata` missing the key → 403 with the actionable message.
31. [x] Docs (Phase 3): `technical_docs/features/token-profiles.md` (new) + `mkdocs.yml`
    nav entry; `technical_docs/features/route-guard.md` — document
    `require_token_profile`; `varco_core/README.md` + `varco_fastapi/README.md`;
    `CLAUDE.md` — replace any `JwtUtil.SYSTEM_ISSUER` guidance with token profiles and add
    a Pitfalls row: *"`is_system()` false for my internal token" → only one static
    `SYSTEM_ISSUER` was configured → define `VARCO_JWT_PROFILE__SYSTEM__ISS` (or a named
    profile) instead.*; `ARCHITECTURE.md`.

### Phase 4 — leeway, enforced audience, `PassthroughAuth` refactor

32. [ ] `varco_core/tests/test_jwt.py` — extend: a token expired 10 s ago parses with
    `leeway=30` and raises `ExpiredSignatureError` with `leeway=0`; `VARCO_JWT_LEEWAY_SECONDS`
    picked up when `leeway=` is omitted; `JwtUtil.is_expired(leeway=…)`;
    `nbf` 10 s in the future accepted with leeway.
33. [x] `varco_core/varco_core/jwt/parser.py` + `varco_core/varco_core/authority/registry.py`
    — thread `leeway` into PyJWT `decode(..., leeway=…)`; default from a new
    `JwtVerificationSettings(VarcoSettings)` (`env_prefix="VARCO_JWT_"`,
    fields `leeway_seconds: float = 0.0`, `audience: str | None = None`) in
    `varco_core/varco_core/jwt/transform/config.py`… ⚠️ put it in a **new**
    `varco_core/varco_core/jwt/config.py` instead — verification settings are not a
    transform concern.
34. [ ] `varco_fastapi/tests/milestone_a/test_server_auth.py` — extend:
    `JwtBearerAuth(registry, audience="orders")` rejects a token with `aud="billing"`
    (401) and accepts `aud="orders"`; with `audience=None` both pass (documented default);
    `VARCO_JWT_AUDIENCE` used when the kwarg is omitted.
35. [x] `varco_fastapi/varco_fastapi/auth/server_auth.py` —
    - `JwtBearerAuth.__init__(..., audience: str | list[str] | None = None,
      leeway: float | None = None)`, defaults from `JwtVerificationSettings.from_env()`;
      pass both to `registry.verify(...)`. Log **one** `warning` at construction when
      `audience is None` ("aud is not enforced; set VARCO_JWT_AUDIENCE …").
    - Replace the hand-rolled parsing in `PassthroughAuth.__call__`
      (`server_auth.py:329-374`) with `JwtParser.parse_unverified(raw_token)`, then
      `ctx = token.auth_ctx or AuthContext(user_id=token.sub)` and merge
      `token.extra_claims` into `ctx.metadata` (preserving today's metadata behaviour —
      assert this with a regression test using the existing reserved-key expectations).
36. [ ] `varco_fastapi/tests/milestone_a/test_server_auth.py` — `PassthroughAuth`
    regression: same `AuthContext` as before the refactor for a canonical token, **plus**
    the transformed roles for a `sofy-roles` token.
37. [x] Docs (Phase 4): `technical_docs/features/jwt-claim-transformer.md` — "Verification
    hardening" section (leeway + audience); `CLAUDE.md` Pitfalls rows: *"Token from another
    service accepted" → `aud` was never enforced → set `VARCO_JWT_AUDIENCE` /
    `JwtBearerAuth(audience=…)"*, and *"Intermittent 401 across hosts" → clock skew → set
    `VARCO_JWT_LEEWAY_SECONDS=30`.*

### Phase 5 — JWKS refresh knobs

38. [ ] `varco_core/tests/test_trusted_issuer_registry.py` — extend: a `kid` miss inside
    `min_refresh_interval` does not re-hit the source; outside it does;
    `ttl_seconds` triggers a proactive reload once the cached keyset's age exceeds the TTL;
    `ttl_seconds=0` disables it (default = today's behaviour).
39. [x] `varco_core/varco_core/authority/registry.py` —
    `__init__(self, *, min_refresh_interval: float | None = None,
    ttl_seconds: float | None = None)` (defaults from `VARCO_JWKS_MIN_REFRESH_SECONDS`
    `= 10.0` and `VARCO_JWKS_TTL_SECONDS` `= 0.0`), add `_loaded_at` to `__slots__`,
    age check at the top of `get_key()`. Keep the existing lazy-`asyncio.Lock` pattern
    (`registry.py:179-193`) — do **not** introduce a background task.
40. [x] Docs (Phase 5): `varco_core/README.md` + the feature doc — JWKS caching knobs and
    an explicit note that background refresh is deferred.

### Phase 6 — public-API + consistency sweep

41. [ ] `varco_core/tests/test_public_api.py` — add
    `test_all_jwt_symbols_importable` mirroring the existing event-`__all__` check for
    `varco_core.jwt` and `varco_core.jwt.transform`.
42. [x] `ARCHITECTURE.md` — final pass: the `varco_core.jwt` tree (new modules), the
    `ClaimTransformer` / `TokenProfile` type hierarchies, and the transform pipeline
    diagram; add both to the "Decision Tree" (`JWT claim shape? → varco_core.jwt.transform`).
43. [x] `CLAUDE.md` — final pass: the two new scenarios and all four Pitfalls rows in one
    place, plus the `VARCO_JWT_*` env-var reference block.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| No `VARCO_JWT_TRANSFORM*` / `VARCO_JWT_PROFILE*` set | `IDENTITY` transformer, empty profile registry → **byte-identical** to today. Guarded by a full-token equality regression test. |
| `iss` absent from the token | Global mapping applies (per-issuer lookup skipped). |
| `iss` matches no configured label | Global mapping; **no error**. |
| Two labels declare the same `__ISS` | `ClaimTransformError` at config-load, naming both labels. Fail fast. |
| Claim path segment missing / intermediate not a mapping | Value is `MISSING` → next source in the chain → finally `[]`/`None`. `required=true` → `ClaimTransformError` naming every tried path. |
| `"a,b"` under `ValueShape.AUTO` | Split on comma → `["a","b"]`. A single role legitimately containing a comma needs `SHAPE=scalar`. Documented ambiguity. |
| `roles` claim is `5` (int) | `strict=false` → `["5"]` + one `logger.warning`; `strict=true` → `ClaimTransformError`. |
| `grants` claim malformed (`[{"resource":"posts"}]`) | `ClaimTransformError` naming index `0` and the missing `actions` key (today: bare `KeyError`). |
| Mapped source name **is** a canonical name (`realm_access.roles` → `roles`) | Canonical key is overwritten in the transformed dict; the original `roles` value is still visible under `extra_claims` **only if** it was not the canonical key itself (same key ⇒ the mapped value wins). Documented. |
| Foreign claim also lands in `extra_claims` | Intentional and non-destructive — `extra_claims` is built from the **raw** dict. |
| Token has only `sub` + `iss`, no auth claims, no matching profile | `auth_ctx is None` — unchanged from today. |
| Same token, matching profile with `implied_roles` | `auth_ctx` **materialised** with `user_id=sub` + implied roles. ⚠️ intentional behaviour change for profile-matched tokens only. |
| Token has `tenant_id`/`actor` but no roles/scopes/grants | `auth_ctx` materialised (widened trigger vs. today's roles/scopes/grants-only rule). Called out in the changelog. |
| Two profiles both match | First registered wins; `resolve()` order documented; `explain()` available for diagnosis. |
| Profile label with no conditions at all | `TokenProfileError` at load — a match-everything profile would silently grant implied roles to every token. |
| `JwtUtil.SYSTEM_ISSUER` monkeypatched, **no** `system` profile registered | Legacy path: `token.iss == JwtUtil.SYSTEM_ISSUER` evaluated live. Existing test passes unchanged. |
| `system` profile registered **and** `SYSTEM_ISSUER` monkeypatched | Profile wins; documented precedence. |
| `configure_claim_transforms()` called after the first parse | Takes effect immediately for subsequent parses (the global is replaced, not merged). |
| Explicit `transformer=` argument | Always wins over the global registry (per-call escape hatch, used by tests). |
| `audience=None` on `JwtBearerAuth` | `aud` not enforced (today's behaviour) + one startup warning. Opt-in hardening. |
| `leeway=0` (default) | Identical to today. |
| Composite deployment (two services, one process) | The transform/profile globals are **process-wide** ⚠️ — two mounted services needing *different* mappings must configure per-issuer entries (keyed by `iss`) rather than differing globals. Called out in §Risks and in the composite-deployment doc. |

---

## Verification

```bash
# From the workspace root
uv sync

# Phase 0–1
uv run pytest varco_core/tests/test_jwt_claim_path.py varco_core/tests/test_jwt_transform.py -v
# Phase 2
uv run pytest varco_core/tests/test_jwt_transform_config.py -v
# Phase 3
uv run pytest varco_core/tests/test_jwt_profiles.py -v
uv run pytest varco_fastapi/tests/auth/test_token_profile_guard.py -v
# Phase 4–5
uv run pytest varco_core/tests/test_jwt.py varco_core/tests/test_trusted_issuer_registry.py -v
uv run pytest varco_fastapi/tests/milestone_a/test_server_auth.py -v

# Regression gates (must stay green at every phase boundary)
uv run pytest varco_core/tests/ -q
uv run pytest varco_fastapi/tests/ -q
uv run pytest varco_casbin/tests/ -q          # policy authorizer consumes AuthContext
uv run pytest varco_core/tests/test_public_api.py -v

# Docs build (mkdocs nav additions must resolve)
uv run mkdocs build --strict
```
No `@pytest.mark.integration` tests are required: the transformer and profiles touch no
external system (env vars + in-process signing with a locally generated RSA key). The one
end-to-end check (Phase 2 step 22 / Phase 3 step 30) runs in-process through
`JwtBearerAuth` / FastAPI `TestClient`.

---

## Risks

- **Mapping is selected by an unenforced `iss`.** `registry.verify()` deliberately does not
  validate `iss` (`registry.py:20-24`). A validly-signed token from issuer A that sets
  `iss=B` would get B's mapping. *Invariant that makes this safe:* the transformer only
  **renames/reshapes claims already inside the verified token** — it can never import a
  value from another token or from configuration. Impact is limited to reading the wrong
  field name. Mitigations shipped: `TokenProfile.issuers` gives per-profile `iss`
  enforcement; the feature doc recommends `JwtUtil(token).is_issuer(...)`. Full `iss`
  enforcement in `verify()` is deferred (C-11).
- **Profiles can grant privileges from configuration.** `implied_roles`/`implied_scopes`
  mean a mis-scoped env var (e.g. an over-broad `__ISS`) grants roles to real user tokens.
  *Invariant:* a profile with **no** conditions is rejected at load; every profile must
  constrain at least one of `iss`/`token_type`/`aud`/`required_claims`. Docs must state
  that `implied_roles` is for internal/system tokens only.
- **Process-global config vs. composite deployments.** `create_composite_app` runs several
  services in one process sharing one `os.environ` and now one transform/profile registry.
  *Invariant:* per-issuer entries (keyed by `iss`) are the supported way to differentiate;
  differing **global** mappings across mounted services are not supported. Documented in
  `technical_docs/features/composite-deployment.md`.
- **`_RESERVED_CLAIM_KEYS` growth is a behaviour change for `JwtBuilder.claim()`**
  (`builder.py:279-286` raises for reserved keys). Adding `tenant_id`/`act` will make
  previously-legal `builder.claim("tenant_id", …)` raise ⚠️. Mitigation: the error message
  points at the new supported path (`AuthContext.metadata["tenant_id"]`); called out as a
  ⚠️ breaking change in the release notes and covered by a test.
- **Widened `auth_ctx` materialisation.** Tokens carrying only `tenant_id`/`actor`, or
  matching a profile with implied roles, now get a non-`None` `auth_ctx` where they
  previously got `None`. Code doing `if token.auth_ctx is None: treat as machine token`
  changes behaviour. *Invariant:* canonical tokens with none of
  roles/scopes/grants/tenant/actor and no matching profile still yield `None`; guarded by
  the regression test in step 6.
- **pydantic-settings env parsing traps.** `list[str]` fields would demand JSON, and the
  per-issuer vars share the settings prefix. *Invariant:* all list-ish fields are declared
  `str`, and `model_config` sets `extra="ignore"`. Both covered by explicit tests (step 16).
- **`PassthroughAuth` refactor may change `metadata` contents.** Its current reserved-key
  set (`server_auth.py:360-373`) differs from `_RESERVED_CLAIM_KEYS`. *Invariant:* a
  golden-value regression test pins the resulting `AuthContext` for a canonical token
  before and after (step 36).
- **No type-check gate in CI** (same limitation as plan 001) — Protocol conformance of
  user transformers is only verified by `runtime_checkable` `isinstance` checks in tests.

---

## Open decisions (flip any of these during review)

| # | Decision | Recommendation (taken) | Alternative | Assumption |
|---|---|---|---|---|
| D-1 | Env prefix | `VARCO_JWT_TRANSFORM_*` (adds `VARCO_` to the user's literal `JWT_TRANSFORM_ROLES_FIELD`) | honour the bare `JWT_TRANSFORM_*` names, or accept both with the bare form deprecated | The repo-wide `VARCO_` prefix convention (`config.py:12-19`) outweighs matching the request literally. Flip = one `env_prefix` string. |
| D-2 | Remap `exp`/`iat`/`nbf`/`aud`/`jti`/`iss`? | **No** | allow `aud`/`jti` remapping | Those claims are verified by PyJWT before this layer; `iss` is the selector. |
| D-3 | `user_id` sourced from a non-`sub` claim | Allowed; `token.sub` keeps the raw RFC value | force `user_id == sub` always | Keycloak users often key on `preferred_username`. `sub` staying raw preserves RFC semantics. |
| D-4 | `to_claims()` output names | **Canonical** | emit mapped external names | varco is the *issuer* when it builds tokens; emitting mapped names would make signing depend on inbound env config and break the parse/encode symmetry. `ClaimMapping.invert()` is the escape hatch. |
| D-5 | New fields on `AuthContext` for tenant/actor/profile | **No** — use `metadata` | add `tenant_id: str | None` to `AuthContext` | `TenantAwareService` already reads `ctx.metadata["tenant_id"]` (`tenant.py:72`), so `metadata` is the established contract. A real field is a wider public-API change. |
| D-6 | Per-issuer keying | by the **`iss` claim value**, env authored by label, `__ISS` falling back to `FASTREST_AUTHORIZATION__<LABEL>__ISS` | key by `AuthorizationConfig` label and have the parser consult the registry | The parser only has claims, not registry labels; keying by `iss` keeps `varco_core.jwt` independent of `varco_core.authority`. |
| D-7 | Implicit canonical fallback appended to every chain | **Yes** | require it to be listed explicitly | Makes mixed fleets (some canonical, some foreign) work with one config. Flip = drop one `+ (canonical,)`. |
| D-8 | Per-issuer mapping inherits the global | **Yes**, field-by-field | full replace | Avoids repeating `SHAPE`/`STRICT`/`STRIP_PREFIX` per issuer. |
| D-9 | Path syntax | dotted only (`\.` escape); **no** array indexing / JSONPath | support `a[0].b` or a JSONPath dependency | No observed real-world claim needs indexing; zero new dependency. |
| D-10 | Multi-source semantics default | **first-non-empty wins**; `MERGE_SOURCES=true` to union | merge by default | First-wins is predictable and matches "try `sofy-roles`, then `roles`". |
| D-11 | `SYSTEM_ISSUER` deprecation | docs-only; keeps working; **no** removal scheduled, **no** runtime warning | emit a `DeprecationWarning` and remove in the next major | A `ClassVar` read cannot be intercepted without a metaclass descriptor; churn not worth it. |
| D-12 | Where profile augmentation runs | inside `_from_raw_claims` (so both seams get it) | a separate explicit `registry.augment(token)` call | Keeping it in the funnel is the whole point of the design; the cost is that the parser now applies config-driven privileges (mitigated by rejecting condition-less profiles). |
| D-13 | `RouteGuard` profile check | new frozen **field** `token_profiles` | a `require_predicate` closure | A field stays hashable/comparable/introspectable and matches the existing `scopes`/`roles` style. |
| D-14 | Case folding of roles/scopes | **not** offered | add `*_LOWERCASE` env flags | `strip_prefix` covers the observed Keycloak/Spring need; case folding invites subtle authz bugs. Cheap to add later. |
| D-15 | `strict` default | `false` (warn-and-skip) | `true` (fail fast) | A shape surprise should not 500 an otherwise-valid request; `STRICT=true` is one env var away for teams that prefer fail-fast. |
| D-16 | JWKS background refresher | deferred; only TTL + min-interval knobs | ship a background task now | A task needs lifespan start/stop wiring and a failure policy — its own plan. |
| D-17 | Audience enforcement default | `None` (opt-in) + one startup warning | enforce whenever `VARCO_JWT_AUDIENCE` is set, warn loudly otherwise | Enforcing by default would 401 existing deployments whose tokens carry a different `aud`. |
