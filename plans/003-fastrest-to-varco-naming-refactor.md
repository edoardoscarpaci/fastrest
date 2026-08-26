# Plan 003 — Purge legacy `FASTREST` naming: env vars, class names, error codes, docs

## Goal

Every runtime-visible `FASTREST*` identifier in the varco workspace is renamed to the
`VARCO*` convention: the two authorization env vars move under the existing
`VARCO_JWT_*` family, the three public classes get `Varco`/neutral names, and the five
error-code strings become `VARCO_0xx`. Old env-var names and old class names keep
working for one deprecation window (emitting `DeprecationWarning`); the error-code
string values change hard. All documentation, docstrings and tests are updated in the
same change.

## Non-goals

- **No behaviour change** beyond naming: no new env vars, no new config knobs, no
  change to issuer-resolution precedence, no change to HTTP status mapping.
- **No edits to `plans/002-jwt-claim-transformer-and-token-profiles.md`** — a dated
  historical artifact; rewriting it would falsify the record (decision D-8).
- **No edits inside `.claude/worktrees/*`** — separate checkouts (see Risks).
- **No dependency-pin changes** across workspace packages (siblings declare a bare
  `"varco-core"` with no version specifier; tightening that is a separate concern).
- **No compatibility shim that emits the old error-code strings** (decision D-5).
- Renaming the package-internal *module* `varco_core/authority/config.py` or the class
  `AuthorizationConfig` — those names contain no `Fastrest`, they stay.

---

## Verified starting state

Confirmed by direct read on 2026-07-26 (branch `main`, HEAD `6ea8aee`). An executor
must re-confirm these before editing; if a line has drifted, re-grep rather than
trusting the number.

### Env vars (runtime reads)
| Anchor | Content |
|---|---|
| `varco_core/varco_core/authority/config.py:62` | `_ENV_PREFIX: str = "FASTREST_AUTHORIZATION__"` |
| `varco_core/varco_core/authority/config.py:65-66` | `_FIELD_URL = "URL"`, `_FIELD_ISS = "ISS"` |
| `varco_core/varco_core/authority/config.py:149-184` | the `os.environ` scan loop in `AuthorizationConfig.from_env()` |
| `varco_core/varco_core/jwt/transform/config.py:57` | `_AUTHORIZATION_ISS_PREFIX = "FASTREST_AUTHORIZATION__"` |
| `varco_core/varco_core/jwt/transform/config.py:272-274` | the only use: `fields_dict.get("ISS") or os.environ.get(f"{_AUTHORIZATION_ISS_PREFIX}{label}__ISS")` |

### Public identifiers
| Anchor | Content |
|---|---|
| `varco_sa/varco_sa/bootstrap.py:184` | `class SAFastrestApp:` |
| `varco_sa/varco_sa/bootstrap.py:338-341` | `__all__ = ["SAConfig", "SAFastrestApp"]` (the real one; **line 71 is a docstring line, not code** — the scout's "`__all__` bootstrap.py:71" is the module-docstring mention) |
| `varco_sa/varco_sa/__init__.py:43, 101` | eager import + `__all__` entry |
| `varco_beanie/varco_beanie/bootstrap.py:117` | `class BeanieFastrestApp:` |
| `varco_beanie/varco_beanie/bootstrap.py:217` | `__all__` entry |
| `varco_beanie/varco_beanie/__init__.py:46, 75` | eager import + `__all__` entry |
| `varco_core/varco_core/exception/codes.py:106` | `class FastrestErrorCodes(Enum):` |
| `varco_core/varco_core/exception/codes.py:199-202` | `__all__ = ["ErrorCode", "FastrestErrorCodes"]` |
| `varco_core/varco_core/__init__.py:239, 467` | eager import + `__all__` entry |

### Error-code string values
`varco_core/varco_core/exception/codes.py` — `"FASTREST_001"`:142, `"FASTREST_002"`:149,
`"FASTREST_003"`:156, `"FASTREST_004"`:163, `"FASTREST_500"`:172.
Consumers: `varco_core/varco_core/exception/http.py:145-149` (`_EXCEPTION_CODE_MAP`),
`:196` (`INTERNAL_ERROR` fallback), `:239-242` (translator receives `msg.code`);
`varco_fastapi/varco_fastapi/middleware/error.py:233` and
`varco_fastapi/varco_fastapi/exceptions.py:68` emit `msg.code` into the JSON body.

### Occurrences the scout report missed (add to the sweep)
| Anchor | Content |
|---|---|
| `varco_core/varco_core/authority/registry.py:584` | error-message string `"Check FASTREST_AUTHORIZATION__* env vars — …"` — **user-visible at runtime**, not just a docstring |
| `varco_core/varco_core/authority/registry.py:710-711, 719-722` | `from_env()` docstring + example block |
| `varco_core/varco_core/authority/registry.py:745, 785` | `from_container(include_env=…)` docstring |
| `varco_core/README.md:61` | `FastrestErrorCodes` in the module table |
| `varco_fastapi/varco_fastapi/exceptions.py:113` | already documents `{"code": "VARCO_XXXX", …}` — **the docstring is currently wrong**; phase 2 makes it true |

### Test surface
- `varco_core/tests/test_jwt_transform_config.py:191-200` — `monkeypatch.setenv("FASTREST_AUTHORIZATION__KEYCLOAK__ISS", …)`
- `varco_core/tests/test_trusted_issuer_registry.py:20, 446, 490` — comments on `include_env=False` isolation
- `varco_sa/tests/test_pool_metrics.py:9, 174, 179, 184, 195` — `test_sa_fastrest_app_pool_metrics_delegate`
- **No test anywhere asserts on the `FASTREST_00x` string values** — the error-code
  rename is currently uncovered. Phase 2 must add that coverage.

### Versions / tooling
`varco_core` 1.1.1, `varco_sa` 2.1.0, `varco_beanie` 1.1.0, `varco_fastapi` 1.1.4.
`filterwarnings` in each `pyproject.toml` (e.g. `varco_core/pyproject.toml:72-76`) only
*ignores* third-party `DeprecationWarning`s — warnings are **not** errors, so new
deprecation warnings will not break the suite, and `pytest.warns(DeprecationWarning)`
works as-is. No `FASTREST` reference exists in any `pyproject.toml`, CI workflow or
Dockerfile.

---

## Design

### Naming map (canonical outcome)

```
env   FASTREST_AUTHORIZATION__<LABEL>__URL  →  VARCO_JWT_AUTHORIZATION__<LABEL>__URL
env   FASTREST_AUTHORIZATION__<LABEL>__ISS  →  VARCO_JWT_AUTHORIZATION__<LABEL>__ISS
class SAFastrestApp                         →  SAApp
class BeanieFastrestApp                     →  BeanieApp
class FastrestErrorCodes                    →  VarcoErrorCodes
value "FASTREST_001".."FASTREST_004", "FASTREST_500"  →  "VARCO_001".."VARCO_004", "VARCO_500"
```

### One shared deprecation helper

Both the env-var fallback and the three class aliases need the same "warn once, keep
working" behaviour, in three different packages. Introduce **one** small module in
`varco_core` and reuse it everywhere:

```
varco_core/varco_core/deprecation.py
├── warn_deprecated(old, new, *, removal, stacklevel=2, log=False) -> None
└── make_deprecated_alias_getattr(module: str, aliases: Mapping[str, str],
                                  namespace: Mapping[str, object], *, removal: str)
        -> Callable[[str], object]        # a PEP 562 module __getattr__
```

`varco_sa` and `varco_beanie` already depend on `varco-core`, so no new dependency edge
is created.

```
varco_core.deprecation
   ↑                 ↑                    ↑                 ↑
authority/config  exception/codes   varco_sa/bootstrap  varco_beanie/bootstrap
 (env fallback)    (class alias)      (class alias)        (class alias)
```

### DESIGN blocks

**D-1 — Env-var read path: tuple of prefixes, canonical first, per-field merge.**
`_ENV_PREFIX: str` becomes
`_ENV_PREFIXES: tuple[str, ...] = (_CANONICAL_ENV_PREFIX, _LEGACY_ENV_PREFIX)` and the
scan loop iterates prefixes in order, merging into the same `groups[label][field]` dict
with **canonical winning per field**.
- ✅ A half-migrated environment (`__URL` renamed, `__ISS` not yet) still resolves.
- ✅ One loop, one code path — no duplicated parsing logic to drift.
- ✅ Precedence is deterministic and testable (canonical always wins a collision).
- ❌ A stale legacy var silently loses to a canonical one; mitigated by warning on
  *every* legacy hit, including shadowed ones.
- Alternative rejected: mutating `os.environ` at import to copy legacy→canonical
  (❌ global side effect on the host process, breaks `monkeypatch` isolation, and
  makes the read path depend on import order).
- Alternative rejected: a `VarcoSettings` pydantic model (❌ the label segment is
  dynamic; `authority/config.py:34-39` already records why plain `os.environ` won here).

**D-2 — Deprecation signal: `DeprecationWarning` *and*, for env vars only, one
`logger.warning`.**
- ✅ `DeprecationWarning` is the standard, pytest-visible, `-W error`-testable signal.
- ❌ `DeprecationWarning` is hidden by default outside `__main__` — an operator running
  a container would never see it, and env-var config is an *ops-facing* mistake.
  Therefore `AuthorizationConfig.from_env()` / the transform ISS fallback **also** emit
  a `logger.warning` (once per process, guarded by a module-level `set[str]` of already
  warned names). Class aliases stay warning-only (dev-facing, seen in test runs).
- Alternative rejected: `FutureWarning` for everything (✅ always displayed;
  ❌ semantically means "behaviour will change", not "name will be removed", and would
  spam every library user's stderr).
- `stacklevel=2` throughout, so the warning points at the caller's import/`from_env()`
  line, not at varco internals.

**D-3 — Class aliases via PEP 562 module `__getattr__`, and old names *out* of `__all__`.**
- ✅ Returns the **same class object** — `SAFastrestApp is SAApp`, `isinstance` checks,
  pickling and DI type-keys all keep working. A subclass shim would break identity.
- ✅ Warns exactly at the moment of access (`from varco_sa import SAFastrestApp`),
  never on plain `import varco_sa`. A module-level `SAFastrestApp = SAApp` assignment
  cannot warn at all.
- ✅ Keeping the old name **out of `__all__`** means `from varco_sa import *` no longer
  drags in a deprecated name and never emits a spurious warning for code that does not
  use it.
- ❌ Static analysers do not see names produced by `__getattr__`. Mitigated with a
  `if TYPE_CHECKING:` block declaring `SAFastrestApp = SAApp` (type-checker-visible,
  zero runtime cost, no warning at runtime because the block never executes).
- ❌ `from varco_sa import *` no longer exports the old name — a real, accepted, minor
  break; called out in the migration guide.
- Both the package `__init__.py` **and** the defining module (`bootstrap.py`) need a
  `__getattr__`, because the `__init__.py` currently imports the name eagerly (which
  would fire the warning at package-import time — unacceptable). The `__init__.py`
  imports only the new name eagerly and declares its own alias `__getattr__`.

**D-4 — Enum: rename the class and the *values*; keep the *member names*.**
`VarcoErrorCodes.NOT_FOUND`, `.UNAUTHORIZED`, `.CONFLICT`, `.VALIDATION_ERROR`,
`.INTERNAL_ERROR` are unchanged; only `.value.code` strings change.
- ✅ Every internal reference (`exception/http.py:145-149`) keeps compiling unchanged
  apart from the class name — member names are the API that Python code actually uses.
- ✅ Blast radius is confined to the wire format and to i18n catalogs.
- ❌ Anyone who hard-coded the string (log greps, alert rules, API clients, catalogs)
  breaks. That is the change the user explicitly accepted; mitigated by D-5.

**D-5 — Migration aid: an exported old→new map, not a runtime compatibility mode.**
Add to `exception/codes.py`:
`LEGACY_ERROR_CODE_MAP: Mapping[str, str]` = `{"FASTREST_001": "VARCO_001", …}`
(a `MappingProxyType` over a module-level dict, so it is read-only).
- ✅ Gives i18n catalog owners a machine-readable migration table and gives the plan a
  testable artifact (one test asserts it covers exactly the five members).
- ✅ Lets a downstream translator fall back: `catalog.get(code) or catalog[LEGACY[code]]`
  — documented in the migration guide.
- ❌ Ships a constant that is dead weight after the next major; delete it with the
  class aliases.
- Alternative rejected: an env var (`VARCO_LEGACY_ERROR_CODES=true`) that restores the
  old strings (❌ two possible wire formats forever, ❌ makes the response body depend
  on deployment config, ❌ the user chose a hard break).
- Alternative rejected: adding a `legacy_code` field to the frozen `ErrorCode`
  dataclass (❌ pollutes a permanent value object with a transitional concern; ❌ every
  app-defined custom `ErrorCode` would have to think about it).

**D-6 — Which namespace the env var joins.** `VARCO_JWT_AUTHORIZATION__*` (user
decision — all JWT config stays in the single `VARCO_JWT_*` family rather than a new
`VARCO_AUTHORIZATION_*` top-level namespace). Note the prefix is a strict superset of
neither `VARCO_JWT_TRANSFORM_` nor `VARCO_JWT_PROFILE__`, and nothing scans a bare
`VARCO_JWT_` prefix with `extra="allow"` (`jwt/config.py` uses explicit fields), so no
pydantic-settings model will accidentally swallow or reject the new vars. **The
executor must re-verify this** by checking `JwtVerificationSettings.model_config` before
finishing phase 1.

**D-7 — ⚠️ INFERRED, not user-stated: the env-var alias is deprecated, not removed.**
The user explicitly chose deprecated aliases for *class names*; this plan applies the
same policy to the *env vars* for consistency. **If a hard rename is wanted instead**
(no fallback, no warning — a redeploy must set the new name), strike phase 1 steps
1.4–1.6 and the corresponding tests; everything else is unaffected. Flag this at review.

**D-8 — `plans/002-*.md` is left byte-identical.** It is a dated record of what was
decided at that time; rewriting history makes the archive untrustworthy. The *current*
truth lives in `technical_docs/`, `README.md`, `CLAUDE.md` and `ARCHITECTURE.md`, all
of which are updated here.

**D-9 — Removal window.** All aliases (env prefix, three class names,
`LEGACY_ERROR_CODE_MAP`) are documented as "removed in the next major". Every shim
carries an identical `.. deprecated::` docstring marker and the string
`"removed in the next major release"` in its warning message, so a future executor can
find all of them with one grep for `deprecation.warn_deprecated`.

---

## Steps

Ordering rationale: **phase 1 (env) and phase 2 (error codes) are independent of each
other and both touch only `varco_core`, so they come first and can land in either
order — but both must precede phase 3**, because the class rename of
`FastrestErrorCodes` (phase 3) touches the same file as the code-value rename (phase 2)
and doing the value rename inside an already-renamed-and-aliased class makes both
diffs harder to review. Phase 4 (tests of pre-existing files) follows the code so the
suite is green at every phase boundary; new *per-phase* tests are written **before**
the code inside each phase, per repo convention. Phase 5 (docs) last, because it
describes the finished state. Phase 6 is the final consistency gate.

### Phase 0 — scaffolding

1. [ ] `varco_core/tests/test_deprecation_helper.py` — **new, failing first.** Tests for
   `varco_core.deprecation`: `warn_deprecated()` raises `DeprecationWarning` with the old
   name, the new name and `"next major"` in the message; a second call with the same
   `old` does **not** log twice (but *does* warn again — warning dedup is `warnings`'
   job, log dedup is ours); `make_deprecated_alias_getattr` returns the mapped object
   and warns; an unknown attribute raises `AttributeError` with the standard
   `module 'x' has no attribute 'y'` message.
2. [ ] `varco_core/varco_core/deprecation.py` — **new module.** `from __future__ import
   annotations`; module logger; `_ALREADY_LOGGED: set[str]`; the two public functions
   with full `Args:`/`Returns:`/`Raises:`/`Edge cases:`/`Thread safety:` docstrings and
   a `DESIGN:` block referencing D-2/D-3. Export via `__all__`.
   *Thread safety note to write: `_ALREADY_LOGGED` is a plain `set` mutated without a
   lock — a benign race can log the same deprecation twice; never a correctness issue,
   and no `asyncio.Lock` is created (repo rule: no locks outside a running loop).*
   - Verify: `uv run pytest varco_core/tests/test_deprecation_helper.py`

### Phase 1 — env-var rename with legacy fallback

3. [ ] `varco_core/tests/test_authorization_env_config.py` — **new, failing first.**
   Cases (all with `monkeypatch.delenv`-clean environments):
   - canonical only: `VARCO_JWT_AUTHORIZATION__GOOGLE__URL/__ISS` → one `IssuerConfig`,
     **no** warning (`warnings.catch_warnings(record=True)` asserts empty).
   - legacy only: `FASTREST_AUTHORIZATION__GOOGLE__URL/__ISS` → identical
     `IssuerConfig`, **and** `pytest.warns(DeprecationWarning, match="VARCO_JWT_AUTHORIZATION")`.
   - both set for the same label+field, different values → canonical value wins,
     warning still emitted.
   - split across prefixes: `__URL` canonical + `__ISS` legacy → merged into one
     `IssuerConfig` with the legacy `iss` (D-1 per-field merge).
   - legacy label with `__URL` but no `__ISS` → the existing label-normalisation
     fallback (`SYSTEM_SVC` → `system-svc`) still applies.
   - empty environment → empty `issuers` tuple, no warning.
4. [ ] `varco_core/tests/test_jwt_transform_config.py` — **add** a canonical-prefix twin
   of the existing `test_iss_fallback_to_authorization_config_label` (line 191) using
   `VARCO_JWT_AUTHORIZATION__KEYCLOAK__ISS`, and **rewrite** the existing legacy one to
   additionally assert `pytest.warns(DeprecationWarning)`.
5. [ ] `varco_core/varco_core/authority/config.py:59-66` — replace `_ENV_PREFIX` with
   `_CANONICAL_ENV_PREFIX = "VARCO_JWT_AUTHORIZATION__"`,
   `_LEGACY_ENV_PREFIX = "FASTREST_AUTHORIZATION__"`, and
   `_ENV_PREFIXES: tuple[str, ...] = (_CANONICAL_ENV_PREFIX, _LEGACY_ENV_PREFIX)`.
6. [ ] `varco_core/varco_core/authority/config.py:145-184` — rewrite the scan loop to
   iterate `_ENV_PREFIXES`; on a legacy prefix hit call
   `warn_deprecated(f"{_LEGACY_ENV_PREFIX}…", f"{_CANONICAL_ENV_PREFIX}…", log=True)`;
   set `groups[label][field]` only if the field is absent (canonical scanned first ⇒
   canonical wins).
7. [ ] `varco_core/varco_core/authority/config.py:1-45, 96, 109, 130, 143` — module
   docstring + `AuthorizationConfig`/`from_env` docstrings: canonical names in all
   examples, one `.. deprecated::` paragraph documenting the legacy prefix, its
   precedence and its removal window; extend the `Edge cases:` list with the two new
   cases (both prefixes set; split across prefixes).
8. [ ] `varco_core/varco_core/jwt/transform/config.py:54-57` — `_AUTHORIZATION_ISS_PREFIX`
   becomes a tuple `_AUTHORIZATION_ISS_PREFIXES` mirroring D-1.
9. [ ] `varco_core/varco_core/jwt/transform/config.py:271-276` — try canonical first,
   then legacy (warning on legacy hit), then the normalised-label fallback. Update the
   module docstring at `:9` and the constant comment at `:54-56`.
10. [ ] `varco_core/varco_core/authority/registry.py:584` — the runtime error message
    now reads `Check VARCO_JWT_AUTHORIZATION__* env vars — …`.
11. [ ] `varco_core/varco_core/authority/registry.py:710-711, 719-722, 745, 785` —
    docstrings and the example block use the canonical prefix; add a one-line note that
    the legacy prefix is still honoured with a `DeprecationWarning`.
    - Verify: `uv run pytest varco_core/tests/test_authorization_env_config.py varco_core/tests/test_jwt_transform_config.py varco_core/tests/test_trusted_issuer_registry.py`

### Phase 2 — error-code value rename (BREAKING)

12. [ ] `varco_core/tests/test_error_codes.py` — **new, failing first.** Asserts:
    each member's `.code` equals `"VARCO_001"`/`002`/`003`/`004`/`500` with the expected
    `http_status`; member *names* are unchanged (`VarcoErrorCodes["NOT_FOUND"]` resolves);
    `LEGACY_ERROR_CODE_MAP` has exactly 5 entries and its values are exactly the set of
    live `.code` values (guards against a future member being added without a map entry
    or vice versa); `error_message_for(ServiceNotFoundError("x")).code == "VARCO_001"`;
    `error_message_for(RuntimeError("x")).code == "VARCO_500"` (unregistered → fallback).
13. [ ] `varco_core/varco_core/exception/codes.py:141-175` — change the five `code=`
    string literals. Member names untouched.
14. [ ] `varco_core/varco_core/exception/codes.py` — add
    `LEGACY_ERROR_CODE_MAP: Mapping[str, str]` (`MappingProxyType`) with a `DESIGN:`
    block per D-5 and a migration snippet in its docstring; add it to `__all__`.
15. [ ] `varco_core/varco_core/exception/codes.py:9-25, 117-135, 183` — docstrings:
    naming convention becomes ``VARCO_0xx``/``VARCO_5xx``; all inline examples updated.
16. [ ] `varco_core/varco_core/exception/http.py:98, 120, 214, 242` — docstring examples
    and the JSON sample use `"VARCO_001"`.
17. [ ] `varco_core/varco_core/__init__.py` — export `LEGACY_ERROR_CODE_MAP`.
    - Verify: `uv run pytest varco_core/tests/test_error_codes.py && uv run pytest varco_fastapi/tests/`
      (the fastapi middleware tests exercise the error body end-to-end and must stay green —
      they assert on `INTERNAL_SERVER_ERROR`/`QUERY_ERROR`/`GATEWAY_TIMEOUT`, none of which change).

### Phase 3 — class renames + alias shims

18. [ ] `varco_core/tests/test_deprecated_aliases.py` — **new, failing first.**
    `from varco_core import VarcoErrorCodes` works with no warning;
    `pytest.warns(DeprecationWarning)` around `getattr(varco_core, "FastrestErrorCodes")`
    and around `from varco_core.exception.codes import FastrestErrorCodes`; asserts
    `FastrestErrorCodes is VarcoErrorCodes`; asserts `"FastrestErrorCodes" not in varco_core.__all__`;
    asserts `getattr(varco_core, "NoSuchName")` raises `AttributeError`.
19. [ ] `varco_core/varco_core/exception/codes.py:103-106, 199-202` — rename the class to
    `VarcoErrorCodes`; `__all__` lists `ErrorCode`, `VarcoErrorCodes`,
    `LEGACY_ERROR_CODE_MAP`; add the module `__getattr__` built by
    `make_deprecated_alias_getattr` plus the `if TYPE_CHECKING: FastrestErrorCodes = VarcoErrorCodes`
    declaration.
20. [ ] `varco_core/varco_core/exception/http.py:12-13, 22, 41, 53, 69, 81-82, 142-149, 169, 176, 196, 239, 280` —
    swap every `FastrestErrorCodes` reference (including the `AnyErrorCode` alias and
    `_EXCEPTION_CODE_MAP`) to `VarcoErrorCodes`.
21. [ ] `varco_core/varco_core/__init__.py:239, 467` — import/export `VarcoErrorCodes`;
    add a package-level alias `__getattr__` for `FastrestErrorCodes`; **remove** the old
    name from `__all__`.
22. [ ] `varco_sa/tests/test_deprecated_aliases.py` — **new, failing first.** Same shape:
    `SAApp` imports clean; `SAFastrestApp` warns from both `varco_sa` and
    `varco_sa.bootstrap`; `SAFastrestApp is SAApp`.
23. [ ] `varco_sa/varco_sa/bootstrap.py:181-184, 338-341` — rename to `SAApp`; add the
    alias `__getattr__` + `TYPE_CHECKING` declaration; update the module docstring
    (`:11, 20, 30, 36, 42, 45`), the class docstring example (`:210`), and the inline
    comment at `:234`.
24. [ ] `varco_sa/varco_sa/__init__.py:43, 101` — eager-import `SAApp` only; add the
    package-level alias `__getattr__`; drop the old name from `__all__`.
25. [ ] `varco_sa/varco_sa/provider.py:127` and `varco_sa/varco_sa/pool_metrics.py:17, 43-44` —
    docstring references → `SAApp`.
26. [ ] `varco_beanie/tests/test_deprecated_aliases.py` — **new, failing first.** Same
    shape for `BeanieApp` / `BeanieFastrestApp`.
27. [ ] `varco_beanie/varco_beanie/bootstrap.py:114-117, 217` — rename to `BeanieApp`;
    alias `__getattr__` + `TYPE_CHECKING` declaration; docstrings at `:11, 19, 27, 33, 146`
    (`:33` reads "parallel structure to SAFastrestApp" → `SAApp`).
28. [ ] `varco_beanie/varco_beanie/__init__.py:46, 75` — as step 24.
    - Verify: `uv run pytest varco_core/tests/ varco_sa/tests/ varco_beanie/tests/`

### Phase 4 — pre-existing test updates

29. [ ] `varco_sa/tests/test_pool_metrics.py:9, 174, 179, 184, 195` — rename the test
    function `test_sa_fastrest_app_pool_metrics_delegate` → `test_sa_app_pool_metrics_delegate`,
    import and instantiate `SAApp`, update the module docstring and section comment.
30. [ ] `varco_core/tests/test_trusted_issuer_registry.py:20, 446, 490` — comments now
    say `VARCO_JWT_AUTHORIZATION__*`; **additionally** confirm the `include_env=False`
    isolation still holds against *both* prefixes, and add one regression test that sets
    a legacy var and asserts `include_env=False` ignores it (this is the isolation
    property the comments claim but never assert).
    - Verify: `uv run pytest varco_core/tests/ varco_sa/tests/ varco_beanie/tests/ varco_fastapi/tests/ varco_redis/tests/ varco_kafka/tests/ varco_casbin/tests/`

### Phase 5 — documentation sweep

31. [ ] `README.md:798-816` — error-code table + code block: `VarcoErrorCodes`,
    `"VARCO_001"`… ; `:831, 845` — the `{"code": "FASTREST_001"}` JSON sample and the
    translator-call example; add a short "Migrating from `FASTREST_*` codes" note
    pointing at `LEGACY_ERROR_CODE_MAP`.
32. [ ] `README.md:1468, 1473, 1479` (`SAApp`) and `:1572, 1576` (`BeanieApp`).
33. [ ] `CLAUDE.md` — **fill the documented gap**: add two rows to the `VARCO_JWT_*`
    env-var reference table (currently lines ~347-357):
    `VARCO_JWT_AUTHORIZATION__<LABEL>__URL` (trusted-issuer key source descriptor) and
    `VARCO_JWT_AUTHORIZATION__<LABEL>__ISS` (expected `iss` claim), each with a
    "legacy `FASTREST_AUTHORIZATION__*` still honoured, deprecated" footnote. Update the
    JWT scenario examples (~317-319, ~780-786, ~807-809) if they mention the old prefix.
34. [ ] `CLAUDE.md` — add three rows to the **Common Pitfalls** table: (a) "Issuers not
    loaded / `DeprecationWarning` at startup" → legacy env prefix → rename;
    (b) "Client sees `VARCO_001` where it expected `FASTREST_001`" → error-code rename →
    update catalogs via `LEGACY_ERROR_CODE_MAP`; (c) "`ImportError` on `from varco_sa import *`"
    → deprecated names are no longer in `__all__` → import by name.
35. [ ] `ARCHITECTURE.md:60, 570, 825` — `SAApp.pool_metrics()`.
36. [ ] `technical_docs/features/jwt-claim-transformer.md:188` — the per-issuer `__ISS`
    fallback documentation → `VARCO_JWT_AUTHORIZATION__<LABEL>__ISS`, with the legacy
    note.
37. [ ] `varco_sa/README.md:33, 48, 61, 67, 73` and `varco_beanie/README.md:33, 46, 56, 60`
    and `varco_core/README.md:61`.
38. [ ] `CHANGELOG.md` — under `## [Unreleased]` (line 181) add a **`### Changed`** and a
    prominent **`### BREAKING`** entry (content = the BREAKING CHANGES section below,
    verbatim table included).
    - Verify: `rg -i 'fastrest' --glob '!plans/**' --glob '!.claude/**' --glob '!CHANGELOG.md' /home/edoardo/projects/varco`
      → the **only** surviving hits must be the intentional shims: `_LEGACY_ENV_PREFIX`
      and its docstrings, the three alias mappings, `LEGACY_ERROR_CODE_MAP`, and the
      deprecation tests. Enumerate them in the PR description.

### Phase 6 — versions and final gate

39. [ ] `varco_core/pyproject.toml:3` — `1.1.1` → `2.0.0` (see Version bumps).
40. [ ] `varco_sa/pyproject.toml:3` — `2.1.0` → `2.2.0`.
41. [ ] `varco_beanie/pyproject.toml:3` — `1.1.0` → `1.2.0`.
42. [ ] `varco_fastapi/pyproject.toml:3` — `1.1.4` → `1.1.5` (docstring-only change at
    `exceptions.py:113`; bump only if that file is touched).
    - Verify: `uv sync && uv run pytest varco_core/tests/ varco_sa/tests/ varco_beanie/tests/ varco_fastapi/tests/ varco_redis/tests/ varco_kafka/tests/ varco_casbin/tests/ varco_ws/tests/ varco_memcached/tests/ varco_nats/tests/`

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| Only `VARCO_JWT_AUTHORIZATION__G__URL` set | Parsed; `iss` inferred as `"g"`; **no** warning |
| Only `FASTREST_AUTHORIZATION__G__URL` set | Parsed identically; `DeprecationWarning` + one `logger.warning` |
| Both prefixes, same label+field, different values | Canonical value used; warning still emitted for the ignored legacy var |
| `VARCO_JWT_AUTHORIZATION__G__URL` + `FASTREST_AUTHORIZATION__G__ISS` | Merged into one `IssuerConfig` (URL canonical, ISS legacy); warning emitted |
| Legacy var with `__ISS` but no `__URL` | Still silently skipped (pre-existing behaviour, unchanged) |
| `FASTREST_AUTHORIZATION__ORPHAN` (no second `__`) | Still silently ignored |
| `from_container(include_env=False)` with legacy vars set | Vars ignored entirely; **no** warning (no scan happens) |
| Same process calls `from_env()` twice with legacy vars | `DeprecationWarning` twice (Python's own dedup applies); `logger.warning` once |
| `from varco_sa import SAApp` | No warning |
| `from varco_sa import SAFastrestApp` | Works, `DeprecationWarning`, returns the same object as `SAApp` |
| `from varco_sa import *` | `SAApp` exported; `SAFastrestApp` **not** exported (accepted break) |
| `isinstance(SAApp(cfg), SAFastrestApp)` | `True` — same class object |
| `getattr(varco_core, "TypoName")` | `AttributeError`, standard message, no warning |
| Unregistered exception type → `error_message_for()` | `"VARCO_500"` |
| Downstream i18n catalog keyed on `FASTREST_001` | **Breaks** — returns the default English message; fix via `LEGACY_ERROR_CODE_MAP` |

---

## Verification

```bash
cd /home/edoardo/projects/varco

# per-phase (see each phase's Verify line), then the full gate:
uv sync
uv run pytest varco_core/tests/ varco_sa/tests/ varco_beanie/tests/ \
              varco_fastapi/tests/ varco_redis/tests/ varco_kafka/tests/ \
              varco_casbin/tests/ varco_ws/tests/ varco_memcached/tests/ varco_nats/tests/

# deprecation shims behave under strict warnings
uv run pytest varco_core/tests/test_deprecated_aliases.py \
              varco_core/tests/test_authorization_env_config.py \
              varco_sa/tests/test_deprecated_aliases.py \
              varco_beanie/tests/test_deprecated_aliases.py -W error::DeprecationWarning
#   ^ expected to FAIL loudly if any varco *internal* code still touches a deprecated
#     name; the tests themselves must use pytest.warns(), which is -W error-safe.

# clean-import smoke test: importing any package must emit zero warnings
uv run python -W error::DeprecationWarning -c \
  "import varco_core, varco_sa, varco_beanie, varco_fastapi; print('clean')"

# residual-reference gate (phase 5)
rg -i 'fastrest' --glob '!plans/**' --glob '!.claude/**' --glob '!CHANGELOG.md' .
```

There is no lint or type-check command configured in this repo; the `-W error` import
smoke test above is the substitute gate for "no internal code uses a deprecated name".

---

## BREAKING CHANGES

**`varco-core` 2.0.0 — error-code strings changed.**
The `code` field of every error response body emitted by `varco_fastapi`'s error
middleware and `ServiceException` handlers changes:

```diff
- {"code": "FASTREST_001", "message": "The requested resource was not found."}
+ {"code": "VARCO_001",    "message": "The requested resource was not found."}
```

Impact:
- **API clients** switching on `body["code"]` must be updated.
- **i18n / translation catalogs** keyed on the old strings will silently fall back to
  the default English message until re-keyed.
- **Log-based alerting rules** grepping `FASTREST_5` must be updated.

Not affected: HTTP status codes, `message` text, `correlation_id`, the enum *member*
names (`VarcoErrorCodes.NOT_FOUND` etc.), and any application-defined custom
`ErrorCode`s.

**Minor break:** `from varco_sa import *` / `from varco_beanie import *` /
`from varco_core import *` no longer export the deprecated class names (D-3).

### Migration guide

**Environment variables** (old still works, warns; removed next major):

| Old | New |
|---|---|
| `FASTREST_AUTHORIZATION__<LABEL>__URL` | `VARCO_JWT_AUTHORIZATION__<LABEL>__URL` |
| `FASTREST_AUTHORIZATION__<LABEL>__ISS` | `VARCO_JWT_AUTHORIZATION__<LABEL>__ISS` |

**Classes** (old still importable, warns; removed next major):

| Old | New | Module |
|---|---|---|
| `SAFastrestApp` | `SAApp` | `varco_sa` |
| `BeanieFastrestApp` | `BeanieApp` | `varco_beanie` |
| `FastrestErrorCodes` | `VarcoErrorCodes` | `varco_core` |

**Error codes** (hard rename, no runtime fallback):

| Old | New | HTTP | Member |
|---|---|---|---|
| `FASTREST_001` | `VARCO_001` | 404 | `NOT_FOUND` |
| `FASTREST_002` | `VARCO_002` | 403 | `UNAUTHORIZED` |
| `FASTREST_003` | `VARCO_003` | 409 | `CONFLICT` |
| `FASTREST_004` | `VARCO_004` | 422 | `VALIDATION_ERROR` |
| `FASTREST_500` | `VARCO_500` | 500 | `INTERNAL_ERROR` |

Transitional catalog shim for downstream i18n:

```python
from varco_core import LEGACY_ERROR_CODE_MAP

_NEW_TO_OLD = {new: old for old, new in LEGACY_ERROR_CODE_MAP.items()}


def translate(code: str, locale: str) -> str | None:
    """Look up ``code``, falling back to its pre-2.0 spelling."""
    catalog = CATALOGS[locale]
    return catalog.get(code) or catalog.get(_NEW_TO_OLD.get(code, ""))
```

---

## Version bumps

| Package | Current | Proposed | Rationale |
|---|---|---|---|
| `varco-core` | 1.1.1 | **2.0.0** | Error-code string values change the public wire format — MAJOR under semver. The class rename alone would be MINOR (aliased), but the code values are a hard break. |
| `varco-sa` | 2.1.0 | **2.2.0** | `SAApp` added, `SAFastrestApp` still works — MINOR. (The `import *` change is technically breaking but affects a discouraged import style; call it out in the changelog rather than forcing a MAJOR.) |
| `varco-beanie` | 1.1.0 | **1.2.0** | Same as `varco-sa`. |
| `varco-fastapi` | 1.1.4 | **1.1.5** | Docstring-only (`exceptions.py:113`); its emitted body changes only because `varco-core` changed. |
| others | — | unchanged | No `FASTREST` reference in `varco_redis`/`kafka`/`ws`/`nats`/`casbin`/`memcached`. |

⚠️ Sibling packages declare a bare `"varco-core"` dependency with **no version
specifier** (`varco_sa/pyproject.toml:23`, etc.). A `varco-core` 2.0.0 on PyPI will
therefore be picked up automatically by anyone installing `varco-sa`, delivering the
error-code break without an explicit opt-in. Adding `varco-core>=2,<3` pins is out of
scope here, but the reviewer should decide whether to do it in a follow-up.

---

## Risks

- **Worktree divergence.** `.claude/worktrees/feature+examples-catalog/`,
  `.claude/worktrees/qol-release/` and `.claude/worktrees/agent-af91380c/` all contain
  the pre-rename names. Merging any of them after this lands will reintroduce
  `FASTREST_*` identifiers. *Invariant to hold:* rebase every live worktree onto this
  commit before merging, and re-run the phase-5 `rg -i fastrest` gate on the merge
  result.
- **Silent env-var shadowing.** If a deployment sets both prefixes with different
  values, the canonical one wins. *Invariant:* every legacy hit warns — including
  shadowed ones — so the log always names the ignored variable.
- **Deprecation warnings invisible in production.** `DeprecationWarning` is filtered by
  default; this is exactly why the env-var path also logs at WARNING (D-2). If the
  reviewer removes the logging, operators will get no signal at all before the next
  major removes the fallback.
- **`__getattr__` alias hides typos from static analysis.** A typo'd import of a *new*
  name still fails at runtime with `AttributeError`, but IDE autocompletion will not
  offer the old names. *Invariant:* the `if TYPE_CHECKING:` alias declarations must be
  present so type checkers resolve the deprecated names without runtime cost.
- **Error-code rename has no pre-existing test coverage** (verified: no test in the
  workspace asserts the `FASTREST_00x` strings). Phase 2's new test file is the only
  thing standing between a typo and a shipped wrong wire format — write it first.
- **`VARCO_JWT_` prefix collision.** If any pydantic-settings model is later given
  `env_prefix="VARCO_JWT_"` with `extra="forbid"`, the new
  `VARCO_JWT_AUTHORIZATION__*` vars would be rejected as unknown fields. *Invariant:*
  every `VARCO_JWT_*` settings model keeps `extra="ignore"` (see
  `jwt/transform/config.py:101-105` for the precedent and the reason).
