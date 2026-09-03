# Plan 026 — `varco_core.tls`: one TLS model, reloadable, and its first consumers

**Prerequisites: Plan 025 must have landed.** This plan imports `varco_core.watch` and
`varco_core.reload.ReloadableResource` and will not build without them.

Covers BACKLOG 3.1 rows **T3** (🔴 must, L), **T7** (🟢 nice, S) and **T5** (🟡 should, S).

## Goal

One TLS trust model in `varco_core.tls`, a strict superset of the two that exist today, plus a
`ReloadingTrustStore` built on Plan 025's watcher/resource pair. `varco_fastapi.auth.TrustStore`
keeps working unchanged as a deprecated subclass with its 3.0 semantics frozen. The four code
paths that each answer "what is a certificate file in this folder?" differently stop disagreeing
silently. `JwksUrlSource` and `OidcDiscoverySource` can finally be pointed at an internal PKI.

## Non-goals

- **No breaking change, anywhere.** 3.1 is strictly additive (locked, `BACKLOG.md:29`). Every
  existing construction of `SSLConfig` or `varco_fastapi.auth.TrustStore` produces a byte-identical
  `ssl.SSLContext` after this plan. The trust set of a running deployment must not widen by one
  certificate.
- **`SSLConfig` does not gain reload** — see §D-T3-oq2. It stays a frozen pydantic value object.
- **No server-side TLS, no `sni_callback`.** Parked (`BACKLOG.md:79`).
- **No `truststore` dependency.** Cut on evidence (`BACKLOG.md:36`, `:80`): on Linux its verifier
  is a documented no-op because OpenSSL's default paths *are* the Linux system store.
- **No HTTP-client adapters and no PKCS#12** — those are Plan 027 (T4/T6). This plan must not add
  a `key_password` or `pkcs12_file` field; 027 adds them additively.
- **No `create_varco_app(tls=...)` kwarg.** See §D-T3-oq3.
- **Not cross-platform.** Linux only (`BACKLOG.md:35`).

---

## Design

### Phase order

Severity then complexity, with one deviation stated up front: **T7 lands before T3's store**, not
last. T7 produces the single `iter_cert_files()` helper that T3's `TrustStore` and the legacy shim
both call; doing it in its backlog-sorted position (last) would mean writing the glob logic twice.

```
P0  T7   🟢 S  one cert-glob helper + loud-not-silent skip warning   (4 call sites)
P1  T3a  🔴 L  varco_core.tls.TrustStore — the superset value object
P2  T3b  🔴 L  build/mutate/swap + ReloadingTrustStore on 025
P3  T3c  🔴 M  varco_fastapi.auth.TrustStore deprecation subclass + snapshot
P4  T5   🟡 S  ssl_context= on JwksUrlSource / OidcDiscoverySource
P5  ——   🟡 S  docs + CHANGELOG + api-surface regeneration           (same commit)
```

### §D-T7 — one helper, four call sites, **no widening**

Verified disagreement (all four are non-recursive):

| Call site | Anchor | Patterns today |
|---|---|---|
| `SSLConfig.build_ssl_context` | `varco_core/varco_core/connection/ssl.py:261` | `*.pem` + `*.crt` |
| `TrustStore.build_ssl_context` | `varco_fastapi/varco_fastapi/auth/trust_store.py:226` | `*.pem` + `*.crt` |
| `PemFolderSource._has_changes` | `varco_core/varco_core/authority/sources/pem_folder.py:193` | `*.pem` **only** |
| `PemFolderSource._scan` | `varco_core/varco_core/authority/sources/pem_folder.py:230` | `*.pem` **only** |

| ID | Choice | Consequence |
|---|---|---|
| D-T7 | Add `varco_core/tls/discovery.py::iter_cert_files(root, *, patterns, recursive)`. All four sites call it. **Each site keeps its current `patterns` tuple as its default.** A file matching the *wider* known set but not this site's patterns is logged once at WARNING and skipped | The silent-misconfiguration surface becomes a loud one, with zero change to what any deployment trusts |

**DESIGN: warn loudly, do not widen**

✅ The BACKLOG's own complaint is that "a `.cer` file … is ignored **with no error**"
(`BACKLOG.md:69`). The defect named is the *silence*, and the warning fixes the named defect.
✅ Widening `SSLConfig`/`TrustStore` from `{pem,crt}` to `{pem,crt,cer}` would make a previously
inert `.cer` file in a CA directory become trusted on upgrade to 3.1 — the exact failure mode the
locked "Cert search" decision (`BACKLOG.md:30`) forbids for `ca_folder`, applied to patterns
instead of recursion. The decision's *reasoning* generalises even though its letter is about
recursion.
✅ Widening `PemFolderSource` is worse than trust-widening: that folder holds **JWT signing keys**,
one file per `kid` (`pem_folder.py:230-233` uses the filename stem as the kid). A `.crt` there is
an X.509 certificate, not a bare public key; feeding it to `JwkBuilder.from_pem` would at best
mint a surprise `kid` and at worst raise `KeyLoadError` (`pem_folder.py:238-240`) and take issuer
loading down. So the one site where the BACKLOG's "only `*.pem`" observation looks most like a bug
is the site where fixing it by widening is most dangerous.
❌ The three globs still differ after this plan. Accepted and documented: they differ *by design*
now, from one helper, with one constant naming the wider set — instead of by accident, from four
hand-written `glob()` calls.
❌ One WARNING per skipped file could be noisy in a folder of mixed content. Mitigated: the warning
is emitted at most once per `(root, pattern-set)` per process, listing the skipped names together.

`CERT_FILE_PATTERNS = ("*.pem", "*.crt", "*.cer")` is the exported "wider known set" and is the
default for the **new** `varco_core.tls.TrustStore` only (which has no existing deployments to
widen). Opting an existing site in is a one-line, explicit `patterns=CERT_FILE_PATTERNS`.

`iter_cert_files` also gets the `recursive: bool = False` flag and reuses Plan 025's `..`-skipping
and symlink-resolution rules (imported from `varco_core.watch`, not re-implemented) so a
`ca_folder` mounted from a Kubernetes Secret enumerates correctly.

### §D-T3-model — the superset

`varco_core/varco_core/tls/store.py::TrustStore`, `@dataclass(frozen=True)` (matching
`varco_fastapi/varco_fastapi/auth/trust_store.py:39`, **not** pydantic — see the alternative
below).

| Field | Type | Default | Provenance |
|---|---|---|---|
| `ca_cert` | `Path \| bytes \| None` | `None` | union of both: `Path` from `SSLConfig` (`ssl.py:119`), `bytes` from `TrustStore` (`trust_store.py:87`) |
| `ca_folders` | `Path \| Sequence[Path] \| None` | `None` | widened per locked "`ca_folder` multiplicity" (`BACKLOG.md:31`); normalised to a tuple in `__post_init__` |
| `cert_patterns` | `tuple[str, ...]` | `CERT_FILE_PATTERNS` | §D-T7 |
| `recursive` | `bool` | **`True`** | locked "Cert search — recursive by default, **on the new type only**" (`BACKLOG.md:30`) |
| `client_cert` | `Path \| None` | `None` | both |
| `client_key` | `Path \| None` | `None` | both |
| `include_system_cas` | `bool` | `True` | `TrustStore` only (`trust_store.py:91`) |
| `verify` | `bool` | `True` | `SSLConfig` only (`ssl.py:131`) — the escape hatch the bridge drops |
| `check_hostname` | `bool` | `True` | `SSLConfig` only (`ssl.py:134`) |

Validation in `__post_init__` reproduces `SSLConfig._validate_ssl_flags`
(`ssl.py:140-160`) exactly: `check_hostname=True` + `verify=False` → `ValueError`; exactly one of
`client_cert`/`client_key` → `ValueError`. Note this is *stricter than today's* `TrustStore`,
which defers the mTLS pairing check to `build_ssl_context()` (`trust_store.py:237-242`) — the
legacy subclass therefore must not inherit the eager check (§D-T3-oq1, "behaviour frozen").

`build_ssl_context()` ordering is the union of both existing implementations, and the two orderings
already agree on everything they share (`ssl.py:245-276` vs `trust_store.py:213-249`):

```
1. base context:  verify and include_system_cas  → ssl.create_default_context()
                  verify and not include_system_cas → SSLContext(PROTOCOL_TLS_CLIENT),
                                                       check_hostname=True, CERT_REQUIRED
                  not verify                      → SSLContext(PROTOCOL_TLS_CLIENT),
                                                       check_hostname=False, CERT_NONE
2. check_hostname=False with verify=True → ctx.check_hostname = False
3. every ca_folders entry, via iter_cert_files(), sorted → load_verify_locations(cafile=)
4. ca_cert: bytes → load_verify_locations(cadata=...utf-8), Path → cafile=
5. client_cert + client_key → load_cert_chain(...)
```

**System CAs stay additive.** Both existing implementations already do `create_default_context()`
*then* additive `load_verify_locations`, and neither has the
`create_default_context(cafile=...)`-replaces-everything bug — locked decision `BACKLOG.md:32`
("this ask is already satisfied; the work is unification, not correction"). The new
implementation must not "improve" this.

**DESIGN: frozen dataclass, not a pydantic `BaseModel`**

✅ Matches the type it is replacing (`trust_store.py:39`) so the deprecation subclass is a plain
`@dataclass(frozen=True)` subclass with no metaclass conflict.
✅ `bytes` and `Callable` fields (027's `key_password`) are awkward in a pydantic model and trivial
in a dataclass; `TrustStore` is never populated from `env_nested_delimiter`, which is the entire
reason `SSLConfig` is a `BaseModel` (`ssl.py:14-16, 29-37`).
✅ Cheaper to construct and to import — relevant to Plan 028 / P1.
❌ No pydantic validators; `__post_init__` does the work by hand. Accepted: there are exactly two
rules and they already exist as hand-written code in both implementations.
❌ Two different shapes now model TLS config (`SSLConfig` pydantic, `TrustStore` dataclass). That
is deliberate: `SSLConfig` is a *settings fragment*, `TrustStore` is a *runtime capability object*.
Lossless conversion exists in both directions (§D-T3-bridge).

### §D-T3-bridge — the lossy bridge, fixed without breaking it

`varco_fastapi/varco_fastapi/connection.py:310-340` builds a `TrustStore` from an `SSLConfig` and
silently drops `verify=False`, documented as a caveat at `:326-328`.

| ID | Choice | Consequence |
|---|---|---|
| D-T3-bridge | Add `SSLConfig.to_trust_store() -> varco_core.tls.TrustStore` (lossless: carries `verify` **and** `check_hostname`) and `TrustStore.to_ssl_config()`. **Leave `HttpConnectionSettings.to_trust_store()`'s return type exactly as it is** (the `varco_fastapi` legacy type) and add a docstring pointer to the lossless replacement | The lossy path stops being the only path; no existing `isinstance` or annotation breaks |

❌ Changing `HttpConnectionSettings.to_trust_store()` to return the *core* type would break any
caller doing `isinstance(x, varco_fastapi.auth.TrustStore)` on the result — with the subclass shim
(§D-T3-oq1) a core instance is **not** an instance of the legacy subclass. That is a silent
runtime break in an additive release; forbidden.
✅ `SSLConfig.to_trust_store()` lives in `varco_core.connection.ssl` and imports
`varco_core.tls` — same package, no layer violation, no cycle (`varco_core.tls` must not import
`varco_core.connection`; enforced by Step 9's import test).

### §D-T3-reload — mutate vs. swap, chosen per event

| ID | Choice | Consequence |
|---|---|---|
| D-T3-reload | `ReloadStrategy` = `AUTO` (default) / `MUTATE` / `SWAP`. `AUTO` mutates the live context when the diff is **additions only**, and builds a fresh context and swaps the reference when anything was **removed or replaced** | The 6-day-cert renewal common path takes the cheap branch; a revoked CA actually stops being trusted |

**DESIGN: both strategies, selected from the watcher's own diff**

✅ Locked decision (`BACKLOG.md:33`), and the mechanism is exactly what brief 001 §2 documents:
`load_verify_locations`/`load_cert_chain` **can** be called on a live `ssl.SSLContext`, but there
is **no unload API** and "already-established TLS connections see no change — only NEW handshakes
use the updated cert". So mutation adds trust for free and can never remove it.
✅ Plan 025's `WatchEvent.kind` already carries ADDED/MODIFIED/REMOVED per file, so the branch is a
one-line predicate over the batch, not new machinery. This is why T1 emits a *diff* rather than a
bare "something changed" signal.
✅ `SWAP` publishes a new context object and bumps `ReloadableResource.generation`, so pooled
clients can be told to rebuild via `subscribe()`. `MUTATE` leaves the object identity alone, so
every client already holding it picks the rotation up with no coordination at all.
❌ `SWAP` cannot revoke trust for connections already established on the old context (brief 001
§2). varco cannot fix that from a library; documented in the `ReloadingTrustStore` docstring and in
`technical_docs/features/`.
❌ `AUTO` is a heuristic and can be wrong if a CA is replaced by a *file rename* that the diff sees
as ADDED+REMOVED — which lands on the SWAP branch, i.e. it errs toward the safe, expensive branch.
Stated as an Edge case.

`ReloadingTrustStore` composes rather than inherits:

```
ReloadingTrustStore
├── spec: TrustStore                     (frozen; the config)
├── _resource: ReloadableResource[ssl.SSLContext]   (Plan 025 / T2 — keep-last-good)
└── _watcher: AbstractPathWatcher        (Plan 025 / T1 — over spec.ca_folders + cert/key paths)

  .context            -> the current ssl.SSLContext           (never None after start())
  .generation         -> int, bumps on SWAP only
  async start()/stop()
  async reload()      -> ReloadOutcome
  subscribe(cb)       -> unsubscribe
```

Keep-last-good comes for free from T2: a folder caught mid-rotation raises out of the loader and
the live context is untouched (`BACKLOG.md:64`). The first load fails fast.

### §D-T3-oq1 — BACKLOG open question 1: the deprecation shim is a **subclass**

| ID | Choice | Consequence |
|---|---|---|
| D-T3-oq1 | `varco_fastapi.auth.trust_store.TrustStore` becomes `@dataclass(frozen=True) class TrustStore(varco_core.tls.TrustStore)` that **pins 3.0 semantics** (`recursive=False`, `cert_patterns=("*.pem","*.crt")`, no eager mTLS-pairing check) and emits a `DeprecationWarning` in `__post_init__`. Removed in 4.0.0 | `isinstance(legacy_instance, core.TrustStore)` is `True`, so every new API accepts an old object; the 3.0 glob/recursion behaviour is frozen in the subclass, not in the shared implementation |

**DESIGN: subclass over plain re-export alias**

✅ **A plain alias is not available here.** AB-1 (`render_rls_ddl`) and AB-2
(`SchemaMigrationError`) could alias because the old and new names denoted the *same behaviour*.
Here they do not: the new type is recursive by default and globs `*.cer` too, and aliasing would
silently give every existing `varco_fastapi.auth.TrustStore` user recursive, wider cert discovery
on upgrade — precisely what the locked "Cert search" decision (`BACKLOG.md:30`) exists to prevent.
The behavioural delta the BACKLOG's open question anticipated is real, and it decides the question.
✅ `include_system_cas` semantics are preserved *by construction*: the field lives on the base with
the same default and the same meaning.
✅ The api-surface snapshot records a class's **defining module**
(`design/api-freeze-and-standards/measurements/api-surface.md:375` →
`varco_fastapi.auth.trust_store`). A subclass keeps that module unchanged, so the snapshot's
`varco_fastapi` entry does not move at all; only a *new* `varco_core` row is added, which is a
non-failing note. `--check` is a CI gate (`.github/workflows/test.yml:64-65`) and stays green.
✅ Class signatures are deliberately not recorded by `api_surface.py` (CLAUDE.md's stated
limitation), so no gate churn from the widened `ca_folders` annotation either.
❌ **The asymmetry**: `isinstance(core_store, varco_fastapi.auth.TrustStore)` is `False`. A user who
constructs the *new* type and passes it to their own function that `isinstance`-checks the *old*
one will get a surprise. Mitigation: nothing in varco does such a check (verified by grep in Step
14), and the deprecation warning tells the user to stop importing the old name. Documented in the
CHANGELOG entry and the class docstring, not hidden.
❌ Two live classes instead of one until 4.0.0. Accepted — that is what a deprecation window is.

The `DeprecationWarning` is emitted **at construction**, not at import, so merely having
`from varco_fastapi import TrustStore` at the top of a module (there is one, at
`varco_fastapi/varco_fastapi/__init__.py:108`) does not warn. `stacklevel` must point at the
caller's construction site.

### §D-T3-oq2 — BACKLOG open question 2: **`SSLConfig` does not gain reload**

| ID | Choice | Consequence |
|---|---|---|
| D-T3-oq2 | `varco_core.tls.ReloadingTrustStore` is the **only** reloadable path. `SSLConfig` stays a frozen pydantic value object and gains only `recursive`/`cert_patterns` (opt-in, defaults preserve today's behaviour) and the lossless `to_trust_store()` | One object owns lifecycle; the settings fragment stays a settings fragment |

**DESIGN: one reloadable type**

✅ `SSLConfig` is embedded as a nested field inside every `ConnectionSettings` subclass
(`ssl.py:8-16`) and is constructed by pydantic-settings, potentially many times, at import/DI
time. Making it reloadable would make every settings object a background-task owner with a
`start()`/`stop()` contract nobody calls — a lifecycle leak by design, and exactly the class of
defect Plan 024's `@Disposes` work spent a phase closing.
✅ It is frozen (`ssl.py:117`) and documented "Thread safety ✅ Frozen — safe to share across
threads" (`:82`). Reload means mutable state and a lazily-created `asyncio.Lock`; that contradicts
the type's advertised contract.
✅ Broker backends that hold an `SSLConfig` are not left out: `cfg.to_trust_store()` →
`ReloadingTrustStore(spec)` is two lines, and the reload story is then identical everywhere.
❌ A user with `SSLConfig` in their settings must convert to get reload. Accepted, and it is one
call that is discoverable from the `SSLConfig` docstring.
❌ Two types where one might do. Answered in §D-T3-model's ❌ list: settings fragment vs. runtime
capability object.

### §D-T3-oq3 — BACKLOG open question 3: the task is owned by the store, registered by the app

| ID | Choice | Consequence |
|---|---|---|
| D-T3-oq3 | `ReloadingTrustStore` owns its background task via `start()`/`stop()` (inherited from Plan 025's composition) and is an `async` context manager. **No `@Configuration` is added to `varco_core`.** `varco_core/tls/di.py` exposes only `bind_trust_store(container, store)`. FastAPI apps register it with `lifespan.register(store)`; non-FastAPI consumers use `async with` or `container` + explicit start | Works identically inside and outside a FastAPI lifespan; nothing starts a watcher implicitly |

**DESIGN: no scanned `@Configuration`, a `bind_*` verb instead**

✅ `container.scan("varco_core", recursive=True)` is a **documented, in-use pattern**
(`README.md:2082`; also `varco_fastapi/tests/test_di_binding_health_i18n_tz.py:23`,
`varco_core/tests/test_observability_di.py:244`). A scan **auto-activates** `@Configuration`
classes (CLAUDE.md's `varco_casbin` rule states this explicitly as the reason
`enable_policy_authorizer` is opt-in). A `TlsConfiguration` in `varco_core` would therefore start a
filesystem watcher in every app that scans `varco_core` — the exact opposite of the locked
"Auto-injection: explicit, opt-in, never implicit" decision (`BACKLOG.md:38`), which brief 001 §3
grounds in truststore's own instruction that *libraries must not* inject.
✅ `start`/`stop` structurally satisfy `varco_fastapi.lifespan.AbstractLifecycle`
(`varco_fastapi/varco_fastapi/lifespan.py:73`, `isinstance` at `:178`) — proven by Plan 025's Step
14 test — so `VarcoLifespan.register(store)` needs no adapter class and no import from `varco_core`
to `varco_fastapi`.
✅ `bind_trust_store(container, store)` matches the taxonomy's `bind_*` row exactly ("sync, mutates
container, registers typed bindings unknowable before app startup") and, crucially, has **no
lifecycle side effect** — the container never starts anything the caller did not start.
❌ The user must remember to register/start the store. Accepted: that is what "explicit" means, and
an unstarted store raises `ResourceNotLoadedError` (Plan 025 / T2) on first `.context` access
rather than silently serving nothing.
❌ No `create_varco_app(tls=...)` convenience this cycle. Deliberate: `tenancy=`/`reliability=`
earned their kwargs by owning multi-component wiring; a single lifecycle object does not, and
adding a kwarg is harder to remove than to add later.

### §D-T3-env — `SSL_CERT_FILE` / `SSL_CERT_DIR` are **additive** in varco

| ID | Choice | Consequence |
|---|---|---|
| D-T3-env | `TrustStore.from_env()` reads the existing `VARCO_*` names (`trust_store.py:96-124`) **and** `SSL_CERT_FILE` → `ca_cert`, `SSL_CERT_DIR` → an entry in `ca_folders`, **additively on top of system CAs**, diverging from OpenSSL's replace-semantics. The divergence is stated in the docstring, the README and the feature doc | varco stays additive everywhere; nobody loses the system store by exporting one env var |

✅ Brief 001 §3: when `SSL_CERT_FILE`/`SSL_CERT_DIR` are set non-empty they **override the default
entirely** — only the specified certs are trusted. That is a footgun in a framework whose every
other CA mechanism is additive (`BACKLOG.md:32`), and silently dropping the system store because a
sidecar exported a variable is a production outage shape.
❌ varco therefore does **not** match OpenSSL/`uv`/`requests` semantics for these two names. This
is a real interoperability wart and must be documented at every mention, never assumed. A future
`strict_env=True` flag can offer the OpenSSL behaviour if anyone asks; not built on speculation.

### §D-T5 — an SSL context for the two URL-based issuer sources

Both fetch with a bare `urllib.request.urlopen(self._url, timeout=...)` and **no `context=`**:
`varco_core/varco_core/authority/sources/jwks_url.py:199` and
`varco_core/varco_core/authority/sources/oidc.py:189`.

| ID | Choice | Consequence |
|---|---|---|
| D-T5 | Add `ssl_context: ssl.SSLContext \| None = None` to both sources' `__init__` (keyword-only), pass it as `urlopen(..., context=...)`, thread it through `IssuerSourceFactory.from_string(..., ssl_context=)` and `AuthorizationConfig.to_registry(ssl_context=)`. `TrustedIssuerRegistry.from_env()` builds one **only if at least one CA env var is set**, otherwise `None` | Default behaviour is byte-identical (`urlopen` with `context=None` is what it does today); an internal PKI or an intercepting corporate proxy becomes configurable without process-wide env vars |

`JwksUrlSource` declares `__slots__` (`jwks_url.py:86-94`) — `"_ssl_context"` must be added or the
assignment raises `AttributeError`. Check `oidc.py` for the same and mirror it.

The env-var trigger set is exactly the ones `TrustStore.from_env()` already reads
(`VARCO_TRUST_STORE_DIR`, `VARCO_CA_CERT`, `VARCO_CLIENT_CERT`, `VARCO_CLIENT_KEY`) plus §D-T3-env's
two. "None of them set" → `None` → today's behaviour, exactly.

### Alternatives considered

- **Move `TrustStore` to `varco_core.tls` and leave a plain alias behind** — ❌ rejected, §D-T3-oq1:
  it silently flips recursion and glob width for existing users.
- **Keep two types and just fix the bridge** — ❌ rejected. It leaves TLS trust living in
  `varco_fastapi.auth`, where a broker backend can never reach it (T3's own rationale,
  `BACKLOG.md:65`), and leaves `include_system_cas` unreachable from `SSLConfig` forever.
- **Make `varco_core.tls.TrustStore` a pydantic `BaseModel` so it can be a settings fragment too**
  — ❌ rejected, §D-T3-model: it breaks the dataclass-subclass shim and buys nothing that
  `SSLConfig` does not already provide.
- **A `TlsConfiguration` `@Configuration` in `varco_core`** — ❌ rejected, §D-T3-oq3: a documented
  recursive scan would auto-start a watcher.
- **Widen all globs to `{pem,crt,cer}` for consistency** — ❌ rejected, §D-T7: widens trust and can
  break issuer-key loading.
- **Reload `SSLConfig` too** — ❌ rejected, §D-T3-oq2.

---

## Steps

### Phase 0 — T7: one cert-glob helper (🟢 nice, S — but first, see Phase order)

1. [ ] `varco_core/tests/test_tls_discovery.py` (new, **failing first**) — `iter_cert_files`:
       pattern filtering; deterministic sort; `recursive=True` finding a cert one level down and
       `recursive=False` not; `..data` symlink layouts enumerating the resolved files once;
       a `.cer` present with `patterns=("*.pem","*.crt")` producing **exactly one** WARNING
       (via `caplog`) and **not** being returned; the warning firing at most once per
       `(root, patterns)` per process; a non-existent root returning empty without raising.
2. [ ] `varco_core/varco_core/tls/discovery.py` (new) — `CERT_FILE_PATTERNS`,
       `iter_cert_files(root, *, patterns, recursive=False)`. Reuses Plan 025's `..`-skip and
       symlink-resolution helpers from `varco_core.watch` (import them; do not copy).
3. [ ] `varco_core/varco_core/connection/ssl.py` — replace the inline glob at `:259-262` with
       `iter_cert_files(folder, patterns=self.cert_patterns, recursive=self.recursive)`; add the
       two **new opt-in fields** `cert_patterns: tuple[str, ...] = ("*.pem", "*.crt")` and
       `recursive: bool = False` with docstrings stating that the defaults preserve 3.0 behaviour
       and why the new type differs (`BACKLOG.md:30`). Update the `build_ssl_context` docstring's
       "Steps" list, which currently hard-codes the glob at `:226`.
4. [ ] `varco_core/varco_core/authority/sources/pem_folder.py` — both `:193` and `:230` call
       `iter_cert_files(self._path, patterns=self._patterns, recursive=False)`; add a keyword-only
       `patterns: tuple[str, ...] = ("*.pem",)` constructor parameter (`__slots__` update if the
       class declares one). The two sites must use the **same** enumeration or `_has_changes` and
       `_scan` can disagree — call that out in a comment.
5. [ ] `varco_core/tests/test_pem_folder_source.py` (extend) — a `.crt` and a `.cer` in the folder
       are still ignored (trust/keyset unchanged) **and** produce the WARNING; passing
       `patterns=CERT_FILE_PATTERNS` explicitly opts in.

### Phase 1 — T3a: the superset value object (🔴 must, L)

6. [ ] `varco_core/tests/test_tls_store.py` (new, **failing first**) — the §D-T3-model field table
       as executable assertions: defaults; `recursive` defaults to `True` **on this type**;
       `ca_folders` accepts one `Path` or a sequence and normalises to a tuple; the two
       `__post_init__` `ValueError`s; `bytes` `ca_cert` loading via `cadata`; `include_system_cas=False`
       producing `CERT_REQUIRED` + `check_hostname=True`; `verify=False` producing `CERT_NONE` +
       `check_hostname=False`; **and a differential test**: for a matrix of configs expressible in
       both old models, `TrustStore(...).build_ssl_context()` and the legacy
       `SSLConfig(...)`/`varco_fastapi` `TrustStore(...)` produce contexts with equal
       `get_ca_certs()`, `verify_mode` and `check_hostname`. That differential test is the
       no-regression proof for the whole phase.
7. [ ] `varco_core/varco_core/tls/store.py` (new) — `TrustStore` per §D-T3-model, with the
       `DESIGN:` block, full docstrings (Args/Returns/Raises/Edge cases/Thread safety), and
       `from_env()` per §D-T3-env.
8. [ ] `varco_core/varco_core/tls/__init__.py` (new) — export `TrustStore`, `CERT_FILE_PATTERNS`,
       `iter_cert_files`, `ReloadStrategy`, `ReloadingTrustStore` (Phase 2), `bind_trust_store`
       (Phase 2). `__all__`.
9. [ ] `varco_core/tests/test_tls_layering.py` (new) — assert `varco_core.tls` imports **no**
       `varco_core.connection`, no `varco_fastapi`, no backend package (walk
       `sys.modules` after a fresh subprocess import, or inspect the module's AST). This is the
       mechanical guard for CLAUDE.md's layer rule and for §D-T3-bridge's no-cycle claim.
10. [ ] `varco_core/varco_core/connection/ssl.py` — add `to_trust_store()` (lossless, carries
        `verify` + `check_hostname`), and `TrustStore.to_ssl_config()` in `tls/store.py` (documents
        the two lossy directions that remain: `bytes` `ca_cert` and `include_system_cas=False`,
        exactly as `trust_store.py:139-147` already documents them).

### Phase 2 — T3b: reload (🔴 must, L)

11. [ ] `varco_core/tests/test_tls_reloading_store.py` (new, **failing first**) — `start()` loads
        and `.context` is usable; adding a CA file to a watched folder is picked up **without**
        the context object identity changing (MUTATE branch) and the new CA appears in
        `get_ca_certs()`; removing a file **does** change identity and bumps `generation` (SWAP
        branch); an explicit `ReloadStrategy.SWAP` always swaps; a mid-rotation unreadable file
        leaves the previous context in place and logs ERROR (keep-last-good); `subscribe()` fires
        once per successful swap; `stop()` is idempotent; `async with` works. Certs are generated
        in-test with `cryptography` (already a hard dependency, `varco_core/pyproject.toml:33-34`)
        — a session fixture minting a CA and two leaf certs.
12. [ ] `varco_core/varco_core/tls/reload.py` (new) — `ReloadStrategy` enum, `ReloadingTrustStore`
        per §D-T3-reload, composing `ReloadableResource[ssl.SSLContext]` and an
        `AbstractPathWatcher` over `spec.ca_folders` + the parent dirs of `ca_cert`/`client_cert`/
        `client_key`. Watcher injectable (`watcher=None` → `default_watcher(...)`) so tests can
        drive a fast one. Docstring must state the brief 001 §2 fact that established connections
        keep the old context either way.
13. [ ] `varco_core/varco_core/tls/di.py` (new) — `bind_trust_store(container, store)` registering
        both the concrete `ReloadingTrustStore` and the `TrustStore` spec, using
        `container.provide(Provider(singleton=...)(factory), returns=...)` per CLAUDE.md's
        `returns=` rule. Module docstring states loudly: **no `@Configuration` here, and why**
        (§D-T3-oq3, `README.md:2082`).
14. [ ] `varco_core/tests/test_tls_di.py` (new) — `bind_trust_store` resolves; a
        `container.scan("varco_core", recursive=True)` **starts no watcher and registers no TLS
        binding** (the anti-implicit assertion — this is the test that keeps §D-T3-oq3 true over
        time); `assert_no_structural_di_issues()` clean.

### Phase 3 — T3c: the deprecation shim (🔴 must, M)

15. [ ] `rg -n "isinstance\(.*TrustStore" .` — confirm no in-repo `isinstance` check on the legacy
        type exists (the ❌ in §D-T3-oq1 depends on it). Record the result in the commit message.
16. [ ] `varco_fastapi/tests/test_trust_store_deprecation.py` (new, **failing first**) —
        constructing `varco_fastapi.auth.TrustStore` emits exactly one `DeprecationWarning` naming
        `varco_core.tls.TrustStore`; **importing** `varco_fastapi` emits none; the legacy type is a
        subclass of the core type; `recursive` is `False` and `cert_patterns` is `("*.pem","*.crt")`
        on the legacy type and `True`/`CERT_FILE_PATTERNS` on the core type; a legacy instance's
        `build_ssl_context()` is byte-equivalent (same `get_ca_certs()`/`verify_mode`/
        `check_hostname`) to what 3.0 produced for a fixture folder containing `ca.pem`, `ca.crt`,
        `ca.cer` and `sub/deep.pem`; partial mTLS config still raises at `build_ssl_context()`
        time, **not** at construction (behaviour frozen).
17. [ ] `varco_fastapi/varco_fastapi/auth/trust_store.py` — replace the implementation with the
        subclass per §D-T3-oq1. Keep `from_env`, `to_ssl_config`, `system` working. Module
        docstring rewritten to a deprecation notice with the migration line and the 4.0.0 removal
        date; the class docstring keeps its existing Examples (they still run).
18. [ ] `uv run python scripts/api_surface.py` — regenerate both snapshot files and **commit them
        in this commit** (CI gate, `.github/workflows/test.yml:64-65`). Expected delta: new
        `varco_core` rows (`TrustStore`, `ReloadingTrustStore`, `ReloadStrategy`,
        `CERT_FILE_PATTERNS`, `iter_cert_files`, `bind_trust_store`) as non-failing *additions*;
        the `varco_fastapi` `TrustStore` row's `module` column unchanged (§D-T3-oq1). **If that
        column moved, the shim was implemented as an alias — stop and fix it.**

### Phase 4 — T5: SSL context for the issuer sources (🟡 should, S)

19. [ ] `varco_core/tests/test_issuer_source_ssl.py` (new, **failing first**) — spin a loopback
        HTTPS server (`http.server` + `ssl.SSLContext.wrap_socket` in a thread) with a
        self-signed CA minted by the Phase 2 fixture; assert `JwksUrlSource(url)` **fails** with
        `KeyLoadError` (untrusted), and `JwksUrlSource(url, ssl_context=store.build_ssl_context())`
        succeeds. Same for `OidcDiscoverySource`. Bind to `127.0.0.1:0`.
20. [ ] `varco_core/varco_core/authority/sources/jwks_url.py` — keyword-only `ssl_context`, added
        to `__slots__` (`:86-94`), passed at `:199`. Docstring: Args entry + an Edge case noting
        `None` means stdlib default, unchanged.
21. [ ] `varco_core/varco_core/authority/sources/oidc.py` — same treatment at `:189`; check and
        update its `__slots__` if present. If OIDC discovery makes a *second* request for the
        `jwks_uri`, the context must be threaded to that call too **and** to the `JwksUrlSource` it
        constructs — grep the module before editing.
22. [ ] `varco_core/varco_core/authority/sources/factory.py` — `from_string(..., ssl_context=None)`,
        forwarded to the two URL-based branches only; docstring's parameter table updated (it
        currently says `algorithm`/`use` are ignored for URL sources — add the mirror sentence).
23. [ ] `varco_core/varco_core/authority/config.py` — `to_registry(ssl_context=None)` forwarding at
        `:213`; `TrustedIssuerRegistry.from_env()` (`registry.py:758-778`) builds a context from
        `TrustStore.from_env()` **only when at least one CA env var is set**, else `None`.
24. [ ] `varco_core/tests/test_issuer_source_ssl.py` (extend) — with no CA env vars set,
        `from_env()` produces sources whose `ssl_context is None` (the byte-identical-default
        proof); with `VARCO_CA_CERT` set, it is not `None`.

### Phase 5 — docs, changelog (🟡 should, S — same commit as the code)

25. [ ] `technical_docs/features/tls-trust-and-hot-reload.md` (new) — the design narrative: the
        two-model history and why they merged; the §D-T3-model field table; mutate-vs-swap with the
        brief 001 §2 citation and the "established connections keep the old context" caveat; the
        `SSL_CERT_FILE`/`SSL_CERT_DIR` **additive divergence** (§D-T3-env) called out as a
        compatibility note; a **Pitfalls** table (per CLAUDE.md's feature-doc rule) with at least:
        the additive-env divergence, "SWAP forces pooled clients to rebuild", "MUTATE cannot
        revoke", "recursive is `True` only on the new type", and "a `.cer` in a `ca_folder` is
        warned about, not loaded".
26. [ ] `README.md` — "TLS trust store" section: `TrustStore`, `from_env()`, `ReloadingTrustStore`
        + `lifespan.register(...)`, the `bind_trust_store` DI line, the `SSLConfig.to_trust_store()`
        bridge, and the T5 `ssl_context=` example. Env-var reference table for the `VARCO_*` names
        plus the two OpenSSL names with their additive caveat.
27. [ ] `ARCHITECTURE.md` — a "TLS trust" type hierarchy (`TrustStore` → legacy subclass;
        `ReloadingTrustStore` composing `ReloadableResource`/`AbstractPathWatcher`) and the new
        `varco_core.tls` module listing.
28. [ ] `CLAUDE.md` — a short Key-Abstractions subsection: **Rule** — TLS trust lives in
        `varco_core.tls`; `varco_fastapi` may import it but never the reverse; **Rule** — never add
        a scanned `@Configuration` to `varco_core.tls` (§D-T3-oq3, with the `README.md:2082`
        reason); one Decision-Tree row (*TLS/CA/mTLS config? → `varco_core.tls`; a settings-embedded
        fragment? → `varco_core.connection.SSLConfig`, which converts*). Link the new feature doc.
29. [ ] `CHANGELOG.md` `## [Unreleased]` — `### Added` (`varco_core.tls`, `ReloadingTrustStore`,
        `SSLConfig.to_trust_store`, `SSLConfig.recursive`/`cert_patterns`, `ssl_context=` on the two
        issuer sources — "Plan 026 / T3, T5, T7"); `### Deprecated`
        (`varco_fastapi.auth.TrustStore` → `varco_core.tls.TrustStore`, removed in 4.0.0, **with
        the isinstance-asymmetry note from §D-T3-oq1**); `### Changed` (a non-matching cert file in
        a `ca_folder` now logs a WARNING instead of being silently skipped — "Plan 026 / T7").
30. [ ] `BACKLOG.md` — replace open questions 1-3 (`BACKLOG.md:87-96`) with their answers, pointing
        at §D-T3-oq1/oq2/oq3 in this file, in the same "answered, not deleted" style Plan 024 used.

---

## Edge cases

- **`ca_folders=[]`** (empty sequence) → treated as `None`; no folder loading, no warning.
- **The same certificate present in two folders** → `load_verify_locations` is idempotent for an
  identical cert; both are loaded, OpenSSL de-duplicates. Assert `get_ca_certs()` length in a test.
- **A `ca_folder` containing a `.pem` that is not a certificate** → `load_verify_locations` raises
  `ssl.SSLError`. At `build_ssl_context()` time that propagates (unchanged from today). Inside
  `ReloadingTrustStore` it is a failed reload → keep-last-good + ERROR log.
- **A CA file renamed in place (same bytes, new name)** → ADDED + REMOVED in one batch → SWAP.
  Correct-but-expensive; noted in §D-T3-reload's ❌.
- **`include_system_cas=False` with no CA at all** → an empty trust store, every connection fails.
  Preserved from `trust_store.py:61-63`, which already documents it.
- **`verify=False` + `include_system_cas=True`** → `verify=False` wins; the base context is
  `CERT_NONE` and `include_system_cas` is irrelevant. Documented, and asserted in Step 6.
- **`ssl_context=` passed to `JwksUrlSource` for an `http://` URL** → ignored by `urlopen`, no
  error. Documented as an Edge case, not enforced.
- **`SSL_CERT_DIR` pointing at a c_rehash-style directory of symlinks** → `iter_cert_files`
  resolves symlinks and de-duplicates by resolved path, so hash-symlinks do not double-load.

## Verification

```bash
uv sync --all-packages --all-extras
uv run pytest varco_core/tests/test_tls_discovery.py varco_core/tests/test_tls_store.py \
              varco_core/tests/test_tls_reloading_store.py varco_core/tests/test_tls_di.py \
              varco_core/tests/test_tls_layering.py varco_core/tests/test_issuer_source_ssl.py \
              varco_core/tests/test_pem_folder_source.py -q
uv run pytest varco_fastapi/tests/test_trust_store_deprecation.py -q
uv run pytest varco_core/tests/ varco_fastapi/tests/      # regression sweep
uv run python scripts/api_surface.py --check              # MUST be clean before committing
make lint && make type-check && make test
```

**DoD:** the Step 6 differential test proves no context differs from 3.0 for any config
expressible in the old models; `api_surface.py --check` green with the regenerated snapshot
committed; the `varco_fastapi` `TrustStore` snapshot row's `module` column unchanged.

## Risks

- **Silently widening what a deployment trusts.** This is *the* risk of the plan. Invariant that
  must hold: **for any configuration expressible in 3.0, the produced `ssl.SSLContext` has an
  identical `get_ca_certs()`, `verify_mode` and `check_hostname`.** Step 6's differential test and
  Step 16's frozen-semantics test are the two guards; neither may be weakened to make a refactor
  pass.
- **The deprecation subclass's `isinstance` asymmetry** (§D-T3-oq1 ❌). Guarded by Step 15's grep
  for in-repo checks; out-of-repo consumers are covered by the CHANGELOG note. If Step 15 finds a
  check, this decision must be revisited before proceeding, not patched around.
- **⚠️ ASSUMPTION — `ssl.SSLContext.get_ca_certs()` is a sufficient equality oracle.** The
  differential test compares loaded CA sets via `get_ca_certs()`; it does not compare cipher
  suites, ALPN or options, which none of the code paths touch. Not brief-grounded; it is a test-
  design judgement. If a future field touches those, the oracle must grow.
- **⚠️ ASSUMPTION — the mutate branch is observable.** §D-T3-reload asserts a MUTATE reload leaves
  the context object identity unchanged while `get_ca_certs()` grows. Brief 001 §2 documents that
  `load_verify_locations` may be called on a live context, but not that the addition is visible via
  `get_ca_certs()` on the same object. Step 11 must **verify this empirically first**; if it is
  not observable, the assertion becomes "a new handshake trusts the new CA" (an actual loopback
  handshake), not an introspection check. Do not delete the test — change the oracle.
- **`urlopen(context=)` and proxies.** T5 fixes verification, not proxy handling; a corporate proxy
  configured via `HTTP_PROXY` still applies. Out of scope, and stated in the README so nobody
  reads T5 as "JWKS through a proxy now works".
- **`__slots__` omissions** (`jwks_url.py:86-94`). A missed `__slots__` entry fails loudly at
  assignment, so this is a build-time risk only — but check `oidc.py` explicitly (Step 21), which
  the scout report did not cover.
