"""
router.py
=========
Product catalog router with full query AST pipeline.

Demonstrates the complete varco query system pipeline on a plain FastAPI
``APIRouter`` — no service layer, no DI, no database.

The endpoint ``GET /v1/products`` supports:
- Filtering via a Lark-grammar expression in the ``q`` query param.
- Sorting via ``+field`` / ``-field`` directives in the ``sort`` param.
- Pagination via ``limit`` and ``offset``.

Filter syntax (single ``q=`` expression using Lark grammar)::

    GET /v1/products?q=price >= 50.0
    GET /v1/products?q=in_stock = "True"
    GET /v1/products?q=category = "electronics"
    GET /v1/products?q=name LIKE "widget"
    GET /v1/products?q=price >= 10.0 AND price <= 50.0

Sort::

    GET /v1/products?sort=-price
    GET /v1/products?sort=+name

Pagination::

    GET /v1/products?limit=5&offset=10

DESIGN: plain ``APIRouter`` over ``GenericRouter`` for this example
    The varco ``GenericRouter`` / ``@route`` decorator only injects ``ctx``
    (AuthContext) and path params into handlers — it does NOT forward the raw
    ``Request`` object.  Since this example needs direct access to query string
    params (``q=``, ``sort=``, ``limit=``, ``offset=``), using a plain
    ``fastapi.APIRouter`` with ``Query()`` dependencies is simpler and more
    instructive.

    ✅ FastAPI's ``Query()`` provides OpenAPI schema generation for free.
    ✅ No DI machinery needed — the router is a pure function dispatcher.
    ✅ Shows the query system without coupling it to the varco router framework.
    ❌ Loses varco middleware / ``RouteGuard`` integration — acceptable for a
       teaching example focused on the query AST, not authorization.

Thread safety:  ✅ Stateless router — all mutable state is per-request local.
Async safety:   ✅ Route handler is ``async def`` with no blocking I/O.
"""

from __future__ import annotations

import logging
from typing import Any

from data import PRODUCTS
from fastapi import APIRouter, Query
from models import Product
from query_visitor import apply_filter
from varco_core.query.parser import QueryParser
from varco_core.query.type import SortField, SortOrder, TransformerNode
from varco_core.query.visitor.query_optimizer import ASTQueryOptimizer
from varco_core.query.visitor.type_coercion import (
    ASTTypeCoercion,
    TypeCoercionRegistry,
    coerce_boolean,
    coerce_float,
    coerce_int,
)

logger = logging.getLogger(__name__)

# ── Shared pipeline components — built once at module load ────────────────────
# DESIGN: module-level singletons for stateless pipeline components
#   ✅ ``QueryParser`` compiles the Lark grammar on first use (cached_property)
#      — building it once amortises the compilation cost across all requests.
#   ✅ ``ASTQueryOptimizer`` and ``ASTTypeCoercion`` are stateless — safe to share.
#   ❌ Module-level state — tests should build their own instances when they
#      want to verify pipeline behaviour in isolation (unit tests do exactly that).

_parser = QueryParser()
_optimizer = ASTQueryOptimizer()

# Coercion registry for Product fields.
# ``price``    → float  (grammar always produces float for numeric literals; explicit
#                         registration ensures IN-list elements are coerced too)
# ``in_stock`` → bool   (grammar has no bool literal; "True"/"False" arrive as strings)
# ``id``       → int    (grammar produces float; we want integer equality checks)
_registry = TypeCoercionRegistry()
_registry.register_field("price", float, coerce_float)
_registry.register_field("id", int, coerce_int)
_registry.register_field("in_stock", bool, coerce_boolean)

_coercer = ASTTypeCoercion(_registry)


def _build_ast(q: str | None) -> TransformerNode | None:
    """
    Parse, coerce, and optimise a filter expression string into an AST node.

    The full pipeline:
    1. ``QueryParser.parse()``   — Lark grammar → typed AST.
    2. ``ASTTypeCoercion``       — string scalars → Python types.
    3. ``ASTQueryOptimizer``     — double-NOT elimination, AND flattening.

    Args:
        q: Filter expression string, e.g. ``"price >= 50.0 AND in_stock = True"``.
           ``None`` means "no filter".

    Returns:
        Optimised ``TransformerNode``, or ``None`` if ``q`` is falsy.

    Raises:
        lark.UnexpectedEOF:   ``q`` is empty.
        lark.UnexpectedToken: ``q`` has a syntax error.
    """
    if not q:
        # No filter — caller should treat None as "select all".
        return None

    node: TransformerNode = _parser.parse(q)
    node = _coercer.visit(node)
    node = _optimizer.visit(node)
    return node


def _parse_sort(sort_str: str | None) -> list[SortField]:
    """
    Parse a sort directive string into a list of ``SortField`` objects.

    Accepts comma-separated sort fields prefixed with ``+`` (ascending) or
    ``-`` (descending).  An unprefixed field defaults to ascending.

    Args:
        sort_str: Sort directive string, e.g. ``"-price,+name"`` or ``"-price"``.
                  ``None`` or empty → no sort applied.

    Returns:
        Ordered list of ``SortField`` directives — first has highest priority.

    Edge cases:
        - ``None`` or empty string → empty list (no sort).
        - Unknown field names are accepted silently; ``getattr`` returns ``None``
          for missing fields, which will raise ``TypeError`` during comparison.
          Production code should validate field names against the model schema.
    """
    if not sort_str:
        return []

    result: list[SortField] = []
    for part in sort_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            result.append(SortField(field=part[1:], order=SortOrder.DESC))
        elif part.startswith("+"):
            result.append(SortField(field=part[1:], order=SortOrder.ASC))
        else:
            # No prefix → ascending by convention (SQL default).
            result.append(SortField(field=part, order=SortOrder.ASC))

    return result


def _apply_sort(items: list[Product], sort_fields: list[SortField]) -> list[Product]:
    """
    Sort a product list in place according to the given sort directives.

    Sorts are applied right-to-left so the first directive has the highest
    priority — this exploits Python's stable sort guarantee.

    Args:
        items:       Mutable list of products to sort (modified in place).
        sort_fields: Ordered sort directives; first has highest priority.

    Returns:
        The same list, sorted and returned for chaining.

    Edge cases:
        - Empty ``sort_fields`` → list unchanged.
        - Field missing on ``Product`` → ``AttributeError`` from ``getattr``;
          production code should validate field names first.
    """
    # Reverse to process from lowest to highest priority, exploiting stable sort.
    for sf in reversed(sort_fields):
        reverse = sf.order == SortOrder.DESC
        items.sort(key=lambda p, f=sf.field: getattr(p, f), reverse=reverse)
    return items


# ── Router ────────────────────────────────────────────────────────────────────


router = APIRouter(prefix="/v1", tags=["products"])


@router.get("/products")
async def list_products(
    q: str | None = Query(
        default=None,
        description=(
            "Lark-grammar filter expression. Examples:\n"
            "- `price >= 50.0`\n"
            '- `in_stock = "True"`\n'
            '- `category = "electronics"`\n'
            '- `name LIKE "widget"`\n'
            "- `price >= 10.0 AND price <= 50.0`\n"
            '- `category IN ("books", "home")`'
        ),
    ),
    sort: str | None = Query(
        default=None,
        description=(
            "Sort directives — comma-separated, prefix with ``-`` (descending) "
            "or ``+`` / none (ascending). Example: ``-price,+name``"
        ),
    ),
    limit: int | None = Query(
        default=None,
        ge=0,
        description="Maximum number of results. Omit for all results.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of results to skip. Default 0.",
    ),
) -> list[dict[str, Any]]:
    """
    List products with optional filtering, sorting, and pagination.

    Runs the full varco query pipeline:

    1. **Parse** ``q`` into a typed AST via ``QueryParser`` (Lark grammar).
    2. **Coerce** string scalars to Python types via ``ASTTypeCoercion``.
    3. **Optimize** the AST (double-NOT elimination, AND flattening).
    4. **Filter** the product list via ``InMemoryFilterVisitor``.
    5. **Sort** results by the ``sort`` directives.
    6. **Paginate** via ``offset`` + ``limit``.

    Returns:
        JSON array of matching products (each has ``id``, ``name``, ``price``,
        ``category``, ``in_stock``).

    Raises:
        422: ``q`` contains a syntax error (Lark ``UnexpectedToken``).
    """
    # ── 1-3. Parse, coerce, optimize ──────────────────────────────────────────
    ast_node = _build_ast(q)

    # ── 4. Filter ─────────────────────────────────────────────────────────────
    # ``apply_filter`` returns a new list — the ``PRODUCTS`` tuple is never mutated.
    filtered = apply_filter(PRODUCTS, ast_node)

    # ── 5. Sort ───────────────────────────────────────────────────────────────
    sort_fields = _parse_sort(sort)
    if sort_fields:
        _apply_sort(filtered, sort_fields)

    # ── 6. Paginate ───────────────────────────────────────────────────────────
    # Apply offset before limit — mirrors SQL ``OFFSET … LIMIT`` semantics.
    paginated = filtered[offset:]
    if limit is not None:
        paginated = paginated[:limit]

    logger.debug(
        "list_products: q=%r sort=%r limit=%r offset=%d "
        "total=%d filtered=%d paginated=%d",
        q,
        sort,
        limit,
        offset,
        len(PRODUCTS),
        len(filtered),
        len(paginated),
    )

    return [p.to_dict() for p in paginated]


__all__ = ["router", "_build_ast", "_parse_sort", "_apply_sort"]
