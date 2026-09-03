# Plan 027 — HTTP-client injection adapters + mTLS hardening

**Prerequisites: Plans 025 and 026 must have landed.** This plan adds methods to
`varco_core.tls.TrustStore` and to `ReloadingTrustStore`; neither exists before 026.

Covers BACKLOG 3.1 rows **T4** (🟡 should, M) and **T6** (🟡 should, S–M).

## Goal

A `TrustStore` (or a live `ReloadingTrustStore`) can be handed to any of the four mainstream Python
HTTP clients in one call, with **no new hard dependency** on any of them; and varco's mTLS support
stops being "unencrypted PEM key files only" — encrypted private keys and PKCS#12/`.pfx` bundles
both work, using `cryptography`, which is already a hard `varco_core` dependency
(`varco_core/pyproject.toml:33-34`).

## Non-goals

- **No implicit injection, ever.** varco never calls `install_process_trust()` on the user's
  behalf, never touches `ssl` module globals at import time, and never sets an env var. Locked
  (`BACKLOG.md:38`), grounded in brief 001 §3: truststore's own documentation instructs that
  *libraries must not* inject; they construct a context and pass it.
- **No `truststore` dependency** (`BACKLOG.md:36`, `:80`).
- **No hard dependency on httpx, aiohttp, urllib3 or requests.** Locked (`BACKLOG.md:37`):
  function-body imports, precedent `varco_fastapi/varco_fastapi/connection.py:333`
  (`# noqa: PLC0415`).
- **No changes to `varco_fastapi.client`.** `AsyncVarcoClient`/`SyncVarcoClient` already accept a
  `TrustStore` through `ClientProfile`; wiring the new adapters into them is not in this cycle.
- **No PKCS#12 *writing*, no key generation, no CSR handling.** Read-side only.
- **No server-side mTLS (client-cert verification by a varco server).** Outbound-only cycle
  (`BACKLOG.md:28`).

---

## Design

### Phase order

```
P0  T6a  🟡 S    encrypted private keys — key_password on TrustStore
P1  T6b  🟡 M    PKCS#12 / .pfx ingestion via cryptography
P2  T4a  🟡 S    test-only client deps in the root dev group   (unblocks P3)
P3  T4b  🟡 M    to_httpx_verify / to_aiohttp_connector / to_urllib3_poolmanager / to_requests_adapter
P4  T4c  🟡 S    install_process_trust() — the acknowledged, app-level global
P5  ——   🟡 S    docs + CHANGELOG + api-surface regeneration   (same commit)
```

T6 sorts before T4 because the adapters' tests are strictly better when they can exercise a real
mTLS handshake with an encrypted key, and because T6 changes `TrustStore`'s field set — doing it
after T4 would mean re-touching four adapters.

### §D-T6-password — encrypted private keys

Both existing implementations call `load_cert_chain(certfile=, keyfile=)` with **no `password=`**
(`varco_core/varco_core/connection/ssl.py:271-274`,
`varco_fastapi/varco_fastapi/auth/trust_store.py:244-247`), so an encrypted key
(`-----BEGIN ENCRYPTED PRIVATE KEY-----`) makes `build_ssl_context()` raise, or worse, prompt on a
TTY.

| ID | Choice | Consequence |
|---|---|---|
| D-T6-password | Add `key_password: str \| bytes \| Callable[[], str \| bytes] \| None = None` to `varco_core.tls.TrustStore`, `field(repr=False)`, passed straight through to `load_cert_chain(..., password=)` | stdlib does the decryption; varco never sees or stores plaintext key material |

**DESIGN: pass the stdlib's own password callback through, do not decrypt ourselves**

✅ Brief 001 §5: `ssl.SSLContext.load_cert_chain(certfile, keyfile, password)` accepts a password
**or a callable returning one**, natively, for all clients. There is nothing to implement.
✅ Accepting a `Callable` means the secret can come from Vault/KMS lazily, at handshake-build time,
and need never live in a config object at all.
✅ `repr=False` plus an explicit `__repr__` test keeps the secret out of logs, tracebacks and the
`DEBUG`-level DI dumps. A frozen dataclass's default `repr` would otherwise print it.
❌ A `str`/`bytes` password *is* held in memory for the lifetime of the store. Unavoidable for the
non-callable form; documented, with the callable form recommended in the docstring.
❌ `SSLConfig` does **not** get this field. Adding it would mean a secret in a pydantic settings
model populated from env vars — the field would land in `model_dump()`, in DI logging and in any
settings echo. `SSLConfig.to_trust_store()` (Plan 026 / §D-T3-bridge) is the path: convert, then
set the password in code. Stated in `SSLConfig`'s docstring so the absence reads as a decision.

If `password` is set but no `client_key`, `__post_init__` raises `ValueError` — a password with
nothing to decrypt is always a misconfiguration.

### §D-T6-pkcs12 — PKCS#12 with zero new dependencies

| ID | Choice | Consequence |
|---|---|---|
| D-T6-pkcs12 | `pkcs12_file: Path \| None` + `pkcs12_password: str \| bytes \| Callable \| None` (also `repr=False`). Decoded with `cryptography.hazmat.primitives.serialization.pkcs12.load_key_and_certificates`, re-serialised to PEM **in memory**, written to a private temp file with mode `0600`, loaded via `load_cert_chain`, then unlinked in a `finally` | Closes the gap brief 001 §5 names as *the* standard one, which normally forces `httpx-pkcs12` / `requests-pkcs12`, with no dependency varco does not already ship |

**DESIGN: temp file, because stdlib `ssl` has no in-memory client-cert API**

✅ `cryptography>=50.0.0` is already a hard dependency (`varco_core/pyproject.toml:33-34`), used by
`varco_core.jwk`. This is genuinely free.
✅ Brief 001 §5 states plainly that PKCS#12 "is not natively supported" by stdlib `ssl` and that
the ecosystem answer is third-party shims — so a first-party implementation is the differentiator
the BACKLOG claims (`BACKLOG.md:68`).
✅ Any intermediate CAs in the bundle are written into the same PEM chain file, which is what
`load_cert_chain` expects; the CA certs in the bundle are additionally offered as trust anchors
**only if** `pkcs12_trust_ca=True` (default `False` — a client bundle's CAs are not automatically
trust anchors, and silently making them so would widen trust).
❌ **The private key touches the filesystem.** `SSLContext.load_cert_chain` takes filenames only;
there is no `load_cert_chain_from_memory`. Mitigations, all mandatory:
`tempfile.mkdtemp()` (mode `0700` by default) inside `/dev/shm` when it exists and is writable —
legitimate here because this cycle is **Linux-only** (`BACKLOG.md:35`) — else the system temp dir;
file created with `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)`; unlinked and the directory
removed in a `finally`, including on exception; never logged. The window is documented in the
feature doc's Pitfalls table, not buried.
❌ `requests-pkcs12` advertises "no temp files" (brief 001 §5) — it achieves that with a custom
`HTTPAdapter`, i.e. by not using `load_cert_chain` at all, which is not available to a
client-agnostic `ssl.SSLContext` builder. Rejected as out of shape for this layer.

`pkcs12_file` and `client_cert`/`client_key` are **mutually exclusive** → `ValueError` in
`__post_init__` (same shape as the existing pairing check, `ssl.py:154-159`).

### §D-T4-adapters — four thin conversions, four function-body imports

| Method | Target API | Version evidence (brief 001 §4) |
|---|---|---|
| `to_httpx_verify()` → `ssl.SSLContext` | `httpx.Client(verify=ctx)` / `AsyncClient(verify=ctx)` | httpx 0.28+: `verify` accepts an `SSLContext`; `cert=` is **deprecated** in 0.28 in favour of building the context yourself |
| `to_aiohttp_connector(**kw)` → `aiohttp.TCPConnector` | `ClientSession(connector=...)` | aiohttp 3.14.3: `TCPConnector(ssl=ctx)`; mutually exclusive with `verify_ssl`/`fingerprint` |
| `to_urllib3_poolmanager(**kw)` → `urllib3.PoolManager` | `PoolManager(ssl_context=ctx)` | urllib3 v2.x (v1.26 EOL'd) |
| `to_requests_adapter()` → `requests.adapters.HTTPAdapter` subclass | `session.mount("https://", adapter)` | requests 2.32.3+ fixed the custom-`SSLContext`-in-`HTTPAdapter`-subclass bug |

All four live in `varco_core/varco_core/tls/clients.py` as **module-level functions** taking a
`TrustStore | ReloadingTrustStore` first argument, and are additionally exposed as methods on
`TrustStore` for discoverability (the methods delegate).

**DESIGN: function-body imports, no `to_*_client()` factories**

✅ Locked (`BACKLOG.md:37`). An adapter for an uninstalled library raises a clear `ImportError`
**when called**, and costs nothing at import time — which matters directly for Plan 028 / P1.
✅ The error is translated to `MissingClientDependencyError(ImportError)` naming the pip package,
mirroring Plan 025's `MissingWatchDependencyError`. One consistent shape for "you asked for an
optional integration you have not installed".
✅ Returning a *connector/adapter/context*, not a configured `Client`/`Session`, keeps varco out of
the business of every client's timeout/retry/proxy options — the user composes.
❌ `to_requests_adapter()` must define an `HTTPAdapter` subclass (that is the only supported way to
inject a context; brief 001 §4). It is defined **inside the function body**, after the import, so
the module has no import-time dependency on `requests`. Slightly unusual; commented as such.
❌ requests is sync-only and this is an async framework. Included anyway because the BACKLOG names
it and because sync scripts/CLIs around a varco service are exactly the PKCS#12/private-PKI
audience.

**Reload interaction.** All four accept a `ReloadingTrustStore` and read `.context` **at call
time**. Consequences, documented in each docstring and in the feature doc's Pitfalls table:

- Under `ReloadStrategy.MUTATE` (Plan 026 / §D-T3-reload) the client keeps working across a
  rotation with no action — the object it holds is the object that was mutated.
- Under `SWAP`, an already-constructed client holds the **old** context. The store's
  `subscribe(cb)` is the hook: rebuild the client/connector in the callback. varco does **not**
  rebuild clients on the user's behalf — it does not own them, and closing a pooled client under
  in-flight requests is not a library's decision.
- Brief 001 §2's underlying constraint applies regardless: established TLS connections never see a
  rotation; only new handshakes do.

### §D-T4-install — `install_process_trust()`, acknowledged and never called by varco

| ID | Choice | Consequence |
|---|---|---|
| D-T4-install | `varco_core.tls.install_process_trust(store, *, acknowledge_global_mutation: bool)` sets `ssl._create_default_https_context` to a factory returning the store's context, returns a `RestoreHandle` that undoes it, and raises `ValueError` unless the kwarg is explicitly `True`. varco itself never calls it | The "automatic injection" half of the ask exists, is documented, is reversible in tests — and is impossible to trigger by accident |

**DESIGN: an explicit, acknowledged, reversible global — or nothing**

✅ Brief 001 §3/§4: `truststore.inject_into_ssl()`-style patching is what makes *all* stdlib-ssl
users (requests, urllib3, httpx, aiohttp) pick up a trust store at once — that is the actual
capability the T4 ask names. The same brief is equally explicit that **libraries must not do it**.
An explicit function the *application* calls is the shape that satisfies both.
✅ The `acknowledge_*` kwarg matches an established varco convention for privileged, hard-to-undo
surfaces (`mount_tenant_admin(..., acknowledge_bundled_admin=True)`,
`mount_reliability_admin(...)`). Consistency here is not decoration — it is what makes the
danger legible to a reviewer.
✅ Returning a `RestoreHandle` (usable as a context manager) makes it testable and makes an
accidental permanent mutation in someone's test suite recoverable.
❌ `ssl._create_default_https_context` is a **private** stdlib name. ⚠️ Marked as an assumption in
Risks; Step 18 asserts the attribute exists on the running interpreter and the function raises a
clear error if it does not, rather than patching something that silently no longer matters.
❌ It only affects contexts created **after** the call, and only those created through
`create_default_context`-style paths (brief 001 §4's caveat). Documented; the docstring tells the
user to call it at the entry point before importing HTTP clients.
❌ Alternative rejected: setting `SSL_CERT_FILE`/`SSL_CERT_DIR` process-wide instead. That has the
replace-not-add semantics varco explicitly rejected in Plan 026 / §D-T3-env, and mutating the
environment of a running process to change library behaviour is strictly more surprising than
patching one documented-by-convention hook.

### Alternatives considered

- **Depend on `httpx-pkcs12` / `requests-pkcs12`** — ❌ rejected. Two dependencies, two client-
  specific code paths, for something `cryptography` (already a hard dep) does in one call. This is
  the differentiator the BACKLOG identified.
- **Ship `to_*_client()` factories returning configured clients** — ❌ rejected: varco would own
  timeouts, retries, proxies and pool sizing for four libraries it does not depend on.
- **Make the adapters accept only `ssl.SSLContext`** — ❌ rejected: it moves the "read `.context`
  at call time" reload subtlety onto every user, which is precisely the trap the Pitfalls table
  exists to prevent.
- **Add httpx/aiohttp/urllib3/requests as optional extras of `varco-core`** — ❌ rejected. Locked
  as function-body imports with no extras (`BACKLOG.md:37`); an extra implies varco has an opinion
  about the version floor, and brief 001 §4's floors are documented in the docstrings instead,
  where they cost nothing.
- **`truststore.inject_into_ssl()`** — ❌ rejected with the dependency (`BACKLOG.md:36`, `:80`).

---

## Steps

### Phase 0 — T6a: encrypted private keys (🟡 should, S)

1. [ ] `varco_core/tests/conftest.py` (extend) or `varco_core/tests/tls_fixtures.py` (new) — a
       session-scoped PKI fixture built with `cryptography`: a self-signed CA, a server leaf
       (SAN `localhost`, `127.0.0.1`), a client leaf, the client key in **both** unencrypted and
       encrypted (`BestAvailableEncryption`) PEM form, and a PKCS#12 bundle of the client identity
       (used in Phase 1). Written under `tmp_path_factory`.
2. [ ] `varco_core/tests/test_tls_mtls.py` (new, **failing first**) — an encrypted client key with
       no `key_password` raises from `build_ssl_context()`; with a `str` password it loads; with a
       `bytes` password it loads; with a `Callable` password it loads and the callable is invoked
       lazily (not at construction); `repr(store)` contains **neither** the password nor the
       string `"password"`'s value; `key_password` without `client_key` raises `ValueError` at
       construction.
3. [ ] `varco_core/varco_core/tls/store.py` — add `key_password` per §D-T6-password
       (`field(default=None, repr=False)`), the `__post_init__` rule, and the `password=`
       argument at the `load_cert_chain` call. Docstring: Args entry + an **Edge cases** note that
       a callable is invoked once per `build_ssl_context()`, and a security note recommending it.
4. [ ] `varco_core/varco_core/connection/ssl.py` — docstring-only change on `SSLConfig`: state
       that key passwords and PKCS#12 are deliberately **not** settings fields (§D-T6-password ❌)
       and point at `to_trust_store()`.

### Phase 1 — T6b: PKCS#12 (🟡 should, M)

5. [ ] `varco_core/tests/test_tls_pkcs12.py` (new, **failing first**) — a password-protected `.p12`
       from the Phase 0 fixture loads and produces a context whose client cert matches the
       standalone-PEM path (compare `ctx.get_ca_certs()` plus a real loopback mTLS handshake
       asserting the server sees the expected client subject); wrong password → a clear varco
       error, not a raw `cryptography` traceback; `pkcs12_file` together with `client_cert` →
       `ValueError`; the temp file and its directory **do not exist** after `build_ssl_context()`
       returns *and* after it raises (assert on the returned path captured via monkeypatch);
       `pkcs12_trust_ca=True` adds the bundle's CA to `get_ca_certs()` and the default `False`
       does not.
6. [ ] `varco_core/varco_core/tls/pkcs12.py` (new) — `load_pkcs12_identity(path, password) ->
       Pkcs12Identity` (frozen dataclass of PEM `bytes`: `key_pem`, `cert_pem`,
       `ca_pems: tuple[bytes, ...]`), and a `materialize_chain()` context manager implementing the
       `/dev/shm`-preferred, `0600`, unlink-in-`finally` discipline from §D-T6-pkcs12. Errors
       wrapped in `Pkcs12LoadError(ValueError)`.
7. [ ] `varco_core/varco_core/tls/store.py` — `pkcs12_file`, `pkcs12_password` (`repr=False`),
       `pkcs12_trust_ca: bool = False`; mutual-exclusion rule; `build_ssl_context()` step 5 branches
       to the PKCS#12 path. The `DESIGN:` block from §D-T6-pkcs12 goes in `pkcs12.py`'s module
       docstring, including the ❌ temp-file window.
8. [ ] `varco_core/tests/test_tls_pkcs12.py` (extend) — `@pytest.mark.integration` end-to-end: a
       loopback TLS server with `verify_mode=CERT_REQUIRED` and the CA loaded, an httpx client
       built from `to_httpx_verify()` (after Phase 3) — or, if Phase 3 has not landed yet in the
       implementer's ordering, a raw `ssl` client socket — completing a mutual handshake with the
       PKCS#12 identity.

### Phase 2 — T4a: test-only client dependencies (🟡 should, S)

9. [ ] `pyproject.toml` (root) `[dependency-groups]` — add a `clients` group with
       `aiohttp>=3.14`, `urllib3>=2`, `requests>=2.32.3` and `httpx>=0.28`, and
       `{ include-group = "clients" }` in `dev`. Comment: **test-only**; these must never appear in
       any `varco_*/pyproject.toml` `[project].dependencies` or `[project.optional-dependencies]`
       (locked, `BACKLOG.md:37`). Floors are brief 001 §4's (requests 2.32.3 is the release that
       fixed custom-`SSLContext` `HTTPAdapter` subclasses; urllib3 v2 because v1.26 is EOL). Then
       `uv lock` + `uv sync --all-packages --all-extras`.
10. [ ] `varco_core/tests/test_tls_no_hard_client_deps.py` (new) — a **structural** test: parse
        `varco_core/varco_core/tls/clients.py` with `ast` and assert every `httpx`/`aiohttp`/
        `urllib3`/`requests` import is inside a function body, and that a fresh subprocess
        `import varco_core.tls` leaves all four out of `sys.modules`. This is the guard that keeps
        the locked decision true after a future refactor.

### Phase 3 — T4b: the four adapters (🟡 should, M)

11. [ ] `varco_core/tests/test_tls_clients.py` (new, **failing first**) — for each of the four:
        the returned object carries the store's context (identity check where the library exposes
        it: `TCPConnector._ssl`, `PoolManager.connection_pool_kw["ssl_context"]`, the adapter's
        stored context, and httpx's returned context itself); a `ReloadingTrustStore` argument
        reads `.context` at call time (assert by swapping the store's context between two calls);
        a missing library raises `MissingClientDependencyError` naming the pip package (simulate
        by monkeypatching `builtins.__import__`).
12. [ ] `varco_core/varco_core/tls/clients.py` (new) — the four functions per §D-T4-adapters, each
        with a full docstring carrying the brief 001 §4 version note (and, for httpx, the
        `cert=`-deprecated-in-0.28 note so nobody re-adds it), Args/Returns/Raises/Edge cases, and
        the reload caveat. `MissingClientDependencyError(ImportError)`.
13. [ ] `varco_core/varco_core/tls/store.py` — thin delegating methods `to_httpx_verify()`,
        `to_aiohttp_connector()`, `to_urllib3_poolmanager()`, `to_requests_adapter()`.
14. [ ] `varco_core/varco_core/tls/reload.py` — the same four delegating methods on
        `ReloadingTrustStore`, each reading `self.context` at call time.
15. [ ] `varco_core/tests/test_tls_clients_integration.py` (new,
        `pytestmark = pytest.mark.integration`) — a loopback TLS server (self-signed CA from the
        Phase 0 fixture) fetched successfully through **all four** clients via the adapters, and
        failing without them. Marked `integration` because it binds a port and does real
        handshakes; it needs no Docker, which the module docstring must say so nobody adds a
        container fixture.
16. [ ] `varco_core/tests/test_tls_clients_integration.py` (extend) — a rotation test: an httpx
        client built from a `ReloadingTrustStore`, a CA **added** to the watched folder (MUTATE
        branch, Plan 026 / §D-T3-reload), and a second request to a server using the new CA
        succeeding **without** rebuilding the client. This is the end-to-end proof of the whole
        cycle's premise; if it fails, the MUTATE branch is not delivering what §D-T3-reload claims
        and that must be reported, not worked around.

### Phase 4 — T4c: `install_process_trust()` (🟡 should, S)

17. [ ] `varco_core/tests/test_tls_install.py` (new, **failing first**) — without
        `acknowledge_global_mutation=True` → `ValueError` and **no** mutation; with it,
        `ssl.create_default_context()`-consuming code sees the store's context, and the returned
        `RestoreHandle` (also usable as a context manager) restores the original exactly; calling
        it twice nests correctly; varco itself never calls it (assert by grep in Step 20).
18. [ ] `varco_core/varco_core/tls/install.py` (new) — `install_process_trust(store, *,
        acknowledge_global_mutation)` + `RestoreHandle`. It must first assert
        `hasattr(ssl, "_create_default_https_context")` and raise a clear, actionable
        `RuntimeError` naming the Python version if not (§D-T4-install ❌ / Risks). Module docstring
        carries the brief 001 §3 "libraries must not inject" citation as the reason for the
        acknowledgement kwarg.
19. [ ] `varco_core/varco_core/tls/__init__.py` — export `install_process_trust`, `RestoreHandle`,
        `MissingClientDependencyError`, `Pkcs12LoadError`, the four adapter functions. Update
        `__all__`.
20. [ ] `rg -n "install_process_trust" varco_*/varco_*` — assert the only hits are the definition
        and the export. Record in the commit message; this is the mechanical form of "varco never
        calls it".

### Phase 5 — docs, changelog, snapshot (🟡 should, S — same commit as the code)

21. [ ] `uv run python scripts/api_surface.py` — regenerate and commit both snapshot files. All
        deltas here are **additions** (non-failing notes), but the gate compares the committed file
        (`.github/workflows/test.yml:64-65`), so it must be regenerated.
22. [ ] `technical_docs/features/tls-trust-and-hot-reload.md` (extend, created by Plan 026) — new
        sections: client injection (the four-row API table with versions), encrypted keys, PKCS#12
        (including the temp-file window and the `/dev/shm` preference), and
        `install_process_trust`. Extend the **Pitfalls** table with: `SWAP` leaves an existing
        client on the old context; established connections never see a rotation (brief 001 §2);
        `install_process_trust` only affects contexts created after the call; a PKCS#12 bundle's
        CAs are **not** trust anchors unless `pkcs12_trust_ca=True`; a `key_password` in a config
        file is a secret in a config file.
23. [ ] `README.md` — extend the Plan 026 TLS section with the four adapter snippets, the mTLS
        (encrypted key + PKCS#12) snippet, and the `install_process_trust` snippet **with its
        warning inline**, not in a footnote.
24. [ ] `CLAUDE.md` — extend the TLS subsection with two rules: **never** add an HTTP client to any
        `varco_*` package's dependencies for the adapters' sake (function-body imports only,
        guarded by `test_tls_no_hard_client_deps.py`); **never** call `install_process_trust()`
        from library code. Add one Decision-Tree row (*Need a varco trust store in httpx/aiohttp/
        urllib3/requests? → `varco_core.tls.clients`, never a hand-built context*).
25. [ ] `CHANGELOG.md` `## [Unreleased]` `### Added` — the four adapters, `install_process_trust`,
        `key_password`, PKCS#12 support; each referencing "Plan 027 / T4" or "Plan 027 / T6".
26. [ ] `BACKLOG.md` — mark T4/T6 against Plan 027.

---

## Edge cases

- **`to_aiohttp_connector()` called outside a running event loop** → aiohttp's `TCPConnector`
  historically wanted a loop. Construct it lazily where possible and document that it must be
  created inside the loop that will use it; assert the failure mode in a test rather than papering
  over it.
- **`to_requests_adapter()` used with `verify=False` in the store** → the context is `CERT_NONE`;
  requests will still emit its own `InsecureRequestWarning` only when `verify=False` is passed to
  the *session*, not from the context. Documented so the missing warning is not read as safety.
- **PKCS#12 bundle with no private key** (a trust-only bundle) → `Pkcs12LoadError` with a message
  naming the likely intent (use `ca_cert`/`ca_folders` instead).
- **PKCS#12 with an empty password vs. no password** → `cryptography` distinguishes `b""` from
  `None`; both are accepted and mapped through unchanged. Tested both ways.
- **`/dev/shm` present but full or read-only** → fall back to the system temp dir; never fail the
  handshake because of a tmpfs problem.
- **`install_process_trust()` called after HTTP clients have already built contexts** → those
  clients are unaffected (brief 001 §4). The docstring states it; there is no way to detect it.
- **Two `RestoreHandle`s released out of order** → each restores the value it captured, so
  out-of-order release can resurrect an older hook. Documented; the context-manager form is the
  supported usage.

## Verification

```bash
uv sync --all-packages --all-extras
uv run pytest varco_core/tests/test_tls_mtls.py varco_core/tests/test_tls_pkcs12.py \
              varco_core/tests/test_tls_clients.py varco_core/tests/test_tls_install.py \
              varco_core/tests/test_tls_no_hard_client_deps.py -q
uv run pytest varco_core/tests/ -m integration -k "tls"   # loopback TLS + mTLS + rotation
uv run pytest varco_core/tests/ varco_fastapi/tests/      # regression sweep
uv run python scripts/api_surface.py --check
make lint && make type-check && make test
```

**DoD:** all four adapters exercised against a real loopback TLS server; the structural
no-hard-dependency test green; PKCS#12 temp material provably removed on both the success and the
failure path; snapshot regenerated and committed.

## Risks

- **⚠️ ASSUMPTION — `ssl._create_default_https_context` is the right hook and exists.** Brief 001
  §4 describes `truststore.inject_into_ssl()` as modifying "`ssl` module globals" and
  "`ssl.SSLContext.create_default_context()` globally", but does **not** name the attribute. The
  private name is long-standing CPython, but it is private. Invariant: `install_process_trust`
  must fail **loudly and immediately** on an interpreter where it is absent (Step 18), never
  silently no-op. If the assumption breaks, the honest fallback is to delete the function and
  document the manual `verify=store.to_httpx_verify()` pattern — not to find a second private hook.
- **⚠️ ASSUMPTION — the introspection attributes used in Step 11** (`TCPConnector._ssl`,
  `PoolManager.connection_pool_kw`) are internal to aiohttp/urllib3 and may change. Not
  brief-grounded. Mitigation: Step 15's real-handshake integration test is the *authoritative*
  proof; Step 11 is the fast unit approximation, and if an introspection attribute disappears the
  fix is to delete that assertion, not to pin a client version.
- **The PKCS#12 temp-file window.** The private key exists on a filesystem for the duration of one
  `load_cert_chain`. Invariant: mode `0600`, private directory, unlink in `finally`, `/dev/shm`
  preferred on Linux. Any refactor that moves the write outside a `try/finally` is a security
  regression, and Step 5 asserts absence on both paths precisely so such a refactor goes red.
- **Secrets in `repr`.** `key_password`/`pkcs12_password` are `repr=False`, but a future field
  added carelessly would not be. Step 2's `repr` assertion is the standing guard; extend it when
  adding fields.
- **Adding a hard client dependency by accident.** A contributor "fixing" a function-body import
  for tidiness would silently add ~4 libraries to every varco install and undo P1's import-budget
  work. `test_tls_no_hard_client_deps.py` (Step 10) is the gate; it must never be marked xfail.
- **Scope creep into `varco_fastapi.client`.** Wiring these adapters into `AsyncVarcoClient`/
  `ClientProfile` is tempting and is explicitly a Non-goal — that surface is in the frozen API
  snapshot and deserves its own plan.
