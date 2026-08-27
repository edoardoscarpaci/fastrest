"""
Unit tests for ``ChaosContainer.url`` (Plan 019 / §RT7b-port, Step 28).

No Docker required — ``FakeWrappedContainer``/``FakeDockerContainer`` are
hand-rolled doubles standing in for testcontainers' ``DockerContainer`` +
docker-py's ``Container``, exercising only ``ChaosContainer``'s own logic:
the ``url`` property must re-derive from ``url_factory`` on **every**
access (never memoised), and a ``ChaosContainer`` built without a
``url_factory`` must fail loudly with ``ValueError`` rather than silently
returning a stale/empty value.

These tests are expected to fail today: ``ChaosContainer`` has no
``url_factory`` parameter and no ``url`` property yet (Plan 019 Step 28's
production change).
"""

from __future__ import annotations

import pytest
from varco_chaos.containers import ChaosContainer


class FakeWrappedContainer:
    """
    Stand-in for docker-py's ``Container`` — records restart() calls and
    appends fresh "ready" log output on each restart, mirroring a real
    container's boot sequence emitting a new readiness line every time it
    comes back up (what ``ChaosContainer.wait_ready()``'s log-offset design
    actually waits for).
    """

    def __init__(self, owner: FakeDockerContainer) -> None:
        self._owner = owner
        self.restart_calls = 0

    def restart(self, timeout: int = 5) -> None:
        self.restart_calls += 1
        self._owner._logs = (  # noqa: SLF001 — cooperating test double
            self._owner._logs[0] + b"ready\n",
            self._owner._logs[1],
        )

    def pause(self) -> None:
        pass

    def unpause(self) -> None:
        pass


class FakeDockerContainer:
    """
    Stand-in for testcontainers' ``DockerContainer``.

    ``port`` simulates the ephemeral host port Docker hands back — bumped by
    the test itself (never by this fake) to simulate a restart-triggered
    remap, mirroring research 006 §A's documented behaviour.
    """

    def __init__(self) -> None:
        self.port = 40000
        self._wrapped = FakeWrappedContainer(self)
        self._logs: tuple[bytes, bytes] = (b"ready\n", b"")

    def get_logs(self) -> tuple[bytes, bytes]:
        return self._logs

    def get_wrapped_container(self) -> FakeWrappedContainer:
        return self._wrapped

    def get_exposed_port(self, _internal: int) -> int:
        return self.port


def _url_from(container: FakeDockerContainer) -> str:
    return f"postgresql://localhost:{container.port}/db"


class TestChaosContainerUrlProperty:
    def test_url_raises_value_error_without_url_factory(self) -> None:
        fake = FakeDockerContainer()
        chaos = ChaosContainer(fake, ready=lambda logs: "ready" in logs)

        with pytest.raises(ValueError, match="url_factory"):
            _ = chaos.url

    def test_url_reflects_url_factory_output(self) -> None:
        fake = FakeDockerContainer()
        chaos = ChaosContainer(fake, ready=lambda logs: "ready" in logs, url_factory=_url_from)

        assert chaos.url == "postgresql://localhost:40000/db"

    def test_url_is_re_derived_on_every_access_never_memoised(self) -> None:
        # The port changes between two `.url` reads (simulating a restart
        # in between) — the second read must reflect the NEW port, proving
        # the property never caches a value from the first access.
        fake = FakeDockerContainer()
        chaos = ChaosContainer(fake, ready=lambda logs: "ready" in logs, url_factory=_url_from)

        first = chaos.url
        fake.port = 50000
        second = chaos.url

        assert first == "postgresql://localhost:40000/db"
        assert second == "postgresql://localhost:50000/db"
        assert first != second

    def test_restart_re_derives_url_after_wait_ready(self) -> None:
        # restart() must leave `.url` reflecting the port AFTER the restart,
        # not a value captured before it — the structural guarantee that
        # makes it impossible for a caller to hold a stale DSN.
        fake = FakeDockerContainer()
        chaos = ChaosContainer(fake, ready=lambda logs: "ready" in logs, url_factory=_url_from)

        before = chaos.url
        fake.port = 60000  # simulates Docker re-allocating the ephemeral port
        chaos.restart(timeout=1)
        after = chaos.url

        assert before == "postgresql://localhost:40000/db"
        assert after == "postgresql://localhost:60000/db"
        assert fake.get_wrapped_container().restart_calls == 1
