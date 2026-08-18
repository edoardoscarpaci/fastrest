"""
varco_fastapi.contract.model
==============================
Frozen value objects for ``ServiceContract`` — the portable, JSON-serializable
descriptor of a ``VarcoRouter``'s routes (Plan 009, Phase 0 / C3 part 1).

``ServiceContract`` is the single artifact both client-generation paths build
from: the in-process path reads it off the imported router class
(``_VarcoClientMeta``), the cross-repo path loads it from an exported
``.contract.json`` file with no router import at all. See
``varco_fastapi/router/introspection.py`` (``introspect_routes`` /
``ResolvedRoute``) for the producer side and ``contract/build.py`` for the
translation from ``ResolvedRoute`` to ``RouteContract``.

DESIGN: dataclasses over Pydantic models for the wire format
    ✅ Frozen + hashable — safe to cache and to use as a dict key.
    ✅ No pydantic version-compat surface for a format meant to outlive any
       one pydantic major version on either side of the wire.
    ❌ Hand-rolled to_dict/from_dict — a small, fully-tested surface (below).

Thread safety:  ✅ Frozen dataclasses — safe to share across threads/tasks.
Async safety:   ✅ Pure value objects; no I/O.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Final

_logger = logging.getLogger(__name__)

# Bumped on a breaking wire-format change. Consumers reject a major version
# they do not understand (ContractVersionError) and warn-but-parse on an
# unknown minor (RD-1).
CONTRACT_VERSION: Final[str] = "1.0"


class ContractVersionError(ValueError):
    """Raised when a contract's major version is not understood by this build."""


def _major(version: str) -> str:
    """Return the ``X`` in an ``"X.Y"`` version string."""
    return version.split(".", 1)[0]


@dataclass(frozen=True)
class ParamContract:
    """
    One resolved handler parameter, serialized for client generation.

    Attributes:
        name:        Parameter name as declared on the handler.
        kind:        ``"path"`` | ``"query"`` | ``"body"`` | ``"header"``.
        schema:      JSON Schema fragment for this parameter (may be a ``$ref``
                     into ``ServiceContract.schemas``).
        required:    Whether the parameter must be supplied.
        default:     Default value when ``required=False``. Must itself be
                     JSON-serializable (enforced by the build step).
        description: Human-readable description, if any.
    """

    name: str
    kind: str
    schema: dict[str, Any]
    required: bool = True
    default: Any = None
    description: str | None = None


@dataclass(frozen=True)
class RouteContract:
    """
    One route's portable, wire-format description.

    See ``varco_fastapi/router/introspection.py::ResolvedRoute`` for the
    richer in-process representation this is built from.
    """

    name: str
    method: str
    path: str
    params: tuple[ParamContract, ...] = ()
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    status_code: int = 200
    is_crud: bool = False
    crud_action: str | None = None
    async_capable: bool = True
    deprecated: bool = False
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServiceContract:
    """
    Portable descriptor of a ``VarcoRouter``'s full route surface.

    Round-trips through JSON via ``to_json``/``from_json`` — this is the
    exact artifact ``varco export-contract`` writes and ``varco gen-client`` /
    ``contract_client`` read back, with no router import required on the
    reading side.

    Edge cases:
        - A router with zero routes produces a valid contract (``routes=()``),
          not an error.
        - ``schemas`` is a flat ``$defs``-shaped registry — a model referenced
          by multiple routes is emitted once and referenced by ``$ref``.
    """

    contract_version: str
    service_name: str
    routes: tuple[RouteContract, ...]
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    base_path: str = ""
    service_version: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain, JSON-ready ``dict``."""
        return {
            "contract_version": self.contract_version,
            "service_name": self.service_name,
            "routes": [_route_to_dict(r) for r in self.routes],
            "schemas": self.schemas,
            "base_path": self.base_path,
            "service_version": self.service_version,
            "description": self.description,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize to a JSON string — the exact ``.contract.json`` format."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServiceContract:
        """
        Reconstruct a ``ServiceContract`` from ``to_dict()`` output.

        Raises:
            ContractVersionError: the contract's major version differs from
                this build's ``CONTRACT_VERSION`` major.

        Edge cases:
            - An unknown *minor* version logs one WARNING and still parses
              (RD-1 — forward-compatible within a major version).
        """
        version = str(data["contract_version"])
        if _major(version) != _major(CONTRACT_VERSION):
            raise ContractVersionError(
                f"Contract major version {version!r} is not compatible with "
                f"this build's {CONTRACT_VERSION!r} (major mismatch)."
            )
        if version != CONTRACT_VERSION:
            _logger.warning(
                "ServiceContract minor version %r differs from this build's %r "
                "— parsing anyway (forward-compatible within a major version).",
                version,
                CONTRACT_VERSION,
            )
        routes = tuple(_route_from_dict(r) for r in data.get("routes", []))
        return cls(
            contract_version=version,
            service_name=data["service_name"],
            routes=routes,
            schemas=dict(data.get("schemas") or {}),
            base_path=data.get("base_path", ""),
            service_version=data.get("service_version"),
            description=data.get("description"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> ServiceContract:
        """Reconstruct a ``ServiceContract`` from ``to_json()`` output."""
        return cls.from_dict(json.loads(raw))

    def route(self, name: str) -> RouteContract:
        """
        Return the ``RouteContract`` named ``name``.

        Raises:
            KeyError: no route with that name — message lists all known
                route names to make the typo obvious.
        """
        for r in self.routes:
            if r.name == name:
                return r
        known = ", ".join(sorted(r.name for r in self.routes))
        raise KeyError(
            f"No route named {name!r} in contract {self.service_name!r}. Known routes: [{known}]"
        )


def _param_to_dict(p: ParamContract) -> dict[str, Any]:
    return {f.name: getattr(p, f.name) for f in fields(p)}


def _param_from_dict(data: Mapping[str, Any]) -> ParamContract:
    return ParamContract(**dict(data))


def _route_to_dict(r: RouteContract) -> dict[str, Any]:
    d = {
        f.name: getattr(r, f.name)
        for f in fields(r)
        if f.name != "params" and f.name != "tags"
    }
    d["params"] = [_param_to_dict(p) for p in r.params]
    d["tags"] = list(r.tags)
    return d


def _route_from_dict(data: Mapping[str, Any]) -> RouteContract:
    d = dict(data)
    d["params"] = tuple(_param_from_dict(p) for p in d.get("params", []))
    d["tags"] = tuple(d.get("tags", ()))
    return RouteContract(**d)
