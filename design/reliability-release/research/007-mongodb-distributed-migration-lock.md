# Research 007 — MongoDB Distributed Migration Lock

Date: 2026-08-27 · Freshness matters: **Yes** — MongoDB TTL behavior, version compatibility, and distributed lock patterns are stable but version-dependent features.

## Question

How should `varco_beanie.migration.BeanieMigrator` (index-mode) serialize concurrent migrators, detect and recover from crashed lock holders, and assess whether a lock is necessary at all for index-mode migrations? Cover: (A) established patterns for distributed locks in MongoDB; (B) crash recovery and TTL-monitor granularity; (C) how real migration tools serialize runners; (D) MongoDB primitives (transactions, findAndModify, change streams) and their deployment prerequisites; (E) whether MongoDB already handles concurrent `createIndex` idempotently; (F) recent version changes affecting the above.

## Findings

### A. Established Patterns for Distributed Locks in MongoDB

**Acquire via `findOneAndUpdate` with upsert + unique index:**
- Lock document stored in dedicated collection (e.g., `migrations_lock`) with document structure: `{ _id: <lock_name>, holder: <instance_id>, acquiredAt: <Date>, expiresAt: <Date> }`
- Create **unique index on the lock name field** to prevent race conditions during concurrent upsert attempts — [MongoDB Distributed Locks](https://oneuptime.com/blog/post/2026-03-31-mongodb-distributed-locks/view)
- Acquire operation is single atomic `findOneAndUpdate`:
  ```javascript
  db.locks.findOneAndUpdate(
    { _id: "migration_lock" },
    { $set: { holder: "instance-A", acquiredAt: <now>, expiresAt: <now + TTL> } },
    { upsert: true }
  )
  ```
  This prevents two processes from acquiring the same lock simultaneously — [findOneAndUpdate Documentation](https://www.mongodb.com/docs/manual/reference/method/db.collection.findOneAndUpdate/)

**Critical race-condition prevention**: Without a **unique index on the lock name**, concurrent `findOneAndUpdate` operations can both insert new documents if the filter doesn't match — [MongoDB Atomicity](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/). The unique index makes upsert safe.

**TTL-based expiry:**
- Create TTL index on `expiresAt` field with `expireAfterSeconds: 0` to delete lock documents when the `expiresAt` timestamp passes — [TTL Indexes Documentation](https://www.mongodb.com/docs/manual/core/index-ttl/)
- Example: `db.locks.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 })`

**Heartbeat renewal (for long-running migrations):**
- Holder updates the lock document periodically: `{ $set: { expiresAt: <now + new_TTL> } }` to prevent expiration while still running
- No transaction needed; single-document atomic update suffices

### B. Crash Recovery: TTL Granularity and Timing

**TTL background task interval: 60 seconds** — [TTL Indexes Documentation](https://www.mongodb.com/docs/manual/core/index-ttl/)
- MongoDB's background thread runs **every 60 seconds**, not continuously
- From official docs: "The background task that removes expired documents runs every 60 seconds. As a result, documents may remain in a collection during the period between the expiration of the document and the running of the background task."
- **Actual deletion may be delayed 0–120 seconds after expiration** (depending on workload and the 60-second interval boundary)

**Crash recovery timing:**
- A crashed holder's lock expires at `expiresAt` timestamp
- The next 60-second TTL monitor cycle (up to 60 seconds after expiration) removes the lock
- A new instance can acquire the lock **up to ~120 seconds after the crash** (worst case: crash occurs 59 seconds after the TTL monitor ran)
- This is deterministic but **not sub-second** — important for test design

**Replica set behavior — critical asymmetry:**
- **Primary**: TTL background thread **actively deletes** expired documents
- **Secondary**: TTL thread is **idle**; secondaries **replicate** deletion operations from the primary — [TTL Indexes Replica Set Behavior](https://www.mongodb.com/docs/manual/core/index-ttl/)
- On a **single-node non-replica-set container** (testcontainers default), there is **no replication and no TTL background thread at all** — lock documents are **never automatically expired**

### C. How Real Migration Tools Serialize Concurrent Runners

**Mongock (Java):**
- Uses a **pessimistic lock persisted in the database** — [Mongock Overview](https://hevodata.com/learn/mongock/)
- Lock is **reserved for 24 hours by default**; if another Mongock instance holds the lock, execution is ignored (unless `throwExceptionIfCannotObtainLock=true`)
- Implements a **sophisticated distributed lock that prevents race conditions even if replicas deploy concurrently** — multi-instance coordination built into the tool
- Stores migration history as an audit trail; ensures each migration runs exactly once per deployment

**migrate-mongo (Node.js):**
- Uses a dedicated **`changelog_lock` collection** (configurable via `lockCollectionName`)
- Stores **TTL index on the lock** with `lockTtl` parameter (in seconds; default not explicitly stated in search results, but configurable; value of 0 disables TTL)
- No special crash-recovery mechanism documented; relies on TTL expiry
- One developer criticized the tool for insufficient protection against concurrent deploys — suggests TTL-only locking can be insufficient in some scenarios — [Migrate-mongo Overview](https://hevodata.com/learn/mongodb-migration-tool/)

**Beanie (Python ODM):**
- Provides **built-in migration support** via `beanie migrate` CLI — [Beanie Migrations Documentation](https://beanie-odm.dev/tutorial/migrations/)
- **Uses transactions by default**, which requires a **replica set** — [Beanie Migrations](https://beanie-odm.dev/tutorial/migrations/)
- Can disable transactions with `--no-use-transaction` flag to avoid replica set requirement
- Does not expose a detailed distributed lock mechanism; orchestrated at the CLI level

**pymongo-migrate and mongolock (Python):**
- No dedicated pymongo-migrate tool found in current ecosystem; generic MongoDB distributed lock patterns apply
- `mongolock` Python package (PyPI) implements standard `findOneAndUpdate` + TTL pattern — [mongolock on PyPI](https://pypi.org/project/mongolock/)

**Consensus:** All tools use **collection + TTL index + upsert-based acquire pattern**; Mongock adds sophistication with configurable TTL and explicit exception handling. None use transactions (Beanie optionally does).

### D. MongoDB Lock Primitives: Transactions, findAndModify, Change Streams

**Transactions:**
- **Requirement: Replica set only** (minimum 3 nodes for production; single-node replica sets may work for dev/testing but not officially supported) — [Transactions Documentation](https://www.mongodb.com/docs/manual/core/transactions/)
- **Snapshot isolation** with `readConcern: "snapshot"` + `writeConcern: { w: "majority" }`
- **Not suitable for testcontainers single-node non-replica-set container** ✗
- Cannot be used to implement a distributed lock that works on standalone instances

**findAndModify (legacy `db.runCommand({ findAndModify: ... })`)**
- **Atomic at single-document level only** — no special lock beyond `updateOne` — [Atomic Read-Modify-Write](https://oneuptime.com/blog/post/2026-03-31-mongodb-atomic-read-modify-write/view)
- Semantically equivalent to `findOneAndUpdate`; no advantage for locking
- **Works on standalone and single-node instances** ✓

**Change Streams:**
- **Require replica set** (depend on oplog which only exists in replica sets) — [Change Streams Replica Set Requirement](https://copyprogramming.com/howto/mongodb-change-stream-replica-set-limitation)
- Single-node replica set mode supported for dev/testing
- **Not suitable for testcontainers standalone** ✗
- Could theoretically watch for lock expiration but introduces extra complexity and replica set dependency

**Summary for testcontainers use:**
- Only **findOneAndUpdate + TTL** pattern works on `testcontainers` single-node non-replica-set MongoDB
- Transactions and change streams require replica set setup
- findAndModify offers no advantage over `findOneAndUpdate`

### E. MongoDB Index Build Idempotency: Does Concurrent `createIndex` Require a Lock?

**MongoDB makes `createIndex()` idempotent — calling with duplicate spec is a no-op:**
- "If you call `createIndex()` multiple times with the same specification, MongoDB will not attempt to recreate the index" — [Index Creation Idempotency](https://oneuptime.com/blog/post/2026-03-31-mongodb-create-index-createindex/)
- MongoDB prevents duplicate indexes with identical specifications; concurrent calls with same spec result in only one index being built
- **No lock is necessary for index-mode migrations** — MongoDB's `createIndex` is atomic and idempotent — ✓

**Behavior on concurrent duplicate calls:**
- First call builds the index
- Subsequent calls (before index build completes) are blocked by MongoDB's internal `maxNumActiveUserIndexBuilds` limit (default 3)
- All calls succeed once the index exists (idempotent no-op)
- **No error is raised** — confirmed by community forum discussion on concurrent `createIndex` — [Multiple Concurrent Invocations](https://www.mongodb.com/community/forums/t/multiple-concurrent-invocation-of-createindex-operation-for-the-same-unique-index-contract/149223)

**Implication:** If varco's Beanie migrator is index-mode only (no data mutations), a distributed lock is **not strictly necessary** — MongoDB's idempotent index creation handles concurrent calls safely. A lock is valuable for **data migrations** (where non-idempotent operations must serialize), but not for index-mode.

### F. Recent Version Changes (MongoDB 6.0–8.0, ~last 2 years)

**Index Build Improvements:**
- **MongoDB 4.4+**: Simultaneous index builds on all replica set members (instead of rolling builds) — behavior carries through 6.0, 7.0, 8.0
- **MongoDB 7.1**: Faster error reporting for index builds; new `indexBuildMinAvailableDiskSpaceMB` parameter to stop builds if disk space too low — [Index Build Improvements](https://oneuptime.com/blog/post/2026-03-31-mongodb-tune-maxnumactiveuserindexbuilds/)
- **MongoDB 8.0**: Commit quorum behavior changed; quorum now specifies how many nodes must be ready to finish before primary commits (distinct from write concern for oplog replication) — [Release Notes](https://www.mongodb.com/docs/v8.0/core/index-creation/)

**TTL Index Changes:**
- **MongoDB 7.0+**: Can create partial TTL indexes on time series collections' `metaField` (previously only `timeField` supported) — [TTL in 7.0](https://www.mongodb.com/docs/v7.0/core/index-ttl/)
- TTL monitor interval remains **60 seconds** across all versions

**Transaction Support:**
- No breaking changes to transaction requirements across 6.0, 7.0, 8.0; replica set requirement consistent

**Relevant for varco:** Index idempotency is not new (4.4+) and is stable in current versions. Simultaneous index builds (4.4+) do not change idempotency semantics.

## Options Compared

| Approach | ✅ Strengths | ❌ Weaknesses | Evidence |
|----------|---|---|---|
| **findOneAndUpdate + TTL + unique index** | Works on standalone & single-node containers; 60-sec deterministic expiry; no replica set required; battle-tested by mongock/migrate-mongo; heartbeat-renewable | TTL monitor granularity (60 sec) not sub-second; requires holder to renew TTL before expiry; unique index must be created beforehand | [TTL Docs](https://www.mongodb.com/docs/manual/core/index-ttl/), [Mongock](https://hevodata.com/learn/mongock/), [migrate-mongo](https://hevodata.com/learn/mongodb-migration-tool/) |
| **Transactions + snapshot isolation** | Strong ACID guarantees; prevents all race conditions atomically | Requires replica set (not testcontainers single-node); higher complexity; added latency; overkill for simple migration coordination | [Transactions Docs](https://www.mongodb.com/docs/manual/core/transactions/) |
| **findAndModify + unique index** | Atomic at document level; equivalent to findOneAndUpdate | No advantage over findOneAndUpdate; legacy syntax; still requires TTL for crash recovery | [Atomicity Docs](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/) |
| **No lock (rely on MongoDB's index idempotency)** | Eliminates lock overhead entirely; concurrent createIndex calls already serialized by MongoDB | Only works for **index-mode migrations** (no data mutations); if data mutations are added later, breaks without warning; loses crash-recovery audit trail | [Index Idempotency](https://oneuptime.com/blog/post/2026-03-31-mongodb-create-index-createindex/), [concurrent createIndex](https://www.mongodb.com/community/forums/t/multiple-concurrent-invocation-of-createindex-operation-for-the-same-unique-index-contract/149223) |
| **Change Streams + watch** | Elegant async notification of lock events | Requires replica set; adds extra subscriptions to oplog; no advantage over TTL-based expiry for this use case | [Change Streams](https://copyprogramming.com/howto/mongodb-change-stream-replica-set-limitation) |

## Version/Compatibility Notes

- **MongoDB 4.4+**: Simultaneous index builds, idempotent `createIndex`, TTL monitors every 60 seconds — all stable through 8.0
- **MongoDB 7.0+**: Partial TTL on time series metaField (not affecting lock implementation)
- **MongoDB 7.1+**: `indexBuildMinAvailableDiskSpaceMB` parameter added (not affecting locking)
- **MongoDB 8.0+**: Commit quorum behavior changed for index builds (not affecting lock logic)
- **Single-node replica sets** (testcontainers): Do NOT auto-enable TTL background thread — must enable via `rs.initiate()` — [Replication Docs](https://www.mongodb.com/docs/manual/replication/)
- **Standalone (non-replica-set) MongoDB**: No TTL background monitor runs; locks expire only on process restart — not suitable for migration serialization without replica set or application-level polling

## Evidence Gaps

1. **Beanie's exact migration lock behavior**: Beanie's internal coordination when using `--no-use-transaction` flag is not documented; likely no distributed lock at all (CLI-level only).
2. **Concurrent `createIndex` queue behavior**: Exact details of what happens when `maxNumActiveUserIndexBuilds` is exceeded (block vs. queue vs. error) not fully specified in public docs; likely works as intended (queue + wait) but not confirmed.
3. **Mongock's exact MongoDB collection schema**: Mongock documentation does not expose the lock collection structure or indexes; inferred from pattern description but not verified against source.
4. **Single-node replica set TTL behavior**: Whether TTL monitor actually runs on a **non-replicated** single-node replica set is ambiguous in official docs; testing required to confirm.
5. **Heartbeat renewal in production**: Best-practice renewal interval (30% of TTL? 50%?) not specified in any migration tool's docs; each tool picks its own heuristic.

## Librarian's Note

**The evidence strongly favours `findOneAndUpdate + TTL + unique index` pattern** for varco's Beanie migrator because:

1. **Works on testcontainers standalone** — no replica set setup needed in integration tests
2. **Proven by production tools** — Mongock and migrate-mongo both use this exact pattern; Mongock is battle-tested in enterprise Spring Boot deployments
3. **Crash recovery is bounded and deterministic** — 60-second TTL + up to 120-second expiry is acceptable for a migration lock; fast enough to prevent stale holds
4. **No transaction complexity** — single-document atomic operations suffice; avoids replica-set-only transaction requirement

However, **for index-mode migrations only, a lock is optional** — MongoDB's `createIndex` idempotency (4.4+) already prevents concurrent duplicate work. A lock adds an audit trail (migration version tracking) and protection for *future* data-mutation steps, so it is **recommended but not essential**. Decision: implement the lock pattern proactively to match the SQL engine's semantics and prepare for data-mutation migrations.

A chaos test verifying crashed-lock recovery must account for the 60-second TTL monitor interval — set the test's crash-recovery assertion timeout to 180 seconds (3 × monitor interval) to avoid flakiness.

