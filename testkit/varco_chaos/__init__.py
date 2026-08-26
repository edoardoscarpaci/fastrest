"""
varco_chaos
===========

Shared, test-only chaos-engineering helpers (Plan 018 / RT7b).

Like its sibling ``testkit/varco_conformance``, this package lives at the
repo root under ``testkit/`` — it is **never** packaged or published, and is
reached only via each participating package's ``pythonpath = ["../testkit"]``
pytest ini setting (same wiring as Plan 012 / RT6).

Contract for every module in this package:

- **Helpers only, never test classes.** Chaos *scenarios* (what container to
  break, what to assert) belong in each package's own
  ``tests/test_*_chaos.py`` module (§RT7-home — chaos tests live in the
  package that owns the thing that fails). This package supplies only the
  mechanism: how to safely restart/pause a container, and how to simulate a
  worker crash for lease-fencing tests.
- This is the **only** place in the repo allowed to call
  ``DockerContainer.get_wrapped_container()`` — every chaos test goes through
  ``ChaosContainer`` instead of reaching for the raw docker-py handle itself,
  so the "never ``.stop()``+``.start()``, always docker-py ``restart()``"
  rule (research 002 §1 — port/ID survivorship) has exactly one place to be
  enforced and documented.
"""

from __future__ import annotations
