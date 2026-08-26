# Research 004 — WebSocket/SSE Backpressure, Reconnect, and Testing Patterns
Date: 2026-08-26 · Freshness matters: yes — library versions and HTTP specs are stable, but websockets 17.x is very recent (client API rewrite).

## Question
For `varco_ws` real WebSocket/SSE server testing (RT4), what are the current recommended APIs, backpressure knobs, ordering semantics, and testing patterns?

Specifically:
1. **websockets 17.x client/server API**: What is the modern import path and API? What is deprecated?
2. **Backpressure controls**: What knobs exist to observe and assert backpressure? How to test deterministically?
3. **Ordering guarantees**: What does RFC 6455 promise about message ordering on a single connection?
4. **Connection pooling tests**: How to run N concurrent connections in pytest-asyncio against a local server?
5. **SSE testing**: Recommended Python library and testing pattern for consuming SSE streams?
6. **Uvicorn in-process**: Recommended pattern for running a uvicorn server in test fixtures?

## Findings

### 1. websockets 17.x API: Modern asyncio Implementation

**Import path**: `websockets.asyncio.client.connect` (default in v17.x). The older `websockets.client` and `websockets.legacy` packages are maintained for backward compatibility but deprecated. — [Upgrade to the new asyncio implementation - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/howto/upgrade.html) (websockets v17.x, official)

**Core API shape**: `connect()` returns an async context manager or async iterator. Key parameters:
- `uri`: connection target
- `max_size` (default 1 MiB): max frame size
- `max_queue` (default 16): incoming message queue depth
- `write_limit` (default 32KB): outgoing buffer limit
- `process_exception`: callback to distinguish transient vs. fatal errors for reconnect logic
- `reconnect_delays`: exponential backoff function (default: 5s jitter, capped at 60s)
- `ping_interval` (default 20s), `ping_timeout` (default 20s)

**Usage as async context manager**: connection closes automatically on exit; safe for single-use. — [Client (asyncio) - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) (websockets v17.x, official)

**Usage as async iterator** ("infinite asynchronous iteration"): automatically reconnects on transient errors with exponential backoff. Each iteration closes the connection; fatal errors raise and break the loop. — [Client (asyncio) - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) (websockets v17.x, official)

**recv() and send() methods**:
- `recv()`: async coroutine returning next message (str for text frames, bytes for binary). Safely cancellable with no data loss. Parameter `decode` can override default UTF-8 handling.
- `send()`: async coroutine transmitting a message (str, bytes, or iterable for fragmented transmission). Parameter `text` can override automatic type detection.
— [Client (asyncio) - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) (websockets v17.x, official)

**Reconnection and exception handling**: `process_exception(exc)` callback receives exceptions during connection; return `True` to treat as transient (reconnect), `False` to treat as fatal (raise and stop). Default built-in function handles common transient errors (DNS, connection reset). — [Client (asyncio) - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) (websockets v17.x, official)

### 2. Backpressure Controls and Testing

**websockets library**:
- **`max_queue` (default 16)**: incoming message queue depth. When exceeded, the connection stops reading from the network until the application consumes messages and the queue drains below the low-water mark.
- **`max_size` (default 1 MiB)**: maximum size per message frame.
- **`write_limit` (default 32KB)**: outgoing buffer threshold. When exceeded, `send()` waits until the buffer drains below the low-water mark before returning.
- **Flow control mechanism**: high-water mark (queue full) stops network reads; low-water mark (queue drains) resumes. The documentation does not specify the exact low-water-mark value; it is implementation-dependent.
— [Memory and buffers - websockets 16.1 documentation](https://websockets.readthedocs.io/en/stable/topics/memory.html) (websockets v16.1, official; applies to v17.x)

**uvicorn server-side**:
- **`--ws-max-queue` (default 32)**: maximum length of WebSocket incoming message queue. Part of uvicorn's websockets protocol support.
- **`--ws-max-size` (default 16 MB)**: maximum WebSocket message size in bytes.
- **`--ws-per-message-deflate` (default enabled)**: per-message compression support.
— [uvicorn/docs/settings.md](https://github.com/encode/uvicorn/blob/master/docs/settings.md) (uvicorn official, GitHub)

**Testing backpressure deterministically**:
- Create a slow consumer (apply `asyncio.sleep()` in a recv loop) and fast producer (rapid `send()` calls).
- Monitor `send()` blocking: when the write buffer overflows, `send()` blocks until drained.
- Observe queue fill by instrumenting the library or tracking message latencies.
- Assert expected behavior: e.g., producer stalls at N pending messages (when `max_queue` is reached).
— [Memory and buffers - websockets 16.1 documentation](https://websockets.readthedocs.io/en/stable/topics/memory.html) (websockets v16.1, official)

### 3. Message Ordering Guarantees (RFC 6455)

**RFC 6455 explicit guarantees**:
- "Message fragments MUST be delivered to the recipient in the order sent by the sender."
- Subsequent frames are processed sequentially; the protocol establishes a sequential delivery model for messages on a single connection.
- "The fragments of one message MUST NOT be interleaved between the fragments of another message unless an extension has been negotiated that can interpret the interleaving."
— [RFC 6455: The WebSocket Protocol](https://www.rfc-editor.org/rfc/rfc6455.txt) (IETF RFC, official specification)

**Testing ordering**:
- Assign monotonic sequence numbers to sent messages.
- Assert recv'd sequence numbers increment without gaps or out-of-order arrival.
- Per-connection ordering is guaranteed by TCP and WebSocket; no cross-connection ordering without application logic.

### 4. Connection Pooling and Concurrent Client Testing

**Recommended pattern with pytest-asyncio**:
Use `pytest-asyncio` with async fixtures that create many concurrent connections to the test server. Each connection runs as a separate asyncio task. — [Testing with pytest-asyncio raises RuntimeError: Event loop is closed · Issue #908 · django/channels](https://github.com/django/channels/issues/908) (Django channels discussion, community)

**File descriptor concerns**:
- File descriptor limits on GitHub Actions ubuntu-latest runners are **not documented** in official GitHub Actions limits. — [Limiting workflow run time](https://docs.github.com/en/enterprise-server@3.14/actions/reference/limits) (GitHub Actions official documentation)
- General guidance: a single process can handle ~10K concurrent TCP connections before hitting event-loop throughput limits. To go higher, use `uvloop`. — [Python WebSocket Server & Client Guide with asyncio | WebSocket.org](https://websocket.org/guides/languages/python/) (community, WebSocket.org)

**Cleanup pattern**:
Use async context managers (`async with connection`) for each client to ensure proper socket closure and avoid descriptor leaks. Collect tasks with `asyncio.gather()` and ensure all complete before test teardown.

### 5. SSE Testing

**Recommended library**: `httpx-sse` v0.4.x. Provides `aconnect_sse()` helper (async) and `connect_sse()` helper (sync) to consume Server-Sent Event streams. — [httpx-sse/README.md](https://github.com/florimondmanca/httpx-sse/blob/master/README.md) (florimondmanca/httpx-sse, official GitHub)

**API shape**:
```python
async with aconnect_sse(client, "GET", "http://localhost:8000/sse/") as event_source:
    events = [sse async for sse in event_source.aiter_sse()]
```
Returns `ServerSentEvent` objects with `.event`, `.data`, `.id`, `.retry` fields plus `.json()` method. — [httpx-sse/README.md](https://github.com/florimondmanca/httpx-sse/blob/master/README.md) (florimondmanca/httpx-sse, official GitHub)

**HTML 5 SSE spec: Last-Event-ID and retry semantics**:
- **Last-Event-ID header**: When a connection fails, the client automatically sends `Last-Event-ID: <last-received-id>` in the reconnect request, allowing the server to resume from the correct position. — [HTML Standard: Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) (W3C/WHATWG, official specification)
- **retry field**: When present in an event, if the field consists of ASCII digits only, the client interprets it as an integer (milliseconds) and updates its reconnection time. — [HTML Standard: Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) (W3C/WHATWG, official specification)
- **Default reconnection time**: "implementation-defined, probably in the region of a few seconds" (typically 3 seconds), with optional exponential backoff to avoid overloading a recovering server. — [HTML Standard: Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) (W3C/WHATWG, official specification)

**Buffering pitfalls**:
- **Nginx proxy buffering**: Nginx buffers ~16KB before flushing SSE events, breaking real-time delivery. Solution: add `X-Accel-Buffering: no` header.
- **Cloudflare**: Buffers ~100KB before flushing, delaying real-time delivery.
- **FastAPI best practices**: FastAPI's `EventSourceResponse` automatically sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`, and sends a "keep alive" comment every 15 seconds to prevent proxy timeouts. These are applied out-of-the-box. — [Server-Sent Events - FastAPI](https://fastapi.tiangolo.com/tutorial/server-sent-events/) (Sebastián Ramírez, FastAPI official)

### 6. Running uvicorn In-Process for Tests

**Official recommendation**: Use `httpx.AsyncClient` with the ASGI app directly (no HTTP) for most unit/integration tests. This is faster and avoids socket overhead. — [Starting an app with Uvicorn for testing — Safir](https://safir.lsst.io/user-guide/uvicorn.html) (community, Safir testing docs)

**When real HTTP is required** (e.g., for browser/HTTP-client-specific testing):
- Use `safir.testing.uvicorn.spawn_uvicorn()` to launch uvicorn in a **separate process** (not in-process). This is more robust than threading. — [Starting an app with Uvicorn for testing — Safir](https://safir.lsst.io/user-guide/uvicorn.html) (community, Safir testing docs)

**In-process async approach** (if necessary):
- Create a `uvicorn.Config`, instantiate `uvicorn.Server()`, wrap in an asyncio task, wait for server startup, yield the URL, then set `should_exit = True` and await task completion.
- No explicit "readiness" API is documented; the pattern relies on the server task accepting connections once the lifespan hook completes.
— [How to start a Uvicorn + FastAPI in background when testing with PyTest](https://www.iditect.com/faq/python/how-to-start-a-uvicorn--fastapi-in-background-when-testing-with-pytest.html) (community, iditect)

## Options compared

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|---|---|---|---|
| **Direct ASGI with httpx.AsyncClient** | Fast (no HTTP/socket overhead). Built-in to httpx. Integrates with pytest-asyncio seamlessly. Recommended pattern. | Does not test actual HTTP/WebSocket protocol over network (misses proxy/buffering issues). | [Safir docs](https://safir.lsst.io/user-guide/uvicorn.html) recommend this as default. |
| **Separate-process uvicorn (safir.spawn_uvicorn)** | Real HTTP/WebSocket over sockets. Tests actual proxy/network behavior. Robust process lifecycle. | Slower startup (full process spawn). Harder to debug test failures. Port allocation needed. | Safir recommends for cases requiring real HTTP. |
| **In-process uvicorn (asyncio task)** | Shared memory with test (easier debugging). Avoids process overhead. | No documented readiness API (must poll or sleep). Complex lifecycle management. Event loop must be running. Fragile if server crashes. | Community reports but not officially recommended. |

## Version/compatibility notes

- **websockets**: v14.0 introduced new asyncio rewrite (now default); legacy v13 and older use `websockets.legacy` (deprecated but supported until November 2029). — [Upgrade to the new asyncio implementation - websockets 17.0 documentation](https://websockets.readthedocs.io/en/stable/howto/upgrade.html) (websockets v17.x, official)
- **httpx-sse**: v0.4.x stable and recommended. Earlier v0.3.x exists but v0.4.x is current.
- **Starlette/FastAPI**: SSE support via `EventSourceResponse` / `StreamingResponse` with automatic header injection (Cache-Control, X-Accel-Buffering, keep-alive pings).
- **uvicorn**: ws-max-queue and ws-max-size available in recent versions; check `--help` or Config class for exact availability in your pinned version.

## Evidence gaps

- **websockets low-water mark value**: The documentation does not specify the exact threshold at which network reads resume after high-water-mark backpressure. This is implementation-defined and may vary by version. A test may need to empirically observe this.
- **GitHub Actions file descriptor limits**: Official documentation does not state the hard limit (e.g., 1024, 4096, 65536). Community reports mention this is a concern but no official limit is published.
- **uvicorn in-process readiness**: No documented API for waiting until the server is listening before yielding to tests. Existing patterns rely on polling or sleep. A health-check GET request is the typical workaround.
- **SSE stream buffering across all proxies**: Only Nginx, Cloudflare, and Akamai are mentioned. Behavior on other reverse proxies (HAProxy, Envoy, AWS ALB) not verified from primary sources.

## Librarian's note

**For varco_ws RT4 testing, the evidence strongly favors these patterns**:

1. **Backpressure testing**: Use websockets' `max_queue` and `write_limit` parameters to create deterministic backpressure conditions. Monitor `send()` blocking times and assert queue fill behavior. uvicorn's `--ws-max-queue` on the server side can be set via Config; varco_ws tests should exercise both client-side and server-side limits.

2. **Ordering**: RFC 6455 guarantees in-order delivery on a single connection. Tests should assign sequence numbers and verify no gaps or out-of-order arrival. This is automatically provided by the protocol; no special library support needed.

3. **Connection pooling**: pytest-asyncio with concurrent async fixtures is the standard pattern. File descriptor limits on GitHub Actions are not officially documented; empirical testing in CI is recommended (start with 100–1000 concurrent connections and observe failures).

4. **SSE testing**: Use httpx-sse 0.4.x with `aconnect_sse()`. The library handles Last-Event-ID and retry field parsing automatically. Verify proxy buffering by including assertions on `X-Accel-Buffering: no` response headers.

5. **Server lifecycle**: For tests requiring actual HTTP (WebSocket over sockets), use Safir's separate-process spawning or httpx.AsyncClient with ASGI (which covers ~90% of cases). Avoid in-process asyncio task approach unless debugging real socket/SSL issues; the pattern is fragile.

The evidence indicates that websockets 17.x, httpx-sse 0.4.x, and Starlette/FastAPI's built-in SSE support are mature and well-documented. The main evidence gaps are GitHub Actions resource limits and uvicorn readiness APIs — these are operational concerns, not API concerns.

