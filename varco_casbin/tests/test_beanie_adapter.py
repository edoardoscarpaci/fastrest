"""
Unit and integration tests for varco_casbin.beanie_adapter
===========================================================

Unit tests use ``unittest.mock`` to patch Beanie's ``Document`` methods so no
real MongoDB connection is required.

Integration tests (``@pytest.mark.integration``) spin up a real MongoDB via
testcontainers and verify the full round-trip: ``create_table`` → ``add_policy``
→ ``load_policy`` → enforce → ``remove_policy`` → reload → no longer enforced.

Run unit tests only::

    uv run pytest varco_casbin/tests/test_beanie_adapter.py -v

Run everything (requires Docker)::

    uv run pytest varco_casbin/tests/test_beanie_adapter.py -v -m integration
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from varco_casbin.adapter import build_adapter
from varco_casbin.config import CasbinSettings

# ── Helpers ───────────────────────────────────────────────────────────────────


def _settings(**kwargs) -> CasbinSettings:
    """Build a ``CasbinSettings`` for adapter='beanie' with sensible defaults."""
    return CasbinSettings(
        adapter="beanie",
        db_url=kwargs.pop("db_url", "mongodb://localhost:27017"),
        db_name=kwargs.pop("db_name", "test_casbin"),
        **kwargs,
    )


# ── build_adapter unit tests ─────────────────────────────────────────────────


def test_build_adapter_beanie_returns_beanie_adapter() -> None:
    """build_adapter with adapter='beanie' returns a BeanieAdapter instance."""
    from varco_casbin.beanie_adapter import BeanieAdapter

    adapter = build_adapter(_settings())
    assert isinstance(adapter, BeanieAdapter)


def test_build_adapter_beanie_missing_db_url() -> None:
    """adapter='beanie' without db_url raises ValueError with VARCO_CASBIN_DB_URL hint."""
    with pytest.raises(ValueError, match="VARCO_CASBIN_DB_URL"):
        build_adapter(CasbinSettings(adapter="beanie", db_name="myapp"))


def test_build_adapter_beanie_missing_db_name() -> None:
    """adapter='beanie' without db_name raises ValueError with VARCO_CASBIN_DB_NAME hint."""
    with pytest.raises(ValueError, match="VARCO_CASBIN_DB_NAME"):
        build_adapter(CasbinSettings(adapter="beanie", db_url="mongodb://localhost:27017"))


def test_build_adapter_beanie_import_error_gives_install_hint() -> None:
    """
    adapter='beanie' raises ImportError with pip install hint when beanie is missing.

    We temporarily hide the beanie_adapter module from sys.modules to simulate
    the package not being installed.
    """
    # Remove the module from sys.modules so the lazy import fails.
    saved = sys.modules.pop("varco_casbin.beanie_adapter", None)
    try:
        with patch.dict(sys.modules, {"varco_casbin.beanie_adapter": None}):  # type: ignore[dict-item]
            with pytest.raises(ImportError, match="varco-casbin\\[beanie\\]"):
                build_adapter(_settings())
    finally:
        # Restore so other tests can import it.
        if saved is not None:
            sys.modules["varco_casbin.beanie_adapter"] = saved


# ── BeanieAdapter method unit tests ──────────────────────────────────────────


@pytest.fixture
def adapter():
    """A ``BeanieAdapter`` with test credentials."""
    from varco_casbin.beanie_adapter import BeanieAdapter

    return BeanieAdapter(db_url="mongodb://localhost:27017", db_name="test_casbin")


async def test_create_table_calls_init_beanie(adapter) -> None:
    """create_table() calls init_beanie with the correct connection_string and model."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    with patch("varco_casbin.beanie_adapter.init_beanie", new_callable=AsyncMock) as mock_init:
        await adapter.create_table()

    mock_init.assert_awaited_once_with(
        connection_string="mongodb://localhost:27017/test_casbin",
        document_models=[CasbinRuleDocument],
    )


async def test_load_policy_calls_load_policy_line_for_each_doc(adapter) -> None:
    """load_policy() calls load_policy_line for every document in the collection."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    # Two mock rule documents with their __str__ producing canonical CSV lines.
    doc1 = MagicMock(spec=CasbinRuleDocument)
    doc1.__str__ = MagicMock(return_value="p, alice, data1, read")
    doc2 = MagicMock(spec=CasbinRuleDocument)
    doc2.__str__ = MagicMock(return_value="g, alice, admin")

    mock_model = MagicMock()

    # Patch find_all().to_list() to return our fake docs.
    find_all_result = AsyncMock(return_value=[doc1, doc2])
    find_all_mock = MagicMock()
    find_all_mock.to_list = find_all_result

    with (
        patch.object(CasbinRuleDocument, "find_all", return_value=find_all_mock),
        patch("varco_casbin.beanie_adapter.persist.load_policy_line") as mock_lpl,
    ):
        await adapter.load_policy(mock_model)

    assert mock_lpl.call_count == 2
    mock_lpl.assert_any_call("p, alice, data1, read", mock_model)
    mock_lpl.assert_any_call("g, alice, admin", mock_model)


async def test_save_policy_deletes_all_then_inserts(adapter) -> None:
    """save_policy() wipes existing rules then bulk-inserts new ones from the model."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    # Build a mock Casbin model with one policy section containing one rule.
    mock_assertion = MagicMock()
    mock_assertion.policy = [["alice", "data1", "read"]]
    mock_model = MagicMock()
    mock_model.model = {"p": {"p": mock_assertion}}

    mock_delete = AsyncMock()
    mock_find_result = MagicMock()
    mock_find_result.delete = mock_delete

    with (
        patch.object(CasbinRuleDocument, "find_all", return_value=mock_find_result),
        patch.object(CasbinRuleDocument, "insert_many", new_callable=AsyncMock) as mock_insert,
    ):
        result = await adapter.save_policy(mock_model)

    # Must have deleted and then inserted.
    mock_delete.assert_awaited_once()
    mock_insert.assert_awaited_once()

    # The inserted docs list must contain one document with the expected fields.
    inserted_docs = mock_insert.call_args[0][0]
    assert len(inserted_docs) == 1
    doc = inserted_docs[0]
    assert doc.ptype == "p"
    assert doc.v0 == "alice"
    assert doc.v1 == "data1"
    assert doc.v2 == "read"

    # Must return True (Casbin contract).
    assert result is True


async def test_add_policy_inserts_one_document(adapter) -> None:
    """add_policy() inserts a single CasbinRuleDocument with the correct fields."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    with patch.object(CasbinRuleDocument, "insert", new_callable=AsyncMock) as mock_insert:
        await adapter.add_policy("p", "p", ["alice", "data1", "read"])

    mock_insert.assert_awaited_once()


async def test_remove_policy_deletes_matching_document(adapter) -> None:
    """remove_policy() finds and deletes the matching document."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    mock_doc = AsyncMock(spec=CasbinRuleDocument)
    mock_doc.delete = AsyncMock()

    with patch.object(
        CasbinRuleDocument, "find_one", new_callable=AsyncMock, return_value=mock_doc
    ):
        await adapter.remove_policy("p", "p", ["alice", "data1", "read"])

    mock_doc.delete.assert_awaited_once()


async def test_remove_policy_noop_when_not_found(adapter) -> None:
    """remove_policy() is a no-op when no matching document exists."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    with patch.object(CasbinRuleDocument, "find_one", new_callable=AsyncMock, return_value=None):
        # Should not raise.
        await adapter.remove_policy("p", "p", ["alice", "data1", "read"])


async def test_remove_filtered_policy_empty_field_values_is_noop(adapter) -> None:
    """remove_filtered_policy() with no field_values is a no-op (guards full-table wipe)."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    # find() should never be called — guard returns early.
    with patch.object(CasbinRuleDocument, "find") as mock_find:
        await adapter.remove_filtered_policy("p", "p", 0)

    mock_find.assert_not_called()


async def test_remove_filtered_policy_field_index_0(adapter) -> None:
    """remove_filtered_policy(field_index=0, 'alice') deletes rules where v0='alice'."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    mock_delete = AsyncMock()
    mock_find_result = MagicMock()
    mock_find_result.delete = mock_delete

    with patch.object(CasbinRuleDocument, "find", return_value=mock_find_result) as mock_find:
        await adapter.remove_filtered_policy("p", "p", 0, "alice")

    # Filter must include ptype and v0.
    mock_find.assert_called_once_with({"ptype": "p", "v0": "alice"})
    mock_delete.assert_awaited_once()


async def test_remove_filtered_policy_field_index_1(adapter) -> None:
    """remove_filtered_policy(field_index=1, 'data1', 'read') filters v1 and v2."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    mock_delete = AsyncMock()
    mock_find_result = MagicMock()
    mock_find_result.delete = mock_delete

    with patch.object(CasbinRuleDocument, "find", return_value=mock_find_result) as mock_find:
        await adapter.remove_filtered_policy("p", "p", 1, "data1", "read")

    mock_find.assert_called_once_with({"ptype": "p", "v1": "data1", "v2": "read"})
    mock_delete.assert_awaited_once()


async def test_remove_filtered_policy_empty_string_is_wildcard(adapter) -> None:
    """An empty string '' in field_values is skipped (acts as a wildcard slot)."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    mock_delete = AsyncMock()
    mock_find_result = MagicMock()
    mock_find_result.delete = mock_delete

    with patch.object(CasbinRuleDocument, "find", return_value=mock_find_result) as mock_find:
        # v0="" is skipped; v1="data1" is included.
        await adapter.remove_filtered_policy("p", "p", 0, "", "data1")

    # v0="" is a wildcard → not in filter.
    mock_find.assert_called_once_with({"ptype": "p", "v1": "data1"})
    mock_delete.assert_awaited_once()


# ── CasbinRuleDocument.__str__ unit tests ─────────────────────────────────────
#
# NOTE: We use ``model_construct`` to build test documents — Beanie v2's
# ``Document.__init__`` calls ``get_pymongo_collection()`` which raises
# ``CollectionWasNotInitialized`` outside a live MongoDB session.
# ``model_construct`` bypasses both pydantic validation and the collection
# check, which is exactly what ``_rule_to_doc()`` does in the adapter.


def test_rule_document_str_produces_csv_line() -> None:
    """CasbinRuleDocument.__str__() returns the canonical Casbin CSV line."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    doc = CasbinRuleDocument.model_construct(
        ptype="p", v0="alice", v1="data1", v2="read", v3="", v4="", v5=""
    )
    assert str(doc) == "p, alice, data1, read"


def test_rule_document_str_stops_at_first_empty_field() -> None:
    """__str__() stops at the first empty v* field — variable-arity rules."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    doc = CasbinRuleDocument.model_construct(
        ptype="g", v0="alice", v1="admin", v2="", v3="", v4="", v5=""
    )
    # v2 is empty ("") — stops here.
    assert str(doc) == "g, alice, admin"


def test_rule_document_str_ptype_only_when_all_fields_empty() -> None:
    """__str__() returns just 'p' when all v* fields are empty."""
    from varco_casbin.beanie_adapter import CasbinRuleDocument

    doc = CasbinRuleDocument.model_construct(ptype="p", v0="", v1="", v2="", v3="", v4="", v5="")
    assert str(doc) == "p"


# ── Integration tests ─────────────────────────────────────────────────────────


# mongo_url (module-scoped, local) was replaced by the session-scoped
# mongo_url fixture in tests/conftest.py (Plan 012 / RT1, Step 6/7) — that
# fixture yields the bare connection URL with no ``authSource``, so this
# file appends it itself via ``mongo_url_with_auth`` below.
#
# testcontainers returns ``mongodb://test:test@host:port``.  The ``test``
# user is created against the ``admin`` database (superuser), so any
# connection to a non-``test`` database must include ``?authSource=admin``
# to authenticate correctly. ``BeanieAdapter.create_table()`` appends
# ``/{db_name}?authSource=admin`` to this base URL.


@pytest.fixture
def mongo_url_with_auth(mongo_url: str) -> str:
    return f"{mongo_url}?authSource=admin"


@pytest.mark.integration
async def test_full_round_trip(mongo_url_with_auth: str) -> None:
    """
    Integration: create_table → add_policy → load_policy → enforce →
    remove_policy → reload → no longer enforced.
    """
    from varco_casbin.config import CasbinSettings
    from varco_casbin.engine import CasbinPolicyEngine
    from varco_core.auth.policy import EnforcementRequest as ER

    settings = CasbinSettings(
        model_preset="rbac",
        adapter="beanie",
        db_url=mongo_url_with_auth,
        db_name="test_casbin_roundtrip",
    )

    # Write phase — add a role and a policy rule.
    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_role_for_user("alice", "admin")
        await writer.add_policy("admin", "*", "*")

        # Enforce in the same engine instance.
        assert await writer.enforce(ER("alice", "posts", "read")) is True
        assert await writer.enforce(ER("bob", "posts", "read")) is False

    # Read phase — fresh engine backed by the same MongoDB should see the rules.
    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("alice", "posts", "read")) is True
        assert ("admin", "*", "*") in await reader.list_policies()

    # Remove phase — delete the role assignment, reload, and verify denial.
    async with CasbinPolicyEngine(settings) as remover:
        await remover.remove_policy("admin", "*", "*")

    async with CasbinPolicyEngine(settings) as verify:
        # Without the policy rule alice can no longer act.
        assert await verify.enforce(ER("alice", "posts", "read")) is False


@pytest.mark.integration
async def test_persistence_across_engine_restart(mongo_url_with_auth: str) -> None:
    """
    Integration: rules written through one engine survive a full restart.
    """
    from varco_casbin.config import CasbinSettings
    from varco_casbin.engine import CasbinPolicyEngine
    from varco_core.auth.policy import EnforcementRequest as ER

    settings = CasbinSettings(
        model_preset="rbac",
        adapter="beanie",
        db_url=mongo_url_with_auth,
        # Separate DB from the round-trip test to avoid interference.
        db_name="test_casbin_restart",
    )

    # First engine: write policies.
    async with CasbinPolicyEngine(settings) as e1:
        await e1.add_role_for_user("charlie", "editor")
        await e1.add_policy("editor", "articles", "write")

    # Second engine (simulated restart): must load the persisted rules.
    async with CasbinPolicyEngine(settings) as e2:
        assert await e2.enforce(ER("charlie", "articles", "write")) is True
        assert await e2.enforce(ER("charlie", "articles", "delete")) is False
