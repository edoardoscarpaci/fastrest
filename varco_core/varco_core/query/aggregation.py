"""
varco_core.query.aggregation
=============================
Aggregation query AST for the varco query system.

Problem
-------
The existing query system handles filter / sort / pagination but not
aggregations.  Analytics endpoints need COUNT, SUM, AVG, MIN, MAX, and
GROUP BY — computations that reduce N entity rows into M group rows.

Design
------
``AggregationQuery`` is a **separate** dataclass from ``QueryParams`` — not
an extension of it.  The two have fundamentally different semantics:

    QueryParams       → returns N entity rows (one per entity)
    AggregationQuery  → returns M group rows (one per group, M ≤ N)

Merging them would force callers to inspect nullable ``group_by`` / ``agg``
fields on every query object, violating single responsibility.

The HAVING clause (filter applied after grouping) reuses the existing
``FilterNode`` type hierarchy from ``varco_core.query.type`` — this avoids
duplicating a node hierarchy just for post-group filtering.

Components
----------
``AggregationFunc``
    Enum of supported aggregate functions: COUNT, SUM, AVG, MIN, MAX.

``AggregationExpression``
    Frozen dataclass: ``(func, field, alias)``.
    ``field`` is ``None`` for COUNT(*).

``AggregationQuery``
    Top-level frozen dataclass: ``(group_by, aggregations, having, limit, offset)``.
    ``having`` is a ``FilterNode | None`` — reuses the existing filter AST.

For the SQLAlchemy applicator that turns an ``AggregationQuery`` into a
``SELECT ... GROUP BY ... HAVING ...`` statement, see
``varco_sa.query.aggregation.SQLAlchemyAggregationApplicator``.

Usage::

    from varco_core.query.aggregation import (
        AggregationExpression,
        AggregationFunc,
        AggregationQuery,
    )

    agg_query = AggregationQuery(
        group_by=("status",),
        aggregations=(
            AggregationExpression(AggregationFunc.COUNT, field=None, alias="count"),
            AggregationExpression(AggregationFunc.SUM, field="amount", alias="total"),
        ),
    )

Thread safety:  ✅ AST nodes are frozen dataclasses — immutable value objects.
Async safety:   ✅ All applicator methods are synchronous.

📚 Docs
- 🐍 https://docs.python.org/3/library/enum.html
  Python Enum — used for AggregationFunc.
- 🔍 https://docs.sqlalchemy.org/en/20/core/functions.html
  SQLAlchemy func — aggregate function helpers (func.count, func.sum, etc.).
- 🔍 https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.group_by
  SQLAlchemy Select.group_by — GROUP BY clause builder.
- 🔍 https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.having
  SQLAlchemy Select.having — HAVING clause builder.
- 📐 https://en.wikipedia.org/wiki/SQL#Queries
  SQL aggregation semantics — GROUP BY / HAVING reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dfield
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # FilterNode is the Union type from query.type — only needed for type hints.
    from varco_core.query.type import AndNode, ComparisonNode, NotNode, OrNode

    FilterNode = ComparisonNode | AndNode | OrNode | NotNode


# ── Security: safe field-name regex ───────────────────────────────────────────
#
# DESIGN: strict allowlist over blocklist — same pattern as the filter visitor.
#   ✅ Prevents __dunder__ access and SQL special characters in GROUP BY fields.
#   ✅ Simple to audit — one regex, one place.
#   ❌ Rejects exotic (but valid) Python identifiers with unicode — acceptable
#      for DB column names which are always ASCII.
_SAFE_FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_field(field: str, context: str) -> None:
    """
    Reject field names that could be used for attribute-injection attacks.

    Args:
        field:   The field name to validate.
        context: Human-readable context for the error message (e.g. "GROUP BY").

    Raises:
        ValueError: If ``field`` does not match the safe identifier pattern.

    Edge cases:
        - Empty string → fails regex → raises ValueError.
        - Dunder names (``__class__``) → caught here before any getattr.
    """
    if not _SAFE_FIELD_RE.match(field):
        raise ValueError(
            f"{context} field {field!r} contains unsafe characters. "
            "Only [a-zA-Z_][a-zA-Z0-9_]* identifiers are accepted."
        )


# ── AggregationFunc ────────────────────────────────────────────────────────────


class AggregationFunc(StrEnum):
    """
    Aggregate functions supported by the aggregation query AST.

    Attributes:
        COUNT: Count of rows — used with ``field=None`` for COUNT(*) or
               a specific field for COUNT(field) (skips NULLs).
        SUM:   Sum of numeric field values.
        AVG:   Average of numeric field values.
        MIN:   Minimum value in the field.
        MAX:   Maximum value in the field.

    Note: ``DISTINCT`` variants (e.g. ``COUNT DISTINCT``) are not modelled
    here — add them as a ``distinct: bool`` flag on ``AggregationExpression``
    if needed in a future iteration.
    """

    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


# ── AggregationExpression ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AggregationExpression:
    """
    A single aggregate column expression: ``func(field) AS alias``.

    Immutable — safe to hash, cache, and reuse across queries.

    DESIGN: frozen dataclass over named tuple
        ✅ Field names are explicit and doc-able.
        ✅ ``__post_init__`` allows validation at construction time.
        ✅ Hashable — can be used in sets / dict keys.
        ❌ Slightly more verbose than a tuple literal for inline use.

    Thread safety:  ✅ Frozen — immutable after construction.
    Async safety:   ✅ Value object; no I/O.

    Attributes:
        func:  The aggregate function to apply.
        field: The column name to aggregate.  Use ``None`` for ``COUNT(*)``.
        alias: The output column alias (e.g. ``"total_revenue"``).  Must be a
               non-empty valid identifier.

    Args:
        func:  ``AggregationFunc`` enum value.
        field: Column name, or ``None`` for COUNT(*).
        alias: Output column alias — used as the result dict key.

    Raises:
        ValueError: If ``alias`` is empty or contains unsafe characters.
        ValueError: If ``field`` is provided and contains unsafe characters.
        ValueError: If ``func`` is not COUNT and ``field`` is None
                    (SUM/AVG/MIN/MAX all require a specific field).

    Edge cases:
        - ``func=COUNT, field=None``  → COUNT(*) — valid.
        - ``func=SUM, field=None``    → raises ValueError.
        - ``alias`` is used verbatim as a Python dict key in result rows —
          keep it unique within an ``AggregationQuery``.

    Example::

        AggregationExpression(AggregationFunc.COUNT, field=None, alias="row_count")
        AggregationExpression(AggregationFunc.SUM, field="amount", alias="total")
    """

    func: AggregationFunc
    field: str | None
    alias: str

    def __post_init__(self) -> None:
        if not self.alias:
            raise ValueError("AggregationExpression.alias must be a non-empty string.")
        _validate_field(self.alias, "alias")

        if self.field is not None:
            _validate_field(self.field, "aggregate field")

        # COUNT is the only function that can operate on all rows (*).
        # SUM / AVG / MIN / MAX all require a specific column.
        if self.func != AggregationFunc.COUNT and self.field is None:
            raise ValueError(
                f"AggregationExpression: func={self.func!r} requires a non-None field "
                f"(only COUNT supports field=None for COUNT(*))."
            )


# ── AggregationQuery ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AggregationQuery:
    """
    Top-level aggregation query descriptor.

    Describes a ``GROUP BY ... HAVING ... LIMIT ... OFFSET`` query with one
    or more aggregate expressions.

    DESIGN: separate from QueryParams
        The existing ``QueryParams`` is designed for entity-row queries (one
        row per entity).  Aggregation returns one row per group — a fundamentally
        different cardinality.  A single merged class would require callers to
        check nullable ``group_by``/``aggregations`` fields on every use.

        ✅ Separate dataclass keeps concerns separate.
        ✅ ``having`` reuses the existing ``FilterNode`` type hierarchy — no
           duplicate AST for post-group filtering.
        ❌ Callers must instantiate a different class for aggregation queries.

    Thread safety:  ✅ Frozen — immutable.
    Async safety:   ✅ Value object.

    Attributes:
        group_by:      Tuple of column names to group by.  Empty tuple → no GROUP BY
                       (computes a single aggregate over all rows).
        aggregations:  Tuple of ``AggregationExpression`` values.  Must be non-empty.
        having:        Optional post-group filter using the existing ``FilterNode``
                       AST.  Translated to a SQLAlchemy HAVING clause.
        limit:         Maximum number of result groups to return.  ``None`` → no limit.
        offset:        Number of result groups to skip.  Default: 0.

    Args:
        group_by:     Column names to group by.  Default: empty tuple (no group).
        aggregations: Aggregate expressions.  Must have at least one.
        having:       Post-group filter node.  Default: ``None``.
        limit:        Result cap.  Default: ``None`` (unlimited).
        offset:       Result skip count.  Default: 0.

    Raises:
        ValueError: If ``aggregations`` is empty.

    Edge cases:
        - ``group_by=()`` → single-row aggregate (e.g. ``SELECT COUNT(*) FROM t``).
        - ``having`` is applied AFTER GROUP BY — it cannot reference raw (non-grouped,
          non-aggregated) columns.  The applicator does not validate this — SQL
          will raise at execution time if the expression is invalid.
        - ``limit=0`` is passed through to the backend unchanged — SQL behaviour
          (returns 0 rows) is the expected result.

    Example::

        AggregationQuery(
            group_by=("status",),
            aggregations=(
                AggregationExpression(AggregationFunc.COUNT, None, "count"),
                AggregationExpression(AggregationFunc.SUM, "amount", "total"),
            ),
            having=ComparisonNode("count", Operation.GREATER_THAN, 0),
            limit=100,
        )
    """

    group_by: tuple[str, ...] = dfield(default_factory=tuple)
    aggregations: tuple[AggregationExpression, ...] = dfield(default_factory=tuple)
    having: Any | None = None  # FilterNode | None — Any avoids circular import
    limit: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        if not self.aggregations:
            raise ValueError(
                "AggregationQuery.aggregations must contain at least one expression. "
                "Use AggregationExpression to define what to compute."
            )
        for col in self.group_by:
            _validate_field(col, "GROUP BY")


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "AggregationFunc",
    "AggregationExpression",
    "AggregationQuery",
]
