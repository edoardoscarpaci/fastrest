"""
Workspace-wide DI declaration health checks.

Why this file lives in ``varco_fastapi/tests``
----------------------------------------------
It has to import *every* varco package at once (``varco_core`` must not depend
on its siblings), and ``varco_fastapi`` is the package that already sits on top
of the whole stack.

What it guards
--------------
A user reported ``TypeError: tracer_provider() missing 1 required positional
argument: 'config'`` when ``VarcoFastAPIModule`` and ``OtelConfiguration``
shared one container — ``Inject[OtelConfig]`` was silently not injected.

Root cause (in providify, not varco):

1. ``ProviderBinding.__init__`` (``providify/binding.py``) falls back to
   ``eval(fn.__annotations__["return"], fn.__globals__)`` when
   ``get_type_hints()`` fails.  Under PEP 563 a *quoted* annotation
   ``-> "Foo"`` is stored as the string ``"'Foo'"``, so that ``eval`` returns
   the **string** ``'Foo'`` — and a binding whose ``interface`` is a ``str``
   gets registered without complaint.
2. ``DIContainer._build_localns()`` (``providify/container.py``) then does
   ``binding.interface.__name__`` → ``AttributeError: 'str' object has no
   attribute '__name__'``.
3. ``DIContainer._collect_kwargs_sync()`` wraps that in
   ``except Exception: hints = {}`` — so **every** provider and constructor in
   the container is called with **zero** injected arguments from then on.

One malformed provider anywhere therefore disables injection container-wide,
and the only visible symptom is a baffling ``missing N required positional
arguments`` on some *unrelated* provider.  varco cannot repair providify from
here, but it must never ship a binding that triggers it.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from opentelemetry.sdk.trace import TracerProvider
from providify import DIContainer, Provider

# Module-scope imports on purpose: with ``from __future__ import annotations``
# a ``@Provider``'s return annotation is a lazy string that providify resolves
# against ``fn.__globals__``.  A type imported inside a test function is absent
# from those globals and ``container.provide()`` fails at registration time.
from varco_core.observability.config import OtelConfig
from varco_core.observability.di import OtelConfiguration
from varco_fastapi.di import VarcoFastAPIModule, setup_varco_defaults

VARCO_PACKAGES = (
    "varco_core",
    "varco_fastapi",
    "varco_sa",
    "varco_redis",
    "varco_kafka",
    "varco_beanie",
    "varco_casbin",
    "varco_nats",
    "varco_memcached",
    "varco_ws",
)


def _iter_modules():
    """Yield every importable module of every varco package."""
    for pkg_name in VARCO_PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError:  # optional backend deps absent — nothing to check
            continue
        yield pkg
        for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg_name}."):
            try:
                yield importlib.import_module(info.name)
            except ImportError:
                continue


def _iter_provider_callables():
    """Yield ``(qualified_name, source_location, fn)`` for every ``@Provider``."""
    from providify.metadata import _get_provider_metadata

    seen: set[int] = set()
    for mod in _iter_modules():
        for name, obj in vars(mod).items():
            if getattr(obj, "__module__", None) != mod.__name__:
                continue
            candidates = []
            if inspect.isfunction(obj):
                candidates.append((f"{mod.__name__}.{name}", obj))
            elif inspect.isclass(obj):
                candidates.extend(
                    (f"{mod.__name__}.{name}.{m}", fn)
                    for m, fn in vars(obj).items()
                    if inspect.isfunction(fn)
                )
            for qual, fn in candidates:
                if id(fn) in seen or _get_provider_metadata(fn) is None:
                    continue
                seen.add(id(fn))
                try:
                    loc = f"{inspect.getsourcefile(fn)}:{inspect.getsourcelines(fn)[1]}"
                except OSError:  # pragma: no cover - source-less callable
                    loc = "<unknown>"
                yield qual, loc, fn


class TestProviderReturnAnnotations:
    def test_regression_no_provider_uses_a_quoted_return_annotation(self) -> None:
        """
        A quoted return annotation is the one shape that makes providify
        register a ``str`` interface (see module docstring, step 1), which
        silently disables injection for the whole container.

        Every varco module already has ``from __future__ import annotations``,
        so quoting is never needed — the annotation is lazy either way.  Import
        the type at module scope and drop the quotes instead.
        """
        offenders = [
            f"{qual} at {loc} -> {fn.__annotations__.get('return')!r}"
            for qual, loc, fn in _iter_provider_callables()
            if isinstance(fn.__annotations__.get("return"), str)
            and fn.__annotations__["return"].strip().startswith(("'", '"'))
        ]
        assert not offenders, (
            "@Provider return annotations must not be quoted under PEP 563 — "
            "providify registers the resulting string as the binding interface "
            "and every Inject[...] in the container is then silently dropped:\n  "
            + "\n  ".join(offenders)
        )

    def test_regression_every_provider_return_type_resolves_to_a_type(self) -> None:
        """
        The generalisation of the test above: whatever providify ends up using
        as the binding interface must be a real type (or a generic alias), not
        a string.
        """
        from typing import get_origin, get_type_hints

        offenders = []
        for qual, loc, fn in _iter_provider_callables():
            try:
                iface = get_type_hints(fn).get("return")
            except Exception as exc:  # noqa: BLE001 - the failure IS the finding
                offenders.append(f"{qual} at {loc}: get_type_hints failed: {exc}")
                continue
            if iface is None:
                offenders.append(f"{qual} at {loc}: no resolvable return type")
            elif not isinstance(iface, type) and get_origin(iface) is None:
                offenders.append(f"{qual} at {loc}: interface {iface!r} is not a type")
        assert not offenders, "\n  ".join(
            ["Broken @Provider return types:", *offenders]
        )


class TestContainerLocalnsHealth:
    """
    ``DIContainer._build_localns()`` is the single choke point: if it raises,
    every ``Inject[...]`` parameter in the container is silently dropped.
    """

    def test_localns_build_never_raises_for_a_full_varco_container(self) -> None:
        container = DIContainer()
        for pkg in VARCO_PACKAGES:
            try:
                container.scan(pkg, recursive=True)
            except ImportError:  # pragma: no cover - optional backend absent
                continue
        container.install(VarcoFastAPIModule)
        setup_varco_defaults(container)
        container.install(OtelConfiguration)

        container._build_localns()  # must not raise

        poisoned = [b for b in container._bindings if isinstance(b.interface, str)]
        assert not poisoned, f"str-interface bindings registered: {poisoned!r}"


@Provider(singleton=True)
def _composite_otel_config() -> OtelConfig:
    """App-supplied ``OtelConfig`` — module scope so its annotation resolves."""
    return OtelConfig(service_name="composite-svc")


class TestVarcoFastApiModuleWithOtelConfiguration:
    """The literal scenario from the bug report."""

    @pytest.mark.parametrize("otel_first", [False, True])
    def test_regression_tracer_provider_resolves_with_both_modules(
        self, otel_first: bool
    ) -> None:
        otel_config = _composite_otel_config

        container = DIContainer()
        container.provide(otel_config)
        if otel_first:
            container.install(OtelConfiguration)
            container.install(VarcoFastAPIModule)
        else:
            container.install(VarcoFastAPIModule)
            container.install(OtelConfiguration)

        provider = container.get(TracerProvider)

        assert isinstance(provider, TracerProvider)
        # Proves Inject[OtelConfig] really resolved rather than being dropped.
        assert provider.resource.attributes["service.name"] == "composite-svc"
