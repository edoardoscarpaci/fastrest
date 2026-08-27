"""
``ChaosContainer`` — a thin, three-method wrapper around a testcontainers
``DockerContainer`` that a chaos test is allowed to break (Plan 018 / RT7b,
§chaos-fixture).

This module is the *only* place in the repo that calls
``DockerContainer.get_wrapped_container()`` (see ``varco_chaos/__init__.py``'s
module docstring) — every chaos test module goes through ``ChaosContainer``
instead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testcontainers.core.container import DockerContainer

_logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0
"""Seconds between readiness polls — matches testcontainers' own
``wait_for_logs`` default interval."""


class ChaosContainer:
    """
    Wraps a running ``DockerContainer`` with restart/pause primitives safe
    for integration chaos tests.

    DESIGN: docker-py ``restart()`` preserves the container ID, NOT the host
    port — the URL must be re-derived on every access (Plan 019 / §RT7b-port;
    supersedes this class's original port-survivorship claim, which rested
    on research 002 §1 and was itself overturned by research 006 §A/§B/§F)
        ✅ ``container.get_wrapped_container().restart()`` preserves the
           container's **ID** — that is still why ``.stop()`` + ``.start()``
           is forbidden below (it deletes and recreates the container,
           losing even the ID). Preserving the ID is what makes restarting
           *in place* meaningful at all.
        ❌ Docker's own Engine API reference states the allocated host port
           "might be changed when restarting the container", and moby's
           libnetwork portmapper releases ephemeral ports on unmap and
           re-requests on map at every restart (research 006 §A) —
           platform-independent and version-stable from moby v1.3.0 to v29.1
           (006 §B), including GitHub Actions' native-Linux dockerd (006
           §F). A URL/DSN captured once at fixture-boot time and reused
           after a ``restart()`` is therefore a **live bug**, not a
           defensive habit — this is why ``ChaosContainer`` owns a ``url``
           property (below) instead of callers capturing a string once.
        ✅ ``DockerContainer.get_exposed_port()`` re-queries the docker
           daemon on **every call** (no caching on testcontainers' side,
           verified by source inspection — ``.venv/…/core/container.py:
           247-258``), so re-deriving the URL on each ``.url`` access is
           cheap and always current.
        ❌ ``DockerContainer.stop()`` followed by ``.start()`` **deletes and
           recreates** the container (loses the ID too, not just the port),
           which is still strictly worse and remains forbidden.
        A future "simplification" back to stop/start would reintroduce that
        worse failure mode — this class exists so neither reasoning has to
        be re-derived, or worse, re-discovered, per chaos module.

    DESIGN: readiness is checked against **log output emitted after the last
    restart**, never the container's full cumulative log history
        Discovered while implementing ``test_sa_chaos.py``'s database-restart
        scenario (Plan 018, resumed session): docker's ``logs()`` API returns
        the **entire** log history since the container was created, not just
        since the last ``restart()``. testcontainers' own ``wait_for_logs``
        greps that entire history — so a readiness string emitted once at
        the container's *original* boot (e.g. Postgres's "database system is
        ready to accept connections") is **still present** in the log buffer
        after a ``restart()``, and a naive re-application of the same
        predicate against the full log matches **immediately**, before the
        restarted process has actually finished coming back up. The test
        then reconnects into a connection refusal.
            ✅ Tracking a byte-offset per stream (captured immediately before
               calling docker's ``restart()``) and matching the predicate
               only against log bytes emitted **after** that offset makes
               ``wait_ready()`` genuinely wait for the *new* boot sequence,
               not any historical one.
            ❌ A second, hand-rolled poll loop instead of delegating to
               testcontainers' ``wait_for_logs`` — that helper has no
               "search from offset N" mode to delegate to. Accepted: the loop
               below is a small, deterministic subset of the same algorithm
               (interval polling to a deadline, `TimeoutError` on expiry).
        ``paused()``/``unpause()`` does not restart the process, so historical
        log content is still valid evidence of current readiness there — the
        offset is therefore only advanced by ``restart()``, never reset by
        ``paused()``.

    Args:
        container: The already-started ``DockerContainer`` this instance is
            allowed to restart/pause. The caller retains ownership of the
            container's lifecycle (start/final stop) — ``ChaosContainer``
            only ever calls ``restart``/``pause``/``unpause`` on it.
        ready: A predicate over the container's stdout+stderr log text
            (searched separately, matching either stream — same contract as
            testcontainers' own ``wait_for_logs``). Re-applied by
            ``wait_ready()`` after every disruptive operation. ``None`` means
            this container has no readiness predicate declared
            (``wait_ready()`` then raises immediately — fail loudly rather
            than silently returning without waiting for anything).
        url_factory: A callable that derives this container's current
            connection URL/DSN from the (live) ``DockerContainer`` — e.g.
            ``lambda c: c.get_connection_url(driver="asyncpg")``. Called
            fresh on every ``.url`` access, never memoised (Plan 019 /
            §RT7b-port) — this is what makes it structurally impossible for
            a caller to hold a stale URL across a ``restart()``. ``None``
            means this container has no URL derivation declared (``.url``
            then raises immediately, mirroring ``wait_ready()``'s missing-
            ``ready`` contract).

    Async safety: every method is synchronous and blocks the calling thread
        (docker-py's HTTP calls to the daemon, and this class's own polling
        loop) — callers awaiting a coroutine test body call these from a
        `sync` context (fixture setup) or accept the (short) blocking cost
        inline, matching every existing chaos test module in this plan.
    """

    def __init__(
        self,
        container: DockerContainer,
        *,
        ready: Callable[[str], bool] | None = None,
        url_factory: Callable[[DockerContainer], str] | None = None,
    ) -> None:
        self._container = container
        self._ready = ready
        self._url_factory = url_factory
        # Offset (stdout_bytes, stderr_bytes) into the container's cumulative
        # log stream, advanced by restart() to just-before the docker
        # restart call. wait_ready() only matches the predicate against log
        # content at-or-after this offset — see the class DESIGN block.
        self._log_offset: tuple[int, int] = (0, 0)

    @property
    def url(self) -> str:
        """
        This container's current connection URL/DSN, re-derived fresh on
        every access — **never memoised** (Plan 019 / §RT7b-port).

        A restart-based chaos scenario can move the container's ephemeral
        host port (research 006 §A) at any time; a cached string would go
        stale the instant that happens. Reading this property is the only
        sanctioned way for a caller to obtain a connection URL from a
        ``ChaosContainer`` — never capture it into a local once and reuse it
        across a ``restart()``.

        Returns:
            The freshly-derived connection URL/DSN.

        Raises:
            ValueError: no ``url_factory`` was supplied at construction time
                — fail loudly rather than silently returning nothing.
        """
        if self._url_factory is None:
            raise ValueError(
                f"{type(self).__name__} was constructed without a `url_factory` — "
                "cannot derive a connection URL"
            )
        return self._url_factory(self._container)

    def _log_lengths(self) -> tuple[int, int]:
        stdout_b, stderr_b = self._container.get_logs()
        return len(stdout_b), len(stderr_b)

    def restart(self, timeout: int = 5) -> None:
        """
        Restart the underlying container in place and wait for it to become
        ready again.

        Args:
            timeout: Seconds docker gives the container to stop gracefully
                before SIGKILL — forwarded verbatim to docker-py's
                ``Container.restart(timeout=...)``.

        Raises:
            TimeoutError: via ``wait_ready()`` if the container's log
                predicate never matches new log output within its timeout.

        Edge cases:
            - The container ID is unchanged by design (see the class
              ``DESIGN`` block), but the host port mapping is **not** —
              docker may re-allocate it (research 006 §A). Callers must read
              ``.url`` again after this returns; never reuse a URL captured
              before the call.
            - The log offset is captured **before** issuing the restart, so
              a readiness line from the boot sequence this call triggers is
              always at-or-after the offset, never lost to a race.
        """
        self._log_offset = self._log_lengths()
        self._container.get_wrapped_container().restart(timeout=timeout)
        self.wait_ready()
        if self._url_factory is not None:
            # Re-derive and log the (possibly new) URL now that the
            # container has confirmed it is ready again — makes a port
            # remap visible in test output without requiring the caller to
            # do anything extra.
            _logger.info("ChaosContainer.restart: post-restart url=%s", self.url)

    @contextmanager
    def paused(self) -> Iterator[None]:
        """
        Pause the container's processes for the duration of the ``with``
        block, unconditionally unpausing on exit.

        A paused container's processes are frozen without being sent any
        signal — in-flight connections black-hole rather than receiving a
        fast RST, which is the harder failure mode ``@timeout`` +
        ``CircuitBreaker`` exist to guard against (§RT7-shape).

        Yields:
            None.

        Edge cases:
            - ``unpause()`` runs in a ``finally`` — a failed assertion (or
              any other exception) inside the ``with`` block never leaves
              the container frozen for the rest of the module's tests
              (§chaos-fixture's module-scope safety contract).
            - Does **not** advance the log offset — the process was frozen,
              not restarted, so log content from before the pause remains
              valid evidence of readiness for a subsequent ``wait_ready()``.
        """
        raw = self._container.get_wrapped_container()
        raw.pause()
        try:
            yield
        finally:
            raw.unpause()

    def wait_ready(self, timeout: float = 60.0) -> None:
        """
        Block until this container's readiness predicate matches log output
        emitted at-or-after the last ``restart()`` (or since construction,
        if ``restart()`` was never called).

        Args:
            timeout: Seconds to poll before giving up.

        Raises:
            ValueError: no ``ready`` predicate was supplied at construction
                time — fail loudly rather than silently returning without
                having waited for anything.
            TimeoutError: the predicate never matched within ``timeout``.

        Edge cases:
            - Deterministic interval polling only, never a fixed
              ``asyncio.sleep(n)`` — research 002 §5 names fixed sleeps as
              the primary avoidable source of chaos test flakiness.
            - See the class ``DESIGN`` block for why this checks only
              *new* log content rather than the full cumulative history.
        """
        if self._ready is None:
            raise ValueError(
                f"{type(self).__name__} was constructed without a `ready` predicate — "
                "cannot wait for readiness"
            )
        stdout_offset, stderr_offset = self._log_offset
        deadline = time.monotonic() + timeout
        while True:
            stdout_b, stderr_b = self._container.get_logs()
            new_stdout = stdout_b[stdout_offset:].decode(errors="replace")
            new_stderr = stderr_b[stderr_offset:].decode(errors="replace")
            if self._ready(new_stdout) or self._ready(new_stderr):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"container did not emit new log output satisfying the readiness "
                    f"predicate within {timeout:.3f}s. New stdout: {new_stdout!r}. "
                    f"New stderr: {new_stderr!r}."
                )
            time.sleep(_POLL_INTERVAL)
