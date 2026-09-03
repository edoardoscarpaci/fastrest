# `@PreDestroy` is silently ignored for `@Provider`-produced instances

**ID**: P22-PROVIDER-PREDESTROY
**Raised by**: varco, Plan 022 Phase 4 (RL-8a — `container.ashutdown()` adoption)
**Date**: 2026-08-31
**providify version**: 2.0.0 (as resolved in `uv.lock`)
**Status**: ✅ Resolved (2026-09-02, Plan 024 / C2) — see §8
**Priority**: 🟡 should. Not a crash; a silent resource leak on a path a
framework is likely to take. Two varco caches leak a live connection pool
because of it.

> ⚠️ **Read §5 before treating this as an upstream-only issue.** varco has a
> supported same-day fix available and is not using it. Whether providify
> changes anything is a separate question from whether varco should ship the
> leak.

---

## 1. What providify does today

`DIContainer._adispose()` (`providify/container.py:4550-4582`) dispatches on
binding **kind**, and returns early for a `ProviderBinding`:

```python
async def _adispose(self, key: Any, binding: AnyBinding) -> None:
    if isinstance(binding, ProviderBinding):
        if binding.disposer is not None:
            instance = self._singleton_cache[key]
            ...
            binding.disposer(instance)
        return                                    # ← returns unconditionally
    if not isinstance(binding, ClassBinding) or binding.pre_destroy is None:
        return
    instance = self._singleton_cache[key]
    bound = getattr(instance, binding.pre_destroy.fn_name)
    ...
```

So `binding.pre_destroy` is consulted **only** for a `ClassBinding`. If an
instance reaches the container as the return value of a `@Provider`, its class's
`@PreDestroy` hook is never looked up — whether or not a `@Disposes` exists.
`_dispose_sync()` is the sync mirror and has the same shape.

## 2. Why this is a gap

Three things, in descending order of how much they matter.

**(a) It contradicts `@PreDestroy`'s own documented contract.** The decorator's
docstring (`providify/decorator/lifecycle.py:161-170`) reads, in full:

> Marks a method to be called on shutdown or scope teardown.

There is no binding-kind caveat. A reader who decorates a method with
`@PreDestroy` has been told it runs at shutdown; for a provider-produced
instance it does not.

**(b) It is silent in every direction.** No exception, no warning, no log line.
And critically, **no validation covers it**: `IssueKind`
(`providify/validation.py:66-90`) has eight members — `MISSING_BINDING`,
`MISSING_BINDING_DEFAULTED`, `MISSING_BINDING_DEFERRED`, `AMBIGUOUS_BINDING`,
`CIRCULAR_DEPENDENCY`, `SCOPE_LEAK`, `LIVE_REQUIRED`, `UNRESOLVED_ANNOTATION` —
and none expresses "this class carries a `@PreDestroy` that is unreachable given
how it is bound." `container.validate()` reports a clean bill of health for the
reproduction in §3. The only way to discover the gap is to notice the resource
still open after shutdown.

**(c) The two mechanisms are not discoverable from each other.** `@Disposes`
(`decorator/lifecycle.py:235-258`) is the designed teardown path for
provider-produced instances and its docstring is clear about what it does. But
nothing at the `@PreDestroy` end points at it, and the failure mode of picking
the wrong one is a silent no-op rather than an error. The library knows both
facts at install time — that this class has a `@PreDestroy`, and that this
binding is a `ProviderBinding` with no disposer — and says nothing.

## 3. Minimal reproduction

Runnable version, kept green in CI as a strict xfail:
`varco_core/tests/test_providify_provider_predestroy.py`. It pairs the case
below with a `ClassBinding` **control** that passes, so the binding kind is
isolated as the only variable.

```python
class Resource:
    def __init__(self) -> None:
        self.closed = False

    @PreDestroy
    async def stop(self) -> None:
        self.closed = True


@Configuration
class Module:
    @Provider(singleton=True)
    async def resource(self) -> Resource:
        return Resource()


container = DIContainer()
await container.ainstall(Module)
resource = await container.aget(Resource)

await container.ashutdown()

assert resource.closed          # ❌ fails — hook never ran
```

Swap the `@Configuration`/`@Provider` for `container.register(Singleton(Resource))`
and the identical assertion passes.

## 4. The ask

Either of these closes it; **(ii) is the smaller, safer change** and would be
enough for varco.

1. **Fall back to `@PreDestroy` for a `ProviderBinding` with no `@Disposes`.**
   In `_adispose`/`_dispose_sync`, when `binding.disposer is None`, look up the
   instance's class `@PreDestroy` the way the `ClassBinding` branch does.
   - ✅ Makes the documented `@PreDestroy` contract true unconditionally, which
     is the least surprising behaviour.
   - ✅ Purely additive: no existing `@Disposes` changes, and a provider-produced
     instance whose class has no `@PreDestroy` is unaffected.
   - ❌ Changes teardown behaviour for anyone who *relied* on the current
     asymmetry — hard to imagine deliberately, but it is a behaviour change and
     belongs in a minor bump, not a patch.

2. **Add a validation issue kind** — e.g. `UNREACHABLE_PRE_DESTROY` — raised when
   a `ProviderBinding` has no disposer and the produced type carries a
   `@PreDestroy`.
   - ✅ Strictly additive; no runtime behaviour changes at all.
   - ✅ Fits the existing `container.validate()` surface, which varco already
     calls at every package's DI-health test via
     `assert_no_structural_di_issues()` — so varco would have caught this
     itself, at the point the wiring was written.
   - ❌ Leaves the docstring in (a) still overstating the contract; it wants a
     matching sentence naming `@Disposes`.

3. **Documentation only** — amend `@PreDestroy`'s docstring to state that it
   applies to class bindings and to point at `@Disposes` for provider bindings.
   - ✅ Zero risk, and correct regardless of whether 1 or 2 lands.
   - ❌ Does nothing for the silence. Every future caller still finds out by
     leaking a connection.

## 5. ⚠️ This is not purely a providify problem — varco's side

**varco can fix its own leak today, without any upstream change**, by adding a
`@Disposes` to the two configurations that produce these caches. That is the
supported mechanism, it exists, and varco simply isn't using it:

```python
# varco_redis/varco_redis/cache.py — RedisCacheConfiguration
@Disposes(CacheBackend)
async def close_cache(self, cache: CacheBackend) -> None:
    await cache.stop()
```

So the honest framing is two separate defects that happen to meet here:

| | Owner | Fixable now? |
|---|---|---|
| `@PreDestroy` silently unreachable for provider bindings, with no validation | providify | Needs upstream |
| `RedisCache` / `MemcachedCache` leak a started pool at shutdown | **varco** | **Yes — add `@Disposes`** |

The varco-side fix was deliberately **not** applied in Plan 022 because it
changes a package's DI shape and Plan 022's Phase 3 was gated on an explicit
break-verdict checkpoint; freelancing a DI change past that gate would have been
a scope violation. It wants its own plan, or an explicit decision to fold it into
the current one — but "waiting for providify" is not the only option, and should
not be the reason a release ships with two leaked connection pools.

## 6. Impact on varco, measured

From `design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md`:
six of ten `@PreDestroy`-bearing singletons were orphans that no lifespan path
tore down. Plan 022's RL-8a adoption (`VarcoLifespan(shutdown=...)` →
`container.ashutdown()`) fixes **four**. The two it cannot fix are exactly the
two bound through a `@Provider`:

| Orphan | Binding | Torn down after RL-8a? |
|---|---|---|
| `KafkaChannelManager`, `NatsStreamManager`, `RedisChannelManager`, `CasbinPolicyEngine` | `@Singleton` → `ClassBinding` | ✅ yes |
| `RedisCache` (`varco_redis/cache.py:131`) | `RedisCacheConfiguration.redis_cache()` `@Provider` | ❌ **no** |
| `MemcachedCache` | `MemcachedCacheConfiguration` `@Provider` | ❌ **no** |

Both of the unfixed ones are the *worst* cases in the measurement: their
providers `await cache.start()`, so the container hands back an
already-connected pool. `varco_memcached.di.async_bootstrap()` defaults
`setup_cache=True`, making the leak the **default** path for that package.

## 7. Guard tests

Both are `strict=True`, so they fail loudly the day the gap closes and the fix
cannot land untested:

- `varco_core/tests/test_providify_provider_predestroy.py` — no Docker, no varco
  types, runs in `make test` on every commit. The primary early-warning signal,
  plus a passing `ClassBinding` control.
- `varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py` — real
  Redis container, `@pytest.mark.integration`, asserts the actual pool is closed.
  Nightly only, and it proves the *effect* rather than the mechanism.

## 8. Resolution (2026-09-02)

providify 2.0.1 shipped 2026-09-01 (`providify/CHANGELOG.md:12-32`) and
**declares the behaviour intentional** — the Jakarta CDI producer-method rule
this report's §5 already suspected: a producer method's output is torn down
only by an explicit disposer, never by a lifecycle hook declared on the
produced class. 2.0.1 adds `IssueKind.UNREACHABLE_PRE_DESTROY` (a `WARNING`
severity, so it never reaches `container.validate()`'s `report.errors`) to
*detect* this shape, plus docstring corrections
(`providify/README.md:945-949`, `SKILL.md:287`, `PROVIDERS.md:133-138`)
stating `@Disposes` is the *only* teardown path for provider-produced
instances. The "Unreleased" section carries no further lifecycle work
planned.

**varco adopted `@Disposes` — exactly this report's §5 proposal.** Not a
workaround: it is upstream's own supported mechanism for this shape, just
never exercised in varco before this plan. The full-repo audit that Plan 024
mandated found **nine** live sites of the identical defect class, not the
two this report named — three visible to the new `UNREACHABLE_PRE_DESTROY`
detector (`RedisCache`, `RedisEventBus`/`RedisStreamEventBus`,
`MemcachedCache`) and six invisible to it (`LayeredCache`, `RedisDLQ`,
`RedisStreamDLQ`, `RedisBulkhead`, `KafkaDLQ`, `NatsDLQ` — none of these
carry a `@PreDestroy` at all, so the detector has nothing to flag). All nine
now carry a `@Disposes` method on their producing `@Configuration`. See
`plans/024-3-0-1-cleanup.md` §D-C2 / §D-C2-audit for the full site table and
`UPSTREAM-GAPS.md`'s "Recently closed" section for the ledger entry.

Both guard tests in §7 above are no longer `strict=True` xfails — they now
assert the passing, adopted-fix contract directly (see each file's own
docstring for the rewritten characterization).

⚠️ **A second, distinct defect surfaced while proving the fix**: providify's
`@Disposes` wiring loop attaches a disposer to the *first* matching binding
across the whole container, not the installing module's own — filed
separately as
[providify-disposes-first-match.md](providify-disposes-first-match.md)
(P24-DISPOSES-FIRSTMATCH). It does not block this resolution; it affects
only the (currently unused-in-production) case of installing both
`RedisCacheConfiguration` and `RedisLayeredCacheConfiguration` into the same
container.

---

**Filing note.** This file is the durable report; it is indexed by a one-row
pointer in [`UPSTREAM-GAPS.md`](../../UPSTREAM-GAPS.md) and cross-referenced from
BACKLOG.md's "Findings from Plan 022 (Phase 4 / RL-8a)" section.

⚠️ **If `UPSTREAM-GAPS.md` is absent, that is expected** — the ledger is cleared
periodically to drop resolved rows, and holds no content of its own. This report
survives every such clearing, which is the point of the split. Recreate the
ledger from the template in its own header and re-add the row; the full index is
rebuildable with `ls design/upstream-gaps/*.md`. (The ledger's previous
incarnation inlined every entry's body and was deleted in `cae7f33`, taking all
of them with it — this structure exists so that cannot recur.)
