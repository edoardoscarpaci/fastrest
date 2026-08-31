# Measurement — `warnings.warn` sites: deprecation vs. operational warning

**Plan 022 / Phase 0, Step 7.** Feeds §D-DEP: *only* the deprecation sites
migrate onto `varco_core/deprecation.py` in Phase 2.

## ⚠️ The plan's "six known sites" is wrong — there are **three**

§D-DEP claims the new mechanism *"Replaces six ad-hoc `warnings.warn` sites
(`varco_beanie/factory.py`, `varco_core/event/consumer.py`,
`varco_core/job/base.py`, `varco_core/tenancy/control/consumer.py`,
`varco_core/exception/codes.py`, `varco_core/exception/http.py`)"*.

Measured, per the plan's own Step 7 command:

```
$ rg -n 'warnings\.warn' varco_*/varco_*
varco_core/varco_core/event/consumer.py:833
varco_core/varco_core/tenancy/control/consumer.py:173
varco_beanie/varco_beanie/factory.py:144
varco_beanie/varco_beanie/factory.py:21     <- prose inside a module docstring, not a call
```

**Three call sites, not six.** The other three files named by §D-DEP
(`job/base.py`, `exception/codes.py`, `exception/http.py`) contain the *string*
`DeprecationWarning` only inside comments and docstrings, each explicitly
saying that **no** warning is emitted. Verified line by line:

| File | Line | What is actually there |
|---|---|---|
| `varco_core/job/base.py` | `:172`, `:234`, `:332` | `Job.request_token` is documented as **"Discouraged"** with *"no `DeprecationWarning`, no removal scheduled"*. A deliberate non-warning (U-19). |
| `varco_core/exception/codes.py` | `:219` | `VarcoErrorCodes = FastrestErrorCodes` — *"A bare alias to the IDENTICAL class object — not a subclass, no `DeprecationWarning`"* (Plan 011 D-5). |
| `varco_core/exception/http.py` | `:259` | The `translator=` parameter is *"Superseded by `message_resolver` — kept working with no `DeprecationWarning`"*. |

Consequence for §D-DEP's ✅ bullet *"a strict surface reduction in concept
count"*: the reduction is **3 → 1**, not 6 → 1. The argument survives, but at
half the claimed size. Recorded rather than quietly restated (U-8).

## Classification of the three real sites

| Site | Category | Warning class today | Migrates in Phase 2? |
|---|---|---|---|
| `varco_core/tenancy/control/consumer.py:173` | **deprecation** | `DeprecationWarning`, `stacklevel=2` | ✅ **Yes** — the only genuine one. |
| `varco_core/event/consumer.py:833` | **operational** | `RuntimeWarning`, `stacklevel=2` | ❌ No. |
| `varco_beanie/factory.py:144` | **operational** | *default* (`UserWarning`), `stacklevel=2` | ❌ No. |

### `varco_core/tenancy/control/consumer.py:173` — deprecation ✅

`TenantProvisionConsumer(provisioner=..., catalog=...)` is named in its own
message as *"a deprecated shim (Plan 008 RD-12)"*, and states the removal
window in prose: *"This shim will be removed one minor release after Plan 008
lands."* This is exactly the discipline §D-DEP's required `removed_in=`
argument exists to make greppable — today the version is unspecified English,
and no `rg` can find it. Migrating this one site is the whole justification for
the mechanism.

### `varco_core/event/consumer.py:833` — operational ❌

`register_to()` called twice on the same bus instance. Nothing is deprecated:
the API is current and correct: the *call pattern* is a wiring bug that would
double-deliver every event. `RuntimeWarning` is the right category and
converting it to `DeprecationWarning` would be actively wrong — `DeprecationWarning`
is hidden by default outside `__main__`, so the misconfiguration would go
silent.

### `varco_beanie/factory.py:144` — operational ❌

`CheckConstraint(...)` on a Beanie/MongoDB model is *"not supported on MongoDB
and will be ignored"*. A capability gap in one backend, not a deprecated
varco API — the same `DomainCheckConstraint` is fully supported by `varco_sa`.
Nothing is being removed.

⚠️ Minor defect noted in passing (not a Plan 022 item): this site passes **no**
warning category, so it defaults to `UserWarning`. `RuntimeWarning` would
match the `consumer.py:833` precedent for "your configuration will not do what
you think". One-line fix, no API change — worth a BACKLOG row, not a break.

## Bottom line for Phase 2

**One** site migrates. §D-DEP's precondition (build the mechanism only if the
checkpoint accepts ≥1 break that ships an alias) is unchanged by this — but the
"replaces six ad-hoc sites" benefit should be restated as "replaces one, and
gives the other two nothing to change".
