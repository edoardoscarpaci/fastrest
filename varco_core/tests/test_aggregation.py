"""
tests.test_aggregation
======================
Unit tests for varco_core.query.aggregation pure AST types.

Covers:
    AggregationFunc         — enum values
    AggregationExpression   — construction, validation, immutability
    AggregationQuery        — construction, validation, immutability

For SQLAlchemyAggregationApplicator tests see varco_sa/tests/test_sa_aggregation.py.
"""

from __future__ import annotations

import pytest
from varco_core.query.aggregation import (
    AggregationExpression,
    AggregationFunc,
    AggregationQuery,
)

# ── AggregationFunc ────────────────────────────────────────────────────────────


def test_aggregation_func_values() -> None:
    """All expected AggregationFunc members must exist."""
    assert AggregationFunc.COUNT == "COUNT"
    assert AggregationFunc.SUM == "SUM"
    assert AggregationFunc.AVG == "AVG"
    assert AggregationFunc.MIN == "MIN"
    assert AggregationFunc.MAX == "MAX"


# ── AggregationExpression ──────────────────────────────────────────────────────


def test_aggregation_expression_count_star() -> None:
    """COUNT(*) is valid: func=COUNT, field=None."""
    expr = AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt")
    assert expr.func == AggregationFunc.COUNT
    assert expr.field is None
    assert expr.alias == "cnt"


def test_aggregation_expression_sum_with_field() -> None:
    """SUM with a specific field is valid."""
    expr = AggregationExpression(AggregationFunc.SUM, field="amount", alias="total")
    assert expr.field == "amount"
    assert expr.alias == "total"


def test_aggregation_expression_sum_no_field_raises() -> None:
    """SUM without a field raises ValueError — only COUNT supports field=None."""
    with pytest.raises(ValueError, match="requires a non-None field"):
        AggregationExpression(AggregationFunc.SUM, field=None, alias="bad")


def test_aggregation_expression_empty_alias_raises() -> None:
    """Empty alias raises ValueError."""
    with pytest.raises(ValueError, match="alias"):
        AggregationExpression(AggregationFunc.COUNT, field=None, alias="")


def test_aggregation_expression_unsafe_alias_raises() -> None:
    """Alias with unsafe characters raises ValueError."""
    with pytest.raises(ValueError, match="unsafe characters"):
        AggregationExpression(AggregationFunc.COUNT, field=None, alias="bad-alias")


def test_aggregation_expression_unsafe_field_raises() -> None:
    """Field with unsafe characters raises ValueError."""
    with pytest.raises(ValueError, match="unsafe characters"):
        AggregationExpression(AggregationFunc.SUM, field="bad field", alias="ok")


def test_aggregation_expression_is_frozen() -> None:
    """AggregationExpression is immutable — assignment raises FrozenInstanceError."""
    expr = AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt")
    with pytest.raises(Exception):  # FrozenInstanceError
        expr.alias = "other"  # type: ignore[misc]


def test_aggregation_expression_hashable() -> None:
    """AggregationExpression is hashable — can be used in sets."""
    e1 = AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt")
    e2 = AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt")
    assert e1 == e2
    assert hash(e1) == hash(e2)


# ── AggregationQuery ──────────────────────────────────────────────────────────


def test_aggregation_query_basic_construction() -> None:
    """AggregationQuery can be constructed with minimal arguments."""
    q = AggregationQuery(
        aggregations=(
            AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt"),
        ),
    )
    assert q.group_by == ()
    assert q.having is None
    assert q.limit is None
    assert q.offset == 0


def test_aggregation_query_empty_aggregations_raises() -> None:
    """AggregationQuery with no aggregations raises ValueError."""
    with pytest.raises(ValueError, match="at least one"):
        AggregationQuery(aggregations=())


def test_aggregation_query_unsafe_group_by_field_raises() -> None:
    """Unsafe characters in group_by field name raises ValueError."""
    with pytest.raises(ValueError, match="unsafe characters"):
        AggregationQuery(
            group_by=("bad field",),
            aggregations=(
                AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt"),
            ),
        )


def test_aggregation_query_is_frozen() -> None:
    """AggregationQuery is immutable."""
    q = AggregationQuery(
        aggregations=(
            AggregationExpression(AggregationFunc.COUNT, field=None, alias="cnt"),
        ),
    )
    with pytest.raises(Exception):
        q.limit = 10  # type: ignore[misc]
