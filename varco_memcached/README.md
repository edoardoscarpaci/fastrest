# varco-memcached
[![PyPI version](https://img.shields.io/pypi/v/varco-memcached.svg)](https://pypi.org/project/varco-memcached/)
Memcached cache backend for the [varco](https://github.com/edoardoscarpaci/varco) framework.

`MemcachedCache` implements `varco_core.cache.CacheBackend` on top of
[aiomcache](https://github.com/aio-libs/aiomcache). Values are serialized
(JSON by default) and stored in Memcached; TTL is enforced natively via
Memcached's `exptime`.

---

## Installation

```bash
uv add varco-memcached
# or: pip install varco-memcached
```

Requires a running Memcached server (`memcached -p 11211`).

---

## Quick start

```python
from varco_memcached import MemcachedCache, MemcachedCacheSettings

settings = MemcachedCacheSettings(host="localhost", port=11211, key_prefix="myapp:")

cache = MemcachedCache(settings)
await cache.start()

await cache.set("user:42", {"name": "Ada"}, ttl=300)
value = await cache.get("user:42")  # {"name": "Ada"}

await cache.stop()
```

---

## Configuration

`MemcachedCacheSettings` reads from environment variables under the
`VARCO_MEMCACHED_CACHE_` prefix (kept separate from any Redis settings in the
same process):

| Field | Default | Env var |
|---|---|---|
| `host` | `"localhost"` | `VARCO_MEMCACHED_CACHE_HOST` |
| `port` | `11211` | `VARCO_MEMCACHED_CACHE_PORT` |
| `pool_size` | `2` | `VARCO_MEMCACHED_CACHE_POOL_SIZE` |
| `key_prefix` | `""` | `VARCO_MEMCACHED_CACHE_KEY_PREFIX` |

```python
settings = MemcachedCacheSettings.from_env()
```

---

## Key differences from `varco_redis.RedisCache`

- **Keys must be `bytes`** — `aiomcache` requires byte keys; `varco_memcached`
  encodes the prefixed key as UTF-8 internally.
- **No native key enumeration.** Memcached has no `SCAN`-equivalent command.
  `clear()` only removes keys written by *this* `MemcachedCache` instance in
  the current process (tracked in an in-process registry) — keys written by
  other processes, or surviving a restart, are unaffected and expire via TTL.
  Use the server's `flush_all()` for a full flush (affects every client
  sharing that server).
- **No native `exists()`.** It performs a `get` and checks for `None` — a
  full round trip, though deserialization is skipped.
- **TTL semantics differ.** Memcached's `exptime` of `0` means "never
  expire" — the opposite convention from a bare Redis `SET` with no TTL.
- **Key length limit.** Memcached rejects keys over 250 bytes or containing
  whitespace/control characters — `MemcachedCacheSettings.memcached_key()`
  does not validate this; callers are responsible for keeping keys short.

---

## DI integration

Unlike scan-only event bus backends, `MemcachedCache` needs an async
`start()` call to open its connection pool — wiring goes through
`MemcachedCacheConfiguration`, a providify `@Configuration`:

```python
from providify import DIContainer
from varco_core.cache import CacheBackend
from varco_memcached.di import async_bootstrap

container = await async_bootstrap()  # installs MemcachedCacheConfiguration
cache = await container.aget(CacheBackend)  # started MemcachedCache singleton
await cache.set("key", "value", ttl=60)
await container.ashutdown()  # stops the cache via @PreDestroy
```

Or manually:

```python
from providify import DIContainer
from varco_memcached.cache import MemcachedCacheConfiguration

container = DIContainer()
await container.ainstall(MemcachedCacheConfiguration)
cache = await container.aget(CacheBackend)
```

Override settings by registering a higher-priority `@Provider` **before**
`ainstall()` (equal-priority bindings resolve first-registered):

```python
from providify import Provider


@Provider(singleton=True)
def memcached_cache_settings() -> MemcachedCacheSettings:
    return MemcachedCacheSettings(host=os.environ["MEMCACHED_HOST"], key_prefix="myapp:")


container.provide(memcached_cache_settings)
await container.ainstall(MemcachedCacheConfiguration)
```

---

## Running tests

```bash
uv run pytest varco_memcached/tests/
```
