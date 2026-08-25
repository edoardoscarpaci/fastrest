"""
Shared ``container.validate()`` structural-health assertion (Plan 016 /
RL-3a, Design §RL-3a, Step 23).

CHANGELOG §2.0.0 (providify) is explicit that ``validate_bindings()`` /
``validate_all()`` stay unchanged and that ``container.validate()`` is
purely additive on top of them, reporting ``MISSING_BINDING``,
``MISSING_BINDING_DEFAULTED``, ``MISSING_BINDING_DEFERRED``,
``AMBIGUOUS_BINDING``, ``CIRCULAR_DEPENDENCY``, ``SCOPE_LEAK``, and
``LIVE_REQUIRED``/``UNRESOLVED_ANNOTATION`` issues. Most of varco's DI
health tests scan ONE package in isolation (e.g.
``container.scan("varco_redis")`` with no application bindings), so a
full-graph ``validate()`` will legitimately report ``MISSING_BINDING`` for
interfaces the application is expected to supply
(``AsyncRepository[User]``, ``AbstractAuthorizer``, app settings, ...).

This helper is the single sanctioned shape (rather than copy-pasted at all
17 call sites) for "fail hard on structural DI defects, tolerate an
app-supplied MISSING_BINDING":

    from varco_conformance.providify_health import assert_no_structural_di_issues

    container.validate_bindings()  # existing scope-leak tier — unchanged
    assert_no_structural_di_issues(container)  # NEW — supplements, never replaces

This module lives at the repo root under ``testkit/`` — never packaged or
published — reached only via each participating package's
``pythonpath = ["../testkit"]`` pytest ini setting, same convention as
every other module in ``varco_conformance`` (see this package's
``__init__.py`` docstring).

Thread safety:  N/A — pure function, no shared state.
Async safety:   N/A — ``container.validate()`` is synchronous.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from providify import IssueKind

if TYPE_CHECKING:
    from providify import DIContainer


def assert_no_structural_di_issues(container: DIContainer) -> None:
    """
    Run ``container.validate(raise_on_error=False)`` and fail the test if
    any STRUCTURAL error is reported — anything other than
    ``IssueKind.MISSING_BINDING``.

    ``MISSING_BINDING`` is tolerated by design (Design §RL-3a): a package
    scanned alone legitimately lacks the application's own bindings
    (``AsyncRepository[User]``, ``AbstractAuthorizer``, app settings). Every
    other error kind (``AMBIGUOUS_BINDING``, ``CIRCULAR_DEPENDENCY``,
    ``SCOPE_LEAK``, ``LIVE_REQUIRED``, ``UNRESOLVED_ANNOTATION``) is a real
    bootstrap defect and must fail loudly.

    Args:
        container: The ``DIContainer`` to validate. Bindings should already
            be registered (via ``scan()``/``install()``/``bind()``/
            ``provide()``) before calling this.

    Raises:
        AssertionError: One or more structural (non-``MISSING_BINDING``)
            errors were reported. The message lists every structural
            issue's ``.message``, one per line.

    Edge cases:
        - A container with only ``MISSING_BINDING`` issues (e.g. a package
          scanned in isolation, missing an app-supplied interface) passes
          silently — this is the whole point of the structural-only filter.
        - Only ``report.errors`` is inspected (``Severity.ERROR``) —
          ``WARNING``-severity issues (``MISSING_BINDING_DEFAULTED``,
          ``MISSING_BINDING_DEFERRED``) never reach ``report.errors`` in the
          first place (see ``IssueKind``'s severity mapping), so this
          function does not need to special-case them.
    """
    report = container.validate(raise_on_error=False)
    structural = [i for i in report.errors if i.kind is not IssueKind.MISSING_BINDING]
    assert not structural, "\n".join(i.message for i in structural)
