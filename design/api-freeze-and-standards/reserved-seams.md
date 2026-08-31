# Reserved seams — what 3.0.0 promises the standards work will never need to break

**Plan 022 / Phase 5, Step 27.** This is the *only* freeze-critical artifact the
standards-alignment work produces. Everything else it needs (a CloudEvents
serializer, an AsyncAPI exporter) is purely additive and can ship in 3.0.0 or in
3.1 at identical cost — **provided the three decisions below are on the record
before the freeze**. They are on the record here.

Read it as a promise with a proof attached, not a roadmap. Each entry states the
seam, the alternative that was rejected, and *why* — so a 3.1 plan cannot
re-litigate any of them cheaply, and a reviewer cutting Phases 6–8 at the Phase 5
boundary loses nothing but time-to-ship.

Sources compressed here: §D-CE1, §D-CE2, §D-CE3, §D-AA4 of
`plans/022-api-freeze-and-standards-alignment.md`, and research briefs 001
(CloudEvents) / 002 (AsyncAPI) in `./research/`.

---

## RS-1 — `Serializer[Event].serialize()` will not change

**Promise.** The `Serializer[Event]` protocol keeps its current method shape
through 3.x. CloudEvents ships as a *second implementation* of it
(`CloudEventsJsonSerializer`), registered at default DI priority so it is opt-in
and never auto-active.

**Why the seam already exists.** `varco_core/event/serializer.py:116-117`
registers `JsonEventSerializer` at `@Singleton(priority=-sys.maxsize - 1)`, and
the module docstring states that any app-supplied `Serializer[Event]` wins at any
registration order. The extension point is built, documented, and already
exercised. Adding an implementation is additive under SemVer.

- ✅ Zero change to `Event` — a frozen Pydantic model with `__init_subclass__`
  auto-registration. No field added, no `model_dump()` shape changed, so no DLQ,
  outbox or audit consumer sees a byte move.
- ✅ Zero change to any bus: every backend already resolves the serializer via DI.
- ✅ Reversible per deployment — rebind and restart.
- ❌ A CloudEvents-serialized and a native-serialized event cannot share a channel
  mid-migration unless the consumer sniffs. Documentation-only mitigation (brief
  001's three-phase dual-emit timeline).

**Rejected — add CloudEvents fields to `Event` itself.** Every event in every app
would change shape; `source` has no correct process-wide default; the DLQ's
stored `event_payload` would retroactively change shape. A frozen base model is
the worst possible home for an optional envelope.

**Rejected — a per-bus `cloudevents=True` flag.** Three settings classes, three
code paths, and it duplicates a DI mechanism that already exists and is already
documented.

**Rejected — depend on the `cloudevents` SDK, or create a `varco_cloudevents`
package.** `varco_core` takes no new runtime dependency for something the repo's
own decision tree calls "extend the existing backend's interface"; structured
mode is ~120 lines with zero third-party imports. An optional
`varco-core[cloudevents]` extra is worse still — an extra that changes *which
implementation* is used means the same import produces different wire bytes
depending on installation state.

---

## RS-2 — `AbstractEventBus.publish()` will not gain `headers=`

**Promise.** The ABC method signature is frozen. When header-bearing transport
metadata is needed — CloudEvents *binary* mode on Kafka is the known case — it
arrives as a **new optional `MessageEncoder` Protocol**
(`encode(event) -> tuple[bytes, Mapping[str, str]]`), resolved by the backends
that have a native header channel, through an **optional, defaulted constructor
kwarg** on the bus. Never through `publish()`.

**Why this is the load-bearing decision of the whole standards question.**
`varco_kafka/bus.py:398` serializes and `:406`/`:408` call
`send_and_wait(topic, value=value)` with no `headers=` argument. Structured mode
is reachable today for NATS (spec-mandated: structured only) and Redis (no
official binding exists — varco defines its own convention). Kafka's binding
additionally requires a `content-type` header, which is unreachable without a
header seam. Deciding *now* that the seam is a new Protocol plus a defaulted
kwarg is what keeps CloudEvents entirely out of the breaking-change window.

- ✅ A new Protocol and a defaulted constructor kwarg are additive under SemVer.
  Changing an ABC method signature is not — it would break every out-of-tree
  `AbstractEventBus` implementation on upgrade.
- ✅ Headers are a *transport* concern; `AbstractEventBus.publish()` is
  deliberately transport-agnostic — the same seam rule CLAUDE.md applies to
  migrations (`varco_fastapi` imports only `varco_core.migration`) and tenancy.
- ✅ Backends with no header concept simply never resolve it — no dead parameter
  on their public API.
- ❌ Two serialization concepts (`Serializer[Event]` and `MessageEncoder`) where a
  naive design has one. Accepted, and mitigated by keeping `MessageEncoder`
  strictly optional and documenting it as "only for backends with a native header
  channel".

**Rejected — `publish(..., headers=None)` on the ABC.** Breaking for every
downstream implementation, and it puts a Kafka-shaped parameter on NATS and Redis
where it can only ever be dead.

**Rejected — a Kafka-only `publish_with_headers()`.** Same breakage risk deferred,
plus it splits the publish path in two for one backend and leaves the DLQ,
outbox and relay call sites having to choose between them.

---

## RS-3 — reserved names

These names are **reserved**: nothing else may claim them, and taking them later
is therefore purely additive rather than a rename.

| Name | For | Notes |
|---|---|---|
| `varco_core.event.cloudevents` | `CloudEventsSettings` + `CloudEventsJsonSerializer` (RS-1) | `CloudEventsSettings` must be registered via a `@Provider`, never `@Singleton` — CLAUDE.md's pydantic-`BaseSettings` rule |
| `varco_core.asyncapi` | The AsyncAPI 3.1.0 document generator | Backend-agnostic: reads only `varco_core.event` metadata and Pydantic schemas, same placement logic that keeps the SQLAlchemy query applicator inside `varco_core.query.applicator` |
| `varco_core.flags` | A future `FeatureFlags` Protocol, **if** OpenFeature's re-park trigger ever fires | No code, not even an ABC, ships in 3.0.0 — see §D-OF. Reserving the name is the entire cost of waiting |
| `export-asyncapi` (`varco.commands` entry-point group) | The CLI verb, with `--check` | Follows the existing `varco export-contract` precedent; the group is already used by `varco_sa`, `varco_fastapi`, `varco_beanie` |

**Why a verb name needs reserving at all.** `--check` compares a regenerated
document against a committed snapshot and exits non-zero on divergence — the same
snapshot-plus-`--check` shape `scripts/api_surface.py` already uses (§D-AUDIT).
That gate is only credible if the verb name is stable, because CI invocations and
contributor muscle memory both hard-code it.

**Rejected — a separate `varco-asyncapi` console script.** A second entry point
for one verb, when `varco_core/cli/main.py` already dispatches the
`varco.commands` group, adds a packaging surface for no user-visible gain.

---

## What this record does *not* promise

- It does not promise the work ships in 3.0.0. Phases 6–8 of Plan 022 are
  explicitly non-blocking for RL-9 and may be cut at the Phase 5 boundary.
- It does not fix the CloudEvents *attribute mapping* (that is §D-CE4, and it is
  internal to an implementation nobody is forced to bind).
- It does not reserve anything for OpenFeature beyond the module name. The spec
  is pre-1.0; shipping an ABC derived from it *inside a version freeze* is the
  one combination §D-OF rules out.
