# Postgres Row-Level Security: the InitPlan cliff, `SET LOCAL`, and two fail-open seams

Plan 005, Phase 8 (gap U-5). Originally filed as a **report, not a request** —
"we build RLS ourselves"; `varco_sa/varco_sa/rls.py`'s two helpers remain a
per-table, opt-in primitive, not something any generated table gets by
default. RLS is wired by the *application's own* Alembic revisions, same as
day one.

Plan 007 (see [Multitenancy](multitenancy.md)) later added the actual
tenancy **layer** this document once described as absent: `TenantIsolation.
SHARED` (± `enforce_rls`) is one of three selectable isolation strategies,
alongside the schema-per-tenant and database-per-tenant strategies §3
below covers. RLS itself is unchanged by that — still assert-only, still
per-table, still opt-in.

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

**`NULLIF(..., '')` around the missing-ok form — the reset-to-empty-string
trap.** Postgres does not reset a `SET LOCAL`/`set_config(..., true)` GUC to
`NULL` when the transaction ends — it resets it to the **empty string**.
Without the `NULLIF` wrapper, the very next statement issued on a pooled
connection that previously ran a tenant-scoped transaction evaluates
`''::uuid` (or `''::text` for a `text`-cast policy) inside the InitPlan and
**raises** (`invalid input syntax for type uuid: ""`) instead of returning
zero rows — the opposite of "no tenant set, hide everything" and, on a
transaction-mode pooler, indistinguishable from a real outage the moment the
next logical caller reuses that connection. `enable_rls_ddl()` therefore
emits:

```sql
USING (tenant_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::uuid))
```

`NULLIF(current_setting(...), '')` maps *both* "GUC never set on this
connection" (missing-ok `NULL`) and "GUC was set earlier this session, then
reset to `''` at the end of a `SET LOCAL` transaction" to the same `NULL`,
so the comparison never matches and the query fails **closed** — zero rows,
no crash — in either case. The wrapper stays inside the scalar subquery, so
the InitPlan optimisation from above is unaffected; this is additive, not a
different rewrite.

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

## 3. Schema-per-tenant: the supported mechanism is `schema_translate_map`, not `search_path`

`varco_sa/varco_sa/connection.py:236` sets `search_path` via
`server_settings={"search_path": self.schema_name}` **once, at connection
init time**, from a single deployment-wide `schema_name` setting. This is
correct and sufficient for the topology it targets: **one schema per
install**. It is not, and was never designed to be, a per-request or
per-tenant routing mechanism.

**Schema-per-tenant is now implemented** (`TenantIsolation.SCHEMA`,
Plan 007 — see [Multitenancy](multitenancy.md)), and the chosen routing
mechanism is `varco_sa.tenancy.router.SASchemaRouter`, built on SQLAlchemy's
**`schema_translate_map`** rather than `SET LOCAL search_path`:

```python
engine.execution_options(schema_translate_map={"tenant": "t_acme"})
```

`schema_translate_map` rewrites schema-qualified table references at the
SQL-**compile** layer, per session — it never touches the database
connection's session state at all, so it sidesteps the `SET`-vs-`SET LOCAL`
pooling hazard from §2 entirely rather than needing the transaction-scoped
form to stay safe. The decisive property over `SET LOCAL search_path`: a
table whose ORM class carries no symbolic schema token simply is not
translated, so a forgotten routing call **fails closed** — a compile/DB
error, not a silent read of the wrong tenant's schema. `SET LOCAL
search_path` would fail open on the same mistake: a session that never set
it silently falls back to the default schema and returns **another
tenant's rows, successfully**. That asymmetry is why `schema_translate_map`
is the primary mechanism and `SET LOCAL search_path` is kept only as a
documented, explicitly-opted-into escape hatch
(`SASchemaRouter(mechanism="search_path")`) for raw `text()` SQL that
`schema_translate_map` cannot reach — and even in that escape-hatch mode,
`SASchemaRouter` always emits `set_config(..., true)` (transaction-scoped),
**never** a bare session-scoped `SET`.

**Raw `text()` SQL is not translated by `schema_translate_map`** — it must
self-qualify its own schema. This is a real, only-partly-mitigable caveat;
see [Multitenancy](multitenancy.md) for the full guidance.

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
fail-open surface under `TenantIsolation.SHARED` (± RLS): a policy applied
via `enable_rls_ddl()` enforces tenant scoping at the database itself, so a
query that bypasses `TenantAwareService` still cannot see another tenant's
rows — the isolation no longer depends on every code path remembering to
filter correctly. RLS does not replace `TenantAwareService` (the mixin's
query shaping, scoped pagination, etc. are still needed at the application
layer); it closes the gap for the paths that skip it.

The **structural** fix for the same fail-open surface is `TenantIsolation.
SCHEMA` or `TenantIsolation.DATABASE` (§3 above, [Multitenancy](
multitenancy.md)): under those strategies a query that bypasses
`TenantAwareService` still cannot reach another tenant's rows, because
there *is* no other tenant's rows reachable from the routed
schema/connection — isolation is enforced by what the query can even see,
not by an application-layer filter or a database policy evaluated per row.
Pick RLS when tenants must stay in one shared schema (unbounded tenant
count, cheap to run); pick `SCHEMA`/`DATABASE` when a wrong query must
*error* rather than merely be *filtered*.

`assert_rls_enabled()` (`varco_sa.tenancy.rls_check`,
`TenancySettings(enforce_rls=True)`) is the automated version of "did I
apply RLS to every table that needs it" — it reads `pg_policies`/
`pg_class.relrowsecurity` and raises naming any table missing a policy. It
**skips `TenantScope.GLOBAL` tables and the ten framework tables** rather
than flagging them: a shared reference table legitimately carries no
`tenant_id` and needs no RLS policy, and without the skip the assertion
would report every such table as "missing a policy" and be unusable in any
deployment with global data. See [Multitenancy](multitenancy.md) for the
full `TenantScope` model.

## Applying RLS via a migration

RLS is DDL. It must be ordered after table creation and reviewed like any
other schema change, so it belongs in a **revision** — never in a startup
hook. Nothing in varco applies an RLS policy automatically, and there is no
mode, flag, or env var that makes it do so.

`varco_sa.migration.ops` provides the two operations (Plan 006 Phase 6):

```python
from alembic import op
from varco_sa.migration.ops import rls_upgrade, rls_downgrade

def upgrade() -> None:
    rls_upgrade(op, "orders")

def downgrade() -> None:
    rls_downgrade(op, "orders")
```

`rls_upgrade(op, table, *, tenant_column="tenant_id", policy_name=None,
setting="rls.tenant_id")` issues `op.execute()` for each statement
`enable_rls_ddl()` builds — it does not duplicate the SQL, so the
InitPlan-form `(SELECT current_setting(..., true))` guarantee of §1 holds
identically here. `varco_sa/tests/test_rls_migration_ops.py` asserts the
rendered statements match `enable_rls_ddl()`'s output exactly; treat that
assertion as load-bearing.

`rls_downgrade(op, table, *, policy_name=None)` issues the matching
`DROP POLICY IF EXISTS` + `ALTER TABLE … DISABLE ROW LEVEL SECURITY`. Pass
the same `policy_name` you passed to `rls_upgrade` if you overrode the
default `f"{table}_tenant_isolation"` scheme.

**Both are no-ops with a logged `WARNING` on a non-PostgreSQL dialect**
rather than raising, so a project that runs the same revisions against
SQLite in CI and PostgreSQL in production does not crash on the SQLite leg.
This also means a CI run proves nothing about the policy — the RLS
integration test needs real PostgreSQL **and a non-superuser role**
(superusers bypass RLS regardless of `FORCE ROW LEVEL SECURITY`).

See [Schema migrations](schema-migrations.md) for how revisions are applied,
the framework Alembic branch, and the operations guidance around running DDL
in production.

## Using the helpers directly

```python
from varco_sa.rls import enable_rls_ddl, set_tenant_local

# The lower-level form — rls_upgrade() above wraps exactly this. Use it when
# you need the raw statements (inspection, a non-Alembic migration tool):
def upgrade() -> None:
    for stmt in enable_rls_ddl("orders"):
        op.execute(stmt)

# In request/transaction setup, before issuing tenant-scoped queries:
async with session.begin():
    await set_tenant_local(session, tenant_id)
    # ... queries within this transaction only see tenant_id's rows
```

`enable_rls_ddl(table, *, tenant_column="tenant_id", setting="rls.tenant_id",
policy_name=None, cast_type="uuid")` returns three DDL statements in order:
`ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY` (without `FORCE`,
Postgres exempts the table owner — often the migration/ORM role — from the
policy entirely, which is itself a silent bypass — **but see the superuser
caveat below**, `FORCE` does not close every exemption), and `CREATE POLICY`
with the InitPlan-form `USING`/`WITH CHECK` clause. It performs no I/O — the
caller runs the statements inside their own Alembic revision.

`cast_type` controls what Postgres type the (always-`text`) GUC value is
cast to before comparison with `tenant_column`: default `"uuid"` matches the
common case of a real `UUID` tenant column. **Pass `cast_type="text"` for a
`VARCHAR`/`TEXT` tenant column** — a mismatched cast aborts the migration
with `operator does not exist: character varying = uuid`. This is not a
theoretical footgun: varco's own two framework tables
(`varco_audit_log`, `varco_dead_letters`) declare `tenant_id` as
`String(255)` (`AuditEntry.tenant_id`/`DeadLetterEntry.tenant_id` are
`str | None`, never `UUID`), so `varco_sa.rls_framework.framework_rls_upgrade()`
defaults to `cast_type="text"` rather than inheriting `enable_rls_ddl()`'s
`"uuid"` default — see `varco_sa/varco_sa/rls_framework.py`. Before this
parameter existed, `framework_rls_upgrade()` could not apply at all: every
call aborted with the cast error above.

**RLS does not stop a superuser or a `rolbypassrls` role — this is a hard
Postgres rule, not a varco gap.** `FORCE ROW LEVEL SECURITY` only revokes
the *table-owner* exemption; `rolbypassrls`/superuser connections bypass RLS
**unconditionally**, `FORCE` or not. A test (or an operator) that connects
as the database's own superuser role — the default for most local/CI
Postgres containers — will see RLS appear to do nothing, not because the
policy is broken but because the connecting role was never subject to it.
Verify RLS behaviour (and write RLS integration tests) using a dedicated
non-superuser, non-`BYPASSRLS` application role; see
`varco_sa/tests/test_rls.py`/`test_framework_rls.py` for the fixture that
provisions one.

`set_tenant_local(session, tenant_id, *, setting="rls.tenant_id")` executes a
single `SELECT set_config(:setting, :value, true)` — call it as the first
statement inside the transaction whose queries should be tenant-scoped; the
setting does not survive past that transaction (see §2), by design.

## What is opt-in and what is not

Nothing in varco applies an RLS policy to any table it generates.
`enable_rls_ddl()` is a pure DDL-string generator, and `rls_upgrade()` only
runs when an application's own revision calls it — a table has no RLS at
all until an application writes a revision that does so and runs it. In
particular, no `VARCO_MIGRATE_MODE` value enables RLS: `mode="upgrade"`
applies whatever revisions exist, and a revision that does not call
`rls_upgrade()` will never produce a policy. This keeps RLS adoption a per-table, per-application decision
matching each table's actual isolation requirements, rather than a
one-size-fits-all default that could break a table that legitimately needs
cross-tenant reads (e.g. an admin reporting table).

## Pitfalls

| Pitfall | Symptom | Root Cause | Fix |
|---|---|---|---|
| **Hand-written RLS policy uses bare `current_setting(...)`** | A query on an RLS-protected table that flies at test data volumes goes from milliseconds to seconds in production (one documented case: 8 100 ms) | `current_setting()` is `VOLATILE` and not `LEAKPROOF` — without a scalar-subquery wrapper the Postgres planner cannot push the predicate below an index scan and falls back to a sequential scan | Always use `varco_sa.rls.enable_rls_ddl()`, which emits the `(SELECT current_setting(..., true))` InitPlan form; never hand-write `USING (tenant_id = current_setting(...)::uuid)` — see `technical_docs/features/postgres-rls.md` |
| **RLS tenant GUC set with `SET` instead of `SET LOCAL`** | Under a transaction-mode pooler (PgBouncer), one tenant's queries leak into a session that was actually serving a different tenant's next transaction | Session-scoped `SET`/`set_config(..., false)` survives past the transaction on a pooled connection — same defect class as `SAAdvisoryLock`'s session-scoped release (U-16) | Use `varco_sa.rls.set_tenant_local(session, tenant_id)` — `set_config(..., true)` (`is_local`) scopes the setting to the current transaction only |
| **`TenantAwareService._scoped_params` bypassed** | Cross-tenant rows returned from a query path that skipped the service mixin (e.g. a raw repository call, an ad-hoc script) | The mixin fails open by design — it only filters queries that actually go through it, there is no enforcement below the service layer | Enable Postgres RLS as defense-in-depth (`varco_sa.rls.enable_rls_ddl()`) on any table where a query bypassing the service layer would leak data across tenants |
| **`enable_rls_ddl()` on a `VARCHAR`/`TEXT` tenant column** | Every migration using the policy aborts with `operator does not exist: character varying = uuid` — this is exactly what made `varco_sa.rls_framework.framework_rls_upgrade()` inapplicable before its fix | `enable_rls_ddl()`'s `cast_type` defaults to `"uuid"`, matching a real `UUID` tenant column; a `String`/`VARCHAR` column needs the GUC cast to match | Pass `cast_type="text"` (`enable_rls_ddl(..., cast_type="text")`); `framework_rls_upgrade()` already does this for the two framework tables, whose `tenant_id` is `String(255)` |
| **RLS test/connection uses a superuser role** | RLS policies appear to do nothing — every row is visible regardless of the tenant GUC — even though `pg_class.relforcerowsecurity` is `True` and the policy is correctly applied | `FORCE ROW LEVEL SECURITY` only revokes the *table-owner* exemption; `rolbypassrls`/superuser connections bypass RLS **unconditionally**, `FORCE` or not — this is a hard Postgres rule, not a varco gap | Connect (and write RLS tests) as a dedicated non-superuser, non-`BYPASSRLS` application role — see `varco_sa/tests/test_rls.py`/`test_framework_rls.py`'s fixture |
| **RLS enabled by a startup hook** | Policies appear/disappear depending on which process booted last; unreviewed DDL in production | RLS is schema DDL that must be ordered after table creation and reviewed like any other change | Put it in a reviewed revision with `varco_sa.migration.ops.rls_upgrade(op, "orders")` / `rls_downgrade`. Nothing in varco auto-enables RLS, and no `VARCO_MIGRATE_MODE` value does either |
