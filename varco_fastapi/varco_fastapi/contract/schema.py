"""
varco_fastapi.contract.schema
================================
JSON Schema ``$defs`` collection for ``build_contract()`` (Plan 009, Phase 0).

Walks every ``request_model``/``response_model``/``ParamSpec.annotation`` a
router references, converts each to a JSON Schema fragment via
``pydantic.TypeAdapter``, and merges every model's ``$defs`` into one flat
``schemas`` registry keyed by model name — so a model referenced by three
routes is emitted once (RD-1's flat-registry-over-inline-schema decision).

DESIGN: flat registry + ``$ref`` over inline schemas
    ✅ One definition per model regardless of route count.
    ✅ OpenAPI-shaped (``$defs``-compatible) — ``datamodel-code-generator``
       remains a valid escape hatch for consumers who want it.
    ❌ Consumers must resolve ``$ref`` — a ~15-line resolver ships alongside.

Thread safety:  ✅ Pure functions; a fresh ``SchemaCollector`` per build.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

_REF_TEMPLATE = "#/schemas/{model}"


class SchemaCollector:
    """
    Accumulates JSON Schema fragments across a single ``build_contract()``
    call, merging every model's ``$defs`` into one flat registry.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}

    @property
    def schemas(self) -> dict[str, dict[str, Any]]:
        return self._schemas

    def schema_for(self, annotation: type | None) -> dict[str, Any] | None:
        """
        Return a JSON Schema fragment (possibly a ``$ref``) for ``annotation``.

        ``None`` in, ``None`` out — callers use this for optional
        request/response models and skip emission entirely.

        Edge cases:
            - A non-Pydantic annotation (``dict``, ``list[str]``, ``None``)
              gets a best-effort ``TypeAdapter`` schema; anything that raises
              degrades to ``{"type": "object"}`` with one DEBUG log.
        """
        if annotation is None:
            return None

        from pydantic import BaseModel, TypeAdapter

        try:
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                json_schema = annotation.model_json_schema(ref_template=_REF_TEMPLATE)
                defs = json_schema.pop("$defs", {})
                # The top-level model's own $defs entry (if TypeAdapter emitted
                # one) plus everything it references — merge all into the flat
                # registry, keyed by model name (one definition per model name,
                # regardless of how many routes reference it).
                for name, frag in defs.items():
                    self._schemas.setdefault(name, frag)
                self._schemas.setdefault(annotation.__name__, json_schema)
                return {"$ref": _REF_TEMPLATE.format(model=annotation.__name__)}

            adapter = TypeAdapter(annotation)
            json_schema = adapter.json_schema(ref_template=_REF_TEMPLATE)
            defs = json_schema.pop("$defs", {})
            for name, frag in defs.items():
                self._schemas.setdefault(name, frag)
            return json_schema
        except Exception as exc:  # noqa: BLE001 - best-effort schema generation
            _logger.debug("Could not derive a JSON Schema for %r: %s", annotation, exc)
            return {"type": "object"}
