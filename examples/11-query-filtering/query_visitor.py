"""
query_visitor.py
================
In-memory AST visitor that evaluates a ``FilterNode`` tree against a Python object.

``InMemoryFilterVisitor`` is the centrepiece of this example.  It extends
``BinaryWalkingVisitor`` (which handles the AND/OR/NOT tree walk) and adds a
comparison evaluator that inspects attributes on any Python object.

This pattern is backend-agnostic: the same AST produced by ``QueryParser`` for
a SQL WHERE clause can be re-targeted at a plain Python list with just this
visitor — no ORM, no database required.

DESIGN: ``BinaryWalkingVisitor`` over bare ``ASTVisitor``
    ✅ ``BinaryWalkingVisitor`` provides the recursive AND/OR/NOT walk already;
       we only need to implement ``_visit_comparison`` + three combine hooks.
    ✅ Same pattern used by the SQLAlchemy and Beanie compilers — shows readers
       how to extend the visitor hierarchy for a new backend.
    ❌ One extra level of indirection vs subclassing ``ASTVisitor`` directly —
       acceptable because the walking logic is non-trivial and should be shared.

DESIGN: ``apply(items, ast)`` factory function over calling ``visit()`` directly
    ✅ Hides the visitor instantiation from the router — a single-call interface.
    ✅ Returns a new list — caller's original sequence is never mutated.
    ❌ One extra function frame — negligible for catalog-sized lists.

Thread safety:  ✅ ``InMemoryFilterVisitor`` is stateless — safe for concurrent use.
Async safety:   ✅ All methods are synchronous; safe to call from async handlers.
"""

from __future__ import annotations

from typing import Any

from varco_core.query.type import (
    ComparisonNode,
    Operation,
    TransformerNode,
)
from varco_core.query.visitor.walking import BinaryWalkingVisitor


class InMemoryFilterVisitor(BinaryWalkingVisitor):
    """
    Evaluates a ``FilterNode`` AST against a Python object via attribute lookup.

    The visitor walks the AST and produces a single ``bool`` indicating whether
    the object satisfies the filter.  Call ``visit(node)`` to get a
    predicate callable, then invoke it with the object under test.

    DESIGN: produce a predicate callable, not a bare bool
        ✅ The predicate is created once per AST (not per object) and can be
           applied to many objects in a loop without re-walking the tree.
        ✅ ``_combine_and`` / ``_combine_or`` compose predicates via ``and`` /
           ``or`` — no mutable state, no closure leakage between items.
        ❌ Slightly more indirect than evaluating against a concrete object
           directly — the indirection is intentional for reusability.

    Supported operators (from ``Operation`` enum):
        - ``EQUAL``         → ``obj.field == value``
        - ``NOT_EQUAL``     → ``obj.field != value``
        - ``GREATER_THAN``  → ``obj.field > value``
        - ``LESS_THAN``     → ``obj.field < value``
        - ``GREATER_EQUAL`` → ``obj.field >= value``
        - ``LESS_EQUAL``    → ``obj.field <= value``
        - ``LIKE``          → case-insensitive substring match
        - ``IN``            → ``obj.field in value_list``
        - ``IS_NULL``       → ``obj.field is None``
        - ``IS_NOT_NULL``   → ``obj.field is not None``

    Thread safety:  ✅ Stateless — new instance per visitor call is fine.
    Async safety:   ✅ Synchronous; safe to call from async handlers.

    Edge cases:
        - Field does not exist on the object → ``getattr`` returns ``None``;
          comparison will almost always be ``False`` (not an error).
        - ``LIKE`` against a non-string field → ``str()`` coercion applied
          before the case-insensitive substring check.
        - ``IN`` with an empty list → always ``False`` (standard membership).
    """

    # ── BinaryWalkingVisitor combine hooks ────────────────────────────────────

    def _combine_and(self, left: Any, right: Any) -> Any:
        """
        Return a predicate that is True when both ``left`` and ``right`` are True.

        Args:
            left:  Predicate from the AND node's left child.
            right: Predicate from the AND node's right child.

        Returns:
            A callable that returns ``left(obj) and right(obj)``.
        """
        # Short-circuit: if left is False, right is never evaluated —
        # mirrors Python's own ``and`` semantics.
        return lambda obj: left(obj) and right(obj)

    def _combine_or(self, left: Any, right: Any) -> Any:
        """
        Return a predicate that is True when either ``left`` or ``right`` is True.

        Args:
            left:  Predicate from the OR node's left child.
            right: Predicate from the OR node's right child.

        Returns:
            A callable that returns ``left(obj) or right(obj)``.
        """
        # Short-circuit: if left is True, right is never evaluated.
        return lambda obj: left(obj) or right(obj)

    def _combine_not(self, inner: Any) -> Any:
        """
        Return a predicate that negates ``inner``.

        Args:
            inner: Predicate from the NOT node's child.

        Returns:
            A callable that returns ``not inner(obj)``.
        """
        return lambda obj: not inner(obj)

    # ── Comparison leaf ───────────────────────────────────────────────────────

    def _visit_comparison(self, node: ComparisonNode, args: Any = None, **kwargs: Any) -> Any:
        """
        Build a predicate for a single field comparison.

        Reads the field value from the object via ``getattr`` and applies the
        comparison operator.  Missing attributes default to ``None``.

        Args:
            node: The ``ComparisonNode`` describing the comparison.
            args: Unused — present for ``BinaryWalkingVisitor`` API compatibility.

        Returns:
            A callable ``(obj: Any) -> bool`` that evaluates the comparison.

        Edge cases:
            - ``getattr(obj, node.field, None)`` — missing fields return None.
            - ``LIKE`` coerces both sides to lowercase strings.
            - Unknown ``Operation`` raises ``NotImplementedError`` immediately.
        """
        # Capture the node fields in local variables so the closure doesn't
        # hold a reference to the full node object unnecessarily.
        field_name = node.field
        op = node.op
        expected = node.value

        def predicate(obj: Any) -> bool:
            # Read field value — missing fields silently become None rather than
            # raising AttributeError, which keeps filters robust against partial
            # objects (e.g. test fixtures with only some fields populated).
            actual = getattr(obj, field_name, None)

            if op == Operation.EQUAL:
                return actual == expected  # type: ignore[return-value]

            if op == Operation.NOT_EQUAL:
                return actual != expected  # type: ignore[return-value]

            if op == Operation.GREATER_THAN:
                return actual is not None and actual > expected  # type: ignore[operator]

            if op == Operation.LESS_THAN:
                return actual is not None and actual < expected  # type: ignore[operator]

            if op == Operation.GREATER_EQUAL:
                return actual is not None and actual >= expected  # type: ignore[operator]

            if op == Operation.LESS_EQUAL:
                return actual is not None and actual <= expected  # type: ignore[operator]

            if op == Operation.LIKE:
                # LIKE = case-insensitive substring match (SQL LIKE with wildcards
                # is replaced here by a simple substring check — sufficient for
                # this example and intuitive for REST API consumers).
                return expected is not None and str(expected).lower() in str(actual).lower()

            if op == Operation.IN:
                # expected is a list (enforced by ComparisonNode.__post_init__)
                return actual in expected  # type: ignore[operator]

            if op == Operation.IS_NULL:
                return actual is None

            if op == Operation.IS_NOT_NULL:
                return actual is not None

            raise NotImplementedError(
                f"InMemoryFilterVisitor does not support operator {op!r}. "
                f"Supported: {list(Operation)}"
            )

        return predicate


def apply_filter(
    items: tuple | list,
    node: TransformerNode | None,
) -> list:
    """
    Apply an AST filter to a sequence of Python objects and return matches.

    A convenience wrapper around ``InMemoryFilterVisitor`` — hides visitor
    instantiation and the predicate call pattern from the router.

    Args:
        items: The sequence of objects to filter.
        node:  Root AST filter node.  ``None`` means "no filter" → all items
               are returned.

    Returns:
        A new list containing only the items that satisfy the filter.
        The original sequence is never mutated.

    Edge cases:
        - ``node is None`` → all items returned as a plain list copy.
        - Empty ``items`` → empty list returned immediately (visitor not built).

    Thread safety:  ✅ Stateless — each call creates a fresh visitor.
    Async safety:   ✅ Synchronous; safe to call from async handlers.
    """
    # Fast path: no filter — return a plain list copy without building a visitor.
    if node is None:
        return list(items)

    # Build the predicate once, then apply to every item in a single pass.
    predicate = InMemoryFilterVisitor().visit(node)
    return [item for item in items if predicate(item)]


__all__ = ["InMemoryFilterVisitor", "apply_filter"]
