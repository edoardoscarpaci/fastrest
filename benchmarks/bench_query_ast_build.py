"""`QueryBuilder` → AST → SQLAlchemy compile (Plan 028 / Phase 3, P2).

**This is Phase 5 (P3)'s gate.** The ``slots=True`` sweep is ⛔ blocked until a
memory measurement over this benchmark's AST node population shows a **≥20%**
per-instance reduction. Two benchmarks, so the AST construction cost and the
compile cost are separable — ``slots`` can only affect the former.

⚠️ This module imports ``varco_sa``. That is allowed and is not a violation of
``README.md``'s rule 2: compiling a ``Select`` into a SQL string is pure
in-process work against SQLAlchemy's expression language — no engine, no
connection, no container. The rule forbids a *broker or database client*, not
every backend package.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import DeclarativeBase
from varco_core.query.builder import QueryBuilder
from varco_core.query.type import Operation
from varco_sa.query.applicator import SQLAlchemyQueryApplicator


class _Base(DeclarativeBase):
    pass


class _ProductORM(_Base):
    """Minimal mapped class — three columns, no relationships, no metadata registry churn."""

    __tablename__ = "bench_products"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    price = Column(Integer)


def _build_ast() -> object:
    """Build a five-node AST: three comparisons, one AND chain, one OR branch."""
    return (
        QueryBuilder()
        .eq("name", "Alice")
        .where("price", Operation.GREATER_THAN, 10)
        .or_(QueryBuilder().where("price", Operation.LESS_THAN, 5))
        .build()
    )


def test_ast_build(benchmark) -> None:  # type: ignore[no-untyped-def]
    node = benchmark(_build_ast)
    assert node is not None


def test_ast_build_and_sa_compile(benchmark) -> None:  # type: ignore[no-untyped-def]
    applicator = SQLAlchemyQueryApplicator(_ProductORM)

    def _build_and_compile() -> str:
        stmt = applicator.apply_query(select(_ProductORM), _build_ast())
        return str(stmt)

    sql = benchmark(_build_and_compile)
    assert "WHERE" in sql.upper()
