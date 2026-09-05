"""`CreateDTO` / `ReadDTO` validate + dump (Plan 028 / Phase 3, P2).

The pydantic hot path: every request pays one ``CreateDTO`` validation on the
way in and one ``ReadDTO`` construction + ``model_dump()`` on the way out.
Nothing in Plan 028 changes this path — it is here as a *reference series*, so
that when a query or service benchmark moves, a reviewer can tell whether
varco moved or pydantic did.
"""

from __future__ import annotations

from typing import Any

from conftest import FIXED_TS
from varco_core.dto import CreateDTO, ReadDTO


class _CreateProductDTO(CreateDTO):
    """Four fields of three scalar types — representative, not exhaustive."""

    name: str
    price: float
    quantity: int
    description: str | None = None


class _ReadProductDTO(ReadDTO):
    """Inherits ``pk`` / ``created_at`` / ``updated_at`` from ``ReadDTO``."""

    name: str
    price: float
    quantity: int


_RAW: dict[str, Any] = {
    "name": "Widget",
    "price": 9.99,
    "quantity": 42,
    "description": "a benchmark fixture",
}


def test_create_dto_validate(benchmark) -> None:  # type: ignore[no-untyped-def]
    dto = benchmark(_CreateProductDTO.model_validate, _RAW)
    assert dto.quantity == 42


def test_read_dto_roundtrip(benchmark) -> None:  # type: ignore[no-untyped-def]
    def _roundtrip() -> dict[str, Any]:
        dto = _ReadProductDTO(
            pk="prd_1",
            name="Widget",
            price=9.99,
            quantity=42,
            created_at=FIXED_TS,
            updated_at=FIXED_TS,
        )
        return dto.model_dump()

    dumped = benchmark(_roundtrip)
    assert dumped["name"] == "Widget"
