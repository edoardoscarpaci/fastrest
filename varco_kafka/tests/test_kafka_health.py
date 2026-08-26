"""
Unit tests for varco_kafka.health.KafkaHealthCheck
====================================================
Uses ``AsyncMock`` to replace AIOKafkaProducer — no real Kafka broker required.

Sections
--------
- Healthy probe     — start + fetch_all_metadata succeed → HEALTHY with latency
- Timeout on start  — producer.start() hangs → UNHEALTHY
- Timeout on meta   — fetch_all_metadata hangs → UNHEALTHY
- Connection error  — start() raises → UNHEALTHY with detail
- Cleanup           — producer.stop() always called
- Never-raise       — exceptions never propagate to caller
- Repr              — human-readable string for logging
- Integration       — real Kafka via testcontainers (marked integration)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from varco_core.health import HealthStatus
from varco_kafka.config import KafkaEventBusSettings
from varco_kafka.health import KafkaHealthCheck

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_producer(
    *,
    start_side_effect=None,
    meta_side_effect=None,
) -> MagicMock:
    """
    Build a mock AIOKafkaProducer whose start() and client.fetch_all_metadata()
    can be configured to succeed or raise.

    Args:
        start_side_effect:  If set, producer.start() raises this.
        meta_side_effect:   If set, fetch_all_metadata() raises this.
    """
    producer = MagicMock()
    producer.start = AsyncMock(side_effect=start_side_effect)
    producer.stop = AsyncMock()

    client = MagicMock()
    client.fetch_all_metadata = AsyncMock(side_effect=meta_side_effect)
    producer.client = client

    return producer


# ── Healthy probe ─────────────────────────────────────────────────────────────


async def test_healthy_returns_healthy_status() -> None:
    producer = _make_producer()

    # AIOKafkaProducer is imported inside check() — patch at the aiokafka module.
    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()

    assert result.status is HealthStatus.HEALTHY


async def test_healthy_component_name() -> None:
    producer = _make_producer()

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()

    assert result.component == "kafka"


async def test_healthy_has_latency() -> None:
    producer = _make_producer()

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()

    assert result.latency_ms is not None
    assert result.latency_ms >= 0.0


# ── Timeout ───────────────────────────────────────────────────────────────────


async def test_timeout_on_start_returns_unhealthy() -> None:
    async def _hang():
        await asyncio.sleep(999)

    producer = MagicMock()
    producer.start = _hang
    producer.stop = AsyncMock()

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(
            KafkaEventBusSettings(bootstrap_servers="kafka:9092"), timeout=0.001
        )
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY
    assert result.latency_ms is None
    assert "timed out" in (result.detail or "")


async def test_timeout_on_metadata_returns_unhealthy() -> None:
    async def _hang():
        await asyncio.sleep(999)

    producer = MagicMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    client = MagicMock()
    client.fetch_all_metadata = _hang
    producer.client = client

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(
            KafkaEventBusSettings(bootstrap_servers="kafka:9092"), timeout=0.001
        )
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY


# ── Connection error ──────────────────────────────────────────────────────────


async def test_connection_error_returns_unhealthy() -> None:
    producer = _make_producer(start_side_effect=ConnectionRefusedError("refused"))

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY
    assert "refused" in (result.detail or "")


async def test_metadata_error_returns_unhealthy() -> None:
    producer = _make_producer(meta_side_effect=OSError("broker unavailable"))

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY


# ── Cleanup ───────────────────────────────────────────────────────────────────


async def test_producer_stopped_on_success() -> None:
    producer = _make_producer()

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        await check.check()

    producer.stop.assert_awaited_once()


async def test_producer_stopped_on_error() -> None:
    producer = _make_producer(start_side_effect=OSError("refused"))

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        await check.check()

    producer.stop.assert_awaited_once()


# ── Never-raise contract ──────────────────────────────────────────────────────


async def test_check_never_raises_on_error() -> None:
    producer = _make_producer(start_side_effect=Exception("boom"))

    import aiokafka as _aiokafka

    with patch.object(_aiokafka, "AIOKafkaProducer", return_value=producer):
        check = KafkaHealthCheck(KafkaEventBusSettings(bootstrap_servers="kafka:9092"))
        result = await check.check()  # must not raise

    assert result.status is HealthStatus.UNHEALTHY


# ── Repr ──────────────────────────────────────────────────────────────────────


def test_repr_contains_bootstrap_servers() -> None:
    check = KafkaHealthCheck(
        KafkaEventBusSettings(bootstrap_servers="b1:9092,b2:9092"), timeout=3.0
    )
    text = repr(check)
    assert "b1:9092" in text
    assert "3.0" in text


# ── Constructor contract ──────────────────────────────────────────────────────


def test_regression_constructor_takes_settings_not_bootstrap_servers() -> None:
    """
    User reports: ``KafkaHealthCheck(bootstrap_servers=..., timeout=10.0)``
    raises ``TypeError: unexpected keyword argument 'bootstrap_servers'``.
    Correct behaviour is that the probe takes the injected
    ``KafkaEventBusSettings`` object, because the check is a DI ``@Singleton``
    whose brokers must be exactly the ones the event bus uses — a second,
    independently-passed address string is a config-drift hazard.

    The old ``bootstrap_servers=`` kwarg predates the settings-object
    constructor and survived only in a Docker-gated integration test.
    """
    check = KafkaHealthCheck(
        KafkaEventBusSettings(bootstrap_servers="b1:9092,b2:9092"), timeout=7.5
    )

    # The probe targets exactly the settings' brokers.
    assert "b1:9092,b2:9092" in repr(check)
    assert "7.5" in repr(check)

    with pytest.raises(TypeError):
        KafkaHealthCheck(bootstrap_servers="b1:9092")  # type: ignore[call-arg]


def test_regression_settings_is_positional_and_timeout_keyword_only() -> None:
    """``timeout`` is keyword-only so it can never be mistaken for settings."""
    with pytest.raises(TypeError):
        KafkaHealthCheck(
            KafkaEventBusSettings(bootstrap_servers="b1:9092"), 3.0
        )  # type: ignore[misc]


# ── Integration: real Kafka ───────────────────────────────────────────────────


@pytest.mark.integration
async def test_integration_healthy_against_real_kafka(kafka_bootstrap: str) -> None:
    """
    Against the shared session-scoped Kafka broker (tests/conftest.py,
    Plan 012 / RT1, Step 6), verifies the health check reports HEALTHY with
    a non-negative latency.
    Run with: VARCO_RUN_INTEGRATION=1 pytest -m integration
    """
    # KafkaHealthCheck takes the injected KafkaEventBusSettings object,
    # never a bare bootstrap_servers string — same shape every unit test
    # above uses.  The old `bootstrap_servers=` kwarg predates the
    # settings-object constructor (varco_core.connection settings pattern)
    # and only survived here because this test never ran without Docker.
    check = KafkaHealthCheck(
        KafkaEventBusSettings(bootstrap_servers=kafka_bootstrap), timeout=10.0
    )
    result = await check.check()
    assert result.status is HealthStatus.HEALTHY
    assert result.latency_ms is not None
