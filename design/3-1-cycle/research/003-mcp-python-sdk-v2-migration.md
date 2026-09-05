# Research 003 — MCP Python SDK v2 migration surface (2026)
Date: 2026-09-03 · Freshness matters: **yes** — MCP v2 is shipping now; varco's current lowlevel-API wrapper will need migration in this cycle.

## Question
What is the migration path from MCP Python SDK v1 (current varco pin: `mcp>=1.28.1,<2`) to v2? 
- When did v2 go stable, and what are the exact lowlevel server API breaking changes?
- Does v2 remove `@server.list_tools()` and `@server.call_tool()` decorators?
- How does varco's router-to-MCP wrapper adapt to the v2 constructor-based handler shape?
- Does v2 address the explicit `inputSchema` registration problem (issue #772 vs #761)?
- How does v2 expose an ASGI app for FastAPI mounting?
- What is the commitment cost of `mcp>=2,<3`?

## Findings

### 1. v2.0.0 Release Date and N1 Claim Verification
- **v2.0.0 stable released 2026-07-28** — [modelcontextprotocol/python-sdk release v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0) ✓ Claim **verified**
- Latest stable is **2.1.1** (2026-08-25) — [PyPI mcp package](https://pypi.org/project/mcp/)
- v1.x is in **maintenance mode only** on the `v1.x` branch; receives security fixes only — [py.sdk.modelcontextprotocol.io v1 docs](https://py.sdk.modelcontextprotocol.io/v1/)
- BACKLOG claim that "v2 removes lowlevel decorators entirely" is **verified, with nuance**: decorators are replaced with constructor parameters, not removed from existence

### 2. Lowlevel Server API Breaking Changes — Decorator to Constructor Pattern

**V1 shape (deprecated):**
```python
server = Server()

@server.list_tools()
async def handle_list_tools():
    return ListToolsResult(tools=[...])

@server.call_tool(name="tool_name")
async def handle_call_tool(arguments):
    return CallToolResult(content=[...])
```

**V2 shape (required):**
```python
async def handle_list_tools(ctx, request):
    return ListToolsResult(tools=[...])

async def handle_call_tool(ctx, request):
    return CallToolResult(content=[...])

server = Server(
    "server-name",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool
)
```

**Key differences:**
- Handlers are **constructor parameters** (`on_list_tools=`, `on_call_tool=`), not decorators — [py.sdk.modelcontextprotocol.io migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- Handler signature **changed**: all handlers now receive `(ctx, params)` and return typed result objects — the context is now an explicit first parameter instead of ambient `server.request_context`
- **Return value wrapping removed** — bare lists/dicts no longer auto-wrap; you must return the correct result type (`ListToolsResult`, `CallToolResult`, etc.) or validation fails
- **Automatic validation removed** — the old decorator auto-wrapped exceptions into `CallToolResult(is_error=True)`; now exceptions propagate and you must handle them in the handler or they become 500s
- Class renamed: `FastMCP` → `MCPServer` (different module path too) — [py.sdk.modelcontextprotocol.io whats-new](https://py.sdk.modelcontextprotocol.io/whats-new/)

**Varco impact**: The current `varco_fastapi/varco_fastapi/router/mcp.py` wraps the lowlevel decorator API to dynamically build a tool list from routers. The wrapper must be refactored to:
1. Collect all route metadata at Server construction time (not via decorators at registration time)
2. Wire a single `on_list_tools` handler that returns the full list
3. Wire a single `on_call_tool` handler that dispatches by tool name and marshals parameters
4. Catch and wrap handler exceptions back into `CallToolResult(is_error=True)`

### 3. Migration Guide Availability and Scope
- **Official migration guide exists** at [py.sdk.modelcontextprotocol.io/migration/](https://py.sdk.modelcontextprotocol.io/migration/)
- Covers "every breaking change with before-and-after code"
- Key sections: lowlevel Server (decorator→constructor), Client API (nested layers consolidated), type system (snake_case Python attributes, camelCase wire format), protocol evolution (handshake/session ID removal), and deprecated capabilities (`roots`, `sampling`, `logging`)
- **Varco-specific steps** not in the guide:
  1. Collect route metadata at bootstrap time, not at call time
  2. Build `ListToolsResult` once, pass to Server constructor
  3. Implement `on_call_tool` to dispatch and marshal Pydantic DTO ↔ JSON arguments
  4. Preserve error mapping from varco exceptions to MCP error structures

### 4. inputSchema Support — Issue #772 vs #761 Status

**Issue #772** ("No support of inputSchema in mcp.tool decorator") — **CLOSED, not fixed** — [github.com issue #772](https://github.com/modelcontextprotocol/python-sdk/issues/772)
- Reported 2025-05-21: `@mcp.tool(inputSchema={...})` raised `TypeError: got an unexpected keyword argument`
- Status: Closed, but no explicit inputSchema parameter was added to the decorator API in v2
- v2 generates `input_schema` (snake_case) **from function signatures only**; no override mechanism exposed

**Issue #761** ("Allow Direct inputSchema Registration for Tools") — **OPEN** — [github.com issue #761](https://github.com/modelcontextprotocol/python-sdk/issues/761)
- Broader enterprise request (opened 2025-05-20): support explicit Tool registration with pre-built OpenAPI/Swagger schemas
- Use case: API gateways that already have JSON Schema specs and need to register them as tools without writing Python functions
- Status: Still open; the underlying limitation persists in v2
- **Workaround in v2**: Build Tool objects manually (not via decorator), then call `server.set_tools(tools)` if that method exists, OR manually construct the Tool/inputSchema and return it from `on_list_tools`

**Varco implication**: Varco's current dynamic schema generation from Pydantic DTOs works, but if varco needs to override the inferred schema (e.g., for a field's example values or custom format), there is no documented API for it. The workaround is to build the `Tool` object in `on_list_tools` with a manually-constructed `input_schema` dict, not relying on the decorator to infer it.

**Wire format caveat**: v2 uses snake_case for Python attributes (`tool.input_schema`, not `inputSchema`). When sending over the wire, use `tool.model_dump(by_alias=True, mode="json")` to get the correct camelCase JSON — [py.sdk.modelcontextprotocol.io whats-new](https://py.sdk.modelcontextprotocol.io/whats-new/)

### 5. ASGI Mounting for FastAPI Integration

**v1 shape:** SSE transport via `sseserver_asgi.SSEServerASGI`

**v2 shape:** StreamableHTTP is the recommended production transport, superseding SSE — [blog.cloudflare.com streamable-http](https://blog.cloudflare.com/streamable-http-mcp-servers-python/)
- Server method `streamable_http_app()` returns an ASGI application — [py.sdk.modelcontextprotocol.io migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- Default path: `/mcp` (customizable)
- FastAPI mounting:
  ```python
  from mcp.server import MCPServer
  from fastapi import FastAPI
  
  mcp_server = MCPServer("my-server")
  app = FastAPI()
  app.mount("/mcp", mcp_server.streamable_http_app())
  ```
- Starlette / Async ASGI mounting:
  ```python
  from starlette.applications import Starlette
  from starlette.routing import Mount
  
  app = Starlette(
      routes=[Mount("/mcp", app=mcp.streamable_http_app())]
  )
  ```
- Serverless deployment: Returns an ASGI callable deployable to AWS Lambda (via Mangum), Cloud Run, Fly, or any container runtime

**Outstanding issue**: [github.com issue #1484](https://github.com/modelcontextprotocol/python-sdk/issues/1484) and [#1367](https://github.com/modelcontextprotocol/python-sdk/issues/1367) document mounting problems with v2's streamable_http_app on existing FastAPI apps; the issues suggest the API or documentation may still have gaps. **Verify with a spike** before committing to v2.

### 6. Compatibility and Risk Assessment

#### Version Support
- **Minimum Python: 3.10+** — [PyPI mcp](https://pypi.org/project/mcp/)
- **Latest stable: 2.1.1** (2026-08-25)
- **v1.x branch**: maintenance mode, security fixes only, documented separately

#### Breaking Changes (Selection)
- `FastMCP` → `MCPServer` class rename and module move
- All type attributes: snake_case in Python, camelCase on wire (requires `model_dump(by_alias=True)` for serialization)
- `mcp-types` package split — types moved out of main SDK
- New hard dependency: `opentelemetry-api` (for trace propagation via `_meta` envelope)
- Handler signature completely changed: `async (ctx, params) → result`
- Removal of auto-wrapping and auto-validation
- Deprecated capabilities removed: `roots`, `sampling`, `logging` (marked `[Obsolete]` with replacements)
- Custom transports and EventStore: new interface contracts
- `RequestParams.Meta` nested class → top-level `RequestParamsMeta` TypedDict

#### v1 and v2 Coexistence
- **Cannot coexist** — `pip install mcp` now installs 2.x
- Pin requirement: `mcp>=1.28.1,<2` stays on v1; `mcp>=2,<3` commits to v2
- No forward/backward compatibility shim; a project must pick one and migrate all its code
- Migration must be all-or-nothing within a single `mcp` dependency

#### Release Cadence
- v1.x: monthly point releases; receives security fixes only
- v2.x: active development, monthly/bi-weekly cadence based on spec evolution (next spec update: 2026-10-??)
- Stability signal: v2.0.0 stable (not alpha/beta); declared "major rework" to fix long-standing issues, suggests maturity

#### Commitment Cost: `mcp>=2,<3`
✅ **Strengths:**
- v2 fixes architectural debt: decorator→constructor improves composability, removes ambiguous state (`server.request_context`)
- StreamableHTTP transport better than SSE for production (lower overhead, cleaner semantics)
- OpenTelemetry tracing built-in (vs optional plugin in v1)
- Better type safety: snake_case Python + wire format clarity

❌ **Weaknesses:**
- Complete lowlevel API rewrite; varco's wrapper requires substantial refactoring
- No explicit inputSchema override (issue #761 still open) — dynamic schema generation from Pydantic is your only option
- Mounting to existing FastAPI apps has known issues (#1484, #1367) — likely fixable but not yet resolved in 2.1.1
- OpenTelemetry-api is now a hard dependency, affecting varco's own dependency footprint
- v2 protocol supports 2026-07-28 revision; if clients are on earlier revisions, compatibility is server-side only (v2 server can answer both), not client-side

---

## Version/Compatibility Notes

| Component | v1 Status | v2 Status | Varco Implication |
|---|---|---|---|
| **Lowlevel Server decorators** | Stable | Removed, replaced with constructor params | Wrapper rewrite needed |
| **StreamableHTTP ASGI** | SSE-based | Native, Streamable HTTP default | Mounting may have gaps (see #1484) |
| **inputSchema override** | Not exposed | Not exposed | Schema from Pydantic signatures only; manual Tool construction workaround |
| **Python version** | 3.8+ | 3.10+ | Varco's minimum floor rises to 3.10 if adopting v2 |
| **OpenTelemetry** | Optional plugin | Hard dependency | Dependency footprint change |
| **v1 maintenance** | Active | N/A | v1.x branch on security-fix-only mode |

---

## Evidence Gaps

1. **Mounting issues (#1484, #1367)** — The GitHub issues describe problems mounting v2's `streamable_http_app()` to existing FastAPI apps. The status (pending fix, workaround available, or closed) cannot be confirmed from the search results alone. **Recommendation**: file a spike with a minimal FastAPI + MCP v2 mount test before greenlight.

2. **inputSchema workaround completeness** — The manual `Tool` construction workaround for #761 is inferred but not documented in the official migration guide. Verify that building `Tool(name=..., input_schema={...})` and returning it from `on_list_tools` actually works as a production path.

3. **Full list of breaking changes** — The migration guide at py.sdk.modelcontextprotocol.io/migration/ exists, but its full scope is not fetched. A detailed review of that page is needed for the complete varco refactoring scope (e.g., exception handling, context shape, result validation rules).

4. **v2 e2e maturity signal** — v2.0.0 shipped 2026-07-28 and is now v2.1.1 (5 weeks of patches). No data on bug severity/frequency; "major rework" suggests caution. **Recommendation**: sample production adoption metrics (GitHub issues, community reports) if available.

5. **Varco's current MCP wrapper scope** — `varco_fastapi/router/mcp.py` line count, current test coverage, and dependency on v1 patterns are not reviewed. The actual refactoring burden cannot be estimated without reviewing that module.

---

## Librarian's Note

**The sources indicate**: v2 is a true breaking release with a rewritten lowlevel API, shipped production-stable on 2026-07-28, and is the forward path (v1 is maintenance-only). Varco's wrapper **must migrate**, but three risks block a clean yes-or-no recommendation:

1. **Mounting to FastAPI has known issues** (#1484, #1367) — the core integration point for varco is not confirmed working in 2.1.1. A spike test is mandatory before any commit.

2. **inputSchema override still impossible** (issue #761 open) — varco's dynamic schema generation from Pydantic works, but if override is needed, the workaround (manual `Tool` construction) is not documented. Test that path too.

3. **Full refactoring scope unknown** — the actual lines of code affected in `varco_fastapi/router/mcp.py` are not reviewed. The effort could be small (swap decorator for constructor params) or large (reshape how metadata flows), and the decision depends on that review.

**Recommendation**: File a spike (1–2 days):
- Clone MCP v2.0.0+, attempt a minimal FastAPI mount and verify `streamable_http_app()` works end-to-end
- Review varco's current MCP wrapper and estimate the refactoring lines
- Test manual `Tool` construction with custom `input_schema` to confirm #761 workaround works
- If all three pass, greenlight varco's v2 migration; otherwise, negotiate scope or timeline with product

---

## Sources

- [modelcontextprotocol/python-sdk Release v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- [PyPI mcp package](https://pypi.org/project/mcp/)
- [MCP Python SDK v2 Migration Guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [MCP Python SDK What's New in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [MCP Python SDK v1 Documentation](https://py.sdk.modelcontextprotocol.io/v1/)
- [GitHub Issue #772: No support of inputSchema in mcp.tool decorator](https://github.com/modelcontextprotocol/python-sdk/issues/772)
- [GitHub Issue #761: Allow Direct inputSchema Registration for Tools](https://github.com/modelcontextprotocol/python-sdk/issues/761)
- [GitHub Issue #1484: Documentation on mounting to existing ASGI server is missing information](https://github.com/modelcontextprotocol/python-sdk/issues/1484)
- [GitHub Issue #1367: Mounting a Streamable HTTP MCP endpoint on existing FastAPI app does not work](https://github.com/modelcontextprotocol/python-sdk/issues/1367)
- [Cloudflare Blog: Streamable HTTP MCP Servers in Python](https://blog.cloudflare.com/streamable-http-mcp-servers-python/)
- [MCP Python SDK v2 Beta - Pydantic Dev](https://pydantic.dev/articles/mcp-python-sdk-v2-beta)
- [MCP Blog: SDK Betas for the 2026-07-28 MCP Spec Release Candidate](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/)
