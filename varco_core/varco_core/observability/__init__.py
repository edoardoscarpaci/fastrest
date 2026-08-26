"""
varco_core.observability
=========================
OpenTelemetry tracing and metrics for varco services.

Public API
----------

Decorators::

    @span                          # wrap any function in an OTel span
    @span(SpanConfig(...))         # configured form

    @counter(CounterConfig(...))   # increment a counter on each successful call
    @histogram(HistogramConfig(...)) # record call duration as a histogram

Service mixin::

    class OrderService(
        TracingServiceMixin,        # auto-spans all CRUD methods
        AsyncService[Order, ...],
    ): ...

Config + DI::

    from providify import Provider

    # Module-level, @Provider-decorated — install() takes no config= kwarg and
    # provide() rejects bare lambdas.  Register it BEFORE install().
    @Provider(singleton=True)
    def otel_config() -> OtelConfig:
        return OtelConfig(
            service_name="orders-svc",
            otlp_endpoint="http://otel-collector:4317",
        )

    container.provide(otel_config)
    container.install(OtelConfiguration)

Plan 004 — automatic parameter capture + global attributes
------------------------------------------------------------
Every ``@span`` (and ``TracingServiceMixin``/``TracingRepositoryMixin``/
``create_span``) automatically records the decorated function's arguments as
``param.<name>`` span attributes, with redaction, truncation, and a global +
per-decorator kill switch (``varco_core.observability.params``).

A process-wide **global attribute registry**
(``varco_core.observability.attributes``) stamps entries on every span AND
every metric measurement — static values, env-var-sourced values (
``VARCO_OTEL_GLOBAL_ATTRS`` / ``VARCO_OTEL_GLOBAL_ATTR_ENV``), and callable
providers for values not known at bootstrap::

    from varco_core.observability import (
        ParamCaptureConfig,
        set_capture_enabled,
        set_global_attributes,
        register_global_attribute_provider,
        current_global_attributes,
        configure_global_attributes,
    )

    set_global_attributes(**{"k8s.pod.name": "orders-7d9"})  # labels every span + metric

See ``varco_core.observability.attributes``'s module docstring for the
Resource-attributes-vs-global-attribute-registry decision — static process
identity belongs in ``OtelConfig.extra_resource_attrs``, not the registry.
"""

from __future__ import annotations

from varco_core.observability.attributes import (
    GlobalAttributes,
    clear_global_attributes,
    configure_global_attributes,
    current_global_attributes,
    register_global_attribute_provider,
    set_global_attributes,
)
from varco_core.observability.config import OtelConfig
from varco_core.observability.di import OtelConfiguration
from varco_core.observability.helpers import (
    create_counter,
    create_histogram,
    create_span,
)
from varco_core.observability.metric import Metric, MetricKind, register_gauge
from varco_core.observability.metrics import (
    CounterConfig,
    HistogramConfig,
    counter,
    histogram,
)
from varco_core.observability.mixin import TracingServiceMixin
from varco_core.observability.params import (
    ParamCaptureConfig,
    set_capture_enabled,
    set_param_capture_defaults,
)
from varco_core.observability.repository_mixin import TracingRepositoryMixin
from varco_core.observability.span import SpanConfig, span

__all__ = [
    # Config
    "OtelConfig",
    # DI
    "OtelConfiguration",
    # Tracing — decorator
    "span",
    "SpanConfig",
    # Tracing — context manager
    "create_span",
    # Metrics — decorators
    "counter",
    "CounterConfig",
    "histogram",
    "HistogramConfig",
    # Metrics — imperative helpers
    "create_counter",
    "create_histogram",
    # Service mixin
    "TracingServiceMixin",
    # Repository mixin
    "TracingRepositoryMixin",
    # Custom named metrics
    "Metric",
    "MetricKind",
    "register_gauge",
    # Plan 004 (A) — automatic parameter capture
    "ParamCaptureConfig",
    "set_capture_enabled",
    "set_param_capture_defaults",
    # Plan 004 (B) — global attribute registry
    "GlobalAttributes",
    "set_global_attributes",
    "register_global_attribute_provider",
    "current_global_attributes",
    "clear_global_attributes",
    "configure_global_attributes",
]
