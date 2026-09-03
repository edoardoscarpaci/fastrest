# `@Disposes` wiring attaches to the first matching binding, not the caller's own

**ID**: P24-DISPOSES-FIRSTMATCH
**Raised by**: varco, Plan 024 Phase 1 (C2 — `@Disposes` adoption), Step 16
**Date**: 2026-09-02
**providify version**: 2.0.1 (as resolved in `uv.lock`)
**Status**: ⛔ Open — filed, not worked around
**Priority**: 🟡 should. Not a crash; a silent resource leak when two
`@Configuration`s in the same container both bind the same interface via
`@Provider`, each with its own `@Disposes` for that interface.

---

## 1. What providify does today

`DIContainer.install()`'s disposer-wiring loop (`providify/container.py:6201-6214`):

```python
# Wire @Disposes teardown methods to their ProviderBinding
for name, fn in vars(module_cls).items():
    if not callable(fn):
        continue
    disposes_marker = _get_disposes_marker(fn)
    if disposes_marker is None:
        continue
    disposed_type = disposes_marker.disposed_type
    for binding in self._bindings:
        if isinstance(binding, ProviderBinding) and _interface_matches(
            binding.interface, disposed_type
        ):
            binding.disposer = getattr(instance, name)
            break
```

For every `@Disposes(X)` method found on the module being installed, this walks
**all** `self._bindings` (every binding in the *entire container*, not just the
ones this `install()` call is registering) and attaches the disposer to the
**first** `ProviderBinding` whose interface matches `X`, then `break`s.

## 2. Why this is a gap

When two independent `@Configuration`s both produce the same interface via
`@Provider` and each carries its own `@Disposes` for that interface — a real
and reasonable shape (varco has exactly this: `RedisCacheConfiguration` and
`RedisLayeredCacheConfiguration` both bind `CacheBackend`) — installing the
second configuration's disposer-wiring loop finds the **first** matching
`CacheBackend` binding in the container (which is the *first* configuration's
own binding, already correctly wired to its own disposer) and **overwrites**
it with the second configuration's disposer. The second configuration's own
binding — the one the loop was ostensibly wiring for — is left with
`disposer = None` and leaks.

Concretely, with `RedisCacheConfiguration` installed first and
`RedisLayeredCacheConfiguration` second:

1. `install(RedisCacheConfiguration)` registers binding **A** (`CacheBackend`
   → `RedisCache`), then wires `A.disposer = RedisCacheConfiguration.close_cache`
   (correct — the only `CacheBackend` binding so far is A itself).
2. `install(RedisLayeredCacheConfiguration)` registers binding **B**
   (`CacheBackend` → `LayeredCache`), then wires its own `close_cache`: the
   loop scans `self._bindings`, finds **A** first (not B), and sets
   `A.disposer = RedisLayeredCacheConfiguration.close_cache`. **B never
   receives a disposer.**

At `ashutdown()`, `A`'s disposer — now `RedisLayeredCacheConfiguration.close_cache`
— is invoked with **A's own produced instance** (`binding.disposer(instance)`,
`providify/container.py:4541-4546`, where `instance` is looked up from the
binding's own singleton cache entry). Since `close_cache(self, cache)` just
does `await cache.stop()` on whatever `cache` it is handed, this *happens* to
call `RedisCache.stop()` on binding A's redis instance — so A is torn down,
coincidentally, by the wrong method. But **B (the `LayeredCache` instance) is
never stopped at all** — its L1 and L2 backends leak silently.

Reproduced in `varco_redis/tests/test_redis_cache_disposes.py::test_both_cache_configurations_installed_together_both_get_stopped`:
only `"redis"` appears in the observed stop-order list; `"layered"` never
does.

## 3. What the report §D-C2-firstmatch predicted vs. what happened

The design note that anticipated this edge case (`plans/024-3-0-1-cleanup.md`
§D-C2-firstmatch) predicted the outcome would be "benign" because both
disposers happen to call `await backend.stop()` on *a* `CacheBackend`. That
prediction is **wrong**: the disposer that gets attached to the winning
binding calls `stop()` on that binding's own instance (not the other
configuration's), so the winning binding *is* torn down correctly — but the
**losing binding's disposer slot is simply never populated**, leaving its
instance with no teardown path whatsoever. This is a genuine leak, not a
redundant-but-harmless double-stop.

## 4. The ask

- ✅ **Preferred**: scope the "first matching binding" search
  (`providify/container.py:6207`) to bindings registered **by this
  `install()` call** (i.e., only bindings whose owning module is
  `module_cls`), not every binding in the container. This is a minimal,
  surgical change — the loop already has `self` and `module_cls` in scope.
- ✅ **Alternative**: raise (or add a new `IssueKind`, `AMBIGUOUS_DISPOSER`) when
  a `@Disposes(X)` disposer-wiring pass would overwrite an *already-set*
  `disposer` on a binding that belongs to a **different** module — surfacing
  the collision instead of silently mis-wiring it.
- ❌ **Not requested**: any change to `@PreDestroy`/producer-method semantics —
  that gap (P22-PROVIDER-PREDESTROY) is settled as intentional and closed
  separately; this is a distinct wiring-loop defect in `@Disposes` itself.

## 5. Interim workaround — none applied in varco

There is no supported, non-hacky way for application code to control which
binding a `@Disposes` attaches to — the loop is purely internal to
`install()`. varco does **not** work around this (per this repo's norm): the
affected test is marked `xfail(strict=True)` so the fix cannot land
unnoticed, and both `RedisCacheConfiguration` and
`RedisLayeredCacheConfiguration` continue to ship their own `@Disposes`
methods (correct in isolation — the bug only manifests when **both**
configurations are installed into the **same** container, which is an
unusual, not-default composition). A future consumer that installs both
should be aware `LayeredCache`'s pool may leak on `ashutdown()` until this is
fixed upstream.

## 6. Guard

`varco_redis/tests/test_redis_cache_disposes.py::test_both_cache_configurations_installed_together_both_get_stopped`
— `xfail(strict=True, reason="BUG: providify @Disposes wiring attaches to the
first matching binding across the whole container, not the installing
module's own — see design/upstream-gaps/providify-disposes-first-match.md")`.
No Docker required (monkeypatched I/O), runs in every `make test`.
