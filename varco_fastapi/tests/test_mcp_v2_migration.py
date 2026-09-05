"""
Red-mode tests for Plan 029 / N1 — the MCP Python SDK v1 -> v2 migration.

Covers plan Step 21/23: the v2 handler shape (``Server(name=...,
on_list_tools=..., on_call_tool=...)``, ``ctx`` first arg,
``CallToolRequestParams``), and the experimental ``tools/list`` result-shape
open question (§D-N1-parity).

``to_mcp_server()`` today (pre-migration) builds a
``mcp.server.lowlevel.Server`` and registers handlers via
``@server.list_tools()``/``@server.call_tool()`` decorators taking
``(name, arguments)`` — it never calls ``mcp.server.Server`` with
``on_list_tools=``/``on_call_tool=`` kwargs. Every test below patches
``mcp.server.Server`` and asserts it WAS constructed that way, so they fail
today with a clean ``AssertionError`` ("Expected ... to have been called")
rather than a fixture error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from varco_fastapi.router.base import VarcoRouter
from varco_fastapi.router.mcp import MCPAdapter
from varco_fastapi.router.mixins import CreateMixin, ReadMixin


class WidgetCreate(BaseModel):
    name: str


class WidgetRead(BaseModel):
    id: int
    name: str


class WidgetRouter(CreateMixin, ReadMixin, VarcoRouter):
    _prefix = "/widgets"
    _create_mcp = True
    _read_mcp = True


@pytest.fixture
def adapter() -> MCPAdapter:
    client = AsyncMock()
    return MCPAdapter(WidgetRouter, client=client)


@pytest.fixture
def patched_server(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch the v2 constructor import site: ``from mcp.server import Server``."""
    mock_cls = MagicMock()
    mock_cls.return_value = MagicMock(name="server-instance")
    monkeypatch.setattr("mcp.server.Server", mock_cls, raising=False)
    return mock_cls


async def test_to_mcp_server_constructs_v2_server_with_on_list_tools_and_on_call_tool(
    adapter: MCPAdapter, patched_server: MagicMock
) -> None:
    adapter.to_mcp_server()

    patched_server.assert_called_once()
    _, kwargs = patched_server.call_args
    assert "on_list_tools" in kwargs
    assert "on_call_tool" in kwargs
    assert callable(kwargs["on_list_tools"])
    assert callable(kwargs["on_call_tool"])


async def test_on_list_tools_handler_takes_ctx_as_first_argument(
    adapter: MCPAdapter, patched_server: MagicMock
) -> None:
    adapter.to_mcp_server()
    _, kwargs = patched_server.call_args
    on_list_tools = kwargs["on_list_tools"]

    # v2 handler shape: ctx is the first positional argument (brief 003 §2).
    #
    # NOTE (Plan 029 / N1a, Step 21 fix): the original assertion here
    # (`isinstance(result, list)`) assumed `on_list_tools` returns a bare
    # `list[Tool]`. Verified false against the installed mcp 2.1.1 SDK:
    # `inspect.signature(Server.__init__)` types `on_list_tools` as
    # `Callable[..., Awaitable[types.ListToolsResult]]` — a bare list is not
    # the contracted return shape at all (§D-N1-parity's experiment, resolved
    # by running this exact assertion and observing the real
    # `AssertionError: assert False` it produced). `result.tools` is the
    # actual list of `Tool` objects; this file's own
    # `test_tools_list_result_carries_required_v2_fields_or_bare_list` below
    # already covers the `ListToolsResult`-vs-bare-list branch correctly.
    result = await on_list_tools(None)
    from mcp.types import ListToolsResult

    assert isinstance(result, ListToolsResult)
    assert (
        any(tool.name.endswith("create_widget") for tool in result.tools) or len(result.tools) > 0
    )


async def test_on_call_tool_handler_receives_call_tool_request_params(
    adapter: MCPAdapter, patched_server: MagicMock
) -> None:
    from mcp.types import CallToolRequestParams

    adapter.to_mcp_server()
    _, kwargs = patched_server.call_args
    on_call_tool = kwargs["on_call_tool"]

    adapter._client.request = AsyncMock(return_value={"id": 1, "name": "gadget"})  # type: ignore[attr-defined]

    tool_name = adapter.tools[0].name
    params = CallToolRequestParams(name=tool_name, arguments={"name": "gadget"})

    # v2 handler shape: ctx first, then a CallToolRequestParams object — NOT
    # (name, arguments) positionals.
    result = await on_call_tool(None, params)
    assert result is not None


def test_type_ignore_untyped_decorator_suppressions_are_removed() -> None:
    # §D-N1-pin: "Deletes two `# type: ignore[untyped-decorator]`
    # suppressions" — the v2 constructor-kwarg style has no decorators to
    # suppress against.
    import inspect

    from varco_fastapi.router import mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "type: ignore[untyped-decorator]" not in source


# ── §D-N1-parity: the tools/list result-shape experiment (Step 21) ──────────


async def test_tools_list_result_carries_required_v2_fields_or_bare_list(
    adapter: MCPAdapter, patched_server: MagicMock
) -> None:
    """
    §D-N1-parity's open question, resolved by experiment: if the installed
    SDK requires ``ttlMs``/``cacheScope`` on a list-shaped result and does not
    default them, ``on_list_tools`` must return a ``ListToolsResult`` carrying
    ``cacheScope="private"`` and a configurable ``ttlMs`` (default 60_000)
    rather than a bare ``list[Tool]``.

    NOTE (Plan 029 / N1a, Step 21 fix): the original assertions here used
    camelCase attribute access (``result.cacheScope``/``result.ttlMs``),
    matching the *wire* JSON key names the 2026-07-28 spec uses. Verified
    false against the installed mcp 2.1.1 SDK — the actual runtime error
    was ``AttributeError: 'ListToolsResult' object has no attribute
    'cacheScope'. Did you mean: 'cache_scope'?`` — pydantic exposes the
    Python-facing attributes as snake_case (``cache_scope``/``ttl_ms``) and
    only serializes them as camelCase via ``model_dump(by_alias=True)``
    (confirmed via ``ListToolsResult(tools=[]).model_dump(by_alias=True)``
    → ``{'ttlMs': 0, 'cacheScope': 'private', ...}``). Fixed to the real
    attribute names.
    """
    from mcp.types import ListToolsResult

    adapter.to_mcp_server()
    _, kwargs = patched_server.call_args
    on_list_tools = kwargs["on_list_tools"]

    result = await on_list_tools(None)

    if isinstance(result, ListToolsResult):
        assert result.cache_scope == "private"
        assert result.ttl_ms == 60_000
    else:
        # Bare list is only acceptable if the SDK defaults the required
        # fields — this branch documents that as the fallback shape.
        assert isinstance(result, list)


async def test_mcp_adapter_exposes_ttl_ms_parameter() -> None:
    client = AsyncMock()
    adapter = MCPAdapter(WidgetRouter, client=client, ttl_ms=120_000)
    assert adapter._ttl_ms == 120_000  # type: ignore[attr-defined]
