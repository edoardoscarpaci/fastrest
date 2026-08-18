"""
varco_fastapi.contract
========================
The ``ServiceContract`` descriptor (Plan 009, Phase 0/8 — C3): the portable,
JSON-serializable route surface both client-generation paths (in-process
``_VarcoClientMeta`` and cross-repo ``contract_client``/``gen-client``) build
through. See ``model.py`` for the value objects and ``build.py`` for the
``introspect_routes()`` → ``ServiceContract`` translation.
"""

from __future__ import annotations

from varco_fastapi.contract.build import build_contract
from varco_fastapi.contract.model import (
    CONTRACT_VERSION,
    ContractVersionError,
    ParamContract,
    RouteContract,
    ServiceContract,
)

__all__ = [
    "CONTRACT_VERSION",
    "ContractVersionError",
    "ParamContract",
    "RouteContract",
    "ServiceContract",
    "build_contract",
]
