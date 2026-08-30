# Research 003 — FastMCP `add_tool()` schema parameter support

Date: 2026-08-28 · Freshness matters: **yes** — MCP Python SDK v2.0.0 just shipped (July 28, 2026) with breaking changes; version pins matter.

## Question

Can varco pass a hand-written JSON Schema to `mcp.server.fastmcp.FastMCP.add_tool()` instead of relying on Python type-hint derivation? What is the current real signature, and what is the sanctioned way to register tools with custom schemas?

## Findings

- **FastMCP.add_tool() (v1.x, current stable in most codebases) does NOT accept an `input_schema` parameter.** The method signature is `add_tool(fn, name=None, description=None)`. Schemas are inferred only from Python function type hints using introspection. — [MCP Python SDK v1.27.0 release notes, April 2, 2026](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.27.0); [MCP Python SDK documentation - Tools](https://py.sdk.modelcontextprotocol.io/servers/tools/); [GitHub issue #761 filed May 20, 2025 requesting this feature](https://github.com/modelcontextprotocol/python-sdk/issues/761), still closed without resolution in v1.x.

- **The low-level Server API (both v1.x and v2.x) DOES support hand-written schemas via `Tool` objects.** You construct a `mcp.types.Tool(name=..., description=..., input_schema={...})` with a plain dict JSON Schema, then register it via the low-level handler decorators (`@server.list_tools()` returns `ListToolsResult(tools=[...])`, `@server.call_tool()` executes). This avoids FastMCP's type-hint derivation entirely. — [MCP Python SDK - The low-level Server documentation (both v1 and v2)](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/); code example shows: `Tool(name="search_books", description="...", input_schema={"type": "object", "properties": {...}, "required": [...]})`. Schema dialects follow JSON Schema 2020-12 by default.

- **Critical caveat: v2.0.0 (released July 28, 2026, current stable) removed the low-level `@server.list_tools()` / `@server.call_tool()` decorators entirely.** v2 replaced them with constructor-based handler registration: `Server(list_tools_handler=..., call_tool_handler=...)`. The handler shape changed to `async (ctx, params) -> result`, and these handlers receive unvalidated `params.arguments` (schemas are advertised but not validated). — [MCP Python SDK v2.0.0 release notes](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0); [Migration guide v1 to v2 — The low-level Server section](https://py.sdk.modelcontextprotocol.io/migration/); [Pydantic blog — MCP Python SDK v2 beta summary](https://pydantic.dev/articles/mcp-python-sdk-v2-beta).

- **In v2, the high-level MCPServer (renamed from FastMCP) retains the decorator API: `@mcp.tool()` decorator signatures remain unchanged**, but schema derivation from type hints is still the only path; no `input_schema=` parameter was added. — [MCP Python SDK v2 — Whats new](https://py.sdk.modelcontextprotocol.io/whats-new/); [Migration guide — "Decorators stay the same"](https://py.sdk.modelcontextprotocol.io/migration/).

- **Breaking change in field naming v1 → v2: `inputSchema` → `input_schema`.** All MCP protocol fields moved to snake_case. But v2 `Tool` constructor accepts both spellings at construction time; only `model_dump(by_alias=True)` produces the wire-format camelCase. — [Migration guide — Type system changes](https://py.sdk.modelcontextprotocol.io/migration/).

- **Structured output (`output_schema`) was added in the protocol; v1.29.1 (Aug 25, 2026) added "recursive tool return types get an object-rooted output schema" support.** v2 makes schemas advertised-only (no automatic validation); handlers must validate manually. — [v1.29.1 release notes](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.29.1).

- **Stable version and compatibility:** Current stable is v2.1.1 (Aug 25, 2026). v1.x is in maintenance-mode-only (critical bug fixes and security patches only; v1.29.1 is the last v1 release). Python ≥3.10 required. — [PyPI mcp package page](https://pypi.org/project/mcp/); [GitHub releases](https://github.com/modelcontextprotocol/python-sdk/releases).

- **The separate jlowin FastMCP (PrefectHQ/fastmcp) is unrelated.** It reached v3.0 GA (Feb 18, 2026) independently; the official SDK's bundled `mcp.server.fastmcp.FastMCP` is the vendored v1.0 of that project from 2024. The two diverged significantly and are NOT interchangeable. — [GitHub issue #1362 — mcp.server.fastmcp vs fastmcp](https://github.com/modelcontextprotocol/python-sdk/issues/1362); [PrefectHQ/fastmcp releases](https://github.com/PrefectHQ/fastmcp/releases); [MCP.directory — FastMCP vs FastAPI-MCP vs Python SDK comparison](https://mcp.directory/blog/fastmcp-vs-fastapi-mcp-vs-python-sdk-2026).

## Options compared

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|---|---|---|---|
| **Keep v1.x, drop to low-level Server API** | Works today; low-level Server is stable in v1.27+; tool objects accept raw `input_schema` dicts; full schema control; no type-hint coupling. | Low-level API is more verbose; no automatic validation/serialization; maintainers recommend v1.x receive only security patches after v2.0.0; eventual migration cost. | [Low-level Server docs](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/); [v1.x maintenance timeline](https://github.com/modelcontextprotocol/python-sdk/releases). |
| **Dynamically generate Python handler from the JSON Schema** | Works in v1.x and v2.x; high-level decorator API stays simple; automatic validation in v1.x (lost in v2). | Fragile; no robust off-the-shelf tool (Pydantic dynamic models exist but are finicky); post-processing/monkey-patching Tool objects is version-brittle; duplicates metadata already in the schema. | [Issue #761 notes this workaround isn't reliable](https://github.com/modelcontextprotocol/python-sdk/issues/761); [Pydantic dynamic model patterns are undocumented](https://docs.pydantic.dev). |
| **Migrate to v2 + use low-level constructor-based API** | Future-proof; v2 is stable and current; low-level API still supports custom schemas via Tool objects; snake_case field names align with modern Python conventions. | High migration cost; **@server decorators removed, must rewrite all handlers**; v2 lost automatic validation (manual validation required); protocol changed to stateless (more complex state management). | [v2.0.0 release notes](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0); [Migration guide](https://py.sdk.modelcontextprotocol.io/migration/). |
| **Pin `mcp>=1.28.1,<2` and request feature via issue/PR** | Unblocks current xfail; buys time; official SDK could add `input_schema=` parameter to MCPServer.add_tool() eventually (issue #761 is watched). | Dependent on SDK maintainers' roadmap; no timeline; issue #761 has been open since May 2025 with no movement; other projects (IBM/mcp-context-forge, Zuplo) facing same blocker. | [Issue #761 status](https://github.com/modelcontextprotocol/python-sdk/issues/761); [Zuplo error doc](https://zuplo.com/learn/mcp/errors/no-module-named-mcp-server-fastmcp); [IBM issue #5839](https://github.com/IBM/mcp-context-forge/issues/5839). |

## Version/compatibility notes

- **v1.x:** Latest is v1.29.1 (Aug 24, 2026). Stable, maintenance-only (security fixes). `mcp.server.fastmcp.FastMCP` bundled with vendored FastMCP 1.0 from 2024. Low-level Server API stable and supports custom schemas.
- **v2.x:** Latest is v2.1.1 (Aug 25, 2026). Stable, actively developed. `FastMCP` renamed to `MCPServer`; moved to `mcp.server.mcpserver`. Low-level API rebuilt: decorators removed, constructor-based handler registration. Field names: camelCase → snake_case. Protocol upgraded to stateless (2026-07-28 spec).
- **Recommended pinning:** Official guidance is `mcp>=1.28.1,<2` for v1.x users staying put (prevents auto-upgrade to v2 on fresh installs). Varco's `mcp>=1.0` (no upper bound) is unsafe — it will auto-upgrade to v2.1.1 on first install after 2026-07-28, breaking any v1-dependent code.
- **Python version:** Both v1.29.1 and v2.1.1 require Python ≥3.10.
- **End-of-life:** v1.x receives only critical fixes; no new features. No announced end-of-life date, but migration to v2 strongly encouraged by maintainers.

## Evidence gaps

- No publicly available comment from MCP maintainers on issue #761 explaining why it remains unresolved or if `input_schema=` parameter is planned for MCPServer. (The issue is closed but no resolution comment visible in search results — GitHub may require authentication to view.)
- No performance or correctness comparison between low-level Server custom-schema approach vs. synthesized Pydantic models for handler type hints (both untested in varco's context).
- The separate jlowin/FastMCP v3.0 project's stance on custom schemas unclear from search results; worth investigating if varco migrates off the bundled SDK version.

## Librarian's note

The evidence strongly favours **dropping to the low-level Server API in v1.x** as the immediate fix for BACKLOG KI-11:

1. **It works now** — `Tool(name=..., input_schema={...})` is a stable, documented v1.x path (and still exists in v2, just with different handler registration).
2. **Schema control is complete** — varco already has the JSON Schema dict from `_tool.input_schema`; the low-level API accepts it as-is, no synthesis or post-processing.
3. **It buys time without lock-in** — If MCP maintainers eventually add `input_schema=` to MCPServer.add_tool() (issue #761), varco can migrate back to high-level decorators. If not, the low-level approach is portable across both v1 and v2.
4. **`mcp>=1.0` pinning is a risk** — Should be narrowed to `mcp>=1.28.1,<2` immediately to prevent accidental v2 upgrade on next pip install, which would lose @server decorators entirely and force a larger rewrite.

An optional longer-term path: contribute `input_schema=` parameter support to MCPServer.add_tool() upstream (issue #761 is the tracking vehicle), or adopt the independent jlowin/fastmcp v3.0 if it supports the feature — but neither blocks the immediate xfail removal.
