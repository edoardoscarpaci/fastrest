"""
varco_core.observability.config
================================
``OtelConfig`` — immutable bootstrap configuration for OpenTelemetry.

This dataclass is the single injectable settings object for the entire
observability stack.  It follows the same pattern as ``SAConfig`` in
``varco_sa``: one frozen dataclass that doubles as both the DI settings object
and the bootstrap config, avoiding a parallel "settings" class.

DESIGN: frozen dataclass instead of pydantic BaseSettings
    ✅ Immutable — callers can't mutate config after construction.
    ✅ Hashable — safe to use as dict key if needed.
    ✅ Zero runtime dependency on pydantic-settings (already a dep of
       varco_core, but keeping this layer pure makes it easier to lift
       the observability package into its own package later).
    ❌ No automatic env-var reading — callers must read os.environ themselves
       or wrap in a pydantic BaseSettings if they want that convenience.
       Trade-off accepted: keeps this module framework-agnostic.

DESIGN: single config object instead of separate TracerConfig + MeterConfig
    ✅ One install() call wires both tracing and metrics consistently.
    ✅ ``service_name`` and ``service_version`` are shared — no risk of typo
       divergence between the tracer and meter resource attributes.
    ❌ Users who only want tracing still carry the metrics fields (and vice
       versa).  Acceptable — the fields have sensible defaults and the DI
       module only creates providers for what the user actually needs.

Stateless / autoscaling notes
------------------------------
Each replica configures its own OTel providers at startup from its own
``OtelConfig``.  There is no shared in-process state between replicas — each
is an independent OS process with its own SDK state.

``extra_resource_attrs`` is the hook for injecting replica identity:

    config = OtelConfig(
        service_name="orders-svc",
        extra_resource_attrs={
            "k8s.pod.name":  os.environ["POD_NAME"],
            "k8s.node.name": os.environ["NODE_NAME"],
        },
    )

This stamps every span and metric with the originating pod so distributed
traces and per-pod metrics work correctly in Grafana / Tempo / Prometheus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from varco_core.observability.params import ParamCaptureConfig

# ── OtelConfig ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OtelConfig:
    """
    Immutable bootstrap configuration for OpenTelemetry tracing and metrics.

    Pass this to ``OtelConfiguration`` (the DI module) to wire both a
    ``TracerProvider`` and a ``MeterProvider`` into the OTel global API so
    that all ``@span``, ``@counter``, and ``@histogram`` decorators in the
    process pick them up automatically.

    Args:
        service_name:
            Human-readable name that identifies this service in traces and
            metrics (e.g. ``"orders-svc"``).  Becomes the ``service.name``
            OTel resource attribute — required by most backends.
        service_version:
            Semantic version of the running binary (e.g. ``"1.2.3"``).
            Useful for correlating regressions with deployments.
        otlp_endpoint:
            gRPC endpoint of the OpenTelemetry Collector
            (e.g. ``"http://otel-collector:4317"``).  Set to ``None`` to
            disable export — spans and metrics are still recorded in memory
            but never sent anywhere.  Useful for local development and tests.
        tracer_name:
            Default tracer name used by ``@span`` when ``SpanConfig.tracer_name``
            is not overridden.  Identifies the *instrumentation library* in
            the OTel data model — use a stable reverse-DNS-style string.
        meter_name:
            Default meter name used by ``@counter`` and ``@histogram`` when
            their config does not override it.  Same naming convention as
            ``tracer_name``.
        export_interval_ms:
            How often the periodic metric reader pushes metrics to the
            collector (milliseconds).  Lower values → more real-time
            dashboards but higher network overhead.  Default is 60 000 ms
            (60 s), which matches the Prometheus scrape default.
        prometheus_enabled:
            When ``True``, ``OtelConfiguration.meter_provider()`` attaches a
            ``PrometheusMetricReader`` to the ``MeterProvider``.  The reader
            registers itself with ``prometheus_client.REGISTRY`` at
            construction time, so ``MetricsRouter``'s ``GET /metrics``
            endpoint can call ``generate_latest()`` to serve Prometheus text
            format.  Both OTLP push and Prometheus pull can coexist — set
            ``otlp_endpoint`` and ``prometheus_enabled=True`` simultaneously
            for dual export.

            Requires ``opentelemetry-exporter-prometheus`` — install via::

                pip install 'varco-fastapi[prometheus]'

            If the package is missing, ``OtelConfiguration`` logs an ``ERROR``
            at startup but does not raise — the app continues without
            Prometheus metrics.  Default: ``False``.
        extra_resource_attrs:
            Additional OTel resource attributes stamped on every span and
            metric produced by this process.  Use this to inject
            infrastructure-level identity that OTel cannot discover
            automatically::

                extra_resource_attrs={
                    "k8s.pod.name":       os.environ["POD_NAME"],
                    "k8s.node.name":      os.environ["NODE_NAME"],
                    "deployment.version": os.environ["APP_VERSION"],
                }

            In autoscaling clusters this is the primary way to distinguish
            metrics from different replicas of the same service.
        capture_params:
            Process-wide override for automatic ``@span`` parameter capture
            (Plan 004).  ``None`` (default) defers to
            ``VARCO_OTEL_CAPTURE_PARAMS`` / the built-in default (``True``).
            Set ``False`` to disable capture for the whole process without
            touching every ``@span`` call site.
        param_capture:
            Full structural override (``ParamCaptureConfig``) applied as the
            process-wide default for parameter capture — prefix, redaction
            patterns, value rendering mode, limits, etc.  ``None`` (default)
            uses ``ParamCaptureConfig()``'s own defaults.
        global_attributes:
            Static key/value pairs seeded into the process-wide global
            attribute registry (``varco_core.observability.attributes``) at
            bootstrap.  Applied via ``set_global_attributes()`` *after*
            ``VARCO_OTEL_GLOBAL_ATTRS*`` env vars are loaded, so explicit
            config here wins over ambient env on key collision.

            Read the Resource-vs-registry decision table in
            ``varco_core.observability.attributes``'s module docstring
            before reaching for this field: static process identity
            (pod name, deployment environment) belongs in
            ``extra_resource_attrs`` above, not here — putting it here makes
            it a **label on every metric series**, multiplying cardinality.
            Use this field for values that must be filterable/groupable as a
            metric label, or that are not known at bootstrap.
        global_attributes_on_spans:
            Whether the global attribute registry is applied to spans.
            Default ``True``.  Runtime equivalent:
            ``configure_global_attributes(apply_to_spans=...)`` /
            ``VARCO_OTEL_GLOBAL_ATTRS_SPANS``.
        global_attributes_on_metrics:
            Whether the global attribute registry is applied to metric
            measurements.  Default ``True``.  See the cardinality warning
            above — this is the field to flip if a global attribute causes
            metric-series explosion.
        promote_global_attrs_to_resource:
            When ``True``, ``OtelConfiguration`` also merges the *static*
            part of the global attribute registry into the OTel ``Resource``
            at bootstrap (in addition to applying it per-span/per-metric, if
            those toggles are also on).  Default ``False`` — two independent
            knobs, no silent double-labelling.

    Edge cases:
        - ``service_name`` empty string → accepted but will look odd in most
          backends; callers are responsible for providing a meaningful name.
        - ``otlp_endpoint=None`` → no exporter attached; the SDK still
          records spans (useful with ``InMemorySpanExporter`` in tests).
        - ``export_interval_ms=0`` → not validated here; the OTel SDK will
          raise at provider construction time.
        - ``extra_resource_attrs`` values are always strings — OTel resource
          attributes must be strings per the semantic conventions spec.

    Thread safety:  ✅ Frozen dataclass — immutable after construction.
    Async safety:   ✅ Stateless value object — safe to share across tasks.

    Example::

        import os
        from varco_core.observability import OtelConfig

        config = OtelConfig(
            service_name="orders-svc",
            service_version="1.0.0",
            otlp_endpoint="http://otel-collector:4317",
            extra_resource_attrs={
                "k8s.pod.name": os.environ.get("POD_NAME", "unknown"),
            },
        )
    """

    # ── Required ──────────────────────────────────────────────────────────────

    service_name: str

    # ── Optional — sensible defaults for local development ────────────────────

    service_version: str = "0.0.0"

    # None means "don't export" — useful in tests and local dev where there is
    # no collector running.
    otlp_endpoint: str | None = None

    # Default tracer / meter names — can be overridden per-decorator via
    # SpanConfig.tracer_name / CounterConfig.meter_name.
    tracer_name: str = "varco"
    meter_name: str = "varco"

    # 60 000 ms matches the Prometheus default scrape interval — a sensible
    # starting point that balances freshness against collector load.
    export_interval_ms: int = 60_000

    # When True, OtelConfiguration.meter_provider() attaches a
    # PrometheusMetricReader to the MeterProvider.  The reader registers with
    # prometheus_client.REGISTRY at construction time so that MetricsRouter's
    # generate_latest() call picks up all OTel metrics automatically.
    # Requires the opentelemetry-exporter-prometheus package — install via
    # the ``prometheus`` optional extra of varco-fastapi:
    #   pip install 'varco-fastapi[prometheus]'
    # If the package is missing and this flag is True, OtelConfiguration logs
    # an ERROR at startup but does not crash — metrics are silently lost.
    prometheus_enabled: bool = False

    # Pod/node identity and any other infrastructure attributes the OTel SDK
    # cannot discover automatically from the environment.
    extra_resource_attrs: dict[str, str] = field(default_factory=dict)

    # ── Plan 004 — parameter capture + global attributes (all defaulted,
    # backwards compatible) ──────────────────────────────────────────────────

    # None → inherit VARCO_OTEL_CAPTURE_PARAMS / the built-in default (True).
    capture_params: bool | None = None
    param_capture: ParamCaptureConfig | None = None

    # Seeded into the global attribute registry at bootstrap — see the
    # Resource-vs-registry guidance above before using this for static
    # process identity (prefer extra_resource_attrs for that).
    global_attributes: dict[str, str] = field(default_factory=dict)
    global_attributes_on_spans: bool = True
    global_attributes_on_metrics: bool = True

    # Two independent knobs by design — see attributes.py's module docstring.
    promote_global_attrs_to_resource: bool = False


__all__ = ["OtelConfig"]
