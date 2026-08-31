# Upstream gaps — ledger

**This file is an index, not a record.** Every gap's actual content — reproduction,
evidence, the ask, the interim workaround — lives in its own file under
[`design/upstream-gaps/`](design/upstream-gaps/). This file only points at them.

> ## ⚠️ This ledger is disposable, and its absence is expected
>
> It is **cleared from time to time** — wiped wholesale once a batch of gaps is
> resolved upstream, so the index does not accumulate closed rows forever.
>
> **If this file is missing, nothing is wrong.** It was cleared. Recreate it from
> this template and add your row. Do not go looking for what happened to it, and
> do not treat its absence as a sign that gap-filing has been abandoned.
>
> **Nothing is lost when it is cleared**, because it never held the only copy of
> anything: the per-gap files under `design/upstream-gaps/` are the durable
> artifacts and are never deleted. That is the whole point of the split. This
> ledger can always be rebuilt by listing that directory:
>
> ```bash
> ls design/upstream-gaps/*.md
> ```
>
> A previous incarnation of this file inlined every entry's full body. It was
> deleted in `cae7f33` and took all of them with it — which is precisely the
> failure mode this structure exists to prevent.

---

## How to file a gap

Full workflow, including when *not* to file: CLAUDE.md's
*"When you hit a `providify` limitation or bug"*.

1. **Verify the claim in the upstream library's own source**, citing `file:line`.
   Never file off a `CLAUDE.md`/`README.md`/docstring claim — the register's
   standing U-8 lesson is that entries filed off documentation did not survive
   contact with source. A docstring that *contradicts* the source is itself
   evidence, but quote both.
2. **Write `design/upstream-gaps/<library>-<short-slug>.md`.** This is the
   durable artifact. Cover: what upstream does today (with `file:line`), why it
   is a gap, a minimal reproduction, the ask (with ✅/❌ per candidate fix), and
   any interim workaround. If the gap is *partly ours*, say so plainly and in its
   own section — a report that blames upstream for something we can fix today is
   worse than no report.
3. **Guard it with a `strict=True` xfail** so the fix cannot land unnoticed and
   untested. Prefer a fast, dependency-free reproduction that runs in
   `make test` over one gated behind Docker and `-m integration`.
4. **Add a row below.** If this file does not exist, create it from this
   template — that is the normal path, not an error.

---

## Open gaps

| ID | Library | Gap | Report | Guard |
|---|---|---|---|---|
| **P22-PROVIDER-PREDESTROY** | `providify` 2.0.0 | `container.ashutdown()` silently never runs the `@PreDestroy` hook of an instance produced by a `@Provider` — `_adispose()` (`providify/container.py:4550-4582`) returns early for a `ProviderBinding` whose `@Disposes` is unset, so `binding.pre_destroy` is consulted only for a `ClassBinding`. Contradicts `@PreDestroy`'s own docstring (`providify/decorator/lifecycle.py:161-170`, "called on shutdown or scope teardown", no binding-kind caveat), and no `IssueKind` covers it so `container.validate()` reports clean. ⚠️ **Partly ours** — varco can close its own leak today with a `@Disposes`, no upstream change needed; see the report's §5 | [providify-provider-predestroy.md](design/upstream-gaps/providify-provider-predestroy.md) | `varco_core/tests/test_providify_provider_predestroy.py` (strict xfail, no Docker, every `make test`) · `varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py` (strict xfail, real container) |

## Recently closed

*(Kept only until the next clearing — a closed row's evidence lives in its report
file and in the CHANGELOG, not here.)*

None.

---

**Last updated:** 2026-08-31 · Recreated after the `cae7f33` deletion, as a thin
index over `design/upstream-gaps/` rather than the inline-body document it used
to be (Plan 022 closeout). Opened with **P22-PROVIDER-PREDESTROY**.
