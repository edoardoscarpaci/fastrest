"""
varco_core.query.applicator
================================
Strategy classes that apply AST nodes, sort directives, and pagination to
backend-native query objects.

    QueryApplicator — abstract strategy base

Import from the sub-module directly::

    from varco_core.query.applicator.applicator import QueryApplicator

For the SQLAlchemy implementation see ``varco_sa.query.applicator``.
"""

from varco_core.query.applicator.applicator import QueryApplicator

__all__ = [
    "QueryApplicator",
]
