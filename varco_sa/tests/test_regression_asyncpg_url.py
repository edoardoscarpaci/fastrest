"""
Regression guard for the testcontainers → asyncpg DSN conversion.

User reports: ten varco_sa integration tests ERROR at fixture setup with
``ModuleNotFoundError: No module named 'psycopg2'``.  Correct behaviour is that
every Postgres integration fixture builds an **asyncpg** DSN, because
``create_async_engine`` cannot drive the sync psycopg2 dialect and psycopg2 is
not a dependency of this workspace.

Root cause: ``PostgresContainer.get_connection_url()`` defaults to
``driver="psycopg2"`` and therefore returns ``postgresql+psycopg2://…``.  A
fixture doing ``.replace("postgresql://", "postgresql+asyncpg://")`` matches
nothing on that string and silently yields the psycopg2 URL unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import asyncpg_url

_TESTS_DIR = Path(__file__).resolve().parent


class _FakeContainer:
    """Minimal stand-in reproducing ``PostgresContainer.get_connection_url``."""

    def __init__(self, driver: str | None) -> None:
        self._driver = driver

    def get_connection_url(self, driver: str | None = "psycopg2") -> str:
        # Mirror testcontainers: an explicit ``driver`` overrides the default.
        chosen = self._driver if driver == "psycopg2" else driver
        suffix = "" if chosen is None else f"+{chosen}"
        return f"postgresql{suffix}://user:pw@localhost:5432/test"


@pytest.mark.parametrize("driver", ["psycopg2", "asyncpg", None])
def test_regression_asyncpg_url_always_yields_asyncpg_dialect(
    driver: str | None,
) -> None:
    """The helper must produce an asyncpg DSN whatever the container default is."""
    url = asyncpg_url(_FakeContainer(driver))
    assert url.startswith("postgresql+asyncpg://"), url
    assert "psycopg2" not in url


def test_regression_no_test_module_hand_rolls_the_broken_replace() -> None:
    """
    No test module may hand-roll the DSN conversion.

    The ``.replace("postgresql://", ...)`` form is a silent no-op against the
    ``postgresql+psycopg2://`` string testcontainers actually returns, so the
    conversion must go through the single ``asyncpg_url`` helper.
    """
    broken = re.compile(r"get_connection_url\(\)\s*\.replace\(")
    offenders = [
        path.name
        for path in _TESTS_DIR.glob("test_*.py")
        if broken.search(path.read_text())
    ]
    assert offenders == [], (
        f"these modules convert the DSN by hand instead of using "
        f"tests.conftest.asyncpg_url: {offenders}"
    )
