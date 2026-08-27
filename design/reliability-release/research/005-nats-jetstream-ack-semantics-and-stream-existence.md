# Research 005 — NATS JetStream Ack Semantics & Stream Existence Checking

Date: 2026-08-27 · Freshness matters: yes — nats-py & JetStream server APIs evolve; exact exception types and method signatures vary by version.

## Question

For the varco_nats backend (RT2-B and RT2-C ABC-contract gaps):

A. Current sanctioned negative-ack API in nats-py for triggering redelivery when a handler raises.
B. Consumer-side configuration (AckPolicy, max_deliver, ack_wait, backoff) and max_deliveries advisory surface.
C. Redelivery-count accessor on a received message.
D. Latency and correctness difference between not acking vs. explicit nak().
E. Current nats-py APIs for true "stream exists" and "consumer exists" predicates.
F. Distinction between "stream exists" vs. "stream has messages" in the API.
G. Version caveats and recent changes (nats-py releases in last ~18 months).

## Findings

### A. Negative Acknowledgment API (Redelivery on Handler Failure)

- **`msg.nak(delay=None)`** — negative acknowledges a message, triggering immediate redelivery (or delayed if `delay` parameter provided). Exact signature: `async def nak(self, delay: Union[int, float, None] = None) -> None`. This is the sanctioned API for handler-failure retry. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)
  
- **`msg.in_progress()`** — signals the message is still being processed and resets the ack_wait timer. Can be called multiple times (unlike `ack()` or `nak()`). Signature: `async def in_progress(self) -> None`. Use when a handler needs to signal "still working" to prevent timeout-triggered redelivery. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)
  
- **`msg.term()`** — terminates a message and disables all future redeliveries. Signature: `async def term(self) -> None`. Use only for unrecoverable failures or poison-pill detection. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)
  
- **No `nak_with_delay()` method exists.** Delay is an optional parameter of `nak()` itself. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)

### B. Consumer-Side Configuration for Redelivery

- **`AckPolicy` values**: 
  - `AckPolicy.EXPLICIT` (default) — each message must be explicitly acknowledged, or it will be redelivered after `ack_wait`.
  - `AckPolicy.NONE` — push consumers may skip acks entirely (useful for fire-and-forget).
  - `AckPolicy.ALL` — acking a message also acks all prior messages in the consumer. For pull consumers, `EXPLICIT` is mandatory.
  — [NATS docs: Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers) (reference)

- **`ack_wait`** — duration (default 30 seconds) the server waits for an acknowledgment before redelivering. Redelivery happens automatically when the timer expires. — [NATS docs: Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers) (reference)

- **`max_deliver`** — maximum number of times a message will be redelivered. When exhausted, the server publishes an advisory message and stops redelivering (the message remains in the stream unless explicitly ack'd or term'd). — [NATS blog: JetStream Reliable Delivery](https://www.synadia.com/blog/jetstream-reliable-delivery-dlq-replay) (2024+)

- **`backoff`** — optional list of durations for escalating redelivery delays. Overrides `ack_wait`. Allows implementing exponential backoff (e.g., `[1s, 5s, 30s]`). — [NATS docs: Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers) (reference)

- **Max Deliveries Advisory**: When `max_deliver` is exhausted, NATS publishes an advisory on `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<STREAM>.<CONSUMER>`. Applications must monitor this subject to route max-delivery messages to a DLQ; there is no automatic DLQ. — [NATS docs: Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers) (reference); [Synadia: JetStream Reliable Delivery](https://www.synadia.com/blog/jetstream-reliable-delivery-dlq-replay) (2024+)

### C. Redelivery Count Accessor

- **`msg.metadata.num_delivered`** (integer) — field on the `Metadata` dataclass accessible from a received JetStream message. Contains the number of times this message has been delivered. A value > 1 indicates redeliveries have occurred. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)

- **Path**: `msg.metadata.num_delivered` (direct attribute access, no getter method). — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)

- **Related metadata fields**: `msg.metadata.sequence.stream` (stream sequence), `msg.metadata.sequence.consumer` (consumer sequence), `msg.metadata.num_pending` (messages not yet consumed in stream). — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/aio/msg.html) (current)

### D. Difference: Not Acking vs. Explicit `nak()`

- **Passive timeout (not acking)**:
  - Server waits `ack_wait` duration (default 30 seconds).
  - If no acknowledgment arrives by then, server automatically redelivers.
  - Latency: `ack_wait` delay (e.g., 30s) before redelivery begins.
  - Semantic: passive, server-side timeout mechanism.
  — [NATS docs: Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers) + [Synadia: JetStream Reliable Delivery](https://www.synadia.com/blog/jetstream-reliable-delivery-dlq-replay) (2024+)

- **Explicit `nak()`**:
  - Consumer explicitly signals the server to redeliver immediately (or after optional `delay`).
  - Latency: immediate (or after `delay` if specified), no timeout wait.
  - Semantic: proactive, consumer-initiated redelivery request.
  - Preferred by ecosystem for handler-failure retry: faster feedback loop and explicit control.
  — [Synadia: JetStream Reliable Delivery](https://www.synadia.com/blog/jetstream-reliable-delivery-dlq-replay) (2024+); [NATS by Example](https://natsbyexample.com/examples/jetstream/push-consumer/go/)

- **Correctness**: Both mechanisms are correct; `nak()` is preferred when you want immediate feedback and control over retry timing.

### E. Stream & Consumer Existence Predicates

- **`await jsm.stream_info(name: str, subjects_filter: Optional[str] = None) -> StreamInfo`**
  - Raises `NotFoundError` (404) if stream does not exist.
  - Returns `StreamInfo` object on success.
  - This is the true "stream exists" predicate; unlike `channel_exists()` implementations in varco_nats, it does not check message count.
  — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html) (current); [nats-py GitHub issues #416](https://github.com/nats-io/nats.py/discussions/416)

- **`await jsm.streams_info(offset: int = 0) -> List[StreamInfo]`**
  - Returns all streams starting at `offset`. Supports pagination.
  - Does not raise; returns empty list if no streams exist.
  - For large deployments, use `jsm.streams_info_iterator()` to avoid memory overhead.
  — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html) (current); [Tessl nats-py 2.11.0 docs](https://tessl.io/registry/tessl/pypi-nats-py/2.11.0/files/docs/jetstream-management.md)

- **`await jsm.consumer_info(stream: str, consumer: str, timeout: Optional[float] = None) -> ConsumerInfo`**
  - Raises `NotFoundError` (404) if consumer does not exist.
  - Equivalent stream-existence check for consumers.
  — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html) (current)

- **`await jsm.consumers_info(stream: str, offset: Optional[int] = None) -> List[ConsumerInfo]`**
  - Returns consumers for a stream, paginated by offset.
  - Async iterator `jsm.consumers_info_iterator(stream)` available for large consumer lists.
  — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html) (current)

- **Exception type**: `nats.js.errors.NotFoundError` (not `KeyError` or generic `Exception`). Catch this for graceful "stream not found" handling. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/errors.html) (current); [nats-py GitHub issue #678](https://github.com/nats-io/nats.py/issues/678)

### F. Stream Exists vs. Stream Has Messages

- **`StreamInfo.state.messages`** (integer) — field in the returned `StreamInfo` state object that holds the message count.
  - Exact path: `stream_info_result.state.messages` (returns int or similar depending on server version).
  - A stream can exist with `messages == 0` (empty stream).
  — [NATS docs: Stream Info](https://docs.nats.io/reference/2.12/jetstream/api/stream/info) (reference); [Synadia: Stream/Message Count Inconsistency](https://www.synadia.com/insights/checks/nats-stream-subject-message-count-inconsistency) (2024+)

- **Clean separation in nats-py**:
  - `stream_info(name)` → raises `NotFoundError` if stream does not exist (true "exists" predicate).
  - `stream_info(name).state.messages` → returns 0 if stream exists but is empty (independent "has messages" predicate).
  - The varco_nats `channel_exists()` currently conflates these; fix by returning `stream_info().state.messages > 0` only for the "has messages" case and removing that logic from the existence check.
  — [NATS docs: Stream Info](https://docs.nats.io/reference/2.12/jetstream/api/stream/info); [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html)

### G. Version Caveats & Recent Changes

- **Current stable: nats-py v2.15.0** (June 5, 2026). No breaking changes to ack/stream-info APIs in recent releases. — [nats-py GitHub releases](https://github.com/nats-io/nats.py/releases)

- **Recent releases (2024–2026)**:
  - v2.15.0 (June 2026) — incremental refinements.
  - v2.14.0 (Feb 2026) — stable.
  - v2.13.0 (Feb 2026) — stable.
  - v2.12.0 (Oct 2025) — stable.
  - v2.11.0 (July 2025) — introduced pagination support via iterators.
  - v2.9.0+ (Aug 2024 onward) — stable nak/in_progress/term APIs.
  — [nats-py GitHub releases](https://github.com/nats-io/nats.py/releases); [PyPI nats-py](https://pypi.org/project/nats-py/)

- **No API deprecations or breaking changes** to acknowledgment or stream-info methods in the last 18 months. The nak(delay=None) API has been stable since at least v2.8.0 (June 2024). — [nats-py GitHub releases](https://github.com/nats-io/nats.py/releases)

- **Asyncio gotcha**: All JetStreamManager methods (`stream_info()`, `consumer_info()`, etc.) are `async` and must be awaited. No synchronous blocking variants; always use within an async context. — [nats.py documentation](https://nats-io.github.io/nats.py/_modules/nats/js/manager.html) (current)

- **NATS server version**: nats-py v2.15.0 requires NATS server v2.2.0+. Current NATS server stable is v2.12.x; no ack/stream-info API changes expected when upgrading within v2.x line. — [NATS docs](https://docs.nats.io/) (reference)

## Options Compared

Not applicable — this research documents the current, canonical API. There is only one correct way to achieve each goal.

## Version/Compatibility Notes

| Component | Current Stable | Min Supported | EOL / Caveat |
|---|---|---|---|
| **nats-py** | 2.15.0 (June 2026) | 2.8.0 (June 2024) | No deprecations; nak/in_progress/term stable since v2.8.0 |
| **NATS Server** | 2.12.x | 2.2.0 | All v2.x releases support these APIs; v2.12.x recommended for production |
| **Python** | 3.8+ | 3.7 | nats-py 2.15.0 requires Python 3.8+ (asyncio improvements) |

## Evidence Gaps

- Exact byte-latency comparison (not acking vs. `nak()`) under network conditions — not officially documented; empirical testing recommended.
- Whether `stream_info()` returns 404 vs. other error for a deleted stream (possible race condition) — not explicitly covered in nats-py docs; NATS server documentation likely has detail.
- Whether `streams_info()` / `consumers_info()` pagination offset wraps or errors on out-of-range; behavior under concurrent additions/deletions. Worth a separate brief if precision required.
- Consumer-level `num_redelivered` counter (vs. message-level `num_delivered`) — exists per Synadia docs but nats-py field path not confirmed; recommend source inspection.

## Librarian's Note

**RT2-B fix**: varco_nats must wrap handler invocation in a try-except and call `msg.nak()` (not silently ack in finally). This is the canonical nats-py/JetStream pattern; no custom override needed. Redelivery semantics will immediately become correct.

**RT2-C fix**: Replace `ChannelManager.channel_exists()` to call `jsm.stream_info(name)` directly and catch `NotFoundError` — a true existence check independent of message count. The current implementation (checking for messages) is the wrong predicate; the API cleanly separates the two concerns.

Both gaps close with straightforward rewrites; no workarounds or nats-py version downgrades needed.
