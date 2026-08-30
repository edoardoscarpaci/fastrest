"""
Tests for BACKLOG KI-10 — ``BeanieFastrestApp``'s non-DI construction path.

``BeanieFastrestApp.__init__`` calls ``BeanieRepositoryProvider(mongo_client=…,
db_name=…, transactional=…)``, but the real ``BeanieRepositoryProvider.__init__``
signature (``varco_beanie/provider.py``) only accepts an injected
``settings: Inject[BeanieSettings]``. Every call therefore raises ``TypeError``.
This class has zero prior test coverage (BACKLOG evidence correction EC-3) —
these are the first tests ever written against it.

RED until Plan 020 Step 21 builds a ``BeanieSettings`` from ``BeanieConfig`` and
passes ``settings=``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from varco_beanie.bootstrap import BeanieConfig, BeanieFastrestApp
from varco_beanie.repository import AsyncBeanieRepository
from varco_core.model import DomainModel


@dataclass
class _Widget(DomainModel):
    """Minimal domain entity for bootstrap testing."""

    name: str = ""


class TestBeanieFastrestAppConstruction:
    """Docker-free: proves the constructor call shape actually works — no I/O required."""

    def test_construction_does_not_raise(self) -> None:
        fake_client = MagicMock()
        config = BeanieConfig(
            mongo_client=fake_client,
            db_name="x",
            entity_classes=(_Widget,),
            transactional=True,
        )

        # This is KI-10 itself: today this raises TypeError because
        # BeanieRepositoryProvider.__init__ only accepts `settings=`.
        app = BeanieFastrestApp(config)

        assert app is not None

    def test_entity_registration_happened_via_uow_provider(self) -> None:
        fake_client = MagicMock()
        config = BeanieConfig(
            mongo_client=fake_client,
            db_name="x",
            entity_classes=(_Widget,),
            transactional=True,
        )

        app = BeanieFastrestApp(config)

        repo = app.uow_provider.get_repository(_Widget)
        assert isinstance(repo, AsyncBeanieRepository)


@pytest.mark.integration
class TestBeanieFastrestAppIntegration:
    async def test_save_and_get_round_trip_through_a_real_mongo(self, mongo_url: str) -> None:
        from pymongo import AsyncMongoClient

        db_name = f"varco_bootstrap_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        try:
            config = BeanieConfig(
                mongo_client=client,
                db_name=db_name,
                entity_classes=(_Widget,),
            )
            app = BeanieFastrestApp(config)
            await app.init()

            async with app.uow_provider.make_uow() as uow:
                repo = uow.get_repository(_Widget)
                saved = await repo.save(_Widget(name="hello"))
                fetched = await repo.find_by_id(saved.id)

            assert fetched is not None
            assert fetched.name == "hello"
        finally:
            await client.drop_database(db_name)
            client.close()
