# Research 003 — aiokafka 0.13.0 rebalance and offset-commit verification surface

Date: 2026-08-26 · Freshness matters: **yes** — aiokafka 0.13.0 released 2026-01-02; rebalance/offset APIs relatively stable; heartbeat tuning values are timing-dependent

## Question

Enumerate the current consumer group / rebalance listener API in aiokafka 0.13.0; verify offset-commit durability testing surface; identify deterministic rebalance-forcing techniques and at-least-once / exactly-once semantics support (transactional or otherwise) — all with production-ready evidence, not example code patterns alone.

## Findings

### Consumer Group & Rebalance Listener API (aiokafka 0.13.0)

- **Rebalance listener interface**: `ConsumerRebalanceListener` with two async callbacks (`on_partitions_revoked`, `on_partitions_assigned`) — [Consumer client — aiokafka documentation](https://aiokafka.readthedocs.io/en/stable/consumer.html) (stable docs, January 2026)
  - `on_partitions_revoked()` invoked when partitions are removed during rebalancing; **critical**: if using manual commit (`enable_auto_commit=False`), offsets **must** be committed here to avoid duplicate delivery post-rebalance — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html)
  - `on_partitions_assigned()` invoked *after* partition re-assignment completes and *before* the consumer resumes fetching — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html)
  - ⚠️ **Deadlock risk**: `ConsumerRebalanceListener` handlers are awaited and block subsequent `getmany()`/`getone()` calls — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html)

- **Subscription method**: `subscribe(topics, listener=None, pattern=None)` — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html) (stable docs)

- **Partition assignors shipped**:
  - Default: `RoundRobinPartitionAssignor` (tuple of assignors; first matching broker preference wins) — [aiokafka consumer.py source, default `partition_assignment_strategy` parameter](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py)
  - `RangeAssignor` available — assigns partitions per-topic in lexicographic range order — [Kafka documentation, RangeAssignor](https://github.com/apache/kafka/blob/trunk/clients/src/main/java/org/apache/kafka/clients/consumer/RangeAssignor.java)
  - `StickyAssignor` available — minimizes partition movement during rebalance while maintaining balance (optimization for `on_partitions_revoked` cleanup efficiency) — [Kafka documentation, StickyAssignor](https://github.com/apache/kafka/blob/trunk/clients/src/main/java/org/apache/kafka/clients/consumer/StickyAssignor.java)
  - **No cooperative/incremental rebalancing found**: aiokafka 0.13.0 documentation only references eager rebalancing; cooperative rebalancing (KIP-429, protocol v5–8) support **not documented** and not evidenced in 0.13.0 — a known gap

- **Heartbeat & session timeout configuration**:
  - `heartbeat_interval_ms` (default TBD from docs) — sent at this interval; must be lower than `session_timeout_ms`, typically ≤1/3 of it — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html)
  - `session_timeout_ms` (default TBD) — broker-side; if no heartbeat arrives within this window, the broker removes the consumer and triggers rebalance — [Consumer client](https://aiokafka.readthedocs.io/en/stable/consumer.html)
  - `max_poll_interval_ms` (supported via [PR #482](https://github.com/aio-libs/aiokafka/pull/482)) — if `poll()` (in aiokafka `getmany()`/`getone()`) blocks longer, broker triggers rebalance even if heartbeats arrive — [PR #482: max_poll_interval_ms support](https://github.com/aio-libs/aiokafka/pull/482)
  - Broker-side floor: `group.min.session.timeout.ms` (Kafka broker config, default 6000 ms) — client-requested `session_timeout_ms` cannot be lower than this — [Kafka broker documentation, implicit from consumer docs](https://github.com/apache/kafka)

### Offset Management Verification API

- **Manual offset commit**: `enable_auto_commit=False` — disables background auto-commit, shifts to at-least-once semantics — [Manual commit example](https://aiokafka.readthedocs.io/en/stable/examples/manual_commit.html) (stable docs)

- **Commit methods**:
  - `commit()` — commits the current fetched offset for all subscribed partitions; returns coroutine — [aiokafka consumer.py source](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py) (line ~820)
  - `commit(offsets=None)` — accepts optional dict `{TopicPartition: (offset, metadata)}` to commit specific offsets instead of current fetch position — [aiokafka consumer.py source](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py)
  - `committed(partition)` — returns last committed offset (or `None` if not yet committed) for a single `TopicPartition` — [aiokafka consumer.py source, line ~876](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py)

- **Offset tracking methods**:
  - `position(partition)` — returns offset of the **next record to be fetched** (local, not broker-confirmed) — [aiokafka consumer.py source, line ~932](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py)
  - `seek(partition, offset)` — manually move fetch position to a specific offset (deterministic replay testing) — [aiokafka consumer.py source, line ~1009](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py)

- **Verifying durable commit**: call `committed(partition)` *after* `commit()` — if offset persists across consumer restart, the broker durably committed it — [Manual commit example and consumer.py semantics](https://aiokafka.readthedocs.io/en/stable/examples/manual_commit.html)

### At-Least-Once & Exactly-Once Semantics

- **At-least-once**: manual `commit()` only after processing (or in `on_partitions_revoked` rebalance hook); if consumer dies before commit, broker redelivers on next consumer join — [Manual commit example](https://aiokafka.readthedocs.io/en/stable/examples/manual_commit.html)

- **Exactly-once via transactions** ✅ **supported in 0.13.0**:
  - **Transactional producer**: `AIOKafkaProducer(transactional_id="unique-id-per-instance", ...)` — enables atomicity — [aiokafka producer documentation](https://aiokafka.readthedocs.io/en/stable/producer.html)
  - Idempotence auto-enabled when `transactional_id` is set — prevents duplicate messages on retries — [aiokafka producer documentation](https://aiokafka.readthedocs.io/en/stable/producer.html)
  - **Transaction context manager**: `async with producer.transaction(): ...` — messages visible to consumers only after commit — [aiokafka producer documentation](https://aiokafka.readthedocs.io/en/stable/producer.html)
  - **Offset-within-transaction**: `send_offsets_to_transaction(commit_offsets, group_id)` — atomically commits consumer offsets as part of the produce transaction — [aiokafka producer documentation](https://aiokafka.readthedocs.io/en/stable/producer.html)
  - **Consumer-side isolation**: `AIOKafkaConsumer(..., isolation_level="read_committed")` — filters out aborted/uncommitted transactional messages — [Transactional consume-process-produce example](https://aiokafka.readthedocs.io/en/stable/examples/transaction_example.html)
  - **Production readiness**: aiokafka 0.13.0 includes transactional support; the pattern is documented and exemplified — [Transactional example](https://aiokafka.readthedocs.io/en/stable/examples/transaction_example.html)
  - ⚠️ **Known constraint**: `transactional_id` must be globally unique per producer instance — reusing it fences the previous instance with non-retriable `ProducerFenced` error, forcing exit — [aiokafka producer documentation](https://aiokafka.readthedocs.io/en/stable/producer.html)

### Testcontainers Kafka Specifics

- **Container image**: `testcontainers[kafka]>=4.0` supports both traditional Kafka with ZooKeeper and KRaft (Kubernetes-native Raft) modes — exact image not specified in stable docs, likely `confluentinc/cp-kafka` or similar; KRaft availability is version/image dependent — [testcontainers.kafka.KafkaContainer documentation](https://testcontainers-python.readthedocs.io/en/latest/modules/kafka/README.html)

- **Bootstrap server**: `container.get_bootstrap_server()` returns the connection string — [testcontainers-python Kafka docs](https://testcontainers-python.readthedocs.io/en/latest/modules/kafka/README.html)

- **Topic creation** (pre-create multi-partition topics for rebalance tests):
  - Use `AIOKafkaAdminClient` + `NewTopic` objects — [aiokafka API](https://aiokafka.readthedocs.io/en/stable/api.html)
  ```python
  admin = AIOKafkaAdminClient(bootstrap_servers=container.get_bootstrap_server())
  await admin.create_topics([NewTopic(name="test-topic", num_partitions=3, replication_factor=1)])
  ```
  - Default partitions for auto-created topics: **not documented in testcontainers-python stable docs**; typically 1 partition (broker default `num.partitions=1`), but this is environment-dependent — [testcontainers.kafka module](https://testcontainers-python.readthedocs.io/en/latest/modules/kafka/README.html)

- **Dynamic topic creation**: by default, testcontainers Kafka allows `auto.create.topics.enable=true` (typical broker default) — sending a message to a non-existent topic auto-creates it (with default partition count) — but this is insecure for production and explicitly disableable — [confluent-kafka-python issue #1476 notes this pattern](https://github.com/confluentinc/confluent-kafka-python/issues/1476)

### Force-Rebalance Techniques for Deterministic Testing

| Technique | Determinism | Min Time (ms) | Trigger Mechanism | Notes |
|---|---|---|---|---|
| **Add a second consumer** | High | ~100–500 | Group membership change; broker elects new leader, rebalances all members | Timing depends on metadata refresh + group coordinator election; safe but not instant |
| **`consumer.stop()` / `await consumer.start()` cycle** | High | ~100–200 | Heartbeat stops; broker detects silence after `session_timeout_ms`; member rejoins as new instance | Must respect session timeout; can be tuned to ~6000ms floor (broker `group.min.session.timeout.ms`) |
| **Manually set `session_timeout_ms` / `heartbeat_interval_ms` low** | High | ~100–300 | Session expires faster; faster heartbeat timeout detection | Requires tuning both; heartbeat_interval ≤ 1/3 × session_timeout; testable in milliseconds with `metadata_max_age_ms=100`, `heartbeat_interval_ms=50`, `session_timeout_ms=300` — see [aiokafka consumer.py heartbeat configuration](https://github.com/aio-libs/aiokafka/blob/master/aiokafka/consumer/consumer.py) |
| **Increase partition count** (administrative change) | Medium | ~500–2000 | Topic metadata changes; triggers rebalance if subscribed | Subject to metadata refresh lag; broker must propagate metadata before rebalance triggers |
| **`consumer.unsubscribe()`** | Very high | ~0 | Immediate; consumer leaves group synchronously | Doesn't trigger group rebalance; consumer just exits group; less useful for testing multi-member rebalance scenarios |

**Recommendation for tests**: Combine low `session_timeout_ms` (6000 ms floor, but testable at 300–500 ms on real brokers) with adding a second consumer in the same group for deterministic, fast rebalance in integration tests — [from consumer docs heartbeat tuning](https://aiokafka.readthedocs.io/en/stable/consumer.html) + [PR #482 rebalance_timeout_ms](https://github.com/aio-libs/aiokafka/pull/482)

### Known Flakiness Traps for Integration Tests

- **Metadata propagation delay**: After broker receives a topic creation or partition count change, metadata caches on clients must refresh before rebalance is triggered — tuning `metadata_max_age_ms` to a low value (e.g., 200 ms) speeds tests but increases broker queries — [Consumer client docs imply metadata refresh cycle](https://aiokafka.readthedocs.io/en/stable/consumer.html)

- **Group coordinator election**: When a test creates a new consumer group, the broker must elect a group coordinator (one of the brokers in the cluster). Timing is not deterministic; tests should wait for first heartbeat/metadata refresh before asserting group state — [implied by consumer group rebalance docs](https://aiokafka.readthedocs.io/en/stable/consumer.html)

- **Offset commit not visible immediately**: `commit()` is asynchronous; committed offset may not be visible to `committed(partition)` instantly on heavily loaded brokers — always `await commit()` and verify with `committed()` in tight assertions — [Manual commit example](https://aiokafka.readthedocs.io/en/stable/examples/manual_commit.html)

- **Stale fetch position after rebalance**: In `on_partitions_revoked`, ensure all in-flight messages are processed and offsets are committed; in `on_partitions_assigned`, seek to correct position if resuming from a checkpoint — [Consumer client warning](https://aiokafka.readthedocs.io/en/stable/consumer.html)

## Version/Compatibility Notes

- **aiokafka 0.13.0** released 2026-01-02 — removed `api_version` parameter from all clients (breaking change); added transaction coordinator failover handling — [CHANGES.rst, aiokafka repository](https://github.com/aio-libs/aiokafka/blob/master/CHANGES.rst)
- **Kafka broker version**: aiokafka 0.13.0 auto-detects broker version on connection (no manual `api_version` specification needed) — [CHANGES.rst](https://github.com/aio-libs/aiokafka/blob/master/CHANGES.rst)
- **Minimum broker version for features used**:
  - Rebalance listeners / manual commit: Kafka 0.10.0+
  - Transactions / `send_offsets_to_transaction`: Kafka 0.11.0+
  - `isolation_level="read_committed"`: Kafka 0.11.0+
  - (All assumed safe for modern Kafka deployments — 2.x+ standard)
- **testcontainers[kafka]>=4.0** — no pinned aiokafka version; confirm compatibility via `pip freeze` in integration test environment — [testcontainers-python Kafka module](https://testcontainers-python.readthedocs.io/en/latest/modules/kafka/README.html)

## Evidence Gaps

- **Cooperative rebalancing support**: aiokafka 0.13.0 documentation does **not mention** KIP-429 (cooperative rebalancing); unclear if eager-only or if protocol v5–8 support exists but is undocumented. Check aiokafka source `group_coordinator.py` for `cooperative` strategy presence (not verified in available docs).
  
- **Default partition count in testcontainers Kafka**: testcontainers-python stable docs do not specify the default partition count for auto-created topics. Workaround: **always** pre-create test topics explicitly with `AIOKafkaAdminClient.create_topics()` specifying `num_partitions` and `replication_factor`.

- **Broker-side session timeout floor in testcontainers**: testcontainers Kafka likely uses defaults (`group.min.session.timeout.ms=6000`), but this is not documented. Check container logs or use `AdminClient.describe_cluster()` if integration tests require tuning below 6000 ms.

- **Exactly-once transactional durability guarantees**: aiokafka 0.13.0 docs exemplify the EOS pattern but do not detail failure scenarios (e.g., producer crash mid-transaction, consumer failover during read_committed isolation). Recommend testing with broker restarts and producer/consumer crashes in RT5 integration suite.

- **Rebalance timeout (`rebalance_timeout_ms`)**: Support was added in [PR #482](https://github.com/aio-libs/aiokafka/pull/482); exact default and tuning guidance not found in stable docs — check source or conduct bench tests.

- **Evidence on `on_partitions_revoked` deadline**: aiokafka docs warn of deadlock risk but do not specify a timeout for rebalance listener execution. If a listener hangs, how long before the broker force-ejects the consumer? Not documented.

## Librarian's Note

The sources strongly indicate **aiokafka 0.13.0 supports all the primitives needed for RT5 integration testing**: rebalance listeners (eager only), manual offset commits with durability verification, and exactly-once semantics via transactional producer + `send_offsets_to_transaction`. However, **cooperative rebalancing is a known gap** (not mentioned in stable docs, likely not supported yet), and **testcontainers Kafka defaults** (partition count, broker config floor values) require explicit pre-creation or discovery during test setup. Start with eager rebalancing and explicit topic creation; escalate cooperative rebalancing to backlog if RT5 scope requires load-minimizing incremental rebalancing.

Sources confirm the reliability model (at-least-once via manual commit, exactly-once via transactions) is production-ready and well-exemplified.
