"""
tests/test_smoke.py
===================
Smoke tests for the ``11-query-filtering`` example.

Covers:
- HTTP integration: full round-trip via ``ASGITransport`` + ``httpx``.
- AST unit tests: direct ``QueryParser`` + visitor tests (no HTTP).
- Type coercion: string → Python type via ``ASTTypeCoercion``.
- Optimizer: double-NOT elimination.

Filter syntax uses the Lark grammar in a single ``q=`` query param:
    ``?q=price >= 50.0 AND in_stock = True``

All tests are ``async def`` — ``asyncio_mode = "auto"`` in the workspace
``pyproject.toml`` means no ``@pytest.mark.asyncio`` is needed.

No database, no broker, no Docker — everything runs in-process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
# The example lives outside the installed package tree; add its directory to
# sys.path so ``app``, ``data``, ``models``, etc. are importable.
_EXAMPLE_DIR = Path(__file__).parent.parent
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

# ── Imports ───────────────────────────────────────────────────────────────────
from app import create_app  # noqa: E402 — after sys.path setup
from data import PRODUCTS  # noqa: E402
from query_visitor import InMemoryFilterVisitor, apply_filter  # noqa: E402
from varco_core.query.parser import QueryParser  # noqa: E402
from varco_core.query.type import (  # noqa: E402
    AndNode,
    ComparisonNode,
    NotNode,
    Operation,
    OrNode,
)
from varco_core.query.visitor.query_optimizer import ASTQueryOptimizer  # noqa: E402
from varco_core.query.visitor.type_coercion import (  # noqa: E402
    ASTTypeCoercion,
    TypeCoercionRegistry,
    coerce_boolean,
    coerce_float,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> httpx.AsyncClient:
    """
    Return an ``AsyncClient`` backed by the in-process ASGI app.

    Each test gets a fresh ``create_app()`` instance — test isolation
    guaranteed because the app and its ASGI transport are created anew.
    """
    app = create_app()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


# ── HTTP integration tests ────────────────────────────────────────────────────


class TestListProductsHTTP:
    """Full round-trip tests via ASGITransport + httpx."""

    async def test_no_filters_returns_all_products(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        ``GET /v1/products`` with no filters must return all 20 catalog items.
        """
        async with client as c:
            resp = await c.get("/v1/products")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == len(PRODUCTS)

        # Verify the shape of the first item — every field must be present.
        first = data[0]
        assert {"id", "name", "price", "category", "in_stock"} == set(first.keys())

    async def test_filter_price_gte(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=price >= 50.0`` must return only products with price ≥ 50.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"q": "price >= 50.0"})

        assert resp.status_code == 200
        data = resp.json()

        # Every returned product must satisfy the filter.
        assert all(p["price"] >= 50.0 for p in data)

        # Cross-check against expected count from the catalog.
        expected_count = sum(1 for p in PRODUCTS if p.price >= 50.0)
        assert len(data) == expected_count

    async def test_filter_in_stock_true(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=in_stock = "True"`` must return only in-stock products.

        The grammar has no bool literal; ``"True"`` arrives as a string and is
        coerced to Python ``True`` by ``coerce_boolean`` registered in the pipeline.
        Lark's ESCAPED_STRING terminal uses double-quotes.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"q": 'in_stock = "True"'})

        assert resp.status_code == 200
        data = resp.json()

        assert all(p["in_stock"] is True for p in data)
        expected_count = sum(1 for p in PRODUCTS if p.in_stock)
        assert len(data) == expected_count

    async def test_filter_in_stock_false(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=in_stock = "False"`` must return only out-of-stock products.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"q": 'in_stock = "False"'})

        assert resp.status_code == 200
        data = resp.json()

        assert all(p["in_stock"] is False for p in data)
        expected_count = sum(1 for p in PRODUCTS if not p.in_stock)
        assert len(data) == expected_count

    async def test_filter_name_like_widget(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=name LIKE "widget"`` (case-insensitive substring) must match
        products whose name contains "widget" regardless of case.
        Lark's ESCAPED_STRING terminal uses double-quotes.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"q": 'name LIKE "widget"'})

        assert resp.status_code == 200
        data = resp.json()

        # All returned products must contain "widget" in the name (case-insensitive).
        assert all("widget" in p["name"].lower() for p in data)

        # Cross-check count against catalog.
        expected = [p for p in PRODUCTS if "widget" in p.name.lower()]
        assert len(data) == len(expected)

    async def test_filter_category_electronics(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=category = "electronics"`` must return only electronics products.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"q": 'category = "electronics"'})

        assert resp.status_code == 200
        data = resp.json()

        assert all(p["category"] == "electronics" for p in data)
        expected_count = sum(1 for p in PRODUCTS if p.category == "electronics")
        assert len(data) == expected_count

    async def test_sort_price_descending(self, client: httpx.AsyncClient) -> None:
        """
        ``?sort=-price`` must return products ordered by price descending.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"sort": "-price"})

        assert resp.status_code == 200
        prices = [p["price"] for p in resp.json()]

        # Each price must be >= the next one.
        assert prices == sorted(prices, reverse=True)

    async def test_sort_price_ascending(self, client: httpx.AsyncClient) -> None:
        """
        ``?sort=+price`` must return products ordered by price ascending.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"sort": "+price"})

        assert resp.status_code == 200
        prices = [p["price"] for p in resp.json()]
        assert prices == sorted(prices)

    async def test_pagination_limit(self, client: httpx.AsyncClient) -> None:
        """
        ``?limit=3`` must return at most 3 products.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"limit": "3"})

        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_pagination_offset(self, client: httpx.AsyncClient) -> None:
        """
        ``?limit=3&offset=0`` and ``?limit=3&offset=3`` must return
        non-overlapping pages.
        """
        async with client as c:
            page1 = (
                await c.get("/v1/products", params={"limit": "3", "offset": "0"})
            ).json()
            page2 = (
                await c.get("/v1/products", params={"limit": "3", "offset": "3"})
            ).json()

        ids_page1 = {p["id"] for p in page1}
        ids_page2 = {p["id"] for p in page2}

        # Pages must not overlap.
        assert ids_page1.isdisjoint(ids_page2)
        assert len(page1) == 3
        assert len(page2) == 3

    async def test_and_filter(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=price >= 10.0 AND price <= 50.0`` must return products in [10, 50].
        """
        async with client as c:
            resp = await c.get(
                "/v1/products",
                params={"q": "price >= 10.0 AND price <= 50.0"},
            )

        assert resp.status_code == 200
        data = resp.json()

        # All returned products must satisfy both constraints.
        assert all(10.0 <= p["price"] <= 50.0 for p in data)

        expected_count = sum(1 for p in PRODUCTS if 10.0 <= p.price <= 50.0)
        assert len(data) == expected_count

    async def test_filter_and_sort_combined(self, client: httpx.AsyncClient) -> None:
        """
        Filtering and sorting can be combined — books sorted by price descending.
        """
        async with client as c:
            resp = await c.get(
                "/v1/products",
                params={"q": 'category = "books"', "sort": "-price"},
            )

        assert resp.status_code == 200
        data = resp.json()

        # All results are books.
        assert all(p["category"] == "books" for p in data)

        # Results are sorted by price descending.
        prices = [p["price"] for p in data]
        assert prices == sorted(prices, reverse=True)

    async def test_limit_zero_returns_empty(self, client: httpx.AsyncClient) -> None:
        """
        ``?limit=0`` is an explicit empty page — returns zero items.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"limit": "0"})

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_offset_beyond_end_returns_empty(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        An ``offset`` larger than the total number of products returns an empty list.
        """
        async with client as c:
            resp = await c.get("/v1/products", params={"offset": "9999"})

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_in_list_filter(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=category IN ("books", "home")`` must return only those two categories.
        Lark's ESCAPED_STRING terminal requires double-quotes.
        """
        async with client as c:
            resp = await c.get(
                "/v1/products",
                params={"q": 'category IN ("books", "home")'},
            )

        assert resp.status_code == 200
        data = resp.json()

        assert all(p["category"] in ("books", "home") for p in data)
        expected_count = sum(1 for p in PRODUCTS if p.category in ("books", "home"))
        assert len(data) == expected_count

    async def test_or_filter(self, client: httpx.AsyncClient) -> None:
        """
        ``?q=price < 20.0 OR price > 400.0`` returns cheap and expensive products.
        Numeric literals don't need quotes — the grammar accepts SIGNED_NUMBER directly.
        """
        async with client as c:
            resp = await c.get(
                "/v1/products",
                params={"q": "price < 20.0 OR price > 400.0"},
            )

        assert resp.status_code == 200
        data = resp.json()

        # Each result must satisfy one of the two conditions.
        assert all(p["price"] < 20.0 or p["price"] > 400.0 for p in data)
        expected_count = sum(1 for p in PRODUCTS if p.price < 20.0 or p.price > 400.0)
        assert len(data) == expected_count


# ── AST unit tests ────────────────────────────────────────────────────────────


class TestQueryParser:
    """Direct parser tests — no HTTP involved."""

    def test_parse_produces_comparison_node(self) -> None:
        """
        ``QueryParser().parse("price >= 50.0")`` must produce a ``ComparisonNode``
        with the correct field, operator, and value.
        """
        parser = QueryParser()
        node = parser.parse("price >= 50.0")

        assert isinstance(node, ComparisonNode)
        assert node.field == "price"
        assert node.op == Operation.GREATER_EQUAL
        # ``QueryTransformer.number()`` converts numeric literals to float.
        assert node.value == 50.0

    def test_parse_and_produces_and_node(self) -> None:
        """
        ``"a = 1 AND b = 2"`` must produce an ``AndNode`` with two comparison children.
        """
        parser = QueryParser()
        node = parser.parse("price >= 10.0 AND price <= 50.0")

        assert isinstance(node, AndNode)
        assert isinstance(node.left, ComparisonNode)
        assert isinstance(node.right, ComparisonNode)

    def test_parse_or_produces_or_node(self) -> None:
        """
        ``"price < 10.0 OR price > 100.0"`` must produce an ``OrNode``.
        """
        parser = QueryParser()
        node = parser.parse("price < 10.0 OR price > 100.0")

        assert isinstance(node, OrNode)
        assert isinstance(node.left, ComparisonNode)
        assert isinstance(node.right, ComparisonNode)

    def test_parse_not_produces_not_node(self) -> None:
        """
        ``NOT (price > 100.0)`` must produce a ``NotNode``.
        """
        parser = QueryParser()
        # The grammar requires grouping parentheses around the NOT operand
        # when it's a comparison (``NOT term`` where ``term`` can be a factor
        # or a parenthesised expr).
        node = parser.parse("NOT (price > 100.0)")

        assert isinstance(node, NotNode)
        assert isinstance(node.child, ComparisonNode)

    def test_parse_in_list(self) -> None:
        """
        ``category IN ("books", "home")`` must produce a ComparisonNode with
        ``Operation.IN`` and a list value.
        Lark's ESCAPED_STRING terminal requires double-quotes (not single quotes).
        """
        parser = QueryParser()
        node = parser.parse('category IN ("books", "home")')

        assert isinstance(node, ComparisonNode)
        assert node.op == Operation.IN
        assert isinstance(node.value, list)
        assert "books" in node.value
        assert "home" in node.value

    def test_parse_is_null(self) -> None:
        """
        ``field IS NULL`` must produce a ``ComparisonNode`` with ``Operation.IS_NULL``.
        """
        parser = QueryParser()
        node = parser.parse("name IS NULL")

        assert isinstance(node, ComparisonNode)
        assert node.op == Operation.IS_NULL
        assert node.value is None

    def test_parse_is_not_null(self) -> None:
        """
        ``field IS NOT NULL`` must produce ``Operation.IS_NOT_NULL``.
        """
        parser = QueryParser()
        node = parser.parse("name IS NOT NULL")

        assert isinstance(node, ComparisonNode)
        assert node.op == Operation.IS_NOT_NULL


class TestTypeCoercion:
    """Tests for ``ASTTypeCoercion`` — string → Python type."""

    def test_float_value_passes_through(self) -> None:
        """
        The ``QueryParser`` already produces floats from numeric literals.
        After coercion with ``float`` registered, the value remains a float.
        """
        registry = TypeCoercionRegistry()
        registry.register_field("price", float, coerce_float)
        coercer = ASTTypeCoercion(registry)

        node = QueryParser().parse("price >= 50.0")
        coerced = coercer.visit(node)

        assert isinstance(coerced, ComparisonNode)
        assert isinstance(coerced.value, float)
        assert coerced.value == 50.0

    def test_boolean_string_coercion_true(self) -> None:
        """
        ``in_stock = "True"`` — Lark parses the double-quoted string as ``"True"``.
        ``coerce_boolean`` must convert it to Python ``True``.

        The Lark grammar uses ``ESCAPED_STRING`` (double-quotes only) so booleans
        must be written as ``"True"`` / ``"False"`` in filter expressions.
        """
        registry = TypeCoercionRegistry()
        registry.register_field("in_stock", bool, coerce_boolean)
        coercer = ASTTypeCoercion(registry)

        node = QueryParser().parse('in_stock = "True"')
        coerced = coercer.visit(node)

        assert isinstance(coerced, ComparisonNode)
        assert coerced.value is True

    def test_boolean_string_coercion_false(self) -> None:
        """
        ``in_stock = "False"`` must coerce to Python ``False``.
        ``"False"`` is not in ``coerce_boolean``'s truthy set → ``False``.
        """
        registry = TypeCoercionRegistry()
        registry.register_field("in_stock", bool, coerce_boolean)
        coercer = ASTTypeCoercion(registry)

        node = QueryParser().parse('in_stock = "False"')
        coerced = coercer.visit(node)

        assert isinstance(coerced, ComparisonNode)
        assert coerced.value is False

    def test_unregistered_field_passes_through(self) -> None:
        """
        Fields not in the registry must pass through unchanged after coercion.
        """
        coercer = ASTTypeCoercion(TypeCoercionRegistry())  # empty registry
        node = QueryParser().parse('category = "electronics"')
        coerced = coercer.visit(node)

        assert isinstance(coerced, ComparisonNode)
        assert coerced.value == "electronics"

    def test_coercion_applied_inside_and_node(self) -> None:
        """
        Coercion must recursively walk AND nodes and coerce each comparison.
        Uses double-quotes for string literals (Lark ESCAPED_STRING requirement).
        """
        registry = TypeCoercionRegistry()
        registry.register_field("price", float, coerce_float)
        registry.register_field("in_stock", bool, coerce_boolean)
        coercer = ASTTypeCoercion(registry)

        node = QueryParser().parse('price >= 50.0 AND in_stock = "True"')
        coerced = coercer.visit(node)

        assert isinstance(coerced, AndNode)
        # Left branch: price (float)
        left = coerced.left
        assert isinstance(left, ComparisonNode)
        assert isinstance(left.value, float)
        # Right branch: in_stock (bool)
        right = coerced.right
        assert isinstance(right, ComparisonNode)
        assert right.value is True


class TestASTOptimizer:
    """Tests for ``ASTQueryOptimizer``."""

    def test_double_not_elimination(self) -> None:
        """
        ``NOT(NOT(ComparisonNode))`` must be reduced to the inner ``ComparisonNode``.
        """
        inner = ComparisonNode(field="price", op=Operation.EQUAL, value=10.0)
        double_not = NotNode(NotNode(inner))

        result = ASTQueryOptimizer().visit(double_not)

        # The double-NOT is eliminated — result is the bare comparison.
        assert isinstance(result, ComparisonNode)
        assert result.field == "price"

    def test_single_not_preserved(self) -> None:
        """
        A single ``NOT`` must remain unchanged by the optimizer.
        """
        inner = ComparisonNode(field="in_stock", op=Operation.EQUAL, value=True)
        single_not = NotNode(inner)

        result = ASTQueryOptimizer().visit(single_not)

        assert isinstance(result, NotNode)

    def test_and_flattening(self) -> None:
        """
        Left-recursive ``(a AND b) AND c`` must be flattened to ``a AND (b AND c)``.
        """
        a = ComparisonNode(field="price", op=Operation.GREATER_EQUAL, value=10.0)
        b = ComparisonNode(field="price", op=Operation.LESS_EQUAL, value=50.0)
        c = ComparisonNode(field="in_stock", op=Operation.EQUAL, value=True)

        # Construct left-recursive: (a AND b) AND c
        left_recursive = AndNode(left=AndNode(left=a, right=b), right=c)
        result = ASTQueryOptimizer().visit(left_recursive)

        # After flattening: a AND (b AND c) — left child is a ComparisonNode
        assert isinstance(result, AndNode)
        assert isinstance(result.left, ComparisonNode)
        assert isinstance(result.right, AndNode)


class TestInMemoryFilterVisitor:
    """Unit tests for ``InMemoryFilterVisitor`` — no HTTP, no parser."""

    def test_equal(self) -> None:
        """Field == value must match when equal, not match when different."""
        from models import Product

        node = ComparisonNode(field="category", op=Operation.EQUAL, value="electronics")
        predicate = InMemoryFilterVisitor().visit(node)

        p_match = Product(
            id=1, name="X", price=10.0, category="electronics", in_stock=True
        )
        p_no_match = Product(
            id=2, name="Y", price=10.0, category="books", in_stock=True
        )

        assert predicate(p_match) is True
        assert predicate(p_no_match) is False

    def test_not_equal(self) -> None:
        """``!=`` must match when different, not match when equal."""
        from models import Product

        node = ComparisonNode(
            field="category", op=Operation.NOT_EQUAL, value="electronics"
        )
        predicate = InMemoryFilterVisitor().visit(node)

        p_elec = Product(
            id=1, name="X", price=10.0, category="electronics", in_stock=True
        )
        p_books = Product(id=2, name="Y", price=10.0, category="books", in_stock=True)

        assert predicate(p_elec) is False
        assert predicate(p_books) is True

    def test_gte(self) -> None:
        """``price >= 50.0`` must match exactly-50 and above, not below."""
        from models import Product

        node = ComparisonNode(field="price", op=Operation.GREATER_EQUAL, value=50.0)
        predicate = InMemoryFilterVisitor().visit(node)

        assert (
            predicate(Product(id=1, name="X", price=50.0, category="x", in_stock=True))
            is True
        )
        assert (
            predicate(Product(id=2, name="Y", price=49.99, category="x", in_stock=True))
            is False
        )

    def test_lte(self) -> None:
        """``price <= 50.0`` must match exactly-50 and below, not above."""
        from models import Product

        node = ComparisonNode(field="price", op=Operation.LESS_EQUAL, value=50.0)
        predicate = InMemoryFilterVisitor().visit(node)

        assert (
            predicate(Product(id=1, name="X", price=50.0, category="x", in_stock=True))
            is True
        )
        assert (
            predicate(Product(id=2, name="Y", price=50.01, category="x", in_stock=True))
            is False
        )

    def test_like_case_insensitive(self) -> None:
        """``name LIKE 'widget'`` must match regardless of case in the name."""
        from models import Product

        node = ComparisonNode(field="name", op=Operation.LIKE, value="widget")
        predicate = InMemoryFilterVisitor().visit(node)

        assert (
            predicate(
                Product(
                    id=1, name="Widget Pro", price=10.0, category="x", in_stock=True
                )
            )
            is True
        )
        assert (
            predicate(
                Product(
                    id=2, name="WIDGET 3000", price=10.0, category="x", in_stock=True
                )
            )
            is True
        )
        assert (
            predicate(
                Product(
                    id=3, name="Coffee Maker", price=10.0, category="x", in_stock=True
                )
            )
            is False
        )

    def test_in_operator(self) -> None:
        """``category IN ['books', 'home']`` must match listed categories only."""
        from models import Product

        node = ComparisonNode(
            field="category", op=Operation.IN, value=["books", "home"]
        )
        predicate = InMemoryFilterVisitor().visit(node)

        p_books = Product(id=1, name="X", price=10.0, category="books", in_stock=True)
        p_home = Product(id=2, name="Y", price=10.0, category="home", in_stock=True)
        p_elec = Product(
            id=3, name="Z", price=10.0, category="electronics", in_stock=True
        )

        assert predicate(p_books) is True
        assert predicate(p_home) is True
        assert predicate(p_elec) is False

    def test_is_null(self) -> None:
        """``IS_NULL`` must match when the field value is ``None``."""

        # Use a plain dict-like object to simulate a None field.
        class Obj:
            name = None

        node = ComparisonNode(field="name", op=Operation.IS_NULL)
        predicate = InMemoryFilterVisitor().visit(node)

        assert predicate(Obj()) is True

    def test_is_not_null(self) -> None:
        """``IS_NOT_NULL`` must match when the field value is not ``None``."""

        class Obj:
            name = "something"

        node = ComparisonNode(field="name", op=Operation.IS_NOT_NULL)
        predicate = InMemoryFilterVisitor().visit(node)

        assert predicate(Obj()) is True

    def test_and_combination(self) -> None:
        """AND predicate must require both sub-predicates to be true."""
        from models import Product

        left = ComparisonNode(field="price", op=Operation.GREATER_EQUAL, value=50.0)
        right = ComparisonNode(field="in_stock", op=Operation.EQUAL, value=True)
        and_node = AndNode(left=left, right=right)

        predicate = InMemoryFilterVisitor().visit(and_node)

        p_both = Product(id=1, name="X", price=100.0, category="x", in_stock=True)
        p_only_price = Product(
            id=2, name="Y", price=100.0, category="x", in_stock=False
        )
        p_only_stock = Product(id=3, name="Z", price=10.0, category="x", in_stock=True)
        p_neither = Product(id=4, name="W", price=10.0, category="x", in_stock=False)

        assert predicate(p_both) is True
        assert predicate(p_only_price) is False
        assert predicate(p_only_stock) is False
        assert predicate(p_neither) is False

    def test_or_combination(self) -> None:
        """OR predicate must be true when at least one sub-predicate is true."""
        from models import Product

        left = ComparisonNode(field="category", op=Operation.EQUAL, value="books")
        right = ComparisonNode(field="category", op=Operation.EQUAL, value="home")
        or_node = OrNode(left=left, right=right)

        predicate = InMemoryFilterVisitor().visit(or_node)

        p_books = Product(id=1, name="X", price=10.0, category="books", in_stock=True)
        p_home = Product(id=2, name="Y", price=10.0, category="home", in_stock=True)
        p_elec = Product(
            id=3, name="Z", price=10.0, category="electronics", in_stock=True
        )

        assert predicate(p_books) is True
        assert predicate(p_home) is True
        assert predicate(p_elec) is False

    def test_not_negation(self) -> None:
        """NOT predicate must invert the result of its inner predicate."""
        from models import Product

        inner = ComparisonNode(field="in_stock", op=Operation.EQUAL, value=True)
        not_node = NotNode(inner)

        predicate = InMemoryFilterVisitor().visit(not_node)

        p_in_stock = Product(id=1, name="X", price=10.0, category="x", in_stock=True)
        p_out_of_stock = Product(
            id=2, name="Y", price=10.0, category="x", in_stock=False
        )

        assert predicate(p_in_stock) is False
        assert predicate(p_out_of_stock) is True

    def test_apply_filter_no_node(self) -> None:
        """``apply_filter(items, None)`` must return all items unchanged."""
        from models import Product

        items = [
            Product(id=1, name="A", price=10.0, category="x", in_stock=True),
            Product(id=2, name="B", price=20.0, category="y", in_stock=False),
        ]
        result = apply_filter(items, None)
        assert result == items

    def test_apply_filter_with_node(self) -> None:
        """``apply_filter`` must return only items that satisfy the AST filter."""
        from models import Product

        items = [
            Product(id=1, name="Cheap", price=5.0, category="x", in_stock=True),
            Product(id=2, name="Pricey", price=100.0, category="x", in_stock=True),
        ]
        node = ComparisonNode(field="price", op=Operation.GREATER_EQUAL, value=50.0)
        result = apply_filter(items, node)

        assert len(result) == 1
        assert result[0].name == "Pricey"

    def test_apply_filter_empty_input(self) -> None:
        """``apply_filter`` on an empty list must return an empty list."""
        node = ComparisonNode(field="price", op=Operation.EQUAL, value=10.0)
        result = apply_filter([], node)
        assert result == []
