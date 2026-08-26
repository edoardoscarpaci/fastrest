"""
varco_fastapi.client.openapi_gen
==================================
Code generator that writes a typed Python client file from an OpenAPI 3.x spec.

The generated file contains:
- Pydantic ``BaseModel`` subclasses for every ``#/components/schemas`` entry.
- A ``GenericClient`` subclass with typed async methods for every path operation.

The generated file can be imported directly — no additional runtime step needed.

CLI entry point::

    varco-gen petstore.json -o ./client.py
    varco-gen https://api.example.com/openapi.json -o ./client.py --class-name MyClient

DESIGN: static file generation alongside a runtime client
    ✅ Generated file has IDE autocomplete + static type checking
    ✅ No dependency on the spec at runtime — generated file is self-contained
    ✅ Same logic as OpenAPIClient so runtime and generated clients are consistent
    ❌ Spec changes require re-running the generator
    Alternative considered: stub generation (``*.pyi``) — rejected because it
    would still require the runtime ``OpenAPIClient`` without giving full
    autocomplete in non-stub-aware editors.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ── Type name helpers ────────────────────────────────────────────────────────


def _to_snake(name: str) -> str:
    """
    Convert camelCase / PascalCase / path slug to snake_case.

    Args:
        name: Input string.

    Returns:
        snake_case version.
    """
    name = name.replace("/", "_").replace("{", "").replace("}", "")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name.lower()


def _to_pascal(name: str) -> str:
    """
    Convert a snake_case or camelCase name to PascalCase.

    Args:
        name: Input string.

    Returns:
        PascalCase version.
    """
    # Split on underscores, spaces, or camelCase boundaries
    parts = re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name)
    return "".join(p.capitalize() for p in parts if p)


# ── Ref / schema utilities ───────────────────────────────────────────────────


def _resolve_refs_gen(obj: Any, root: dict[str, Any]) -> Any:
    """
    Recursively replace ``$ref`` pointers with their referents (same as
    ``openapi._resolve_refs`` but self-contained for the generator).

    Args:
        obj:  Object to resolve.
        root: Full spec root.

    Returns:
        Ref-resolved object.
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref.startswith("#/"):
                parts = ref[2:].split("/")
                target: Any = root
                for part in parts:
                    part = part.replace("~1", "/").replace("~0", "~")
                    target = target[part]
                return _resolve_refs_gen(target, root)
        return {k: _resolve_refs_gen(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs_gen(item, root) for item in obj]
    return obj


def _get_ref_name(schema_obj: dict[str, Any]) -> str | None:
    """
    Return the schema name from a ``$ref`` string, or ``None``.

    Args:
        schema_obj: Possibly a ``{"$ref": "#/components/schemas/Foo"}`` dict.

    Returns:
        ``"Foo"`` or ``None``.
    """
    ref = schema_obj.get("$ref", "")
    if ref.startswith("#/components/schemas/"):
        return ref[len("#/components/schemas/") :]
    return None


def _schema_to_type_str(
    schema: dict[str, Any],
    name_hint: str = "Any",
    inline_models: dict[str, str] | None = None,
) -> str:
    """
    Convert a schema dict to a Python type annotation string (for code gen).

    Inline objects are accumulated in ``inline_models`` for deferred output.

    Args:
        schema:        Resolved schema dict.
        name_hint:     Used as class name for inline objects.
        inline_models: Accumulator for inline model source lines.

    Returns:
        Type annotation string (e.g. ``"str"``, ``"int"``, ``"list[Pet]"``).

    Edge cases:
        - Inline object → a Pydantic model class source is stored in
          ``inline_models`` and the model name is returned as the type.
        - Unknown type → ``"Any"``.
    """
    if inline_models is None:
        inline_models = {}

    if not schema or not isinstance(schema, dict):
        return "Any"

    # $ref to a named schema component
    ref_name = _get_ref_name(schema)
    if ref_name:
        return ref_name

    for combiner in ("allOf", "anyOf", "oneOf"):
        if combiner in schema:
            sub = schema[combiner]
            if sub and isinstance(sub, list):
                return _schema_to_type_str(sub[0], name_hint, inline_models)
            return "Any"

    t = schema.get("type")
    if t == "string":
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        items = schema.get("items", {})
        inner = _schema_to_type_str(items, f"{name_hint}Item", inline_models)
        return f"list[{inner}]"
    if t == "object" or "properties" in schema:
        model_name = _to_pascal(name_hint) if name_hint else "InlineModel"
        if model_name not in inline_models:
            # Build the class source and store it
            lines: list[str] = [f"class {model_name}(BaseModel):"]
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if props:
                for field_name, field_schema in props.items():
                    field_type = _schema_to_type_str(
                        field_schema, f"{model_name}_{field_name}", inline_models
                    )
                    if field_name in required:
                        lines.append(f"    {field_name}: {field_type}")
                    else:
                        lines.append(f"    {field_name}: {field_type} | None = None")
            else:
                lines.append("    pass")
            inline_models[model_name] = "\n".join(lines)
        return model_name
    return "Any"


# ── Spec → code ──────────────────────────────────────────────────────────────


def _generate_source(
    spec: dict[str, Any],
    class_name: str,
    base_url: str | None = None,
) -> str:
    """
    Convert a full OpenAPI spec dict to a Python source string.

    Args:
        spec:       The OpenAPI spec dict.
        class_name: Name of the generated client class.
        base_url:   Optional base URL embedded in the generated class docstring.

    Returns:
        A complete Python source string ready to be written to a ``.py`` file.

    Edge cases:
        - Schemas with ``$ref`` to other schemas are resolved before type mapping.
        - Path operations with no ``operationId`` derive a name from verb + path.
        - Duplicate operation names get a numeric suffix.
    """
    spec_source = spec.get("info", {}).get("title", "unknown spec")

    # ── File header ─────────────────────────────────────────────────────────
    lines: list[str] = [
        f"# Generated by varco-gen from {spec_source}",
        "from __future__ import annotations",
        "from typing import Any",
        "from pydantic import BaseModel",
        "from varco_fastapi.client import GenericClient",
        "",
    ]

    # ── Component schemas → Pydantic models ─────────────────────────────────
    schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    inline_models: dict[str, str] = {}

    # Build schema model source in two passes to handle cross-refs cleanly
    schema_model_lines: dict[str, str] = {}
    for name, schema_def in schemas.items():
        resolved = _resolve_refs_gen(schema_def, spec)
        model_name = _to_pascal(name)
        model_lines: list[str] = [f"class {model_name}(BaseModel):"]
        props = resolved.get("properties", {})
        required_fields: list[str] = resolved.get("required", [])
        if props:
            for field_name, field_schema in props.items():
                resolved_field = _resolve_refs_gen(field_schema, spec)
                field_type = _schema_to_type_str(
                    resolved_field, f"{model_name}_{field_name}", inline_models
                )
                if field_name in required_fields:
                    model_lines.append(f"    {field_name}: {field_type}")
                else:
                    model_lines.append(f"    {field_name}: {field_type} | None = None")
        else:
            model_lines.append("    pass")
        schema_model_lines[model_name] = "\n".join(model_lines)

    # Output inline models first (they may be referenced by named ones)
    for model_src in inline_models.values():
        lines.append(model_src)
        lines.append("")

    # Output named schema models
    for model_src in schema_model_lines.values():
        lines.append(model_src)
        lines.append("")

    # ── Client class ─────────────────────────────────────────────────────────
    class_lines: list[str] = [f"class {class_name}(GenericClient):"]
    # Add a note about the base URL when available
    _base = base_url or spec.get("servers", [{}])[0].get("url", "") if spec.get("servers") else ""
    if _base:
        class_lines.append(f'    """Generated client for {spec_source}. Base URL: {_base}"""')

    method_names_seen: set[str] = set()

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for verb in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(verb)
            if not operation:
                continue

            # Derive method name
            raw_name: str = operation.get("operationId") or f"{verb}_{path}"
            method_name = _to_snake(raw_name)
            # Deduplicate
            final_name = method_name
            counter = 1
            while final_name in method_names_seen:
                final_name = f"{method_name}_{counter}"
                counter += 1
            method_names_seen.add(final_name)

            # Path params
            path_param_names: list[str] = re.findall(r"\{(\w+)\}", path)

            # Query params
            query_params: list[dict[str, Any]] = [
                _resolve_refs_gen(p, spec)
                for p in (operation.get("parameters") or [])
                if _resolve_refs_gen(p, spec).get("in") == "query"
            ]

            # Request body type
            body_type_str: str | None = None
            req_body = operation.get("requestBody")
            if req_body:
                req_body_r = _resolve_refs_gen(req_body, spec)
                content = req_body_r.get("content", {})
                json_schema = content.get("application/json", {}).get("schema", {})
                if json_schema:
                    json_schema_r = _resolve_refs_gen(json_schema, spec)
                    body_type_str = _schema_to_type_str(
                        json_schema_r, f"{final_name}_body", inline_models
                    )

            # Response type
            response_type_str: str | None = None
            responses = operation.get("responses", {})
            for sc in ("200", "201"):
                resp = responses.get(sc)
                if resp:
                    resp_r = _resolve_refs_gen(resp, spec)
                    content = resp_r.get("content", {})
                    resp_schema = content.get("application/json", {}).get("schema", {})
                    if resp_schema:
                        resp_schema_r = _resolve_refs_gen(resp_schema, spec)
                        response_type_str = _schema_to_type_str(
                            resp_schema_r, f"{final_name}_response", inline_models
                        )
                    break

            # Build method signature
            return_type = response_type_str or "dict[str, Any] | None"
            sig_parts: list[str] = ["self"]
            sig_parts.extend(path_param_names)  # positional path params
            if query_params:
                sig_parts.append("*")  # force keyword-only for query params
                for qp in query_params:
                    qp_name = qp.get("name", "param")
                    qp_required = qp.get("required", False)
                    if qp_required:
                        sig_parts.append(f"{qp_name}: str")
                    else:
                        sig_parts.append(f"{qp_name}: str | None = None")
            elif path_param_names and body_type_str:
                pass  # path params already positional; body follows
            if body_type_str:
                sig_parts.append(f"body: {body_type_str} | None = None")

            sig = ", ".join(sig_parts)

            # Build method body — call _request directly
            expected_status = 201 if verb == "post" else 200
            path_format = path
            for pn in path_param_names:
                path_format = path_format.replace(f"{{{pn}}}", f"{{{pn}}}")

            body_parts: list[str] = [
                f"    async def {final_name}({sig}) -> {return_type}:",
                "        return await self._request(",
                f"            {verb.upper()!r},",
                f"            {path_format!r}.format(**{{k: v for k, v in locals().items() if k in {path_param_names!r}}}),",
                "            body=body," if body_type_str else "            body=None,",
                f"            query_params={{k: str(v) for k, v in {{{', '.join(repr(qp['name']) + ': ' + qp['name'] for qp in query_params)}}}.items() if v is not None}},",
                f"            response_model={response_type_str or 'None'},",
                f"            expected_status={expected_status},",
                "        )",
            ]
            class_lines.extend(body_parts)

    if len(class_lines) == 1 or (len(class_lines) == 2 and class_lines[1].startswith('    """')):
        # Empty class — no operations found
        class_lines.append("    pass")

    lines.extend(class_lines)

    return "\n".join(lines) + "\n"


# ── Public API ────────────────────────────────────────────────────────────────


def generate_client(
    spec: str | Path | dict[str, Any],
    *,
    output_path: str | Path,
    class_name: str | None = None,
    base_url: str | None = None,
) -> Path:
    """
    Generate a typed Python client file from an OpenAPI spec.

    Writes Pydantic models and a ``GenericClient`` subclass with one typed
    async method per path operation to ``output_path``.

    Args:
        spec:        The OpenAPI spec — one of:
                     - ``str`` URL (``https://...``) → fetched with httpx,
                     - ``str`` / ``Path`` file path → read as JSON or YAML,
                     - ``dict`` → used directly.
        output_path: Where to write the generated ``.py`` file.
        class_name:  Name of the generated client class.  Defaults to the spec
                     title converted to PascalCase + ``"Client"``.
        base_url:    Optional base URL embedded as a comment in the class.

    Returns:
        The resolved ``Path`` of the written file.

    Raises:
        ImportError:  YAML spec provided without ``pyyaml`` installed.
        httpx.HTTPError: Remote spec URL fetch failed.
        OSError:      ``output_path`` cannot be written.

    Edge cases:
        - ``output_path`` parent directories must exist; this function does not
          create them.
        - When ``class_name`` is not given and the spec has no ``info.title``,
          the generated class is named ``GeneratedClient``.
    """
    # Resolve spec to a dict
    spec_dict: dict[str, Any]

    if isinstance(spec, dict):
        spec_dict = spec

    elif isinstance(spec, (str, Path)):
        spec_str = str(spec)

        if spec_str.startswith("http://") or spec_str.startswith("https://"):
            # Remote URL — fetch synchronously using httpx
            import httpx as _httpx  # noqa: PLC0415

            response = _httpx.get(spec_str, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            spec_dict = response.json()

        else:
            # Local file
            spec_path = Path(spec_str)
            suffix = spec_path.suffix.lower()
            if suffix in (".yaml", ".yml"):
                try:
                    import yaml  # noqa: PLC0415
                except ImportError as exc:
                    raise ImportError(
                        "YAML spec files require pyyaml. "
                        "Install it with: pip install 'varco-fastapi[openapi]'"
                    ) from exc
                with open(spec_path) as fh:
                    spec_dict = yaml.safe_load(fh)
            else:
                with open(spec_path) as fh:
                    spec_dict = json.load(fh)
    else:
        raise TypeError(f"spec must be a dict, str, or Path — got {type(spec).__name__!r}")

    # Derive class name from spec title when not provided
    if class_name is None:
        title = spec_dict.get("info", {}).get("title", "Generated")
        class_name = _to_pascal(title) + "Client"

    source = _generate_source(spec_dict, class_name=class_name, base_url=base_url)

    out = Path(output_path)
    out.write_text(source, encoding="utf-8")
    return out


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    """
    CLI entry point for ``varco-gen``.

    Usage::

        varco-gen petstore.json -o ./generated/client.py
        varco-gen https://api.example.com/openapi.json -o ./client.py --class-name MyClient

    Args (positional):
        spec: URL (``https://...``) or local file path.

    Flags:
        -o / --output    Required output file path.
        --class-name     Override the generated class name.
        --base-url       Embed a base URL in the generated file.

    Raises:
        SystemExit: On argument error or generation failure.
    """
    parser = argparse.ArgumentParser(
        prog="varco-gen",
        description="Generate a typed Python client from an OpenAPI 3.x spec.",
    )
    parser.add_argument("spec", help="OpenAPI spec: URL or local file path (JSON/YAML)")
    parser.add_argument("-o", "--output", required=True, help="Output .py file path")
    parser.add_argument(
        "--class-name",
        default=None,
        help="Generated client class name (default: derived from spec title)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL embedded in the generated class (default: from spec servers)",
    )

    args = parser.parse_args()

    try:
        out = generate_client(
            args.spec,
            output_path=args.output,
            class_name=args.class_name,
            base_url=args.base_url,
        )
        print(f"Generated: {out}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


__all__ = ["generate_client", "main"]
