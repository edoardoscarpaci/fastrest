# Research 001 — TLS Certificate Hot-Reload, Trust Stores, and File Watching in Python (2025–2026)

Date: 2026-09-03 · Freshness matters: **yes** — these technologies move fast; watch for watchfiles/truststore adoption, Let's Encrypt cert timeline changes, and new hot-reload standards.

## Question

What is the current state of the art for **TLS certificate hot-reload, trust stores, and file watching in the Python ecosystem** at the feature level (not implementation tutorials)? Cover file watching (watchdog/watchfiles/polling), certificate hot-reload mechanics, trust stores (certifi/truststore), SSL context injection into HTTP clients, and mTLS support.

---

## Findings

### 1. File Watching in Python

**watchfiles (Rust/notify-based) is the modern production standard.**
- **Current version**: 1.2.0 — [watchfiles documentation](https://watchfiles.helpmanual.io/) (official)
- **Platform support**: Linux (x86_64, aarch64, armv7l, musl variants), macOS (Intel/Apple Silicon), Windows (x86_64, aarch64, i686)
- **Async support**: ✅ Full async via `awatch()` and `arun_process()` using `anyio.to_thread.run_sync()` — [watchfiles docs](https://watchfiles.helpmanual.io/)
- **Maintenance**: Active successor to `watchgod` (deprecated; security fixes only). Used by uvicorn for `--reload` — [uvicorn dependency](https://pypi.org/project/uvicorn/)
- **Watchdog comparison**: Older, broader Python version support (3.6+), narrower API (no granular event types or custom handlers). Watchfiles requires Python 3.10+. — [watchfiles PyPI](https://pypi.org/project/watchfiles/0.13/)
- **Polling**: Neither watchfiles nor watchdog ship polling as a default; both rely on OS file-system notifications (inotify/FSEvents/kqueue). Polling is a fallback only when inotify fails.

**Known pitfalls:**
- **inotify on Docker bind mounts / NFS**: inotify does NOT fire on NFS-mounted volumes (filesystem-level limitation in Linux kernel — NFS before v4.1 incompatible). Workaround: mount NFS on host first, use that mount as Docker volume. — [Docker forums](https://forums.docker.com/t/inotify-not-working-on-nfs-type-volumes/131283)
- **Kubernetes ConfigMap/Secret volumes**: Kubelet updates mounted files by creating a new timestamped directory and atomically swapping a symlink (`..data` → `..data.NEW`). File watchers see only `IN_DELETE_SELF` on the old symlink, not file content changes. Debouncing required. — [Kubernetes inotify pitfalls](https://ahmet.im/blog/kubernetes-inotify/)
- **Editor atomic-replace semantics**: Editors (VSCode, vim with `:w`) write to a temp file then rename. Race condition risk if watching before rename completes. — Implicit in [Kubernetes security docs](https://kubernetes.io/docs/concepts/configuration/secret/)

### 2. Certificate Hot-Reload / Rotation in Comparable Systems

**The ecosystem splits into two patterns: in-process context swap vs. external SDS.**

#### In-Process Context Swap (Python ASGI servers)
- **uvicorn 0.30+** and **hypercorn**: Both pass `--ssl-certfile` / `--ssl-keyfile` CLI args. Neither has native hot-reload; currently requires rolling restart (out of load-balancer → restart → restore). — [Uvicorn docs](https://uvicorn.dev/deployment/)
- **Python `ssl.SSLContext` mechanics**: 
  - `ctx.load_cert_chain(certfile, keyfile)` CAN be called on a live context, but **already-established TLS connections see no change** — only NEW handshakes use the updated cert. — [Python ssl docs](https://docs.python.org/3/library/ssl.html)
  - `SSLContext.sni_callback` (Python 3.7+, preferred over legacy `set_servername_callback`) is called per-handshake with `(socket, server_name, context)`. Allows returning different contexts per request. If callback raises, TLS handshake fails. — [Python 3.13 ssl docs](https://docs.python.org/3.13/library/ssl.html)
  - ⚠️ **No "swap context in-place" primitive** — must create a new context, call `load_cert_chain()`, then switch references. Active connections on the old context persist until close.

#### External Push Model (Infrastructure Standard)
- **Envoy SDS (Secret Discovery Service)**: Central SDS server pushes new certs to proxies over gRPC; proxies apply immediately without restart. Requires mTLS auth between proxy and SDS. — [Envoy SDS docs](https://www.envoyproxy.io/docs/envoy/latest/configuration/security/secret)
- **SPIFFE/SPIRE Workload API**: SPIRE Agent auto-rotates workload X.509 SVIDs (typically ~1 hr lifetime) at ~50% of TTL, streams new certs to workload over Unix socket. Apps never see cert expiry. — [SPIRE certificate rotation](https://protocolsoup.com/protocol/spiffe/flow/certificate-rotation)
- **cert-manager (Kubernetes)**: Watches Certificate resources, renews at 1/3 TTL (for 90-day certs: day 60), updates backing Kubernetes Secret. Apps must watch Secret and reload; cert-manager handles ACME protocol. — [cert-manager docs](https://cert-manager.io/v1.4-docs/usage/certificate/)

**Emerging standard (2025):** IETF Internet-Draft proposing passive file-mtime-based auto-reload for web servers, but not yet adopted by uvicorn/hypercorn. — [IETF draft-ahrweiler-hotreload](https://www.ietf.org/ietf-ftp/internet-drafts/draft-ahrweiler-hotreload-00.html)

### 3. Trust Stores in Python

**Dual model: static bundle (`certifi`) vs. OS delegating (`truststore`).**

- **truststore 0.10.4**: Exposes native OS certificate stores (Windows CryptoAPI, macOS Security, Linux OpenSSL). Automatically updated; checks CRLs. Works with Python 3.10+ on macOS 10.8+, Windows, Linux. — [truststore ReadTheDocs](https://truststore.readthedocs.io/en/latest/)
  - **Injection**: Call `truststore.inject_into_ssl()` early (modifies `ssl` module globals). ⚠️ **Libraries must NOT call this**; libraries create `truststore.SSLContext()` directly and pass to their HTTP client. — [truststore docs](https://truststore.readthedocs.io/en/latest/)
  - **pip integration**: pip 24.2+ uses truststore by default with certifi fallback. — [pip 24.2 release](https://truststore.readthedocs.io/en/stable/)
  
- **certifi**: Static Mozilla CA bundle, updated monthly. De facto standard for 15+ years; still used as fallback. — [certifi on PyPI](https://pypi.org/project/certifi/)

- **uv integration**: uv 0.11.0 (March 2026) deprecated `--native-tls` in favor of `--system-certs` for native OS trust store. Honors `SSL_CERT_FILE` env var override. — [uv certificates docs](https://docs.astral.sh/uv/concepts/authentication/certificates/)

- **Environment variable conventions**:
  - `SSL_CERT_FILE`: Path to PEM bundle (single cert or multi-cert). Honored by Python's ssl module, requests, urllib3, httpx, aiohttp. One file only.
  - `SSL_CERT_DIR`: Directory of PEM files (c_rehash naming). Supported by OpenSSL; Python's ssl module, requests.
  - `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`: requests-specific; curl-specific (not honored by Python stdlib).
  - ⚠️ When set to non-empty, these **override the default entirely** — only the specified certs are trusted. — [uv docs](https://docs.astral.sh/uv/concepts/authentication/certificates/)

### 4. Injecting Custom SSL Context into HTTP Clients (Latest Versions)

#### requests (2.32.3+)
- **API**: Pass `ssl.SSLContext` to `HTTPAdapter`, mount onto session: 
  ```python
  import requests
  adapter = requests.adapters.HTTPAdapter()
  # HTTPAdapter holds ssl_context internally
  # Pass context via requests.Session.mount()
  ```
- **Fix**: Bug preventing custom SSLContext in HTTPAdapter subclasses fixed in 2.32.3. — [requests changelog](https://github.com/requests/requests)
- **mTLS**: Pass client cert + key as file paths via `cert=("/path/to/cert", "/path/to/key")`.
- **Pitfall**: Corporate MITM proxies inserting intermediate CAs — use `SSL_CERT_FILE` or construct context with proxy CA included.

#### urllib3 v2.x (current; v1.26 EOL'd)
- **API**: `PoolManager(ssl_context=ctx)` or `HTTPSConnectionPool(..., ssl_context=ctx)`.
- **Best practice**: Obtain context from `urllib3.util.create_urllib3_context()` to preserve defaults, then mutate: — [urllib3 advanced usage](https://urllib3.readthedocs.io/en/latest/advanced-usage.html)
  ```python
  from urllib3.util import create_urllib3_context
  ctx = create_urllib3_context()
  ctx.load_cert_chain(...)  # safe to customize
  ```
- **mTLS**: Same as requests — file paths via constructor kwargs.
- **Proxy SSL**: `ProxyManager(proxy_ssl_context=ctx)` for proxy TLS.

#### httpx (latest 0.28+)
- **API**: `Client(verify=ssl_context)` or per-request `client.get(..., verify=ssl_context)`. The `verify` parameter accepts bool, file path, or `ssl.SSLContext`.
- **Deprecation**: httpx 0.28 **deprecated** `cert=` parameter in favor of building an `ssl.SSLContext` yourself. — [httpx SSL docs](https://www.python-httpx.org/advanced/ssl/)
- **Companion libraries**: `httpx-pki` and `httpx-pkcs12` wrap `ssl.SSLContext` for PKCS#12 mTLS convenience. — [httpx-pkcs12 PyPI](https://pypi.org/project/httpx-pkcs12/)
- **Global SSL**: `truststore.inject_into_ssl()` affects httpx via its use of stdlib ssl.

#### aiohttp 3.14.3 (latest stable; 4.0.0 alpha exists)
- **API**: `TCPConnector(ssl=ssl_context)` passed to `ClientSession()`. The `ssl` parameter also accepts:
  - `True` (default, uses `ssl.create_default_context()`)
  - `False` (no verification)
  - `aiohttp.Fingerprint` (pin by fingerprint)
  - `ssl.SSLContext` (custom)
  - They are **mutually exclusive** with `verify_ssl`, `fingerprint`. — [aiohttp 3.14.3 docs](https://docs.aiohttp.org/en/stable/client_reference.html)
- **mTLS**: File paths loaded into context via `SSLContext.load_cert_chain()` before passing.

#### General Pattern: "Trust Store Monkeypatching"
- `truststore.inject_into_ssl()` modifies `ssl.SSLContext.create_default_context()` globally so all stdlib-ssl-using libraries (requests, urllib3, httpx, aiohttp) pick up the OS trust store automatically.
- **Caveat**: Only affects future `ssl.SSLContext` creation; existing contexts unaffected. Must be called before any library imports ssl. — [truststore docs](https://truststore.readthedocs.io/en/latest/)

### 5. mTLS / Client Certificates

**File paths (native) vs. PKCS#12 (requires third-party libs).**

- **Native approach** (all clients): Load cert + key from files:
  ```python
  ssl_context = ssl.create_default_context()
  ssl_context.load_cert_chain(certfile, keyfile, password_func)  # password_func for encrypted keys
  ```
  Works with requests, urllib3, httpx, aiohttp. — [Python ssl.SSLContext docs](https://docs.python.org/3/library/ssl.html)

- **PKCS#12 / PFX (encrypted container)**: Not natively supported; requires dedicated libraries:
  - **httpx-pkcs12**: Wraps PKCS#12 decoding for httpx. — [PyPI](https://pypi.org/project/httpx-pkcs12/)
  - **requests-pkcs12**: Wraps PKCS#12 decoding for requests via custom HTTPAdapter; no temp files, no monkey-patching. — [PyPI](https://pypi.org/project/requests-pkcs12/)
  - **httpx-pki**: Higher-level convenience wrapper. — [PyPI](https://pypi.org/project/httpx-pki/)

- **Encrypted private key support**: Python's `ssl.SSLContext.load_cert_chain(certfile, keyfile, password_function)` accepts a password callback. All native support; third-party libs inherit this. — [Python docs](https://docs.python.org/3/library/ssl.html)

- **Encrypted-key as file limitation**: Keys in `PEM` format can have `ENCRYPTED` header (e.g., `-----BEGIN ENCRYPTED RSA PRIVATE KEY-----`). The password callback must return bytes; all clients support this. PKCS#12 is a binary format bundling cert + encrypted key; third-party libs exist because stdlib ssl lacks `PKCS#12` decoding.

---

## Version / Compatibility Notes

| Technology | Current Version | Python Min | Status | Notes |
|---|---|---|---|---|
| **watchfiles** | 1.2.0 | 3.10 | ✅ Active | Replaces deprecated watchgod; used by uvicorn |
| **watchdog** | (3.x line) | 3.6+ | ✅ Maintained | Older; broader compat; narrower API |
| **truststore** | 0.10.4 | 3.10+ | ✅ Stable | pip 24.2+, uv 0.11.0+ integrated it; OS trust store native |
| **certifi** | (2024.x) | Any | ✅ Maintained | Static bundle; fallback in pip/uv if truststore unavailable |
| **requests** | 2.32.3+ | 3.8+ | ✅ Latest | Fixed HTTPAdapter SSLContext bug in 2.32.3 |
| **urllib3** | 2.x | 3.7+ | ✅ Current | v1.26 EOL'd; v2.0+ uses TLS 1.2 by default |
| **httpx** | 0.28+ | 3.8+ | ✅ Latest | Deprecated `cert=` param in 0.28; use `verify=ssl_context` |
| **aiohttp** | 3.14.3 | 3.8+ | ✅ Latest | 4.0.0-alpha in dev; TCPConnector(ssl=) stable API |
| **Python ssl** | stdlib | 3.10+ | ✅ Stable | `SSLContext.sni_callback` since 3.7 (preferred over `set_servername_callback`) |
| **uvicorn** | 0.30+ | 3.8+ | ✅ Latest | No native hot-reload; uses watchfiles for `--reload` |
| **hypercorn** | 0.18+ | 3.8+ | ✅ Latest | No native hot-reload; ASGI server only |

### Certificate Lifecycle

- **Let's Encrypt classic**: 90-day validity window (issue time ≤ 1 hour before legal start, so ~89 days of use).
- **Let's Encrypt 6-day short-lived**: 160 hours (~6.67 days). Generally available as of 2026-01-15. Opt-in via ACME certificate profile. — [Let's Encrypt 6-day GA](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
- **Let's Encrypt future**: Transitioning to 45-day certs by 2028 (timeline confirmed 2026-02). — [Let's Encrypt 45-day announcement](https://letsencrypt.org/2025/12/02/from-90-to-45)
- **cert-manager renewal**: Begins at 1/3 TTL (90-day cert → day 60). Configurable via `Certificate.spec.renewBefore`. — [cert-manager docs](https://cert-manager.io/v1.4-docs/usage/certificate/)
- **SPIFFE/SPIRE**: ~1 hour TTL (configurable to minutes), auto-renewed at 50% TTL. Workloads never see expiry. — [SPIRE use cases](https://spiffe.io/docs/latest/spire-about/use-cases/)

---

## Evidence Gaps

1. **Hot-reload in production at scale**: No major open-source Python ASGI server ships native hot-reload; pattern of "rolling restart" is standard but not well-documented in one place. IETF draft exists (2025) but unimplemented. Worth a separate brief: *Survey of production TLS reload strategies (sidecar proxies, SPIRE agents, cert-manager watchers)*.

2. **Watchfiles performance benchmarks**: Documentation claims "much faster" than watchdog due to Rust backend, but no published benchmark figures. Observed in practice but not quantified.

3. **Kubernetes-native file watching**: The `..data` symlink swap means atomic updates but require custom debouncing. Upstream Kubernetes docs (Secrets v1 API) note this but don't prescribe a watcher; implies apps must solve individually. Worth a separate brief: *Kubernetes Secret/ConfigMap mutation detection patterns in Python*.

4. **truststore adoption in ecosystem**: pip 24.2+ integrated; uv 0.11.0+ integrated. Adoption by downstream frameworks (FastAPI, Django, Starlette) unknown; likely relies on users calling `truststore.inject_into_ssl()` rather than framework doing it automatically.

5. **Corporate MITM proxy + truststore interaction**: How does truststore interact with intercepting proxies that inject intermediate CAs? Unclear from docs; requires testing. Standard approach (SSL_CERT_FILE pointing to proxy CA bundle) likely works but not documented.

---

## Librarian's Note

**The evidence favours the following choices for a production Python service:**

1. **File watching**: Use `watchfiles` if Python 3.10+ available; it is the modern standard, async-capable, and actively maintained. Fallback to `watchdog` only if legacy Python required. **Do NOT use polling as a primary strategy** — it is slow and racy. **Exception**: Kubernetes ConfigMap/Secret mounts require custom debouncing (watchfiles sees symlink swap, not file content change); consider statting the file on each event to confirm changes.

2. **Certificate hot-reload**: **Avoid trying to implement in-process hot-reload in Python ASGI servers** — neither uvicorn nor hypercorn have native support, and manually swapping `SSLContext` on a live server is fragile (old connections persist, race windows exist). **Instead**: (a) Deploy a reverse proxy (Nginx, Caddy, Envoy) in front of your ASGI server and let the proxy handle TLS + auto-reload via SDS or file watching. (b) If on Kubernetes: use cert-manager + a sidecar agent (e.g., step-sds) watching Secrets and restarting your app or signalling it via SIGHUP (application-level hot-reload). (c) For true zero-downtime: use SPIFFE/SPIRE (auto-rotation + streaming delivery) or Envoy SDS (push model). **In-process callback-based SNI is viable only for multi-tenancy where different tenants have different certs; does not help with rotation.**

3. **Trust stores**: Call `truststore.inject_into_ssl()` in your app's entry point (before any imports of http clients). Requires Python 3.10+. Older projects: set `SSL_CERT_FILE` env var or use `certifi` as fallback. pip/uv handle this automatically.

4. **SSL context injection**: All major HTTP clients (requests, urllib3, httpx, aiohttp) support custom `ssl.SSLContext`. **Recommended pattern**: Create context once at app startup, reference it in all clients (singletons, dependency injection). Do NOT create contexts per-request (expensive). For PKCS#12 mTLS: use `httpx-pkcs12` or `requests-pkcs12` as thin wrappers.

5. **mTLS / client certificates**: Prefer file paths + encrypted-key support (`SSLContext.load_cert_chain` with password callback) for simplicity. Use third-party PKCS#12 wrappers only if receiving certs in that format. No significant ecosystem friction; all approaches work.

---

## Sources

- [watchfiles 1.2.0 documentation](https://watchfiles.helpmanual.io/)
- [watchfiles PyPI](https://pypi.org/project/watchfiles/0.13/)
- [Kubernetes inotify pitfalls](https://ahmet.im/blog/kubernetes-inotify/)
- [Docker forums — inotify on NFS volumes](https://forums.docker.com/t/inotify-not-working-on-nfs-type-volumes/131283)
- [Kubernetes Secrets documentation](https://kubernetes.io/docs/concepts/configuration/secret/)
- [Python 3.13 ssl — TLS/SSL wrapper](https://docs.python.org/3.13/library/ssl.html)
- [Envoy SDS (Secret Discovery Service)](https://www.envoyproxy.io/docs/envoy/latest/configuration/security/secret)
- [SPIRE certificate rotation flow](https://protocolsoup.com/protocol/spiffe/flow/certificate-rotation)
- [cert-manager Certificate Resources documentation](https://cert-manager.io/v1.4-docs/usage/certificate/)
- [IETF draft-ahrweiler-hotreload (2025)](https://www.ietf.org/ietf-ftp/internet-drafts/draft-ahrweiler-hotreload-00.html)
- [truststore 0.10.4 ReadTheDocs](https://truststore.readthedocs.io/en/latest/)
- [pip 24.2 truststore integration](https://truststore.readthedocs.io/en/stable/)
- [certifi on PyPI](https://pypi.org/project/certifi/)
- [uv TLS certificates documentation](https://docs.astral.sh/uv/concepts/authentication/certificates/)
- [requests 2.32.3+ on PyPI](https://pypi.org/project/requests/)
- [urllib3 v2.x advanced usage](https://urllib3.readthedocs.io/en/latest/advanced-usage.html)
- [httpx 0.28+ SSL documentation](https://www.python-httpx.org/advanced/ssl/)
- [httpx-pkcs12 PyPI](https://pypi.org/project/httpx-pkcs12/)
- [requests-pkcs12 PyPI](https://pypi.org/project/requests-pkcs12/)
- [aiohttp 3.14.3 Client Reference](https://docs.aiohttp.org/en/stable/client_reference.html)
- [Let's Encrypt 6-day certificates GA (2026-01-15)](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
- [Let's Encrypt 45-day transition announcement (2025-12-02)](https://letsencrypt.org/2025/12/02/from-90-to-45)
- [Uvicorn 0.30+ deployment documentation](https://uvicorn.dev/deployment/)
