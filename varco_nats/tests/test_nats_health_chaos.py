"""
``NatsHealthCheck`` against a real, deliberately-broken NATS server
(Plan 018 / RT2, Step 11 — chaos tier).

``varco_nats/varco_nats/health.py`` opens a throw-away connection per
``check()`` and calls JetStream ``account_info()``. Its whole reason for
existing is the "down" half — a cached connection would report HEALTHY
against an unreachable server — and nothing verified that half against a
server that is genuinely unreachable.

Mechanism: ``docker pause`` (§RT7-shape). A paused container's processes
are frozen, so the connect attempt **black-holes** rather than getting a
fast RST from a closed port. That is strictly the harder failure mode and
the one ``NatsHealthCheck``'s ``asyncio.wait_for`` timeout exists for.

Container scope (§chaos-fixture): this module declares its **own**
module-scoped ``nats_container_chaos`` fixture rather than reusing the
session-scoped ``nats_url``. Pausing the session container would freeze it
under every other test in ``varco_nats/tests/``. The fixture is declared
here, never in ``conftest.py``, so no non-chaos test can accidentally
depend on a container that gets paused under it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from varco_chaos.containers import ChaosContainer
from varco_core.health import HealthStatus
from varco_nats.config import NatsEventBusSettings
from varco_nats.health import NatsHealthCheck

pytestmark = [pytest.mark.integration, pytest.mark.chaos]


@pytest.fixture(scope="module")
def nats_container_chaos() -> Iterator[ChaosContainer]:
    """
    A NATS container this module is allowed to break.

    Yields:
        A ``ChaosContainer`` wrapping a JetStream-enabled NATS server.

    Edge cases:
        - Module-scoped: every test here must leave the container healthy
          (``paused()`` unpauses in ``finally``), or the module's remaining
          tests fail confusingly. §chaos-fixture's named fallback is to drop
          this module to ``function`` scope, never to add cleanup cleverness
          to ``ChaosContainer``.
    """
    from testcontainers.nats import NatsContainer  # noqa: PLC0415

    with NatsContainer().with_command("-js") as container:
        _CHAOS_URL["nats"] = container.nats_uri()
        yield ChaosContainer(container, ready=lambda logs: "Server is ready" in logs)


# The connection URL is captured once, at boot. ``ChaosContainer.restart()``
# uses docker-py's ``restart()`` (never ``.stop()`` + ``.start()``), which
# preserves the container id AND its host port mapping — so a URL captured
# here stays valid across every chaos operation in this module.
_CHAOS_URL: dict[str, str] = {}


async def test_health_reports_unhealthy_while_the_server_is_paused_then_recovers(
    nats_container_chaos: ChaosContainer,
) -> None:
    """
    HEALTHY → (paused) UNHEALTHY → (unpaused + ready) HEALTHY.

    All three phases in one test on purpose: the recovery assertion is only
    meaningful if the same probe instance reported UNHEALTHY moments before,
    and the ``paused()`` block must be the only thing that changed.

    Edge cases:
        - A short ``timeout`` keeps the black-holed probe bounded; the
          default 5 s would make the paused phase needlessly slow.
    """
    chaos = nats_container_chaos
    settings = NatsEventBusSettings(servers=_CHAOS_URL["nats"])
    probe = NatsHealthCheck(settings, timeout=2.0)

    healthy_before = await probe.check()
    assert healthy_before.status is HealthStatus.HEALTHY, (
        f"baseline probe must be healthy, got {healthy_before}"
    )

    with chaos.paused():
        during = await probe.check()

    assert during.status is HealthStatus.UNHEALTHY, (
        "a paused (black-holed) NATS server must report UNHEALTHY — the probe "
        f"opens a throw-away connection precisely so it cannot go stale; got {during}"
    )
    assert during.component == "nats"

    chaos.wait_ready()
    healthy_after = await probe.check()
    assert healthy_after.status is HealthStatus.HEALTHY, (
        f"the probe must recover once the server is unpaused and ready; got {healthy_after}"
    )
