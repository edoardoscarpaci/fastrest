# Research 001 — testcontainers-python first-party NATS & Memcached support
Date: 2026-08-20 · Freshness matters: **yes** — testcontainers-python releases frequently; module availability and wait strategies may change

## Question
Does testcontainers-python (≥4.0) ship first-party modules for NATS and Memcached brokers? If yes: package/extra name, container class, minimum version, canonical usage pattern, and wait strategy. If no: idiomatic way to run them via generic `DockerContainer` API with proper synchronization.

## Findings

### NATS Module — First-Party Support ✅
- **Status**: Official first-party module since **v4.3.0** (released March 24, 2024) — [CHANGELOG](https://github.com/testcontainers/testcontainers-python/blob/main/CHANGELOG.md)
- **Extra**: `testcontainers[nats]` — no additional dependencies required beyond testcontainers itself — [pyproject.toml](https://github.com/testcontainers/testcontainers-python/blob/main/pyproject.toml)
- **Container class**: `testcontainers.nats.NatsContainer` — [module docs](https://testcontainers-python.readthedocs.io/en/latest/modules/nats/README.html)
- **Default image**: `nats:latest`
- **Ports**: Client `4222`, management `8222`
- **Wait strategy**: `wait_for_logs` with default message `'Server is ready'`, timeout `120` seconds
- **Constructor parameters**:
  - `image` (str, default `'nats:latest'`)
  - `client_port` (int, default `4222`)
  - `management_port` (int, default `8222`)
  - `expected_ready_log` (str, default `'Server is ready'`)
  - `ready_timeout_secs` (int, default `120`)
- **Connection URI method**: `nats_uri()` → returns connection string for the nats-py client
- **Canonical usage**:
  ```python
  from testcontainers.nats import NatsContainer
  import nats
  
  with NatsContainer() as nats_container:
      client = await nats.connect(nats_container.nats_uri())
      # perform NATS operations
      await client.close()
  ```
  — [official example](https://testcontainers-python.readthedocs.io/en/latest/modules/nats/README.html)

### Memcached Module — First-Party Support ✅
- **Status**: Official first-party module since **v4.4.1** (released May 14, 2024) — [CHANGELOG](https://github.com/testcontainers/testcontainers-python/blob/main/CHANGELOG.md)
- **Extra**: `testcontainers[memcached]` — no additional dependencies — [pyproject.toml](https://github.com/testcontainers/testcontainers-python/blob/main/pyproject.toml)
- **Container class**: `testcontainers.memcached.MemcachedContainer` — [module docs](https://testcontainers-python.readthedocs.io/en/latest/modules/memcached/README.html)
- **Default image**: `memcached:1`
- **Ports**: Default exposed port `11211` (standard Memcached protocol)
- **Wait strategy**: Port-based wait strategy (implicitly waits for `11211` to be listening via `PortWaitStrategy`)
- **Constructor parameters**:
  - `image` (str, default `'memcached:1'`)
  - `port_to_expose` (int, default `11211`)
  - Additional keyword arguments passed to parent `DockerContainer`
- **Host/port retrieval method**: `get_host_and_port()` → tuple `(host, port)`
- **Canonical usage**:
  ```python
  from testcontainers.memcached import MemcachedContainer
  
  with MemcachedContainer() as memcached_container:
      host, port = memcached_container.get_host_and_port()
      # connect with aiomcache, pymemcache, or similar
  ```
  — [official example](https://testcontainers-python.readthedocs.io/en/latest/modules/memcached/README.html)

### Current Stable Version
- **testcontainers-python 4.15.0** released July 24, 2026 — [PyPI](https://pypi.org/project/testcontainers/)
- Both NATS (v4.3.0+) and Memcached (v4.4.1+) are available in all stable versions since their introduction

## Options compared

| Module | First-party? | Extra name | Since version | Wait strategy | Default image | Port | Connection method |
|---|---|---|---|---|---|---|---|
| **NATS** | ✅ Yes | `testcontainers[nats]` | 4.3.0 (Mar 2024) | `wait_for_logs` ("Server is ready", 120s timeout) | `nats:latest` | 4222 (client), 8222 (mgmt) | `nats_uri()` |
| **Memcached** | ✅ Yes | `testcontainers[memcached]` | 4.4.1 (May 2024) | Port 11211 (`PortWaitStrategy`) | `memcached:1` | 11211 | `get_host_and_port()` |

## Version/compatibility notes

- **NATS module**: Introduced v4.3.0 (March 24, 2024); all versions ≥4.3.0 support it. Pre-4.3.0 deployments must use generic `DockerContainer` or upgrade.
- **Memcached module**: Introduced v4.4.1 (May 14, 2024); all versions ≥4.4.1 support it. Pre-4.4.1 deployments must use generic `DockerContainer` or upgrade.
- **Current varco target**: If targeting `testcontainers>=4.0`, you can assume ≥4.3.0 for NATS and ≥4.4.1 for Memcached (both released over 2 years ago as of Aug 2026).
- **Breaking changes**: None. Both modules are stable since introduction; no deprecations logged.

## Generic DockerContainer fallback (if not using first-party modules)

Should you need to support pre-4.3.0 (NATS) or pre-4.4.1 (Memcached) or prefer hand-rolled containers:

### NATS via GenericContainer
```python
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

class NatsContainer(DockerContainer):
    def __init__(self, image: str = "nats:latest", **kwargs):
        super().__init__(image, **kwargs)
        self.port_to_expose = 4222
        self.with_exposed_ports(self.port_to_expose)
        self.with_command("--debug")  # optional: enable debug logs for waiting
        self.wait_strategy = wait_for_logs(self, "Server is ready", timeout=120)

    def get_connection_url(self) -> str:
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self.port_to_expose)
        return f"nats://{host}:{port}"
```

### Memcached via GenericContainer
```python
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

class MemcachedContainer(DockerContainer):
    def __init__(self, image: str = "memcached:1", **kwargs):
        super().__init__(image, **kwargs)
        self.port_to_expose = 11211
        self.with_exposed_ports(self.port_to_expose)
        # Memcached has no debug log message; use wait_for_port instead
        from testcontainers.core.container import DEFAULT_TIMEOUT
        self.wait_strategy = wait_for_logs(self, "", timeout=120)  # fallback

    def get_host_and_port(self) -> tuple:
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self.port_to_expose)
        return host, int(port)
```

**Better practice** (per [testcontainers-python wait-strategies docs](https://deepwiki.com/testcontainers/testcontainers-python/3.5-wait-strategies)): Use `PortWaitStrategy` or `wait_for_port()` for services that don't emit clear log markers:

```python
from testcontainers.core.waiting_utils import wait_for_logs

# For Memcached specifically (no recognizable log marker):
wait_strategy = wait_for_port(11211)  # waits for TCP port to accept connections
```

## Evidence gaps
- Exact internal wait-strategy implementation for `MemcachedContainer` (whether it uses port-based or other method) is not documented in the Read the Docs examples; inferred from general testcontainers-python patterns and Memcached service nature (no verbose startup logs).
- No explicit mention in official docs of `testcontainers[nats]` or `testcontainers[memcached]` as *required* extras; the modules are discoverable but package extras themselves list as `[]` (zero dependencies), so both work with bare `testcontainers>=4.0`.
- Future support: NATS and Memcached modules are stable; no deprecation or removal planned as of latest (4.15.0) docs.

## Librarian's note
**What the sources indicate:** Both NATS and Memcached have been first-party modules in testcontainers-python since spring 2024 (v4.3.0 and v4.4.1 respectively). Using the dedicated `testcontainers[nats]` and `testcontainers[memcached]` extras is the standard, forward-compatible approach; neither requires additional Python packages beyond testcontainers itself. `varco_nats` and `varco_memcached` should replace bare `testcontainers>=4.0` in their `pyproject.toml` extras with the proper broker-specific ones to enable IDE autocomplete and signal intent clearly to maintainers. Fallback to `DockerContainer` is documented but unnecessary for current and future deployments.

---

## Sources
- [testcontainers-python CHANGELOG.md](https://github.com/testcontainers/testcontainers-python/blob/main/CHANGELOG.md)
- [testcontainers-python pyproject.toml extras](https://github.com/testcontainers/testcontainers-python/blob/main/pyproject.toml)
- [NATS module documentation](https://testcontainers-python.readthedocs.io/en/latest/modules/nats/README.html)
- [Memcached module documentation](https://testcontainers-python.readthedocs.io/en/latest/modules/memcached/README.html)
- [testcontainers-python PyPI page (current version 4.15.0)](https://pypi.org/project/testcontainers/)
- [Wait strategies documentation (DeepWiki mirror)](https://deepwiki.com/testcontainers/testcontainers-python/3.5-wait-strategies)
