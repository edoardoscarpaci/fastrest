# Plan 022 — API freeze prep (RL-8 / RL-8a) + standards alignment (CloudEvents / AsyncAPI / OpenFeature)

> ## ✅ CLOSED — 2026-08-31
>
> **Phases 0–5 and 8 executed. Phases 6–7 deferred to 3.1 by decision at Step 30**, with their
> design finished and their seams reserved. Both goals stated below are met:
>
> 1. **The 3.0.0 breaking-change window is closed.** RL-8 and RL-8a are ✅ DONE. 12 candidates
>    audited against a reproducible 471-export snapshot; **4 accepted** (AB-1/AB-2/AB-4 with
>    deprecated aliases, AB-5 — the CORS security default — without, as no alias is possible for a
>    default value), **8 `leave-and-document`**, **0 rejected**. RL-8a was decided on measurement,
>    not suspicion: **6 orphaned `@PreDestroy` singletons of 10**, and `container.ashutdown()` was
>    adopted via a `shutdown=` hook symmetric to `setup=`. **RL-9 (version freeze) is unblocked.**
> 2. **The standards position is decided.** CloudEvents and AsyncAPI un-parked, scoped, and
>    deferred to 3.1 with a written seam reservation (`reserved-seams.md`) that makes the deferral
>    cost zero deprecation cycles. OpenFeature re-parked with a falsifiable two-clause trigger.
>
> ⚠️ **Two findings inherited, not fixed** — filed with strict-xfail guards, neither a freeze or
> SemVer concern, neither blocking RL-9: `P22-PROVIDER-PREDESTROY` (two caches still leak a
> started connection pool; `design/upstream-gaps/providify-provider-predestroy.md` records a **varco-side
> `@Disposes` fix needing no upstream change**) and `P22-REDIS-DOUBLE-BUS`.

## Goal

Two things exist after this plan:

1. **A closed 3.0.0 breaking-change window.** BACKLOG rows **RL-8** and **RL-8a** are resolved: a
   reproducible, committed snapshot of the public surface of all ten packages exists; a ranked
   candidate-break list has been through an explicit **user accept/reject checkpoint**; every
   accepted break has landed; `VarcoLifespan`'s relationship to `container.ashutdown()` is
   decided on measured evidence rather than suspicion. Phase 5 (RL-9 version freeze) is unblocked.
2. **A decided standards position.** The Parked row *"Standards alignment — CloudEvents envelope,
   AsyncAPI export, OpenFeature integration"* is un-parked and resolved item-by-item: CloudEvents
   and AsyncAPI ship as **purely additive** work with a written seam reservation so they never
   need a break; OpenFeature is **re-parked with a dated, falsifiable trigger** rather than built
   against a pre-1.0 spec inside a version freeze.

## Organizing principle (read this before anything else)

> **3.0.0 is the last cheap breaking-change window** (BACKLOG.md:184). Anything the standards
> work needs to *change* in the public surface must land before the freeze. Anything purely
> additive can land after, at zero cost.

Applying that test honestly, item by item, produces the plan's central and slightly
counter-intuitive finding:

| Item | Changes public surface? | Freeze-window? |
|---|---|---|
| RL-8 accepted renames / removals | **Yes, by construction** | 🔴 **Must precede freeze** |
| RL-8a `container.ashutdown()` adoption | Signature additive (`shutdown=` kwarg), **behaviour breaking** (new teardown runs) | 🔴 **Must precede freeze** |
| CloudEvents structured envelope | No — a second `Serializer[Event]` implementation (§D-CE1) | 🟢 Additive; may slip to 3.1 free |
| CloudEvents binary / header mode | No, **given §D-CE2's routing decision** (new optional Protocol + defaulted kwarg, *not* an `AbstractEventBus.publish()` signature change) | 🟢 Additive — **because we decide the routing now** |
| AsyncAPI 3.1.0 export | No — new module + new `varco.commands` verb | 🟢 Additive; may slip to 3.1 free |
| OpenFeature | Would add a **frozen** ABC derived from a pre-1.0 spec | ⛔ Not built (§D-OF) |

**Therefore: the standards work does not need the freeze window, and this plan does not pad it
into one.** What it *does* need from the window is a one-page **written seam reservation**
(Phase 5) recording the three decisions above, so that shipping CloudEvents/AsyncAPI in 3.1
provably costs no deprecation cycle. That reservation is a document, not code, and it is the
only standards artifact that is freeze-critical.

**Consequence of the un-parking, stated explicitly (the reversal the user asked for).** The
BACKLOG's Locked-decisions table says *"Standards-alignment work (CloudEvents/AsyncAPI/OpenFeature)
parked to 3.1"*. That is now reversed: the work is **scoped, decided and planned here**, and
two-thirds of it is implementable immediately. It is reversed on the merits — the analysis above
shows the park was protecting the freeze window from work that never threatened it. The park's
*effect* on 3.0.0's ship date is preserved by the phase order: **Phases 6–8 are explicitly
non-blocking for RL-9**, and a reviewer may cut them at the Phase 5 boundary without reopening a
single decision.

---

## Non-goals

- **No break lands without the Phase 1 checkpoint.** BACKLOG.md:237-239 requires an explicit
  accept/reject gate ("each one spends irreplaceable 3.0.0 budget"). This plan proposes and ranks;
  it does not pre-approve. Phase 3 is unexecutable until the gate is passed.
- **The two BREAKING JWT security defaults are not relitigated.** `VARCO_JWT_ALLOW_ANY_AUDIENCE=false`
  and `VARCO_JWT_ENFORCE_ISS=true` are already hardened (CLAUDE.md §"Two BREAKING security
  defaults"). They are excluded from the fail-open enumeration by construction.
- **No written SemVer/deprecation *policy*.** That is RL-9 (Phase 5 of the BACKLOG). This plan
  builds the *mechanism* only, and only if §D-DEP's precondition is met.
- **No OpenFeature code**, not even an ABC. See §D-OF.
- **No CloudEvents SDK dependency**, anywhere — including in a would-be `varco_cloudevents`
  package. See §D-CE3.
- **No AsyncAPI *validator* written in Python.** Brief 002 §5 establishes none exists; the gate is
  snapshot-diff, and Node-based `asyncapi validate` is opt-in local tooling, never a CI gate.
- **No version bump, no `Development Status` change, no release automation.** All RL-9/RL-10.
- **No fix for KI-12, RT7b-kafka-restart-recovery, RT9-beanie-index-mode, or any other open
  BACKLOG defect** encountered in passing. Scope-guard discipline: file, do not freelance.

---

## Design

### §D-AUDIT — RL-8's audit is a committed snapshot artifact, not an eyeball pass

The audit must be reproducible or it is worthless the second time it is needed. Build
`scripts/api_surface.py`: for each of the ten packages it imports the top-level module, walks
`__all__`, and emits for every exported name its kind (class / function / alias / constant), its
defining module, and — for callables — `inspect.signature()` rendered as a string. Output goes to
`design/api-freeze-and-standards/measurements/api-surface.json` (machine) and a sibling `.md`
(human, sorted, diffable).

DESIGN: snapshot artifact over a one-off review
- ✅ Reproducible. The same command re-run after any edit shows exactly what moved.
- ✅ **Reusable as the post-freeze break detector.** `scripts/api_surface.py --check` diffs against
  the committed snapshot and fails on any removal or signature narrowing — which is precisely the
  enforcement RL-9's deprecation policy will otherwise have to invent from scratch. Same
  snapshot-plus-`--check` shape as §D-AA4's AsyncAPI gate and the OpenAPI precedent brief 002 §6
  names.
- ✅ Scales: `varco_core.__all__` is 243 symbols (`varco_core/__init__.py:377-643`) and
  `varco_fastapi.__all__` is 125 (`varco_fastapi/__init__.py:241-365`) — 400+ names is past the
  point where an eyeball pass is honest.
- ❌ `inspect.signature()` under `from __future__ import annotations` renders annotations as
  strings, so a *semantic* narrowing that keeps the same source text is invisible. Accepted: the
  detector's job is removals and shape changes, and mypy already owns semantic typing (Plan 021).
- ❌ A new script to maintain. Mitigated: it derives its package list from `scripts/packages.sh`
  (Plan 020 / RL-18), so it structurally cannot drift like the four hand-written lists did.

#### Alternatives considered

- **`griffe` / `pdoc` API-diff tooling**: rejected — ✅ mature, understands annotations properly;
  ❌ a new dev dependency and a new failure mode inside the very phase whose job is to *shrink*
  dependency and surface risk, for a one-time need `inspect` covers.
- **Read `__all__` lists by hand and write the table**: rejected — ❌ not reproducible, and
  BACKLOG.md's own RL-20/RL-21 lesson is that unrepeatable observations produce false findings.

### §D-RANK — the candidate list format, and the checkpoint that gates it

`design/api-freeze-and-standards/api-break-candidates.md`, one row per candidate:

| Col | Meaning |
|---|---|
| `AB-n` | Stable ID, cited in the commit that lands it |
| Symbol | Fully qualified name + `file:line` |
| Category | `collision` / `verb-taxonomy` / `fail-open` / `duplicate-value-object` / `naming` |
| Proposal | `rename+alias` / `rename-hard` / `remove` / `leave-and-document` |
| Blast radius | Measured: in-tree source call sites (`rg`, excluding `plans/`, `audits/`, `design/`), plus docs/test sites counted separately |
| Cost if deferred | Concretely: which post-3.0.0 deprecation cycle it would need |
| Verdict | **empty until the checkpoint** |

**⛔ The checkpoint is a hard stop.** Phase 1 ends by presenting this file. No Phase 3 step may
execute until every row's Verdict column is filled by the user. A plan step that lands a break on
an empty Verdict is a plan violation, not a judgement call.

### §D-C1 — Collision 1: `enable_rls_ddl()` outside the `enable_*` family → **propose rename + alias**

Verified in source: `varco_sa/varco_sa/rls.py:71-78` is a pure DDL-string generator
(`-> list[str]`), takes no container, and its own docstring at `:82-84` says *"Nothing is applied
here — this function performs no I/O. Despite the…"* — the file already knows it is misnamed.
Measured blast radius: 94 total occurrences across 23 files, of which the **source** sites are
`varco_sa/migration/ops.py` (4), `varco_sa/rls_framework.py` (6), `varco_sa/rls.py` (5); the rest
are tests (23), docs (20) and historical plans/audits (~25, never edited).

Proposal to the checkpoint: rename to **`render_rls_ddl()`**, keep `enable_rls_ddl` as a
`@deprecated` alias (§D-DEP).

- ✅ Removes a wart CLAUDE.md's taxonomy section says it "exists specifically to call out" — a
  documented wart is a wart worth deleting while it is free.
- ✅ `render_*` states the shape truthfully (returns strings, no side effect) and collides with no
  existing verb family.
- ❌ Spends breaking budget on a cosmetic issue with **zero** correctness impact.
- ❌ 15 source sites plus 23 test sites is a real, if mechanical, diff.

### §D-C2 — Collision 2: dual `MigrationError`/`MigrationPlan` → **propose rename the schema pair + re-export**

Two unrelated pairs coexist: domain data/field migration at `varco_core/migrator.py:89` and `:167`,
schema migration at `varco_core/migration/errors.py:23` and `varco_core/migration/base.py:59`.
`varco_core/__init__.py` deliberately re-exports only the *older* pair, with an explanatory note at
`:246-250` and holes at `:404-406` / `:632-633`. CLAUDE.md carries a ⚠️ warning about the
non-re-export.

Proposal to the checkpoint: rename the **schema** pair to `SchemaMigrationError` /
`SchemaMigrationPlan`, re-export both from `varco_core`, and keep
`varco_core.migration.MigrationError` / `.MigrationPlan` as deprecated aliases.

- ✅ Deletes a ⚠️ from CLAUDE.md and two deliberate holes from `varco_core/__init__.py` — the
  top-level namespace becomes uniform again.
- ✅ Renames the **newer, narrower** pair (Plan 006 vintage, one subsystem) rather than the older
  domain-migration pair, which is the smaller blast radius by construction.
- ✅ `Schema*` prefixed names are self-documenting at an import site where today the reader must
  know which module a bare `MigrationError` came from.
- ❌ Any downstream doing `from varco_core.migration import MigrationError` gets a
  `DeprecationWarning`; a `except MigrationError` clause keeps working (alias is the same object).
- ❌ Alternative reading: neither name is *wrong* in its own namespace, and Python routinely
  tolerates this (`json.JSONDecodeError` vs `xml`'s). Recorded so the checkpoint can reject on it.

### §D-C3 — Collision 3: `install_*` vs providify's `container.install()` → **propose leave-and-document, plus a taxonomy correction**

Proposal: **leave**. Renaming four functions to fix a naming *adjacency* that CLAUDE.md's taxonomy
table already resolves in one row is a poor use of the window.

- ✅ Zero cost, zero risk, already documented.
- ❌ The confusion persists for anyone who skips the table.

**But the audit surfaces a genuine documentation defect, free to fix.** CLAUDE.md's taxonomy row
says `install_*` is "sync, **container-free** … a process-global side effect". That is true of
`install_cache_metrics` (`varco_core/observability/cache.py:203`) and `install_reliability_metrics`
(`varco_core/observability/reliability.py:373`) — and **false** of `install_middleware_stack`
(`varco_fastapi/middleware/__init__.py:75`) and `install_cors`
(`varco_fastapi/middleware/cors.py:212`), which take and mutate an ASGI `app`. `install_*` is two
shapes under one verb. Fix: amend the taxonomy row to state both shapes (docs-only, free, not a
break). Do **not** move the two app-taking functions into the `mount_*` family — `mount_*` is
documented as "an opt-in privileged HTTP surface, always behind an explicit acknowledgement
kwarg", which middleware installation is not.

### §D-C4 — `BeanieConfig` / `BeanieSettings` → **propose collapse**, deferring to the checkpoint

BACKLOG's `BEANIE-CFG` row explicitly routes here: "Collapsing them deletes exported public API
(`varco_beanie/__init__.py:46`) and belongs in the **RL-8 API-surface audit**, before the 3.0.0
version freeze." KI-10's fix already maps one onto the other field-for-field, which is the
strongest possible evidence they are one concept.

Proposal: keep `BeanieSettings` (the `BaseSettings` one, DI-registered), make `BeanieConfig` a
deprecated alias, and have `BeanieFastrestApp` construct `BeanieSettings` directly.

- ✅ Deletes a duplicate value object from the public surface at the one moment it is free.
- ✅ Removes the field-for-field mapping KI-10 had to write, which is pure carrying cost.
- ❌ `BeanieConfig` may be the shape a non-DI caller actually wants (plain dataclass, no env
  reading). If the checkpoint sees that as load-bearing, the verdict is `leave-and-document`.

### §D-FAILOPEN — fail-open defaults get an enumeration step, not a guess

The scout did not exhaustively enumerate them, and neither does this plan. The enumeration is
**Step 4**, and it is mechanical: introspect every `pydantic_settings.BaseSettings` subclass in the
ten packages (`__mro__` walk, not `rg`, so subclasses of subclasses are caught), and for each field
whose default disables a safety / isolation / durability / verification property, emit a row into
the same §D-RANK table with category `fail-open`.

Known going in, and their prior:
- `TenancySettings`: `isolation=SHARED`, `enforce_rls=False`, `fanout_framework_tables=False`.
  Prior = **leave**. CLAUDE.md documents "Default is byte-identical to pre-Plan-007 behaviour" as a
  deliberate contract, and `SHARED` is a *deployment strategy* default, not a security failure. The
  arguable one is `enforce_rls=False`; it goes to the checkpoint as its own row.
- JWT: **excluded** (Non-goals).
- ⚠️ Everything else is unknown until Step 4 runs. The plan does not pretend otherwise — see Risks.

### §D-DEP — a minimal deprecation *mechanism*, conditional on the checkpoint

**Precondition**: build this only if the checkpoint accepts ≥1 break that ships an alias
(§D-C1/§D-C2/§D-C4 all would). If every accepted verdict is `remove` or `leave-and-document`, skip
Phase 2 entirely.

Shape: one new module `varco_core/deprecation.py` exporting `deprecated(*, since, removed_in,
replacement)` (decorator for functions/classes) and `deprecated_alias(name, target, *, since,
removed_in)` (module-level alias factory). Emits `DeprecationWarning` with `stacklevel` set so the
warning points at the *caller*, once per call site.

DESIGN: one mechanism now, policy later
- ⚠️ **CORRECTED at Phase 0 Step 7 (measured, U-8).** This bullet claimed six ad-hoc
  `warnings.warn` sites. There are **three** in the whole tree, and only **one** is a
  deprecation: `varco_core/tenancy/control/consumer.py:173`. `varco_core/event/consumer.py:833`
  (`RuntimeWarning`, double `register_to()`) and `varco_beanie/factory.py:144` (`UserWarning`,
  MongoDB cannot do `CheckConstraint`) are *operational* warnings and stay. The other three files
  named above contain the string `DeprecationWarning` only in prose that says no warning is
  emitted. So the surface reduction is **3 → 1, not 6 → 1** — the argument survives at half its
  claimed size.
- ⚠️ **Second correction, at Phase 2 Step 14.** Even that one site was **not** migrated onto
  `@deprecated`, and could not be: the deprecation is *argument-conditional* (only the
  `provisioner=`/`catalog=` shape is deprecated, while `control_service=` is the supported path
  through the same constructor), whereas `@deprecated` decorates a whole callable and would warn
  on every construction including the correct one. What §D-DEP actually buys was applied by hand
  instead — the message now names a concrete `removed_in` version (4.0.0) rather than the
  unfalsifiable "one minor release after Plan 008 lands" it carried before.
- ✅ RL-9 needs it regardless; building it here means RL-9 writes prose, not code.
- ✅ `removed_in=` forces every deprecation to name its removal version at authoring time, which is
  the one discipline ad-hoc `warnings.warn` cannot enforce.
- ❌ Adds a public symbol during an audit whose purpose is to shrink the surface. Accepted: one
  module, two functions, and it is the mechanism that makes every *other* removal cheap.
- ❌ Migrating the six existing sites is churn unrelated to any accepted break. Mitigation: migrate
  them, but as their own commit, and only sites whose semantics are genuinely "deprecated API"
  (some may be operational warnings, not deprecations — check before converting).

#### Alternative considered

- **PEP 702 `warnings.deprecated`**: rejected — ✅ type-checker-visible, stdlib-blessed; ❌ it is in
  stdlib `warnings` only from **Python 3.13**, and every package is `requires-python = ">=3.12"`,
  so adopting it means a new `typing_extensions` **runtime** dependency. This is the identical
  trade Plan 021 §PEP 696 rejected, for the identical reason. Record it as the intended migration
  the day the floor moves to 3.13.

### §D-8a1 — RL-8a: prove the premise before deciding it

BACKLOG's RL-8a row carries "⚠️ **Suspected, not proven:** a `@PreDestroy`-bearing singleton that
is not also a registered `VarcoLifespan` component is never torn down today."

Verified half: `varco_fastapi/varco_fastapi/lifespan.py:181-190` — the class's **own docstring
already asserts** it never calls `container.shutdown()`/`ashutdown()`, and `_stop_all()`
(`:221-233`) iterates only `self._components`. So *"if such a singleton exists, it is never torn
down"* is **proven**.

Unproven half: **that such a singleton actually exists.** That requires cross-checking the ten
`@PreDestroy` classes against what `create_varco_app()` registers — `app.py:420` builds
`VarcoLifespan(*lifespan_components)` and `app.py:716-728` populates that list via four
`_try_resolve_component()` calls (`AbstractEventBus`, `AbstractJobRunner`, and two more). Step 5
produces the table and the plan branches on it. Do **not** carry the suspicion forward as a premise.

### §D-8a2 — RL-8a decision: **adopt, conditional on Step 5**, via a `shutdown=` hook symmetric to `setup=`

Recommendation: **yes, adopt `container.ashutdown()`** — provided Step 5 finds ≥1 orphaned
`@PreDestroy` singleton, and Step 6 confirms `stop()` idempotency. Three mechanics:

**(a) How the container reaches the lifespan.** Add an optional
`shutdown: Callable[[], Awaitable[None]] | None = None` kwarg to `VarcoLifespan.__init__`,
**exactly symmetric to the existing `setup=` kwarg** at `lifespan.py:124/135`. `create_varco_app()`
— which already holds the container — passes `lambda: container.ashutdown()`.

- ✅ Preserves the DESIGN block at `lifespan.py:131-135` byte-for-byte in spirit: *"Keeps
  VarcoLifespan a plain orchestrator — no DI knowledge."* A callable is not DI knowledge; a
  `container=` kwarg would be.
- ✅ Additive (defaulted kwarg): no existing `VarcoLifespan(...)` call site changes.
- ✅ Testable without providify: pass any coroutine factory.
- ❌ One more optional kwarg on a class that already has one. Accepted — symmetry is the point.
- Rejected alternative — `container=` kwarg: ❌ gives the orchestrator DI knowledge the DESIGN
  block explicitly refuses. Rejected alternative — `DIContainer.current()` lookup inside
  `_stop_all()`: ❌ implicit global state, untestable, and silently wrong in multi-container tests.

**(b) Double-stop.** Order is unchanged LIFO `_stop_all()` **first**, then `await shutdown()`.
Components registered with the lifespan are stopped in dependency order (consumers before bus —
`lifespan.py:40-47` documents why this matters); `ashutdown()` then sweeps whatever the container
holds. Any component in both paths gets `stop()` called twice.

- ✅ Preserves the documented teardown ordering guarantee, which `ashutdown()` alone cannot offer.
- ✅ `register()`'s own docstring (`lifespan.py:148-151`) *already* states "components should be
  idempotent" — this makes an existing documented expectation load-bearing rather than inventing one.
- ❌ Requires the expectation to actually hold. **Step 6 verifies it** by reading all ten `stop()`
  implementations; any non-idempotent one is fixed with a `self._started` guard in its own commit,
  or that component is excluded and the finding filed.
- Rejected alternative — skip `_stop_all()` for container-held singletons: ❌ the lifespan cannot
  know which are container-held without DI knowledge, and it would silently reorder teardown away
  from the documented LIFO dependency order.

**(c) Aggregated `ShutdownError`: log, do not raise.**
`varco_fastapi/tests/test_lifespan_shutdown_characterization.py:55-99` already locks providify
2.0.0's shape (two failing hooks → one aggregated error carrying `ShutdownFailure` entries) and
exists purely to de-risk this decision — wire it in.

- ✅ Consistent with `_stop_all()`'s existing, documented "logs errors but not raising" contract
  (`lifespan.py:222`). Two teardown paths with opposite failure semantics would be indefensible.
- ✅ Raising out of an `asynccontextmanager`'s `finally` during ASGI shutdown produces an
  unactionable traceback that can mask the real cause.
- ❌ A genuine teardown bug is now only visible in logs. Mitigated: log at ERROR, enumerate each
  `ShutdownFailure` individually (component name + exception), never a single opaque line.
- ❌ No knob to make it raise. Deliberate — a knob here is a config surface added during a freeze.

**Breaking classification.** The *signature* change is additive; the *behaviour* change is not —
apps upgrading to 3.0.0 will newly run up to ten `@PreDestroy` hooks at shutdown. That makes this a
🔴 freeze-window item and a **BREAKING CHANGELOG entry**, despite no signature breaking.

**If Step 5 finds zero orphans**, the answer flips to **no**: document the non-adoption in
`lifespan.py`'s docstring (replacing the current "see BACKLOG RL-8 for the adoption decision"
pointer), and record the trigger — *the first `@PreDestroy`-bearing singleton that is not a
registered lifecycle component reopens this*.

### §D-CE1 — CloudEvents seam: a second `Serializer[Event]`, **not** a change to `Event`

`varco_core/varco_core/event/serializer.py:116-117` registers `JsonEventSerializer` as
`@Singleton(priority=-sys.maxsize - 1)` subclassing `Serializer[Event]`, and its module docstring
(`:13-21`) spells out that any app-supplied `Serializer[Event]` wins at any registration order.
That is the seam, already built and already documented.

Ship `varco_core/event/cloudevents.py` with `CloudEventsJsonSerializer(Serializer[Event])`,
registered at **default** priority so it is *not* auto-active — opt in with
`container.provide(cloudevents_serializer)` or by binding it explicitly. Structured content mode
(brief 001 §"Structured vs. Binary"): the whole CloudEvents JSON envelope is the message body.

- ✅ Zero change to `Event` (`event/base.py:240-310`, frozen Pydantic with `__init_subclass__`
  auto-registration) — no field added, no `model_dump()` shape changed, no DLQ/outbox/audit
  consumer affected.
- ✅ Zero change to any bus. Every backend already resolves `Serializer[Event]` through DI.
- ✅ Reversible per deployment: rebind the serializer, restart. Brief 001's "Dual Envelope
  Approach" for free.
- ✅ Zero new public *behaviour* by default — nobody who does not opt in sees a byte change.
- ❌ A CloudEvents-serialized event and a native-serialized event cannot share a channel during
  migration unless the consumer sniffs. Documented, with brief 001 §"Migration Timeline"'s
  three-phase pattern as the recommended rollout.
- Rejected alternative — **add CloudEvents fields to `Event` itself**: ❌ every event in every app
  changes shape; `source` has no correct process-wide default; the DLQ's stored `event_payload`
  (`event/dlq.py`) changes shape retroactively. A frozen base model is the worst possible place for
  an optional envelope.
- Rejected alternative — **a per-bus `cloudevents=True` flag**: ❌ three settings classes to change,
  three code paths to test, and it duplicates a DI mechanism that already exists and is documented.

### §D-CE2 — Binary mode needs a header seam; route it through a new Protocol, **never** through `AbstractEventBus.publish()`

**Verified, and this is the load-bearing fact for the whole freeze question**:
`varco_kafka/varco_kafka/bus.py:398` serializes, and `:406`/`:408` call

```python
await self._producer.send_and_wait(topic, value=value)
```

with **no `headers=` argument**. Consequences, per brief 001:

| Backend | Structured mode today | Binary mode today | Verdict |
|---|---|---|---|
| **NATS** | ✅ Fully spec-compliant with the serializer swap alone — brief 001 §"Protocol Bindings: NATS": payload MUST be the JSON event format, and *"NATS will only support structured data mode at this time"* | ❌ Impossible **by spec**, not by our code | Done at §D-CE1 |
| **Redis** | ✅ Compliant with **our own convention** — brief 001 §"Protocol Bindings: Redis" and §Evidence-gap 2: no official binding exists; varco defines one | ❌ n/a | Done at §D-CE1, convention written down (§D-CE4) |
| **Kafka** | ⚠️ Body is spec-correct, but the binding requires the `content-type` header to start with `application/cloudevents` (brief 001 §"Content Mode Selection") — unreachable today | ❌ Needs `ce_`-prefixed headers | Needs the seam below |

Decision: when Kafka header support lands, it goes through a **new optional Protocol** —
`MessageEncoder` with `encode(event) -> tuple[bytes, Mapping[str, str]]` — resolved by the bus
alongside `Serializer[Event]` via an optional, defaulted constructor kwarg. It does **not** change
`AbstractEventBus.publish()`.

- ✅ **This is the decision that keeps CloudEvents out of the freeze window.** A new Protocol plus a
  defaulted constructor kwarg is additive under SemVer; changing an ABC method signature is not,
  and would break every out-of-tree `AbstractEventBus` implementation.
- ✅ Headers are a *transport* concern; `AbstractEventBus.publish()` is deliberately
  transport-agnostic (the same seam rule CLAUDE.md applies to migrations and tenancy).
- ✅ Backends with no header concept (NATS, Redis) simply never resolve it — no dead parameter on
  their public API.
- ❌ Two serialization concepts (`Serializer[Event]`, `MessageEncoder`) where a naive design has
  one. Accepted, and mitigated by making `MessageEncoder` strictly optional and documented as
  "only backends with a native header channel".
- **Phase 5 reserves this in writing.** No code in this plan.

### §D-CE3 — Hand-roll; do not depend on the `cloudevents` SDK, and do not create `varco_cloudevents`

- ✅ CLAUDE.md is unambiguous: *"implement the ABC, do NOT add a runtime dependency to
  varco_core"*, and `varco_core`'s ten runtime deps are all fundamental (`varco_core/pyproject.toml:22-46`).
- ✅ Brief 001 §"Options Compared" row 2 and its Librarian's Note both put the cost at **~200 lines**
  for spec-compliant Kafka + NATS structured mode — and we need strictly less, because §D-CE1 ships
  structured mode only: attribute mapping plus JSON in/out, realistically **~120 lines**.
- ✅ Brief 001 §"Python SDK" notes the SDK is Python ≥3.10 and Pydantic-v2-capable — i.e. it would
  *work*; the objection is the dependency, not compatibility.
- ❌ We own spec compliance forever. Mitigated: v1.0.2 has been stable since Feb 2022, is CNCF
  Graduated (Jan 2024), and brief 001 §"Versioning guarantee" says new optional properties arrive
  only in MINOR versions without breaking v1.0 consumers. This is about as low-churn as a spec gets.
- Rejected alternative — **a `varco_cloudevents` workspace package** (brief 001 §Options row 3): ❌
  the repo's own decision tree says do *not* create a package for "a new feature (extend the
  existing backend's interface)" — this is one serializer class with zero third-party imports; an
  eleventh package under lockstep 3.0.0 versioning is pure machinery.
- Rejected alternative — **an optional `varco-core[cloudevents]` extra pulling the SDK**: ❌ an extra
  that changes *which implementation* is used, rather than enabling one, is the worst kind — the
  same import produces different wire bytes depending on installation state.

### §D-CE4 — Attribute mapping, and the two conventions varco must define

Per brief 001 §"Field Mapping Strategy" and §"Extension Attributes":

| CloudEvents | varco source | Notes |
|---|---|---|
| `specversion` | literal `"1.0"` | Required |
| `id` | `Event.event_id` (`event/base.py:293`) | Required; UUID4 → string |
| `type` | `Event.event_type_name()` (`base.py:323`) — `__event_type__` or class name | Required. **`event_type_name()` returns bare names like `"order.placed"` or `"OrderPlacedEvent"`, which is spec-legal but violates the reverse-DNS *recommendation*** — hence the `source_prefix` setting below |
| `source` | **`CloudEventsSettings.source`, required, no default** | Required, non-empty URI-reference. There is no correct default for "who am I"; construction fails loudly rather than emitting `"varco"` |
| `time` | `Event.timestamp` (`base.py:294`) | RFC 3339; already aware-UTC |
| `datacontenttype` | literal `"application/json"` | |
| `data` | `Event.model_dump(mode="json")` minus `event_id`/`timestamp` | Same call `JsonEventSerializer` already makes (`serializer.py:178`) |
| `correlationid` | `correlation_id`, when the event carries one | Extension. **Name is `correlationid`** — brief 001 §"Extension Attributes: Naming Rules": lowercase ASCII letters and digits only, no underscores or hyphens |
| `tenantid` | `current_tenant()` at serialize time, when set | Extension, same naming rule. See ⚠️ below |

Two conventions varco must define because nobody else has:

1. **Redis** (brief 001 §Evidence-gap 2): structured JSON body for both pub/sub and Streams. For
   Streams, the whole CloudEvents JSON goes in a single field named `ce` of the stream entry —
   *not* one field per attribute, so `XADD` field names never collide with a future varco field.
   Write this down in `technical_docs/features/cloudevents-envelope.md` as a named, versioned
   convention so downstreams can implement against it.
2. **Tenant encoding** (brief 001 §Evidence-gap 4: "No guidance found in the spec"): a `tenantid`
   extension attribute, **never** encoded into `source` or `subject`. Rationale: `source` must be
   stable per producer for the spec's `source`+`id` uniqueness rule; folding a per-message tenant
   into it makes uniqueness reasoning tenant-dependent for no gain.

⚠️ **Deliberate constraint on `tenantid`:** the serializer reads `current_tenant()` — CLAUDE.md's
single source of truth, never `RequestContext`. A serializer runs on whatever task the publish
happens on, so an `OutboxRelay`-driven publish has **no ambient tenant**. `tenantid` is therefore
emitted only when `current_tenant()` is set, and is documented as best-effort. Do not "fix" this by
adding a tenant field to `Event` — that is §D-CE1's rejected alternative wearing a different hat.

### §D-AA1 — AsyncAPI target 3.1.0, generated from a **live wired consumer**, not static imports

Target **3.1.0** (brief 002 §1: current stable, Jan 2026, no breaking changes from 3.0.0). The 3.x
channels/operations split (brief 002 §2) maps onto varco cleanly: a `@listen` channel string is an
AsyncAPI **channel**; a decorated handler is an AsyncAPI **operation** with `action: receive`; the
`Event` subclass is the **message**, its payload from Pydantic's `model_json_schema()`.

**But generation must be runtime, not static.** Verified: `@listen` stores a list of frozen
`_ListenEntry` dataclasses on `func.__listen_entries__` (`event/consumer.py:141-142`, read at
`:854`), and the entry's channel field is typed
`channel: str | Callable[[Any], str]` (`:180`) — a callable form resolved at `register_to()` time
against a bound `self` (`:155-166`). A static import-time walk cannot resolve those.

Decision: the generator takes **consumer instances** (or a container to resolve them from) and
reads their resolved channels — FastStream's approach (brief 002 §5: "Traverses decorated handlers
at runtime, reflects on type hints").

- ✅ Correct for callable channels, which a static scan gets silently wrong.
- ✅ Reuses the exact metadata `register_to()` already consumes — one source of truth, so the
  document cannot describe a wiring that does not exist.
- ❌ The CLI verb must import and construct the app. Same cost FastAPI's own `openapi.json` export
  has; acceptable and familiar.
- ❌ A consumer never registered is never documented. That is correct behaviour, and worth stating
  as such in the docs.

### §D-AA2 — Imitate FastStream's *approach*, diverge on its *packaging*

Explicit call, as requested.

- **Imitate**: runtime decorator introspection; type-hint-derived message schemas; a
  `docs gen`-shaped CLI verb (brief 002 §5).
- **Diverge**: FastStream *is* the broker framework, so its generator can assume one app object and
  one broker. varco's event system is bus-agnostic by construction, so the generator takes an
  explicit list of consumers plus an explicit `protocol=` per server rather than inferring from a
  broker instance. varco also emits **no `servers` block by default** — a broker URL is deployment
  configuration, not source truth, and baking a dev URL into a committed snapshot is exactly the
  documentation rot brief 002 §6's gate exists to prevent. `--server name=protocol://host` supplies
  one explicitly when wanted.
- **Do not depend on FastStream** — it is a competing framework, not a library.

### §D-AA3 — Binding coverage: Kafka only, and say so in the document

Per brief 002 §4: Kafka binding **0.5.0** is stable with real fields (`topic`, `partitions`,
`groupId`, `clientId`); NATS **0.1.0** has exactly one operation-level field (`queue`); Redis
**0.1.0** has *zero* properties at all four binding levels.

- Emit Kafka channel bindings (`topic`) and operation bindings (`groupId`) when a `KafkaEventBus` is
  the source.
- Emit the NATS operation binding **only** when a queue group is actually configured — a
  `bindings: {nats: {bindingVersion: "0.1.0"}}` stanza carrying nothing is noise.
- Emit **no** Redis binding block. An empty reserved binding communicates nothing; the channel's
  `address` already carries everything Redis has.
- Record all three choices in the generated document's `info.description`, so a reader who wonders
  why their Redis channels have no bindings finds the answer in the artifact itself rather than in
  a plan file.

### §D-AA4 — CLI verb, home, and the snapshot + `--check` gate

- **Verb**: `varco export-asyncapi`, following the existing `varco export-contract` precedent
  (`varco_fastapi/contract/`). Registered in the **`varco.commands`** entry-point group — the same
  group `varco_sa/pyproject.toml:67`, `varco_fastapi/pyproject.toml:74` and
  `varco_beanie/pyproject.toml:45` already use, dispatched from `varco_core/cli/main.py`
  (`[project.scripts] varco = "varco_core.cli.main:main"`, `varco_core/pyproject.toml:62-63`).
- **Home**: the *generator* is `varco_core/asyncapi/` (backend-agnostic, reads only
  `varco_core.event` metadata and Pydantic schemas — same placement logic that keeps the
  SQLAlchemy query applicator in `varco_core.query.applicator`). The *CLI verb* is
  `varco_core/cli/asyncapi.py`.
- **Gate**: `varco export-asyncapi --check` regenerates and diffs against the committed snapshot,
  exiting non-zero on divergence — brief 002 §6's established pattern, and structurally identical
  to §D-AUDIT's `--check`. Wire it into `make lint`, **not** into a new CI job.
- **No Node in CI.** Brief 002 §5 establishes `@asyncapi/cli` needs Node 24+ and that no Python
  validator exists. Adding a Node toolchain to CI to validate a generated document is a large
  operational cost for a small assurance gain. Instead: document the local
  `npx @asyncapi/cli validate` invocation in `technical_docs/features/asyncapi-export.md`, and run
  it **once, by hand, during Phase 7**, recording the result in the plan's evidence directory.
  Brief 002 §Evidence-gap 1 (no blessed `schemaFormat` for Draft 2020-12) makes that one real
  validation genuinely worth doing before shipping.

### §D-OF — OpenFeature: **do not build it in 3.0.0**, and do not ship an ABC either

Recommendation, with reasoning, as asked — this is a real question and the answer is no.

- ❌ **The spec is v0.8.0, pre-1.0** (brief 003 §"CNCF maturity"), and the Python SDK is **0.10.0**
  with a documented breaking change *inside a minor bump* (`set_provider()` no longer blocks; use
  `set_provider_and_wait()` — brief 003 §"Python SDK version"). Shipping an abstraction shaped to
  that spec **inside a version freeze** is the specific combination to avoid: under lockstep 3.0.0,
  our ABC is frozen while the thing it mirrors is not.
- ❌ **The brief's own decision criterion is not met.** Brief 003 §"Librarian's note": *"The
  integration makes sense only if varco intends to support runtime flag evaluation (not
  startup-config-only)"* and *"If today's `BaseSettings` pattern is sufficient, adding OpenFeature
  introduces abstraction overhead … without payoff."* There is no runtime-flag requirement in tree
  and none in the BACKLOG.
- ❌ **The "ship just the ABC as a seam" compromise is worse than either extreme.** It freezes a
  five-method surface (`resolve_boolean/string/integer/float/object_details`, brief 003
  §"Provider contract") derived from a moving spec, and buys nothing — a `FeatureFlags` ABC added
  in 3.1 is *purely additive*, so there is no freeze-window cost to waiting. Deferring is free;
  guessing is not.
- ✅ **The honest positives, recorded so the deferral is informed.** The Python SDK is genuinely
  async (`*_async` on all five types, graceful fallback when a provider is sync — brief 003
  §"Async support"), has no third-party runtime deps, requires only Python ≥3.10, and propagates
  evaluation context via `ContextVar` — the same PEP 567 seam `AmbientVar`
  (`varco_core/context/ambient.py`) already uses. If the trigger fires, integration is
  straightforward and the shape is known.
- ✅ **The shape, if it is ever built** (so a future plan does not re-derive it): a `FeatureFlags`
  Protocol in `varco_core.flags` with five typed getters; a `varco_openfeature` adapter package
  holding the SDK dependency; wiring via `enable_feature_flags(container)` — the `enable_*` verb,
  matching `varco_casbin.di.enable_policy_authorizer` exactly, because the shared reason is
  identical (an opt-in binding that would otherwise shadow an app default); an `AmbientVar` →
  `EvaluationContext` bridge implemented as an OpenFeature **Before hook** (brief 003
  §"Evaluation context & hooks": Before is the only mutable stage) reading `current_tenant()`
  **and** `RequestContext` separately, per CLAUDE.md's rule that `RequestContext` never holds the
  tenant; `InMemoryProvider` for tests, no bundled production provider.

**Trigger to reopen (write this into BACKLOG's Parked row, so the deferral is falsifiable):**
either (a) the OpenFeature **spec reaches ≥1.0**, or (b) a concrete in-tree requirement for
*runtime* (not startup) flag evaluation appears. Either one, not both.

---

## Steps

Every phase ends green: `make lint`, `make type-check`, `make test`.
**Phases 0–5 are freeze-critical. Phases 6–8 are not, and may be cut at the Phase 5 boundary
without reopening any decision.**

### Phase 0 — measure and prove (no production code)

1. [x] `scripts/api_surface.py` — new. Derives its package list from `scripts/packages.sh` (Plan 020
   / RL-18). Emits `design/api-freeze-and-standards/measurements/api-surface.json` + `.md` per
   §D-AUDIT. Supports `--check` (diff vs committed, non-zero exit on removal or signature change).
2. [x] `varco_core/tests/test_api_surface_snapshot.py` — failing test first: `--check` against the
   committed snapshot passes on an unmodified tree, and fails when a symbol is removed from an
   `__all__` (simulate with a monkeypatched module).
3. [x] Run it; commit the snapshot. Record the per-package export counts in the plan's evidence
   directory. Expected order of magnitude: `varco_core` 243, `varco_fastapi` 125, `varco_redis` 28,
   `varco_kafka` 8 — **if these differ, the new run is ground truth and this plan's numbers are
   advisory** (U-8 discipline).
4. [x] `design/api-freeze-and-standards/measurements/fail-open-defaults.md` — enumerate every
   `BaseSettings` subclass field across the ten packages whose default disables a
   safety/isolation/durability/verification property, per §D-FAILOPEN. Walk `__mro__`, do not `rg`.
   Exclude the two JWT defaults by name, with a one-line note saying why.
5. [x] `design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md` — §D-8a1's proof.
   For each of the ten `@PreDestroy` classes (`varco_kafka/bus.py:321`, `varco_kafka/channel.py:234`,
   `varco_nats/bus.py:281`, `varco_nats/channel.py:319`, `varco_redis/bus.py:217`,
   `varco_redis/streams.py:318`, `varco_redis/channel.py:132`, `varco_redis/cache.py:213`,
   `varco_memcached/cache.py:248`, `varco_casbin/engine.py:212`) record: is it registered as a
   lifecycle component by `create_varco_app()` (`app.py:420`, `:716-728`)? **Yes / No / Only under
   config X.** This table decides §D-8a2.
6. [x] Same file — read all ten `stop()` implementations and record **idempotent: yes/no** for each,
   with the line that makes it so (or the line that does not). This is §D-8a2(b)'s precondition.
7. [x] `rg -n 'warnings\.warn' varco_*/varco_*` — classify each of the six known sites as
   *deprecation* vs *operational warning*. Only the former migrate in Phase 2.

### Phase 1 — ⛔ CHECKPOINT

8. [x] `design/api-freeze-and-standards/api-break-candidates.md` — write the §D-RANK table. Seed it
   with: AB-1 §D-C1 (`enable_rls_ddl`), AB-2 §D-C2 (`MigrationError`/`MigrationPlan`), AB-3 §D-C3
   (`install_*`, proposal `leave-and-document`), AB-4 §D-C4 (`BeanieConfig`/`BeanieSettings`), plus
   one row per Step 4 finding. Measure every blast radius with `rg`, counting source / test /
   docs sites separately.
9. [x] Add the two **non-breaking** riders as separate, pre-approved rows (they need no verdict —
   they are bug fixes, land unconditionally in Phase 3): the missing `container is None` guard in
   `varco_redis/di.py:168`'s `async_bootstrap()` (BACKLOG "Deferred follow-ups (Plan 014 / audit 001
   Batch B)"), and the `set[int]`-keyed double-mount guards in `varco_fastapi/tenancy/mount.py` +
   `varco_fastapi/admin/mount.py` → `weakref.WeakSet` (same source; **change both together**).
10. [x] **STOP.** Present the file. Do not proceed until every Verdict cell is filled by the user.
    Record the verdicts in the file itself, dated.

### Phase 2 — deprecation mechanism (conditional: skip if no accepted break ships an alias)

11. [x] `varco_core/tests/test_deprecation.py` — failing tests first: warning category is
    `DeprecationWarning`; `stacklevel` points at the caller not at the decorator; `removed_in` is
    required and appears in the message; an aliased class still satisfies `isinstance`/`except`.
12. [x] `varco_core/deprecation.py` — implement `deprecated()` + `deprecated_alias()` per §D-DEP.
    Full docstrings (`Args`/`Returns`/`Raises`/`Edge cases`), `DESIGN:` block with ✅/❌ including
    the PEP 702 rejection.
13. [x] `varco_core/__init__.py` — export both. Re-run `scripts/api_surface.py`; the snapshot grows
    by exactly two symbols and nothing else moves.
14. [x] Migrate the Step-7-classified *deprecation* sites onto the new mechanism. Own commit.

### Phase 3 — land accepted breaks (RL-8) — one commit per `AB-n`

15. [x] For each accepted `AB-n`, in blast-radius order (smallest first): failing/updated test →
    rename → alias (if the verdict says so) → update every in-tree call site → update
    README/CLAUDE.md/`technical_docs/` in the **same commit** (docs are never a follow-up).
16. [x] Land the two Step-9 riders (non-breaking, no verdict needed).
17. [x] `CLAUDE.md` §"DI wiring verb taxonomy" — amend the `install_*` row to record both shapes
    (§D-C3), and update the two collision bullets to reflect whatever the checkpoint decided.
    Docs-only, unconditional.
18. [x] Re-run `scripts/api_surface.py`; commit the updated snapshot. Every delta must map to an
    accepted `AB-n`. **An unexplained delta means an accidental break — revert it.**
19. [x] `CHANGELOG.md` — one BREAKING entry per accepted break, each naming its `AB-n`, the alias
    (if any) and the `removed_in` version.

### Phase 4 — RL-8a

20. [x] **BRANCH TAKEN: adoption.** Step 5 measured **6 orphans of 10**, so the "zero orphans"
    branch below did **not** fire; steps 21–23 all ran. (Original text:) Branch on Step 5. **If zero orphans**: skip to step 24, write the non-adoption + trigger
    into `lifespan.py`'s docstring (replacing the `:189-190` "see BACKLOG.md's RL-8 row" pointer),
    and close RL-8a as *decided: no*. Steps 21–23 do not run.
21. [x] **N/A — zero work, as Step 6 predicted (10 idempotent / 0 non-idempotent).** The budget was
    respent, per the measurement's own recommendation, as five `test_stop_idempotency_regression.py`
    double-`stop()` regression files so the now-load-bearing property cannot silently regress.
    (Original text:) Fix any non-idempotent `stop()` found in Step 6 — one commit per component, each with a
    regression test calling `stop()` twice.
22. [x] `varco_fastapi/tests/test_lifespan_shutdown.py` — failing tests first: `shutdown=` is called
    after `_stop_all()`; it is called even when a component's `stop()` raised; an aggregated
    `ShutdownError` is logged (caplog, one line per `ShutdownFailure`) and **not** raised; omitting
    `shutdown=` is byte-identical to today.
23. [x] `varco_fastapi/varco_fastapi/lifespan.py` — add the `shutdown=` kwarg per §D-8a2(a); call it
    in `__call__`'s `finally` after `_stop_all()`; handle providify's `ShutdownError` per (c).
    Update the `:175-190` Edge-cases docstring block, which currently documents the opposite.
    `varco_fastapi/varco_fastapi/app.py` — pass `lambda: container.ashutdown()` at `:420`.
24. [x] `varco_fastapi/tests/test_lifespan_shutdown_characterization.py` — keep it; add a comment
    naming this plan as the adoption it de-risked (or as the decision that declined it).
25. [x] ⚠️ **Deviation, on measured evidence — the orphan under test is `NatsStreamManager`, not
    `RedisCache`.** `RedisCache` is bound by a `@Provider`, and providify's `_adispose()`
    (`container.py:4550-4582`) runs a `@Disposes` disposer for a `ProviderBinding` and never
    reaches the `@PreDestroy` of the instance a provider *returned* — only a `ClassBinding`
    consults `binding.pre_destroy`. So `RedisCache`/`MemcachedCache` are **not** fixed by this
    adoption. That is filed (BACKLOG "Findings from Plan 022", `P22-PROVIDER-PREDESTROY`) and
    pinned by a `strict=True` xfail in
    `varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py`, **not** worked around
    in varco code. The positive proof uses `NatsStreamManager` — orphan #4, a `@Singleton`
    (`ClassBinding`) whose `@PostConstruct` opens a real NATS connection and whose `@PreDestroy`
    closes it, so "released" is observable on the client socket rather than on a private flag:
    `varco_nats/tests/test_nats_lifespan_shutdown_integration.py`, green against a real container.
    (Original text:) Integration check: one real-broker test asserting a `@PreDestroy`-bearing singleton that is
    *not* a registered lifecycle component (pick one confirmed orphan from Step 5) is actually torn
    down. Real Docker container — this is exactly the "touching real external systems" case the
    testing rule names.
26. [x] `CHANGELOG.md` — BREAKING entry: new teardown behaviour, signature additive.

### Phase 5 — the freeze-window design record (**last gate before RL-9**)

27. [x] `design/api-freeze-and-standards/reserved-seams.md` — the only freeze-critical standards
    artifact. Record, with the reasoning compressed from §D-CE1/§D-CE2/§D-AA4: (a)
    `Serializer[Event].serialize()` will not change — CloudEvents is a second implementation; (b)
    `AbstractEventBus.publish()` will not gain `headers=` — header-bearing transport metadata goes
    through a new optional `MessageEncoder` Protocol plus a defaulted bus constructor kwarg; (c)
    the names `varco_core.event.cloudevents`, `varco_core.asyncapi`, `varco_core.flags` and the
    `varco.commands` verb `export-asyncapi` are reserved. Each entry names the alternative rejected
    and why, so 3.1 cannot re-litigate cheaply.
28. [x] `BACKLOG.md` — Phase 4 table: mark **RL-8 ✅ DONE (Plan 022)** with the accepted/rejected
    counts, and **RL-8a ✅ DONE (Plan 022)** with the decision (*adopted* or *decided: no*) and the
    Step-5 orphan count. Correct the RL-8a row's "⚠️ Suspected, not proven" to the measured answer
    (U-8: do not close a row over an unverified claim).
29. [x] `BACKLOG.md` — Parked table: replace the *"Standards alignment"* row. It becomes three
    rows: **CloudEvents envelope** (un-parked → Plan 022 Phase 6, additive, 3.0.0-or-3.1),
    **AsyncAPI export** (un-parked → Phase 7, same), **OpenFeature** (⏸️ re-parked with §D-OF's
    two-clause trigger written out verbatim). Add a ⚠️ note recording that the Locked-decisions
    row *"parked to 3.1"* was **reversed this plan**, with the reason — the same
    visible-reversal discipline BACKLOG.md:22-25 already uses for the GitHub Actions park.
30. [x] **Decision point — DECIDED 2026-08-31: defer Phases 6–8 to 3.1.** RL-9 (version freeze) is unblocked as of
    Step 29. **Phases 6–8 are deferred pending the user's call**; this implementation pass
    deliberately stops at the Phase 5 boundary and starts none of Phase 6. Nothing in Phases 6–8
    is referenced by RL-9/RL-10/RL-11/RL-12, so cutting here costs the release nothing.

    **Why the deferral is free**: Step 27's `design/api-freeze-and-standards/reserved-seams.md` is
    the entire reason. It records, before the freeze, that (RS-1) `Serializer[Event].serialize()`
    will not change because CloudEvents ships as a second *implementation*; (RS-2)
    `AbstractEventBus.publish()` will not gain `headers=` because header-bearing transport metadata
    routes through a new optional `MessageEncoder` Protocol plus a defaulted bus constructor kwarg;
    and (RS-3) the names `varco_core.event.cloudevents`, `varco_core.asyncapi`, `varco_core.flags`
    and the `varco.commands` verb `export-asyncapi` are reserved. Each entry names the alternative
    rejected and why. With that on the record, shipping Phases 6–7 in 3.1 provably costs **no
    deprecation cycle** — the only thing 3.0.0 ever owed the standards work was this document, and
    it is now written.

    **The decision taken (user, 2026-08-31): cut at the Phase 5 boundary. Plan 022 is CLOSED at
    Phase 8 with Phases 6 and 7 unbuilt.** The reasoning is the one this plan's Organizing
    Principle argued for from the start — the freeze window is for changes to the *public surface*,
    and neither CloudEvents nor AsyncAPI needs one. The groundwork that *was* freeze-critical is
    all landed: the RL-8 audit is a reproducible artifact rather than an eyeball pass, RL-8a is
    decided on measured evidence, and the seams are reserved in writing. Phases 6–8 are re-filed as
    3.1 BACKLOG rows (Step 45) citing this plan as their **completed design** — a future plan
    implements them, it does not re-derive them.

    ⚠️ **What this closure does *not* claim.** Two things stay open and are the successor's
    inheritance, not this plan's debt: `P22-PROVIDER-PREDESTROY` (two caches still leak a started
    connection pool — see `design/upstream-gaps/providify-provider-predestroy.md`, indexed in the recreated
    `UPSTREAM-GAPS.md` ledger, which records a varco-side `@Disposes` fix needing no upstream
    change) and `P22-REDIS-DOUBLE-BUS`. Both are filed with
    strict-xfail guards, neither is a freeze or SemVer concern, and neither blocks RL-9.

### Phase 6 — CloudEvents structured envelope (additive)

> ⛔ **NOT EXECUTED — deferred to 3.1 at Step 30 (decided 2026-08-31).** The steps below are a
> *finished design*, not outstanding work items, and their checkboxes stay unticked on purpose so
> nobody reads this plan as half-done. A successor plan implements them as written; the seam
> reservation in `design/api-freeze-and-standards/reserved-seams.md` is what guarantees doing so in
> 3.1 costs no deprecation cycle. Tracked as 3.1 rows in BACKLOG.md's Parked table.

31. [ ] `varco_core/tests/test_cloudevents_serializer.py` — failing tests first: all four required
    attributes present and correctly typed; `source` missing → construction raises (never a
    default); round-trip `serialize`→`deserialize` preserves the event class via the same
    `Event._registry` lookup `JsonEventSerializer` uses; `correlationid`/`tenantid` obey the
    lowercase-alphanumeric naming rule; **no `tenantid` emitted when `current_tenant()` is unset**
    (the OutboxRelay case, §D-CE4); a native-serialized payload fed to the CloudEvents
    deserializer fails loudly, not silently.
32. [ ] `varco_core/varco_core/event/cloudevents.py` — `CloudEventsSettings` (frozen; `source`
    required, `source_prefix` optional for reverse-DNS `type` prefixing) and
    `CloudEventsJsonSerializer(Serializer[Event])` per §D-CE1/§D-CE4. Default DI priority (opt-in),
    **not** `-sys.maxsize - 1`. Register `CloudEventsSettings` via a `@Provider`, never `@Singleton`
    — CLAUDE.md's pydantic-`BaseSettings` rule.
33. [ ] `varco_core/__init__.py` — export both; re-run `scripts/api_surface.py` and commit the
    two-symbol growth.
34. [ ] Integration tests: NATS and Redis round-trip against real containers, asserting the wire
    bytes parse as a CloudEvents structured JSON envelope. Session-scoped fixtures + a
    `uuid4().hex[:8]` namespace per CLAUDE.md's Test Conventions. **Kafka gets a unit test only**,
    with an explicit comment that binary mode is out of scope per §D-CE2.
35. [ ] `technical_docs/features/cloudevents-envelope.md` — new. Attribute mapping table; the
    **Redis convention** (§D-CE4, named and versioned, since no official binding exists); the
    Kafka structured-mode `content-type` limitation and its §D-CE2 resolution path; brief 001's
    three-phase migration timeline; a **Pitfalls** table (opting in mid-stream without a dual-read
    consumer; expecting `tenantid` on relayed events).
36. [ ] `README.md` + `CLAUDE.md` — README gets a runnable opt-in snippet; CLAUDE.md's event-system
    section gets one sentence plus the doc pointer. Same commit.

### Phase 7 — AsyncAPI 3.1.0 export (additive)

> ⛔ **NOT EXECUTED — deferred to 3.1 at Step 30 (decided 2026-08-31).** The steps below are a
> *finished design*, not outstanding work items, and their checkboxes stay unticked on purpose so
> nobody reads this plan as half-done. A successor plan implements them as written; the seam
> reservation in `design/api-freeze-and-standards/reserved-seams.md` is what guarantees doing so in
> 3.1 costs no deprecation cycle. Tracked as 3.1 rows in BACKLOG.md's Parked table.

37. [ ] `varco_core/tests/test_asyncapi_generator.py` — failing tests first: a two-consumer fixture
    produces a valid 3.1.0 skeleton (`asyncapi`/`info`/`channels`/`operations`, brief 002 §2);
    `action` is `receive`; a **callable** channel resolves against the bound instance (§D-AA1);
    `CHANNEL_ALL` (`"*"`) does **not** produce a channel named `*` — see Edge cases; Kafka bindings
    appear only for a Kafka source; **no** Redis binding block is emitted (§D-AA3); two consumers
    on one channel merge into one channel with two operations; output is deterministically ordered
    (sorted keys) so the snapshot diff is stable.
38. [ ] `varco_core/varco_core/asyncapi/__init__.py` + `generator.py` — `build_asyncapi(consumers,
    *, title, version, servers=None) -> dict[str, Any]`, plus a `to_yaml`/`to_json` writer. Message
    payloads from `model_json_schema()` hoisted into `components/schemas` with `$ref`s. Omit
    `schemaFormat` entirely — brief 002 §3 / §Evidence-gap 1: there is no blessed value for Draft
    2020-12, and omission means "AsyncAPI Schema", which tools accept. Record that reasoning in the
    module docstring, not just here.
39. [ ] `varco_core/varco_core/cli/asyncapi.py` + `varco_core/pyproject.toml` — the
    `export-asyncapi` verb with `--check`, registered in the `varco.commands` group per §D-AA4.
    Mirror `varco_fastapi/contract/cli.py`'s argparse shape.
40. [ ] `examples/00-full-stack-post-api/asyncapi.yaml` — the committed snapshot, generated from the
    example app's real consumers. This is the artifact `--check` diffs against.
41. [ ] `Makefile` — add `varco export-asyncapi --check` to the `lint` target (§D-AA4: no new CI
    job, no Node in CI).
42. [ ] **Manual, one-time validation.** Run `npx @asyncapi/cli validate` against the snapshot and
    record the verbatim output in
    `design/api-freeze-and-standards/measurements/asyncapi-validate.txt`. If it rejects the
    embedded Draft 2020-12 schemas, brief 002 §Evidence-gap 1 has materialized — **stop**, file a
    BACKLOG row, and fall back to emitting Draft-07-compatible schemas rather than shipping an
    invalid document.
43. [ ] `technical_docs/features/asyncapi-export.md` — new. The generation model (runtime, not
    static, and why — §D-AA1); the FastStream imitate/diverge call (§D-AA2); per-binding coverage
    and the reasoning for each omission (§D-AA3); the snapshot + `--check` workflow; the local
    `npx` validation recipe; a **Pitfalls** table (an unregistered consumer is silently absent; a
    callable channel needs an instance; a wildcard listener is not a channel).
44. [ ] `README.md` + `CLAUDE.md` + `ARCHITECTURE.md` — usage snippet, one CLAUDE.md sentence with
    the doc pointer, and `varco_core.asyncapi` added to ARCHITECTURE.md's package map. Same commit.

### Phase 8 — close out

45. [x] `BACKLOG.md` — **deferred branch taken.** The CloudEvents and AsyncAPI rows created in
    Step 29 are restated as **3.1 rows citing this plan as their completed design** (📋 DEFERRED TO
    3.1, "design COMPLETE, implementation not started"), and the reversal notice records that the
    Phase-5 cut was actually exercised. The un-park is *not* undone — it was decided on the merits;
    the scheduling decision that followed is separate and separately recorded.
46. [x] `CHANGELOG.md` — **nothing to add, deliberately.** This step existed to record Phases 6–7;
    they were not built, so there is no additive entry to write. The CHANGELOG's Plan 022 content is
    exactly the two BREAKING sections (RL-8's four accepted breaks, RL-8a's teardown behaviour) plus
    the Added section for `deprecated`/`deprecated_alias` and `scripts/api_surface.py`. Writing an
    "additive: none" heading would be noise; leaving a CloudEvents entry would be false.
47. [x] Final full verification (see Verification below) — run green at closure, 2026-08-31.

---

## Edge cases

- **A checkpoint verdict rejects every break** → Phase 2 and most of Phase 3 are skipped; Steps 16,
  17, 18 still run (riders + docs + snapshot). RL-8 still closes: an audit that finds nothing worth
  breaking is a *result*, not a failure.
- **`CHANNEL_ALL` (`"*"`) in `@listen`** → it is a filter, not an address. Do **not** emit a channel
  named `*`. Emit an operation bound to a synthetic channel whose `address` is `null`, carrying
  `x-varco-channel-filter: "*"`, and document it. ⚠️ See Risks — `address: null` needs validating.
- **A callable channel whose resolution raises** (needs instance state not yet set) → the generator
  catches, logs a WARNING naming the consumer class and method, and emits the synthetic
  unknown-address channel. It must never abort the whole document over one handler.
- **Two `Event` subclasses with the same `event_type_name()`** → `Event.__init_subclass__`
  (`base.py:321`) silently overwrites in `_registry`, and the AsyncAPI generator would emit one
  message where two were meant. The generator detects the collision and **fails loudly** rather
  than emitting a quietly wrong document. (Do not change `__init_subclass__` — out of scope.)
- **`current_tenant()` unset at CloudEvents serialize time** → omit `tenantid`. Never emit
  `"tenantid": null` (a present-but-null extension attribute is worse than an absent one), never
  invent a default. §D-CE4.
- **An `Event` subclass with a field literally named `source`, `id`, `type` or `time`** → these live
  under `data`, not at envelope level, so there is no collision. Assert this in a test, because it
  is exactly the kind of thing a future "flatten the payload" optimization would break.
- **`--check` run on a tree where the example app cannot import** (missing extra) → the verb must
  exit with a clear "could not import <module>" message, not a bare `ImportError` traceback. Same
  standard `varco export-contract` already meets.
- **A `stop()` made idempotent in Step 21 that was load-bearing non-idempotent** (e.g. it
  intentionally raises on double-stop to catch a wiring bug) → do not paper over it; file a BACKLOG
  row and exclude that component from double-stop exposure instead.
- **`scripts/api_surface.py` cannot import a package** because an optional extra is absent → it must
  fail loudly and name the extra, never silently emit a smaller snapshot. A silently-shrinking
  snapshot would report every symbol in that package as removed.

## Verification

```bash
# 1. The three standing gates, at every phase boundary.
make lint          # includes ruff format --check, and (Phase 7+) export-asyncapi --check
make type-check    # strict = true, Plan 021
make test          # all eleven suites, accumulated

# 2. The API-surface gate — the artifact this plan exists to produce.
uv run python scripts/api_surface.py --check     # must be clean at every phase boundary
uv run python scripts/api_surface.py             # regenerate; commit only alongside an accepted AB-n

# 3. Targeted suites for the riskiest edits.
uv run pytest varco_fastapi/tests/test_lifespan_shutdown.py \
              varco_fastapi/tests/test_lifespan_shutdown_characterization.py   # Phase 4
uv run pytest varco_core/tests/test_deprecation.py                             # Phase 2
uv run pytest varco_core/tests/test_cloudevents_serializer.py                  # Phase 6
uv run pytest varco_core/tests/test_asyncapi_generator.py                      # Phase 7
uv run pytest varco_sa/tests/ varco_beanie/tests/                              # Phase 3 renames

# 4. DI health — any rename touching a binding annotation can silently break injection.
uv run pytest varco_fastapi/tests/test_di_binding_health.py
uv run pytest varco_*/tests/ -k "validate_bindings or di_health"

# 5. Real brokers (Phase 4 step 25, Phase 6 step 34).
make integration-test-clean

# 6. One-time, by hand, Phase 7 step 42 — never in CI.
npx @asyncapi/cli validate examples/00-full-stack-post-api/asyncapi.yaml
```

## Risks

- ⚠️ **ASSUMPTION — `create_varco_app()`'s lifecycle registration set.** The plan reads `app.py:420`
  (`VarcoLifespan(*lifespan_components)`) and four `_try_resolve_component()` calls at `:716-728`,
  but has **not** traced every conditional path that appends to `lifespan_components` (`:387`
  suggests at least one more). Step 5 is the proof; until it runs, §D-8a2's "adopt" recommendation
  is provisional and its "if zero orphans" branch is live.
- ⚠️ **ASSUMPTION — `stop()` idempotency across the ten `@PreDestroy` components.** Unverified.
  `register()`'s docstring (`lifespan.py:148-151`) *expects* it; expectation is not evidence. Step 6
  is the check, and Step 21 is the remediation budget. **If more than three components turn out
  non-idempotent, stop and reconsider §D-8a2(b)** — that many suggests the double-stop model is
  wrong, not that the components are.
- ⚠️ **ASSUMPTION — the fail-open enumeration finds something.** It may find nothing beyond the
  three `TenancySettings` fields already known and already argued as deliberate. That is an
  acceptable, publishable result — Step 4 must record "none found beyond X" explicitly rather than
  quietly producing a short table.
- ⚠️ **ASSUMPTION — AsyncAPI 3.x permits `address: null`** for a design-time-unknown channel
  (Edge cases, wildcard listeners). Brief 002 does not state this; it is inferred from the 3.x
  channel model. Step 42's one-time `npx @asyncapi/cli validate` is the check. **Fallback if
  rejected**: emit a channel with a literal address of the consumer's declared default and an
  `x-varco-channel-filter` extension; never ship a document the official parser rejects.
- ⚠️ **ASSUMPTION — brief 002 §Evidence-gap 1 (Draft 2020-12 `schemaFormat`) is benign.** §D-AA4
  omits `schemaFormat` on the theory that tools are permissive. Step 42 is the only real evidence;
  §D-AA4 and Step 42 both name the Draft-07 fallback so this cannot become a late surprise.
- ⚠️ **ASSUMPTION — `varco_kafka/bus.py:406` is the only publish path lacking `headers=`.** Verified
  for `send_and_wait` at `:406`/`:408`; the transactional path near `:527` was not read. §D-CE2's
  conclusion does not depend on it (no header support anywhere is the same conclusion as header
  support in one place), but a Phase-6 implementer should confirm before writing the doc's Kafka
  limitation paragraph.
- **A rename silently breaks DI injection.** This is the one place an annotation *is* runtime
  behaviour in this repo (CLAUDE.md's two annotation pitfalls; Plan 021 §Risks). Invariant: every
  package's `validate_bindings()` + `assert_no_structural_di_issues()` test stays green, and
  `varco_fastapi/tests/test_di_binding_health.py` is the canary. Never quote a renamed type in a
  `@Provider` return annotation.
- **Phases 6–7 delay the 3.0.0 release.** The mitigation is structural, not aspirational: Step 30 is
  an explicit decision point, and nothing in Phases 6–8 is referenced by RL-9/RL-10/RL-11/RL-12. If
  the release is close, cut at Step 30 and the reserved-seams record (Step 27) guarantees the
  deferral costs nothing.
- **The deprecation aliases rot.** Every alias carries `removed_in=`, but nothing enforces removal.
  Mitigation: RL-9 owns the written policy; this plan's contribution is that `removed_in` is a
  *required* argument, so the removal version is always greppable
  (`rg 'removed_in="3\.' varco_*/varco_*`).
- **CloudEvents opt-in mid-stream loses events.** A deployment that rebinds the serializer while a
  channel holds native-format messages gets deserialization failures. This is inherent to any
  envelope change, not a defect. Mitigation is documentation only: `technical_docs/features/cloudevents-envelope.md`
  leads with brief 001's three-phase dual-emit timeline, and the Pitfalls table names it first.
- **`scripts/api_surface.py` becomes a maintenance burden nobody runs.** Mitigated by wiring
  `--check` into `make lint` in the same phase that creates it (Step 41's precedent), so it is a
  gate from day one rather than a script that rots.
