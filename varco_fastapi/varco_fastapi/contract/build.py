"""
varco_fastapi.contract.build
===============================
``build_contract(router_cls)`` — translates ``introspect_routes()``'s
``list[ResolvedRoute]`` into a portable ``ServiceContract`` (Plan 009,
Phase 0 / C3 part 1). See the module docstring in ``varco_fastapi.contract``
for the two-consumer picture this feeds (in-process client metaclass,
cross-repo ``contract_client``/``gen-client``).

Thread safety:  ✅ Pure function; a fresh ``SchemaCollector`` per call.
Async safety:   ✅ No I/O — introspection is synchronous.
"""

from __future__ import annotations

from typing import Any

from varco_fastapi.contract.model import (
    CONTRACT_VERSION,
    ParamContract,
    RouteContract,
    ServiceContract,
)
from varco_fastapi.contract.schema import SchemaCollector
from varco_fastapi.router.base import _resolve_type_args
from varco_fastapi.router.introspection import ResolvedRoute, introspect_routes


def build_contract(
    router_cls: type,
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    enabled_routes: set[str] | None = None,
    strict: bool = False,
) -> ServiceContract:
    """
    Build a ``ServiceContract`` for ``router_cls``.

    Args:
        router_cls:      A ``VarcoRouter`` subclass (CRUD or generic).
        service_name:    Contract's ``service_name``. Defaults to
                          ``router_cls.__name__``.
        service_version: Optional app-level version string, independent of
                          ``contract_version`` (the wire-format version).
        enabled_routes:  Optional route-name allow-list, passed through to
                          ``introspect_routes()``.
        strict:          When ``True``, an unresolvable parameter annotation
                          raises instead of degrading to ``Any``/``dict``. For
                          CI use — catches ``TYPE_CHECKING``-only forward refs
                          that would otherwise silently lose typing.

    Returns:
        A ``ServiceContract`` with ``contract_version=CONTRACT_VERSION``.

    Raises:
        ValueError: two params on the same route share a name but differ in
            ``kind`` (e.g. a path ``{id}`` and a query ``id``) — ambiguous for
            client generation.
        Exception: (when ``strict=True``) a parameter's annotation could not
            be resolved to a concrete type.

    Edge cases:
        - A router with zero routes returns a valid contract (``routes=()``).
        - A model referenced by multiple routes is emitted once in ``schemas``
          (``SchemaCollector`` dedupes by model name).
    """
    type_args = _resolve_type_args(router_cls)
    resolved = introspect_routes(router_cls, enabled_routes=enabled_routes, type_args=type_args)

    collector = SchemaCollector()
    routes: list[RouteContract] = [
        _build_route_contract(r, collector, strict=strict) for r in resolved
    ]

    return ServiceContract(
        contract_version=CONTRACT_VERSION,
        service_name=service_name or router_cls.__name__,
        routes=tuple(routes),
        schemas=collector.schemas,
        service_version=service_version,
    )


def _build_route_contract(
    route: ResolvedRoute, collector: SchemaCollector, *, strict: bool
) -> RouteContract:
    params: list[ParamContract] = []
    seen_by_name: dict[str, str] = {}

    for spec in route.param_specs:
        if strict and spec.annotation is None:
            raise ValueError(
                f"Route {route.name!r}: parameter {spec.name!r} has an unresolvable "
                f"annotation and strict=True was requested."
            )
        prior_kind = seen_by_name.get(spec.name)
        if prior_kind is not None and prior_kind != spec.kind:
            raise ValueError(
                f"Route {route.name!r}: parameter {spec.name!r} appears as both "
                f"{prior_kind!r} and {spec.kind!r} — ambiguous for client generation."
            )
        seen_by_name[spec.name] = spec.kind

        schema = collector.schema_for(spec.annotation) or {"type": "object"}
        params.append(
            ParamContract(
                name=spec.name,
                kind=spec.kind,
                schema=schema,
                required=spec.required,
                default=spec.default if _is_jsonable(spec.default) else None,
                description=spec.description,
            )
        )

    request_schema = collector.schema_for(route.request_model)
    response_schema = collector.schema_for(route.response_model)

    return RouteContract(
        name=route.name,
        method=route.method,
        path=route.path,
        params=tuple(params),
        request_schema=request_schema,
        response_schema=response_schema,
        status_code=route.status_code,
        is_crud=route.is_crud,
        crud_action=route.crud_action,
        async_capable=route.async_capable,
        deprecated=route.deprecated,
        summary=route.summary,
        description=route.description,
        tags=route.tags,
    )


def _is_jsonable(value: Any) -> bool:
    """Best-effort JSON-serializability check for a param default."""
    import json

    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False
