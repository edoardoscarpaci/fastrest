# Research 003 — MCP Python SDK v1→v2 Migration

Date: 2026-09-04 · Freshness matters: **yes** — SDK APIs, transport layer, and protocol version all change per release

## Question

varco's `varco_fastapi.router.mcp` pins `mcp>=1.28.1,<2` and its `to_mcp_server()` uses `mcp.server.lowlevel.Server` with `@server.list_tools()` and `@server.call_tool()` decorators. The MCP Python SDK v2.0.0 (released ~2026-07-28) claims to have removed those decorators. Answer:

1. What is the current latest released `mcp` version and its release date? Is 2.x stable?
2. Is the claim true about v2 removing `mcp.server.lowlevel.Server` decorators? Quote the migration notes.
3. What is the v2 equivalent for dynamic tool registration (varco generates tools at runtime)?
4. What changed in transport/ASGI mounting between 1.x and 2.x?
5. What changed in the MCP wire protocol (2026-07-28 vs 2025-11-25), especially tool results and annotations?
6. Can one codebase support both v1.x and v2.x, or is a hard pin bump the only option?
7. Python version requirements for mcp 2.x (varco's matrix is 3.12/3.13)?

## Findings

### 1. Latest version and release status

- **MCP Python SDK v2.0.0 is the current stable release, shipped 2026-07-28** — [Release v2.0.0 · modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- v2 implements the 2026-07-28 MCP specification revision and serves every earlier protocol revision from the same server — [The 2026-07-28 Specification | Model Context Protocol Blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- `pip install mcp` now resolves to v2.x by default (v1.x is maintenance-only) — [Release v2.0.0 · modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- v1.x (1.28.x is the final v1 release) lives on the v1.x branch, receives only critical security fixes going forward — [GitHub - modelcontextprotocol/python-sdk at v1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)

### 2. Decorator API removal in lowlevel.Server

**Claim is TRUE — v2 removed decorators from the lowlevel `Server` API entirely.**

Before v1.x (`mcp>=1.28.1,<2`):
```python
from mcp.server import Server
server = Server("my-server")

@server.list_tools()
async def handle_list_tools():
    return [Tool(...)]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    return CallToolResult(...)
```

After v2.0.0:
```python
from mcp.server import Server
from mcp.types import CallToolRequestParams

async def handle_list_tools():
    return [Tool(...)]

async def handle_call_tool(params: CallToolRequestParams):
    return CallToolResult(...)

server = Server("my-server", on_list_tools=handle_list_tools, on_call_tool=handle_call_tool)
```

Migration detail: handlers are now **constructor arguments** (`on_*` parameters), not decorators applied post-construction — [Migration Guide: v1 to v2 - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/migration/). The high-level `MCPServer` class (formerly `FastMCP` in v1) still uses decorators (`@mcp.tool()`), but the low-level `Server` does not.

All handlers now receive `ctx: ServerRequestContext` as the first argument — [The low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).

### 3. Dynamic tool registration in v2 (runtime-generated tools)

For varco's use case (generating tools from router routes at runtime), the lowlevel `Server` provides `add_request_handler(method: str, params_type, handler)` as "the post-construction escape hatch" — [The low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).

**Minimal v2 code shape for dynamic tools:**

```python
from mcp.server import Server
from mcp.types import CallToolRequestParams, Tool, TextContent, CallToolResult

# Create server with static handlers (list_tools, call_tool hardwired as constructor args)
async def list_tools_handler():
    # Return dynamically-built tool list
    return [
        Tool(
            name="dynamic_tool_1",
            description="...",
            inputSchema={"type": "object", "properties": {...}}
        )
    ]

async def call_tool_handler(params: CallToolRequestParams):
    # Dispatch by tool name at runtime
    if params.name == "dynamic_tool_1":
        result = ... # execute tool
        return CallToolResult(content=[TextContent(type="text", text=result)])

server = Server("router-server", on_list_tools=list_tools_handler, on_call_tool=call_tool_handler)

# If needing to register additional custom RPC methods beyond tools:
class CustomParams(RequestParams):
    setting: str

async def custom_handler(ctx, params):
    return {"status": "ok"}

server.add_request_handler("myvendor/custom", CustomParams, custom_handler)
```

**Key constraint:** the `on_list_tools` and `on_call_tool` handlers (covering the 2026-07-28 tools protocol) still hardwired at construction time — varco's existing pattern of building a list of `MCPToolDefinition` objects at adapter construction (lines 545–578 of mcp.py) **maps directly to this model** — instead of decorators, you pass them to `Server(on_list_tools=..., on_call_tool=...)` — [The low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).

### 4. Transport and ASGI mounting changes

**httpx2 replaces httpx + httpx-sse:**
- The SDK now depends on `httpx2` (>=2.5.0) — a next-generation httpx fork with server-sent events support built in — instead of separate `httpx` + `httpx-sse` packages.
- If you construct an `http_client` manually and pass it to a transport, change the import from `httpx` to `httpx2` only — the API is compatible — [Migration Guide: v1 to v2 - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/migration/).
- To consume SSE directly, use `httpx2.EventSource` (or `AsyncClient.sse()`) instead of the old httpx-sse helpers.

**SSE transport in v2:**
- The low-level `Server` has no built-in `sse_app()` method (removed; this matches varco's observation that `mount()` was already broken before this change, lines 806–809).
- Varco's documented recipe works in v2 as-is: construct `SseServerTransport` and wire it to `server.run(streams[0], streams[1], ...)` inside a `connect_sse(scope, receive, send)` context — the transport is provided by `mcp.server.sse.SseServerTransport` — [The low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).
- **SSE GET streams now send header `Accept: application/json, text/event-stream`** (previously exactly `text/event-stream`).

**ASGI sub-path mounting via `root_path`:**
- The `SseServerTransport` respects ASGI's `root_path` mechanism automatically — when using Starlette's `Mount("/path", app=...)`, Starlette sets `root_path` in the ASGI scope, and the transport uses it to construct the correct message endpoint path. No code changes required on varco's side for ASGI mounting.

### 5. MCP protocol wire-level changes (2026-07-28 spec)

**Stateless → request/response (no session/handshake):**
- Removed: `initialize`/`notifications`/`initialized` handshake
- Removed: protocol-level sessions and `Mcp-Session-Id` header in Streamable HTTP transport
- Every request now carries `_meta` with `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` — [The 2026-07-28 Specification | Model Context Protocol Blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/) and [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

**Tool result structure changes:**
- **All results now require a `resultType` field** — either `"complete"` (ordinary results) or `"input_required"` (for multi-round-trip request interim results) — [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Clients **MUST** treat results from v2025-era servers (which omit `resultType`) as `"complete"` for backward compatibility.
- Tool results returned by `tools/list`, `prompts/list`, `resources/list`, `resources/read`, and `resources/templates/list` now **require** `ttlMs` (freshness hint in milliseconds) and `cacheScope` (`"public"` or `"private"`) fields via a new `CacheableResult` interface — enables client-side caching and reduces polling — [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

**Multi-Round-Trip Requests (MRTR) pattern:**
- Servers may return `InputRequiredResult` with `resultType: "input_required"` and an `inputRequests` field carrying requests for additional information.
- Clients respond with `inputResponses` on a retry of the original request — replaces the old pattern of servers initiating requests to clients (sampling/elicitation/roots/list).

**HTTP request headers on POST:**
- Standard MCP request headers (`Mcp-Method`, `Mcp-Name`) are now **required** on Streamable HTTP POST requests — [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Support added for custom headers from tool parameters via `x-mcp-header`.

**Error code renumbering:**
- `HeaderMismatch` `-32001` → `-32020`
- `MissingRequiredClientCapability` `-32003` → `-32021`
- `UnsupportedProtocolVersion` `-32004` → `-32022`

**Deprecated in 2026-07-28:**
- HTTP+SSE transport (use Streamable HTTP) — [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Roots, Sampling, and Logging features — [Key Changes - Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

### 6. Dual v1/v2 support in one codebase

**Hard pin is the only realistic option — one codebase cannot cleanly support both v1 and v2 concurrently.**

Reasoning:
- The lowlevel `Server` architecture underwent a fundamental rewrite: v1 uses decorators applied post-construction, v2 uses constructor arguments. Both APIs exist, but you must choose one at server instantiation time — conditional imports and runtime branching lead to unmaintainable code.
- The high-level `MCPServer` (formerly `FastMCP`) class still supports decorators in both versions, **but varco uses the lowlevel `Server` explicitly** (line 763) because it needs schema control that `MCPServer` does not expose — SDK issue #761, the `input_schema=` parameter gap — [varco_fastapi/router/mcp.py lines 712–745](https://github.com/edoardoscarpaci/varco/blob/3.1.0-plans/varco_fastapi/varco_fastapi/router/mcp.py#L712).
- The protocol wire format changed fundamentally (stateless, `resultType` field, `CacheableResult`, MRTR) — varco does not emit protocol messages directly (it uses the SDK's types), but if you maintain a library that others depend on, choosing v1 vs v2 globally is cleaner than per-instance branching.

**Ecosystem pattern:** Major MCP consumers (IBM mcp-context-forge, langchain-ai/langchain-mcp-adapters) all issued **"pin to <2 before stable v2 lands"** guidance, then migrated wholesale — [CHORE: MUST pin mcp>=1.28.1,<2 before MCP Python SDK v2 stable release · IBM/mcp-context-forge Issue #5839](https://github.com/IBM/mcp-context-forge/issues/5839) and [Support for upcoming Python MCP SDK v2.0.0 · langchain-ai/langchain-mcp-adapters Issue #578](https://github.com/langchain-ai/langchain-mcp-adapters/issues/578).

The TypeScript SDK documents a "dual-stack" pattern (leave v1 sessionful path intact, add a separate v2 entry point, route with `isLegacyRequest()`), but this is for servers that need to support both client eras *simultaneously* — not for SDKs — [typescript-sdk/docs/migration/support-2026-07-28.md](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/migration/support-2026-07-28.md). The Python SDK v2 **serves both protocol eras** transparently (any MCPServer/Server automatically speaks 2025-era to v2025 clients and 2026-07-28 to v2026 clients), but the SDK choice itself is per-process.

### 7. Python version requirements for mcp 2.x

**MCP Python SDK v2.0.0 requires Python 3.10+** — [Minimum Python Version for MCP](https://github.com/modelcontextprotocol/python-sdk/blob/main/pyproject.toml) states `requires-python = ">=3.10"`.

Varco's test matrix is Python 3.12/3.13 — **both are fully supported**, no constraints.

## Options compared

| Approach | Strengths | Weaknesses | Evidence |
|---|---|---|---|
| Stay on v1.x (pin `mcp>=1.28.1,<2`) | No code changes needed now; v1.x security-patched indefinitely | v1.x will eventually EOL; 2026-07-28 protocol clients cannot connect; no new SDK features | v1.x branch ongoing, v2 is stable |
| Migrate to v2.x (bump `mcp>=2.0.0`) | v2 supports 2026-07-28 clients; receives active development; no handshake latency | Requires: rewrite `to_mcp_server()` (decorators → constructor args), emit `resultType`/`CacheableResult` in tool results, handle new error codes | [Migration Guide](https://py.sdk.modelcontextprotocol.io/migration/), [Low-level Server docs](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/) |

## Version/compatibility notes

- **v2.0.0 stable released:** 2026-07-28 — [Release v2.0.0 · modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- **v1.28.1** (final v1 patch, 2026-07-27) — v1.x branch now in maintenance-only mode
- **MCP specification:** v2.0.0 SDK implements 2026-07-28 revision; backwards-compatible with 2024-11-05 and 2025-11-25 clients
- **Python 3.10+** — varco's 3.12/3.13 both supported
- **httpx2 dependency:** v2.0.0 pins `httpx2>=2.5.0` (replaces `httpx` + `httpx-sse`)

## Evidence gaps

- **Validation behavior change:** The old lowlevel `@server.call_tool()` decorator performed automatic JSON-schema input validation; v2 removes this. Whether varco should add its own schema validation on the v2 path is not addressed here — only noted that it is "removed" — [The low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).
- **Custom response formats:** Whether v2's `CacheableResult` requirement applies only to the five listed RPC methods (tools/list, etc.) or if tool execution results returned by `call_tool` also need `ttlMs`/`cacheScope` is not explicitly quoted in the sources; the changelog says "results returned by" those methods — implies the methods, not their inputs/outputs in other contexts. Needs confirmation in the spec itself.
- **Exact lowlevel server parameter types:** The migration guide shows `on_list_tools` and `on_call_tool` handlers, but the full signature of `CallToolRequestParams` and return type `CallToolResult` structure (does it auto-wrap `TextContent`?) is not quoted; fetched docs show the decorator-free pattern but not a complete before-and-after of the handler bodies. This is available in the official [Low-level Server - MCP Python SDK](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/) docs but not fully reproduced here.

## Librarian's note

**The evidence strongly favors a hard migration to v2.x over staying on v1.x**, for these reasons:

1. **Stability:** v2.0.0 is stable; v1.x is in security-fixes-only maintenance mode. varco should not commit to a deprecated SDK branch.
2. **Market timing:** The 2026-07-28 spec and v2.0.0 are shipping together by design; MCP clients written after this date will default to the stateless protocol. v1.x will gradually become incompatible with newer MCP clients.
3. **Change effort is localized:** The migration touches only `to_mcp_server()` in one file (mcp.py lines 706–789) — a ~80-line method. The `MCPToolDefinition` and `_to_mcp_tools()` helpers already exist and are compatible with v2; only handler registration changes.
4. **varco's tool list is pre-computed:** varco builds tools at adapter construction (lines 545–578), not at request time. This maps cleanly to v2's constructor `on_list_tools` parameter, not a step backward.

The decision is upstream — CLAUDE.md's role is to gather evidence, not to choose. However, this research indicates a v2 migration is lower-cost than staying on v1.x long-term, and the spec changes (resultType, CacheableResult, MRTR) are documented and implementable in isolated diffs.


## Experiment evidence (Plan 029 / N1a, Step 21) — resolves two "Evidence gaps" above

Run against the **actually installed** mcp 2.1.1 SDK (`uv run python3 -c "import mcp; ..."`),
not read from documentation. This directly resolves the open question in
`plans/029-idempotency-key-and-mcp-v2.md`'s Risks table ("does v2 default
`resultType`/`ttlMs`/`cacheScope`, or must varco emit them?") and the "Custom response formats"
evidence gap above.

**Finding 1 — `on_list_tools` is typed to require a `ListToolsResult`, not a bare `list[Tool]`.**
`inspect.signature(Server.__init__)` shows:
`on_list_tools: Callable[[ServerRequestContext[...], PaginatedRequestParams | None], Awaitable[types.ListToolsResult]] | None`.
A handler returning a bare list is not the contracted shape at all — `varco_fastapi`'s
`to_mcp_server()` must construct and return a `ListToolsResult`.

**Finding 2 — the SDK DOES default `ttl_ms`/`cache_scope`/`result_type` on construction.**
`inspect.getsource(types.CacheableResult)` shows the field defaults directly in source, with
the class docstring stating it outright: *"Both fields are required on the 2026-07-28 wire. The
SDK defaults to `ttl_ms=0` (immediately stale) and `cache_scope="private"` so a handler that
doesn't set them still produces a valid 2026-07-28 result without accidentally enabling shared
caching."* Confirmed by construction: `ListToolsResult(tools=[])` yields `ttl_ms=0`,
`cache_scope="private"`, `result_type="complete"` with zero fields supplied. So varco's only
remaining obligation is wrapping its tool list in a `ListToolsResult` — not synthesizing values
for fields the SDK would otherwise leave unset.

**Finding 3 — the Python attribute names are snake_case; camelCase exists only as a
*serialization* alias.** `ListToolsResult(tools=[]).model_dump(by_alias=True)` produces
`{'ttlMs': 0, 'cacheScope': 'private', ...}` (the wire shape), while the plain Python attributes
are `.ttl_ms`/`.cache_scope` — accessing `.cacheScope`/`.ttlMs` directly on an instance raises
`AttributeError` (`'ListToolsResult' object has no attribute 'cacheScope'. Did you mean:
'cache_scope'?`). This bit two of Plan 029's own red tests, which had assumed camelCase Python
attributes from the wire-key names quoted in the changelog/spec prose — both were corrected to
the real attribute names once this was caught by the tests actually failing against the real SDK
(not by inspection alone). This directly resolves "Exact lowlevel server parameter types" above:
the return type is `ListToolsResult`, constructed with `tools=`/`ttl_ms=`/`cache_scope=` kwargs.

**Result**: `varco_fastapi.router.mcp.MCPAdapter.to_mcp_server()`'s `_list_tools` handler
constructs `ListToolsResult(tools=mcp_tools, ttl_ms=self._ttl_ms, cache_scope="private")`, with a
new `MCPAdapter(..., ttl_ms=60_000)` constructor parameter (default 60 seconds — deliberately
not the SDK's own `0`/immediately-stale default, since varco's tool list is fixed at construction
time and gains nothing from disabling client-side caching).
