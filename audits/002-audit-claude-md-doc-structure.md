# Audit 002 — CLAUDE.md documentation-structure audit — 2026-08-23

## Summary
`CLAUDE.md` (2018 lines) has grown from "agent-facing guidance" into the project's de-facto architecture bible, user manual, and pitfall FAQ combined — roughly 1000 of its 2018 lines (the "Key Abstractions" subsystem narratives, the full "Common Scenarios" walkthrough, and most of the "Common Pitfalls" table) duplicate content that already exists in `ARCHITECTURE.md`, `README.md`, or `technical_docs/features/*.md`, or belongs in a README section that was never created (Profiling, Background Jobs, Database Auditing, DLQ, Encryption/crypto-shredding, A2A, and Casbin/policy-engine authorization have **no** README coverage at all today, so their CLAUDE.md "Scenario"/subsystem code is the only usage doc that exists). The single biggest risk is drift: several `technical_docs/features/*.md` files already say "see the CLAUDE.md pitfall table" for their own topic's pitfalls, meaning the source of truth for feature-specific troubleshooting currently sits in the wrong file and both copies (where they exist) can silently diverge.

## Findings (ranked by severity, then effort ascending)

### F1 · Package overview + dependency graph duplicates ARCHITECTURE.md
- **Severity:** 🔴 HIGH
- **Effort:** S
- **Sourced:** general principles
- **Location:** CLAUDE.md `## Architecture` section, lines 48-84 (package table lines 54-65, dependency graph lines 69-79)
- **Destination:** MOVE-TO-ARCHITECTURE (`ARCHITECTURE.md`'s existing `## Package Overview`, lines 7-121, already lists every package with richer per-module detail) + SUMMARIZE-IN-PLACE (keep 2-3 sentences + the "See ARCHITECTURE.md" pointer that already opens the file at line 5)
- **Rationale:** Two files now enumerate the same ten packages and the same dependency arrows; a new package or a changed dependency has to be edited in both places or the docs silently disagree. `ARCHITECTURE.md` is strictly more detailed (per-module file listing), so CLAUDE.md's copy is pure redundancy, not a useful summary.

### F2 · Per-subsystem "layer diagram + code snippet" pattern duplicates ARCHITECTURE.md's type hierarchies and README's usage sections
- **Severity:** 🔴 HIGH
- **Effort:** M
- **Sourced:** general principles
- **Location:** Event system (87-117), Service layer (119-133), Resilience (169-182), Cache system's `AsyncCache`/`CacheBackend` hierarchy diagram (428-440), Query system pipeline diagram (483-499), Transactional Outbox (516-531)
- **Destination:** split — diagrams → MOVE-TO-ARCHITECTURE (ARCHITECTURE.md's `## Type Hierarchy & Protocols` section already carries the identical `AbstractEventBus`/`AbstractDeadLetterQueue` hierarchies verbatim, e.g. lines 126-150 read); code snippets → MOVE-TO-README (README already has full, more complete sections for all six: "Event System", "Service Layer", "Resilience", "Cache System", "Query System", "Transactional Outbox"); the bolded **"Rule:"** sentences in each (e.g. "services must never hold or call `AbstractEventBus` directly") → KEEP / SUMMARIZE-IN-PLACE, these are the genuine agent-behavior content
- **Rationale:** Confirmed line-for-line duplication — e.g. CLAUDE.md:1136-1150 (`AbstractEventBus` ABC) is the same shape README already documents at "### AbstractEventBus", and the three-arrow event-layer diagram (CLAUDE.md:91-102) is the same diagram ARCHITECTURE.md's Type Hierarchy section and README's "Layer map" both already carry. Only the terse imperative rules are unique agent guidance; everything else is a third (or fourth) copy of the same fact.

### F3 · Ambient-context / i18n / Timezones / Error-taxonomy tetralogy is Plan-011 design narrative, not agent guidance
- **Severity:** 🔴 HIGH
- **Effort:** L
- **Sourced:** general principles
- **Location:** lines 267-369 (`### Ambient request context`, `### Internationalization`, `### Timezones`, `### Error taxonomy`) — ~100 lines
- **Destination:** SUMMARIZE-IN-PLACE, collapsing each to 2-3 sentences + a pointer. Full detail already exists and is explicitly cross-referenced at the end of each subsection: `technical_docs/features/i18n-and-localization.md`, `technical_docs/features/timezone-handling.md`, `technical_docs/features/error-taxonomy-and-i18n.md` (no dedicated doc exists yet for the `AmbientVar`/`RequestContext` primitive itself — fold that one into `context/ambient.py`'s own module docstring or add a short "Ambient context" subsection to `i18n-and-localization.md`, since X1 only exists to serve I2/T1)
- **Rationale:** This block re-derives decision rationale (RD-numbers, "byte-identical to today," precedence-chain reasoning, the `GettextMessageCatalog` vs. Flask-Babel comparison) that the linked technical docs already own in full. CLAUDE.md repeating it means two places to keep in sync on every future i18n/tz change.

### F4 · Reliability trio (DLQ, Background jobs, Database auditing) duplicates their technical docs and has zero README usage coverage
- **Severity:** 🔴 HIGH
- **Effort:** L
- **Sourced:** general principles
- **Location:** `### Dead Letter Queue` (184-231), `### Background jobs` (533-586), `### Database auditing` (587-640)
- **Destination:** split — design narrative (Plan 005/009 rationale, `DeadLetterEntry` source enum, lease-fencing internals, hash-chain tamper evidence) → SUMMARIZE-IN-PLACE (already pointed to `technical_docs/features/dead-letter-queues.md`, `job-scheduling-and-leases.md`, `database-auditing.md`); the usage code (the `try_claim`/`renew`/`save` snippet at 543-548, the zoned-`enqueue` snippet at 571-577, the `AuditWiring`/`AuditLogMixin` class example at 596-613) → MOVE-TO-README as **new** sections ("Background Jobs", "Database Auditing" are absent from README's table of contents entirely; DLQ has no README section either)
- **Destination note:** README's TOC has no "Dead Letter Queue", "Background Jobs", or "Database Auditing" entries at all — for these three features, CLAUDE.md is currently the *only* place an app developer can learn how to use them.
- **Rationale:** These are fully shipped, user-facing features with real usage patterns (wiring a `JobRunner` with leases, wiring an `AuditConsumer`) that a library user would reasonably look for in README, not in an agent-instructions file. Leaving them only in CLAUDE.md means `README.md`'s own promise ("full API reference") is broken for three shipped features.

### F5 · Multitenancy section is the single largest duplicate block (~150 lines)
- **Severity:** 🔴 HIGH
- **Effort:** L
- **Sourced:** general principles
- **Location:** lines 912-1065 (`### Multitenancy — isolation strategies, control plane, global scope`)
- **Destination:** SUMMARIZE-IN-PLACE — collapse to a short paragraph (isolation strategies exist, opt-in, default is byte-identical) + pointer; `technical_docs/features/multitenancy.md` already owns every decision (RD-4, RD-7, RD-9 through RD-18) this section re-narrates, and the file's own line 891 already says *"See the CLAUDE.md 'Multitenancy' section's Common Pitfalls rows"* — i.e. the tech doc and CLAUDE.md already point at each other, evidence of the split being organically wrong today. The one bit of genuinely new usage code (the `mount_tenant_admin()` snippet, 1049-1058) → MOVE-TO-README, appended to the existing "Multi-tenancy (DB-level)" section, which currently stops at the static `TenantUoWProvider` and only gestures at `varco_core.tenancy` in two sentences (README lines 979-993)
- **Rationale:** This is the densest concentration of internal design rationale (broadcast-vs-local command/fact DAG rules, readiness coordinator semantics, RD-13/RD-16/RD-17 reasoning) in the whole file — none of it is "how should an agent navigate this repo," all of it is "why was multitenancy built this way," which is exactly the technical_docs/features mandate.

### F6 · Schema migrations section duplicates schema-migrations.md
- **Severity:** 🟡 MEDIUM
- **Effort:** M
- **Sourced:** general principles
- **Location:** lines 841-911 (`### Schema migrations`)
- **Destination:** SUMMARIZE-IN-PLACE — the multi-pod exclusion mechanism (held-open transaction, `SET LOCAL idle_in_transaction_session_timeout`), the ten-framework-table branch design, and the `ensure_table()` reconciliation story are all already the subject of `technical_docs/features/schema-migrations.md` (confirmed cross-referenced at line 910 and independently referenced by `technical_docs/features/schema-migrations.md:190` for the advisory-lock pitfall). README already has its own "Schema Migrations" usage section, so no README gap exists here — this is pure narrative duplication, not a missing-doc case.
- **Rationale:** Same failure mode as F3/F5 at smaller scale: two owners for one set of facts (the ASCII diagram of `AbstractMigrator` → `AlembicMigrator`/`BeanieMigrator` → `varco_core.cli` is also a second copy of ARCHITECTURE.md-style type-hierarchy content).

### F7 · Remaining feature-narrative blocks duplicate their own technical docs
- **Severity:** 🔴 HIGH
- **Effort:** M
- **Sourced:** general principles
- **Location:** Observability params/global-attributes (238-266), Cache stampede-protection + bulk-ops (444-481), Encryption/crypto-shredding (641-669), A2A protocol surface (670-707), JWT claim-transform + token profiles (738-790, excluding the env-var reference table), Authorization policy engine / Casbin (792-840)
- **Destination:** SUMMARIZE-IN-PLACE for all six — each already ends with (or has a sibling file for) the full detail: `technical_docs/features/observability-attributes.md`, `cache-hardening.md`, `crypto-shredding.md`, `a2a-surface.md`, `jwt-claim-transformer.md` + `token-profiles.md`, `casbin-authorization.md`
- **Rationale:** Same pattern repeated six times — a paragraph of "why," a Plan/decision number, and a "See technical_docs/..." pointer at the end, meaning the paragraph itself is redundant with what's behind the pointer. The `VARCO_JWT_*` env-var reference **table** (778-790) is the one piece of genuine "quick lookup" content worth keeping as-is or moving to README (it's a pure reference table an app developer configuring env vars would want, arguably MOVE-TO-README next to the JWT/Authority section).

### F8 · Profiling documented twice inside CLAUDE.md itself, and zero times in README
- **Severity:** 🔴 HIGH
- **Effort:** M
- **Sourced:** general principles
- **Location:** `### Profiling` (370-426) and `#### Scenario: Profile a slow operation` (~1310-1349) — near-identical decorator/context-manager/custom-backend examples appear in both places within the same file
- **Destination:** MOVE-TO-README — create a new "Profiling" section (README's TOC has no entry for it at all today) merging the two copies into one; leave a single sentence + pointer in CLAUDE.md ("off by default, see README's Profiling section / `varco_core.profiling`")
- **Rationale:** This is the clearest case of the file duplicating *itself*, not just other docs — a maintainer updating the profiling API has two near-identical code blocks to keep in sync in one file, and an app developer looking for "how do I profile my app" finds nothing in README, the file that markets itself as "full API reference."

### F9 · Entire "Common Scenarios" block is user-facing usage content (15 scenarios, ~560 lines)
- **Severity:** 🔴 HIGH
- **Effort:** L
- **Sourced:** general principles
- **Location:** lines 1090-1647 — 15 `#### Scenario:` subsections (event handler, service caching, list filtering, `GenericRouter`, typed CRUD custom methods, composite deployment, profiling, external-API resilience, foreign JWT consumption, token-profile gating, non-router A2A source, schema-per-tenant onboarding, calling a peer service, cross-repo contract client, one-line durability opt-in)
- **Destination:** MOVE-TO-README — for topics with an existing README section (caching, resilience, query filtering, event handling), fold the scenario in as a worked example under that section; for topics with **no** README section today (composite deployment — though `technical_docs/features/composite-deployment.md` exists for the design, not usage; A2A non-router source; cross-repo contract client — though `technical_docs/features/portable-contracts.md` exists; reliability preset one-liner), create new README sections
- **Rationale:** This is a textbook "how do I use the library" tutorial chapter, not agent-behavior guidance — it is addressed to "you" the app developer ("Use `GenericRouter` when the server has no `AsyncService`..."), not to an agent deciding where code belongs. At ~560 lines it is over a quarter of the entire CLAUDE.md file.

### F10 · Giant "Common Pitfalls" table mixes agent coding-pattern traps with app-operator troubleshooting FAQ
- **Severity:** 🟡 MEDIUM
- **Effort:** L
- **Sourced:** general principles
- **Location:** lines 1720-1850 (`## Common Pitfalls & How to Avoid Them`), ~100+ rows
- **Destination:** split — keep ~15-20 rows that are pure code-pattern traps an agent must not reintroduce while writing new code (shared `CircuitBreaker`/`Bulkhead`/`Singleflight`/`RedisPubSubBackplane` instance rule, lazy `asyncio.Lock`, mixin `super()` chaining, quoted `@Provider`/`TypeAlias` annotations, `@Singleton` on pydantic `BaseSettings`) in CLAUDE.md; redistribute the remaining feature-specific operator-facing rows (wrong env var → wrong runtime behavior, e.g. "Roles empty although the JWT has them," "tzdata absent in a slim image," "`?lang=xx` silently ignored") into each feature's own technical_docs/features/*.md, most of which already have (or should have) a "Pitfalls" subsection
- **Rationale:** Several technical docs already point *back* at this table instead of owning their own pitfalls: `technical_docs/features/observability-attributes.md:449` ("see the main CLAUDE.md pitfall table"), `technical_docs/features/multitenancy.md:891`, `technical_docs/features/opa-design.md:68`, `technical_docs/features/distributed-locks.md:102`. This is the inverse of the intended relationship — the feature doc should be self-contained and CLAUDE.md should (at most) point *to* it. Note: the DI-wiring-specific rows in this same table were already scoped as a separate concern in `audits/001-audit-di-wiring.md` (F5) and `plans/013-refactor-di-wiring-docs-tests.md` — this finding is about the other ~90 rows.

### F11 · SQLAlchemy backend section is a redundant stub
- **Severity:** 🟡 MEDIUM
- **Effort:** S
- **Sourced:** general principles
- **Location:** lines 232-236 (`### SQLAlchemy backend (varco_sa)`)
- **Destination:** SUMMARIZE-IN-PLACE / fold into ARCHITECTURE.md's existing `varco_sa/` package-overview entry (ARCHITECTURE.md lines 69-85 already describe `SAModelFactory`/`SAConfig` and every module in more detail); README already has a full "SQLAlchemy Backend" usage section
- **Rationale:** Two sentences that add nothing beyond what both other docs already say — lowest-effort, lowest-risk cleanup in the file.

### F12 · Test Conventions' RT1/RT6 paragraphs are narrative-heavy for a "conventions" list
- **Severity:** 🟢 LOW
- **Effort:** S
- **Sourced:** general principles
- **Location:** lines ~1685-1719 (the "Shared, session-scoped integration containers" and "Conformance suite opt-in" paragraphs inside `## Test Conventions`)
- **Destination:** KEEP the section overall (it is genuine "how must an agent write/run tests here" guidance), but consider trimming these two paragraphs to bullets — no existing dedicated doc to move them to; this is a judgment call, not a clear misplacement
- **Rationale:** Borderline — unlike the findings above, this content has no duplicate elsewhere and is directly actionable for an agent writing a test, so it arguably belongs in CLAUDE.md as-is. Flagged only because it is denser prose than the rest of the section's bullet-point style.

## Not-findings (deliberate, leave alone)
- **`### DI wiring verb taxonomy` table** (lines 147-167) — looks like exactly the kind of reference table this audit would flag, but `plans/013-refactor-di-wiring-docs-tests.md:76-80` explicitly considered moving it to a new `technical_docs/features/di-wiring.md` and rejected it: *"the audit's own framing is that this gap is 'exactly the CLAUDE.md-style gap this file's own conventions are good at closing everywhere else'... Rejected; CLAUDE.md it is."* Respecting that prior decision.
- **`## Commands`** (9-46) — pure "how to run tests/build from this workspace root" agent guidance, no duplication found elsewhere.
- **`### Before Adding a Feature`** (1070-1088) — terse decision aid, correctly agent-facing.
- **`## Coding Standards`** (1648-1660) — short, non-duplicated, directly actionable rules.
- **`## Decision Tree: What to Implement Where?`** (1851-1999) — this is precisely the "how should an agent navigate/decide" content the task description calls out as legitimate CLAUDE.md material.
- **`## Pre-Implementation Checklist`** (2000-2018) — same rationale as the Decision Tree.

## Suggested batches
- **Batch A (quick wins, mechanical):** F1, F11 — merge into ARCHITECTURE.md, delete duplicate prose from CLAUDE.md, leave one-line pointers.
- **Batch B (extract + create README sections for undocumented features):** F4, F8, F9 — the highest-value batch: create README sections for Profiling, Background Jobs, Database Auditing, DLQ, A2A-non-router, composite deployment, and durability-preset, folding in the existing CLAUDE.md/Scenario code; needs a pass to de-duplicate the Profiling double-copy (F8) as part of the same edit.
- **Batch C (trim narrative to pointers):** F2, F3, F5, F6, F7 — mechanical "keep 2-3 sentences + pointer" edits across ~15 subsections; lowest risk (technical_docs/features/*.md already contain the full text in every case), but touches the most call sites (see the consolidated cross-reference list below) since several tech docs point *back* at these CLAUDE.md sections.
- **Batch D (pitfall table triage):** F10 — needs a manual per-row pass to decide "coding-pattern trap → keep" vs. "feature-specific FAQ → move," then update the ~5 tech docs that currently point back at the CLAUDE.md table instead of owning their own pitfalls section.

---

## Consolidated finding: cross-references to CLAUDE.md that will need updating once content moves

Repo-wide grep for `CLAUDE\.md` (excluding `.claude/worktrees/*`, which are mirrored branch copies of the same files and would be updated automatically by whatever process syncs those worktrees). Grouped by why the reference exists and whether Batch B/C/D above would require touching it:

**Docs that explicitly point back at a CLAUDE.md section for detail (will need re-pointing once that section moves under Batch C/D):**
- `ARCHITECTURE.md:119` — "See *Authorization* in CLAUDE.md" (points at the Authorization/policy section, F7)
- `technical_docs/features/observability-attributes.md:449` — "see the main CLAUDE.md pitfall table" (F10)
- `technical_docs/features/multitenancy.md:891` — "See the CLAUDE.md 'Multitenancy' section's Common Pitfalls rows" (F5 + F10)
- `technical_docs/features/opa-design.md:68` — "see the resilience rules in CLAUDE.md" (F10, resilience rows are staying, so this one likely needs no change)
- `technical_docs/features/distributed-locks.md:102` — "per CLAUDE.md's DI..." (F10-adjacent)
- `technical_docs/features/cache-hardening.md:360` — "CLAUDE.md's pre-implementation checklist" (checklist stays — no change needed)
- `technical_docs/features/dead-letter-queues.md:258,407` — "CLAUDE.md's layer-rule"/"CLAUDE.md's [migration recipe]" (layer rule stays; migration-recipe language may move under F4/F6)
- `technical_docs/features/schema-migrations.md:190` — "the U-16 defect already in CLAUDE.md's [pitfall table]" (F10)

**Code comments/docstrings citing a CLAUDE.md rule (these cite *rules* that this audit recommends KEEPING — e.g. shared-instance, lazy-lock, layer-rule — so most need no change; flagged here only in case F10's row-by-row triage moves the specific row they cite):**
- `varco_redis/varco_redis/bulkhead.py:443,453`
- `varco_redis/varco_redis/backplane.py:14,133,170`
- `varco_sa/varco_sa/rls.py:86`
- `varco_sa/varco_sa/rls_framework.py:15`
- `varco_sa/varco_sa/di.py:158`
- `varco_fastapi/varco_fastapi/app.py:468`
- `varco_fastapi/varco_fastapi/client/peer.py:19,317`
- `varco_fastapi/pyproject.toml:100`
- `varco_core/varco_core/cache/singleflight.py:17,31,105`
- `varco_core/varco_core/cache/backplane.py:91`
- `varco_core/varco_core/cache/decorator.py:101,211`
- `varco_core/varco_core/cache/mixin.py:238,317,408`
- `varco_core/varco_core/migration/settings.py:11`
- `testkit/varco_conformance/dlq.py:130`
- `testkit/varco_conformance/cache.py:119`

**Test files citing a CLAUDE.md pitfall/convention (mostly cite rules staying in CLAUDE.md — timing-margin convention, shared-breaker rule, per-call-bulkhead rule; low update risk):**
- `varco_beanie/tests/test_beanie_di.py:294`
- `varco_redis/tests/test_redis_dlq_integration.py:154`
- `varco_redis/tests/test_redis_bulkhead.py:198`
- `varco_redis/tests/test_breaker_chaos_integration.py:48`
- `varco_kafka/tests/test_kafka_dlq_integration.py:10,132`
- `varco_kafka/tests/test_kafka_offsets_integration.py:15`
- `varco_kafka/tests/test_kafka_rebalance_integration.py:11,30`
- `varco_sa/tests/test_job_lease_chaos_integration.py:9`
- `varco_sa/tests/test_outbox_chaos_integration.py:111,165`
- `varco_sa/tests/test_rls_migration_ops.py:8`
- `varco_sa/tests/test_migration_di.py:4,17`
- `varco_sa/tests/test_sa_di.py:47`
- `varco_fastapi/tests/test_localization_middleware_ordering.py:77,90,96`
- `varco_fastapi/tests/test_i18n_app.py:36`

**Example apps citing a CLAUDE.md rule:**
- `examples/19-resilience-payment-gateway/router.py:19`
- `examples/19-resilience-payment-gateway/app.py:66`
- `examples/00-full-stack-post-api/example/tests/conftest.py:218`

**Internal `.claude/` process files that drive agent behavior by naming CLAUDE.md sections (these need re-pointing if the named section's content is redistributed — most reference "Decision Tree" / "Common Pitfalls" / "Pre-Implementation Checklist," all of which this audit recommends KEEPING in place, so risk is low, but F10's pitfall-table redistribution affects several):**
- `.claude/agents/release-planner.md:29,40,54,80,131,150,231`
- `.claude/agents/feature-doc-writer.md:31,85,115,187`
- `.claude/agents/feature-test-writer.md:21,164`
- `.claude/agents/feature-implementer.md:18,32,36,160,176,224,327`
- `.claude/agents/api-docs-maintainer.md:55,167`
- `CODING_STANDARD.md:809`

**Historical/frozen artifacts — grep matches present but explicitly out of scope for updating (they are point-in-time records of decisions already shipped, not living documentation):**
- `audits/001-audit-di-wiring.md` (multiple lines) — a prior audit, itself a historical record.
- `plans/001-*.md` through `plans/014-*.md` (dozens of matches) — shipped/in-review plan documents; editing them after the fact would falsify the historical record of what was decided when.
- `.claude/agent-memory/release-planner/project_qol_scan_2026_06_18.md:27,59,62` — agent scratch memory, not a repo doc.
- `docs/peer-service-integration.md:86` — cites the per-call-CircuitBreaker rule, which is staying; no change needed but listed for completeness.

**Duplicated worktree copies (excluded from the counts above, would follow whatever the primary files do):**
- `.claude/worktrees/agent-af91380c/CODING_STANDARD.md:809`
- `.claude/worktrees/feature+examples-catalog/ARCHITECTURE.md:104`
- `.claude/worktrees/feature+examples-catalog/technical_docs/features/opa-design.md:68`
- `.claude/worktrees/qol-release/ARCHITECTURE.md:104`
- `.claude/worktrees/qol-release/CLAUDE.md:1`
- `.claude/worktrees/qol-release/CODING_STANDARD.md:809`
- `.claude/worktrees/qol-release/.claude/agents/*.md` (release-planner, feature-doc-writer, feature-test-writer, feature-implementer, api-docs-maintainer)
- `.claude/worktrees/qol-release/technical_docs/features/opa-design.md:68`
