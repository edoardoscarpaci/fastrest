"""
varco_sa.query.aggregation
============================
SQLAlchemy implementation of the aggregation query applicator.

Applies an ``AggregationQuery`` (defined in ``varco_core.query.aggregation``)
to a SQLAlchemy 2.x ``Select`` statement, producing GROUP BY / HAVING / LIMIT
queries.

Thread safety:  ✅ Stateless after construction.
Async safety:   ✅ Synchronous — returns a modified ``Select`` object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, Select, func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase
from varco_core.exception.repository import FieldNotFound
from varco_core.query.aggregation import (
    AggregationExpression,
    AggregationFunc,
    AggregationQuery,
)

from varco_sa.query.compiler import SQLAlchemyQueryCompiler

if TYPE_CHECKING:
    from varco_core.query.type import AndNode, ComparisonNode, NotNode, OrNode

    FilterNode = ComparisonNode | AndNode | OrNode | NotNode


class SQLAlchemyAggregationApplicator:
    """
    Applies an ``AggregationQuery`` to a SQLAlchemy 2.x ``Select`` statement.

    Translates the query's ``group_by``, ``aggregations``, ``having``,
    ``limit``, and ``offset`` into the corresponding SQLAlchemy clauses.

    DESIGN: applicator class over standalone functions
        ✅ Consistent with ``SQLAlchemyQueryApplicator`` — same usage pattern.
        ✅ Stateless after construction — safe to share across requests.
        ✅ ``model_cls`` is injected at construction — column resolution is
           done once, not per-apply call.
        ❌ Extra allocation vs. standalone function — negligible for I/O-bound DB calls.

    Thread safety:  ✅ Stateless after construction — no mutable state.
    Async safety:   ✅ Synchronous — returns a modified ``Select`` object.

    Args:
        model_cls: SQLAlchemy declarative model class for column resolution.

    Edge cases:
        - Unknown ``group_by`` field name → ``FieldNotFound``.
        - Unknown aggregate ``field`` name → ``FieldNotFound``.
        - ``having`` is translated using ``SQLAlchemyQueryCompiler`` — all
          HAVING filter constraints apply (allowed_fields, safe paths, etc.).
        - The returned ``Select`` is a new object — the input ``stmt`` is not
          mutated.

    Example::

        applicator = SQLAlchemyAggregationApplicator(model_cls=OrderModel)
        stmt = select(OrderModel)
        stmt = applicator.apply(stmt, agg_query)
        rows = await session.execute(stmt)
        result = [dict(row) for row in rows.mappings()]
    """

    def __init__(self, model_cls: type[DeclarativeBase]) -> None:
        """
        Args:
            model_cls: Mapped SQLAlchemy ORM model class (not an instance).
        """
        self._model_cls = model_cls

    def apply(self, stmt: Select, agg_query: AggregationQuery) -> Select:
        """
        Apply the aggregation query to a ``Select`` statement.

        The pipeline is:
        1. Replace the SELECT columns with group-by columns + aggregate exprs.
        2. Add GROUP BY clause.
        3. Add HAVING clause (if provided).
        4. Add LIMIT / OFFSET.

        Args:
            stmt:      Base ``Select`` statement (typically ``select(ModelClass)``).
            agg_query: The aggregation query descriptor.

        Returns:
            A new ``Select`` statement with GROUP BY, aggregates, HAVING,
            LIMIT, and OFFSET applied.

        Raises:
            FieldNotFound: If a ``group_by`` column or aggregate ``field`` does not
                           exist on ``model_cls``.

        Thread safety:  ✅ Stateless — no mutations; ``Select`` is immutable.
        Async safety:   ✅ Synchronous.

        Edge cases:
            - ``agg_query.group_by = ()`` → no GROUP BY clause — produces a
              single-row global aggregate.
            - ``agg_query.having = None`` → no HAVING clause added.
            - ``stmt`` may already have a WHERE clause — it is preserved.
        """
        # Build the list of SELECT-level column expressions.
        # Order: group-by columns first, then aggregate expressions.
        select_cols: list[ColumnElement[Any]] = []

        # ── Group-by columns ──────────────────────────────────────────────────
        group_cols: list[ColumnElement[Any]] = []
        for col_name in agg_query.group_by:
            col = self._resolve_column(col_name)
            select_cols.append(col)
            group_cols.append(col)

        # ── Aggregate expressions ─────────────────────────────────────────────
        for agg_expr in agg_query.aggregations:
            agg_col = self._build_agg_column(agg_expr)
            select_cols.append(agg_col)

        # Replace the original SELECT columns with our computed set.
        # ``with_only_columns`` returns a new Select — the original is unchanged.
        stmt = stmt.with_only_columns(*select_cols)

        # ── GROUP BY ──────────────────────────────────────────────────────────
        if group_cols:
            stmt = stmt.group_by(*group_cols)

        # ── HAVING ───────────────────────────────────────────────────────────
        if agg_query.having is not None:
            having_expr = self._compile_filter(agg_query.having)
            stmt = stmt.having(having_expr)

        # ── LIMIT / OFFSET ────────────────────────────────────────────────────
        if agg_query.limit is not None:
            stmt = stmt.limit(agg_query.limit)
        if agg_query.offset:
            stmt = stmt.offset(agg_query.offset)

        return stmt

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _resolve_column(self, field: str) -> ColumnElement[Any]:
        """
        Resolve a field name to a SQLAlchemy ``MappedColumn`` on ``model_cls``.

        Args:
            field: Column name to resolve.

        Returns:
            The SQLAlchemy column element for the field.

        Raises:
            FieldNotFound: If the column does not exist on ``model_cls``.

        Edge cases:
            - Field was already validated against ``_SAFE_FIELD_RE`` in
              ``AggregationQuery.__post_init__`` and
              ``AggregationExpression.__post_init__``.  No second validation here.
        """
        col = getattr(self._model_cls, field, None)
        if col is None:
            raise FieldNotFound(
                field,
                self._model_cls.__tablename__,
            )
        return col

    def _build_agg_column(self, expr: AggregationExpression) -> ColumnElement[Any]:
        """
        Build a labelled SQLAlchemy aggregate expression for one ``AggregationExpression``.

        Args:
            expr: The aggregate expression descriptor.

        Returns:
            A labelled SQLAlchemy ``ColumnElement``.

        Raises:
            FieldNotFound: If ``expr.field`` is not None and the column is not found.
        """
        # Resolve the target column, or None for COUNT(*).
        if expr.field is not None:
            target = self._resolve_column(expr.field)
        else:
            # COUNT(*) — use SQLAlchemy's func.count() with no argument.
            target = None

        # Build the aggregate expression based on the func enum.
        # DESIGN: explicit if/elif over a dispatch dict — it is exhaustive,
        # readable, and the enum is small enough that a dict adds no value.
        match expr.func:
            case AggregationFunc.COUNT:
                # func.count(col) → COUNT(col); func.count() → COUNT(*)
                agg = func.count(target) if target is not None else func.count()
            case AggregationFunc.SUM:
                agg = func.sum(target)  # type: ignore[assignment]
            case AggregationFunc.AVG:
                agg = func.avg(target)  # type: ignore[assignment]
            case AggregationFunc.MIN:
                agg = func.min(target)  # type: ignore[assignment]
            case AggregationFunc.MAX:
                agg = func.max(target)  # type: ignore[assignment]
            case _:
                # Defensive branch — AggregationFunc is exhaustive, but future
                # enum additions before updating this match will hit here.
                raise ValueError(
                    f"Unsupported AggregationFunc: {expr.func!r}. "
                    "Update SQLAlchemyAggregationApplicator._build_agg_column."
                )

        # Label the expression so result rows can be accessed by alias name.
        return agg.label(expr.alias)

    def _compile_filter(self, node: Any) -> ColumnElement[Any]:
        """
        Compile a ``FilterNode`` into a SQLAlchemy ``ColumnElement`` for HAVING.

        Delegates to ``SQLAlchemyQueryCompiler`` — reuses the existing filter
        visitor to avoid duplicating comparison logic.

        Args:
            node: A ``FilterNode`` (ComparisonNode, AndNode, OrNode, NotNode).

        Returns:
            A SQLAlchemy ``ColumnElement`` for the HAVING clause.

        Edge cases:
            - The HAVING clause can only reference grouped columns or aggregate
              expressions.  The compiler does not enforce this — the DB raises at
              execution time.
            - Dotted relationship paths in HAVING are not supported — the HAVING
              clause operates on the GROUP BY output, not on joined tables.
        """
        compiler = SQLAlchemyQueryCompiler(self._model_cls)
        return compiler.visit(node)

    def _column_names(self) -> list[str]:
        """
        Return a list of column names on ``model_cls`` for use in error messages.

        Returns:
            Sorted list of column attribute names (excludes relationships).
        """
        try:
            mapper = sa_inspect(self._model_cls)
            return sorted(c.key for c in mapper.columns)
        except Exception:
            # Inspection may fail for unmapped classes in tests — return empty.
            return []


__all__ = ["SQLAlchemyAggregationApplicator"]
