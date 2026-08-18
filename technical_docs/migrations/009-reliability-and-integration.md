# Migration Note — Plan 009: Reliability & Service Integration

Consolidated breaking-change note for Plan 009 (`plans/009-reliability-and-service-integration.md`).
**Nothing in this release changes default runtime behaviour without an
explicit opt-in** — `ReliabilityPreset.off()` is the default preset, metrics
install only when asked, RLS is never auto-enabled, the Beanie DLQ has no TTL
index by default, and the reliability admin surface mounts only via an
explicit call with an explicit acknowledgement.

| # | Change | Who breaks | Fix |
|---|---|---|---|
| 1 | `AuditRepository.list_for_entity()` gains keyword-only `tenant_id: str \| None = None` | Out-of-tree `AuditRepository` subclasses | Add the parameter to your override and filter on it. Ignoring it is the security bug this fixes. |
| 2 | `AbstractDeadLetterQueue` gains 6 members (`supports_random_access`, `get`, `list_entries`, `delete`, `delete_where`, `count_by_channel`) | Nobody at import time — all are concrete (raising or defaulted) | Implement the ones your operators need. `delete()` already works via `ack()`. |
| 3 | `AuditRepository` gains `list()`, `delete_where()`, `verify_chain()` | Nobody at import time — concrete-but-raising / portable default | Implement `list()` if you mount `build_audit_router`. |
| 4 | `OutboxRepository` gains `count_pending()`, `oldest_pending_at()` | Nobody — concrete-but-raising; the gauge self-disables | Implement for outbox lag alerting. |
| 5 | Client custom-route methods change from `**kwargs: Any` to a synthesized signature | Callers passing undeclared kwargs, or passing declared ones positionally | Everything except the request body is now keyword-only. A wrong kwarg is now a `TypeError` — that is the feature. |
| 6 | `make_client`, `GenericClient`, `OpenAPIClient`, `ClientConfigurator`, `generate_client` removed from `varco_fastapi.client.__all__` | `from varco_fastapi.client import GenericClient` | Import from `varco_fastapi.client.advanced` (or the original module). The `AttributeError` message names the new path. |
| 7 | `@listen`'s `retry_policy=`/`dlq=` defaults become an internal `_UNSET` sentinel | Code introspecting `@listen`'s defaults (very unlikely) | Passing `None` explicitly still means "no retry"; omitting now means "use the global preset", which defaults to `off()`. |
| 8 | `DeadLetterEntry` gains `tenant_id: str \| None = None` (appended, defaulted) | Positional construction beyond the last field (none exists) | None. Backends persisting entries need a `tenant_id` column — the shipped Alembic revision handles `varco_sa`. |
| 9 | New Alembic revisions on the `varco` branch (`tenant_id` on `varco_dead_letters` and `varco_audit_log`; optional audit chain columns on `varco_audit_log`) | Deployments on `varco migrate` | `varco migrate upgrade` (always `heads`, plural). `ensure_table()` deployments: `varco migrate adopt` first, then upgrade. |
| 10 | `mount_reliability_admin` requires `acknowledge_bundled_admin=True` | New API — nobody | Pass it, or run the admin surface standalone. |

## ⚠️ Item 5 — verified status differs from the plan's own success criterion

**Read this before relying on item 5 for `client_for()`.** The typed,
keyword-only client-method signature described in row 5 is fully implemented
and tested for:

- `varco gen-client` / the generated module's client class, and
- `contract_client()` (`varco_fastapi.contract.runtime`) — the cross-repo
  runtime path.

**It is NOT yet wired into `_VarcoClientMeta`** (`varco_fastapi/client/base.py`)
— the metaclass that builds the client returned by `client_for()` for an
**importable** router. Both `client_for()`'s CRUD methods and its custom
`@route` methods still use the pre-Plan-009
`custom_method(self, **kwargs: Any)` closure. This means:

- A `client_for(OrderRouter, ...)` call site does **not** get the new
  `TypeError`-on-wrong-kwarg behaviour yet — the old permissive `**kwargs`
  pass-through is still in effect for the in-process path.
- A cross-repo-generated client (`gen-client`) for the exact same router
  **does** get the new typed, keyword-only signature.

This is a deliberate, plan-acknowledged deferral (the plan's own Risks
section calls the `_VarcoClientMeta` rewrite "the highest-blast-radius
change" and prescribes exactly this two-commit split), not an oversight —
but it means the two client-construction paths for the **same router** are
not yet behaviourally identical. Track `_VarcoClientMeta`'s own migration to
`build_client_method` as unfinished follow-up work before treating row 5 as
fully landed. See `technical_docs/features/portable-contracts.md`'s status
note for the full detail and `docs/client-code-generation.md` for the
practical consequence at a call site.

## Verified-but-integration-only components

Two components in this release are logic-complete, unit-reviewed, and
covered by written tests, but those tests are `@pytest.mark.integration`
(require Docker) and were **not run against a live backend in this session**
(no Docker available):

- `varco_beanie.dlq.BeanieDeadLetterQueue` — `varco_beanie/tests/test_beanie_dlq.py`
- `varco_beanie.audit.BeanieAuditRepository(hash_chain=True)` — `varco_beanie/tests/test_beanie_audit_chain.py`

Run `uv run pytest varco_beanie/tests/ -m integration` against a real
MongoDB container before treating either as production-verified.

## Upgrade checklist

```bash
# 1. Alembic — always heads, plural (the varco branch ships its own revisions)
varco migrate upgrade -t myapp.db:migrator   # or `varco migrate adopt` first if
                                              # your deployment used ensure_table()

# 2. Audit an out-of-tree AuditRepository subclass for the new
#    list_for_entity(..., tenant_id=) parameter (item 1) and, if you mount
#    build_audit_router, implement list() (item 3).

# 3. Audit any code that constructs AbstractDeadLetterQueue.delete_where()/
#    get()/list_entries() calls expecting a portable default — only delete()
#    has one; the rest raise NotImplementedError unless your backend
#    implements them (SA/Redis/Beanie do; Kafka/NATS deliberately don't).

# 4. Grep for `from varco_fastapi.client import` — GenericClient/OpenAPIClient/
#    ClientConfigurator/make_client/generate_client now raise AttributeError
#    naming varco_fastapi.client.advanced.

# 5. If you call a custom @route through a *cross-repo generated* client,
#    switch any positional kwargs (other than the body) to keyword form —
#    they now bind by name and reject unknown kwargs.
```

See also `plans/009-reliability-and-service-integration.md` for the full
phase-by-phase design rationale and the Resolved Decisions (RD-1 through
RD-9) this note summarizes.
