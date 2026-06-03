"""
tests.test_di
=============
Unit tests for varco_ws.di — DI bootstrap via scan.

Covers:
    container.scan("varco_ws")   — discovers @Singleton WebSocketEventBus and
                                   SSEEventBus automatically.

All tests use InMemoryEventBus — no real broker required.
"""

from __future__ import annotations

import pytest

from providify import Provider

from varco_core.event.base import AbstractEventBus
from varco_core.event.memory import InMemoryEventBus

from varco_ws.sse import SSEEventBus
from varco_ws.websocket import WebSocketEventBus


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_container_with_bus() -> tuple[object, InMemoryEventBus]:
    """
    Build a minimal providify DIContainer with an InMemoryEventBus registered
    as AbstractEventBus.  Returns (container, bus).
    """
    try:
        from providify import DIContainer
    except ImportError:
        pytest.skip("providify not installed — skipping DI tests")

    container = DIContainer()
    bus = InMemoryEventBus()

    # Register InMemoryEventBus as the AbstractEventBus implementation.
    # providify resolves the DI token from the return type annotation and
    # requires the @Provider decorator on the factory function.
    @Provider(singleton=True)
    def _bus_provider() -> AbstractEventBus:  # type: ignore[type-arg]
        return bus

    container.provide(_bus_provider)
    return container, bus


def _make_container_with_ws_scan() -> tuple[object, InMemoryEventBus]:
    """
    Build a container with InMemoryEventBus and scan varco_ws so both
    WebSocketEventBus and SSEEventBus are registered as @Singleton.

    Use this in tests that need DI-resolved adapters — the new scan-based API
    is the supported scan-based registration pattern.
    """
    container, bus = _make_container_with_bus()
    # scan() discovers both @Singleton adapters — replaces install(XConfiguration).
    container.scan("varco_ws", recursive=True)
    return container, bus


# ── Scan-based discovery tests ────────────────────────────────────────────────


def test_scan_provides_websocket_event_bus() -> None:
    """
    After scanning varco_ws, the container must resolve WebSocketEventBus.
    """
    container, _ = _make_container_with_ws_scan()

    ws_bus = container.get(WebSocketEventBus)
    assert isinstance(ws_bus, WebSocketEventBus)


def test_scan_websocket_wraps_registered_bus() -> None:
    """
    The WebSocketEventBus discovered by scan must wrap the AbstractEventBus
    that was registered in the container.
    """
    container, bus = _make_container_with_ws_scan()

    ws_bus = container.get(WebSocketEventBus)
    # Internal attribute _bus must be the registered InMemoryEventBus.
    assert ws_bus._bus is bus


def test_scan_websocket_singleton() -> None:
    """
    WebSocketEventBus must be a singleton — resolving it twice returns the
    same instance.
    """
    container, _ = _make_container_with_ws_scan()

    first = container.get(WebSocketEventBus)
    second = container.get(WebSocketEventBus)
    assert first is second


def test_scan_websocket_bus_not_started_after_scan() -> None:
    """
    The WebSocketEventBus must NOT be started automatically by the DI module.
    Callers must call start() explicitly in their lifespan handler.

    DESIGN: not starting automatically avoids an asyncio.Loop dependency at
    scan time — the container may be built synchronously before an event loop
    is running.
    """
    container, _ = _make_container_with_ws_scan()

    ws_bus = container.get(WebSocketEventBus)
    # Internal subscription handle is None when not started.
    assert ws_bus._subscription is None


def test_scan_provides_sse_event_bus() -> None:
    """
    After scanning varco_ws, the container must resolve SSEEventBus.
    """
    container, _ = _make_container_with_ws_scan()

    sse_bus = container.get(SSEEventBus)
    assert isinstance(sse_bus, SSEEventBus)


def test_scan_sse_wraps_registered_bus() -> None:
    """
    The SSEEventBus discovered by scan must wrap the AbstractEventBus
    that was registered in the container.
    """
    container, bus = _make_container_with_ws_scan()

    sse_bus = container.get(SSEEventBus)
    assert sse_bus._bus is bus


def test_scan_sse_singleton() -> None:
    """SSEEventBus must be a singleton."""
    container, _ = _make_container_with_ws_scan()

    first = container.get(SSEEventBus)
    second = container.get(SSEEventBus)
    assert first is second


def test_scan_sse_bus_not_started_after_scan() -> None:
    """
    The SSEEventBus must NOT be started automatically.  Callers start it
    in the FastAPI lifespan handler.
    """
    container, _ = _make_container_with_ws_scan()

    sse_bus = container.get(SSEEventBus)
    assert sse_bus._subscription is None


def test_scan_provides_both_adapters() -> None:
    """
    A single scan("varco_ws") must provide both WebSocketEventBus and SSEEventBus
    in the same container without conflicts — they provide different types.
    """
    container, _ = _make_container_with_ws_scan()

    ws_bus = container.get(WebSocketEventBus)
    sse_bus = container.get(SSEEventBus)

    # Different types — different instances.
    assert isinstance(ws_bus, WebSocketEventBus)
    assert isinstance(sse_bus, SSEEventBus)
    assert ws_bus is not sse_bus


async def test_ws_bus_is_functional_after_start() -> None:
    """
    The WebSocketEventBus obtained from the container must be fully functional
    after manually calling start().

    DESIGN: functional test to confirm the DI-provided adapter delivers events,
    not just that it was constructed correctly.
    """
    import asyncio

    from varco_core.event.base import Event

    class PingEvent(Event):
        __event_type__ = "test.ping"
        count: int = 0

    container, bus = _make_container_with_ws_scan()

    ws_bus = container.get(WebSocketEventBus)
    await ws_bus.start()

    class FakeWebSocket:
        sent: list[str] = []

        async def send_text(self, msg: str) -> None:
            self.sent.append(msg)

    fake_ws = FakeWebSocket()
    async with ws_bus.connect(fake_ws):
        await bus.publish(PingEvent(count=1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(fake_ws.sent) == 1

    await ws_bus.stop()


async def test_sse_bus_is_functional_after_start() -> None:
    """
    The SSEEventBus obtained from the container must be fully functional
    after manually calling start().
    """
    import asyncio

    from varco_core.event.base import Event

    class PongEvent(Event):
        __event_type__ = "test.pong"
        value: str = ""

    container, bus = _make_container_with_ws_scan()

    sse_bus = container.get(SSEEventBus)
    await sse_bus.start()

    async with sse_bus.subscribe() as conn:
        await bus.publish(PongEvent(value="hello"))
        await asyncio.sleep(0)

        message = await asyncio.wait_for(conn._queue.get(), timeout=1.0)
        assert "test.pong" in message
        assert "hello" in message

    await sse_bus.stop()
