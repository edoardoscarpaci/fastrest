"""
varco_sa.query
==============
SQLAlchemy-specific query implementations.

    SQLAlchemyQueryCompiler        — AST → SA filter expressions (visitor)
    SQLAlchemyQueryApplicator      — applies filter/sort/pagination to Select
    SQLAlchemyAggregationApplicator — applies GROUP BY / HAVING / LIMIT to Select

Import directly from sub-modules::

    from varco_sa.query.compiler import SQLAlchemyQueryCompiler
    from varco_sa.query.applicator import SQLAlchemyQueryApplicator
    from varco_sa.query.aggregation import SQLAlchemyAggregationApplicator
"""

from varco_sa.query.aggregation import SQLAlchemyAggregationApplicator
from varco_sa.query.applicator import SQLAlchemyQueryApplicator
from varco_sa.query.compiler import SQLAlchemyQueryCompiler

__all__ = [
    "SQLAlchemyQueryCompiler",
    "SQLAlchemyQueryApplicator",
    "SQLAlchemyAggregationApplicator",
]
