"""`QueryParser.parse()` on a fixed filter string (Plan 028 / Phase 3, P2).

**This is Phase 4 (P4)'s gate.** The reflection-caching row — replacing
``QueryParser._parser``'s per-instance ``@cached_property`` with a module-level
cached, transformer-less ``Lark`` — is ⛔ blocked until this benchmark shows a
**≥10%** improvement. If it does not, §D-P3P4-gate says the row closes as
*measured, not worth it*, and that is a successful outcome.

Two benchmarks, because they answer different questions:

* ``test_parse_reused_parser`` — parse cost with the Lark object already built.
  This is the steady-state cost and the thing P4 would *not* change.
* ``test_parse_fresh_parser`` — construct a ``QueryParser`` and parse once.
  This is the per-request cost **if** parsers are constructed per request,
  which is exactly the unverified assumption Plan 028's Step 23 must check
  first. The gap between the two numbers *is* P4's entire available win.
"""

from __future__ import annotations

from conftest import FILTER_QUERY
from varco_core.query.parser import QueryParser


def test_parse_reused_parser(benchmark) -> None:  # type: ignore[no-untyped-def]
    # Warm the cached_property outside the measured region, so this measures
    # parsing and not grammar compilation.
    parser = QueryParser()
    parser.parse(FILTER_QUERY)
    node = benchmark(parser.parse, FILTER_QUERY)
    assert node is not None


def test_parse_fresh_parser(benchmark) -> None:  # type: ignore[no-untyped-def]
    def _build_and_parse() -> object:
        return QueryParser().parse(FILTER_QUERY)

    node = benchmark(_build_and_parse)
    assert node is not None
