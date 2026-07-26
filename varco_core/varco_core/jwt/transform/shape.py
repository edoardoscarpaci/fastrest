"""
varco_core.jwt.transform.shape
===================================

``ValueShape`` — the small set of claim value shapes seen across real-world
JWT issuers — and ``normalize()``, the pure function that converts a raw
claim value into the shape a ``ClaimRule`` target expects.

DESIGN: explicit ``ValueShape`` enum with an ``AUTO`` default, not
inference-only
    ✅ ``AUTO`` covers the common cases (list, OAuth2 space-delimited scope,
       CSV, single scalar, dict-of-flags) with zero configuration.
    ❌ ``"a,b"`` is genuinely ambiguous — one role legitimately containing a
       comma vs. two roles.  ``AUTO`` guesses "two roles"; an explicit
       ``ValueShape.SCALAR`` override is required for the comma-containing
       case.  Rejected making shape 100% inferred (D-A in plan §A) because
       correctness in that one case is worth one env var.

Thread safety:  ✅ Pure functions — no shared state.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from varco_core.jwt.exceptions import ClaimTransformError

_logger = logging.getLogger(__name__)


# ── ValueShape ─────────────────────────────────────────────────────────────────


class ValueShape(StrEnum):
    """
    The claim-value shape a ``ClaimRule`` should normalize its source value
    into.

    Members:
        AUTO:       Infer from the runtime type — see the module docstring
                    and the AUTO table in the plan for every case.
        LIST:       Alias of AUTO's list-passthrough behaviour, spelled out
                    explicitly for readability in config.
        SPACE:      Split a string on whitespace (OAuth2 ``scope`` claim).
        CSV:        Split a string on commas.
        SCALAR:     Keep the raw value as a single-item list — the escape
                    hatch for a value that legitimately contains a comma or
                    space and must NOT be split.
        DICT_KEYS:  For claims shaped as ``{"perm": true, ...}`` — returns
                    the sorted key list.
        GRANTS:     Validates ``list[{"resource": str, "actions": list[str]}]``
                    — used only for the ``grants`` canonical target.
        RAW:        Passes the value through completely unmodified — for
                    escape-hatch / metadata_fields targets that want the
                    verbatim claim value (e.g. a nested dict).
    """

    AUTO = "auto"
    LIST = "list"
    SPACE = "space"
    CSV = "csv"
    SCALAR = "scalar"
    DICT_KEYS = "dict_keys"
    GRANTS = "grants"
    RAW = "raw"


# ── normalize() ────────────────────────────────────────────────────────────────


def normalize(
    value: Any,
    shape: ValueShape,
    *,
    strip_prefix: str | None = None,
    strict: bool = False,
    target: str = "value",
) -> Any:
    """
    Normalize a raw claim value into the shape required by ``shape``.

    Args:
        value:        Raw claim value as decoded from JSON (``str``,
                      ``list``, ``dict``, ``int``, ``bool``, ``None``, …).
        shape:        Target ``ValueShape``.
        strip_prefix: When set, stripped from the front of every resulting
                      string element (applied AFTER shaping) — e.g.
                      Keycloak/Spring's ``"ROLE_"`` prefix convention.
                      Ignored for ``RAW``/``GRANTS``/``DICT_KEYS`` results
                      that are not a flat list of strings.
        strict:       When ``True``, an unsupported input type (e.g. a bare
                      ``int``/``bool`` under ``AUTO``/``SPACE``/``CSV``)
                      raises ``ClaimTransformError`` instead of being
                      coerced with a warning.
        target:       Canonical claim name this value is destined for — used
                      only to make the warning/error message actionable
                      (e.g. ``"roles"``).

    Returns:
        - ``AUTO``/``LIST``/``SPACE``/``CSV``/``SCALAR``/``DICT_KEYS`` → ``list[str]`` (``[]`` for ``None``).
        - ``GRANTS`` → the (validated) ``list[dict]`` unchanged.
        - ``RAW`` → ``value`` unchanged.

    Raises:
        ClaimTransformError: ``strict=True`` and the input type is not
            supported for the requested shape, or ``shape=GRANTS`` and an
            entry is missing ``resource``/``actions``.

    Edge cases:
        - ``None`` → ``[]`` for every list-producing shape (never an error,
          even with ``strict=True`` — "claim absent" is not a shape error).
        - Non-string, non-list scalars (``int``, ``bool``) are coerced via
          ``str(value)`` when ``strict=False`` — logged once per call at
          WARNING level naming ``target``.

    Example::

        normalize("read write", ValueShape.AUTO)  # -> ["read", "write"]
        normalize(["ROLE_admin"], ValueShape.AUTO, strip_prefix="ROLE_")
        # -> ["admin"]
    """
    if shape is ValueShape.RAW:
        return value

    if shape is ValueShape.GRANTS:
        return _normalize_grants(value, strict=strict, target=target)

    if shape is ValueShape.DICT_KEYS:
        result = _dict_keys(value, strict=strict, target=target)
        return _apply_strip(result, strip_prefix)

    if shape is ValueShape.SCALAR:
        # SCALAR never splits — the whole value becomes the single element.
        result = [] if value is None else [value]
        return _apply_strip(result, strip_prefix)

    if shape is ValueShape.SPACE:
        result = _split_string(value, " ", strict=strict, target=target)
        return _apply_strip(result, strip_prefix)

    if shape is ValueShape.CSV:
        result = _split_string(value, ",", strict=strict, target=target)
        return _apply_strip(result, strip_prefix)

    if shape is ValueShape.LIST:
        result = _as_list_passthrough(value, strict=strict, target=target)
        return _apply_strip(result, strip_prefix)

    # AUTO — infer from the runtime type. See the plan's AUTO table.
    result = _auto(value, strict=strict, target=target)
    return _apply_strip(result, strip_prefix)


# ── Internal helpers ────────────────────────────────────────────────────────────


def _apply_strip(items: Any, prefix: str | None) -> Any:
    """Strip ``prefix`` from every string element of a list result."""
    if not prefix or not isinstance(items, list):
        return items
    return [
        (
            item[len(prefix) :]
            if isinstance(item, str) and item.startswith(prefix)
            else item
        )
        for item in items
    ]


def _coerce_scalar(value: Any, *, strict: bool, target: str) -> list[str]:
    """
    Coerce an unsupported scalar type (int/bool/float/…) to ``[str(value)]``.

    Raises:
        ClaimTransformError: ``strict=True``.
    """
    if strict:
        raise ClaimTransformError(
            f"Claim {target!r} has unexpected value type "
            f"{type(value).__name__} ({value!r}); expected a string, list, "
            f"or dict. Fix the source claim, set an explicit SHAPE, or set "
            f"strict=False to coerce via str()."
        )
    _logger.warning(
        "Claim %r had unexpected value type %s (value=%r) — coercing to "
        "%r via str(). Set VARCO_JWT_TRANSFORM_STRICT=true to fail hard "
        "instead of coercing.",
        target,
        type(value).__name__,
        value,
        [str(value)],
    )
    return [str(value)]


def _auto(value: Any, *, strict: bool, target: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        return sorted(value.keys())
    if isinstance(value, str):
        if " " in value:
            return value.split()
        if "," in value:
            return value.split(",")
        return [value]
    return _coerce_scalar(value, strict=strict, target=target)


def _as_list_passthrough(value: Any, *, strict: bool, target: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return _coerce_scalar(value, strict=strict, target=target)


def _split_string(value: Any, sep: str, *, strict: bool, target: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split() if sep == " " else value.split(sep)
    if isinstance(value, list):
        # Already a list — nothing to split.
        return value
    return _coerce_scalar(value, strict=strict, target=target)


def _dict_keys(value: Any, *, strict: bool, target: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return sorted(value.keys())
    if strict:
        raise ClaimTransformError(
            f"Claim {target!r} with ValueShape.DICT_KEYS requires a dict, "
            f"got {type(value).__name__}: {value!r}."
        )
    _logger.warning(
        "Claim %r with ValueShape.DICT_KEYS expected a dict, got %s "
        "(value=%r) — treating as empty.",
        target,
        type(value).__name__,
        value,
    )
    return []


def _normalize_grants(value: Any, *, strict: bool, target: str) -> list[dict[str, Any]]:
    """
    Validate a ``grants`` claim shaped as
    ``list[{"resource": str, "actions": list[str]}]``.

    Replaces the previous bare ``KeyError`` (parser.py) with an actionable
    ``ClaimTransformError`` naming the offending list index.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ClaimTransformError(
            f"Claim {target!r} (grants) must be a list, got "
            f"{type(value).__name__}: {value!r}."
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ClaimTransformError(
                f"grants[{index}] must be an object with 'resource' and "
                f"'actions' keys, got {item!r}."
            )
        if "resource" not in item:
            raise ClaimTransformError(
                f"grants[{index}] is missing the required key 'resource': "
                f"{dict(item)!r}."
            )
        if "actions" not in item:
            raise ClaimTransformError(
                f"grants[{index}] is missing the required key 'actions': "
                f"{dict(item)!r}."
            )
    return value


__all__ = [
    "ValueShape",
    "normalize",
    "ClaimTransformError",
]
