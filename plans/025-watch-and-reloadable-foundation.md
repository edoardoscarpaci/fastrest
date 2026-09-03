# Plan 025 — `varco_core.watch` + `ReloadableResource[T]` (3.1 foundation)

**Prerequisites: none.** This is the first plan of the 3.1 cycle and nothing else in it can land
first — Plans 026 and 027 both import what this plan creates.

## The 3.1 cycle map

The 3.1 backlog (`BACKLOG.md:13-99`) is split across four plan files, in dependency order. Land
them in this order; each is independently executable by `/build` once its prerequisite has landed.

| Plan | Rows | Covers | Prerequisite |
|---|---|---|---|
| **025** (this file) | T1, T2 | `varco_core.watch` (`AbstractPathWatcher` + `StatPollWatcher` + `WatchfilesWatcher`), `varco_core.reload.ReloadableResource[T]` | — |
| **026** | T3, T7, T5 | `varco_core.tls` (unified `TrustStore` + `ReloadingTrustStore`), the deprecation shim for `varco_fastapi.auth.TrustStore`, one cert-glob helper for all four disagreeing call sites, SSL contexts for `JwksUrlSource`/`OidcDiscoverySource` | 025 |
| **027** | T4, T6 | httpx / aiohttp / urllib3 / requests injection adapters, `install_process_trust()`, encrypted private keys, PKCS#12 | 026 |
| **028** | P1, P2 (+P3, P4 **P2-gated**) | Lazy `varco_core/__init__.py` (PEP 562) + import budget, CodSpeed benchmark harness, then the two *measured-or-not-done* perf rows | 026 + 027 (edits the same `__init__.py`) |

## Goal

`varco_core` gains two backend-agnostic primitives with no new hard dependency:

1. **`varco_core.watch`** — an `AbstractPathWatcher` ABC with two implementations: `StatPollWatcher`
   (default, stdlib-only) and `WatchfilesWatcher` (opt-in `varco-core[watch]` extra). Both are
   correct under atomic rename and under the Kubernetes `..data` symlink swap, both debounce, and
   both expose `async start()` / `async stop()`.
2. **`varco_core.reload.ReloadableResource[T]`** — load → swap under a lock → notify subscribers,
   with **keep-last-good** on any post-startup load failure.

Nothing consumes them yet. Plan 026 is the first consumer.

## Non-goals

- **No TLS in this plan.** `varco_core.tls` does not exist until Plan 026. This plan must not
  import `ssl`, must not touch `SSLConfig`, and must not touch `varco_fastapi.auth.trust_store`.
- **No migration of the three in-repo consumers.** `PemFolderSource`, `GettextMessageCatalog` and
  `varco_casbin`'s file adapter keep their current behaviour byte-for-byte; they are named in the
  BACKLOG as *evidence the abstraction is reusable*, not as work items. Only `PemFolderSource`'s
  **glob set** is touched, and that is Plan 026 / T7.
- **No server-side cert rotation, no `sni_callback`.** Parked (`BACKLOG.md:79`); the cycle is
  outbound-only.
- **No cross-platform work.** Linux only this cycle (locked decision, `BACKLOG.md:35`). macOS
  FSEvents / Windows CryptoAPI behaviour is neither implemented nor claimed.
- **No `watchdog`, no `truststore`.** Both cut (`BACKLOG.md:34`, `:36`, `:80`).
- **N1–N5 stay out of the whole 3.1 cycle.** MCP v2, CloudEvents, AsyncAPI, NATS→DLQ and the
  `BeanieConfig` collapse (`BACKLOG.md:113`, 3.0.1 section's "3.1 — scoped, not worked this cycle"
  table) remain scoped to a later cycle. They were excluded by explicit user decision this
  session: this cycle is a coherent trust-store/hot-reload/performance unit, and N1–N5 share no
  code, no research brief and no reviewer context with it. They are **not** cancelled and **not**
  relitigated — they keep their existing rows.

---

## Design

### Phase order

Backlog default (severity, then complexity ascending). Both rows are 🔴 must / M; T1 sorts first
because T2's tests want a watcher to drive them.

```
P0  T1  🔴 M  varco_core.watch — ABC + StatPollWatcher       (stdlib only)
P1  T1b 🔴 S  WatchfilesWatcher + the `watch` optional extra
P2  T2  🔴 M  varco_core.reload.ReloadableResource[T]
P3  ——  🟡 S  docs + CHANGELOG + api-surface snapshot        (same commit as P0-P2)
```

### §D-T1-shape — one ABC for files and directories, callback-driven, not an async iterator

| ID | Choice | Consequence |
|---|---|---|
| D-T1-shape | One `AbstractPathWatcher` ABC over a **set of watch roots**; subscribers register a callback; `start()`/`stop()` own the background task | No separate `FileWatcher`/`DirWatcher` types; a "file watcher" is a watcher whose root is that file's **parent directory**, filtered to that name |

**DESIGN: one ABC, parent-directory watching, callback subscribers**

✅ A file watcher that watches the file itself is *wrong on Kubernetes* — kubelet swaps the
`..data` symlink, so the watched inode is deleted and never modified (brief 001 §1: watchers see
only `IN_DELETE_SELF` on the old symlink, not a content change). Watching the parent and filtering
by name is the only shape that survives it, so there is no reason for two ABCs.
✅ Callbacks compose with `ReloadableResource` (§D-T2) without either side owning an event loop
queue, and they let one watcher feed N resources.
✅ `start()`/`stop()` **structurally satisfy** `varco_fastapi.lifespan.AbstractLifecycle` — it is a
`runtime_checkable` Protocol (`varco_fastapi/varco_fastapi/lifespan.py:73`) checked with
`isinstance` at registration (`:178`). So a `varco_core` object registers into `VarcoLifespan`
with **zero import from `varco_core` to `varco_fastapi`**, honouring the layer rule.
❌ An `async for event in watcher.watch()` iterator API is more Pythonic and is what `watchfiles`
itself exposes. Rejected: it forces every consumer to own a task, and a shared watcher feeding two
resources then needs a fan-out layer anyway. A callback registry *is* that fan-out layer.
❌ Callbacks make error handling the watcher's problem — addressed explicitly in §D-T1-errors.

Public surface (`varco_core/varco_core/watch/`):

```
base.py     AbstractPathWatcher (ABC), WatchEvent, WatchKind, WatchTarget
poll.py     StatPollWatcher      — stdlib only, the default
wfiles.py   WatchfilesWatcher    — opt-in, `varco-core[watch]`
snapshot.py _DirSnapshot         — internal: the stat fingerprint + diff (not exported)
__init__.py
```

`WatchEvent` is `@dataclass(frozen=True)`: `path: Path`, `kind: WatchKind`
(`ADDED`/`MODIFIED`/`REMOVED`), `detected_at: float` (monotonic). `WatchTarget` is
`@dataclass(frozen=True)`: `root: Path`, `patterns: tuple[str, ...]`, `recursive: bool`.

### §D-T1-fingerprint — what "changed" means

| ID | Choice | Consequence |
|---|---|---|
| D-T1-fingerprint | Per-file fingerprint = `(st_mtime_ns, st_size, st_ino)` of the **fully resolved** path; a set-difference over the whole target gives ADDED/REMOVED/MODIFIED | Catches the K8s symlink swap (new inode, same name) and catches a same-nanosecond rewrite that changes size; does **not** catch a same-size, same-mtime, same-inode content edit |

**DESIGN: three-field stat fingerprint over bare mtime**

This generalises `PemFolderSource._has_changes()`
(`varco_core/varco_core/authority/sources/pem_folder.py:179-199`), which snapshots
`{p: p.stat().st_mtime for p in self._path.glob("*.pem")}` and compares dicts. That code is the
proof the repo already needed this; it is also exactly the code that would *miss* a K8s rotation.

✅ `st_ino` is what makes the `..data` symlink swap visible: the name is unchanged and the mtime of
the *symlink* may be unchanged, but the resolved target is a different inode (brief 001 §1).
✅ `st_size` closes the "atomic rename within the same mtime granularity" window that a
mtime-only snapshot leaves open. `st_mtime_ns` (not `st_mtime`) removes float rounding.
✅ Cheap — `os.stat` only, no reads. Runs in `asyncio.to_thread()`, the pattern already used at
`pem_folder.py:171` with the comment "run in thread to avoid blocking the loop on slow filesystems
(NFS, etc.)".
❌ A content edit that preserves all three fields is not detected. Documented as an Edge case; the
mitigation is an opt-in `digest=True` mode (`hashlib.blake2b` of each file) for paranoid callers,
off by default because it reads every file on every poll.
❌ `st_ino` is meaningless on some filesystems. Linux-only cycle — no claim is made for others.

Entries whose **name** begins with `..` are skipped when enumerating (that is kubelet's
`..data` / `..2026_09_03_...` bookkeeping); the resolved targets are stat'ed instead. Symlinks are
resolved with `Path.resolve()`; a dangling symlink is treated as REMOVED, never an exception.

### §D-T1-debounce — quiet period, applied by both implementations

| ID | Choice | Consequence |
|---|---|---|
| D-T1-debounce | A `quiet_period: float = 0.25` s. After any detected change the watcher waits for the target to be *stable* for one quiet period before notifying, and coalesces everything in that window into one notification batch | A directory rotation that rewrites six files fires **one** callback, not six; a half-written file is never handed to a consumer |

**DESIGN: debounce in the ABC's shared helper, not per implementation**

✅ Brief 001 §1 names debouncing as *required*, not optional, for K8s Secret/ConfigMap mounts and
for editor atomic-replace semantics.
✅ Putting it in a shared `_settle()` helper means `WatchfilesWatcher` inherits it — watchfiles'
own `debounce`/`step` knobs are about event batching, not about "the writer has finished".
❌ Adds up to one `quiet_period` of latency to every rotation. Certs rotate on the order of days
(brief 001's Certificate Lifecycle table: 6-day Let's Encrypt certs at the short end, SPIFFE ~1 h);
250 ms is not a consideration.

### §D-T1-poll — `StatPollWatcher` is the default, and that is not a compromise

| ID | Choice | Consequence |
|---|---|---|
| D-T1-poll | `StatPollWatcher(interval=5.0)` is the **default** implementation exported as `default_watcher()`; `watchfiles` is opt-in | Zero new dependency on the default path; correct on NFS and Docker bind mounts, where inotify does not fire at all |

**DESIGN: polling first**

✅ Locked decision (`BACKLOG.md:34`). The evidence: inotify does **not** fire on NFS-mounted
volumes (brief 001 §1, kernel-level limitation) and Kubernetes cert delivery is a symlink swap
that an inotify consumer sees as `IN_DELETE_SELF` on a path it no longer watches. Certs arrive via
exactly those two channels.
✅ A 5 s poll of a directory of ~10 certs is ~10 `stat` calls per 5 s — unmeasurable.
❌ Brief 001's Librarian's Note recommends *against* polling as a primary strategy ("slow and
racy"). That recommendation is written for editor-driven dev-server reload, where latency is the
product; it is explicitly qualified two sentences later by the Kubernetes exception. For a
certificate whose renewal window is measured in days, a 5 s detection latency is not slow, and the
"racy" objection is answered by §D-T1-debounce. **This divergence from the brief is deliberate and
is recorded here so it is not silently re-decided.**

### §D-T1-watchfiles — the opt-in extra

`varco-core[watch]` → `watchfiles>=1.2.0` (brief 001's version table: 1.2.0, Python 3.10+, Rust
`notify` backend, used by uvicorn's `--reload`). Declared exactly like `varco_fastapi`'s `mcp`
extra (`varco_fastapi/pyproject.toml:39-49`) — a `[project.optional-dependencies]` entry with a
comment stating why it is bounded.

`WatchfilesWatcher.__init__` raises a `MissingWatchDependencyError` (subclass of `ImportError`)
naming the extra, at construction time, not import time — the module is importable without
watchfiles installed so `varco_core.watch`'s `__init__` can export the name unconditionally.

⚠️ **The watchfiles implementation still re-stats.** On an event it recomputes the same
`_DirSnapshot` fingerprint and emits `WatchEvent`s from the *diff*, never from watchfiles' own
`Change` enum. This is the brief 001 §1 pitfall stated as code: the notification says "something
about this directory changed", the snapshot says *what*. It also means both implementations emit
identical event streams, which is what makes one shared contract test suite possible.

### §D-T1-errors — a watcher must never die

| Situation | Behaviour |
|---|---|
| `OSError` while stat-ing (root deleted, permission lost) | Log WARNING once per transition, keep the last snapshot, keep polling. The root reappearing produces ADDED events. |
| A subscriber callback raises | Log ERROR with the callback's `__qualname__`, continue with the remaining subscribers. One bad consumer never stops rotation for the others. |
| `stop()` called twice / before `start()` | Idempotent no-op. |
| The background task is cancelled | `asyncio.CancelledError` propagates out of the task only; `stop()` awaits it and swallows the cancellation. |

### §D-T2-shape — `ReloadableResource[T]`

| ID | Choice | Consequence |
|---|---|---|
| D-T2-shape | `ReloadableResource[T]` owns a loader, the current value, a generation counter and subscribers. It **optionally** owns a watcher; `reload()` is also callable by hand | Usable from a watcher, from a SIGHUP handler, from an admin endpoint or from a test, with no code change |

```python
class ReloadableResource[T]:
    def __init__(
        self,
        loader: Callable[[], T] | Callable[[], Awaitable[T]],
        *,
        watcher: AbstractPathWatcher | None = None,
        name: str = "",
    ) -> None: ...

    @property
    def current(self) -> T: ...          # raises ResourceNotLoadedError before start()
    @property
    def generation(self) -> int: ...     # increments only on a successful swap
    async def start(self) -> None: ...   # first load (fail-fast) + watcher.start()
    async def stop(self) -> None: ...
    async def reload(self) -> ReloadOutcome: ...
    def subscribe(self, cb: Callable[[T], None]) -> Callable[[], None]: ...  # returns unsubscribe
```

A **sync** loader is run through `asyncio.to_thread()` (same reasoning and same precedent as
`pem_folder.py:171`); an async loader is awaited directly. `ReloadOutcome` is
`@dataclass(frozen=True)`: `changed: bool`, `generation: int`, `error: Exception | None`.

**DESIGN: keep-last-good, but fail-fast on the very first load**

✅ Locked in the BACKLOG row itself: "a truncated or half-written file must never take down a live
service, and a cert folder mid-rotation is exactly that" (`BACKLOG.md:64`). Every post-startup
load failure logs ERROR, leaves `current` and `generation` untouched, and returns
`ReloadOutcome(changed=False, error=exc)`.
✅ The **first** load is different: there is no last-good to keep, and a service that starts with
no CA bundle and discovers it on the first outbound call is strictly worse than one that refuses
to start. `start()` therefore propagates. This mirrors `JwksUrlSource.refresh()`
(`varco_core/varco_core/authority/sources/jwks_url.py:178-183`), which returns the stale keyset on
failure but re-raises when `self._keyset is None` — the repo already made this exact call once.
❌ A resource that is *permanently* broken after startup serves a stale value indefinitely. The
mitigation is observability, not failure: every failed reload logs ERROR and the outcome carries
the exception, so a caller may escalate. varco does not decide to take a process down.

**Async safety**: the swap is guarded by an `asyncio.Lock` created **lazily** in a
`_get_lock()` helper on first use — never in `__init__`, never at module scope (CLAUDE.md's
lock rule). `current` is a plain attribute read of an immutable reference, so readers never take
the lock and never see a torn value.

**Subscriber notification happens outside the lock**, after the swap, so a subscriber that itself
calls `reload()` cannot deadlock. Subscriber exceptions are logged and swallowed (§D-T1-errors,
same rule).

### Alternatives considered

- **`watchdog` instead of / alongside `watchfiles`** — ❌ rejected. Brief 001 §1: older, narrower
  API, no granular event types; its only advantage is Python 3.6+ support, irrelevant to a
  `requires-python = ">=3.12"` package (`varco_core/pyproject.toml:9`). Two optional backends
  would double the contract-test matrix for zero capability.
- **Raw `inotify` via `ctypes`** — ❌ rejected. It is precisely the mechanism that does not fire on
  the two filesystems certs actually arrive on (brief 001 §1), and it is Linux-only in a way that
  would have to be undone when the cross-platform parked item is re-opened.
- **`signal.SIGHUP`-driven reload only, no watcher** — ❌ rejected as the *primary* mechanism (it
  requires an external actor, which is the thing cert-manager does not provide; brief 001 §2 has
  it as a sidecar pattern). ✅ but it composes: `ReloadableResource.reload()` is public precisely
  so an app can wire `SIGHUP` to it in three lines. Documented, not implemented.
- **Making `ReloadableResource` a `CacheBackend` / reusing the cache layer** — ❌ rejected. It has
  one key, no TTL, no eviction and a completely different failure contract; `AsyncCache`'s
  `isinstance`-checkable Protocol would be actively misleading (same reasoning as Plan 011 D-11's
  refusal to put bulk ops on `AsyncCache`).
- **Putting the watcher in `varco_fastapi` so it can implement `AbstractLifecycle` nominally** —
  ❌ rejected, violates CLAUDE.md's layer rule. `AbstractLifecycle` is a `runtime_checkable`
  Protocol (`lifespan.py:73`) so structural conformance is enough; verified by a test (Step 14).

---

## Steps

### Phase 0 — T1: the ABC and `StatPollWatcher` (🔴 must, M)

1. [x] `varco_core/varco_core/watch/base.py` (new) — `WatchKind` (`enum.Enum`), `WatchEvent`
       (`@dataclass(frozen=True)`), `WatchTarget` (`@dataclass(frozen=True)`),
       `MissingWatchDependencyError(ImportError)`, and `AbstractPathWatcher` (ABC) with
       `subscribe(cb) -> unsubscribe`, `async start()`, `async stop()`, abstract
       `async _run(self) -> None`, and the shared `_notify(events)` helper implementing
       §D-T1-errors' subscriber rule. Full module docstring with the `DESIGN:` block from
       §D-T1-shape (✅/❌ copied, not paraphrased). `from __future__ import annotations`.
2. [x] `varco_core/varco_core/watch/snapshot.py` (new) — internal `_DirSnapshot`: `take(target)`
       (skips `..`-prefixed names, resolves symlinks, fingerprints `(st_mtime_ns, st_size,
       st_ino)`, optional `digest`), and `diff(other) -> tuple[WatchEvent, ...]`. Not exported
       from `varco_core.watch.__init__`.
3. [x] `varco_core/tests/test_watch_snapshot.py` (new, **failing first**) — unit tests for
       `_DirSnapshot` alone, driven from `tmp_path`: added file, removed file, rewritten file,
       same-size rewrite with a bumped mtime, `..data`-style symlink swap (build the real kubelet
       layout: `..2026_01_01/`, `..data -> ..2026_01_01`, `ca.pem -> ..data/ca.pem`, then create
       `..2026_01_02/` and re-point `..data` atomically via `os.replace` on a temp symlink),
       dangling symlink, and non-recursive-vs-recursive enumeration.
4. [x] `varco_core/tests/watch_contract.py` (new) — a **shared, non-`Test*`-prefixed** contract
       base class `PathWatcherContract` with an abstract `watcher` fixture, covering: a single
       ADDED event; coalescing three rapid writes into one notification (§D-T1-debounce); the
       kubelet symlink swap producing exactly one MODIFIED; REMOVED on unlink; a raising
       subscriber not preventing the other subscribers; `stop()` idempotence; `start()` twice
       being a no-op. Same naming discipline as `testkit/varco_conformance` (not collected
       standalone, so an unimplemented fixture fails loudly).
5. [x] `varco_core/varco_core/watch/poll.py` (new) — `StatPollWatcher(targets, *, interval=5.0,
       quiet_period=0.25, digest=False)`. Loop: `await asyncio.to_thread(snapshot.take)` →
       diff → if changed, settle (§D-T1-debounce) → `_notify`. `asyncio.sleep(interval)` between
       polls; `OSError` handled per §D-T1-errors.
6. [x] `varco_core/tests/test_watch_poll.py` (new) — `class TestStatPollWatcher(PathWatcherContract)`
       with a fast fixture (`interval=0.02, quiet_period=0.05`), plus poll-specific tests: the
       root directory being deleted and recreated mid-run; a `digest=True` run detecting a
       same-mtime/same-size/same-inode rewrite that `digest=False` misses (this test *documents*
       the §D-T1-fingerprint limitation by asserting both halves).

### Phase 1 — T1b: `WatchfilesWatcher` + the `watch` extra (🔴 must, S)

7. [x] `varco_core/pyproject.toml` — add to `[project.optional-dependencies]`:
       `watch = ["watchfiles>=1.2.0"]`, with a comment stating (a) why it is opt-in and not a
       dependency (brief 001 §1 / locked decision `BACKLOG.md:34`) and (b) the floor's provenance
       (brief 001's version table). Then `uv lock` + `uv sync --all-packages --all-extras`.
8. [x] `varco_core/varco_core/watch/wfiles.py` (new) — `WatchfilesWatcher`, function-body import
       of `watchfiles` inside `__init__` (`# noqa: PLC0415`, precedent
       `varco_fastapi/varco_fastapi/connection.py:333`) raising `MissingWatchDependencyError`
       with the exact `pip install "varco-core[watch]"` string. `_run()` consumes
       `awatch(*roots, stop_event=...)` but **derives events from `_DirSnapshot.diff`**, never
       from watchfiles' `Change` (§D-T1-watchfiles).
9. [x] `varco_core/tests/test_watch_watchfiles.py` (new) — `class
       TestWatchfilesWatcher(PathWatcherContract)` guarded by
       `pytest.importorskip("watchfiles")`, plus a test asserting the construction-time
       `MissingWatchDependencyError` message when the import is monkeypatched away. CI installs
       `--all-extras` (`.github/workflows/test.yml:52,83`), so this suite runs on every CI run.
10. [x] `varco_core/varco_core/watch/__init__.py` (new) — export `AbstractPathWatcher`,
        `StatPollWatcher`, `WatchfilesWatcher`, `WatchEvent`, `WatchKind`, `WatchTarget`,
        `MissingWatchDependencyError`, and `default_watcher(targets, **kw)` (returns a
        `StatPollWatcher`; documented as the stable way to get "the default"). Module `__all__`.

### Phase 2 — T2: `ReloadableResource[T]` (🔴 must, M)

11. [x] `varco_core/tests/test_reloadable_resource.py` (new, **failing first**) — `current`
        before `start()` raises `ResourceNotLoadedError`; first-load failure propagates out of
        `start()`; a post-startup loader failure keeps `current` and `generation` and returns
        `ReloadOutcome(changed=False, error=...)`; a successful reload bumps `generation` exactly
        once and notifies every subscriber; a raising subscriber does not prevent the others; an
        unchanged reload (loader returns an equal value) still counts as a swap (see Edge cases);
        `subscribe()`'s returned callable unsubscribes; a subscriber calling `reload()` re-entrantly
        does not deadlock; sync and async loaders both work.
12. [x] `varco_core/varco_core/reload.py` (new) — `ReloadableResource[T]` per §D-T2-shape,
        `ReloadOutcome`, `ResourceNotLoadedError(RuntimeError)`. Lazy `asyncio.Lock` via
        `_get_lock()`. Docstrings carry Args/Returns/Raises/Edge cases/**Async safety** (CLAUDE.md
        docstring rule), and the `DESIGN:` block from §D-T2-shape.
13. [x] `varco_core/tests/test_reloadable_resource_watch.py` (new) — the integration of the two:
        a `ReloadableResource` whose loader reads a `tmp_path` file, wired to a fast
        `StatPollWatcher`; assert the value updates within a bounded wait after a kubelet-style
        symlink swap, and that a mid-write truncated file leaves the last-good value in place.
        (If this becomes timing-flaky, **increase the sleep margin** — never xfail it. CLAUDE.md
        Test Conventions.)
14. [x] `varco_core/tests/test_watch_lifecycle_protocol.py` (new) — assert
        `isinstance(StatPollWatcher(...), varco_fastapi.lifespan.AbstractLifecycle)` and the same
        for `ReloadableResource`, proving §D-T1-shape's structural claim against
        `varco_fastapi/varco_fastapi/lifespan.py:73,178`. `varco-fastapi` is already a dev-only
        dependency of `varco_core` (`varco_core/pyproject.toml:73-83`), so this needs no new dep;
        the test module must state that in a comment so nobody "fixes" it into a runtime import.

### Phase 3 — docs, snapshot, changelog (🟡 should, S — same commit as Phases 0-2)

15. [x] `varco_core/varco_core/__init__.py` — **do not** re-export the watch/reload names at the
        top level. They are imported as `from varco_core.watch import StatPollWatcher`. Reason:
        Plan 028 / P1 is about *shrinking* this file's eager import graph, and `varco_core.watch`
        is explicitly meant to be usable from a sidecar or CLI without paying for the framework.
        Record this as a one-line comment where the other subsystem import blocks live.
16. [x] `uv run python scripts/api_surface.py` — regenerate
        `design/api-freeze-and-standards/measurements/api-surface.{json,md}`. Step 15 means
        `varco_core.__all__` is unchanged, so this should be a no-op; run it anyway and commit any
        delta, because `--check` is a CI gate (`.github/workflows/test.yml:64-65`).
17. [x] `README.md` — a new "File watching and hot reload" section under the existing subsystem
        sections: `StatPollWatcher` usage, the `watch` extra, `ReloadableResource` with the
        keep-last-good contract, the SIGHUP composition snippet, and the `VarcoLifespan.register()`
        wiring line.
18. [x] `ARCHITECTURE.md` — add `varco_core.watch` and `varco_core.reload` to the `varco_core`
        per-module listing and a small type hierarchy for `AbstractPathWatcher`.
19. [x] `CLAUDE.md` — one short subsection under Key Abstractions: **Rule** — a watcher's
        fingerprint is `(st_mtime_ns, st_size, st_ino)` of the **resolved** path and enumeration
        skips `..`-prefixed names, because Kubernetes delivers certs as a `..data` symlink swap;
        never "fix" a watcher to stat the symlink itself. Plus a Decision-Tree row: *File/dir
        change detection? → `varco_core.watch`, never a hand-rolled mtime dict.*
20. [x] `technical_docs/common-pitfalls.md` — one row: hand-rolled mtime-dict directory watching
        misses the K8s `..data` rotation (anchor: `pem_folder.py:179-199` as the historical
        example) → use `varco_core.watch`.
21. [x] `testkit/varco_conformance/COVERAGE.md` — add a short note (not a suite): `AbstractPathWatcher`
        is a **new ABC that is not one of the five**; its two implementations both live in
        `varco_core`, so its shared contract base is `varco_core/tests/watch_contract.py` rather
        than a `testkit` module (testkit exists to reach *across* packages). If a future backend
        ships a third implementation, promote it. This pre-empts the "why is there no suite for
        this?" audit question that COVERAGE.md exists to answer.
22. [x] `CHANGELOG.md` `## [Unreleased]` — `### Added` entries for `varco_core.watch` and
        `ReloadableResource`, each referencing "Plan 025 / T1" and "Plan 025 / T2", plus the new
        `varco-core[watch]` extra under the same heading.
23. [x] `BACKLOG.md` — mark T1/T2 as in-flight against Plan 025 (do not delete the rows).

---

## Edge cases

- **Watch root does not exist at `start()`** → `StatPollWatcher.start()` succeeds with an empty
  snapshot and logs one WARNING; the directory appearing later produces ADDED events. Rationale: a
  cert volume can be mounted after the process starts. A *`ReloadableResource`* over that root
  still fails fast (§D-T2), which is the correct division of responsibility.
- **Root is a file, not a directory** → `WatchTarget` normalises: watch `path.parent`, filter to
  `path.name`. Documented in `WatchTarget`'s docstring.
- **A file is replaced by a directory of the same name** → REMOVED then ADDED in the same batch.
- **Clock moves backwards / mtime in the future** → irrelevant: the fingerprint is compared for
  *inequality*, never ordering.
- **Two `ReloadableResource`s share one watcher** → both are notified; each reloads independently;
  one failing does not affect the other.
- **Loader returns an equal-but-not-identical value** → still a swap, `generation` still
  increments. `ReloadableResource` does not require `T` to be comparable, and equality on an
  `ssl.SSLContext` (Plan 026's `T`) is identity anyway. `ReloadOutcome.changed` means *"a reload
  ran and succeeded"*, not *"the bytes differ"* — stated in the docstring so 026 does not assume
  otherwise.
- **`stop()` while a reload is in flight** → `stop()` acquires the same lock, so it waits for the
  in-flight swap to finish; the watcher task is cancelled first so no new reload starts.
- **watchfiles installed but the platform is unsupported** → `WatchfilesWatcher.__init__` lets the
  underlying error propagate unchanged; only `ImportError` is translated.

## Verification

```bash
uv sync --all-packages --all-extras
uv run pytest varco_core/tests/test_watch_snapshot.py \
              varco_core/tests/test_watch_poll.py \
              varco_core/tests/test_watch_watchfiles.py \
              varco_core/tests/test_reloadable_resource.py \
              varco_core/tests/test_reloadable_resource_watch.py \
              varco_core/tests/test_watch_lifecycle_protocol.py -q
uv run pytest varco_core/tests/          # no regressions in the package
make lint                                # ruff check + ruff format --check + api_surface --check
make type-check                          # mypy strict over the ten source dirs
make test                                # all eleven suites
```

**DoD:** both implementations pass the identical `PathWatcherContract`; `make lint` green
(including `api_surface.py --check`); CHANGELOG/README/ARCHITECTURE/CLAUDE.md updated in the same
commit as the code (CLAUDE.md's same-commit docs rule).

## Risks

- **⚠️ ASSUMPTION — the kubelet layout reproduced in Step 3.** Brief 001 §1 documents the `..data`
  symlink-swap mechanism and that watchers see `IN_DELETE_SELF`, but the brief does not give the
  exact directory names/permissions kubelet writes. The test builds a *representative* layout
  (`..YYYY_MM_DD_HH_MM_SS.NNNNNNN/`, `..data`, per-key symlinks). If a real cluster ever shows a
  different shape, the fingerprint rule (resolve, then stat) is what matters and is shape-agnostic
  — but the test's fidelity is an assumption, not a citation.
- **⚠️ ASSUMPTION — `st_ino` stability across the swap.** The claim "the resolved target is a
  different inode after a kubelet rotation" follows from kubelet writing a *new timestamped
  directory*, but is not directly cited in brief 001. Invariant that must hold: **the watcher
  detects the rotation.** `st_size` and `st_mtime_ns` are the redundant belt-and-braces if `st_ino`
  ever coincides; Step 3's test asserts detection, not the mechanism.
- **Timing flakiness in Steps 6/13.** The contract suite waits on background tasks. Mitigation is
  the repo's stated rule: widen the sleep margin, never xfail. Every wait is a bounded poll loop
  (`until` helper with a deadline), never a bare `asyncio.sleep`.
- **`watchfiles` is a Rust extension module.** It ships wheels for every platform in this repo's CI
  matrix (brief 001 §1), but it is now in `--all-extras`, so a wheel-less platform would break
  `uv sync` for contributors, not just CI. Invariant: it must stay in
  `[project.optional-dependencies]` and never migrate into `dependencies`.
- **Scope creep into Plan 026.** The strongest risk here is "while I'm at it" migrating
  `PemFolderSource` to the new watcher. It must not happen in this plan: `PemFolderSource` has a
  pull-driven refresh contract that `TrustedIssuerRegistry` depends on, and changing it to
  push-driven is a behaviour change with its own test surface. Not scoped in 3.1 at all.
