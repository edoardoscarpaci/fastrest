# varco-sa

[![PyPI version](https://img.shields.io/pypi/v/varco-sa)](https://pypi.org/project/varco-sa/)
[![Python](https://img.shields.io/pypi/pyversions/varco-sa)](https://pypi.org/project/varco-sa/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/edoardoscarpaci/varco/blob/main/LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-edoardoscarpaci%2Fvarco-blue?logo=github)](https://github.com/edoardoscarpaci/varco)

SQLAlchemy async backend for **varco**.

Generates SQLAlchemy ORM classes at runtime from your `DomainModel` subclasses — no hand-written ORM models needed. Requires [`varco-core`](https://pypi.org/project/varco-core/).

---

## Install

```bash
pip install varco-sa

# With PostgreSQL async driver:
pip install "varco-sa[postgresql]"

# With SQLite (tests / local dev):
pip install "varco-sa[sqlite]"
```

---

## Features

- **Zero-boilerplate ORM** — `SAModelFactory` generates `DeclarativeBase` subclasses at runtime; no duplication between domain and ORM layers
- **Full async repository** — `AsyncSQLAlchemyRepository` implements `AsyncRepository` (CRUD, `exists()`, `stream_by_query()` with server-side cursor)
- **Unit of Work** — `SQLAlchemyUnitOfWork` manages `AsyncSession` lifecycle and atomic commits
- **One-liner bootstrap** — `SAFastrestApp` + `SAConfig` wire engine, session factory, ORM generation, and table creation
- **Alembic integration** — `get_target_metadata()` and `print_create_ddl()` helpers for migration scripts
- **Schema Guard** — `SchemaGuard` detects drift between ORM metadata and the live database schema
- **Query integration** — accepts `varco-core` `QueryParams` / `QueryBuilder` AST natively; translates to SQLAlchemy `where()` clauses

---

## What's in the package

| Module | Purpose |
|---|---|
| `factory.py` | `SAModelFactory` — generates `DeclarativeBase` subclasses at runtime; `SAModelRegistry` — escape hatch |
| `repository.py` | `AsyncSQLAlchemyRepository` — `AsyncSession`-backed CRUD + `exists()` + `stream_by_query()` |
| `uow.py` | `SQLAlchemyUnitOfWork` — session lifecycle + atomic commits |
| `provider.py` | `SQLAlchemyRepositoryProvider` — wires factory + repos + UoW |
| `bootstrap.py` | `SAConfig`, `SAFastrestApp` — one-liner app setup |
| `alembic_helpers.py` | `get_target_metadata`, `print_create_ddl` — Alembic integration |
| `schema_guard.py` | `SchemaGuard` — drift detection between ORM metadata and live DB |
| `job_store.py` | `SAJobStore` — `varco_jobs` table; time/lease/fencing (`run_at`, `owner_id`, `lease_expires_at`, `lease_epoch`), `claim_next`/`renew`/`reap_expired_leases` |
| `dlq.py` | `SADeadLetterQueue` — `varco_dead_letters` table; durable DLQ for `OutboxRelay`/job-runner poison entries |
| `outbox.py` | `SAOutboxRepository`, `SARelayOutboxRepository` — `varco_outbox` table with attempt tracking (`attempts`, `last_error`, `next_attempt_at`) and native `mark_failed()` |
| `advisory_lock.py` | `SAAdvisoryLock` (session-scoped, default) / `SAXactAdvisoryLock` (transaction-scoped, pooler-safe) — `AbstractDistributedLock` implementations over PostgreSQL advisory locks |

---

## Quick start

### Bootstrap (one-liner)

```python
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
from varco_sa import SAConfig, SAFastrestApp

class Base(DeclarativeBase): pass

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/mydb")

app = SAFastrestApp(SAConfig(
    engine=engine,
    base=Base,
    entity_classes=(User, Post),
))

await app.create_all()              # CREATE TABLE IF NOT EXISTS ...
uow_provider = app.uow_provider     # ready to inject as IUoWProvider
```

### Manual setup

```python
from sqlalchemy.ext.asyncio import async_sessionmaker
from varco_sa import SQLAlchemyRepositoryProvider

sessions = async_sessionmaker(engine, expire_on_commit=False)
provider = SQLAlchemyRepositoryProvider(engine=engine, session_factory=sessions)
provider.register(User, Post)
await provider.create_all()

async with provider.make_uow() as uow:
    user = await uow.users.save(User(name="Edo", email="edo@example.com"))
    print(user.pk)
```

### Query integration

```python
from varco_core import QueryBuilder, QueryParams

async with provider.make_uow() as uow:
    # exists() — uses SA identity-map cache, no full ORM load when cached
    if await uow.posts.exists(post_id):
        ...

    # stream_by_query() — server-side cursor, constant memory regardless of result size
    params = QueryParams(node=QueryBuilder().eq("active", True).build())
    async for post in uow.posts.stream_by_query(params):
        await process(post)
```

### Alembic integration

```python
# alembic/env.py
from varco_sa import get_target_metadata
from myapp.models import User, Post

target_metadata = get_target_metadata(User, Post)
```

Preview the DDL before running a migration:

```python
from varco_sa import print_create_ddl

print(print_create_ddl(User, Post, dialect="postgresql"))
```

### Infrastructure table migrations (job store, outbox, DLQ)

⚠️ This package does not ship a checked-in Alembic environment for its own
infrastructure tables (`varco_jobs`, `varco_outbox`, `varco_dead_letters`,
`varco_encryption_keys`) — they are plain `Table`/`MetaData` objects (`jobs_metadata`,
`outbox_metadata`, `dead_letters_metadata`) generated at import time, not
ORM-mapped domain classes. Include them in your own application's Alembic
`target_metadata` and let `autogenerate` produce the revision:

```python
# alembic/env.py
from varco_sa.job_store import jobs_metadata
from varco_sa.outbox import outbox_metadata
from varco_sa.dlq import dead_letters_metadata

target_metadata = [get_target_metadata(User, Post), jobs_metadata, outbox_metadata, dead_letters_metadata]
```

Every column added by Plan 005 Phase 3/4 (`OutboxEntryModel.attempts`/`last_error`/
`next_attempt_at`; `varco_jobs`'s ten new time/lease/retention/reference columns
plus three new indexes) is nullable or server-defaulted — the autogenerated
`ALTER TABLE ADD COLUMN` is additive and safe to run against a live table. On
Postgres, build the three new `varco_jobs` indexes `CONCURRENTLY` if the table
is large and live. See `technical_docs/features/job-scheduling-and-leases.md`
and `technical_docs/features/dead-letter-queues.md`.

### Distributed locking (transaction pooling safety)

Two `AbstractDistributedLock` implementations ship in `advisory_lock.py`
(Plan 005 Phase 5, U-16). Pick based on your connection topology:

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from varco_sa.advisory_lock import SAAdvisoryLock, SAXactAdvisoryLock

engine = create_async_engine("postgresql+asyncpg://...")

# Direct connections / session-mode pooler → SAAdvisoryLock is fine.
lock = SAAdvisoryLock(engine)
handle = await lock.try_acquire("inventory:item_42", ttl=30)
if handle is not None:
    async with handle:
        await reserve_item(42)

# ⚠️ Behind a TRANSACTION-mode pooler (PgBouncer pool_mode=transaction,
# pgcat, Supavisor in transaction mode) → use SAXactAdvisoryLock.xact()
# instead. It runs on YOUR OWN session/transaction and is released
# automatically at COMMIT/ROLLBACK — no separate release() call that a
# pooler could misroute to a different physical connection.
xact_lock = SAXactAdvisoryLock()
session_factory = async_sessionmaker(engine, expire_on_commit=False)

async with session_factory() as session:
    async with session.begin():
        async with xact_lock.xact("inventory:item_42", session) as acquired:
            if acquired:
                await reserve_item_via(session, 42)
        # COMMIT here releases the lock — no release() call needed.
```

**Why `SAAdvisoryLock` is unsafe behind a transaction-mode pooler:** the pooler
may route `try_acquire()`'s `pg_try_advisory_lock` and a later `release()`'s
`pg_advisory_unlock` to two *different* physical connections. The unlock then
returns `false` (wrong session), the lock leaks on the original connection,
and the next unrelated borrower of that connection silently inherits it. See
`technical_docs/features/distributed-locks.md` for the full pooling
compatibility matrix and why `RedisLock` is not a drop-in substitute in an
air-gapped/no-Redis deployment.

`AbstractDistributedLock` DI binding: `SAModule` registers `SAAdvisoryLock`
as the default (upgrade-safe) binding and `SAXactAdvisoryLock` as a
directly-injectable singleton — see `di.py` for the override recipe if you
want `SAXactAdvisoryLock` to win the `AbstractDistributedLock` binding.

### Row-Level Security helpers (opt-in, nothing wired by default)

`varco_sa.rls` (Plan 005 Phase 8 / U-5) ships two small helpers — no RLS policy is applied
to any generated table unless your own Alembic revision calls `enable_rls_ddl()`:

```python
from varco_sa.rls import enable_rls_ddl, set_tenant_local

# In your own Alembic revision:
def upgrade() -> None:
    for stmt in enable_rls_ddl("orders"):
        op.execute(stmt)

# Before issuing tenant-scoped queries, inside the transaction:
async with session.begin():
    await set_tenant_local(session, tenant_id)
    # ... queries in this transaction only see tenant_id's rows
```

`enable_rls_ddl()` **always** emits the `(SELECT current_setting(..., true))` InitPlan form
— the naive `current_setting(...)` (no subquery) form defeats index usage and forces a
sequential scan; one documented query went 8 100 ms → 94 ms from this rewrite alone.
`set_tenant_local()` uses transaction-scoped `set_config(..., true)`, the same
PgBouncer-transaction-mode-safe pattern as `SAXactAdvisoryLock` above — a session-scoped
`SET` would leak into whichever session next borrows the pooled connection. See
`technical_docs/features/postgres-rls.md` for the full report: the InitPlan finding,
`SET LOCAL` vs `SET`, the `search_path` hazard, and why `TenantAwareService._scoped_params`
fails open and RLS is the recommended defense-in-depth.

### Schema Guard — detect drift

```python
from varco_sa import SchemaGuard

guard = SchemaGuard(engine, User, Post)
differences = await guard.check()
if differences:
    print("Schema drift detected:", differences)
```

### Access the generated SA model (escape hatch)

```python
from varco_sa import SAModelRegistry
from sqlalchemy.orm import relationship

UserORM = SAModelRegistry.get(User)
UserORM.posts = relationship("PostORM", back_populates="author")
```

---

## Connection settings

`PostgresConnectionSettings` is a structured, env-var loadable config object
that produces driver-ready output for asyncpg and SQLAlchemy.

### Plain connection

```python
from varco_sa.connection import PostgresConnectionSettings
from sqlalchemy.ext.asyncio import create_async_engine

conn = PostgresConnectionSettings(
    host="my-db",
    port=5432,
    database="orders",
    username="svc_user",
    password="s3cret",
)

# SQLAlchemy async engine — two equivalent forms:
engine = create_async_engine(conn.to_sqlalchemy_url())

# With pool settings included:
engine = create_async_engine(conn.to_sqlalchemy_url(), **conn.to_engine_kwargs())
```

### From environment variables

```bash
POSTGRES_HOST=my-db
POSTGRES_PORT=5432
POSTGRES_DATABASE=orders
POSTGRES_USERNAME=svc_user
POSTGRES_PASSWORD=s3cret
```

```python
conn = PostgresConnectionSettings.from_env()
engine = create_async_engine(conn.to_sqlalchemy_url(), **conn.to_engine_kwargs())
```

### With TLS / SSL

```python
from varco_core.connection import SSLConfig
from pathlib import Path

ssl = SSLConfig(
    ca_cert=Path("/etc/ssl/postgres-ca.pem"),
    verify=True,
)

conn = PostgresConnectionSettings.with_ssl(
    ssl,
    host="prod-db",
    database="orders",
    username="svc_user",
    password="s3cret",
)

# to_engine_kwargs() includes the ssl context inside connect_args automatically:
engine = create_async_engine(conn.to_sqlalchemy_url(), **conn.to_engine_kwargs())
```

Or from env:

```bash
POSTGRES_HOST=prod-db
POSTGRES_DATABASE=orders
POSTGRES_SSL__CA_CERT=/etc/ssl/postgres-ca.pem
POSTGRES_SSL__VERIFY=true
```

### With mTLS (client certificates)

```python
ssl = SSLConfig(
    ca_cert=Path("/etc/ssl/ca.pem"),
    client_cert=Path("/etc/ssl/client.crt"),
    client_key=Path("/etc/ssl/client.key"),
)

conn = PostgresConnectionSettings.with_ssl(ssl, host="prod-db", database="orders")
```

### With structured auth object

```python
from varco_core.connection import BasicAuthConfig

conn = PostgresConnectionSettings(
    host="prod-db",
    database="orders",
    auth=BasicAuthConfig(username="admin", password="s3cret"),
)
# auth overrides the inline username/password fields in to_dsn()
```

Or from env:

```bash
POSTGRES_AUTH__TYPE=basic
POSTGRES_AUTH__USERNAME=admin
POSTGRES_AUTH__PASSWORD=s3cret
```

### Connection settings reference

| Env var | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Database server hostname |
| `POSTGRES_PORT` | `5432` | Database server port |
| `POSTGRES_DATABASE` | `postgres` | Database name |
| `POSTGRES_SCHEMA_NAME` | `public` | Default search path schema |
| `POSTGRES_USERNAME` | `postgres` | Inline auth username |
| `POSTGRES_PASSWORD` | _(empty)_ | Inline auth password |
| `POSTGRES_POOL_SIZE` | `5` | SQLAlchemy pool min connections |
| `POSTGRES_MAX_OVERFLOW` | `10` | SQLAlchemy pool max additional connections |
| `POSTGRES_POOL_TIMEOUT` | `30.0` | Seconds to wait for a pool connection |
| `POSTGRES_SSL__CA_CERT` | — | Path to CA certificate |
| `POSTGRES_SSL__CLIENT_CERT` | — | Path to client certificate (mTLS) |
| `POSTGRES_SSL__CLIENT_KEY` | — | Path to client private key (mTLS) |
| `POSTGRES_SSL__VERIFY` | `true` | TLS peer verification |
| `POSTGRES_AUTH__TYPE` | — | `basic` |
| `POSTGRES_AUTH__USERNAME` | — | Auth username (overrides inline) |
| `POSTGRES_AUTH__PASSWORD` | — | Auth password (overrides inline) |

---

## Related packages

| Package | Description |
|---|---|
| [`varco-core`](https://pypi.org/project/varco-core/) | Domain model, service layer, query AST, JWT — required dependency |
| [`varco-beanie`](https://pypi.org/project/varco-beanie/) | Beanie / Motor MongoDB backend (alternative to this package) |

---

## Migration 1.x → 2.0

`SQLAlchemyRepositoryProvider.__init__` is now the **DI-only** path (it takes an
injected `SAConfig`). Direct construction moved to a `from_components()`
classmethod:

```python
# Before (1.x)
provider = SQLAlchemyRepositoryProvider(base=Base, session_factory=sessions)

# After (2.0)
provider = SQLAlchemyRepositoryProvider.from_components(
    base=Base, session_factory=sessions
)
```

The DI path (`container.get(SQLAlchemyRepositoryProvider)` with an `SAConfig`
bound) is unchanged.

---

## Links

- **Repository**: https://github.com/edoardoscarpaci/varco
- **Full docs**: https://github.com/edoardoscarpaci/varco#sqlalchemy-backend
- **Issue tracker**: https://github.com/edoardoscarpaci/varco/issues
