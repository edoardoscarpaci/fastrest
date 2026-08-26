"""
Tests for varco_fastapi.middleware.tracing.TracingMiddleware — Plan 004,
step 21: the server span created per HTTP request must also carry
process-wide global attributes (``varco_core.observability.attributes``),
via the same merge helper used by ``@span``.

This is new, FAILING coverage until the tracing middleware is routed through
the shared merge helper (Plan 004, Phase 6, optional). If that wiring is
dropped for scope reasons, this test documents the gap.

Global-state hygiene: the global attribute registry is process-wide mutable
state — reset around every test in this file.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from varco_fastapi.middleware.tracing import TracingMiddleware


@pytest.fixture(autouse=True)
def _reset_global_attrs():
    def _reset() -> None:
        try:
            from varco_core.observability.attributes import clear_global_attributes

            clear_global_attributes()
        except ImportError:
            pass

    _reset()
    yield
    _reset()


@pytest.fixture()
def span_exporter():
    """Same isolation trick as varco_core/tests/test_observability.py."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with mock.patch("opentelemetry.trace.get_tracer_provider", return_value=provider):
        yield exporter


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TracingMiddleware)

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    return app


class TestTracingMiddlewareGlobalAttributes:
    def test_request_span_carries_registered_global_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(**{"k8s.pod.name": "orders-7d9"})

        client = TestClient(_make_app())
        response = client.get("/ping")
        assert response.status_code == 200

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes.get("k8s.pod.name") == "orders-7d9"

    def test_no_global_attribute_leak_when_registry_empty(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """Regression companion: an empty registry must not add stray attributes."""
        client = TestClient(_make_app())
        response = client.get("/ping")
        assert response.status_code == 200

        spans = span_exporter.get_finished_spans()
        assert "k8s.pod.name" not in spans[0].attributes
