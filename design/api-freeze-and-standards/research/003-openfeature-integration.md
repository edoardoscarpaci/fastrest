# Research 003 — OpenFeature integration feasibility for varco

Date: 2026-08-30 · Freshness matters: **yes** — SDK releases frequently; spec < 1.0; async support matured in 2025.

## Question

Can OpenFeature (Python SDK) cleanly integrate into varco as a feature-flag abstraction? Specifically:
1. What is OpenFeature's CNCF/spec maturity (2026)?
2. What are the Python SDK's versioning, maintenance signal, and dependency footprint?
3. Is the Python SDK truly async-first, or does it require blocking calls / thread offload?
4. What exact provider contract must varco's ABC map onto?
5. How do evaluation context and hooks work—especially request-scoped context flow?
6. What providers are available off-the-shelf for testing and production?
7. What are the honest tradeoffs vs. building our own `BaseSettings`-only approach?

## Findings

**CNCF maturity & spec version**
- OpenFeature is a [CNCF Incubating project](https://www.cncf.io/projects/openfeature/) as of December 19, 2023 — not graduated, not sandbox
- Current spec version is v0.8.0 (introduced OFREP protocol; stable sections require no breaking changes without major version)
- The project demonstrates healthy contributor growth: 2,312 contributors (+53% YoY), 746 orgs (+32% YoY) — [CNCF Project Metrics](https://www.cncf.io/project-metrics/)

**Python SDK version, release cadence, dependencies**
- Current version: **0.10.0** (released June 1, 2026) — [openfeature-sdk on PyPI](https://pypi.org/project/openfeature-sdk/)
- Specification support: v0.8.0 (confirmed badge on PyPI)
- Python requirement: ≥3.10 (excludes Python 3.8–3.9)
- **Maintenance signal: strong.** Multiple releases per year; 0.10.0 introduced breaking change (`set_provider()` no longer blocks; use `set_provider_and_wait()`); regular security/dependency updates — [CHANGELOG on GitHub](https://github.com/open-feature/python-sdk/blob/main/CHANGELOG.md)
- **Dependencies: minimal.** No third-party runtime deps declared in PyPI metadata; pure Python implementation
- Note: The SDK has NOT shipped a stable (≥1.0) release; minor versions still carry breaking changes (e.g., 0.10.0 breaking change noted above)

**Async support — critical question**
- **YES, fully async-first.** Python SDK provides both sync and async evaluation APIs
  - Sync (blocking): `client.get_boolean_value(flag_key, default)` — [OpenFeature Python SDK docs](https://openfeature.dev/docs/reference/sdks/server/python/)
  - Async (non-blocking): `await client.get_boolean_value_async(flag_key, default)` — suffixed with `_async`
- Same pattern for all five flag types (boolean, string, integer, float, object)
- **Provider-side async is optional:** If a provider has not implemented `resolve_*_details_async()`, the SDK falls back to blocking the async call (calls the sync method inside `await`) — graceful degradation, not failure
- **Request-scoped context propagation:** Uses `ContextVarsTransactionContextPropagator` for asyncio (PEP 567 `ContextVar`-based, same seam as varco's own ambient-context system) — [OpenFeature Hooks spec](https://openfeature.dev/specification/sections/hooks/)
- **Verdict:** Async-compatible end-to-end for varco; no thread offload needed for well-behaved providers

**Provider contract (AbstractProvider interface)**
- Required abstract methods per flag type:
  ```python
  async def resolve_boolean_details(
    self,
    flag_key: str,
    default_value: bool,
    evaluation_context: Optional[EvaluationContext] = None,
  ) -> FlagResolutionDetails[bool]:
    """Return FlagResolutionDetails containing: value, reason, error_code, metadata"""
  ```
  Same signature for `_string`, `_integer`, `_float`, `_object` variants — [Python SDK docs](https://openfeature.dev/docs/reference/sdks/server/python/)
- Optional async variants:
  ```python
  async def resolve_boolean_details_async(...) -> FlagResolutionDetails[bool]:
  ```
- Required metadata:
  ```python
  def get_metadata(self) -> ProviderMetadata:
    """Return name and other provider info"""
  ```
- Optional hooks:
  ```python
  def get_provider_hooks(self) -> List[Hook]:
  ```
- `FlagResolutionDetails[T]` shape: `(value: T, reason: str, error_code: Optional[str], metadata: Optional[dict])`
- **Mapping note:** varco's ABC would need to wrap these five methods; sync calls need no wrapper, async calls map 1:1

**Evaluation context & hooks — request-scoped flow**
- **EvaluationContext composition** (precedence order, low→high): API (global) → Transaction (scoped by `ContextVar`) → Client (instance-level) → Invocation (call-level) → Before hooks (mutable only here)
  - Duplicate keys overwritten at each level; higher level wins — [Evaluation Context docs](https://openfeature.dev/docs/reference/concepts/evaluation-context/)
- **Hook stages:** Before (modify context, runs before resolution) → After (after success) → Error (after failure) → Finally (always)
  - Before hooks receive a **mutable** `EvaluationContext`; only this stage can modify it
  - After/Error/Finally hooks receive immutable context — [Hooks spec](https://openfeature.dev/specification/sections/hooks/)
- **Execution order:** Before (API→Client→Invocation→Provider); After/Error/Finally (reverse, Provider→Invocation→Client→API)
- **Variance with varco's pattern:** Varco's `AmbientVar[T]` + `RequestContext` uses `ContextVar` for tenant/locale at a different layer (request middleware). Integration point: Before hook can pull `current_tenant()` and inject into `EvaluationContext` automatically — no user code needed — **if a Before hook is registered at client initialization**

**Available providers — in-process and remote**
- **In-process/testing:**
  - `InMemoryProvider` (bundled with SDK): in-memory flag definitions, intended for unit tests — [Appendix A: Included Utilities](https://openfeature.dev/specification/appendix-a-included-utilities/)
  - `EnvironmentVariableProvider` (bundled): demo showing env-var-based evaluation
- **Remote (separate packages, via open-feature ecosystem):**
  - `openfeature-provider-flagd` (v0.x) — reference implementation, containerized, open-source — [PyPI](https://pypi.org/project/openfeature-provider-flagd/)
  - `launchdarkly-openfeature-server` (0.1.2+) — LaunchDarkly's official OpenFeature adapter — [GitHub](https://github.com/launchdarkly/openfeature-python-server)
  - `openfeature-provider-posthog` (0.1.26+) — PostHog integration, released July 31, 2026 — [PyPI](https://pypi.org/project/openfeature-provider-posthog/0.1.26/)
  - Flipt, ConfigCat, GrowthBook, DevCycle, Cloudflare Flagship — all have working Python providers (via SDKs or third-party bindings)
- **No "official standard" in-process provider beyond InMemory** — `flagd` is the reference but requires containerization; for unit tests, InMemory is the norm

**Tradeoffs vs. varco's current `BaseSettings`-only approach**
| Aspect | OpenFeature abstraction | varco `BaseSettings` + env vars |
|---|---|---|
| **Runtime evaluation** | ✅ Flags can change at runtime (via provider) | ❌ Read once at startup; immutable thereafter |
| **Vendor portability** | ✅ Swap providers (flagd→LaunchDarkly) without code change | ❌ Tied to env vars / config format |
| **Feature parity** | ❌ LCM API loses vendor-specific features (A/B, gradual rollout, analytics) | ✅ Vendors' full feature sets via direct SDK if needed |
| **Abstraction cost** | ⚠️ 5-method interface per provider; hooks layer adds indirection | ✅ Native SDK = zero indirection |
| **Data portability** | ❌ Doesn't solve flag-config/history migration between vendors | ❌ Not solved by either approach |
| **Operational complexity** | ⚠️ Still requires testing, validation, rollback; code-level portability ≠ operational portability | ⚠️ Same; env-var changes need validation |
| **Testing** | ✅ InMemoryProvider + easy provider mocking | ✅ Override env vars in test fixtures |
| **Startup blocking** | ⚠️ 0.10.0 breaking change: `set_provider()` is now async; must use `set_provider_and_wait()` if you need synchronous wiring | ✅ `BaseSettings` is synchronous, no async startup cost |

**Honest critique — when OpenFeature is NOT the right fit:**
- **If your only flag use is configuration (enable/disable feature X by env var):** Building a varco-native `FeatureFlag` abstraction in `varco_core` backed by `BaseSettings` is simpler, zero-dependency, and sufficient. OpenFeature's payoff only materializes if you will (a) adopt a remote flag service, (b) want runtime flag changes, or (c) genuinely evaluate multi-vendor strategies
- **If you need advanced vendor features (A/B testing, targeting, analytics):** OpenFeature's LCM API will force wrapper layers anyway; using the vendor SDK directly may be clearer
- **The real limitation OpenFeature doesn't solve:** You are locked into your vendor's **configuration, rules, analytics, and audit trail**, not just the code. Switching providers requires manual rule re-implementation and data loss/migration — OpenFeature only removes code churn, not operational churn
- **Pre-1.0 spec risk:** Breaking changes are possible in 0.x releases (0.10.0 example: `set_provider()` no longer blocks); this is minor but real for long-lived varco integrations

## Version/compatibility notes

- **Python SDK:** 0.10.0 (June 2026), spec v0.8.0
- **Spec maturity:** v0.8.0 stable sections require major version for breaking changes; unstable sections may change
- **Python support:** ≥3.10 (varco currently supports 3.12+, so no compatibility gap)
- **Key breaking change:** 0.10.0 requires `set_provider_and_wait()` if you need blocking provider initialization; `set_provider()` is now async-only

## Evidence gaps

- **SDK dependency footprint:** PyPI metadata lists no explicit dependencies; need to verify transitive closure (setuptools, typing-extensions, etc.) to confirm "minimal" claim
- **Production adoption signal:** Search results show logos/mentions from enterprises (Harness, DevCycle, Cloudflare) but no public data on maturity/performance characteristics of Python SDK specifically at scale
- **flagd as de-facto standard:** Appears in docs and tutorials but no explicit "recommended" language in OpenFeature's own material for Python; Go/Java might have stronger community
- **Comparative async performance:** No benchmarks found comparing OpenFeature's async dispatch overhead vs. native SDK direct calls or thread-pool alternatives
- **Worth a separate brief:** flagd architecture, deployment/operational model (stateless? config reload patterns?), and whether it's suitable as varco's "out-of-the-box" provider for dev/test environments

## Librarian's note

**What the sources indicate:**

OpenFeature is a credible, actively maintained abstraction with genuinely async Python support (as of 2025, mature). The spec is < 1.0 but stable for core sections. **The integration makes sense only if varco intends to support runtime flag evaluation (not startup-config-only)**. If today's `BaseSettings` pattern is sufficient, adding OpenFeature introduces abstraction overhead (5-method provider ABC, hook mechanics, `set_provider_and_wait()` async wiring) without payoff.

The honest tradeoff: OpenFeature eliminates **code** vendor lock-in but not **data** lock-in (rule configs, analytics history, audit trails stay with the vendor). Pre-1.0 spec means breaking changes are possible on minor-version bumps, though the Python SDK's release cadence and breaking-change documentation are professional.

**Decision criterion:** If varco's roadmap includes "swap flag providers at runtime" or "support teams that want LaunchDarkly, Flipt, or flagd interchangeably," OpenFeature is the right fit. If the goal is "make environment variables first-class," a varco-native `FeatureFlag` abstraction in `varco_core` (backed by `BaseSettings`) is simpler and has zero external dependencies.

---

## Sources

- [OpenFeature CNCF Project Page](https://www.cncf.io/projects/openfeature/)
- [OpenFeature Becomes CNCF Incubating (Dec 2023)](https://www.cncf.io/blog/2023/12/19/openfeature-becomes-a-cncf-incubating-project/)
- [CNCF Project Metrics](https://www.cncf.io/project-metrics/)
- [openfeature-sdk on PyPI](https://pypi.org/project/openfeature-sdk/)
- [Python SDK CHANGELOG](https://github.com/open-feature/python-sdk/blob/main/CHANGELOG.md)
- [OpenFeature Python SDK Documentation](https://openfeature.dev/docs/reference/sdks/server/python/)
- [OpenFeature Specification — Hooks](https://openfeature.dev/specification/sections/hooks/)
- [OpenFeature Specification — Evaluation Context](https://openfeature.dev/docs/reference/concepts/evaluation-context/)
- [OpenFeature Specification — Appendix A (Included Utilities)](https://openfeature.dev/specification/appendix-a-included-utilities/)
- [OpenFeature Providers Concept](https://openfeature.dev/docs/reference/concepts/provider/)
- [openfeature-provider-flagd (PyPI)](https://pypi.org/project/openfeature-provider-flagd/)
- [LaunchDarkly OpenFeature Python Provider (GitHub)](https://github.com/launchdarkly/openfeature-python-server)
- [openfeature-provider-posthog (PyPI)](https://pypi.org/project/openfeature-provider-posthog/0.1.26/)
- [DevCycle: Top 10 OpenFeature Providers](https://blog.devcycle.com/comparing-top-openfeature-providers/)
- [TGGL: What is OpenFeature and Does It Avoid Vendor Lock-In?](https://tggl.io/blog/what-is-openfeature-and-does-it-really-avoid-vendor-lock-in)
- [ConfigCat: OpenFeature Without Vendor Lock-In](https://configcat.com/blog/feature-flags-without-vendor-lock-in/)
