# Research 002 — providify 2.0.1 release status
Date: 2026-09-02 · Freshness matters: yes — version boundaries and release timing change.

## Question
Has `providify` 2.0.1 shipped, and specifically has it fixed the `container.ashutdown()` lifecycle gap where `@PreDestroy` hooks on instances produced by `@Provider` methods are never consulted (an orphaned resource leak)?

## Findings
- **Latest published version is 2.0.1, released September 1, 2026** — [providify 2.0.1 on PyPI](https://pypi.org/project/providify/2.0.1/) (upload date Sep 1, 2026)
- **2.0.1 has shipped.** — Local checkout at `/home/edoardo/projects/providify` reflects the released version in `pyproject.toml` — [version 2.0.1](file:///home/edoardo/projects/providify/pyproject.toml:3) (local working copy)
- **2.0.1 does NOT fix the ashutdown/@PreDestroy/@Provider gap.** — The CHANGELOG entry states: "This release did **not** introduce changes to `ashutdown()`, general lifecycle fixes, or disposal mechanisms—those came in version 2.0.0." The only changes in 2.0.1 are: (1) a new validation warning `IssueKind.UNREACHABLE_PRE_DESTROY` that *reports* the gap, and (2) docstring corrections clarifying that `@PreDestroy` applies only to class bindings, while `@Disposes` is the exclusive teardown path for provider-produced instances — [CHANGELOG.md 2.0.1 entry](file:///home/edoardo/projects/providify/CHANGELOG.md:12-32)
- **2.0.0 (Aug 25, 2026) introduced the lifecycle machinery, but did not fix the @Provider/@PreDestroy gap.** — The 2.0.0 CHANGELOG section "Graceful shutdown — reverse-dependency-order teardown" and "Lifecycle hooks on scope exit" describe teardown *order* fixes and scope-exit callbacks, but the only hook teardown path for provider-produced instances is `@Disposes` — no facility was added to invoke `@PreDestroy` on instances created by `@Provider` methods — [CHANGELOG.md 2.0.0 entry, lines 289–327](file:///home/edoardo/projects/providify/CHANGELOG.md:289-327)
- **Release cadence:** 0.1.3 (Apr 10) → 0.1.4a1 (Apr 18) → 0.1.4a2 (Apr 20) → 0.1.5 (Apr 24) → 0.1.6 (Apr 25) → 0.1.7 (Apr 26) → 2.0.0 (Aug 25) → 2.0.1 (Sep 1). The jump 0.1.7 → 2.0.0 (eight weeks) and 2.0.0 → 2.0.1 (seven days) show a slow pre-1.0 alpha phase (within one week in April), then a eight-week gap, then rapid 2.x iteration — [CHANGELOG.md full history](file:///home/edoardo/projects/providify/CHANGELOG.md:1-665)

## Interpretation — does varco's gate trigger?

The varco 3.0.1 release plan explicitly gates on providify 2.0.1 fixing an upstream gap: "container.ashutdown() never consulting @PreDestroy for @Provider-produced instances (an orphaned resource leak)." Two `strict=True` xfails in varco guard the gap and are expected to turn red once the fix lands.

**Evidence status:**
- 2.0.1 has shipped (Sep 1, 2026 — today is Sep 2).
- 2.0.1 does NOT contain the fix. The CHANGELOG explicitly states this release contains no lifecycle fixes; it only adds a *validation warning* that detects the gap and documentation corrections clarifying the gap is intentional (per Jakarta CDI spec: producer-method instances receive no lifecycle callbacks).
- The gap remains unfixed and no public signal points to a future release that will fix it. The CHANGELOG "Unreleased" section (lines 10–11) is empty — no planned fixes are tracked publicly.

**The xfails will not turn red yet.** varco's gate depends on a fix that has not landed in any released version of providify.

## Version/compatibility notes
- **providify current releases** — 2.0.1 (Sep 1, 2026), 2.0.0 (Aug 25), 0.1.7 (Apr 26), and earlier 0.1.x alphas.
- **varco pins providify ≥ 2.0.0** in ten `pyproject.toml` files and locks at 2.0.0 in `uv.lock`.
- **@Provider producer-method instances and lifecycle:** Jakarta CDI spec (provider methods never receive lifecycle callbacks) is the reference; providify 2.0.0/2.0.1 follow this design — `@Disposes` is the only teardown path for provider-produced singletons, `@PreDestroy` applies only to class bindings.

## Evidence gaps
- **No public GitHub issues or pull requests** found signalling that the @Provider/@PreDestroy fix is planned, in progress, or deferred. The gap detection (2.0.1's new `UNREACHABLE_PRE_DESTROY` validation warning) exists as a way to audit existing code for the pattern, not as a step toward a fix.
- **It is unknown whether the fix is planned at all** — the CHANGELOG is the only public signal, and it shows no roadmap for provider-instance lifecycle callbacks. An issue or discussion on the providify repository would clarify intent.

## Librarian's note
**What the sources indicate:** 2.0.1 has shipped, but it does not contain the lifecycle fix varco 3.0.1 awaits. The gap remains by design (Jakarta CDI spec) and is now *detected* by validation (2.0.1), but not *fixed*. Varco's release gate will not trigger; the xfails will remain in place. A future providify release *could* add provider-instance lifecycle support (e.g., an optional hook registry or a @Disposes discovery pass for `@PreDestroy` methods), but no public signal indicates this is planned or imminent. Recommend: (1) check providify's open issues to see if maintainer intent is documented, (2) consider whether varco's 3.0.1 scope can be decoupled from this upstream fix, or (3) open an issue on providify if the fix is a blocker.
