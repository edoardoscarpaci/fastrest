# BACKLOG

Feature backlog produced by `/discover` (focus: **providify 2.0.0 upgrade + first official
public release of the varco packages**).

**Stated priority** (user, this session): move onto providify 2.0.0 and confirm compatibility,
then close the gap between "feature-rich alpha" and "first official release" — mirroring the
release engineering providify itself just shipped (GitHub Actions, trusted publishing, repo
settings, governance files).

## Locked decisions (this session)

| Decision | Choice | Consequence |
|---|---|---|
| **Release scope** | Release engineering **+ reliability floor** | Standards-alignment work (CloudEvents/AsyncAPI/OpenFeature) parked to 3.1 |
| **Versioning** | **Lockstep at 3.0.0** across all ten packages | PyPI forbids version reuse and `varco_sa` is already at 2.2.0, so 1.0.0 is unavailable. Mirrors providify's own 1.1.1 → 2.0.0 jump: a major bump that buys escape from the Alpha classifier, **not** breakage |
| **Breaking-change appetite** | **Deliberately spend the window** | 3.0.0 is the last cheap moment before the SemVer contract binds — an API-surface audit (RL-8) is in scope and must precede the version freeze |
| **RT reliability push** | **Gates 3.0.0** | See the status correction below — far less remains than originally reported |
| **Docs hosting** | **GitHub Pages + `mike`**, versioned, published from CI | No third-party service; a docs job joins the release workflow |
| **Ordering** | **Dependency-ordered phases**, overriding the default (severity, complexity) sort | Each phase is independently shippable and unblocks the next |

⚠️ **Reversal of a prior decision.** A previous `/discover` session parked GitHub Actions
entirely (see Parked: "user chose local-only tooling over any GitHub Actions involvement, even
non-blocking"). This session reverses that deliberately — a public release needs CI. The parked
entries are kept below, marked as superseded, so the reversal is visible rather than silent.

⚠️ **Status correction.** The scan backing this session's proposals reported all nine RT items
as pending. Verification against source shows otherwise: **RT1 and RT6 are complete**, and
RT2/RT4/RT5 have materially advanced. The RT table below carries verified statuses. The "all
nine gate 3.0.0" decision was taken on the stale figure — the remaining work is smaller than
that decision assumed.

**Research briefs backing this backlog:**

- `design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md` — release table stakes
  for multi-package Python OSS frameworks in 2026 (SemVer + deprecation cycles, monorepo
  versioning strategy, trusted publishing + PEP 740 attestations, docs hosting, SECURITY.md);
  varco's commodity-vs-differentiator feature matrix; 2025–2026 ecosystem shifts.
- `design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md` —
  testcontainers-python vs GitHub Actions `services:`; conformance/contract testing precedent.
- `design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md` —
  chaos/fault-injection precedent (Temporal, Confluent, Celery).

---

## Phase 1 — providify 2.0.0

Unblocks everything else: the compat shim deletion and the gap-register reconcile both change
what later phases have to carry. providify 2.0.0's changelog states **no breaking API changes**
(the 1.x → 2.x jump is purely to escape the Alpha classifier), so this phase is expected to be
mechanical — but the 1.1.0-era behaviour changes it inherits are not.

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RL-1 | **✅ DONE (Plan 016)** — **Upgrade to providify 2.0.0 and un-vendor it** — drop `vendor/providify-1.1.0-py3-none-any.whl` from the root `[tool.uv.sources]`, bump all nine `providify>=1.1.0` constraints to `>=2.0.0`, resolve from PyPI, full test sweep | 🔴 must | S | The user's explicit opening ask. Un-vendoring also removes the documented "must re-build the wheel when providify changes" maintenance burden in the root `pyproject.toml`. Vendored path override is incompatible with publishing anyway — consumers can't resolve a local wheel | providify `CHANGELOG.md` §2.0.0 ("this release contains no breaking API changes"); root `pyproject.toml:25-39`; commits `7cea36b`/`1008655`; `vendor/providify-1.1.0-py3-none-any.whl` deleted (Plan 016 Phase F, Step 43) |
| RL-2 | **✅ DONE (Plan 016)** — **Delete `varco_core.providify_compat`, adopt `@Provider(returns=...)`** — migrate the `bind_*` factory sites off the annotation-patching shim onto the native override | 🔴 must | S | providify 2.0.0 ships **exactly** the API requested in UPSTREAM-GAPS U-20. CLAUDE.md already describes the shim as "a deletable compat shim, not a DI entry point" — this is the deletion it was written for. Shipping a public 1st release still carrying a shim for a closed upstream gap is indefensible | providify `CHANGELOG.md` §"`returns=` — explicit interface override for `@Provider` and `provide()`": *"removes the only reason a caller ever had to mutate `factory.__annotations__["return"]`"*; `UPSTREAM-GAPS.md` U-20 (CLOSED); commit `c7e1c11` |
| RL-3 | **✅ DONE (Plan 016)** — **Adopt `container.validate()`; audit `ShutdownError` + priority direction; evaluate providify's pytest plugin** | 🔴 must | M | Four distinct 2.0.0 deltas land on varco: (a) `validate()` walks the whole graph without instantiating — strictly stronger than varco's hand-rolled per-package `validate_bindings()` health tests; (b) `shutdown()` now raises an **aggregated** `ShutdownError` instead of the first raw exception — varco_fastapi's lifespan teardown must handle it; (c) the "higher priority value wins" doc correction needs an audit for code written against the old wrong wording; (d) providify now registers a `pytest11` plugin (`di_container`/`di_overrides`/`di_global`) + `ContainerOverrides` — adopt, and check for fixture-name collisions in varco's suites | providify `CHANGELOG.md` §2.0.0 — "startup-time full graph validation", "`shutdown()`/`ashutdown()` now aggregate ALL teardown failures", "Priority direction — documentation corrected", "Pytest integration"; commits `bff1492` (RL-3a), `deeecb7` (RL-3b), `356868e` (RL-3c), `4815301` (RL-3d) |
| RL-4 | **✅ DONE (Plan 016)** — **Reconcile `UPSTREAM-GAPS.md` against source** — verify every open entry, close what providify 2.0.0 or varco's own later work already fixed, re-file what remains | 🔴 must | S | 20 entries, last touched 2026-08-23. Several P0/P1 "blockers" (U-11 job lease fencing, U-17 job `run_at`, U-13 JWT `iss` enforcement) read as **already implemented** per CLAUDE.md — the register looks stale, and a public release must not ship with a blocker list nobody trusts. Front-loaded because the answer resizes phases 3 and 4. The register's own U-8 lesson mandates verifying in source, not from docs | `UPSTREAM-GAPS.md` (20 entries) vs `CLAUDE.md` §"Background jobs — time, lease, fencing" and §"Two BREAKING security defaults"; **outcome:** U-1, U-2, U-3, U-11, U-13, U-17 closed (genuinely implemented, not merely documented); U-12 confirmed still open against providify 2.0.0's own source (`validate()` covers wiring resolvability only, never interface conformance); U-14/U-15 absences reconfirmed unchanged; commit `f1d5b27` |

---

## Phase 2 — CI green

✅ **DONE (Plan 017).** Nothing downstream is trustworthy until the suite runs automatically —
both workflows are now live (see RL-5/RL-6 below); the new findings and deferred work the
implementation surfaced are recorded in the "Plan 017 findings" subsection immediately after
this table.

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RL-5 | ✅ **DONE (Plan 017)** — **Re-enable and rebuild the GitHub Actions workflows** — `test.yml` (Python 3.12/3.13 matrix, ruff gate, mypy gate, unit-test matrix with per-package accumulation instead of a per-package job fan-out), `integration.yml` (Docker-backed, testcontainers) | 🔴 must | M | Zero automated gating before this plan. providify's working `ci.yml` supplied the matrix/pinning/aggregate shape (adopted, not the per-package job count — see plan 017 §RL-5-shape). **Supersedes the prior session's park** (see the reversal note above). ⚠️ **Evidence correction (U-8 discipline):** this row's own original Evidence cell — "`test.yml` is 81/92 lines commented, `integration.yml` 169/200, `publish.yml` 134/154" — did not survive contact with the tree: all three were **100% commented, zero live lines** (totals 92/200/154), not partially live. `publish.yml` is untouched (RL-10). Branch protection (requiring only the `all-green` check) is a repo-settings change **not yet applied** — see CLAUDE.md's CI subsection | `.github/workflows/test.yml`/`integration.yml` (live); `git diff cae7f33..HEAD -- .github/workflows/`; commits 8dba7d3/f03e613/8350f92 |
| RL-6 | ✅ **DONE (Plan 017)** — **Workspace-level lint/type config** — a root `[tool.ruff]` and `[tool.mypy]` with declared strictness, so the CI gates in RL-5 have something to enforce | 🟡 should | S | Packages carry `[tool.pytest.ini_options]` but there was no workspace ruff/mypy config before this plan — both ran unpinned/on defaults if run at all. **Measured, not estimated:** whole-repo ruff under the adopted config (providify's `E,F,I,UP` select, `E501`/`UP046`/`UP047` ignored) found **1799 errors, 1723 auto-fixable** (source-only: 505 errors, 489 auto-fixable = 96.8%, above the plan's 95% threshold — no `per-file-ignores` needed); this plan's own pre-measured 987 figure did not hold and is corrected here per the same discipline. mypy's real venv baseline (not the pre-measured `--no-site-packages` figure of 117) was **219 errors** — split varco_fastapi 85 / varco_core 51 / varco_sa 35 / varco_beanie 33 / varco_redis 6 / varco_casbin 4 / varco_nats 3 / varco_kafka 2 / varco_memcached 0 / varco_ws 0 — closed with granular `# type: ignore[<code>]` suppressions per the plan's design, under the 250 re-litigate threshold. ⚠️ **Evidence correction (U-8 discipline):** this row's own original Evidence cell said "`py.typed` present in all nine packages" — there are **ten** distributed packages (plus `examples` = 11 workspace members), and `py.typed` was present in only **nine** of them; `varco_nats/pyproject.toml` declared `"Typing :: Typed"` while shipping no marker file. Fixed and wheel-verified. Python 3.13 measured **green** across all ten member suites + the example suite (Decision-table row 1, matrix `[3.12, 3.13]`, no skip markers) | `pyproject.toml` root `[tool.ruff]`/`[tool.mypy]`; `varco_nats/varco_nats/py.typed` (new); `.pre-commit-config.yaml` ruff rev bump; commits 8dba7d3/f03e613/8350f92 |

### Plan 017 findings — new rows filed during CI-green implementation

⚠️ **RL-20 — `examples/00-full-stack-post-api` unit suite is RED (8 failed, 3 passed)**, 🔴 must,
S. Pre-existing and unrelated to plan 017 (reproduced at `cae7f33`, before any plan-017 commit);
root cause is the example's `InMemoryUoW` test double lacking the `.posts` attribute
`service.py:181` expects. **This is load-bearing, stated without softening:**
`scripts/unit_tests.sh` (the new `unit` CI job's entry point) includes this suite as an
`EXTRA_SUITES` entry, so **the `unit` job is RED on its very first CI run** until this is fixed
— the "gates land green" claim for RL-5/RL-6 covers ruff/mypy/the ten packages' own suites only,
not this example. Fix the test double before relying on `all-green` as a merge gate. Evidence:
`examples/00-full-stack-post-api/example/tests/`; `scripts/unit_tests.sh`'s `EXTRA_SUITES` array.

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RL-14 | **Deferred mypy strictness ramp** (§RL-6-mypy, not enabled) — `disallow_untyped_defs` (High cost, "often 500+ locations", largest single jump), `check_untyped_defs` (medium-high, "exposes type mismatches inside functions that were previously unchecked" — would invalidate the measured 219-error baseline entirely), `disallow_any_generics` (medium, bare `list`/`dict`/`tuple`, cheap-ish but unmeasured), `no_implicit_reexport` (medium, needs `__all__` everywhere — research 003 §3 says this is the flag that matters *most* for a `py.typed` library, and the one whose blast radius across ten `__init__.py` files is least predictable; whether `__all__` is already defined is unverified), `warn_return_any` (medium, real unsoundness catcher, unmeasured); `disallow_untyped_calls`/`disallow_any_unimported`/`disallow_any_expr` recommended skipped outright ("saves ~20–30% effort", research 003 §5) | 🟡 should | M | Transcribed verbatim from plan 017's Design §RL-6-mypy per-flag table so the ramp survives the plan file. `rg -c 'type: ignore' varco_*/varco_*` (currently 219) is the progress metric — it should trend to 0 as each flag is enabled and its fallout fixed | `plans/017-ci-green-workflows-and-lint-type-gates.md` §RL-6-mypy; `pyproject.toml` root `[tool.mypy]` |
| RL-15 | **`# noqa` filed during the ruff sweep** — 9× `UP042` (deferred `StrEnum` migration: `ErrorPolicy`, `DispatchMode`, `HealthStatus`, `PKStrategy`, `CircuitState`, `KafkaDeliverySemantics`, `NatsDeliverySemantics`, `BackpressurePolicy`, and the example's `FailMode`); 1× `UP045` in `varco_beanie/factory.py`; 1× `UP007` in `varco_core/meta.py`; 2× `UP045` in `varco_core/tests/test_serializer.py` (intentional `Optional[]` backward-compat testing, not migration debt) | 🟢 nice | S | Each is a mechanical follow-up to the RL-6 sweep, deliberately deferred rather than silently widening `[tool.ruff.lint] ignore` | `git grep -n 'noqa: UP0' -- 'varco_*' 'examples/**'` |
| RL-16 | **Integration tests do not gate PRs** (§RL-5-triggers, deliberate) | 🟡 should | — | `integration.yml` runs on push-to-`main` + nightly + manual dispatch only, never on PRs — a PR can break a broker-facing path (Kafka/NATS/Redis/Mongo/Postgres/Memcached) and only the nightly run catches it. Deliberate per the plan (fast PR feedback, bounded Actions minutes) but genuinely weaker than gating. Revisit in Phase 3 | `.github/workflows/integration.yml` triggers; plan 017 §RL-5-triggers |
| RL-17 | **`ruff format` deferred** (plan 017 Non-goal) | 🟢 nice | S | No formatter gate exists; `E501` is ignored in `[tool.ruff.lint]`, so nothing currently depends on a formatter running. A future adoption is unmeasured whole-tree churn across 439+ source files | `pyproject.toml` `[tool.ruff.lint] ignore` (no `[tool.ruff.format]` section exists) |
| RL-18 | **Package-list triplication** — `Makefile:PACKAGES`, `scripts/unit_tests.sh`, `scripts/integration_tests.sh` each hold their own hand-written copy of the ten/eleven-member list | 🟡 should | S | This is exactly how `varco_casbin` went missing from `make lint`/`make type-check`/etc. in the first place (RL-6's own finding). Deriving the list from `[tool.uv.workspace] members` in the root `pyproject.toml` would make the three files structurally unable to drift again | `Makefile:PACKAGES`; `scripts/unit_tests.sh`; `scripts/integration_tests.sh:91` |
| RL-19 | **`.pre-commit-config.yaml` ruff rev bumped `v0.4.1` → `v0.16.4`, unplanned** | 🟢 nice | — | Not anticipated by plan 017 — required because `v0.4.1` predates the `UP046`/`UP047` rule codes now referenced in `[tool.ruff.lint] ignore` and could not even parse the config, which would have blocked every local commit via the pre-commit hook | `.pre-commit-config.yaml` |
| KI-9 | **`varco_beanie.audit.BeanieAuditRepository.list_for_entity` is missing tenant scoping** — no `tenant_id` parameter, unlike the base `AuditRepository` class | 🔴 must | S | Surfaced by the mypy sweep, suppressed with a `# type: ignore` + inline note rather than silently patched (fix-first rule only covers trivially-local, obviously-correct fixes — adding tenant scoping to an audit query is a behaviour change, out of scope for a docstring/type-annotation pass). The Beanie audit trail is currently unscoped by tenant | `varco_beanie/varco_beanie/audit.py` (`list_for_entity`) |
| KI-10 | **`varco_beanie.bootstrap.BeanieApp`'s non-DI construction path is out of sync with `BeanieRepositoryProvider`'s real signature** — it calls `BeanieRepositoryProvider(mongo_client=, db_name=, transactional=)`, but that class's actual `__init__` takes `settings=` | 🔴 must | S | Surfaced by the mypy sweep (a genuine type error, not a suppression candidate for the fix-first rule since reconciling the two constructors is a behaviour decision). The two construction paths (DI-managed vs. `BeanieApp`'s direct instantiation) need reconciling before `BeanieApp`'s non-DI path can be trusted | `varco_beanie/varco_beanie/bootstrap.py` (`BeanieApp`); `varco_beanie/varco_beanie/di.py` (`BeanieRepositoryProvider`) |

---

## Phase 3 — reliability floor (RT, Plan 012)

**Verified status**, not the stale figures. RT1 and RT6 are done; the rest is genuinely
remaining work.

| ID | Feature | Status | Severity | Complexity | Rationale |
|----|---------|--------|----------|------------|-----------|
| RT1 | Testcontainers-backed integration runner (session-scoped fixtures per service, `VARCO_TEST_<SERVICE>_URL` override contract, `scripts/integration_tests.sh`) | ✅ **done** | 🔴 must | M | Shipped. Documented in CLAUDE.md §"Shared, session-scoped integration containers" |
| RT6 | Conformance/contract suite — one module per `varco_core` ABC, opted into by each backend | ✅ **done** | 🔴 must | L | Shipped: `testkit/varco_conformance/{event_bus,cache,job_store,dlq}.py`, subclassed by 8 backends incl. the Docker-free in-process run |
| RT2 | `varco_nats` real-broker coverage | 🟠 partial (2/13 files marked) | 🔴 must | S | Markers added, but coverage is thin against a 13-file suite |
| RT5 | `varco_kafka` integration — DLQ, offset management, partition rebalancing | 🟠 partial (7/15) | 🔴 must | M | Materially advanced from 1/9; rebalancing/offset paths still need real-broker verification |
| RT4 | `varco_ws` real WebSocket/SSE server — pooling, backpressure, reconnect, ordering | 🟠 partial (2/7) | 🔴 must | M | Was zero; still the thinnest coverage of any shipped event-bus backend |
| RT3 | `varco_casbin` + Postgres/SQLAlchemy adapter integration | 🟠 partial (2/10) | 🔴 must | S/M | Postgres is CLAUDE.md's recommended durable adapter and remains largely unverified against a real database |
| RT8 | One-command smoke run of `examples/00-full-stack-post-api` | 🟠 partial | 🟡 should | S | Suite executed and its findings recorded (below), but not wired into the standard runner |
| RT9 | Migration lifecycle integration — `create_varco_app(migrations=...)` against a real DB | ⬜ pending | 🟡 should | S | Startup migration (locking, `check`/`upgrade`) is safety-critical and unit-tested only; a regression breaks every app at boot |
| RT7 | Chaos / fault-injection — outbox relay + broker restart, breaker + real network failure, job lease + worker crash | ⬜ pending | 🔴 must | L | **The long pole.** Validates the guarantees varco actually sells against real failure, not mocks. If the release horizon tightens, this is the first item to renegotiate |

---

## Phase 4 — API freeze prep

Must complete **before** the version freeze in phase 5 — 3.0.0 is the last cheap window.

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RL-8 | **API-surface audit + breaking cleanup** — ranked pass over the DI wiring verb taxonomy, the documented name collisions (`MigrationError`/`MigrationPlan` not re-exported from `varco_core`; `enable_rls_ddl()` sitting outside the `enable_*` family; `install_*` colliding with providify's `container.install`), and remaining fail-open defaults | 🔴 must | M | User decision this session: deliberately spend the breaking-change window. CLAUDE.md already documents these collisions as things it "exists specifically to call out" — a wart the docs must warn about is a wart worth removing while it's still free. Post-3.0.0 each of these costs a full deprecation cycle | `CLAUDE.md` §"DI wiring verb taxonomy" (two collisions called out explicitly); §"Schema migrations" (⚠️ not-re-exported note) |
| RL-8a | **Decide whether `VarcoLifespan` should call `container.ashutdown()`** — providify 2.0.0's aggregated `ShutdownError`/`ShutdownFailure` (characterized, not adopted, by Plan 016 / RL-3b) has no call site in varco today; `VarcoLifespan._stop_all()` only stops explicitly registered lifecycle components, logging-not-raising on a failing `stop()`. Adopting `ashutdown()` would newly fire every `@PreDestroy` hook in the container (`varco_kafka/bus.py:329`, `varco_nats/bus.py:288`, `varco_redis/bus.py:225`, `varco_redis/cache.py:214`, `varco_redis/streams.py:326`, `varco_casbin/engine.py:211`, `varco_kafka/channel.py:235`, `varco_nats/channel.py:276`, `varco_redis/channel.py:132`, `varco_memcached/cache.py`, …) — some of which may double-stop a component `_stop_all()` already stopped, a real behaviour change out of scope for a mechanical upgrade phase | 🟡 should | S | ⚠️ **Suspected, not proven:** a `@PreDestroy`-bearing singleton that is not also a registered `VarcoLifespan` component is never torn down today — this is derived from `lifespan.py`'s `_stop_all()` shape plus the `@PreDestroy` inventory above, but has not been exhaustively cross-checked class-by-class against what `create_varco_app()` registers as a lifecycle component | `varco_fastapi/varco_fastapi/lifespan.py:205-212` (`VarcoLifespan.__call__`/`_stop_all`), `varco_fastapi/tests/test_lifespan_shutdown_characterization.py` (locks the `ShutdownError`/`ShutdownFailure` shape for the future adoption) |

---

## Phase 5 — release

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RL-9 | **Version unification at 3.0.0 + written SemVer/deprecation policy** — a single-source-of-truth bump mechanism replacing ten hand-edited `version =` fields, and `Development Status :: 5 - Production/Stable` | 🔴 must | M | Current state is incoherent: core/fastapi/beanie 1.2.0, memcached 1.1.1, ws 2.1.0, kafka/nats/casbin 2.1.1, redis 2.1.2, sa 2.2.0 — no bump tool, all still Alpha-classified. Research: a written deprecation policy (PEP 387-style, 2-year cycles) is the expected bar, not optional polish | scan of all ten `pyproject.toml` versions; [research brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §1 |
| RL-10 | **Release automation + supply-chain posture** — tag-triggered PyPI publish via OIDC **trusted publishing**, PEP 740 attestations, `dependabot.yml`, OpenSSF Scorecard workflow, all actions pinned by commit SHA | 🔴 must | M | The user's explicit ask ("similar to providify"). providify ships precisely this and it can be copied in shape across ten packages. Trusted publishing removes the long-lived-token risk entirely | providify `.github/workflows/{release,scorecard}.yml`, `.github/dependabot.yml`; [research brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §1 (PyPI attestations) |
| RL-11 | **Governance + community files** — `CONTRIBUTING.md` (carrying RL-9's versioning/deprecation policy), `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue + PR templates, `CODEOWNERS`; plus gitignore hygiene | 🔴 must | S | All absent today. Research lists `SECURITY.md` as outright release-blocking for a framework handling JWTs, encryption keys and multitenant isolation. Hygiene: `dist/`, `site/`, `scratchpad/`, `integration_test.log` and a stray `varco_beanie/.venv/` are sitting in the working tree | scan of repo root and `.github/`; [research brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §1 |
| RL-12 | **Versioned docs hosting — GitHub Pages + `mike`** — publish the existing mkdocs site from CI with a version switcher (3.0 / latest / dev) | 🔴 must | S/M | `mkdocs.yml` and a locally-built `site/` exist but nothing publishes them. User chose Pages over Read the Docs to stay inside the GitHub setup RL-10 already builds | `mkdocs.yml`, `scripts/gen_ref_pages.py`, untracked `site/`; [research brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §1 |
| RL-13 | **PEP 639 license metadata + PEP 735 dependency-groups audit** across all ten packages | 🟢 nice | S | Modernizes packaging metadata to the 2026 standard while every `pyproject.toml` is already being edited for RL-9. Cheap only if done in the same pass — otherwise it's ten more PRs later | [research brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §3 (uv + PEP 735 + PEP 639 standardization) |

---

## Parked

| Feature | Why parked |
|---------|------------|
| **Standards alignment — CloudEvents envelope, AsyncAPI export, OpenFeature integration** | Research flags these as moving from differentiator to table stakes in 2026, but the user scoped 3.0.0 to release engineering + reliability. Deferred to **3.1**. Note varco already has A2A and MCP adapter surfaces, so the gap is narrower than the category suggests. [Brief](design/varco-1-0-release/research/001-release-and-ecosystem-stakes.md) §2–3 |
| **GraphQL surface, event sourcing** | Named by research as absent vs comparable frameworks, but neither is on the differentiation axis varco actually competes on (multitenancy isolation, field-level encryption/crypto-shredding, audit trails for regulated workloads). Not re-litigating without user demand |
| **Independent per-package versioning** | Considered and rejected this session in favour of lockstep 3.0.0. Revisit only if release churn from ten-package bumps becomes a real cost |
| **`varco` umbrella meta-package with pinned extras** | Rejected as the most machinery for the least 3.0.0 benefit under lockstep versioning — a lockstep release already gives users one number to trust |
| ~~**Re-enable `integration.yml` in GitHub Actions** (as a PR gate)~~ | ⚠️ **SUPERSEDED** — parked in a prior session ("no CI budget concern, but the user runs integration tests on their own schedule locally"). Reversed this session: a public release needs CI. Now **RL-5** |
| ~~**Re-enable `integration.yml` as manual/nightly trigger**~~ | ⚠️ **SUPERSEDED** — same reversal. Now **RL-5** |
| **New dedicated e2e reference application** | Research found no comparable reliability-focused framework uses a monolithic reference app as its primary regression strategy — per-feature chaos tests (RT7) score better, and an existing example already covers the cross-feature case (RT8). Park stands |

---

## Answered by Plan 016 (do not relitigate)

- **RL-1 sequencing** → **two-step, one branch, two commits**: un-vendor against 1.1.0 first
  (commit `7cea36b`), full sweep, *then* bump to 2.0.0 (commit `1008655`), full sweep again. A
  red sweep at step one means "PyPI's artifact ≠ the vendored local build"; a red sweep at step
  two means "2.0.0 changed behaviour" — two different fixes, and a conflated failure would have
  been expensive to diagnose across ten packages. See Plan 016 Design §RL-1.
- **RL-3 pytest plugin adoption** → **leave it to consumers; document, do not wrap.** The four
  fixtures (`di_container`/`di_overrides`/`di_global`/`di_acontainer`) are documented in
  `CLAUDE.md`'s Test Conventions and `README.md`'s testing section (commit `4815301`); varco's
  testkit deliberately does not re-export or wrap them, and a consumer conftest redefining
  `di_container` wins over the plugin default. See Plan 016 Design §RL-3d.

## Open questions for `/plan`

- **RL-9 bump mechanism**: which tool owns the single source of truth for the lockstep version
  (hatch-vcs from the git tag, `uv version`, or a `scripts/bump.py`), and whether the ten
  packages' inter-dependencies pin exactly (`==3.0.0`) or compatibly (`~=3.0`).
- **RL-8 audit scope**: the audit produces a ranked list of candidate breaks — an explicit
  accept/reject checkpoint is needed before any of them land, since each one spends
  irreplaceable 3.0.0 budget.
- **RT7 shape**: which failures are simulated in-process vs driven by real container
  kill/restart, and whether chaos tests run in `integration.yml` or on a separate schedule.

---

## Appendix — Plan 012 / 014 / 015 history

Retained from the previous backlog: findings and deferred follow-ups from earlier work,
kept because several remain open and feed phases 3 and 4.

### Known issues found while implementing Plan 012 (xfail'd, not fixed — Non-goals)

| ID | Finding | Evidence |
|----|---------|----------|
| KI-3 | ✅ **Fixed** — `RedisCache.set(ttl=)` (`varco_redis/varco_redis/cache.py`) truncated a sub-second float `ttl` to `int()` before calling `SETEX` — `ttl=0.05` became `0`, and Redis's `SETEX` rejects a `0`/negative expire time with `ResponseError: invalid expire time in 'setex' command`, raising instead of storing a very-short-lived entry. `CacheBackend.set()`'s `ttl: float \| None` contract implies sub-second precision is valid. Fixed by switching `set()`/`set_many()` to millisecond-precision `PSETEX` (`round(ttl * 1000)`) instead of second-precision `SETEX`/`int(ttl)`; a ttl that still rounds to `<=0`ms now raises a clear `ValueError` instead of Redis's cryptic `ResponseError`. `TestLayeredCacheConformance::test_ttl_expiry` (L1 `InMemoryCache` + real Redis L2) inherited the same symptom but for an unrelated second reason — its fixture built L1 with no `InvalidationStrategy`, and per `InMemoryCache`'s own documented contract a strategy-less L1 never expires a ttl-bearing entry on its own; fixed by giving the fixture's L1 a `TTLStrategy()`. | `varco_redis/tests/test_redis_conformance.py::TestRedisCacheConformance::test_ttl_expiry` and `::TestLayeredCacheConformance::test_ttl_expiry` (previously `xfail(strict=True)`, now pass), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Redis instance |
| KI-5 | ✅ **Fixed** — `MemcachedCache.set(ttl=)` (`varco_memcached/varco_memcached/cache.py`) truncated a sub-second float `ttl` to `int()` before passing it as `exptime` — `ttl=0.05` became `exptime=0`, which the Memcached protocol treats as "no expiry" rather than "expire almost immediately"; the entry was never evicted. Same root cause as KI-3 (`RedisCache`), different failure mode (silent no-expiry instead of a raised error). Unlike KI-3, Memcached's `exptime` is genuinely whole-seconds-only at the wire-protocol level — there is no millisecond-precision command to switch to (Redis's `PSETEX` fix does not apply here). Fixed by rounding a positive sub-second `ttl` UP to the smallest expressible non-zero `exptime` (`1`) via `math.ceil()`, instead of truncating DOWN to `0` — an explicit `ttl<=0`/`ttl=None` still means no-expiry, unchanged. The shared conformance suite's `ttl=0.05`/`sleep(0.3)` timing cannot observe a real 1-second-granularity expiry, so `TestMemcachedCacheConformance.test_ttl_expiry` overrides the shared test with timing compatible with that real granularity (`sleep(1.3)`) rather than loosening the shared suite for every other backend. | `varco_memcached/tests/test_memcached_conformance.py::TestMemcachedCacheConformance::test_ttl_expiry` (previously `xfail(strict=True)`, now passes with an overridden timing window), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Memcached instance; hardened with 3 new unit tests in `varco_memcached/tests/test_cache.py` (sub-second round-up, fractional-above-1s round-up, explicit `ttl=0` still no-expiry) |
| KI-6 | ✅ **Fixed** — `BeanieDeadLetterQueue.count_by_channel()` (`varco_beanie/varco_beanie/dlq.py`) did `await DeadLetterDocument.aggregate(pipeline).to_list()`. Root cause: beanie's `AggregationQuery.get_cursor()` unconditionally `await`s the collection's `aggregate()` call, but the installed motor version's `AsyncIOMotorCollection.aggregate()` returns its cursor synchronously (not a coroutine) — `TypeError: object AsyncIOMotorLatentCommandCursor can't be used in 'await' expression`. Fixed by driving `DeadLetterDocument.get_pymongo_collection().aggregate(pipeline)` directly (bypassing beanie's broken cursor plumbing) and iterating with `async for`, tolerating both a sync-cursor and a coroutine-returning `aggregate()`. | `varco_beanie/tests/test_beanie_conformance.py::TestBeanieDeadLetterQueueConformance::test_count_by_channel_no_predicate_refuses_or_raises` (previously `xfail(strict=True)`, now passes), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real MongoDB instance |
| KI-7 | ✅ **Fixed** — `NatsDLQ.delete_where()` (`varco_nats/varco_nats/dlq.py`) always raised `NotImplementedError`, even when called with **no predicate at all** — same class of deviation as KI-2 (`KafkaDLQ`) from the `AbstractDeadLetterQueue` ABC's documented "no predicate -> `ValueError`" contract (`varco_core/varco_core/event/dlq.py:440-489`). Root cause: `delete_where()` jumped straight to the backend-support `NotImplementedError`, never reaching the ABC's own "was any predicate given at all?" guard. Fixed by adding that guard as the first check in `NatsDLQ.delete_where()`, matching its own full keyword-only signature (`older_than`/`source`/`channel`/`tenant_id`/`limit`) instead of a catch-all `**_kwargs`, so an unbounded call raises `ValueError` before the backend-support `NotImplementedError`. | `varco_nats/tests/test_nats_conformance.py::TestNatsDLQConformance::test_delete_where_no_predicate_raises` (previously `xfail(strict=True)`, now passes), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real NATS/JetStream broker |
| KI-8 | ✅ **Fixed** — `CasbinPolicyEngine.enforce()` (`varco_casbin/varco_casbin/engine.py`) always wraps subject/object in `_AttrStr` (a `str` subclass with a custom `__new__(cls, value, attrs)`), even for the plain RBAC preset. `_AttrStr` had no `__deepcopy__`/`__reduce__`; once one had been threaded into Casbin's internal role-manager/model state via an `enforce()` call, a later `CasbinPolicyEngine.reload()` (-> Casbin's `load_policy()` -> `copy.deepcopy(self.model)`) raised `TypeError: _AttrStr.__new__() missing 1 required positional argument: 'attrs'` — `copy.deepcopy`'s default reconstruction for a `str` subclass calls `cls(value)` only, never the extra `attrs` kwarg the custom `__new__` requires. Fixed by adding `_AttrStr.__reduce__`, which stashes the original `attrs` mapping in `__new__` and returns `(cls, (str(self), self._attrs))` so `deepcopy`/`pickle` reconstruct through the real constructor instead of the broken default path. Chosen over the alternative (only wrap in `_AttrStr` for ABAC-configured engines) because it fixes the actual `str`-subclass/`deepcopy` incompatibility at its root without adding preset-conditional branching to `enforce()`, and preserves the existing "one engine serves ACL/RBAC/ABAC uniformly" design the module docstring describes. | `varco_casbin/tests/test_persistence_integration.py::test_two_engines_share_database_writer_reader` (previously `xfail(strict=True)`, now passes), verified against a real Postgres-backed `CasbinPolicyEngine`; full `varco_casbin/tests/` suite (67 tests, unit + `-m integration`, including ABAC tests in `test_abac_e2e.py`) green |

### Example suite findings (Plan 012 / RT8, Step 34 — corrected in test files, no production code touched)

Running `examples/00-full-stack-post-api`'s real integration suite for the first time (C-6/A-6
had never actually executed it) surfaced one missing-config issue and two stale test
expectations, all fixed inside `examples/00-full-stack-post-api/example/tests/` (a permitted
path):

- `example/app.py` constructs `JwtBearerAuth(registry=registry, required=False)` with no
  `audience=` — since Plan 005 Phase 2, `JwtBearerAuth()` refuses to construct without an
  audience configured (`ValueError`). Fixed by setting `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` in
  `example/tests/conftest.py`'s `running_server` fixture (a demo app has no single-audience
  concept to enforce).
- `test_me_with_garbage_token_is_anonymous` asserted a *present* malformed Bearer token falls
  back to anonymous — `JwtBearerAuth.__call__`'s own docstring documents `required=False` only
  covers an *absent* Authorization header; a present-but-invalid token always raises 401.
  Renamed to `test_me_with_garbage_token_returns_401` and corrected the assertion.
- `test_anonymous_cannot_create_post_returns_403` asserted anonymous `POST /v1/posts` is
  rejected — `example/authorizer.py`'s own docstring documents anonymous CREATE as allowed
  (`author_id=None`); only anonymous UPDATE/DELETE are rejected. Renamed to
  `test_anonymous_can_create_post_with_null_author` and corrected the assertion.

---

### Deferred follow-ups (Plan 014 / audit 001 Batch B)

- **`weakref.WeakSet[FastAPI]` upgrade for the double-mount guards** — both `varco_fastapi.tenancy.mount._MOUNTED_APPS` and `varco_fastapi.admin.mount._MOUNTED_APPS` are `set[int]` keyed by `id(app)`, which can produce a spurious `ValueError` if a collected `FastAPI` instance's id is reused by a new, unrelated app; deliberately not fixed in Plan 014 to keep `mount_reliability_admin()`'s guard shape-identical to the `mount_tenant_admin()` reference it was ported from — should change *both* modules together in one follow-up.
- **`varco_redis.di.async_bootstrap()` is missing the `container is None` guard `varco_memcached.di.async_bootstrap()` has** — when providify is absent, `bootstrap()` returns `None` and the subsequent `await container.ainstall(RedisCacheConfiguration)` (when `setup_cache=True`) raises `AttributeError: 'NoneType' object has no attribute 'ainstall'` instead of returning `None` like every other varco `async_bootstrap()`.

---

### Deferred follow-ups (Plan 015 / audit 002)

- **F12 — `## Test Conventions` prose density (RT1/RT6 paragraphs)** — the audit flagged this as
  "a judgment call, not a clear misplacement," and Plan 015 explicitly left it untouched
  (`## Test Conventions` in `CLAUDE.md` is byte-identical to before the refactor). Revisit in a
  future pass if the section keeps growing.

---

### Findings from Plan 016 (RL-3a — `container.validate()` adoption, Step 24)

`assert_no_structural_di_issues()` (`testkit/varco_conformance/providify_health.py`) was wired in
after the existing `container.validate_bindings()` call at all 17 surviving per-package DI health
sites (see Plan 016 Step 23's file list). **No structural error (`AMBIGUOUS_BINDING`,
`CIRCULAR_DEPENDENCY`, `SCOPE_LEAK`, `LIVE_REQUIRED`, `UNRESOLVED_ANNOTATION`) was found at any
site** — every one of the 17 tests passed unmodified on the first run. Per Design §RL-3a /
Non-goals, no `xfail` was needed and no production code was touched.

Every `MISSING_BINDING` the new assertion tolerates (a package scanned alone legitimately lacks
the application's own bindings) — recorded here rather than in an allowlist, per the design's
own rejection of a per-test allowlist:

| Package (scanned alone, no app bindings) | Tolerated `MISSING_BINDING` |
|---|---|
| `varco_core` | none |
| `varco_kafka` | none |
| `varco_nats` | none |
| `varco_redis` | none |
| `varco_sa` | `SAConfig` — `'config' requests SAConfig but no binding is registered and no default value exists` (the app must supply its own `SAConfig`, e.g. via a `@Provider`) |
| `varco_beanie` | `BeanieSettings` — `'settings' requests BeanieSettings but no binding is registered and no default value exists` (same shape as `SAConfig` above — the app supplies its Mongo connection settings) |
| `varco_fastapi` | none observed at this call site — `varco_fastapi`'s framework-default ABCs (`AbstractJobStore`, `AbstractServerAuth`, …) are registered by `VarcoFastAPIModule`, a `@Configuration` class that a bare `container.scan("varco_fastapi", recursive=True)` does **not** auto-install (it requires an explicit `container.install(VarcoFastAPIModule)`), so this scan-alone shape never reaches the code paths that would report those interfaces missing |

---
