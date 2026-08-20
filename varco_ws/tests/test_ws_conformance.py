"""
Real-server ``varco_ws`` conformance — design resolution (Plan 012 / RT6,
Step 27; RT4, Steps 18-20).

DESIGN RESOLUTION (implementer decision, per the red-mode module docstring
this file previously carried): ``WebSocketEventBus``/``SSEEventBus``
(``varco_ws/varco_ws/websocket.py:355``, ``sse.py:175``) are **push
adapters** wrapping an ``AbstractEventBus`` — they do not themselves
implement ``AbstractEventBus.publish()``/``.subscribe()`` and cannot be
plugged into ``varco_conformance.event_bus.EventBusConformance`` as-is.

Chose option (a) from the original docstring: real-server WS/SSE coverage
lives as bespoke tests in ``test_ws_integration.py``/``test_sse_integration.py``
(against the ``running_server`` fixture from ``tests/conftest.py``), not as
a new, narrower conformance base class in ``testkit/varco_conformance/``.
Rationale — a push adapter's contract ("wraps a bus, forwards to connected
clients") is a one-package concern; adding a second, adapter-shaped
conformance base class for a contract only one backend package implements
would be more machinery than a directly-owning test module, without buying
back the "same test runs against N implementations" value a shared
conformance suite exists for (Non-goals: prefer the simpler option absent a
second implementation to prove the abstraction against).

No test classes live in this module — this file exists so the RT6 opt-in
list (Step 27) has a documented landing page pointing at where the real
coverage is, rather than a silently-missing file.
"""

from __future__ import annotations
