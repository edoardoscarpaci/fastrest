# varco-beanie

[![PyPI version](https://img.shields.io/pypi/v/varco-beanie)](https://pypi.org/project/varco-beanie/)
[![Python](https://img.shields.io/pypi/pyversions/varco-beanie)](https://pypi.org/project/varco-beanie/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/edoardoscarpaci/varco/blob/main/LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-edoardoscarpaci%2Fvarco-blue?logo=github)](https://github.com/edoardoscarpaci/varco)

Beanie (Motor / MongoDB) async backend for **varco**.

Generates Beanie `Document` classes at runtime from your `DomainModel` subclasses — no hand-written Document models needed. Requires [`varco-core`](https://pypi.org/project/varco-core/).

---

## Install

```bash
pip install varco-beanie
```

### Requirements

- Python ≥ 3.12
- MongoDB ≥ 4.0
- For multi-document transactions: a MongoDB replica set or sharded cluster

---

## Features

- **Zero-boilerplate ODM** — `BeanieModelFactory` generates `Document` subclasses at runtime from your `DomainModel` classes; no duplication
- **Full repository** — `AsyncBeanieRepository` implements `AsyncRepository` (CRUD, `exists()`, `stream_by_query()`)
- **Unit of Work** — `BeanieUnitOfWork` wraps Motor session lifecycle with optional transactions
- **One-liner bootstrap** — `BeanieRepositoryProvider` + `BeanieFastrestApp` wire everything including `init_beanie()`
- **Query integration** — accepts `varco-core` `QueryParams` / `QueryBuilder` AST natively
- **Multitenancy** — `varco_beanie.tenancy`: database-per-tenant isolation via per-tenant
  Document class clones (`BeanieTenantPool`/`BeanieTenantBinding`), the per-tenant
  `dropDatabase` GDPR-erasure primitive (`BeanieDatabaseProvisioner`), and the durable
  tenant catalog (`BeanieTenantCatalog`) — see
  [`technical_docs/features/multitenancy.md`](../technical_docs/features/multitenancy.md)
  for the RD-7 clone-cost formula and worked example

---

## What's in the package

| Module | Purpose |
|---|---|
| `factory.py` | `BeanieModelFactory` — generates `Document` subclasses; `BeanieDocRegistry` — escape hatch to access the generated Document |
| `repository.py` | `AsyncBeanieRepository` — Motor-backed CRUD + `exists()` + `stream_by_query()` |
| `uow.py` | `BeanieUnitOfWork` — Motor session lifecycle (optional transactions) |
| `provider.py` | `BeanieRepositoryProvider` — wires factory + repos + UoW + `init_beanie()` |
| `bootstrap.py` | `BeanieConfig`, `BeanieFastrestApp` — one-liner app setup |

---

## Quick start

### Bootstrap (one-liner)

```python
from motor.motor_asyncio import AsyncIOMotorClient
from varco_beanie import BeanieConfig, BeanieFastrestApp

client = AsyncIOMotorClient("mongodb://localhost:27017")

app = BeanieFastrestApp(
    BeanieConfig(
        motor_client=client,
        db_name="myapp",
        entity_classes=(User, Post),
    )
)

await app.init()  # calls beanie.init_beanie() internally
uow_provider = app.uow_provider  # ready to inject as IUoWProvider
```

### Manual setup

```python
from varco_beanie import BeanieRepositoryProvider

provider = BeanieRepositoryProvider(motor_client=client, db_name="myapp")
provider.register(User, Post)
await provider.init()

async with provider.make_uow() as uow:
    user = await uow.users.save(User(name="Edo", email="edo@example.com"))
    print(user.pk)
```

### Transactions (replica set required)

```python
provider = BeanieRepositoryProvider(
    motor_client=client,
    db_name="myapp",
    transactional=True,  # wraps each UoW in a Motor session transaction
)
```

### Query integration

```python
from varco_core import QueryBuilder, QueryParams

async with provider.make_uow() as uow:
    # exists() — uses .count(), no document load
    if await uow.posts.exists(post_id):
        ...

    # stream_by_query() — Motor batches internally, bounded memory
    params = QueryParams(node=QueryBuilder().eq("published", True).build())
    async for post in uow.posts.stream_by_query(params):
        await process(post)
```

### Access the generated Beanie Document (escape hatch)

```python
from varco_beanie import BeanieDocRegistry

PostDoc = BeanieDocRegistry.get(Post)
# use PostDoc for Beanie-specific operations not exposed by the repository
```

### Migrations

MongoDB is schemaless, so there is **no autogenerate and no document-shape differ** — that
would compare varco's generated `Document` shape against sampled documents and produce
guesses. What ships instead is hand-written, ordered migrations plus index reconciliation,
behind the same `varco_core.migration.AbstractMigrator` contract `varco_sa` implements.

```python
from varco_beanie import Migration, MigrationRegistry, BeanieMigrator


class BackfillOrderStatus(Migration):
    version = "20260812_001"  # sortable; uniqueness validated at register() time
    name = "backfill order status"

    async def up(self, db) -> None:
        await db.orders.update_many({"status": None}, {"$set": {"status": "pending"}})

    async def down(self, db) -> None:  # optional — omit and downgrade raises
        await db.orders.update_many({"status": "pending"}, {"$set": {"status": None}})


registry = MigrationRegistry()
registry.register(BackfillOrderStatus)
# or: registry.discover("myapp.migrations")

migrator = BeanieMigrator(db, registry)
await migrator.upgrade()
```

Applied migrations are recorded one document per migration in the `varco_migrations`
collection (created lazily on first use), alongside a `{_id: "__lock__"}` document that
provides multi-pod exclusion — acquired with a conditional `find_one_and_update` upsert,
renewed by a background heartbeat, and released with owner fencing so a reclaimed holder
cannot delete the new holder's lock. A recorded migration whose source no longer matches
its stored checksum raises (tamper detection); opt out with `verify_checksums=False`. A
migration with no `down()` raises `IrreversibleMigrationError` on downgrade.

#### ⚠️ Index reconciliation is `check` by default — even in `upgrade` mode

`index_mode: "off" | "check" | "create"` defaults to `"check"` and is **independent of
`VARCO_MIGRATE_MODE`**. Running `mode="upgrade"` does **not** silently start building
indexes.

This is a rule, not a caveat. An index build on a large collection is minutes-to-hours of
work; on a replica set it replicates and can stall secondaries; and it would happen exactly
when a rolling deploy is starting N new pods. `check` reports drift through the existing
`BeanieIndexGuard` and lets `on_failure` decide; missing indexes appear in the plan as
`Revision(id="index:<collection>:<label>", branch="index")`. `create` applies **missing**
indexes only — it never drops unexpected ones, because dropping an index someone added
deliberately is destructive.

Create indexes from the CLI, as a pre-deploy job:

```bash
varco migrate index -t myapp.db:migrator --create
varco migrate new --name "backfill status" --out myapp/migrations   # scaffold a Migration
```

Hand-written `up(db)` scripts run under `mode="upgrade"` normally — the restriction applies
only to *reconciled* indexes, the ones varco derives implicitly.

The Mongo lock does have the TTL-sizing problem that the Postgres advisory-lock design
avoids (a crashed holder is reclaimed only after expiry), which is one more reason index
builds belong in the CLI path. See
[`technical_docs/features/schema-migrations.md`](../technical_docs/features/schema-migrations.md).

---

## Notes

- `CheckConstraint` entries in `varco-core` metadata are silently ignored — MongoDB has no SQL CHECK constraints. Use Pydantic validators instead.
- Foreign key hints are metadata only — MongoDB has no FK enforcement.
- Composite PKs are emulated via compound unique indexes; `find_by_id(pk_tuple)` issues a `find_one` with a composite filter.

---

## Related packages

| Package | Description |
|---|---|
| [`varco-core`](https://pypi.org/project/varco-core/) | Domain model, service layer, query AST, JWT — required dependency |
| [`varco-sa`](https://pypi.org/project/varco-sa/) | SQLAlchemy async backend (alternative to this package) |

---

## Links

- **Repository**: https://github.com/edoardoscarpaci/varco
- **Full docs**: https://github.com/edoardoscarpaci/varco#beanie-backend
- **Issue tracker**: https://github.com/edoardoscarpaci/varco/issues
