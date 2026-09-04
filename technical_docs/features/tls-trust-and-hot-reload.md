# TLS trust and hot reload — `varco_core.tls`

Plan 026 (BACKLOG 3.1, rows **T3** must/L, **T7** nice/S, **T5** should/S). Closes: "there are
two overlapping TLS trust models in different layers, neither is a superset of the other, and
neither can be reloaded without a process restart."

## The two-model history, and why they merged

Before this plan, "what TLS should I trust?" had two independent answers:

- `varco_core.connection.ssl.SSLConfig` — a pydantic `BaseModel`, embedded as a nested field in
  every `ConnectionSettings` subclass, loaded from env vars via `env_nested_delimiter`. Had the
  `verify=False` escape hatch and `check_hostname`, but no `include_system_cas` toggle and no
  `bytes`-CA support.
- `varco_fastapi.auth.trust_store.TrustStore` — a frozen dataclass, a runtime capability object
  used by the auth layer. Had `include_system_cas` and in-memory `bytes` CAs, but always
  verified (no `verify=False`) and lived in `varco_fastapi`, unreachable from a broker backend
  (`varco_kafka`/`varco_redis`/`varco_sa`) that has no `varco_fastapi` dependency.

The one bridge between them, `HttpConnectionSettings.to_trust_store()`
(`varco_fastapi/varco_fastapi/connection.py:310-340`), was one-directional and **silently
dropped `verify=False`** — a documented caveat, never fixed, because fixing it required a type
that could carry every field either predecessor had.

`varco_core.tls.TrustStore` is that type: a strict superset (§D-T3-model in
`plans/026-tls-unification-and-reloading-truststore.md`), living in `varco_core` so any backend
can reach it, with a lossless bridge in both directions:

```python
from varco_core.connection.ssl import SSLConfig
from varco_core.tls import TrustStore

# SSLConfig -> TrustStore, carrying BOTH verify and check_hostname (lossless)
store = SSLConfig(verify=False, check_hostname=False).to_trust_store()

# TrustStore -> SSLConfig (lossy in two directions — see the docstring: bytes ca_cert and
# include_system_cas=False are not representable in SSLConfig)
cfg = store.to_ssl_config()
```

`varco_fastapi.auth.trust_store.TrustStore` still exists — as a deprecated subclass of
`varco_core.tls.TrustStore` that pins its exact 3.0 semantics (non-recursive scan,
`("*.pem", "*.crt")` patterns, deferred mTLS-pairing check). See "The deprecation shim" below.

## The field table (§D-T3-model)

`varco_core.tls.TrustStore` (`varco_core/varco_core/tls/store.py`), `@dataclass(frozen=True)`:

| Field | Type | Default | Notes |
|---|---|---|---|
| `ca_cert` | `Path \| bytes \| None` | `None` | `bytes` loads via `cadata=`; `Path` via `cafile=` |
| `ca_folders` | `Path \| Sequence[Path] \| None` | `None` | one path or many; normalised to a tuple (or `None`) in `__post_init__` |
| `cert_patterns` | `tuple[str, ...]` | `CERT_FILE_PATTERNS` (`("*.pem", "*.crt", "*.cer")`) | see "The cert-glob helper" below |
| `recursive` | `bool` | **`True`** | recursive folder scan — new default, **on this type only** |
| `client_cert` / `client_key` | `Path \| None` | `None` | mTLS client identity; both or neither |
| `include_system_cas` | `bool` | `True` | whether to layer on top of `ssl.create_default_context()` |
| `verify` | `bool` | `True` | `False` → `CERT_NONE`, the escape hatch `SSLConfig` had and the old `TrustStore` did not |
| `check_hostname` | `bool` | `True` | requires `verify=True` |

Validation is **eager**, at construction — `check_hostname=True` with `verify=False` raises
`ValueError`, and exactly one of `client_cert`/`client_key` set raises `ValueError`. This is
*stricter* than the pre-3.1 `varco_fastapi.auth.TrustStore`, which deferred the mTLS check to
`build_ssl_context()` — the deprecation subclass keeps deferring it (frozen behaviour).

```python
from pathlib import Path
from varco_core.tls import TrustStore

# Private CA + mTLS, recursive folder scan (this type's default)
store = TrustStore(
    ca_folders=Path("/etc/ssl/private-ca"),
    client_cert=Path("/etc/ssl/client.crt"),
    client_key=Path("/etc/ssl/client.key"),
)
ctx = store.build_ssl_context()
```

## The cert-glob helper (§D-T7)

Before this plan, four call sites each answered "what is a certificate file in this folder?"
differently and silently: `SSLConfig`/the old `TrustStore` globbed `*.pem` + `*.crt`;
`PemFolderSource` globbed `*.pem` only. A `.cer` file dropped into a CA folder was silently
ignored — no error, no log.

`varco_core.tls.discovery.iter_cert_files(root, *, patterns, recursive=False)` is now the one
implementation all four sites call, and it fixes the **named** defect (silence) without
widening what any of the three pre-existing sites trusts or loads: each site keeps its own
`patterns` default (`SSLConfig` and the legacy `varco_fastapi` shim: `("*.pem", "*.crt")`;
`PemFolderSource`: `("*.pem",)`). A file matching the wider `CERT_FILE_PATTERNS` set but not a
given site's own patterns is skipped **and logged once at WARNING** per `(root, patterns)` per
process, naming the skipped file(s).

`varco_core.tls.TrustStore` is the one type that defaults to the wider `CERT_FILE_PATTERNS` —
safe because it has no existing deployments to widen. Opt an existing `SSLConfig` into the
wider set explicitly:

```python
from varco_core.connection.ssl import SSLConfig
from varco_core.tls import CERT_FILE_PATTERNS

cfg = SSLConfig(ca_folder=Path("/etc/ssl/ca"), cert_patterns=CERT_FILE_PATTERNS, recursive=True)
```

`PemFolderSource` was **not** widened by default for a sharper reason than "consistency": that
folder holds JWT *signing keys*, one file per `kid`. Feeding it an X.509 certificate would at
best mint a surprise `kid`, at worst raise `KeyLoadError` and take issuer loading down — so the
one call site where the narrow default looks most like a bug is the one where "fixing" it by
widening is most dangerous.

## Reload — mutate vs. swap, chosen per event (§D-T3-reload)

`ReloadingTrustStore` (`varco_core/varco_core/tls/reload.py`) composes Plan 025's
`ReloadableResource[ssl.SSLContext]` (keep-last-good swap semantics) and `AbstractPathWatcher`
(filesystem change detection) — it does not inherit from either.

```python
from pathlib import Path
from varco_core.tls import TrustStore, ReloadingTrustStore

spec = TrustStore(ca_folders=Path("/etc/ssl/private-ca"))
store = ReloadingTrustStore(spec)

async with store:
    ctx = store.context  # ssl.SSLContext, never None after start()
```

`ReloadStrategy` (`AUTO` default / `MUTATE` / `SWAP`) picks the branch from the watcher's own
per-file diff:

- **A settled batch is additions only** → `MUTATE`: `load_verify_locations()` is called on the
  *live* `ssl.SSLContext` object. `ssl.SSLContext` has **no unload API** — brief
  001 §2 documents that "already-established TLS connections see no change — only NEW
  handshakes use the updated cert" — so mutation can only ever *add* trust, never revoke it.
  Cheap: no rebuild, no swap, every client already holding a reference to `store.context` picks
  up the new CA with zero coordination. This is the 6-day-cert-renewal common path.
- **Anything was removed or replaced** → `SWAP`: a fresh context is built from scratch and the
  reference is swapped, `generation` bumps by one, and `subscribe()` fires so pooled clients can
  rebuild.

⚠️ **Established connections keep whatever context they negotiated with either way** — `SWAP`
cannot revoke trust for a connection already open on the *old* context object. This is a
consequence of how TLS handshakes work, not something varco can fix from a library.

```python
def rebuild_pool(new_ctx: ssl.SSLContext) -> None:
    ...  # tell your connection pool to use new_ctx for new connections


unsubscribe = store.subscribe(rebuild_pool)  # fires only on SWAP, never on MUTATE
```

`AUTO` is a heuristic: a CA replaced by a file *rename* (same bytes, new name) is seen by the
watcher's diff as ADDED+REMOVED in one batch, which lands on the (safe, expensive) `SWAP`
branch rather than the cheap one it might logically deserve. Force a strategy explicitly if you
need one behaviour unconditionally: `ReloadingTrustStore(spec, strategy=ReloadStrategy.SWAP)`.

## `SSLConfig` does not gain reload (§D-T3-oq2)

`SSLConfig` stays a frozen pydantic settings fragment — it does not grow `start()`/`stop()`.
`SSLConfig` is constructed by pydantic-settings, potentially many times, at import/DI time;
making every settings object a background-task owner would be a lifecycle leak by design.
`ReloadingTrustStore` is the **only** reloadable path. A broker backend holding an `SSLConfig`
converts: `ReloadingTrustStore(cfg.to_trust_store())`.

## Owning the reload task (§D-T3-oq3)

`ReloadingTrustStore` owns its own background task via `start()`/`stop()` and is an `async`
context manager — there is **no scanned `@Configuration` in `varco_core`**. A
`container.scan("varco_core", recursive=True)` (a documented, in-use pattern — see the DI
section below) auto-activates every scanned `@Configuration`, so one in `varco_core.tls` would
start a filesystem watcher in *every* app that scans `varco_core` — the opposite of the locked
"Auto-injection: explicit, opt-in, never implicit" decision.

`start()`/`stop()` structurally satisfy `varco_fastapi.lifespan.AbstractLifecycle` (a
`runtime_checkable` Protocol), so a FastAPI app registers an already-constructed, already-owned
store with zero import from `varco_core` into `varco_fastapi`:

```python
from varco_fastapi.lifespan import VarcoLifespan
from varco_core.tls import TrustStore, ReloadingTrustStore

store = ReloadingTrustStore(TrustStore(ca_folders=Path("/etc/ssl/private-ca")))

lifespan = VarcoLifespan()
lifespan.register(store)  # starts/stops the watcher for you
```

Non-FastAPI consumers use `async with store:` or call `start()`/`stop()` directly. An unstarted
store raises `ResourceNotLoadedError` on `.context` access rather than silently serving nothing.

### DI wiring

`varco_core.tls.bind_trust_store(container, store)` is the only DI-wiring surface this package
exposes — it registers an **already-constructed** `ReloadingTrustStore` (and its `TrustStore`
spec) as DI singletons. It has **no lifecycle side effect** — the caller still owns
`start()`/`stop()`:

```python
from varco_core.tls import TrustStore, ReloadingTrustStore, bind_trust_store

spec = TrustStore(ca_folders=Path("/etc/ssl/private-ca"))
store = ReloadingTrustStore(spec)
bind_trust_store(container, store)
await store.start()  # the caller's responsibility, not bind_trust_store's
```

## `SSL_CERT_FILE` / `SSL_CERT_DIR` — additive, not OpenSSL-compatible (§D-T3-env)

`TrustStore.from_env()` reads the existing `VARCO_*` names **and** the two OpenSSL-standard
names, **additively on top of system CAs**:

⚠️ **This diverges from OpenSSL/`uv`/`requests` semantics on purpose.** OpenSSL, `uv`, and
`requests` treat a non-empty `SSL_CERT_FILE`/`SSL_CERT_DIR` as *replacing* the default trust
store entirely — only the specified certs are trusted. varco does not: both are merged
additively, alongside `include_system_cas`'s default of `True`, because silently dropping the
system store just because a sidecar exported one env var is a production-outage shape in a
framework whose every other CA mechanism is additive. If you need the OpenSSL replace-semantics,
set `include_system_cas=False` explicitly.

## An SSL context for the two URL-based issuer sources (§D-T5)

`JwksUrlSource` and `OidcDiscoverySource` used to fetch with a bare
`urllib.request.urlopen(url, timeout=...)` and no `context=` — unverifiable against an internal
PKI or an intercepting corporate proxy without process-wide env vars. Both now take a
keyword-only `ssl_context: ssl.SSLContext | None = None`:

```python
from varco_core.authority.sources import JwksUrlSource, OidcDiscoverySource
from varco_core.tls import TrustStore

ctx = TrustStore(ca_folders=Path("/etc/ssl/internal-pki")).build_ssl_context()

jwks = JwksUrlSource("https://idp.internal/.well-known/jwks.json", ssl_context=ctx)
oidc = OidcDiscoverySource("https://idp.internal", ssl_context=ctx)
```

`None` (the default) means "stdlib default context" — byte-identical to pre-Plan-026 behaviour.
`IssuerSourceFactory.from_string(..., ssl_context=)` and `AuthorizationConfig.to_registry(
ssl_context=)` forward it; `OidcDiscoverySource` threads the *same* context to both its own
discovery-document fetch and the delegate `JwksUrlSource` it constructs, because an internal PKI
issuer needs the same trust for its `.well-known` document and its JWKS endpoint.

`TrustedIssuerRegistry.from_env()` builds a context from `TrustStore.from_env()` **only when at
least one of the CA-triggering env vars is set** (`VARCO_TRUST_STORE_DIR`, `VARCO_CA_CERT`,
`VARCO_CLIENT_CERT`, `VARCO_CLIENT_KEY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`) — with none of them
set, `ssl_context` stays `None`, so `from_env()`'s default behaviour is unchanged.

⚠️ **Out of scope: this fixes verification, not proxy handling.** `ssl_context=` has no bearing
on `HTTP_PROXY`/`HTTPS_PROXY` — a corporate proxy configured that way still applies exactly as
before. Do not read this as "JWKS through a proxy now works."

## The deprecation shim (§D-T3-oq1)

`varco_fastapi.auth.trust_store.TrustStore` is now
`@dataclass(frozen=True) class TrustStore(varco_core.tls.TrustStore)` — a subclass, not a plain
re-export alias, because the old and new names do **not** denote the same behaviour: the base
type is recursive by default and globs a wider cert set. Aliasing would silently give every
existing `varco_fastapi.auth.TrustStore` construction recursive, wider cert discovery on
upgrade to 3.1 — exactly what the "Cert search" locked decision exists to prevent. The subclass
instead pins the exact 3.0 defaults (`recursive=False`, `cert_patterns=("*.pem", "*.crt")`, and
a deferred — not eager — mTLS-pairing check) and emits a `DeprecationWarning` at construction
(not at import — `from varco_fastapi import TrustStore` alone does not warn).

```python
import warnings
from varco_fastapi.auth import TrustStore  # no warning yet

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    ts = TrustStore(ca_cert=Path("/etc/ssl/ca.pem"))  # DeprecationWarning fires here
```

`isinstance(legacy_instance, varco_core.tls.TrustStore)` is `True` — every new API accepts an
old object. The reverse is not: `isinstance(varco_core.tls.TrustStore(), varco_fastapi.auth.
TrustStore)` is `False`. This asymmetry is inherent to a subclass shim, not a bug — see the
Pitfalls table below. `HttpConnectionSettings.to_trust_store()` (`varco_fastapi.connection`)
deliberately keeps returning the legacy type — see `SSLConfig.to_trust_store()` above for the
lossless replacement.

Removed in 4.0.0.

## Pitfalls

| Pitfall | Symptom | Root Cause | Fix |
|---|---|---|---|
| **`SSL_CERT_FILE`/`SSL_CERT_DIR` don't behave like OpenSSL** | A sidecar exports `SSL_CERT_DIR` expecting it to *replace* the trust store, but the system CAs are still trusted too | varco's `TrustStore.from_env()` is deliberately additive, diverging from OpenSSL/`uv`/`requests` replace-semantics (§D-T3-env) | If replace-semantics are actually wanted, set `include_system_cas=False` explicitly — do not rely on the env var alone |
| **A pooled HTTP/broker client never picks up a rotated CA** | `ReloadingTrustStore.generation` bumps and `subscribe()` fires, but an existing connection pool keeps using the old context | `SWAP` publishes a *new* `ssl.SSLContext` object — pooled clients that cached the old object by reference are never told unless they subscribe | Register a `store.subscribe(...)` callback that tells the pool to rebuild for new connections; existing established connections keep the old context regardless (brief 001 §2 — no library can fix this) |
| **A revoked CA is still trusted after a config change** | A CA file is removed from the watched folder, but existing connections (and, briefly, in-flight requests on the old context) still trust it | `MUTATE` can only ever *add* trust — `ssl.SSLContext` has no unload API. The watcher's diff correctly routes a removal through `SWAP`, but `SWAP` still can't revoke *already-established* connections | This is inherent to how `ssl.SSLContext` works, not a bug — plan for it: bound-lifetime connections/short pool max-age reduce the exposure window |
| **A private-CA `TrustStore()` is non-recursive when you expected the default** | `store = varco_fastapi.auth.TrustStore(...)` doesn't pick up a cert in a subdirectory of `ca_folder` | You're on the **legacy** shim, which pins `recursive=False` — only `varco_core.tls.TrustStore` defaults to `recursive=True` | Migrate to `varco_core.tls.TrustStore`, or pass `recursive=True` explicitly if staying on the legacy type during the deprecation window |
| **A `.cer` file in a `ca_folder` silently isn't trusted** | A cert file is present but never appears in `ctx.get_ca_certs()` | The call site's `cert_patterns` default doesn't include `*.cer` (`SSLConfig`/the legacy shim keep 3.0's `("*.pem", "*.crt")`) — this is now *warned about*, not silent, but still not loaded by default | Opt in explicitly with `cert_patterns=varco_core.tls.CERT_FILE_PATTERNS`, or use `varco_core.tls.TrustStore` (which defaults to the wider set) |
