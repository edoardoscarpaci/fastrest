"""
Plan 022 / Phase 4 (RL-8a), Step 22 — ``VarcoLifespan(shutdown=...)``.

RED tests for a kwarg that does not exist yet.  ``VarcoLifespan.__init__``
gains ``shutdown: Callable[[], Awaitable[None]] | None = None``, exactly
symmetric to the existing ``setup=`` kwarg (§D-8a2(a)), so that
``create_varco_app()`` can hand it ``lambda: container.ashutdown()`` and the
six confirmed orphaned ``@PreDestroy`` singletons measured in
``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``
are finally torn down.

Contract under test (§D-8a2):
  (a) additive, keyword-only, defaulted — omitting it is byte-identical to today;
  (b) called AFTER ``_stop_all()``, so components still stop in documented LIFO
      dependency order and the container only sweeps afterwards;
  (c) an aggregated providify ``ShutdownError`` is logged at ERROR — one line
      per ``ShutdownFailure`` — and never re-raised out of the ASGI shutdown.

The hook is deliberately a plain coroutine factory, so every test here runs
without providify (§D-8a2(a)'s ✅ "testable without providify").  Only the
``ShutdownError`` tests import providify, and only for its exception shape —
the same shape ``test_lifespan_shutdown_characterization.py`` pins.

Thread safety:  N/A (unit test)
Async safety:   ✅ all lifespans are driven as async context managers.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import pytest
from providify.exceptions import ShutdownError, ShutdownFailure
from varco_fastapi.lifespan import VarcoLifespan

# ── Test doubles ───────────────────────────────────────────────────────────────


class RecordingComponent:
    """Lifecycle component appending its transitions to a shared call log."""

    def __init__(self, name: str, log: list[str]) -> None:
        self._name = name
        self._log = log

    async def start(self) -> None:
        self._log.append(f"start:{self._name}")

    async def stop(self) -> None:
        self._log.append(f"stop:{self._name}")


class FailingStopComponent(RecordingComponent):
    """Component whose ``stop()`` raises — ``_stop_all()`` logs and continues."""

    async def stop(self) -> None:
        self._log.append(f"stop:{self._name}")
        raise RuntimeError(f"{self._name} failed to stop")


@pytest.fixture
def call_log() -> list[str]:
    return []


async def _run_lifespan(lifespan: VarcoLifespan) -> None:
    """Drive one full startup→shutdown cycle exactly as FastAPI would."""
    async with lifespan(object()):
        pass


# ── (a) signature: additive, keyword-only, defaulted ───────────────────────────


def test_shutdown_kwarg_is_keyword_only_and_defaults_to_none() -> None:
    # §D-8a2(a): symmetric to setup=; a positional would collide with *components.
    param = inspect.signature(VarcoLifespan.__init__).parameters["shutdown"]

    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


async def test_omitting_shutdown_makes_no_extra_calls(call_log: list[str]) -> None:
    # The additive guarantee: every existing VarcoLifespan(...) call site is unchanged.
    lifespan = VarcoLifespan(RecordingComponent("bus", call_log))

    await _run_lifespan(lifespan)

    assert call_log == ["start:bus", "stop:bus"]


async def test_omitting_shutdown_logs_nothing_extra(
    call_log: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    # No new ERROR/WARNING noise for apps that never opt in.
    lifespan = VarcoLifespan(RecordingComponent("bus", call_log))

    with caplog.at_level(logging.WARNING, logger="varco_fastapi.lifespan"):
        await _run_lifespan(lifespan)

    assert caplog.records == []


async def test_explicit_none_shutdown_is_identical_to_omitting_it(
    call_log: list[str],
) -> None:
    # shutdown=None must not be special-cased into "call something".
    lifespan = VarcoLifespan(RecordingComponent("bus", call_log), shutdown=None)

    await _run_lifespan(lifespan)

    assert call_log == ["start:bus", "stop:bus"]


# ── (b) ordering: _stop_all() first, shutdown() second ─────────────────────────


async def test_shutdown_is_awaited_after_stop_all(call_log: list[str]) -> None:
    """
    ORDER is the whole point of §D-8a2(b): components stop in documented LIFO
    dependency order, THEN the container sweep runs.  Asserting only that both
    happened would pass under the wrong (and unsafe) order.
    """

    async def shutdown() -> None:
        call_log.append("container-ashutdown")

    lifespan = VarcoLifespan(
        RecordingComponent("bus", call_log),
        RecordingComponent("consumer", call_log),
        shutdown=shutdown,
    )

    await _run_lifespan(lifespan)

    assert call_log == [
        "start:bus",
        "start:consumer",
        "stop:consumer",  # LIFO — dependents before dependencies
        "stop:bus",
        "container-ashutdown",  # container sweep last
    ]


async def test_shutdown_still_runs_when_a_component_stop_raised(
    call_log: list[str],
) -> None:
    # It must live in a finally: a broken stop() cannot strand container singletons.
    async def shutdown() -> None:
        call_log.append("container-ashutdown")

    lifespan = VarcoLifespan(
        RecordingComponent("bus", call_log),
        FailingStopComponent("consumer", call_log),
        shutdown=shutdown,
    )

    await _run_lifespan(lifespan)

    assert call_log == [
        "start:bus",
        "start:consumer",
        "stop:consumer",
        "stop:bus",
        "container-ashutdown",
    ]


async def test_shutdown_runs_when_a_component_start_raised(
    call_log: list[str],
) -> None:
    """
    A failed startup already tears down the started prefix and re-raises; the
    container sweep must still run, otherwise a half-built app leaks every
    eagerly-constructed singleton (measurement Part 1, #8/#9).
    """

    class FailingStartComponent(RecordingComponent):
        async def start(self) -> None:
            self._log.append(f"start:{self._name}")
            raise RuntimeError("boom")

    async def shutdown() -> None:
        call_log.append("container-ashutdown")

    lifespan = VarcoLifespan(
        RecordingComponent("bus", call_log),
        FailingStartComponent("consumer", call_log),
        shutdown=shutdown,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await _run_lifespan(lifespan)

    assert call_log == [
        "start:bus",
        "start:consumer",
        "stop:bus",
        "container-ashutdown",
    ]


async def test_shutdown_runs_with_no_components_registered(
    call_log: list[str],
) -> None:
    # An app with zero lifecycle components can still hold orphaned singletons.
    async def shutdown() -> None:
        call_log.append("container-ashutdown")

    await _run_lifespan(VarcoLifespan(shutdown=shutdown))

    assert call_log == ["container-ashutdown"]


# ── (c) aggregated ShutdownError: log at ERROR, never re-raise ─────────────────


def _aggregated_shutdown_error() -> ShutdownError:
    """Build the exact aggregated shape the characterization test pins."""
    failures = [
        ShutdownFailure(
            owner="_EarliestCreatedFailingComponent",
            exception=RuntimeError("earliest-created component failed to tear down"),
        ),
        ShutdownFailure(
            owner="_LaterCreatedFailingComponent",
            exception=RuntimeError("later-created component failed to tear down"),
        ),
    ]
    return ShutdownError(failures)


async def test_aggregated_shutdown_error_is_not_reraised(call_log: list[str]) -> None:
    # §D-8a2(c): raising from an asynccontextmanager's finally masks the real cause.
    async def shutdown() -> None:
        raise _aggregated_shutdown_error()

    lifespan = VarcoLifespan(RecordingComponent("bus", call_log), shutdown=shutdown)

    await _run_lifespan(lifespan)  # must not raise

    assert call_log == ["start:bus", "stop:bus"]


async def test_aggregated_shutdown_error_logs_one_error_line_per_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Never a single opaque line — §D-8a2(c)'s stated mitigation for "a genuine
    teardown bug is now only visible in logs".
    """

    async def shutdown() -> None:
        raise _aggregated_shutdown_error()

    with caplog.at_level(logging.ERROR, logger="varco_fastapi.lifespan"):
        await _run_lifespan(VarcoLifespan(shutdown=shutdown))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) >= 2


@pytest.mark.parametrize(
    "expected",
    [
        "_EarliestCreatedFailingComponent",
        "_LaterCreatedFailingComponent",
        "earliest-created component failed to tear down",
        "later-created component failed to tear down",
    ],
)
async def test_shutdown_error_log_names_each_component_and_exception(
    caplog: pytest.LogCaptureFixture, expected: str
) -> None:
    # Each ShutdownFailure's owner AND its exception must be recoverable from logs.
    async def shutdown() -> None:
        raise _aggregated_shutdown_error()

    with caplog.at_level(logging.ERROR, logger="varco_fastapi.lifespan"):
        await _run_lifespan(VarcoLifespan(shutdown=shutdown))

    assert expected in caplog.text


async def test_non_shutdown_error_from_shutdown_is_also_logged_not_raised(
    call_log: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    """
    A plain exception (e.g. the container itself blew up) must not escape ASGI
    shutdown either — same reasoning as (c), and consistent with _stop_all()'s
    existing "logs errors but does not raise" contract.
    """

    async def shutdown() -> None:
        raise ValueError("container exploded")

    lifespan = VarcoLifespan(RecordingComponent("bus", call_log), shutdown=shutdown)

    with caplog.at_level(logging.ERROR, logger="varco_fastapi.lifespan"):
        await _run_lifespan(lifespan)  # must not raise

    assert "container exploded" in caplog.text


async def test_component_stop_failure_does_not_suppress_shutdown_error_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Both teardown paths report independently; neither swallows the other.
    log: list[str] = []

    async def shutdown() -> None:
        raise _aggregated_shutdown_error()

    lifespan = VarcoLifespan(FailingStopComponent("consumer", log), shutdown=shutdown)

    with caplog.at_level(logging.ERROR, logger="varco_fastapi.lifespan"):
        await _run_lifespan(lifespan)

    assert "consumer failed to stop" in caplog.text
    assert "_EarliestCreatedFailingComponent" in caplog.text


def test_shutdown_hook_is_not_called_at_construction_time() -> None:
    # Constructing a lifespan must have zero side effects (mirrors setup=).
    calls: list[Any] = []

    async def shutdown() -> None:
        calls.append(1)

    VarcoLifespan(shutdown=shutdown)

    assert calls == []
