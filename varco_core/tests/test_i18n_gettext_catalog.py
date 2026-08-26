"""
Red-mode tests for Plan 011 Phase 2, step 24 — GettextMessageCatalog.

Plan line (step 23): "GettextMessageCatalog(directory, domain='messages',
locales=...): stdlib gettext.translation() per locale, all loaded in
start() ... format_message uses ngettext when params carries an integer
count. No install(), no activate(), no process-global active locale."
"""

from __future__ import annotations

import struct

from varco_core.i18n.gettext_catalog import GettextMessageCatalog


def _write_mo(
    path, translations: dict[str, str], plural: dict[str, list[str]] | None = None
) -> None:
    """
    Build a minimal valid .mo file by hand (no msgfmt/pybabel dependency in
    the test suite, per step 24's "a .mo fixture generated in-test" note).
    """
    keys = list(translations.keys())
    values = list(translations.values())
    if plural:
        for msgid, forms in plural.items():
            keys.append(msgid + "\x00" + msgid + "s")
            values.append("\x00".join(forms))

    offsets = []
    ids = b""
    strs = b""
    for k, v in zip(keys, values):
        k_enc = k.encode("utf-8")
        v_enc = v.encode("utf-8")
        offsets.append((len(ids), len(k_enc), len(strs), len(v_enc)))
        ids += k_enc + b"\x00"
        strs += v_enc + b"\x00"

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    offsets_data = koffsets + voffsets

    output = struct.pack(
        "Iiiiiii",
        0x950412DE,  # magic
        0,  # version
        len(keys),  # number of entries
        7 * 4,  # start of key index
        7 * 4 + len(keys) * 8,  # start of value index
        0,
        0,
    )
    output += struct.pack("i" * len(offsets_data), *offsets_data)
    output += ids
    output += strs
    path.write_bytes(output)


def test_gettext_catalog_loads_mo_file_in_start_and_renders(tmp_path) -> None:
    locale_dir = tmp_path / "fr" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    _write_mo(locale_dir / "messages.mo", {"Hello": "Bonjour"})

    catalog = GettextMessageCatalog(str(tmp_path), domain="messages", locales=["fr"])
    import asyncio

    # DEVIATION: dropped a stray `asyncio.get_event_loop()` call — in a
    # full-suite run (many prior async tests having already touched the
    # event loop policy), this deprecated call raises
    # `RuntimeError: There is no current event loop in thread 'MainThread'`
    # depending on test execution order, even though nothing in this test
    # uses its return value. `asyncio.run()` below is the only line this
    # (sync, deliberately non-`async def`) test actually needs.
    async def _run() -> str | None:
        await catalog.start()
        return catalog.get_message("Hello", "fr")

    result = asyncio.run(_run())
    assert result == "Bonjour"


async def test_gettext_catalog_missing_domain_is_skipped_with_warning(tmp_path, caplog) -> None:
    catalog = GettextMessageCatalog(str(tmp_path), domain="messages", locales=["xx"])
    await catalog.start()  # must not raise
    assert catalog.get_message("Hello", "xx") is None


async def test_gettext_catalog_no_process_global_install_or_activate() -> None:
    # D-1 / Flask-Babel force_locale note: no global mutable "active locale".
    catalog = GettextMessageCatalog.__new__(GettextMessageCatalog)
    assert not hasattr(catalog, "activate")
    assert not hasattr(catalog, "install")
