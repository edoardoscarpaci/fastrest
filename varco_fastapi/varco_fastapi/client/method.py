"""
varco_fastapi.client.method
==============================
``build_client_method`` — synthesizes one typed client method from a
``RouteContract`` (Plan 009, Phase 7 / C2).

The single mechanism shared by BOTH client-generation paths:

- **In-process** (``_VarcoClientMeta``, custom-route branch): drives this
  with ``ImportedTypeResolver`` — schema ``$ref`` → the real Pydantic class.
- **Cross-repo** (``contract_client()`` / ``varco gen-client``, Phase 8):
  drives this with ``SynthesizedTypeResolver`` — schema ``$ref`` →
  ``pydantic.create_model()``.

Both produce a method with an *equal* ``__signature__`` for the same route —
enforced by construction (one function, two resolvers), not by discipline.
``test_resolver_parity`` (``test_client_typed_routes.py``) is the load-bearing
test for this guarantee; do not delete it.

DESIGN: keyword-only for everything except the body
    ✅ Adding a query param later is never a positional-arity break for
       callers — only ever a KeyError-shaped surprise if they relied on
       positional args in the first place, which the signature forbids.
    ❌ Slightly more verbose call sites (`cancel(reason, order_id=..., limit=...)`).

Thread safety:  ✅ Pure function; builds a new closure per call.
Async safety:   ✅ The synthesized method is itself ``async def``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

_SCALAR_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}

# Framework-reserved names on the synthesized signature — a route parameter
# colliding with one is renamed with a trailing underscore (Phase 7 edge case).
_RESERVED_PARAM_NAMES = {"self", "with_async"}


class TypeResolver(Protocol):
    """Maps a contract JSON-Schema fragment to a runtime type (or ``None``)."""

    def resolve(self, schema: Mapping[str, Any] | None) -> type | None: ...


class ImportedTypeResolver:
    """
    In-process resolver: schema ``$ref`` → the real Pydantic class, resolved
    by re-introspecting the (importable) router class.

    Args:
        contract:   The ``ServiceContract`` built from ``router_cls``.
        router_cls: The actual, importable ``VarcoRouter`` subclass.
    """

    def __init__(self, contract: Any, router_cls: type) -> None:
        from varco_fastapi.router.base import _resolve_type_args
        from varco_fastapi.router.introspection import introspect_routes

        self._contract = contract
        self._by_name: dict[str, type] = {}

        type_args = _resolve_type_args(router_cls)
        for route in introspect_routes(router_cls, type_args=type_args):
            for model in (route.request_model, route.response_model):
                if model is not None:
                    self._by_name[model.__name__] = model
            for spec in route.param_specs:
                if spec.annotation is not None:
                    self._by_name[spec.annotation.__name__] = spec.annotation

    def resolve(self, schema: Mapping[str, Any] | None) -> type | None:
        if schema is None:
            return None
        ref = schema.get("$ref")
        if ref:
            name = str(ref).rsplit("/", 1)[-1]
            return self._by_name.get(name)
        return _SCALAR_TYPES.get(schema.get("type"))  # type: ignore[arg-type]


class SynthesizedTypeResolver:
    """
    Cross-repo resolver: schema ``$ref`` → ``pydantic.create_model()``, built
    entirely from the exported ``ServiceContract.schemas`` — no router
    import required.

    Args:
        contract: The ``ServiceContract`` to resolve schemas from.
    """

    def __init__(self, contract: Any) -> None:
        self._contract = contract
        self._cache: dict[str, type] = {}

    def resolve(self, schema: Mapping[str, Any] | None) -> type | None:
        if schema is None:
            return None
        ref = schema.get("$ref")
        if ref:
            name = str(ref).rsplit("/", 1)[-1]
            return self._build_model(name)
        return _SCALAR_TYPES.get(schema.get("type"))  # type: ignore[arg-type]

    def _build_model(self, name: str) -> type | None:
        if name in self._cache:
            return self._cache[name]
        frag = self._contract.schemas.get(name)
        if frag is None:
            return None
        import pydantic

        properties = frag.get("properties", {})
        required = set(frag.get("required", []))
        fields: dict[str, Any] = {}
        for pname, pschema in properties.items():
            ptype = self.resolve(pschema) or Any
            fields[pname] = (ptype, ... if pname in required else None)
        model = pydantic.create_model(name, **fields)  # type: ignore[call-overload]
        self._cache[name] = model
        return model


def build_client_method(
    route: Any,
    resolver: TypeResolver,
    *,
    async_capable_returns_job: bool = True,
) -> Callable[..., Awaitable[Any]]:
    """
    Synthesize one client method with a real ``__signature__``/``__annotations__``.

    Args:
        route:    A ``RouteContract`` (see ``varco_fastapi.contract.model``).
        resolver: Maps each param's JSON-Schema fragment to a runtime type.
        async_capable_returns_job: Whether ``with_async=True`` is offered on
            an ``async_capable`` route.

    Returns:
        An ``async def`` callable whose ``__signature__`` is
        ``(self, <body_param>?, *, <path params>, <query params>,
        with_async: bool = False)`` — body positional, everything else
        keyword-only.

    Edge cases:
        - A route parameter named ``self``/``with_async`` is renamed with a
          trailing underscore in the signature and mapped back to its real
          wire name at send time.
        - Zero params → ``(self, *, with_async: bool = False)``.
        - No response model → return annotation ``None``.
    """
    P = inspect.Parameter

    body_params = [p for p in route.params if p.kind == "body"]
    other_params = [p for p in route.params if p.kind != "body"]

    params: list[inspect.Parameter] = [P("self", kind=P.POSITIONAL_OR_KEYWORD)]
    wire_names: dict[str, str] = {}  # synthesized name -> original wire name
    kinds: dict[str, str] = {}  # synthesized name -> "path"/"query"/"body"/"header"

    def _safe_name(name: str) -> str:
        return f"{name}_" if name in _RESERVED_PARAM_NAMES else name

    body_param = body_params[0] if body_params else None
    if body_param is not None:
        safe = _safe_name(body_param.name)
        wire_names[safe] = body_param.name
        kinds[safe] = "body"
        annotation = resolver.resolve(body_param.schema) or Any
        default = P.empty if body_param.required else body_param.default
        params.append(
            P(
                safe,
                kind=P.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=annotation,
            )
        )

    for p in other_params:
        safe = _safe_name(p.name)
        wire_names[safe] = p.name
        kinds[safe] = p.kind
        annotation = resolver.resolve(p.schema) or Any
        default = P.empty if p.required else p.default
        params.append(
            P(safe, kind=P.KEYWORD_ONLY, default=default, annotation=annotation)
        )

    if route.async_capable and async_capable_returns_job:
        params.append(
            P("with_async", kind=P.KEYWORD_ONLY, default=False, annotation=bool)
        )

    response_type = resolver.resolve(route.response_schema)
    signature = inspect.Signature(params, return_annotation=response_type)

    async def _method(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        call_kwargs = dict(bound.arguments)
        self_obj = call_kwargs.pop("self")
        with_async = call_kwargs.pop("with_async", False)

        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        body_value: Any = None
        for synth_name, value in call_kwargs.items():
            kind = kinds.get(synth_name)
            wire_name = wire_names.get(synth_name, synth_name)
            if kind == "path":
                path_params[wire_name] = value
            elif kind == "body":
                body_value = value
            elif kind in ("query", "header"):
                query_params[wire_name] = value

        path = route.path
        query = dict(query_params)
        if with_async:
            query["with_async"] = "true"

        return await self_obj._request(
            route.method,
            path,
            body=body_value,
            path_params=path_params,
            query_params=query,
            response_model=response_type,
            expected_status=route.status_code,
        )

    _method.__signature__ = signature  # type: ignore[attr-defined]
    _method.__name__ = route.name
    _method.__annotations__ = {
        p.name: p.annotation for p in params if p.annotation is not P.empty
    }
    return _method


__all__ = [
    "ImportedTypeResolver",
    "SynthesizedTypeResolver",
    "TypeResolver",
    "build_client_method",
]
