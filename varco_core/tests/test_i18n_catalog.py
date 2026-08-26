"""
Red-mode tests for Plan 011 Phase 2, step 22 — varco_core.i18n.catalog.
MessageCatalog ABC + NullMessageCatalog + DictMessageCatalog.

Plan line (step 21): "MessageCatalog ABC (abstract get_message; concrete
format_message, available_locales, async start/stop), NullMessageCatalog
(the default), DictMessageCatalog(mapping)".
"""

from __future__ import annotations

import pytest
from varco_core.i18n.catalog import (
    DictMessageCatalog,
    MessageCatalog,
    NullMessageCatalog,
)


def test_message_catalog_abc_requires_only_get_message() -> None:
    # A minimal subclass implementing only get_message() must be instantiable.
    class _Minimal(MessageCatalog):
        def get_message(self, key: str, locale: str) -> str | None:
            return None

    inst = _Minimal()
    assert isinstance(inst, MessageCatalog)


def test_message_catalog_abc_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        MessageCatalog()  # type: ignore[abstract]


def test_null_message_catalog_get_message_returns_none() -> None:
    catalog = NullMessageCatalog()
    assert catalog.get_message("varco.error.not_found", "fr") is None


def test_null_message_catalog_available_locales_is_empty_frozenset() -> None:
    catalog = NullMessageCatalog()
    assert catalog.available_locales() == frozenset()


def test_dict_message_catalog_get_message_looks_up_locale_and_key() -> None:
    catalog = DictMessageCatalog({"fr": {"greeting": "Bonjour"}})
    assert catalog.get_message("greeting", "fr") == "Bonjour"
    assert catalog.get_message("greeting", "de") is None
    assert catalog.get_message("missing", "fr") is None


def test_format_message_default_interpolates_params() -> None:
    catalog = DictMessageCatalog({"en": {"greeting": "Hello {name}"}})
    rendered = catalog.format_message("greeting", "en", {"name": "Ada"})
    assert rendered == "Hello Ada"


def test_format_message_missing_placeholder_renders_literal_brace_never_raises() -> None:
    # Edge cases table: "a missing interpolation param renders the literal
    # {name}; no KeyError inside an exception handler."
    catalog = DictMessageCatalog({"en": {"greeting": "Hello {name}"}})
    rendered = catalog.format_message("greeting", "en", {})
    assert rendered == "Hello {name}"


def test_format_message_unknown_key_returns_none_not_empty_string() -> None:
    catalog = DictMessageCatalog({"en": {}})
    assert catalog.format_message("unknown.key", "en") is None


async def test_start_and_stop_are_awaitable_no_ops_on_dict_catalog() -> None:
    catalog = DictMessageCatalog({"en": {}})
    await catalog.start()
    await catalog.stop()
