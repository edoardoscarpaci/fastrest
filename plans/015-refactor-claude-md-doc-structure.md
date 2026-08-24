# Plan 015 — CLAUDE.md documentation-structure refactor (audit 002, F1–F11)

## Goal

`CLAUDE.md` shrinks from 2018 lines to a focused agent-instructions file: commands,
layer **rules**, the DI verb taxonomy, a triaged coding-pattern pitfall table, the
Decision Tree, and the Pre-Implementation Checklist. Every fact it currently duplicates
lands in exactly one owning file — `ARCHITECTURE.md` (type hierarchies, package map),
`README.md` (how an app developer uses the library), or `technical_docs/features/*.md`
(why a feature was built this way + its operator pitfalls) — and every cross-reference
that pointed at moved CLAUDE.md content is re-pointed at the new owner.

Success criterion: **zero content deleted without a verified destination.** Every block
removed from CLAUDE.md is provable-by-grep to exist in its new home.

## Non-goals

- **F12 is explicitly out of scope.** The audit marks the Test Conventions RT1/RT6
  paragraphs as "a judgment call, not a clear misplacement." Do not touch
  `## Test Conventions`.
- No changes to the audit's **"Not-findings"** set: `## Commands` (9–46),
  `### DI wiring verb taxonomy` (147–167 — explicitly kept by `plans/013`),
  `### Before Adding a Feature` (1070–1088), `## Coding Standards` (1648–1660),
  `## Test Conventions` (1661–1719), `## Decision Tree` (1854–2002),
  `## Pre-Implementation Checklist` (2003–2018).
- **No runtime code changes.** Docstrings and code comments that cite a CLAUDE.md rule
  are left alone (the audit confirms they cite rules that are *staying*), with one
  exception handled in Phase 5 only if Phase 4's triage actually moves the cited row.
- **No edits to frozen artifacts**: `audits/001-*.md`, `audits/002-*.md`,
  `plans/001-*.md` … `plans/014-*.md`, `.claude/agent-memory/**`.
- **No edits under `.claude/worktrees/**`** — mirrored copies, synced by a separate process.
- No DI-wiring pitfall rows are re-triaged: rows owned by `audits/001-audit-di-wiring.md`
  F5 / `plans/013` / `plans/014` stay verbatim in CLAUDE.md.
- No new `technical_docs/features/*.md` file is created except where Phase 4 needs a
  `## Pitfalls` heading appended to an existing one.
- Not a rewrite: prose is **moved and trimmed**, not re-authored. Preserve wording so the
  fingerprint greps in the verification steps actually match.

## Design

Three owning files, one rule each:

```
CLAUDE.md            → "what must an agent DO / NOT DO in this repo"
                       imperative rules, verb taxonomy, decision tree, code-pattern traps
ARCHITECTURE.md      → "what exists and how the types relate"
                       package map, dependency graph, type hierarchies, module listings
README.md            → "how do I, an app developer, use this"
                       runnable usage snippets, env-var reference tables
technical_docs/
  features/*.md      → "why was this built this way" + per-feature operator Pitfalls
```

The refactor is four batches, sequenced so each is independently reviewable and
independently revertable (one commit per phase):

```
Phase 0  baseline capture + cross-ref inventory   (characterization — no edits)
Phase 1  Batch A — F1, F11        mechanical merges into ARCHITECTURE.md
Phase 2  Batch B — F4, F8, F9     NEW README sections (highest value)
Phase 3  Batch C — F2,F3,F5,F6,F7 trim ~17 narratives to 2-3 sentences + pointer
Phase 4  Batch D — F10            pitfall-table row-by-row triage
Phase 5  cross-reference sweep + final verification against F1–F11 + Not-findings
```

**Ordering rationale.** C precedes D because C establishes the "CLAUDE.md points *at*
the feature doc" direction; D then fills those feature docs with the pitfall rows,
finally inverting the current backwards relationship (`observability-attributes.md:449`
→ "see the main CLAUDE.md pitfall table"). B precedes C because C's trimmed summaries
must be able to point at the README sections B creates.

**The trimming template** (apply verbatim in Phase 3, so the result is uniform):

```markdown
### <Subsystem> (<module path>)

<1-2 sentences: what it is and the one thing that makes it non-obvious.>

**Rule**: <the existing bolded imperative, copied VERBATIM — do not reword.>

Full detail: `technical_docs/features/<file>.md`. Usage: README's "<Section>".
Types: ARCHITECTURE.md's "<Section>".
```

**Content-preservation verification** is by *fingerprint grep*, not by eyeball: for each
moved block, 2–3 distinctive literal strings are recorded in Phase 0 and re-grepped in the
destination file after the move. A fingerprint that is absent from the destination means
content was dropped — the step fails.

### Alternatives considered

- **Delete the duplicated prose outright, add no destination.**
  ✅ Fastest, smallest diff. ❌ Rejected: F4/F8/F9 content is the *only* usage
  documentation that exists for Profiling, Background Jobs, Database Auditing, DLQ,
  non-router A2A, composite deployment, and the durability preset — README has no TOC
  entry for any of them (verified: `README.md:55-152`). Deleting would break README's own
  "full API reference" promise for seven shipped features.
- **Leave CLAUDE.md as-is; only add "see also" pointers to the tech docs.**
  ✅ Zero risk of losing content. ❌ Rejected: does not fix the actual defect. The audit's
  finding is *drift* — two copies of the same fact that can silently diverge — and adding
  a pointer next to a duplicate leaves both copies in place.
- **Split agent guidance into `CLAUDE.md` + a new `AGENT-PITFALLS.md`.**
  ✅ Keeps CLAUDE.md tiny. ❌ Rejected: Claude Code auto-loads `CLAUDE.md` only; a sibling
  file needs an explicit read, so the code-pattern traps would stop being ambient guidance
  — exactly the property that makes them worth keeping.
- **One big commit for all four batches.**
  ✅ No intermediate half-moved state. ❌ Rejected: a ~1000-line docs diff is unreviewable,
  and a mistake in Batch D's 100-row triage would force reverting Batch B's genuinely
  valuable README sections along with it.
- **Move the `VARCO_JWT_*` env table into a new `technical_docs/features/jwt-verification.md`.**
  ✅ Keeps README shorter. ❌ Rejected: it is a pure lookup table an app developer reads
  while writing a `.env` file, and README already owns the "JWT / Authority System" section
  it belongs next to (`README.md:2150`). F7 itself flags README as the arguable destination.

---

## Steps

### Phase 0 — Baseline capture & cross-reference inventory (characterization, no edits)

Everything in this phase is read-only. It produces the evidence later phases verify against.

1. [ ] `mkdir -p /tmp/varco-doc-refactor` — scratch, never committed.
2. [ ] Snapshot the pre-edit working-tree state of every file this plan may touch:
   ```bash
   cd /home/edoardo/projects/varco
   D=/tmp/varco-doc-refactor
   mkdir -p "$D/before/technical_docs/features"
   cp CLAUDE.md ARCHITECTURE.md README.md "$D/before/"
   cp technical_docs/features/*.md "$D/before/technical_docs/features/"
   wc -l CLAUDE.md ARCHITECTURE.md README.md technical_docs/features/*.md > "$D/before/wc.txt"
   ```
   Record the baseline in the plan's Verification section as you go. Expected today:
   `CLAUDE.md` = 2018 lines.
3. [ ] `CLAUDE.md` — capture the section-boundary manifest (this is the line-range
   snapshot the later phases diff against):
   ```bash
   rg -n '^#{2,4} ' CLAUDE.md > /tmp/varco-doc-refactor/before/claude-sections.txt
   ```
   Confirm it matches the ranges this plan assumes (verified at plan time):
   `## Architecture` 48 · `### Dependency graph` 67 · `### Event system` 87 ·
   `### Service layer` 119 · `### DI wiring verb taxonomy` 147 · `### Resilience` 169 ·
   `### Dead Letter Queue` 184 · `### SQLAlchemy backend` 232 · `### Observability` 238 ·
   `### Ambient request context` 267 · `### Internationalization` 297 · `### Timezones` 322 ·
   `### Error taxonomy` 338 · `### Profiling` 370 · `### Cache system` 428 ·
   `### Query system` 483 · `### Transactional Outbox` 516 · `### Background jobs` 533 ·
   `### Database auditing` 587 · `### Field-level encryption` 641 · `### A2A protocol surface` 670 ·
   `### Authority / JWT system` 708 · `#### Claim transformation + token profiles` 738 ·
   `### Authorization — policy engine` 792 · `### Schema migrations` 841 ·
   `### Multitenancy` 912 · `### Common Scenarios` 1090 (15 `#### Scenario:` blocks,
   1092–1647) · `## Coding Standards` 1648 · `## Test Conventions` 1661 ·
   `## Common Pitfalls` 1720 · `## Decision Tree` 1854 · `## Pre-Implementation Checklist` 2003.
   **If any heading has drifted, update this plan's line numbers before proceeding** —
   `CLAUDE.md` is currently uncommitted-modified, so a re-verify is mandatory, not optional.
4. [ ] Count the pitfall table rows to be triaged in Phase 4:
   ```bash
   sed -n '1720,1852p' CLAUDE.md | rg -c '^\| \*\*' 
   ```
   Record the number. Phase 4 must account for **every** row: kept, or moved with a named
   destination file.
5. [ ] Build the cross-reference inventory and confirm each site the audit flags actually
   exists at the stated location:
   ```bash
   rg -n 'CLAUDE\.md' --glob '!.claude/worktrees/**' --glob '!plans/0*.md' \
      --glob '!audits/**' --glob '!.claude/agent-memory/**' \
      > /tmp/varco-doc-refactor/before/xrefs.txt
   ```
   Spot-verified at plan time — these six are the "docs that point back at a CLAUDE.md
   section" set that **must** be re-pointed:

   | File:line | Current text (abbreviated) | Points at | Re-pointed in |
   |---|---|---|---|
   | `ARCHITECTURE.md:119` | "See *Authorization* in CLAUDE.md" | F7 Casbin narrative | Phase 3 |
   | `technical_docs/features/observability-attributes.md:449` | "see the main CLAUDE.md pitfall table" | F10 obs rows | Phase 4 |
   | `technical_docs/features/multitenancy.md:891` | "See the `CLAUDE.md` \"Multitenancy\" section's Common Pitfalls rows" | F5 + F10 | Phase 4 |
   | `technical_docs/features/schema-migrations.md:190` | "the U-16 defect already in CLAUDE.md's [pitfall table]" | F10 lock row | Phase 4 |
   | `technical_docs/features/dead-letter-queues.md:258` | "see CLAUDE.md's layer-rule" | layer rule (**stays**) | no change — assert only |
   | `technical_docs/features/dead-letter-queues.md:407` | "per CLAUDE.md's [migration recipe]" | F4/F6 | Phase 3 |

   And these three are **assert-no-change** sites (they cite rules this plan keeps):
   `technical_docs/features/opa-design.md:68` (resilience shared-instance rules — kept),
   `technical_docs/features/cache-hardening.md:360` (pre-implementation checklist — kept),
   `technical_docs/features/distributed-locks.md:102` (DI binding-priority rule — kept).
6. [ ] Record the "must not be touched" guard list, to be re-asserted after every phase:
   ```bash
   git status --porcelain | rg '^(audits/00|plans/0(0|1[0-4])|\.claude/worktrees/|\.claude/agent-memory/)'
   ```
   Must print nothing at the end of every phase.
7. [ ] Confirm no automated test asserts on documentation content, so the whole refactor
   cannot break the suite (verified at plan time: every `*.md` mention in `**/test_*.py` is
   a prose comment, never a file read). Re-confirm:
   ```bash
   rg -n "open\(.*\.md|Path\(.*\.md" --glob '**/test_*.py'
   ```
   Expect zero hits. If any appear, add them to this plan's Risks before continuing.

### Phase 1 — Batch A: mechanical merges into ARCHITECTURE.md (F1, F11)

8. [ ] `ARCHITECTURE.md` — in `## Package Overview` (starts line 7), confirm all ten
   packages from `CLAUDE.md:54-65` are already listed with equal-or-richer detail. Add any
   missing one-line role descriptor (in particular check `varco_nats`, `varco_ws`,
   `varco_memcached`, `varco_casbin` each carry a role sentence).
9. [ ] `ARCHITECTURE.md` — append the dependency-graph ASCII block from `CLAUDE.md:69-79`
   (verbatim, including the two parenthetical notes about `varco_fastapi`/`varco_casbin`
   optional extras) plus the sentence at `CLAUDE.md:81` ("`varco_core` is the only package
   without a `[tool.uv.sources]` sibling reference…") to the end of `## Package Overview`,
   under a new `### Dependency graph` subheading.
10. [ ] `ARCHITECTURE.md` — in the `varco_sa/` package-overview entry (≈ lines 69-85),
    confirm `SAModelFactory` and `SAConfig` are described. Append the one fact
    `CLAUDE.md:236` adds that ARCHITECTURE.md may lack: *"`SAConfig` doubles as the DI
    settings object, avoiding a parallel `SASettings` class."*
11. [ ] `CLAUDE.md:48-84` — replace the `## Architecture` body (package block + dependency
    graph) with 2–3 sentences: uv workspace monorepo, ten packages, `varco_core` has no
    sibling deps and everything else depends on it, + "Package roles, per-module listings,
    and the dependency graph live in ARCHITECTURE.md's *Package Overview*." Keep the
    `## Architecture` heading and the `---` rules around it.
12. [ ] `CLAUDE.md:232-236` — delete the `### SQLAlchemy backend (varco_sa)` subsection
    entirely. Its two facts now live in ARCHITECTURE.md (step 10) and README's
    "SQLAlchemy Backend" section (`README.md:1518`) already covers usage. Do **not** leave
    an empty stub heading.
13. [ ] **Verify Phase 1** — fingerprints must be present in `ARCHITECTURE.md` and absent
    from `CLAUDE.md`:
    ```bash
    rg -c 'varco_ws +— WebSocket \+ Server-Sent Events' ARCHITECTURE.md   # ≥1
    rg -c 'tool.uv.sources.* sibling reference' ARCHITECTURE.md            # ≥1
    rg -c 'avoiding a parallel `SASettings` class' ARCHITECTURE.md         # ≥1
    rg -c 'SQLAlchemy backend \(varco_sa\)' CLAUDE.md                      # 0
    ```
    Then re-run step 6's guard.

### Phase 2 — Batch B: create the missing README sections (F4, F8, F9)

This is the highest-value batch: seven features currently have their only usage docs in
CLAUDE.md. Add each new section to `README.md`'s `## Table of Contents` (lines 55-152) at
the same time as the section body — a section added without a TOC entry counts as
incomplete.

14. [ ] `README.md` — new `## Profiling` section, placed after `## Observability`
    (line 2013-ish region, adjacent to the existing observability content). **Merge the
    two existing near-identical copies** (`CLAUDE.md:370-427` and
    `#### Scenario: Profile a slow operation` at `CLAUDE.md:1326-1371`) into ONE section
    containing: the off-by-default statement + `VARCO_PROFILING_ENABLED`, the `@profile`
    decorator form, the `async with profiled(...)` context-manager form, the FastAPI
    middleware/`create_varco_app(enable_profiling=True)` + `X-Profile-*` headers, and the
    custom-backend registration example (`register_cpu_backend` / `register_memory_backend`).
    Add TOC entry `- [Profiling](#profiling)`.
15. [ ] `README.md` — new `## Background Jobs` section (README's TOC has no entry today).
    Source: `CLAUDE.md:533-586`. Move the usage code: the `try_claim`/`renew`/
    `save(expected_epoch=)` snippet (`CLAUDE.md:543-548`) and the zoned-`enqueue(tz=...)`
    snippet (`CLAUDE.md:571-577`). Include `JobPoller(lease_aware=True)` and the
    `delete_where(..., limit=)` chunked-sweep one-liner. Design rationale (Plan 005 Phase 4
    numbering, ABC concrete-but-raising reasoning) does **not** move here — it stays in
    `technical_docs/features/job-scheduling-and-leases.md`. Add TOC entry.
16. [ ] `README.md` — new `## Database Auditing` section. Source: `CLAUDE.md:587-640`. Move
    the `AuditLogMixin` service-composition example and the `AuditWiring` `@PostConstruct`
    class example (`CLAUDE.md:596-613`) verbatim. Add a two-line note that idempotency is
    backend-specific and point at `technical_docs/features/database-auditing.md`. Add TOC entry.
17. [ ] `README.md` — new `## Dead Letter Queue` section. Source: `CLAUDE.md:184-231`. Move
    the `@listen(..., retry_policy=, dlq=)` handler snippet and the interface summary
    (`AbstractDeadLetterQueue` / `InMemoryDeadLetterQueue` / the backend impls). Keep the
    **contract** sentence — "`push()` must never raise" — in *both* places (README as
    documentation, CLAUDE.md as a rule; this is one of the few deliberate duplications, and
    it is a one-liner). Add TOC entry.
18. [ ] `README.md` — new `## Composite Deployment` section. Source:
    `#### Scenario: Combine multiple services into one all-in-one deployment`
    (`CLAUDE.md:1286-1325`). Move the `create_composite_app([ServiceMount(...)])` snippet
    and the "Key facts" bullets (lifespan does not descend into mounts; fail-fast startup;
    `aggregate_health`; `build_service(prefix, factory, env={...})` env isolation). Point at
    `technical_docs/features/composite-deployment.md` for the design. Add TOC entry.
19. [ ] `README.md` — extend the existing `## FastAPI Integration` section (line 2343) with a
    `### A2A — exposing a non-router subject` subsection. Source:
    `#### Scenario: Expose a non-router subject over A2A` (`CLAUDE.md:1492-1536`) — the
    `ReportSkillSource` example, the `SkillAdapter(None, source=...)` construction, and the
    "`adapter.router_class` is `None` for a non-router source — that is the contract" note.
    Add a nested TOC entry.
20. [ ] `README.md` — new `## Durability preset (one-line opt-in)` section. Source:
    `#### Scenario: Opt into durability in one line` (`CLAUDE.md:1628-1647`) — the
    `ReliabilityPreset.durable(dlq=dlq)` snippet + the bullet list of what it turns on.
    Point at `technical_docs/features/reliability-preset.md`. Add TOC entry.
21. [ ] `README.md` — fold the remaining `#### Scenario:` blocks into their **existing**
    README sections as worked examples (no new sections needed for these):
    - `Scenario: Add a new event type and handler` (1092-1143) → `## Event System`
      (line 1086), after "Consumer — EventConsumer + @listen".
    - `Scenario: Add caching to a service method` (1144-1185) → `## Cache System`
      (line 1833), after "CacheServiceMixin".
    - `Scenario: Add filtering to a list endpoint` (1186-1213) → `## Query System`
      (line 997), after "QueryParams".
    - `Scenario: Build a service-free / data-processing REST server` (1214-1250) →
      `## FastAPI Integration`; also cross-link `technical_docs/features/generic-router.md`.
    - `Scenario: Expose custom service methods on a typed CRUD router` (1251-1285) →
      `## FastAPI Integration`, next to "VarcoRouter and VarcoCRUDRouter"; also cross-link
      `technical_docs/features/custom-routes.md`.
    - `Scenario: Integrate a new external API (with resilience)` (1372-1431) →
      `## Resilience` (line 2013), after "Composing patterns".
    - `Scenario: Consume a foreign-shaped JWT` (1432-1460) and
      `Scenario: Gate a route on a named token profile` (1461-1491) →
      `## JWT / Authority System` (line 2150).
    - `Scenario: Turn on schema-per-tenant isolation and onboard a tenant` (1537-1574) →
      `## Multi-tenancy (DB-level)` (line 944) — this section currently stops at the static
      `TenantUoWProvider` and only gestures at `varco_core.tenancy` in two sentences
      (README:979-993), so append rather than replace.
    - `Scenario: Call another varco service` (1575-1603) → `## FastAPI Integration`'s
      existing "Calling other varco services — client_for" subsection.
    - `Scenario: Cross-repo service integration` (1604-1627) → same subsection, as a
      follow-on; point at `technical_docs/features/portable-contracts.md`.
22. [ ] `CLAUDE.md` — delete the entire `### Common Scenarios` block (1090-1647) including
    the `### Common Scenarios` heading itself. `### Before Adding a Feature` (1070-1088)
    stays; `## Planning & Development Workflows` (1068) keeps that one child. Add a single
    closing line under `### Before Adding a Feature`: *"Worked usage examples for every
    subsystem live in README.md — see its Table of Contents."*
23. [ ] `CLAUDE.md:370-427` — replace `### Profiling` with 2 sentences + pointer per the
    trimming template. Keep the four bolded **Rules** (process-global one-session-at-a-time,
    `cProfile`-across-`await`, tracemalloc state restored, never leave always-on) — these
    are agent-behavior content; everything else points at README's new Profiling section.
24. [ ] `CLAUDE.md:184-231` (DLQ), `533-586` (Background jobs), `587-640` (Database
    auditing) — trim to the template. Keep verbatim: DLQ's "`push()` must never raise"
    contract sentence; Background jobs' zoned-schedule `run_at`-is-materialized rule; audit's
    "`AuditLogMixin` composes to the LEFT of `AsyncService`" and "wire the consumer from
    `@PostConstruct`" rules. Everything else → the three existing tech docs + the new README
    sections.
25. [ ] **Verify Phase 2** — every new README section exists, is in the TOC, and carries its
    fingerprints; CLAUDE.md no longer carries the duplicates:
    ```bash
    # new sections present + TOC-linked
    for s in Profiling "Background Jobs" "Database Auditing" "Dead Letter Queue" \
             "Composite Deployment" "Durability preset"; do
      rg -q "^## $s" README.md && rg -qi "\[$s\]\(#" README.md || echo "MISSING: $s"
    done
    # fingerprints landed
    rg -c 'register_cpu_backend'            README.md   # ≥1  (F8)
    rg -c 'VARCO_PROFILER_ATTACH_HEADERS'   README.md   # ≥1  (F8)
    rg -c 'reap_expired_leases'             README.md   # ≥1  (F4 jobs)
    rg -c 'expected_epoch=claimed.lease_epoch' README.md # ≥1 (F4 jobs)
    rg -c 'class AuditWiring'               README.md   # ≥1  (F4 audit)
    rg -c 'create_composite_app'            README.md   # ≥1  (F9)
    rg -c 'ReportSkillSource'               README.md   # ≥1  (F9)
    rg -c 'ReliabilityPreset.durable'       README.md   # ≥1  (F9)
    # CLAUDE.md duplicates gone
    rg -c '#### Scenario:'                  CLAUDE.md   # 0
    rg -c 'register_cpu_backend'            CLAUDE.md   # 0  (F8 double-copy resolved)
    ```
    Then re-run step 6's guard.

### Phase 3 — Batch C: trim narratives to 2-3 sentences + pointer (F2, F3, F5, F6, F7)

Apply the trimming template to each row below. **Keep every bolded `**Rule**:` /
`**Contract**:` / `⚠️` sentence verbatim** — those are the only unique agent-behavior
content in these blocks and must survive the trim character-for-character.

26. [ ] `CLAUDE.md:87-117` `### Event system` — keep the "services must never hold or call
    `AbstractEventBus` directly" rule (with its three named exceptions: `OutboxRelay`,
    `EventConsumer.register_to()`, `DlqRedriver`) and the "`@listen` is declarative /
    `register_to` is imperative" rule. Delete the three-layer ASCII diagram (already in
    ARCHITECTURE.md `### Event System`, line 126) and the `OrderConsumer` snippet (already
    in README `### Consumer — EventConsumer + @listen`, line 1235). Point at both.
27. [ ] `CLAUDE.md:119-133` `### Service layer` — keep the mixin-composition `super()`
    chaining rule and "Authorization is enforced at the service layer (not HTTP)". Delete
    the five-type-parameter listing and `_get_repo` snippet → ARCHITECTURE.md
    `### Service Layer` (163) + README `## Service Layer` (429).
28. [ ] `CLAUDE.md:169-182` `### Resilience` — keep the shared-`CircuitBreaker`-per-external-
    dependency rule and the "`@retry` wrapper is built at `register_to()` time" note. Delete
    the decorator stack snippet → README `## Resilience` (2013) + ARCHITECTURE.md
    `### Resilience` (398).
29. [ ] `CLAUDE.md:428-482` `### Cache system` — keep the "never instantiate
    `InvalidationStrategy` outside its backend's `start()`/`stop()` lifecycle" rule. Delete
    the `AsyncCache`/`CacheBackend`/`LayeredCache` hierarchy diagram → ARCHITECTURE.md
    `### Cache System` (194); delete the stampede-protection and bulk-operations paragraphs
    (444-481) → `technical_docs/features/cache-hardening.md` (append to its
    "Bulk operations" / Decisions sections if any fact is missing there). Keep the
    per-process-only `Singleflight` caveat as a one-liner.
30. [ ] `CLAUDE.md:483-515` `### Query system` — keep the "all AST nodes are
    `@dataclass(frozen=True)`" rule and the "SQLAlchemy applicator lives in
    `varco_core.query.applicator.sqlalchemy`, not `varco_sa`" layer rule. Delete the
    pipeline diagram → ARCHITECTURE.md `### Query System` (267) + README `## Query System`
    (997). Move the whole `DatetimeCoercionPolicy` paragraph — including the ⚠️
    `ASTTypeCoercion`-has-no-`policy=` caveat — into
    `technical_docs/features/timezone-handling.md`'s T3 section, leaving a one-line pointer.
31. [ ] `CLAUDE.md:516-532` `### Transactional Outbox` — keep "Services must **not** publish
    events directly after a DB commit" and "`OutboxRelay` is the only place allowed to call
    `AbstractEventBus` directly (besides `EventConsumer.register_to()`)". Delete the
    numbered mechanism + `async with uow:` snippet → README `## Transactional Outbox`
    (1458) + ARCHITECTURE.md `### Outbox Pattern` (447).
32. [ ] `CLAUDE.md:267-296` `### Ambient request context` — keep **only** the two rules:
    "`RequestContext` never holds the tenant" (with `current_tenant()` as the single source
    of truth, composition by ordering not containment) and the module-scope-`ContextVar`-is-
    correct note (it is a live exception to the lazy-`asyncio.Lock` rule and an agent will
    otherwise "fix" it). Move the `AmbientVar`/`RequestContext`/`resolve_precedence` design
    narrative into a new `## Ambient context (`varco_core.context`)` section at the top of
    `technical_docs/features/i18n-and-localization.md` — the audit's chosen destination,
    since X1 exists only to serve I2/T1 and has no dedicated doc.
33. [ ] `CLAUDE.md:297-321` `### Internationalization` — trim to: off by default
    (`I18nSettings.enabled=False`), `MessageCatalog` ABC with three implementations, and the
    `localization_cache_key(...)` **fails-closed** rule (keep verbatim — same rule class as
    `tenancy_cache_key()`). Everything else → `technical_docs/features/i18n-and-localization.md`.
34. [ ] `CLAUDE.md:322-337` `### Timezones` — trim to: off by default, "**varco never changes
    what it stores** — everything is written aware-UTC; this is a rendering layer only"
    (keep verbatim), and the RFC 9557 output-only note. Everything else (five-source
    precedence chain, tzdata startup validation) → `technical_docs/features/timezone-handling.md`.
35. [ ] `CLAUDE.md:338-369` `### Error taxonomy` — trim to: "`code` is the machine
    identifier, `message_key` is the i18n key" and the `error_params()` **exfiltration-
    surface** warning (both verbatim — the latter is a security rule an agent must apply when
    overriding). Everything else (D-4 wire delta, `VarcoErrorCodes` alias reasoning, RFC 9457
    opt-in) → `technical_docs/features/error-taxonomy-and-i18n.md`.
36. [ ] `CLAUDE.md:238-266` `### Observability` — keep the bolded
    "**Rule — Resource attribute vs. global attribute registry**" verbatim and the ⚠️
    "`TracingServiceMixin`/`TracingRepositoryMixin` do NOT auto-capture `pk`/`dto`/`params`"
    caveat. Everything else → `technical_docs/features/observability-attributes.md`.
37. [ ] `CLAUDE.md:641-669` `### Field-level encryption & crypto-shredding` — keep the
    "Never embed personal data in a scope string" rule and the "`destroy` vs `retire`"
    one-liner. Everything else (the ⚠️ backfill requirement, capability-shim rule) →
    `technical_docs/features/crypto-shredding.md`.
38. [ ] `CLAUDE.md:670-707` `### A2A protocol surface` — keep the "`router_cls` and `source=`
    are mutually exclusive — `ValueError` otherwise" rule and the "`ctx` is the U-3 auth-
    passthrough contract" sentence. Everything else (v1.0.0 vs legacy path table, async-A2A
    provenance note) → `technical_docs/features/a2a-surface.md`. Usage already moved to
    README in step 19.
39. [ ] `CLAUDE.md:738-777` `#### Claim transformation + token profiles` — trim to two
    sentences + pointers at `technical_docs/features/jwt-claim-transformer.md` and
    `token-profiles.md`. Keep the "`JwtParser._from_raw_claims` is the single funnel" fact
    (it is the layer rule that makes the feature zero-code-change).
40. [ ] `CLAUDE.md:778-790` — move the **`VARCO_JWT_*` env-var reference table** verbatim
    into `README.md`'s `## JWT / Authority System` section (line 2150) as a new
    `### Verification hardening (VARCO_JWT_*)` subsection, with a TOC entry. In CLAUDE.md,
    keep only the two **BREAKING security defaults** as prose (audience required unless
    `allow_any_audience`; `iss` enforced by default) plus a pointer to the README table.
41. [ ] `CLAUDE.md:792-840` `### Authorization — policy engine` — keep the three bolded
    Rules verbatim: authorizer is opt-in via `enable_policy_authorizer()` and must NOT be a
    scanned `@Configuration`; `CasbinPolicyEngine` must be a shared singleton; `CasbinSettings`
    via `@Provider`, not `@Singleton`. Delete the two-layer narrative + ASCII bridge diagram
    → `technical_docs/features/casbin-authorization.md` (append any fact missing there) and
    ARCHITECTURE.md `### Authorization — policy engine` (379).
42. [ ] `ARCHITECTURE.md:119` — re-point. Current text ends "See *Authorization* in
    CLAUDE.md". Change to point at `technical_docs/features/casbin-authorization.md` (full
    design) and note that CLAUDE.md retains only the three wiring rules.
43. [ ] `CLAUDE.md:841-911` `### Schema migrations` — keep: "**Default is `off` — nothing
    runs**" with the three `mode` values one-liner, and the ⚠️ "`MigrationError` and
    `MigrationPlan` are NOT re-exported from `varco_core`" name-collision warning (verbatim
    — this is a real import trap). Delete the ASCII type diagram, the held-open-transaction
    mechanism, the ten-framework-table branch story, the `ensure_table()` reconciliation
    narrative, and the Mongo index-mode paragraph →
    `technical_docs/features/schema-migrations.md` (verify each is already there before
    deleting; append only what is missing).
44. [ ] `technical_docs/features/dead-letter-queues.md:407` — re-point. It currently says
    "per CLAUDE.md's [migration recipe]"; that recipe now lives in
    `technical_docs/features/schema-migrations.md`. Update to a relative link to that file.
45. [ ] `technical_docs/features/dead-letter-queues.md:258` — **assert only, do not edit.**
    It cites the layer rule, which stays in CLAUDE.md. Confirm the cited rule text still
    exists in CLAUDE.md after step 26.
46. [ ] `CLAUDE.md:912-1065` `### Multitenancy` — the single largest block (~154 lines).
    Trim to: three `TenantIsolation` values exist and are opt-in; "**Default is byte-identical
    to pre-Plan-007 behaviour**"; the `varco_fastapi.tenancy` imports-only-`varco_core.tenancy`
    seam rule; and the "`mount_tenant_admin()` is the **only** way to expose the admin
    surface — there is deliberately **no** `VARCO_TENANCY_MOUNT_ADMIN` env var, ever" rule
    (all four verbatim). Everything else (RD-4/RD-7/RD-9…RD-18 reasoning, the command/fact
    DAG rule, readiness-coordinator semantics, `schema_translate_map`-vs-`search_path`,
    fan-out supervisor narrative, new env vars) → `technical_docs/features/multitenancy.md`;
    verify each fact is already present there before deleting, append only what is missing.
    The `mount_tenant_admin()` usage snippet (1049-1058) → README `## Multi-tenancy (DB-level)`
    (appended in step 21).
47. [ ] **Verify Phase 3** — every kept rule survived and every deleted narrative landed:
    ```bash
    # kept rules — must still be in CLAUDE.md
    rg -c 'services must never hold or call'                    CLAUDE.md  # ≥1
    rg -c 'RequestContext` never holds the tenant'              CLAUDE.md  # ≥1
    rg -c 'never a scanned `@Configuration`|not a scanned `@Configuration`' CLAUDE.md # ≥1
    rg -c 'MigrationError` and `MigrationPlan` are NOT re-exported' CLAUDE.md # ≥1
    rg -c 'no `VARCO_TENANCY_MOUNT_ADMIN` env var'              CLAUDE.md  # ≥1
    rg -c 'varco never changes what it stores'                  CLAUDE.md  # ≥1
    # moved narratives — must now be in the feature docs
    rg -c 'AmbientVar'          technical_docs/features/i18n-and-localization.md   # ≥1
    rg -c 'ASTTypeCoercion'     technical_docs/features/timezone-handling.md       # ≥1
    rg -c 'idle_in_transaction_session_timeout' technical_docs/features/schema-migrations.md # ≥1
    rg -c 'schema_translate_map' technical_docs/features/multitenancy.md           # ≥1
    # env table moved
    rg -c 'VARCO_JWT_ALLOW_ANY_AUDIENCE' README.md  # ≥1
    # re-pointed
    rg -n 'CLAUDE\.md' ARCHITECTURE.md                                  # 0 hits, or none at :119
    rg -n 'CLAUDE\.md' technical_docs/features/dead-letter-queues.md    # only the :258 layer-rule cite
    ```
    Then re-run step 6's guard.

### Phase 4 — Batch D: pitfall-table triage (F10)

`CLAUDE.md:1720-1852`, ~100+ rows. **Every row must be accounted for**: kept, or moved with
a named destination. Work the table top-to-bottom; do not batch-delete.

48. [ ] `CLAUDE.md` — **KEEP** these rows verbatim in `## Common Pitfalls & How to Avoid
    Them`. They are pure code-pattern traps an agent must not reintroduce while writing new
    code (18 rows), plus the DI-wiring cluster owned by `plans/013`/`plans/014` (7 rows,
    **do not re-triage**):

    *Code-pattern traps (keep):* Direct bus access in service · Events published after commit ·
    Subscription in `__init__` · Forgot `@PostConstruct` on consumer · Per-call
    `CircuitBreaker` · Per-call `Bulkhead` · `InMemoryRateLimiter` in multi-pod ·
    In-process `Bulkhead` in multi-pod · Hedging non-idempotent writes · Mixin hook doesn't
    chain · Instantiate `InvalidationStrategy` outside lifecycle · Cache key collision ·
    Per-call `Singleflight` · Coalescing on a pre-tenant-namespaced key · Per-call
    `RedisPubSubBackplane` · Adding a bulk method directly to `AsyncCache` · Async lock at
    module level · Missing `await` on async call.

    *DI cluster (keep, owned elsewhere):* Quoted `@Provider` return annotation · Quoted
    `TypeAlias` used in an injected annotation · Protocol impl not resolvable by DI · A
    package's suite is green but its container won't bootstrap · `container.provide(lambda:
    X())` · Override registered after `install()`/`scan()` · `@Singleton` on pydantic
    `BaseSettings` · Forgot `<pkg>.bootstrap(container)` · `varco_memcached.async_bootstrap()`
    opens a pool you didn't want.

49. [ ] Move the remaining rows into the feature doc named below, appending to that file's
    existing `## Pitfalls` section — or **creating one** where the file has none (verified at
    plan time: only `database-auditing.md`, `dead-letter-queues.md`, `reliability-preset.md`,
    `distributed-locks.md`, `job-scheduling-and-leases.md`, `multitenancy.md` have a
    `## Pitfalls` heading today; every other destination below needs one added). Preserve
    the four-column `| Pitfall | Symptom | Root Cause | Fix |` table format so the rows
    transplant unchanged.

    | Destination file | Rows to move |
    |---|---|
    | `casbin-authorization.md` *(add `## Pitfalls`)* | Per-call `CasbinPolicyEngine` · Policy authorizer silently active · `memory` adapter in production · Sync Casbin adapter with `AsyncEnforcer` |
    | `jwt-claim-transformer.md` *(add)* | Roles empty although the JWT has them |
    | `token-profiles.md` *(add)* | `is_system()` false for my internal token |
    | `README.md` → new "Verification hardening" subsection (step 40) | Token from another service accepted · Forged/misrouted `iss` claim accepted · Intermittent 401 across hosts |
    | `observability-attributes.md` *(add)* | Secret in a span attribute · Metric series explosion after adding a global attribute · Global attribute never appears · Provider called on every measurement · `isinstance(create_counter(...), Counter)` is False |
    | `database-auditing.md` | Audit entries never written · `relation "varco_audit_log" does not exist` · `CollectionWasNotInitialized` on audit save · Audit record lost on broker outage |
    | `crypto-shredding.md` *(add)* | Destroyed key renders as corrupt data · Per-subject registry built with `build_tenant_registry` |
    | `dead-letter-queues.md` | Poison outbox row silently stops a stream · `OutboxRelay(max_attempts=…)` without a `dlq` · `list_entries(tenant_id=…)` misses a framework-level dead letter · `redrive(entry_id)` called on Kafka/NATS · `mount_reliability_admin()` without `acknowledge_bundled_admin=True` · `mount_reliability_admin()` called twice |
    | `job-scheduling-and-leases.md` | Long job killed at 5 minutes · Stalled worker resumes and overwrites a completed result · External `AbstractJobStore` subclass breaks on `lease_ttl` · `JobPoller` reaps a legitimately-running unleased job · Retention sweep starves the pool · Raw JWT readable in the jobs table · `enqueue(tz=…)` raises `ValueError` naming the store class |
    | `distributed-locks.md` | `release()` returns false and the lock leaks |
    | `postgres-rls.md` *(add)* | Hand-written RLS policy uses bare `current_setting(...)` · RLS tenant GUC set with `SET` instead of `SET LOCAL` · `TenantAwareService._scoped_params` bypassed · `enable_rls_ddl()` on a `VARCHAR`/`TEXT` tenant column · RLS test/connection uses a superuser role · RLS enabled by a startup hook |
    | `schema-migrations.md` *(add)* | `mode="upgrade"` in a large multi-pod deployment · `ensure_table()` and migrations both active · `upgrade head` (singular) with the framework branch present · `index_mode="create"` on a large Mongo collection · `VARCO_MIGRATE_MODE` set but no `migrations=` passed · `from varco_core import MigrationError` gets the wrong class · `varco migrate upgrade` without `--all-tenants` · Global migration run after the tenant fan-out |
    | `multitenancy.md` | Raw `text()` SQL under `TenantIsolation.SCHEMA` · `SET` instead of `SET LOCAL` for schema routing · Per-tenant engine/binding never `dispose()`d · Unbounded per-tenant pool · `init_beanie()` rebinds every tenant · `BeanieDocRegistry.get(User)` expected to return a clone · `TenantIsolation.DATABASE` without `fanout_framework_tables` · `TenantIsolation.SCHEMA` on `varco_beanie` · `TENANT`-scoped cache key outside `tenant_context()` · `GLOBAL`-scoped cache key namespaced by tenant · `TenantAwareService` mixed into a `GLOBAL`-entity service · Expecting one transaction across tenant + global DB · Global write raises `GlobalScopeReadOnlyError` · Literal DSN stored in `varco_tenants` · Admin DSN present in an app pod · `mount_tenant_admin()` without `acknowledge_bundled_admin=True` · Bundled admin router left ungated at the ingress · Redelivered `TenantProvisionRequested` assumed unique · Bus-onboarded tenant 404s · Consumer constructed with `provisioner=` · Bundled node called only `request_provision()` · Missing store in `expected_stores` · Counting pods instead of stores · Expecting readiness to survive a restart |
    | `cache-hardening.md` *(add)* | `LayeredCache` in multi-pod without a backplane · `LayeredCache(backplane=…, promote_ttl=None)` · Backplane key names visible fleet-wide · `soft_ttl >= ttl` · Enabling envelope mode mid-rolling-deploy · Negative caching hiding a fixed row · Cache metrics never appear |
    | `i18n-and-localization.md` *(add)* | `?lang=xx` silently ignored · `Content-Language` header missing · Localized response cached and served to the wrong locale · `tenant_id` expected in `RequestContext` |
    | `error-taxonomy-and-i18n.md` *(add)* | Error body gained `message_key`/`params` after upgrade · Error response not localized although i18n is enabled |
    | `timezone-handling.md` *(add)* | tzdata absent in a slim image · `assume="utc"` breaks a working datetime filter · `DatetimeCoercionPolicy(assume="utc")` has no effect on a `?field__gte=` filter |
    | `portable-contracts.md` *(add)* | `client_for()`'s custom `@route` method assumed typed/strict |
    | `reliability-preset.md` | `ReliabilityPreset(outbox_max_attempts=…)` without `dlq` · Per-call breaker for a peer service |
    | `composite-deployment.md` *(add)* | Naive `app.mount()` in a composite · Two composite services share a bare env name |
    | `route-guard.md` *(add)* | `requires=` without `_auth` · `ctx` declared but no `_auth` |
    | `custom-routes.md` *(add)* | Custom service method unknown on `self._service` |
    | `README.md` → `## Profiling` (step 14), as a "Caveats" list | Profiling left always-on · Two profiling sessions concurrent · `cProfile` across `await` on a busy loop · tracemalloc state not restored |

50. [ ] `CLAUDE.md` — after the trimmed table, add one closing line: *"Feature-specific
    operational pitfalls (wrong env var → wrong runtime behaviour) live in each feature's own
    `technical_docs/features/*.md` **Pitfalls** section, not here."*
51. [ ] `technical_docs/features/observability-attributes.md:449` — re-point. Replace
    "(see the main CLAUDE.md pitfall table)" with a reference to this file's own new
    `## Pitfalls` section (added in step 49).
52. [ ] `technical_docs/features/multitenancy.md:891` — re-point. Replace "See the
    `CLAUDE.md` \"Multitenancy\" section's Common Pitfalls rows for…" with a reference to
    this file's own `## Pitfalls` section, which now owns those rows.
53. [ ] `technical_docs/features/schema-migrations.md:190` — re-point. It cites "the U-16
    defect already in CLAUDE.md's [pitfall table]"; that row (`release()` returns false and
    the lock leaks) moved to `distributed-locks.md`. Update to a relative link there.
54. [ ] `technical_docs/features/opa-design.md:68` and
    `technical_docs/features/cache-hardening.md:360` and
    `technical_docs/features/distributed-locks.md:102` — **assert only, do not edit.** They
    cite the resilience shared-instance rules, the Pre-Implementation Checklist, and the DI
    binding-priority rule respectively — all kept in CLAUDE.md. Confirm each cited target
    still exists.
55. [ ] `.claude/agents/feature-doc-writer.md` (≈line 85) and
    `.claude/agents/feature-implementer.md` (≈line 160) — both say "Mirror the CLAUDE.md
    pitfalls table format where relevant." Append one clause: *"…and put feature-specific
    pitfalls in the feature's own `technical_docs/features/*.md` `## Pitfalls` section, not
    in CLAUDE.md."* Do not touch the other `.claude/agents/*.md` references — they name the
    Decision Tree / Pre-Implementation Checklist / Coding Standards, all of which stay.
56. [ ] **Verify Phase 4** — row accounting must balance:
    ```bash
    # every moved row's fingerprint now exists in exactly its named destination
    rg -c 'Per-call `CasbinPolicyEngine`'      technical_docs/features/casbin-authorization.md  # ≥1
    rg -c 'Metric series explosion'            technical_docs/features/observability-attributes.md # ≥1
    rg -c 'Counting pods instead of stores'    technical_docs/features/multitenancy.md          # ≥1
    rg -c 'tzdata absent in a slim image'      technical_docs/features/timezone-handling.md     # ≥1
    rg -c 'soft_ttl >= ttl'                    technical_docs/features/cache-hardening.md       # ≥1
    rg -c 'upgrade head' technical_docs/features/schema-migrations.md                           # ≥1
    # and is gone from CLAUDE.md
    for f in 'Per-call `CasbinPolicyEngine`' 'Metric series explosion' \
             'Counting pods instead of stores' 'tzdata absent' 'soft_ttl >= ttl'; do
      rg -q "$f" CLAUDE.md && echo "STILL IN CLAUDE.md: $f"; done
    # kept rows survived
    rg -c 'Per-call CircuitBreaker|Per-call `CircuitBreaker`' CLAUDE.md  # ≥1
    rg -c 'Async lock at module level'          CLAUDE.md  # ≥1
    rg -c 'Quoted `@Provider` return annotation' CLAUDE.md # ≥1
    # row count: kept ≈ 27 (18 code-pattern + 9 DI)
    sed -n '/^## Common Pitfalls/,/^## Decision Tree/p' CLAUDE.md | rg -c '^\| \*\*'
    # every feature doc that received rows now has a Pitfalls heading
    rg -L '^## Pitfalls' technical_docs/features/*.md
    ```
    Then re-run step 6's guard.

### Phase 5 — Final verification pass

57. [ ] Re-read the trimmed `CLAUDE.md` end-to-end against audit 002's F1–F11 destinations
    and its **Not-findings** list. Assert present and unmodified: `## Commands`,
    `### DI wiring verb taxonomy`, `### Before Adding a Feature`, `## Coding Standards`,
    `## Test Conventions` (F12 — untouched), `## Decision Tree`,
    `## Pre-Implementation Checklist`.
    ```bash
    diff <(sed -n '/^## Test Conventions/,/^## Common Pitfalls/p' /tmp/varco-doc-refactor/before/CLAUDE.md) \
         <(sed -n '/^## Test Conventions/,/^## Common Pitfalls/p' CLAUDE.md)
    # must be empty — F12 is out of scope
    diff <(sed -n '/^### DI wiring verb taxonomy/,/^### Resilience/p' /tmp/varco-doc-refactor/before/CLAUDE.md) \
         <(sed -n '/^### DI wiring verb taxonomy/,/^### Resilience/p' CLAUDE.md)
    # must be empty — plans/013 explicitly kept this table
    ```
58. [ ] `## Decision Tree` — update only the file-path pointers inside it if any subsystem's
    owning doc changed (e.g. a branch that said "see the Profiling section above" must now
    say README's Profiling section). The tree's *structure* and *decisions* do not change.
59. [ ] Update the `## Decision Tree`'s sibling pointer at `CLAUDE.md:5` ("Quick reference:
    See ARCHITECTURE.md…") to a three-way pointer: ARCHITECTURE.md for types/packages,
    README.md for usage, `technical_docs/features/` for per-feature design + pitfalls.
60. [ ] Repo-wide dangling-reference sweep — no doc may point at a CLAUDE.md section that no
    longer exists:
    ```bash
    rg -n 'CLAUDE\.md' --glob '!.claude/worktrees/**' --glob '!plans/0*.md' \
       --glob '!audits/**' --glob '!.claude/agent-memory/**'
    ```
    Diff against `/tmp/varco-doc-refactor/before/xrefs.txt`. Every remaining hit must cite a
    rule/section that survives (layer rule, shared-instance rules, lazy lock, DI rules,
    Decision Tree, Pre-Implementation Checklist, Coding Standards, timing-margin convention).
61. [ ] Out-of-scope guard, final:
    ```bash
    git status --porcelain
    ```
    The changed set must be exactly: `CLAUDE.md`, `ARCHITECTURE.md`, `README.md`,
    `technical_docs/features/*.md`, `.claude/agents/feature-doc-writer.md`,
    `.claude/agents/feature-implementer.md`, `plans/015-refactor-claude-md-doc-structure.md`
    — plus whatever was already modified before this plan started (see the pre-existing
    `git status` set; do not revert those). **Nothing** under `plans/00*`–`plans/014*`,
    `audits/`, `.claude/worktrees/`, or `.claude/agent-memory/`.
62. [ ] Sanity: the refactor is docs-only, so the suite must be unaffected.
    `make lint && make type-check && uv run pytest varco_core/tests/ -q`
63. [ ] `CHANGELOG.md` — one entry under the unreleased section: documentation
    restructure, CLAUDE.md reduced to agent guidance, new README sections (Profiling,
    Background Jobs, Database Auditing, Dead Letter Queue, Composite Deployment, Durability
    preset), per-feature Pitfalls sections added to `technical_docs/features/*.md`.
64. [ ] `BACKLOG.md` — add one line recording that audit 002's **F12** was deliberately
    deferred (Test Conventions RT1/RT6 prose density), so it is not silently lost.

---

## Edge cases

- **A "moved" fact turns out not to exist in the destination doc.** → Do not delete it from
  CLAUDE.md. Append it to the destination first, verify by grep, then delete. Every Phase 3
  step says "verify each fact is already present there before deleting, append only what is
  missing" for exactly this reason.
- **A destination `technical_docs/features/*.md` has no `## Pitfalls` heading.** → Create
  one at the end of the file, above any "See also"/references block, using the same
  four-column table header. Verified list of files that already have one:
  `database-auditing`, `dead-letter-queues`, `reliability-preset`, `distributed-locks`,
  `job-scheduling-and-leases`, `multitenancy`. Everything else needs one.
- **A pitfall row is genuinely both** (a code-pattern trap *and* feature-specific) — e.g.
  "Coalescing on a pre-tenant-namespaced key". → Keep the row in CLAUDE.md (the code-pattern
  reading wins, because that is the reading an agent needs while typing) and add a
  cross-reference line, not a duplicate row, in the feature doc.
- **CLAUDE.md line numbers have drifted** from this plan's ranges (the file is
  uncommitted-modified at plan time). → Phase 0 step 3 catches this; re-derive ranges from
  `rg -n '^#{2,4} '` and update this plan before editing. Never edit by line number without
  re-confirming the heading at that line.
- **README section-anchor collision** — README already has two `### exists() and stream()`
  headings, so its TOC uses `#exists-and-stream-1`. Any new heading that collides with an
  existing one must get a disambiguating title, not a duplicate anchor.
- **A `#### Scenario:` block has no obvious README home.** → It is one of the six that get a
  *new* README section (steps 14-20). If a seventh appears, add a new section rather than
  cramming it into an unrelated one.
- **Content in CLAUDE.md that is newer than the tech doc** (CLAUDE.md was updated for a
  recent plan but the feature doc was not). → The CLAUDE.md text is authoritative; carry it
  into the feature doc verbatim and note the discrepancy in the commit message.
- **`.claude/worktrees/` copies drift after this lands.** → Out of scope by design; they
  follow whatever the primary files do via their own sync process.

## Verification

Run after **every** phase, not just at the end:

```bash
cd /home/edoardo/projects/varco

# 1. Nothing out of scope was touched
git status --porcelain | rg '^(.{2}) (audits/|plans/0(0|1[0-4])|\.claude/worktrees/|\.claude/agent-memory/)' \
  && echo "FAIL: out-of-scope file modified"

# 2. Content preservation — every fingerprint in its new home (see per-phase step lists)
#    Phase 1: steps 13   Phase 2: step 25   Phase 3: step 47   Phase 4: step 56

# 3. Cross-references resolve
rg -n 'CLAUDE\.md' --glob '!.claude/worktrees/**' --glob '!plans/0*.md' \
   --glob '!audits/**' --glob '!.claude/agent-memory/**'

# 4. Size check — the audit's headline claim
wc -l CLAUDE.md ARCHITECTURE.md README.md
#    CLAUDE.md: 2018 → expected ~700-900 after all four batches

# 5. Untouched sections are byte-identical (Phase 5 step 57)

# 6. Docs-only change cannot break the suite
make lint && make type-check
uv run pytest varco_core/tests/ -q
```

Commit boundary: one commit per phase, message prefixed `docs(claude-md):` and naming the
finding IDs it closes (e.g. `docs(claude-md): batch A — merge package map + SA stub into
ARCHITECTURE.md (F1, F11)`).

## Risks

- **Silent content loss during a large delete.** Mitigated by the fingerprint-grep
  verification after every phase and by the Phase 0 full-file snapshot in
  `/tmp/varco-doc-refactor/before/` (recoverable with `diff` at any point). *Invariant: no
  block leaves CLAUDE.md until its fingerprint greps green in the destination.*
- **CLAUDE.md is currently uncommitted-modified**, so this plan's line numbers may be stale.
  Mitigated by Phase 0 step 3's mandatory heading re-derivation. *Invariant: never edit by
  line number without confirming the heading text at that line.*
- **Agent behaviour regression** — a rule that an agent relied on ambiently disappears from
  the auto-loaded file. Mitigated by the "keep every bolded `**Rule**:` verbatim" constraint
  in Phase 3 and the explicit KEEP list in Phase 4 step 48. *Invariant: every bolded
  imperative sentence in the pre-edit CLAUDE.md is still in the post-edit CLAUDE.md, or was
  moved with a pointer left behind.*
- **Batch D's row triage is judgment-heavy** (~100 rows). Mitigated by the exhaustive
  destination table in step 49 — every row is named, so "unaccounted for" is detectable by
  the row-count check in step 56.
- **Merge conflicts with concurrent work** — `CLAUDE.md`, `varco_fastapi/varco_fastapi/app.py`
  and several tech docs are already modified in the working tree, and `plans/014` (two files,
  both numbered 014) is in flight. Mitigated by phase-sized commits and by not re-triaging
  the DI-wiring pitfall rows those plans own. *Invariant: this plan never edits a pitfall row
  introduced by `plans/013` or `plans/014`.*
- **README grows past readability** (already ~2500 lines; this adds six sections). Accepted:
  README is explicitly the "full API reference" and its TOC makes it navigable. If it becomes
  unmanageable, splitting README is a separate, later plan — not scope creep here.
