"""
Unit tests for varco_nats.NatsHealthCheck
==========================================
All tests fake ``nats-py`` — no real NATS broker required.

``NatsHealthCheck.check()`` does ``import nats`` lazily, so the tests patch
``nats.connect`` directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from varco_core.health import HealthStatus
from varco_nats import NatsEventBusSettings, NatsHealthCheck

from tests.fakes import FakeJetStream, FakeNatsClient


@pytest.fixture
def settings() -> NatsEventBusSettings:
    return NatsEventBusSettings(servers="nats://fake:4222")


# ── name ──────────────────────────────────────────────────────────────────────


class TestName:
    def test_name(self, settings: NatsEventBusSettings) -> None:
        assert NatsHealthCheck(settings).name == "nats"


# ── check — healthy ───────────────────────────────────────────────────────────


class TestHealthy:
    async def test_healthy_when_connect_and_account_info_succeed(
        self, settings: NatsEventBusSettings
    ) -> None:
        async def _fake_connect(**_: object) -> FakeNatsClient:
            return FakeNatsClient(FakeJetStream())

        with patch("nats.connect", new=_fake_connect):
            result = await NatsHealthCheck(settings).check()

        assert result.status is HealthStatus.HEALTHY
        assert result.component == "nats"
        # A healthy probe records the round-trip latency.
        assert result.latency_ms is not None


# ── check — unhealthy ─────────────────────────────────────────────────────────


class TestUnhealthy:
    async def test_unhealthy_when_connect_fails(
        self, settings: NatsEventBusSettings
    ) -> None:
        async def _fake_connect(**_: object) -> FakeNatsClient:
            raise ConnectionRefusedError("no server")

        with patch("nats.connect", new=_fake_connect):
            result = await NatsHealthCheck(settings).check()

        assert result.status is HealthStatus.UNHEALTHY
        # check() must never raise — the error surfaces in `detail`.
        assert result.detail is not None

    async def test_unhealthy_when_jetstream_not_enabled(
        self, settings: NatsEventBusSettings
    ) -> None:
        js = FakeJetStream()
        # account_info() raises when JetStream is not enabled on the server.
        js.account_info_ok = False

        async def _fake_connect(**_: object) -> FakeNatsClient:
            return FakeNatsClient(js)

        with patch("nats.connect", new=_fake_connect):
            result = await NatsHealthCheck(settings).check()

        assert result.status is HealthStatus.UNHEALTHY

    async def test_unhealthy_on_timeout(self, settings: NatsEventBusSettings) -> None:
        async def _slow_connect(**_: object) -> FakeNatsClient:
            # Exceed the probe budget so asyncio.wait_for times out.
            await asyncio.sleep(1.0)
            return FakeNatsClient(FakeJetStream())

        with patch("nats.connect", new=_slow_connect):
            result = await NatsHealthCheck(settings, timeout=0.01).check()

        assert result.status is HealthStatus.UNHEALTHY
        assert result.detail is not None
        assert "timed out" in result.detail
