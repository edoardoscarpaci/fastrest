# 11 — Query Filtering

A deep-dive into varco's query AST system applied to a read-only product
catalog.  No database, no broker, no Docker required.

## What this teaches

The full query pipeline from HTTP string to filtered Python list:

| Step | Component | What it does |
|------|-----------|--------------|
| 1 | `QueryParser` | Parses a Lark-grammar expression into a typed AST |
| 2 | `ASTTypeCoercion` | Coerces string scalars to Python types (`"True"` → `True`, `50.0` already float) |
| 3 | `ASTQueryOptimizer` | Simplifies the AST (double-NOT elimination, AND flattening) |
| 4 | `InMemoryFilterVisitor` | Evaluates the AST against a Python object list |
| 5 | Sort + pagination | Applied in Python after filtering |

## File map

```
app.py              — FastAPI factory (plain APIRouter, no DI)
data.py             — 20-item hardcoded product catalog
models.py           — Product frozen dataclass
query_visitor.py    — InMemoryFilterVisitor + apply_filter()
router.py           — GET /v1/products with full query pipeline
tests/
  test_smoke.py     — HTTP + AST + visitor + coercion + optimizer tests
```

## Run locally

```bash
cd examples/11-query-filtering
uv run uvicorn app:app --reload
```

Open `http://localhost:8000/docs` to explore the interactive OpenAPI UI.

## Filter syntax

All string literals **must** use double-quotes — the grammar's `ESCAPED_STRING`
terminal does not accept single quotes.

```
# Numeric comparison (no quotes needed)
GET /v1/products?q=price >= 50.0
GET /v1/products?q=price >= 10.0 AND price <= 100.0

# Boolean (coerced from string via ASTTypeCoercion)
GET /v1/products?q=in_stock = "True"
GET /v1/products?q=in_stock = "False"

# String equality
GET /v1/products?q=category = "electronics"

# Substring match (LIKE = case-insensitive contains)
GET /v1/products?q=name LIKE "widget"

# IN list
GET /v1/products?q=category IN ("books", "home")

# OR
GET /v1/products?q=price < 20.0 OR price > 400.0

# NOT
GET /v1/products?q=NOT (category = "clothing")

# Compound AND
GET /v1/products?q=price >= 50.0 AND in_stock = "True" AND category = "electronics"
```

## Sort and pagination

```
# Sort: - = descending, + or none = ascending
GET /v1/products?sort=-price
GET /v1/products?sort=+name

# Pagination
GET /v1/products?limit=5&offset=0

# Combined
GET /v1/products?q=category = "books"&sort=-price&limit=3&offset=0
```

## Key finding

The Lark grammar uses `ESCAPED_STRING` for string literals, which only accepts
**double-quoted** strings.  Single-quoted strings like `category = 'books'` cause
a parse error.  See `FINDINGS.md` for the full note (F09).

## Run tests

```bash
# From workspace root
uv run pytest .claude/worktrees/feature+examples-catalog/examples/11-query-filtering/tests/ -v
```
