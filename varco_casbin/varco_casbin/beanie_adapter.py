"""
varco_casbin.beanie_adapter
===========================
Casbin async adapter backed by MongoDB via Beanie.

``BeanieAdapter`` implements ``casbin.persist.adapters.asyncio.AsyncAdapter``
and stores each Casbin policy rule as a ``CasbinRuleDocument`` in the
``casbin_rule`` MongoDB collection.

``CasbinPolicyEngine.start()`` calls ``create_table()`` on the adapter (via
the ``getattr`` guard in ``engine.py``) — this is where ``init_beanie()`` is
called so that:

  1. Beanie registers ``CasbinRuleDocument`` against the live database.
  2. MongoDB creates the collection and compound index if they don't yet exist.

DESIGN: ``init_beanie()`` called inside ``create_table()``, not at module level
    ✅ Calling ``init_beanie()`` at module level would require a live MongoDB
       connection at *import time*, making ``varco_casbin`` un-importable in
       environments that don't have MongoDB (e.g., CI for other packages).
    ✅ The engine calls ``create_table()`` inside its own ``@PostConstruct`` —
       at that point a running event loop is guaranteed and the caller has
       already provided credentials via ``CasbinSettings``.
    ✅ Idempotent: Beanie's ``init_beanie`` can be called multiple times and
       MongoDB's ``create_index`` is a no-op when the index already exists.
    ❌ ``CasbinRuleDocument`` cannot be used before ``create_table()`` is
       called — Beanie will raise ``RuntimeError``.  This is acceptable because
       no adapter method is called before ``create_table()`` in the engine
       lifecycle.

DESIGN: ``connection_string`` + ``db_name`` over raw ``AsyncIOMotorClient``
    ✅ ``init_beanie(connection_string=..., ...)`` delegates client creation to
       Beanie/motor — no direct motor dependency in this module.
    ✅ Avoids the ``asyncio.Lock`` problem: a motor client created at
       ``__init__`` time would be constructed *before* the event loop starts.
    ✅ Simpler: no client bookkeeping or ``close()`` call needed.
    ❌ Less control over client pool settings.  This is acceptable for policy
       storage which has low throughput.  Users needing tuning can subclass.

Thread safety:  ✅ Stateless after ``create_table()`` — all state lives in MongoDB.
Async safety:   ✅ All methods are ``async def``; no shared mutable Python state.

📚 Docs
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — class definition, Settings.name, index configuration.
- 🔍 https://beanie-odm.dev/api-documentation/document/
  Beanie Document CRUD methods — find, insert, delete.
- 🔍 https://casbin.org/docs/adapters
  Casbin adapters — the interface contract BeanieAdapter implements.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from beanie import Document, init_beanie
from casbin import persist
from casbin.persist.adapters.asyncio import AsyncAdapter
from pydantic import Field
from pymongo import ASCENDING, IndexModel

_logger = logging.getLogger(__name__)

# ── Casbin rule document ──────────────────────────────────────────────────────


class CasbinRuleDocument(Document):
    """
    Beanie document storing one Casbin policy rule.

    Maps to the ``casbin_rule`` MongoDB collection.  Each document represents
    one row in the policy file — a ``ptype`` (``"p"`` or ``"g"``) plus up to
    six positional fields (``v0`` … ``v5``).

    Register this document in your ``init_beanie()`` call *or* rely on
    ``BeanieAdapter.create_table()`` to do it automatically.

    DESIGN: UUID surrogate PK instead of MongoDB ObjectId
        ✅ Consistent with the rest of the varco_beanie layer (e.g.
           ``DeduplicationDocument``) — UUID PKs are Beanie-idiomatic varco style.
        ✅ Avoids ObjectId serialisation complexity in tests.
        ❌ Slightly more space than a 12-byte ObjectId.  Negligible for policy
           tables that typically hold hundreds of rules.

    DESIGN: empty-string defaults for v0…v5
        Casbin rules have variable arity.  Using ``""`` as the absent sentinel
        (rather than ``None``) mirrors the SQLAlchemy adapter's pattern and lets
        the ``__str__`` method build the canonical ``"p, alice, data1, read"``
        string without ``None`` check branches.

    Attributes:
        id:    UUIDv4 surrogate key — internal Beanie document identity.
        ptype: Rule type — ``"p"`` for policy rules, ``"g"`` for role assignments.
        v0:    First positional field (subject / user).
        v1:    Second positional field (object / resource).
        v2:    Third positional field (action).
        v3–v5: Optional extra fields for ABAC / domain rules.

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie operations are ``async def``.

    Edge cases:
        - The compound index ``(ptype, v0, v1)`` is a *non-unique* performance
          index — Casbin allows duplicate policy rules (it deduplicates in
          memory).
        - Empty fields (``v3``/``v4``/``v5``) are stored as ``""`` in MongoDB,
          not ``null``.  ``__str__`` stops at the first empty field.
    """

    # Surrogate PK — keeps Beanie identity separate from the policy-rule key.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]

    # Rule type ("p" = policy rule, "g" = role assignment).
    ptype: str

    # Positional fields match the columns in Casbin's CSV policy format.
    # Defaulting to "" (not None) mirrors the SQLAlchemy adapter — allows a
    # single __str__ implementation that stops at the first empty field.
    v0: str = ""
    v1: str = ""
    v2: str = ""
    v3: str = ""
    v4: str = ""
    v5: str = ""

    class Settings:
        """Beanie collection and index configuration."""

        # Collection name — matches the SQL adapter's table name for
        # consistency when migrating from SQLAlchemy to Beanie.
        name = "casbin_rule"

        indexes = [
            # Non-unique compound index on (ptype, v0, v1) — the most common
            # query pattern is "list all rules of type p for subject alice".
            # Non-unique: Casbin allows duplicate rules (it deduplicates in-RAM).
            IndexModel(
                [("ptype", ASCENDING), ("v0", ASCENDING), ("v1", ASCENDING)],
                name="casbin_ptype_v0_v1",
            ),
        ]

    def __str__(self) -> str:
        """
        Return the canonical Casbin CSV line for this rule.

        Builds ``"p, alice, data1, read"`` by joining ``ptype`` and the
        non-empty ``v*`` fields with ``", "``.  This is the format that
        ``casbin.persist.load_policy_line`` expects.

        Returns:
            A comma-separated string like ``"p, alice, data1, read"`` —
            ready for ``load_policy_line``.

        Edge cases:
            - Stops at the first empty ``v*`` field — rules with gaps
              (empty v1 but non-empty v2) are not supported by Casbin itself,
              so this is not a practical limitation.
        """
        parts = [self.ptype]
        for v in (self.v0, self.v1, self.v2, self.v3, self.v4, self.v5):
            # Stop at the first empty field — variable-arity rule handling.
            if not v:
                break
            parts.append(v)
        return ", ".join(parts)

    def __repr__(self) -> str:
        return f"CasbinRuleDocument(ptype={self.ptype!r}, rule={str(self)!r})"


# ── BeanieAdapter ─────────────────────────────────────────────────────────────

# Maps the positional slot index (0 = v0, 1 = v1 …) to the Beanie field name.
# Used in ``remove_filtered_policy`` to build the MongoDB filter dict.
_V_FIELDS: tuple[str, ...] = ("v0", "v1", "v2", "v3", "v4", "v5")


class BeanieAdapter(AsyncAdapter):
    """
    Casbin async adapter backed by MongoDB via Beanie.

    Stores each policy rule as a ``CasbinRuleDocument`` in the ``casbin_rule``
    collection.  Designed to be used with ``CasbinPolicyEngine`` — call
    ``create_table()`` (or let the engine call it via the ``getattr`` hook in
    ``CasbinPolicyEngine.start()``) before any policy operation.

    Usage::

        from varco_casbin.beanie_adapter import BeanieAdapter
        from varco_casbin.engine import CasbinPolicyEngine
        from varco_casbin.config import CasbinSettings

        # Via CasbinSettings (recommended — engine calls create_table() for you):
        settings = CasbinSettings(
            model_preset="rbac",
            adapter="beanie",
            db_url="mongodb://localhost:27017",
            db_name="myapp",
        )
        async with CasbinPolicyEngine(settings) as engine:
            await engine.add_policy("alice", "data1", "read")

        # Standalone:
        adapter = BeanieAdapter(db_url="mongodb://localhost:27017", db_name="myapp")
        await adapter.create_table()

    Args:
        db_url:  MongoDB connection string (e.g. ``"mongodb://localhost:27017"``).
        db_name: Name of the MongoDB database to use for the ``casbin_rule``
                 collection.

    Thread safety:  ✅ Stateless after ``create_table()`` — all persistent state
                        lives in MongoDB.
    Async safety:   ✅ All methods are ``async def``.  No shared mutable Python
                        state between concurrent callers.

    Edge cases:
        - Must call ``create_table()`` (or start via ``CasbinPolicyEngine``)
          before any policy operation.  Beanie raises ``RuntimeError`` if
          ``CasbinRuleDocument`` is not initialised.
        - ``save_policy`` is a full wipe-then-insert — concurrent writers will
          lose data.  Use database-level locking or ensure only one writer runs
          ``save_policy`` at a time.
        - ``remove_policy`` and ``remove_filtered_policy`` silently no-op if no
          matching document exists (idempotent delete).
        - ``update_policy`` replaces the first matching rule — if multiple
          identical rules exist, only the first (by insertion order) is updated.
    """

    def __init__(self, *, db_url: str, db_name: str) -> None:
        """
        Args:
            db_url:  MongoDB connection string.
            db_name: Target database name.
        """
        # Store credentials for deferred use in create_table().
        # Do NOT create a motor client here — asyncio.Lock / event-loop safety:
        # motor clients must be created inside a running event loop.
        self._db_url = db_url
        self._db_name = db_name

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def create_table(self) -> None:
        """
        Initialise Beanie with ``CasbinRuleDocument`` and create the index.

        Called by ``CasbinPolicyEngine.start()`` via the ``getattr`` guard::

            create_table = getattr(self._adapter, "create_table", None)
            if callable(create_table):
                await create_table()

        Idempotent — calling it multiple times is safe: Beanie's
        ``init_beanie`` is a no-op when already initialised, and MongoDB's
        ``create_index`` skips existing indexes.

        Async safety: ✅ Safe to call once per engine lifecycle.  Concurrent
                          calls are theoretically safe (MongoDB index creation
                          is idempotent) but callers should avoid racing.

        Raises:
            pymongo.errors.ServerSelectionTimeoutError: MongoDB unreachable.
            RuntimeError: Beanie internal initialisation error.

        Edge cases:
            - If ``init_beanie()`` was already called with ``CasbinRuleDocument``
              in scope, this method is effectively a no-op.
        """
        # DESIGN: use init_beanie(connection_string=...) rather than creating
        # a raw motor client.  This delegates the client/pool creation to
        # Beanie/motor, avoids the asyncio event-loop problem (motor clients
        # created before the loop starts raise RuntimeError), and keeps this
        # adapter free of a direct motor import.
        _logger.debug(
            "BeanieAdapter.create_table: calling init_beanie " "db_url=%s db_name=%s",
            self._db_url,
            self._db_name,
        )
        # Build the connection string by inserting the db_name as the URL path.
        # We must insert it BEFORE any query parameters so the resulting URL
        # has the form:  mongodb://user:pass@host:port/db_name?authSource=admin
        # rather than:   mongodb://user:pass@host:port?authSource=admin/db_name
        #
        # Strategy: split on "?" to separate the URL base from any query string,
        # append the db_name to the base, then re-attach the query string.
        if "?" in self._db_url:
            base_url, query_string = self._db_url.split("?", 1)
            connection_string = f"{base_url}/{self._db_name}?{query_string}"
        else:
            connection_string = f"{self._db_url}/{self._db_name}"

        await init_beanie(
            # connection_string lets Beanie own the motor client lifecycle.
            connection_string=connection_string,
            document_models=[CasbinRuleDocument],
        )

    # ── AsyncAdapter interface ─────────────────────────────────────────────────

    async def load_policy(self, model: Any) -> None:
        """
        Load all rules from MongoDB into the Casbin model.

        Iterates every ``CasbinRuleDocument`` and calls
        ``casbin.persist.load_policy_line(str(doc), model)`` to populate the
        in-memory model.  ``str(doc)`` produces the canonical CSV line
        (e.g. ``"p, alice, data1, read"``) that Casbin's parser expects.

        Args:
            model: The Casbin model object to populate.

        Async safety: ✅ Single ``find_all()`` await; no shared Python state.

        Edge cases:
            - An empty collection is valid — the model is left unpopulated.
            - Documents with all-empty ``v*`` fields produce a line like
              ``"p"`` which Casbin ignores (no tokens after the ptype).
        """
        docs = await CasbinRuleDocument.find_all().to_list()
        for doc in docs:
            # load_policy_line parses "p, alice, data1, read" → model.model[p][p]
            persist.load_policy_line(str(doc), model)

    async def save_policy(self, model: Any) -> bool:
        """
        Replace all stored rules with the current in-memory model state.

        Deletes all existing ``CasbinRuleDocument`` records, then bulk-inserts
        documents for every rule in every policy section.  This is a full
        snapshot write — it is safe only when one writer is active at a time.

        Args:
            model: The Casbin model whose policy sections are persisted.

        Returns:
            ``True`` always — Casbin expects a bool return.

        Async safety: ✅ delete-then-insert is not atomic in MongoDB without
                          a transaction.  Concurrent ``save_policy`` calls or
                          concurrent reads during a ``save_policy`` may see a
                          transient empty collection.  Use an application-level
                          mutex if strict consistency is required.

        Edge cases:
            - An empty model saves zero documents (collection is wiped).
        """
        # Delete all existing rules before writing the fresh snapshot.
        await CasbinRuleDocument.find_all().delete()

        # Iterate all policy sections ("p", "g") and their rule types.
        docs: list[CasbinRuleDocument] = []
        for sec in model.model.values():
            for ptype, assertion in sec.items():
                for rule in assertion.policy:
                    docs.append(_rule_to_doc(ptype, rule))

        if docs:
            # Bulk insert is more efficient than individual inserts for snapshots.
            await CasbinRuleDocument.insert_many(docs)

        return True

    async def add_policy(self, sec: str, ptype: str, rule: list[str]) -> None:
        """
        Insert one policy rule into MongoDB.

        Args:
            sec:   Policy section (``"p"`` or ``"g"``).  Unused directly —
                   ``ptype`` is the stored discriminator.
            ptype: Rule type (e.g. ``"p"``, ``"g"``).
            rule:  List of field values (e.g. ``["alice", "data1", "read"]``).

        Async safety: ✅ Single ``insert()`` await.

        Edge cases:
            - Casbin allows duplicate rules — no uniqueness enforcement here.
        """
        # sec is the section key (unused — ptype is stored in the document).
        doc = _rule_to_doc(ptype, rule)
        await doc.insert()

    async def remove_policy(self, sec: str, ptype: str, rule: list[str]) -> None:
        """
        Delete the first document matching ``(ptype, rule)`` from MongoDB.

        Args:
            sec:   Policy section (unused — kept for interface compatibility).
            ptype: Rule type to match.
            rule:  Exact field values to match.

        Async safety: ✅ Single find-and-delete; idempotent if no match.

        Edge cases:
            - No matching document: silently no-ops (idempotent).
            - Multiple matching documents: only the first is deleted (consistent
              with Casbin's behaviour — duplicates are unusual but allowed).
        """
        # Build a filter matching ptype and the non-empty rule fields.
        filt = _rule_filter(ptype, rule)
        doc = await CasbinRuleDocument.find_one(filt)
        if doc is not None:
            await doc.delete()

    async def remove_filtered_policy(
        self,
        sec: str,
        ptype: str,
        field_index: int,
        *field_values: str,
    ) -> None:
        """
        Delete all rules matching a partial field pattern.

        ``field_index`` is the starting position; ``field_values`` are the
        values to match at positions ``field_index, field_index+1, …``.

        For example, to remove all rules where ``v0 == "alice"``::

            await adapter.remove_filtered_policy("p", "p", 0, "alice")

        To remove rules where ``v1 == "data1"`` and ``v2 == "read"``::

            await adapter.remove_filtered_policy("p", "p", 1, "data1", "read")

        Args:
            sec:          Policy section (unused — interface compatibility).
            ptype:        Rule type to match.
            field_index:  Starting ``v*`` slot (0 = v0, 1 = v1, …).
            *field_values: Values to match at slots starting from ``field_index``.
                           Empty string ``""`` matches any value at that slot.

        Async safety: ✅ Single ``find().delete()`` query.

        Edge cases:
            - An empty ``field_values`` deletes nothing (guards against
              accidental full-table wipe).
            - Empty string ``""`` in ``field_values`` is treated as a wildcard
              (that slot is not added to the filter).
            - ``field_index + len(field_values) > 6`` is silently truncated to
              the valid field range (v0 … v5).
        """
        # Guard: empty field_values → nothing to filter → no-op.
        if not field_values:
            return

        filt: dict[str, Any] = {"ptype": ptype}

        for offset, value in enumerate(field_values):
            slot = field_index + offset
            # Respect the v0–v5 range; extra slots are silently ignored.
            if slot >= len(_V_FIELDS):
                break
            # Empty string "" is treated as "match anything" — skip the slot.
            if value:
                filt[_V_FIELDS[slot]] = value

        await CasbinRuleDocument.find(filt).delete()

    async def update_policy(
        self,
        sec: str,
        ptype: str,
        old_rule: list[str],
        new_rule: list[str],
    ) -> None:
        """
        Replace one rule document in MongoDB.

        Finds the first document matching ``(ptype, old_rule)`` and replaces its
        ``v*`` fields with ``new_rule``.  Called by Casbin's auto-save on
        in-place rule edits.

        Args:
            sec:      Policy section (unused — interface compatibility).
            ptype:    Rule type.
            old_rule: Current field values to locate the document.
            new_rule: Replacement field values.

        Async safety: ✅ Single find-then-set; not atomic.  Concurrent updates
                          to the same rule may race — acceptable for low-write
                          policy management workflows.

        Edge cases:
            - No matching document: silently no-ops.
            - Multiple matching documents: only the first is updated.
        """
        filt = _rule_filter(ptype, old_rule)
        doc = await CasbinRuleDocument.find_one(filt)
        if doc is None:
            return

        # Apply new field values — zero out any slots the new rule doesn't fill.
        new_fields = _rule_fields(new_rule)
        for field_name in _V_FIELDS:
            # setattr updates the Beanie model field in-place.
            setattr(doc, field_name, new_fields.get(field_name, ""))

        await doc.save()

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"BeanieAdapter(" f"db_url={self._db_url!r}, " f"db_name={self._db_name!r})"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rule_fields(rule: list[str]) -> dict[str, str]:
    """
    Map a positional rule list to a ``{v0: …, v1: …}`` dict.

    Only slots that exist in ``rule`` are included; trailing empty slots
    are left at their default ``""`` value in the document.

    Args:
        rule: Positional field values (e.g. ``["alice", "data1", "read"]``).

    Returns:
        A dict mapping ``v0``…``v{n}`` to the corresponding rule values.

    Edge cases:
        - An empty ``rule`` list returns an empty dict.
        - Extra elements beyond ``v5`` are silently truncated.
    """
    return {_V_FIELDS[i]: v for i, v in enumerate(rule) if i < len(_V_FIELDS)}


def _rule_to_doc(ptype: str, rule: list[str]) -> CasbinRuleDocument:
    """
    Build a ``CasbinRuleDocument`` from ``ptype`` and a positional rule list.

    DESIGN: uses ``model_construct`` instead of ``CasbinRuleDocument(...)``
        Beanie v2's ``Document.__init__`` calls ``get_pymongo_collection()``,
        which raises ``CollectionWasNotInitialized`` if ``init_beanie()`` has
        not been called yet.  ``model_construct`` bypasses both pydantic
        validation and the Beanie collection lookup — safe here because:
          ✅ All field values come from Casbin internals (already validated).
          ✅ The adapter only builds docs to immediately ``insert()`` or pass
             to ``insert_many()`` — no downstream code relies on pydantic
             validators running.
          ❌ Validators won't catch type errors if Casbin passes non-strings.
             Acceptable — Casbin's own type system enforces string rules.

    Args:
        ptype: Rule type (``"p"`` or ``"g"``).
        rule:  Positional field values.

    Returns:
        An unsaved ``CasbinRuleDocument`` ready for ``.insert()``.
    """
    # model_construct skips __init__ / Beanie collection check — see DESIGN above.
    fields = _rule_fields(rule)
    # Supply default empty strings for missing v* slots so __str__ works correctly.
    for f in _V_FIELDS:
        fields.setdefault(f, "")
    return CasbinRuleDocument.model_construct(ptype=ptype, id=uuid4(), **fields)


def _rule_filter(ptype: str, rule: list[str]) -> dict[str, Any]:
    """
    Build a MongoDB filter dict matching ``(ptype, rule)`` exactly.

    Includes only the non-empty rule slots — trailing empty slots are not
    added to the filter so they match documents stored with ``""`` defaults.

    Args:
        ptype: Rule type.
        rule:  Positional field values.

    Returns:
        A MongoDB filter dict, e.g. ``{"ptype": "p", "v0": "alice", "v1": "data1", "v2": "read"}``.
    """
    filt: dict[str, Any] = {"ptype": ptype}
    for i, v in enumerate(rule):
        if i >= len(_V_FIELDS):
            break
        # Include this slot even if empty — the caller gave us the full rule.
        filt[_V_FIELDS[i]] = v
    return filt


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "BeanieAdapter",
    "CasbinRuleDocument",
]
