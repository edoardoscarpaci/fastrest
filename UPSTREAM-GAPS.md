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
| **P24-DISPOSES-FIRSTMATCH** | `providify` 2.0.1 | `DIContainer.install()`'s `@Disposes` wiring loop (`providify/container.py:6201-6214`) attaches a disposer to the **first** matching `ProviderBinding` across the **whole container**, not the installing module's own binding — when two `@Configuration`s both bind the same interface via `@Provider`, each with its own `@Disposes`, the second `install()` overwrites the first binding's disposer and leaves the second binding's own instance with no teardown path at all | [providify-disposes-first-match.md](design/upstream-gaps/providify-disposes-first-match.md) | `varco_redis/tests/test_redis_cache_disposes.py::test_both_cache_configurations_installed_together_both_get_stopped` (strict xfail, no Docker, every `make test`) |

## Recently closed

*(Kept only until the next clearing — a closed row's evidence lives in its report
file and in the CHANGELOG, not here.)*

- **P22-PROVIDER-PREDESTROY** — resolved 2026-09-02 (Plan 024 / C2). providify
  2.0.1 declared the leaked-teardown behaviour intentional (Jakarta CDI
  producer-method rule) and shipped only a `WARNING`-severity detector
  (`IssueKind.UNREACHABLE_PRE_DESTROY`); varco adopted `@Disposes` — upstream's
  own supported teardown mechanism — at all nine affected sites. See
  [providify-provider-predestroy.md](design/upstream-gaps/providify-provider-predestroy.md)
  §8 for the full resolution.

---

**Last updated:** 2026-09-02 · Recreated after the `cae7f33` deletion, as a thin
index over `design/upstream-gaps/` rather than the inline-body document it used
to be (Plan 022 closeout). P22-PROVIDER-PREDESTROY closed by Plan 024; opened
with **P24-DISPOSES-FIRSTMATCH**.
