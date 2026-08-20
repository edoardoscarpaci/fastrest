"""
varco_conformance
==================

Shared, test-only conformance suites for ``varco_core`` ABCs
(``AbstractEventBus``, ``CacheBackend``, ``AbstractJobStore``,
``AbstractDeadLetterQueue``).

This package lives at the repo root under ``testkit/`` — it is **never**
packaged or published, and is reached only via each participating package's
``pythonpath = ["../testkit"]`` pytest ini setting (Plan 012 / RT6, Open
Question 2).

Contract for every class in this package:

- Never named ``Test*`` — pytest's default collection only picks up
  ``Test*`` classes, so these abstract base classes are never collected
  standalone. A backend opts in by subclassing and naming its concrete
  subclass ``Test<Backend><Thing>Conformance``.
- Every abstract fixture (the thing under test, e.g. ``bus``/``cache``/
  ``store``/``dlq``) raises ``NotImplementedError`` by default — a backend
  subclass that forgets to override the fixture fails loudly and
  immediately, rather than silently skipping the whole suite.
- A genuine backend contract violation discovered by these suites is
  recorded by the *consuming* per-backend test module as
  ``@pytest.mark.xfail(reason="BUG: ...", strict=True)`` plus a BACKLOG
  entry — these base classes never weaken an assertion to work around a
  known bug (Plan 012 Non-goals).
"""

from __future__ import annotations
