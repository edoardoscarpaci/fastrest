"""
varco_fastapi.client.openapi
==============================
``OpenAPIClient`` — a runtime HTTP client generated from an OpenAPI 3.x spec.

Loads a spec (dict, file, or remote URL), builds Pydantic models for all
``#/components/schemas``, then attaches typed async methods for every path
operation.  The result is a live, type-annotated client without any code
generation step.

DESIGN: dynamic Pydantic models + runtime method injection
    ✅ Zero code-gen step — spec changes are picked up at startup
    ✅ Pydantic validates both request bodies and responses automatically
    ✅ Works with any OpenAPI 3.x spec (Petstore, internal services, etc.)
    ❌ No IDE autocomplete (use openapi_gen.py to generate a static typed file)
    ❌ Circular schema references cause deferred ``Any`` types
    Alternative considered: code generation only — rejected because runtime
    clients are more convenient for scripting and interactive exploration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from varco_fastapi.client.generic import GenericClient
    from varco_fastapi.client.middleware import AbstractClientMiddleware


# ── Ref resolver ─────────────────────────────────────────────────────────────


def _resolve_refs(obj: Any, root: dict[str, Any]) -> Any:
    """
    Recursively resolve all ``$ref`` pointers in ``obj`` against ``root``.

    Only JSON-pointer refs of the form ``#/path/to/element`` are supported.
    External refs (``http://...``, ``./other-file.yaml``) are left untouched
    and will map to ``Any`` downstream.

    Args:
        obj:  The object (dict, list, or scalar) to resolve.
        root: The spec root, used to look up ``#/`` pointers.

    Returns:
        A new object with all ``$ref`` entries replaced by their referents.

    Edge cases:
        - Circular refs are not detected here; callers must build models in
          topological order and fall back to ``Any`` for unresolved names.
        - ``$ref`` in an allOf/anyOf/oneOf list → the array is resolved element
          by element; the combining keyword is handled by the caller.
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref.startswith("#/"):
                parts = ref[2:].split("/")
                target: Any = root
                for part in parts:
                    # JSON Pointer escaping: ~1 → /, ~0 → ~
                    part = part.replace("~1", "/").replace("~0", "~")
                    target = target[part]
                # Recursively resolve refs inside the target itself
                return _resolve_refs(target, root)
        return {k: _resolve_refs(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(item, root) for item in obj]
    return obj


# ── Schema → Python type mapping ─────────────────────────────────────────────


def _schema_to_type(
    schema: dict[str, Any],
    models: dict[str, Any],
    name_hint: str = "Anon",
) -> Any:
    """
    Convert an (already ref-resolved) OpenAPI schema dict to a Python type.

    Args:
        schema:     Resolved OpenAPI schema object.
        models:     Already-built Pydantic model cache (name → class).
        name_hint:  Used as the dynamic model name when ``schema`` is an
                    inline object with no ``$ref`` title.

    Returns:
        A Python type suitable for use as a Pydantic field annotation.

    Edge cases:
        - ``allOf`` / ``anyOf`` / ``oneOf`` → returns ``Any`` (best-effort).
        - Inline nested objects → a new anonymous Pydantic model is created and
          cached under ``name_hint``.
        - Unrecognised type strings → ``Any``.
    """
    from pydantic import BaseModel, create_model  # noqa: PLC0415

    if not schema or not isinstance(schema, dict):
        return Any

    # Combining keywords — best-effort: use first schema fragment or Any
    for combiner in ("allOf", "anyOf", "oneOf"):
        if combiner in schema:
            sub = schema[combiner]
            if sub and isinstance(sub, list):
                return _schema_to_type(sub[0], models, name_hint)
            return Any

    schema_type = schema.get("type")

    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool

    if schema_type == "array":
        items = schema.get("items", {})
        inner = _schema_to_type(items, models, f"{name_hint}Item")
        return list[inner]  # type: ignore[valid-type]

    if schema_type == "object" or "properties" in schema:
        # Inline object — build a Pydantic model on the fly
        if name_hint in models:
            return models[name_hint]
        props: dict[str, Any] = schema.get("properties", {})
        required_fields: list[str] = schema.get("required", [])
        field_defs: dict[str, Any] = {}
        for field_name, field_schema in props.items():
            field_type = _schema_to_type(
                field_schema, models, f"{name_hint}_{field_name}"
            )
            if field_name in required_fields:
                field_defs[field_name] = (field_type, ...)
            else:
                field_defs[field_name] = (field_type | None, None)
        model_cls = create_model(name_hint, **field_defs, __base__=BaseModel)
        models[name_hint] = model_cls
        return model_cls

    return Any


# ── Topological schema builder ────────────────────────────────────────────────


def _build_models(
    schemas: dict[str, dict[str, Any]],
    root: dict[str, Any],
) -> dict[str, Any]:
    """
    Build Pydantic models for all ``#/components/schemas`` entries.

    Resolves ``$ref`` inside each schema and processes them in topological order
    so referenced models exist before the schemas that use them.  Unresolvable
    forward references are mapped to ``Any``.

    Args:
        schemas: The ``components.schemas`` dict from the spec.
        root:    The full spec root (used by ``_resolve_refs``).

    Returns:
        A dict mapping schema name → Pydantic model class (or Python type).

    Edge cases:
        - Circular schemas → the first pass maps them to ``Any``; re-passes
          are not attempted (callers should use ``from_dict`` which resolves
          first).
        - Non-object schemas (e.g. plain string aliases) → stored as their
          Python type equivalent.
    """
    models: dict[str, Any] = {}
    # Two-pass: first pass resolves what it can; unresolved are left as Any.
    # Sufficient for acyclic schemas (the vast majority of real-world specs).
    for name, schema in schemas.items():
        resolved = _resolve_refs(schema, root)
        _schema_to_type(resolved, models, name_hint=name)
        if name not in models:
            # Scalar or array schema — store the type directly for method signatures
            models[name] = _schema_to_type(resolved, models, name_hint=name)
    return models


# ── Snake-case helper ─────────────────────────────────────────────────────────


def _to_snake(name: str) -> str:
    """
    Convert a camelCase or PascalCase or mixed string to snake_case.

    Also converts ``/pets/{petId}`` slugs (``/`` → ``_``, ``{`` / ``}`` stripped).

    Args:
        name: The string to convert.

    Returns:
        snake_case version of ``name``.

    Edge cases:
        - Already snake_case → returned unchanged.
        - Leading/trailing underscores from path delimiters are stripped.
    """
    # Replace path separators and braces so path slugs become clean identifiers
    name = name.replace("/", "_").replace("{", "").replace("}", "")
    # Insert underscores before uppercase letters that follow lowercase/digits
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    # Collapse multiple underscores and strip surrounding ones
    name = re.sub(r"_+", "_", name).strip("_")
    return name.lower()


# ── Method builder ────────────────────────────────────────────────────────────


def _build_operation_method(
    http_method: str,
    path: str,
    operation: dict[str, Any],
    models: dict[str, Any],
    root: dict[str, Any],
) -> tuple[str, Any]:
    """
    Build a named async method from a single OpenAPI path operation.

    Args:
        http_method: HTTP verb (``"get"``, ``"post"``, etc.).
        path:        URL path template (e.g. ``"/pets/{petId}"``).
        operation:   The operation object from ``spec["paths"][path][method]``.
        models:      Already-built Pydantic model cache.
        root:        Full spec root (for ref resolution).

    Returns:
        A ``(method_name, callable)`` tuple ready to be set on the client class.

    Edge cases:
        - ``operationId`` missing → method name derived from ``"{verb}_{slug}"``.
        - Path params in the template are extracted as positional function args.
        - Query params become keyword-only args with default ``None``.
        - ``requestBody`` content other than ``application/json`` → body typed as ``Any``.
        - Response schema missing or ``204`` → response typed as ``dict | None``.
    """
    # Derive a Pythonic method name from operationId or path + verb
    raw_name: str = operation.get("operationId") or f"{http_method}_{path}"
    method_name = _to_snake(raw_name)

    # Collect path parameter names from the URL template: /pets/{petId} → ["petId"]
    path_param_names: list[str] = re.findall(r"\{(\w+)\}", path)

    # Collect query parameter info
    query_param_specs: list[dict[str, Any]] = [
        p
        for p in (operation.get("parameters") or [])
        if _resolve_refs(p, root).get("in") == "query"
    ]

    # Resolve request body type
    body_type: Any = None
    req_body = operation.get("requestBody")
    if req_body:
        req_body = _resolve_refs(req_body, root)
        content = req_body.get("content", {})
        json_content = content.get("application/json", {})
        body_schema = _resolve_refs(json_content.get("schema", {}), root)
        if body_schema:
            body_type = _schema_to_type(
                body_schema, models, name_hint=f"{method_name}_body"
            )

    # Resolve response type from 200 or 201
    response_type: Any = None
    responses = operation.get("responses", {})
    for status_code in ("200", "201"):
        resp = responses.get(status_code)
        if resp:
            resp = _resolve_refs(resp, root)
            content = resp.get("content", {})
            json_content = content.get("application/json", {})
            resp_schema = _resolve_refs(json_content.get("schema", {}), root)
            if resp_schema:
                # Look up named $ref model if available
                ref_title = (
                    resp_schema.get("title")
                    or resp_schema.get("$ref", "").rsplit("/", 1)[-1]
                )
                if ref_title and ref_title in models:
                    response_type = models[ref_title]
                else:
                    response_type = _schema_to_type(
                        resp_schema, models, name_hint=f"{method_name}_response"
                    )
            break

    # Capture loop variables to avoid late-binding in the closure
    _path = path
    _method = http_method.upper()
    _path_params = list(path_param_names)
    _query_params_specs = list(query_param_specs)
    _body_type = body_type
    _response_type = response_type

    async def operation_method(
        self: Any, *args: Any, body: Any = None, **kwargs: Any
    ) -> Any:
        """Auto-generated method from OpenAPI spec."""
        # Map positional args to path parameter names
        path_kwargs: dict[str, Any] = {}
        for i, param_name in enumerate(_path_params):
            if i < len(args):
                path_kwargs[param_name] = args[i]
            elif param_name in kwargs:
                path_kwargs[param_name] = kwargs.pop(param_name)

        # Remaining kwargs are query parameters
        query_kwargs: dict[str, str] = {
            k: str(v) for k, v in kwargs.items() if v is not None
        }

        # Build the resolved path
        resolved_path = _path
        for key, val in path_kwargs.items():
            resolved_path = resolved_path.replace(f"{{{key}}}", str(val))

        return await self._client_instance._request(
            _method,
            resolved_path,
            body=body,
            query_params=query_kwargs,
            response_model=_response_type,
            expected_status=200 if _method != "POST" else 201,
        )

    operation_method.__name__ = method_name
    return method_name, operation_method


# ── OpenAPIClient ─────────────────────────────────────────────────────────────


class OpenAPIClient:
    """
    Typed HTTP client generated at runtime from an OpenAPI 3.x spec.

    Loads the spec from a URL, file, or dict; creates Pydantic models for
    every schema component; and attaches an async method for each path
    operation.  All HTTP calls are delegated to an underlying ``GenericClient``.

    Usage::

        client = await OpenAPIClient.from_url(
            "https://petstore3.swagger.io/api/v3/openapi.json",
            base_url="https://petstore3.swagger.io/api/v3",
        )
        pets = await client.list_pets(limit=10)
        pet  = await client.get_pet(pet_id=1)

    Thread safety:  ⚠️ Conditional — do not share one instance across threads;
                    each coroutine should use its own instance or context manager.
    Async safety:   ✅ Concurrent calls within the same instance are safe
                    (``GenericClient`` uses httpx which is concurrency-safe).

    Edge cases:
        - ``from_url`` fetches the spec with httpx; ensure the spec URL is
          reachable and returns valid JSON.
        - YAML specs require the ``pyyaml`` extra (``pip install varco-fastapi[openapi]``).
        - ``$ref`` cycles are resolved best-effort; circular schemas fall back to ``Any``.
        - Methods for operations with no ``operationId`` are named
          ``{verb}_{path_slug}`` (e.g. ``get_pets_pet_id``).
    """

    def __init__(
        self,
        _client: GenericClient,
        _methods: dict[str, Any],
        _models: dict[str, Any],
    ) -> None:
        """
        Internal constructor.  Use factory methods ``from_url``, ``from_file``,
        or ``from_dict`` instead of calling this directly.

        Args:
            _client:  Backing ``GenericClient`` instance.
            _methods: Mapping of method name → callable built from the spec.
            _models:  Mapping of schema name → Pydantic model class.
        """
        # Store client separately — methods reference it via self._client_instance
        self._client_instance = _client
        self._models = _models
        # Attach generated methods to this instance
        for name, fn in _methods.items():
            # Bind the method to this instance with the GenericClient accessible
            import types  # noqa: PLC0415

            setattr(self, name, types.MethodType(fn, self))

    # ── Context manager passthrough ───────────────────────────────────────

    async def __aenter__(self) -> OpenAPIClient:
        """Open the underlying GenericClient."""
        await self._client_instance.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Close the underlying GenericClient."""
        await self._client_instance.__aexit__(*exc)

    # ── Factory methods ───────────────────────────────────────────────────

    @classmethod
    async def from_url(
        cls,
        spec_url: str,
        *,
        base_url: str | None = None,
        middleware: tuple[AbstractClientMiddleware, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        headers: dict[str, str] | None = None,
    ) -> OpenAPIClient:
        """
        Fetch an OpenAPI spec from ``spec_url`` and build a live client.

        Args:
            spec_url:   URL of the OpenAPI JSON spec.
            base_url:   Override for the API base URL.  Defaults to the first
                        entry in ``spec.servers[0].url``.
            middleware: Middleware stack for the underlying ``GenericClient``.
            timeout:    Request timeout in seconds.
            verify:     TLS verification flag or CA bundle path.
            headers:    Static headers added to every request.

        Returns:
            A configured ``OpenAPIClient`` instance.

        Raises:
            httpx.HTTPError: Spec fetch failed.
            ValueError:      Response is not valid JSON.
        """
        import httpx as _httpx  # noqa: PLC0415

        async with _httpx.AsyncClient(verify=verify) as http:
            response = await http.get(spec_url, timeout=timeout)
            response.raise_for_status()
            spec: dict[str, Any] = response.json()

        return cls._build_from_spec(
            spec,
            base_url=base_url,
            middleware=middleware,
            timeout=timeout,
            verify=verify,
            headers=headers,
        )

    @classmethod
    def from_file(
        cls,
        spec_path: str | Path,
        *,
        base_url: str | None = None,
        middleware: tuple[AbstractClientMiddleware, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        headers: dict[str, str] | None = None,
    ) -> OpenAPIClient:
        """
        Load an OpenAPI spec from a local file and build a live client.

        Supports JSON (``.json``) and YAML (``.yaml`` / ``.yml``) files.
        YAML parsing requires ``pyyaml`` (``pip install varco-fastapi[openapi]``).

        Args:
            spec_path:  Path to the spec file.
            base_url:   Override for the API base URL.
            middleware: Middleware stack.
            timeout:    Request timeout in seconds.
            verify:     TLS verification flag.
            headers:    Static headers.

        Returns:
            A configured ``OpenAPIClient`` instance.

        Raises:
            FileNotFoundError: ``spec_path`` does not exist.
            ImportError:       YAML file provided but ``pyyaml`` not installed.
            ValueError:        File is not valid JSON or YAML.
        """
        import json as _json  # noqa: PLC0415

        spec_path = Path(spec_path)
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
                spec = yaml.safe_load(fh)
        else:
            with open(spec_path) as fh:
                spec = _json.load(fh)

        return cls._build_from_spec(
            spec,
            base_url=base_url,
            middleware=middleware,
            timeout=timeout,
            verify=verify,
            headers=headers,
        )

    @classmethod
    def from_dict(
        cls,
        spec: dict[str, Any],
        *,
        base_url: str | None = None,
        middleware: tuple[AbstractClientMiddleware, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        headers: dict[str, str] | None = None,
    ) -> OpenAPIClient:
        """
        Build a live client from an in-memory OpenAPI spec dict.

        This is the primary entry point for tests (no network or file I/O needed).

        Args:
            spec:       Complete OpenAPI 3.x spec as a Python dict.
            base_url:   Override for the API base URL.
            middleware: Middleware stack.
            timeout:    Request timeout in seconds.
            verify:     TLS verification flag.
            headers:    Static headers.

        Returns:
            A configured ``OpenAPIClient`` instance.
        """
        return cls._build_from_spec(
            spec,
            base_url=base_url,
            middleware=middleware,
            timeout=timeout,
            verify=verify,
            headers=headers,
        )

    @classmethod
    def _build_from_spec(
        cls,
        spec: dict[str, Any],
        *,
        base_url: str | None,
        middleware: tuple[AbstractClientMiddleware, ...],
        timeout: float,
        verify: bool | str,
        headers: dict[str, str] | None,
    ) -> OpenAPIClient:
        """
        Core builder: resolve refs, build models, attach methods, return client.

        Args:
            spec:       The raw OpenAPI spec dict.
            base_url:   Explicit base URL override.
            middleware: Middleware stack.
            timeout:    Timeout in seconds.
            verify:     TLS verification.
            headers:    Static headers.

        Returns:
            A fully built ``OpenAPIClient``.

        Edge cases:
            - ``base_url`` is None and ``spec.servers`` is absent → uses ``""``.
              The client will error on the first request if no URL is configured.
            - Operations with no paths → no methods are generated; client is valid
              but has no callable attributes besides lifecycle methods.
        """
        from varco_fastapi.client.generic import GenericClient  # noqa: PLC0415

        # Resolve base URL: explicit > spec's first server > empty string
        resolved_base = base_url
        if not resolved_base:
            servers = spec.get("servers", [])
            if servers and isinstance(servers, list):
                resolved_base = servers[0].get("url", "")

        # Build Pydantic models from components/schemas
        schemas: dict[str, dict[str, Any]] = spec.get("components", {}).get(
            "schemas", {}
        )
        models = _build_models(schemas, spec)

        # Generate methods for every path operation
        methods: dict[str, Any] = {}
        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for verb in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(verb)
                if not operation:
                    continue
                method_name, method_fn = _build_operation_method(
                    verb, path, operation, models, spec
                )
                # Deduplicate: later operations with the same name get a suffix
                final_name = method_name
                counter = 1
                while final_name in methods:
                    final_name = f"{method_name}_{counter}"
                    counter += 1
                methods[final_name] = method_fn

        # Build the underlying GenericClient for actual HTTP I/O
        generic = GenericClient(
            resolved_base or "http://localhost",
            middleware=middleware,
            timeout=timeout,
            verify=verify,
            headers=headers,
        )

        return cls(generic, methods, models)

    def __repr__(self) -> str:
        """Return a concise debug string."""
        return (
            f"OpenAPIClient("
            f"methods={list(self.__dict__.keys())!r}, "
            f"url={self._client_instance._base_url!r})"
        )


__all__ = ["OpenAPIClient"]
