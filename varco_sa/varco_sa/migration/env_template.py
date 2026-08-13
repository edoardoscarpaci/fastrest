"""
varco_sa.migration.env_template
================================
Helpers an app's ``alembic/env.py`` imports directly: ``include_object``
(filters framework-owned tables out of app-side autogenerate) and
``configure_kwargs()`` (the recommended ``context.configure(...)`` kwargs).

DESIGN: two standalone functions, not a base ``env.py`` class
    ✅ Drop-in for an existing ``env.py`` — one import, one line wired into
       ``context.configure(include_object=include_object, ...)``.
    ✅ ``configure_kwargs()`` centralises ``transaction_per_migration=True``
       and ``compare_type=True`` (Plan 006 D1/design) so every varco-aware
       ``env.py`` gets the same defaults without copy-pasting them.
    ❌ An app with more elaborate ``include_object`` logic must compose it
       manually (call ``include_object`` from within their own callback).

Thread safety:  ✅ Pure functions.
Async safety:   ✅ Synchronous — Alembic's env.py runs outside the event loop.
"""

from __future__ import annotations

from typing import Any


def include_object(
    object_: Any,
    name: str,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """
    Alembic ``include_object`` callback — excludes framework-owned tables.

    Returns ``False`` for any table whose name is in
    ``varco_sa.metadata.framework_table_names()``, so an app's own
    ``autogenerate`` never tries to re-declare a table already owned by the
    packaged ``varco`` Alembic branch (Plan 006 D3).

    Args:
        object_:    The schema object being considered (table, column, …).
        name:       The object's name.
        type_:      Alembic's object type string (``"table"``, ``"column"``, …).
        reflected:  Whether the object was reflected from the live DB.
        compare_to: The corresponding object in ``target_metadata``, or ``None``.

    Returns:
        ``False`` to exclude a framework table from autogenerate; ``True``
        otherwise (Alembic's default behaviour).
    """
    from varco_sa.metadata import framework_table_names

    return not (type_ == "table" and name in framework_table_names())


def configure_kwargs(**overrides: Any) -> dict[str, Any]:
    """
    Return the recommended ``context.configure(...)`` kwargs.

    Args:
        **overrides: Any kwarg here overrides the recommended default —
                     useful for an app that wants ``compare_type=False`` for
                     a specific migration environment.

    Returns:
        A dict with ``transaction_per_migration=True`` and
        ``compare_type=True`` (Plan 006's chosen defaults — see D4), merged
        with ``overrides``.
    """
    defaults: dict[str, Any] = {
        "transaction_per_migration": True,
        "compare_type": True,
    }
    defaults.update(overrides)
    return defaults


__all__ = ["configure_kwargs", "include_object"]
