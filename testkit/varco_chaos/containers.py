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

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testcontainers.core.container import DockerContainer

_POLL_INTERVAL = 1.0
"""Seconds between readiness polls — matches testcontainers' own
``wait_for_logs`` default interval."""


class ChaosContainer:
    """
    Wraps a running ``DockerContainer`` with restart/pause primitives safe
    for integration chaos tests.

    DESIGN: docker-py ``restart()``, never ``.stop()`` + ``.start()``
        ✅ ``container.get_wrapped_container().restart()`` preserves the
           container's ID *and* its host port mapping (research 002 §1) — a
           connection URL/DSN captured once at fixture-boot time (before any
           chaos operation) stays valid across every ``restart()`` call in
           the module.
        ❌ ``DockerContainer.stop()`` followed by ``.start()`` **deletes and
           recreates** the container, which testcontainers re-exposes on a
           **new random host port** — every captured URL silently goes
           stale, and the test fails with a confusing connection error that
           has nothing to do with the chaos scenario under test.
        A future "simplification" back to stop/start would reintroduce
        exactly that failure mode — this class exists so the reasoning does
        not have to be re-derived, or worse, re-discovered, per chaos
        module.

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
    ) -> None:
        self._container = container
        self._ready = ready
        # Offset (stdout_bytes, stderr_bytes) into the container's cumulative
        # log stream, advanced by restart() to just-before the docker
        # restart call. wait_ready() only matches the predicate against log
        # content at-or-after this offset — see the class DESIGN block.
        self._log_offset: tuple[int, int] = (0, 0)

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
            - The container ID and host port mapping are unchanged by
              design (see the class ``DESIGN`` block) — any URL/DSN captured
              before this call remains valid after it returns.
            - The log offset is captured **before** issuing the restart, so
              a readiness line from the boot sequence this call triggers is
              always at-or-after the offset, never lost to a race.
        """
        self._log_offset = self._log_lengths()
        self._container.get_wrapped_container().restart(timeout=timeout)
        self.wait_ready()

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
