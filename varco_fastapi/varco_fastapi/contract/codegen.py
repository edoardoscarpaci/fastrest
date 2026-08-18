"""
varco_fastapi.contract.codegen
=================================
``render_client_module`` — emits a standalone ``.py`` client from a
``ServiceContract`` (Plan 009, Phase 8 / C3 part 2).

Hand-rolled JSON-Schema → Pydantic emitter, scoped to what ``varco_fastapi``
itself ever emits into ``ServiceContract.schemas`` (object/array/scalar/
``$ref``/nullable) — not a general-purpose JSON Schema compiler.

DESIGN: hand-rolled emitter over a `datamodel-code-generator` dependency
    ✅ No new runtime dependency for the common case.
    ✅ Bounded input shape (`schema.py`'s own output) keeps the emitter small.
    ❌ Anything outside that bounded set degrades to ``dict[str, Any]`` with
       an emitted ``# TODO: unsupported schema`` comment — honest and
       visible. The ``schemas`` block is still OpenAPI-``$defs``-shaped, so
       ``datamodel-code-generator`` remains a valid escape hatch for a
       consumer who wants full JSON-Schema coverage.

Every route method's parameter shape is built through the SAME logic as
``build_client_method`` (mirrored here as literal source text — see
``_render_route_method``) — the two must never diverge; the load-bearing
parity guarantee is enforced by ``test_signature_parity``
(``test_contract_codegen.py``), not by this docstring's promise alone.

Thread safety:  ✅ Pure function.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

from typing import Any

_JSON_TO_PY: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}


def _py_type_for_schema(
    schema: dict[str, Any] | None, *, known_models: set[str]
) -> str:
    """Render a Python type annotation (as source text) for a schema fragment."""
    if not schema:
        return "Any"
    ref = schema.get("$ref")
    if ref:
        name = str(ref).rsplit("/", 1)[-1]
        return name if name in known_models else "dict[str, Any]"
    t = schema.get("type")
    if t in _JSON_TO_PY:
        return _JSON_TO_PY[t]
    if t == "array":
        item = _py_type_for_schema(schema.get("items"), known_models=known_models)
        return f"list[{item}]"
    if t == "object" and "properties" in schema:
        return "dict[str, Any]"
    return "Any"


def _render_model(name: str, frag: dict[str, Any], *, known_models: set[str]) -> str:
    """Render one pydantic model class, or a `dict[str, Any]` fallback alias."""
    properties = frag.get("properties") if isinstance(frag, dict) else None
    if not isinstance(properties, dict):
        # Unbounded / unrecognized schema shape — degrade honestly, visibly.
        return f"{name} = dict[str, Any]  # TODO: unsupported schema\n"

    required = set(frag.get("required", []))
    lines = [f"class {name}(BaseModel):"]
    if not properties:
        lines.append("    pass")
    for pname, pschema in properties.items():
        py_type = _py_type_for_schema(pschema, known_models=known_models)
        if pname not in required:
            py_type = f"{py_type} | None"
            lines.append(f"    {pname}: {py_type} = None")
        else:
            lines.append(f"    {pname}: {py_type}")
    return "\n".join(lines) + "\n"


def _render_route_method(route: Any, *, known_models: set[str]) -> str:
    """
    Render one ``async def`` route method — mirrors
    ``build_client_method``'s parameter shape (body positional, everything
    else keyword-only, ``with_async`` last).
    """
    body_params = [p for p in route.params if p.kind == "body"]
    other_params = [p for p in route.params if p.kind != "body"]
    reserved = {"self", "with_async"}

    def safe(n: str) -> str:
        return f"{n}_" if n in reserved else n

    sig_parts = ["self"]
    body_param = body_params[0] if body_params else None
    if body_param is not None:
        ann = _py_type_for_schema(body_param.schema, known_models=known_models)
        default = "" if body_param.required else " = None"
        sig_parts.append(f"{safe(body_param.name)}: {ann}{default}")

    if other_params or route.async_capable:
        sig_parts.append("*")
        for p in other_params:
            ann = _py_type_for_schema(p.schema, known_models=known_models)
            default = "" if p.required else " = None"
            sig_parts.append(f"{safe(p.name)}: {ann}{default}")
        if route.async_capable:
            sig_parts.append("with_async: bool = False")

    response_ann = _py_type_for_schema(route.response_schema, known_models=known_models)
    sig = ", ".join(sig_parts)

    path_kwargs = ", ".join(
        f'"{p.name}": {safe(p.name)}' for p in route.params if p.kind == "path"
    )
    query_kwargs = ", ".join(
        f'"{p.name}": {safe(p.name)}' for p in route.params if p.kind == "query"
    )
    body_expr = safe(body_param.name) if body_param is not None else "None"

    return (
        f"    async def {route.name}({sig}) -> {response_ann}:\n"
        f"        query = {{{query_kwargs}}}\n"
        f"        if with_async:\n"
        f'            query["with_async"] = "true"\n'
        f"        return await self._request(\n"
        f'            "{route.method}", "{route.path}",\n'
        f"            body={body_expr},\n"
        f"            path_params={{{path_kwargs}}},\n"
        f"            query_params=query,\n"
        f"            response_model=None,\n"
        f"            expected_status={route.status_code},\n"
        f"        )\n"
    )


def render_client_module(contract: Any, *, class_name: str) -> str:
    """
    Emit a standalone ``.py``: Pydantic models from ``contract.schemas`` + a
    typed ``AsyncVarcoClient`` subclass with one method per route.

    Args:
        contract:   The ``ServiceContract`` to render.
        class_name: Name of the generated client class.

    Returns:
        Valid, ``ast.parse``-clean Python source. Importable with only
        ``varco-fastapi`` installed — no dependency on the origin service's
        own package.
    """
    known_models = set(contract.schemas.keys())

    lines: list[str] = [
        "# Auto-generated by `varco gen-client` — DO NOT EDIT.",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from pydantic import BaseModel",
        "from varco_fastapi.client import AsyncVarcoClient",
        "",
        "",
    ]

    for name, frag in contract.schemas.items():
        lines.append(_render_model(name, frag, known_models=known_models))
        lines.append("")

    lines.append(f"class {class_name}(AsyncVarcoClient):")
    if not contract.routes:
        lines.append("    ...")
    else:
        for route in contract.routes:
            lines.append(_render_route_method(route, known_models=known_models))

    return "\n".join(lines) + "\n"


__all__ = ["render_client_module"]
