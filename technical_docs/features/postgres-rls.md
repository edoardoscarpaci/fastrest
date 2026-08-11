# Postgres Row-Level Security: the InitPlan cliff, `SET LOCAL`, and two fail-open seams

Plan 005, Phase 8 (gap U-5). Filed as a **report, not a request** — "we build
RLS ourselves"; this document and `varco_sa/varco_sa/rls.py`'s two helpers are
the deliverable, not a wired-in tenancy layer. Nothing here is applied to any
generated table by default. RLS stays opt-in, per table, wired by the
*application's own* Alembic revisions.

## 1. The InitPlan finding — the 150× cliff

`current_setting()` is a **`VOLATILE`** Postgres function, and it is **not
`LEAKPROOF`**. Both properties matter to the planner independently of each
other, and together they explain a performance cliff that is invisible in
every functional test and catastrophic under production data volumes.

The obvious way to write a tenant-isolation policy is:

```sql
CREATE POLICY orders_tenant_isolation ON orders
    USING (tenant_id = current_setting('rls.tenant_id')::uuid);
```

This is **correct** — it returns the right rows — and it is also what causes
the planner to fall back to a **sequential scan** on every RLS-protected
query, regardless of whatever index exists on `tenant_id`. Because
`current_setting()` is volatile, Postgres cannot assume it returns the same
value for every row being evaluated within the same query, so it cannot
safely push the filter below an index scan the way it would push down a
literal or a stable-function result. The planner re-evaluates (or must assume
it might need to re-evaluate) the call per row.

The fix is a one-line rewrite — wrap the call in a scalar subquery:

```sql
CREATE POLICY orders_tenant_isolation ON orders
    USING (tenant_id = (SELECT current_setting('rls.tenant_id', true)::uuid));
```

Wrapping `current_setting(...)` in `(SELECT ...)` gives the planner an
**InitPlan**: a subquery the planner can prove is uncorrelated with the outer
query, so it is evaluated **once per query**, not once per row (or,
functionally, treated as if it were once per row without the rewrite). One
documented production query went from **8 100 ms to 94 ms** — an ~86×
improvement — from this rewrite alone, with the returned rows byte-identical
before and after.

**This is invisible in tests.** At development/test data volumes (dozens to
low thousands of rows), a sequential scan and an index scan both complete in
single-digit milliseconds — nothing in a functional test suite distinguishes
the two forms. The regression only shows up against a production-sized table,
which is exactly why it is dangerous: it ships clean and then costs an
incident.

**Any varco RLS helper MUST emit the `(SELECT …)` form.**
`varco_sa.rls.enable_rls_ddl()` does this unconditionally — it is not a
configurable option, because there is no correct reason to emit the naive
form. `varco_sa/tests/test_rls.py` asserts the literal substring `"(SELECT "`
is present in the generated DDL as a permanent regression test; treat that
assertion as load-bearing, not decorative.

The second argument to `current_setting`, `true`, is the **missing-ok** flag:
`current_setting('rls.tenant_id', true)` returns `NULL` instead of raising
when the GUC has never been set on this connection — which is what makes "a
session that never called `set_tenant_local()` sees zero rows" the failure
mode, rather than every unscoped query raising a Postgres error.

## 2. `SET LOCAL` vs `SET` — the same defect class as U-16

Setting the tenant GUC has two forms with very different pooling behaviour:

```sql
SET rls.tenant_id = '...';                         -- session-scoped
SELECT set_config('rls.tenant_id', '...', true);   -- transaction-scoped (is_local = true)
```

`SET` (or `set_config(..., false)`) is **session-scoped** — it survives past
the current transaction and is visible to every subsequent statement on that
physical connection until it is changed or the session ends. Under a
**transaction-mode** connection pooler (PgBouncer `pool_mode=transaction`,
pgcat, Supavisor in transaction mode), "the session" is a fiction: the
pooler returns the physical connection to its pool as soon as the
transaction commits, and the *next* logical caller to borrow that connection
inherits whatever GUC value was left set — silently applying the wrong
tenant's filter (or no filter, if RLS is bypassed by a stale unset value) to
someone else's queries.

This is **the same defect class as U-16**'s `SAAdvisoryLock` finding (see
`technical_docs/features/distributed-locks.md`): a construct whose safety
depends on "my session" being a stable, dedicated physical connection breaks
silently the moment a transaction-mode pooler is introduced between the
application and Postgres.

`set_config(..., true)` (`is_local = true`, i.e. `SET LOCAL`'s functional
equivalent as a callable) is scoped to the **current transaction only** — it
is unset automatically at `COMMIT`/`ROLLBACK`, regardless of what pooler sits
in front of the connection, because the reset happens as part of the
transaction boundary rather than relying on session lifetime.
`varco_sa.rls.set_tenant_local()` always uses this form — there is no
session-scoped variant offered, for the same reason `SAXactAdvisoryLock` is
the recommended primitive over the session-scoped `SAAdvisoryLock`: the
transaction-scoped form is pooling-safe unconditionally, and the
session-scoped form is only safe under a topology (direct connections, no
transaction-mode pooler) that is easy to assume and expensive to get wrong.

## 3. The `search_path` hazard

`varco_sa/varco_sa/connection.py:236` sets `search_path` via
`server_settings={"search_path": self.schema_name}` **once, at connection
init time**, from a single deployment-wide `schema_name` setting. This is
correct and sufficient for the topology varco ships today: **one schema per
install**. It is not, and was never designed to be, a per-request or
per-tenant routing mechanism.

If a downstream consumer wants **schema-per-tenant** (a different isolation
strategy from RLS — a dedicated Postgres schema per tenant instead of a
shared table with a policy), setting `search_path` once at connection init is
unsafe: `search_path` is ordinary session state, and under a pooled
connection it has exactly the same leak shape as `SET` in §2 — a tenant's
schema routing set on one borrow of a physical connection would apply to
whatever the next logical caller runs on that same connection.

The two correct patterns for schema-per-tenant, neither of which is what
`connection.py:236` does today:

- **`SET LOCAL search_path`** — the same transaction-scoped `set_config(...,
  true)` primitive as §2, applied to `search_path` instead of a custom GUC.
- **SQLAlchemy's `schema_translate_map`** — a per-`Connection`/`Session`
  mapping that rewrites schema-qualified table references at the SQL-compile
  layer, without touching the database session's `search_path` at all.

**Verified: no `schema_translate_map` usage exists anywhere in this codebase
today.** Schema-per-tenant is not implemented, and if it is ever added, it
must not reuse the connection-init `search_path` assignment as its routing
mechanism — that would silently reproduce the pooling hazard this section
describes.

## 4. `TenantAwareService._scoped_params` fails open

`varco_core/varco_core/service/tenant.py:424`'s `_scoped_params` hook —
the mixin that prepends `tenant_id = <tid>` to every query issued through
`AsyncService.list()`/`.read()` for a `TenantAwareService` subclass — is
**application-layer** isolation. Stated plainly, because it is the whole
point of this section: **any query path that bypasses the mixin returns
cross-tenant rows.** This is not a bug in the mixin; it has no visibility
into queries that never go through it — a raw repository call from a script,
a report query built by hand, an admin tool, a future code path a reviewer
missed. `TenantAwareService` filters what passes through it and enforces
nothing below the service layer.

Postgres RLS is the correct **defense-in-depth** answer to this specific
fail-open surface: a policy applied via `enable_rls_ddl()` enforces tenant
scoping at the database itself, so a query that bypasses
`TenantAwareService` still cannot see another tenant's rows — the isolation
no longer depends on every code path remembering to filter correctly. RLS
does not replace `TenantAwareService` (the mixin's query shaping, scoped
pagination, etc. are still needed at the application layer); it closes the
gap for the paths that skip it.

## Using the helpers

```python
from varco_sa.rls import enable_rls_ddl, set_tenant_local

# In an Alembic revision, applied by the APPLICATION — varco never does this
# to a generated table by default:
def upgrade() -> None:
    for stmt in enable_rls_ddl("orders"):
        op.execute(stmt)

# In request/transaction setup, before issuing tenant-scoped queries:
async with session.begin():
    await set_tenant_local(session, tenant_id)
    # ... queries within this transaction only see tenant_id's rows
```

`enable_rls_ddl(table, *, tenant_column="tenant_id", setting="rls.tenant_id",
policy_name=None)` returns three DDL statements in order: `ENABLE ROW LEVEL
SECURITY`, `FORCE ROW LEVEL SECURITY` (without `FORCE`, Postgres exempts the
table owner — often the migration/ORM role — from the policy entirely, which
is itself a silent bypass), and `CREATE POLICY` with the InitPlan-form
`USING`/`WITH CHECK` clause. It performs no I/O — the caller runs the
statements inside their own Alembic revision.

`set_tenant_local(session, tenant_id, *, setting="rls.tenant_id")` executes a
single `SELECT set_config(:setting, :value, true)` — call it as the first
statement inside the transaction whose queries should be tenant-scoped; the
setting does not survive past that transaction (see §2), by design.

## What is opt-in and what is not

Nothing in this plan applies an RLS policy to any table varco generates.
`enable_rls_ddl()` is a pure DDL-string generator — a table has no RLS at
all until an application writes an Alembic revision that calls it and runs
that revision. This keeps RLS adoption a per-table, per-application decision
matching each table's actual isolation requirements, rather than a
one-size-fits-all default that could break a table that legitimately needs
cross-tenant reads (e.g. an admin reporting table).
