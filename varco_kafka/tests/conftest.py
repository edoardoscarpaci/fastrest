"""
Shared fixtures for varco_kafka integration tests (Plan 012 / RT1, Steps 6-8).

``kafka_bootstrap`` starts a single Kafka broker container **once per test
session**, shared by every integration test in this package, replacing the
per-file ``scope="module"`` ``KafkaContainer`` fixtures previously declared
in ``test_kafka_integration.py``, ``test_kafka_channel_integration.py``, and
``test_kafka_health.py``.

Per-test namespacing rule: because the broker is shared for the whole
session, every test MUST use a unique topic name and/or a unique
``group_id`` (e.g. ``f"test-{uuid4().hex[:8]}"``) — never assume a topic
starts empty. A test that needs a pristine broker (e.g. asserting on the
full list of topics) must declare its own function-scoped
``kafka_container_fresh`` fixture instead.

``VARCO_TEST_KAFKA_URL`` overrides the container entirely (Open Question 1)
— when set, no container is started; the value (a bootstrap-servers string)
is used as-is and reported via ``request.config.stash``.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def kafka_bootstrap(request: pytest.FixtureRequest) -> str:
    """
    Session-scoped Kafka ``bootstrap.servers`` string — real broker or override.

    See the module docstring for the per-test namespacing rule and the
    ``VARCO_TEST_KAFKA_URL`` override contract.

    Yields:
        A ``host:port`` bootstrap-servers string.
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip(
            "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration"
        )

    override = os.environ.get("VARCO_TEST_KAFKA_URL")
    if override:
        request.config.stash.setdefault("varco_test_overrides", []).append(("kafka", override))
        yield override
        return

    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415

    with KafkaContainer() as container:
        yield container.get_bootstrap_server()
