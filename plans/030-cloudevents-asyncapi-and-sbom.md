# Plan 030 — CloudEvents envelope (N2), AsyncAPI export (N3), SBOM + CRA posture (D5)

Covers the three 🟡 **should** "emit a document to a spec" rows of BACKLOG's *"3.1 — API surface &
interop (discover, 2026-09-04)"* cycle: **N2** (CloudEvents, M), **N3** (AsyncAPI export, M),
**D5** (CycloneDX SBOM + CRA/NIS2 posture, S).

## Scope and siblings

One of four plans covering that cycle; see plan 029's *Scope and siblings* table for the full set.
This plan is **independent of 029, 031 and 032** and may be built in any order relative to them.

The three rows are grouped because they share one shape — *generate an artifact conforming to an
external specification, commit a snapshot, and gate on `--check`* — and therefore share the
snapshot/gate machinery and the "we own spec compliance forever" trade-off. They are otherwise
independent and each phase below is separately shippable.

**Research briefs backing this plan:**
- `design/research/004-flags-asyncapi-and-sbom-tooling.md` §2 (AsyncAPI), §3 (SBOM/CRA/NIS2).
- `design/research/005-idempotency-webhooks-and-cloudevents.md` §3 (CloudEvents).

**Prior design — implement, do not re-litigate.** N2 and N3 were fully designed in
`plans/022-api-freeze-and-standards-alignment.md` §D-CE1–§D-CE4 and §D-AA1–§D-AA4, with seams
recorded in `design/api-freeze-and-standards/reserved-seams.md` (RS-1, RS-2, RS-3). Those
decisions stand. This plan restates only what the new briefs **confirm, contradict, or add**.

## Goal

An app can opt into a spec-compliant CloudEvents envelope by binding one serializer. `varco
export-asyncapi` produces a committed, gate-checked AsyncAPI 3.1.0 document describing every wired
consumer. Each release publishes a CycloneDX SBOM, and varco's regulatory posture is written down
honestly rather than implied.

## Non-goals

- **No CloudEvents binary/header mode.** RS-2 promises `AbstractEventBus.publish()` will not gain
  `headers=`, and that promise is load-bearing. Structured mode only (§D-CE1/§D-CE2).
- **No change to `Event`.** §D-CE1's rejected alternative; still rejected.
- **No new runtime dependency in `varco_core`** — neither `cloudevents` nor any AsyncAPI library.
  See §D-N2-sdk, which re-examines this against new evidence and upholds it.
- **No Node toolchain in CI** for AsyncAPI validation (§D-AA4). One manual `npx @asyncapi/cli
  validate` run, recorded.
- **No claim of CRA compliance or certification.** §D-D5-posture. The document states a position;
  it is not legal advice and must not read as any.
- **No `servers` block by default** in the AsyncAPI document (§D-AA2).

---

## Design

### Phase order

```
P0  N2   🟡 M  varco_core/event/cloudevents.py + settings + docs
P1  N3a  🟡 M  varco_core/asyncapi/ generator
P2  N3b  🟡 S  export-asyncapi CLI verb + snapshot + make lint gate
P3  D5   🟡 S  CycloneDX in release.yml + PEP 770 + posture document
```

D5 last: it touches only CI and docs, so it can be cut at any checkpoint without stranding code.

---

## §D-N2-sdk — the `cloudevents` SDK is still rejected, and brief 005 does not change that

Brief 005 §3 reports the CNCF SDK is at v1.10.1, healthy, 10+ contributors, and recommends taking
it. §D-CE3 rejected it. **The rejection stands**, and the reasoning is stronger than the brief's,
because the brief was not asked to weigh varco's dependency policy:

- CLAUDE.md's decision tree is explicit — *"a new catalog format? → implement the ABC, do NOT add a
  runtime dependency to `varco_core`"* — and the same rule governs here.
- §D-CE3 puts the hand-rolled cost at **~120 lines** for structured mode only. That is not a
  dependency-shaped problem.
- Brief 005 §3 itself records the SDK carries a **"work-in-progress disclaimer — breaking changes
  possible with every update"**. Taking a dependency that disclaims its own stability, to avoid
  120 lines against a spec that has been stable since 2022 and is CNCF-Graduated, is the wrong
  trade in both directions.
- §D-CE3's third rejection (an optional `varco-core[cloudevents]` extra) is the worst option and
  remains so: an extra that changes *which implementation* runs makes the same import produce
  different wire bytes depending on installation state.

DESIGN: hand-roll, upholding §D-CE3 against new evidence
  ✅ Zero new runtime dependencies, the standing repo rule.
  ✅ Brief 005 §3's own stability disclaimer is evidence *for* the rejection, not against it.
  ❌ varco owns spec compliance forever. Mitigated exactly as §D-CE3 argued: v1.0.2 stable since
     2022, CNCF Graduated, new optional attributes arrive only in MINOR versions.

### §D-N2-attrs — brief 005 confirms §D-CE4's mapping, with one tightening

Brief 005 §3 independently confirms every choice in §D-CE4: the four REQUIRED attributes
(`id`/`source`/`specversion`/`type`), extension names restricted to lowercase ASCII letters and
digits, and structured JSON as `application/cloudevents+json`. `correlationid` and `tenantid` are
legal names under that rule and no registered extension covers either (brief 005 §3: *"No
registered extensions exist"* for tenant/correlation).

Three things brief 005 adds that §D-CE4 did not state:

1. **A 20-character recommended maximum** on extension names. `correlationid` (13) and `tenantid`
   (8) both fit. Record the limit in the docs so a future extension is not invented over it.
2. **`data` vs `data_base64` selection is normative**: use `data` when `datacontenttype` ends in
   `json` or `+json`, `data_base64` otherwise, and the two are **mutually exclusive**. §D-CE4 fixes
   `datacontenttype` to `application/json`, so varco always emits `data` — but the serializer must
   assert this rather than assume it, in case a future setting makes the content type configurable.
3. **`partitionkey` and `traceparent` are registered extensions.** varco should not invent names
   for either concept. `traceparent` in particular: varco already has OTel context, and emitting a
   home-grown trace attribute when a registered one exists would be a real interop defect. Filed
   as a parked follow-up rather than scope creep here.

### §D-N3-nodep — `datamodel-code-generator` was a mis-scoped risk; N3 is unblocked

The backlog flagged a `datamodel-code-generator` dependency risk as *"unassessed and `/plan`'s
first question"* (`BACKLOG.md`, N3 row). **Assessed and closed.**

Brief 004 §2: `datamodel-code-generator` generates *Python models from* JSON Schema/OpenAPI/Avro —
it **consumes** schemas and **cannot produce** an AsyncAPI document. It was never a candidate
dependency for this work. The one Python package that touches it (`asyncapi-python`) uses it
internally for the opposite direction (generating apps *from* a spec).

The path is: introspect wired consumers → build a plain `dict` → `model_json_schema()` for message
payloads → serialize with `json` and `pyyaml`. Brief 004 §2 confirms Pydantic v2 emits JSON Schema
Draft 2020-12 and that AsyncAPI 3.x accepts it without a compatibility concern.

Zero new dependencies. `pyyaml` — verify at Step 8 whether it is already a transitive dependency;
if not, JSON-only output is acceptable for v1 and YAML is a follow-up.

### §D-N3-version — 3.1.0, and the two sources agree

§D-AA1 targets AsyncAPI **3.1.0**; RS-3 reserves the name for *"the AsyncAPI 3.1.0 document
generator"*; brief 004 §2 independently confirms 3.1.0 is the current spec version. No
reconciliation needed. Kafka bindings per §D-AA3, with brief 004 §2 confirming the binding's
required `bindingVersion` field plus `partitions`/`replicas`/`topicConfiguration` (channel) and
`groupId`/`clientId` (operation).

§D-AA3's three-way coverage decision (Kafka bindings emitted; NATS only when a queue group is
configured; no Redis block at all) stands unchanged, as does the requirement to explain it inside
the generated document's own `info.description`.

### §D-D5-posture — the backlog's premise is wrong, and the document must say so

**The backlog calls D5 "an obligation, not a feature" and cites "EU CRA reporting obligations bind
from September 2026" (`BACKLOG.md`, D5 row). Brief 004 §3 contradicts this.**

- The CRA's non-commercial FOSS exemption applies to software not monetized by its developers.
  Accepting donations *exceeding development costs*, selling support, or offering SLA-backed
  hosting would constitute commercial activity and remove the exemption. varco does none of these.
- The "open-source software steward" category — lighter obligations, but obligations — covers
  entities supporting FOSS *for commercial purposes*. varco has no such backer.
- NIS2 does not bind FOSS authors at all; it binds *organizations using* the software.
- Full CRA enforcement is **2027-12-11**, not September 2026.

**Severity recommendation: D5 stays 🟡 but its rationale changes from obligation to credibility.**
It is worth doing — an SBOM is genuinely useful to downstream consumers who *are* CRA- or
NIS2-regulated, and that is the honest case for it — but nothing in this cycle is a compliance
deadline, and the plan must not inherit a false urgency. If the cycle needs to shed scope, D5 is
the cheapest row to cut and the least costly to defer.

DESIGN: ship the SBOM, write the posture honestly, claim nothing
  ✅ Downstream CRA/NIS2-regulated consumers get the artifact they actually need from an upstream.
  ✅ An honest "we believe the non-commercial FOSS exemption applies; here is why; here is what
     would change that" is more useful to a consumer's own assessment than a compliance claim.
  ❌ A reader may want a definitive answer we cannot give. Mitigated by stating plainly that this
     is a position, not legal advice, and that the exemption depends on facts (monetization) that
     could change.
  Rejected — **claiming CRA compliance**: ❌ false, unverifiable, and actively harmful to a
  consumer relying on it.
  Rejected — **saying nothing**: ❌ the SBOM without the posture invites the reader to guess.

### §D-D5-tooling — `cyclonedx-bom` via `uv export`, plus PEP 770 in the wheel

Brief 004 §3: the package is `cyclonedx-bom` v7.3.1 (renamed from `cyclonedx-py`), supports
CycloneDX 1.7, and has **no native `uv` integration** — the workaround is
`uv export --format requirements-txt | cyclonedx-bom`. PEP 770 is finalized, places SBOMs at
`.dist-info/sboms/` in the wheel, and PyPI does **not** yet serve them as a separate artifact.

So: both pathways, per brief 004 §3's recommendation — generate during `release.yml`'s build,
attach to the GitHub Release for discovery, and include in the wheel per PEP 770 for consumers who
only ever `pip install`.

⚠️ Ten distributions means ten SBOMs. Whether that is one per package or one workspace-wide
document is Open Question 2.

---

## Steps

### Phase 0 — N2: the CloudEvents serializer

1. [x] `varco_core/varco_core/event/cloudevents.py` — `CloudEventsJsonSerializer(Serializer[Event])`
       per §D-CE1, structured mode only, registered at **default** DI priority so it is opt-in and
       never auto-active (contrast `JsonEventSerializer` at `event/serializer.py:116-117`,
       `priority=-sys.maxsize - 1`).
2. [x] `CloudEventsSettings` — `source` **required with no default** (§D-CE4: there is no correct
       default for "who am I"; construction fails loudly). Registered via `@Provider`, never
       `@Singleton` (RS-3's note and CLAUDE.md's pydantic-BaseSettings rule).
3. [x] Attribute mapping exactly per §D-CE4's table, plus §D-N2-attrs' `data`/`data_base64`
       assertion. `tenantid` from `current_tenant()` only — never `RequestContext`, best-effort,
       absent under an `OutboxRelay`-driven publish (§D-CE4's ⚠️ constraint).
4. [x] Unit tests: every REQUIRED attribute present and non-empty; `specversion == "1.0"`;
       RFC 3339 `time`; round-trip through `deserialize`; `tenantid` present with an ambient tenant
       and **absent without one** (the documented best-effort behaviour, asserted so it cannot
       regress silently); construction fails without `source`; extension names match
       `^[a-z0-9]{1,20}$`.
5. [x] `technical_docs/features/cloudevents-envelope.md` — the **Redis convention** named and
       versioned (§D-CE4: whole envelope in a single stream field `ce`, never one field per
       attribute), the Kafka structured-mode `content-type` limitation and its §D-CE2 resolution
       path, the three-phase dual-emit migration timeline, and a Pitfalls table.
6. [x] Integration test: publish/consume through `varco_kafka` and `varco_redis` with the
       serializer bound, asserting the wire bytes are a valid CloudEvent.
7. [x] Import budget (`--warn-only`) + API surface snapshot regeneration.

⛔ **CHECKPOINT** — N2 is independently shippable here.

### Phase 1 — N3a: the generator

8. [x] `varco_core/varco_core/asyncapi/` per §D-AA4's home decision. Generator takes **consumer
       instances or a container**, never a static import walk — §D-AA1, because `@listen`'s channel
       may be `Callable[[Any], str]` (`event/consumer.py:180`) resolved at `register_to()` time
       against a bound `self`, which a static scan gets silently wrong.
9. [x] Map `@listen` metadata → AsyncAPI: channel string → **channel**; decorated handler →
       **operation** with `action: receive`; `Event` subclass → **message** with payload from
       `model_json_schema()` (§D-AA1, brief 004 §2).
10. [x] Bindings per §D-AA3 — Kafka channel (`topic`) and operation (`groupId`) bindings when a
        `KafkaEventBus` is the source; NATS operation binding **only** when a queue group is
        configured; **no** Redis binding block. Record all three choices in the generated
        document's own `info.description`.
11. [x] No `servers` block by default; `--server name=protocol://host` supplies one explicitly
        (§D-AA2 — a broker URL is deployment config, not source truth).
12. [x] Unit tests: callable-channel resolution; two consumers on one channel; an unregistered
        consumer is absent from the document (correct behaviour, asserted); Kafka bindings present
        and Redis bindings absent.

### Phase 2 — N3b: the CLI verb and the gate

13. [x] `varco_core/cli/asyncapi.py` — `varco export-asyncapi` with `--check`, registered in the
        `varco.commands` entry-point group (§D-AA4; precedent `varco_sa/pyproject.toml:67`,
        `varco_fastapi/pyproject.toml:74`, `varco_beanie/pyproject.toml:45`).
14. [x] Commit a snapshot generated from `examples/00-full-stack-post-api`'s consumers, so the gate
        has a real subject rather than a synthetic one.
15. [x] Wire `varco export-asyncapi --check` into `make lint`'s **no-`PKG` path** — beside
        `api-check` and `import-budget`, and deliberately skipped by `make lint PKG=<one>`
        (CLAUDE.md §D-C5). **No new CI job** (§D-AA4).
16. [x] Run `npx @asyncapi/cli validate` **once, by hand**, and record the output in
        `design/api-freeze-and-standards/measurements/`. §D-AA4 makes this worth doing exactly once
        because brief 002 §Evidence-gap 1 flags no blessed `schemaFormat` for Draft 2020-12. **No
        Node in CI.**
17. [x] `technical_docs/features/asyncapi-export.md` — including the local `npx` invocation for
        contributors.
18. [x] API surface snapshot; import budget.

⛔ **CHECKPOINT** — N3 shippable.

### Phase 3 — D5: SBOM and posture

19. [x] `.github/workflows/release.yml` — add an SBOM step per §D-D5-tooling:
        `uv export --format requirements-txt` → `cyclonedx-bom` (pin the version, per the repo's
        pinned-dev-dependency discipline). Attach to the GitHub Release.
20. [x] PEP 770: emit the SBOM into each wheel's `.dist-info/sboms/`. **Verify hatchling supports
        this** before committing to it — if it does not, attach to the GitHub Release only and file
        the wheel half as a follow-up row with the blocker named. Do not hand-patch wheels.
21. [x] `SECURITY.md` — verify presence and content; add coordinated-disclosure contact if absent.
22. [x] `docs/regulatory-posture.md` (or a `SECURITY.md` section) per §D-D5-posture: the CRA
        exemption position and *why*, the facts it depends on, what would change it (donations
        exceeding costs, paid support, SLA hosting), the 2027-12-11 full-enforcement date, NIS2's
        non-applicability to authors, and an explicit **"this is a position, not legal advice"**.
        Cite brief 004 §3.
23. [x] Amend the BACKLOG D5 row: its "obligation, not a feature" rationale and its "bind from
        September 2026" date are both corrected, with brief 004 §3 cited. Leave the row 🟡 but
        record the changed *reason*.
24. [x] Note in the posture doc that PyPI does not yet serve SBOMs as a discoverable artifact
        (brief 004 §3), so the GitHub Release is the canonical location for now.

⛔ **CHECKPOINT** — `make lint`, `make type-check`, `make test` green; a dry-run
`workflow_dispatch` of `release.yml` produces an SBOM artifact.

---

## Parked

| Item | Why | Un-park trigger |
|---|---|---|
| CloudEvents **binary/header mode** | RS-2 promises `publish()` gains no `headers=`; §D-CE2 routes it through a future `MessageEncoder` Protocol | A consumer needs varco events readable by a non-CloudEvents Kafka consumer |
| `traceparent` / `partitionkey` registered extensions | Real interop value (brief 005 §3), but scope creep on a row whose design is already fixed | 3.2, or a consumer correlating varco events in a CloudEvents-native tracing backend |
| AsyncAPI bindings beyond Kafka | §D-AA3: NATS has one field, Redis has zero | A binding spec gains real fields |
| AsyncAPI validation in CI | Needs Node 24+; §D-AA4 judges the cost/assurance trade poor | A Python AsyncAPI validator exists |
| CycloneDX VEX / vulnerability annotations | An SBOM is the artifact consumers asked for; VEX is a separate discipline | varco starts issuing security advisories at volume |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| ⚠️ **ASSUMPTION** — hatchling can place files in `.dist-info/sboms/` per PEP 770. Brief 004 §3 confirms the *standard* and its adoption but says nothing about our build backend | Medium — blocks half of Step 20 | ✅ **VERIFIED (Step 20): hatchling 1.31.0 supports it** — `[tool.hatch.build.targets.wheel] sbom-files`, `builders/wheel.py::add_sboms`. No degradation needed; a built `varco_core` wheel carries `.dist-info/sboms/varco-core.cdx.json`. One caveat found and handled: `sbom-files` entries are **literal existing paths** (no globbing, hard error if absent), so `scripts/sbom.py` injects the key at release time instead of committing it. No wheel was hand-patched |
| ⚠️ **ASSUMPTION** — `pyyaml` is already available; otherwise YAML output adds a dependency | Low | ✅ **CHECKED (Step 8): present in the dev environment (6.0.3, via the docs toolchain) but NOT a `varco_core` runtime dependency.** So YAML was not taken: the exporter is **JSON-only**, the accepted v1. YAML would be an optional extra at most |
| varco owns CloudEvents spec compliance forever (§D-CE3's accepted drawback) | Low | v1.0.2 stable since 2022, CNCF Graduated, MINOR-only additive changes |
| The AsyncAPI snapshot gate churns on unrelated changes, training contributors to regenerate blindly | Medium | Snapshot is generated from one example app's consumers, not the whole repo, so it moves only when that app's wiring moves |
| ⚠️ **ASSUMPTION** — varco is non-commercial FOSS and stays so. The entire §D-D5-posture position depends on this fact, not on a legal reading | Medium | The document states the dependency explicitly and lists what would void it. **We are not lawyers and the document says so** |
| A consumer treats the posture document as legal advice | Medium | Explicit disclaimer, required by Step 22 |
| ⚠️ **ASSUMPTION** — that no MCP-style surprise lurks in AsyncAPI 3.1.0's `reply`/`replyAddresses` additions. Brief 004 flags these as fetched-but-not-detailed | Low | varco emits `action: receive` pub/sub only; reply patterns are unused |

## Open questions

1. **Does the CloudEvents serializer round-trip through the DLQ?** A dead letter stores
   `event_payload`. If a CloudEvents-serialized event dead-letters, is the stored payload the
   envelope or the inner `data`? §D-CE1 promises "no DLQ consumer affected", which is true for
   *non-adopters*; adopters need an answer. Decide at Step 3 and test at Step 4.
   ✅ **DECIDED (Step 3): the WHOLE ENVELOPE.** A dead letter stores exactly the bytes the broker
   carried, so a redrive re-publishes an identical, still-spec-compliant message and an operator
   sees `source`/`tenantid`/`correlationid` in the row. Tested in
   `varco_core/tests/test_cloudevents_serializer.py::TestDeadLetterPayloadIsTheEnvelope`;
   documented in `technical_docs/features/cloudevents-envelope.md`.
2. **One SBOM per distribution, or one workspace-wide?** Ten distributions from one lockfile. Per
   distribution is more accurate (each has its own dependency subset) and more work; workspace-wide
   is one artifact that over-reports for any single package. Decide at Step 19 — lean per
   distribution, because an over-reporting SBOM is actively misleading to the consumer it exists
   to serve.
   ✅ **DECIDED (Step 19): one per distribution.** `uv export --package <name>` already yields
   exactly that subset, so accuracy costs one flag; measured over-report for the workspace-wide
   alternative is ~6x (`varco-core` 25 components vs. the workspace's 154). PEP 770 is per-wheel by
   construction, so a workspace-wide document would be wrong *inside* the wheel regardless.
   Reasoning recorded in `scripts/sbom.py`'s module DESIGN block.
