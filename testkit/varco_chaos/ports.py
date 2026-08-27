"""
``reserve_host_port`` — pick a free ephemeral TCP port for a testcontainers
``with_bind_ports`` pin (Plan 019 / §RT7b-port, Step 26).

Only needed by chaos modules that must pin a container's host port because
the containerized process bakes its own advertised address into an on-disk
artifact at first boot (Kafka's ``tc-start.sh`` — see
``kafka_container_chaos``'s fixture docstring in ``test_kafka_chaos.py`` for
the full finding). Every other restart-based chaos container re-queries its
port fresh via ``ChaosContainer.url`` instead — see ``containers.py``'s
DESIGN block for why re-querying alone is the default and pinning is the
exception.
"""

from __future__ import annotations

import socket


def reserve_host_port() -> int:
    """
    Reserve a free ephemeral TCP port on the loopback interface.

    Binds a throwaway socket to port 0 (asking the OS kernel to allocate a
    free ephemeral port), reads back the port the kernel chose, then closes
    the socket immediately so the port is free again for docker to bind.

    Returns:
        A TCP port number that was free at the moment this function
        returned.

    Edge cases:
        - **TOCTOU window** (§Risks): the port is free *at reservation time*,
          not *guaranteed* free at the later ``docker run``/``with_bind_ports``
          call — another process (or another concurrent test session) could
          grab it in between. A collision surfaces as a **container-start
          failure** (loud, immediate), never as a mid-test connection flake,
          because docker itself refuses to bind an already-used host port.
        - The window is small (microseconds to the container-start call) and
          this helper is only used for a single pinned port per test session
          (Kafka's chaos fixture), keeping the collision probability low in
          practice.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        return probe.getsockname()[1]


__all__ = ["reserve_host_port"]
