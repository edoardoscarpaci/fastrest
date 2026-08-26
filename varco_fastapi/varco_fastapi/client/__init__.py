"""
varco_fastapi.client
=====================
HTTP client layer for varco services.

**Documented front door (Plan 009, Phase 3 / C1)**::

    from varco_fastapi.client import client_for

    client = client_for(OrderRouter, "https://orders.internal")
    order = await client.read(order_id)

``client_for()`` returns a ready-to-call instance; ``client_class_for()`` is
the DI-binding counterpart (see ``varco_fastapi.di.bind_clients_from``).

Everything else that was previously first-class here — ``make_client``,
``GenericClient``, ``OpenAPIClient``, ``ClientConfigurator``,
``generate_client`` — is demoted to ``varco_fastapi.client.advanced``
(still fully supported, just no longer the first thing you reach for).
Importing a demoted name directly from this module raises ``AttributeError``
naming the new location — see ``__getattr__`` below.
"""

from __future__ import annotations

from typing import Any

from varco_fastapi.client.base import AsyncVarcoClient, ClientProfile, VarcoClient
from varco_fastapi.client.config import ClientConfig
from varco_fastapi.client.front_door import client_class_for, client_for
from varco_fastapi.client.handle import JobFailedError, JobHandle
from varco_fastapi.client.middleware import (
    AbstractClientMiddleware,
    AuthForwardMiddleware,
    CorrelationIdMiddleware,
    HeadersMiddleware,
    JwtMiddleware,
    LoggingMiddleware,
    OTelClientMiddleware,
    PreparedRequest,
    RetryMiddleware,
    TimeoutMiddleware,
)
from varco_fastapi.client.peer import PeerConfig, PeerRegistry, bind_peers
from varco_fastapi.client.protocol import ClientProtocol
from varco_fastapi.client.sync import SyncClientAsyncError, SyncVarcoClient

# Demoted names — importable from varco_fastapi.client.advanced (and their
# own modules), NOT from this module directly. Mapping is name -> module
# path, used by __getattr__ below to produce a legible AttributeError.
_DEMOTED: dict[str, str] = {
    "make_client": "varco_fastapi.client.advanced",
    "GenericClient": "varco_fastapi.client.advanced",
    "OpenAPIClient": "varco_fastapi.client.advanced",
    "ClientConfigurator": "varco_fastapi.client.advanced",
    "generate_client": "varco_fastapi.client.advanced",
}


def __getattr__(name: str) -> Any:
    """
    PEP 562 module ``__getattr__`` — turns a stale ``from varco_fastapi.client
    import GenericClient`` into a legible error naming the new location,
    rather than a silent re-export (Phase 3's deliberate hard break — see
    the migration note in the plan).
    """
    if name in _DEMOTED:
        raise AttributeError(
            f"{name!r} was moved out of varco_fastapi.client's front door "
            f"(Plan 009, Phase 3). Import it from {_DEMOTED[name]} instead: "
            f"`from {_DEMOTED[name]} import {name}`."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "client_for",
    "client_class_for",
    "PeerRegistry",
    "PeerConfig",
    "bind_peers",
    "AsyncVarcoClient",
    "VarcoClient",
    "ClientProfile",
    "ClientConfig",
    "ClientProtocol",
    "SyncVarcoClient",
    "SyncClientAsyncError",
    "JobHandle",
    "JobFailedError",
    "PreparedRequest",
    "AbstractClientMiddleware",
    "HeadersMiddleware",
    "CorrelationIdMiddleware",
    "AuthForwardMiddleware",
    "JwtMiddleware",
    "RetryMiddleware",
    "LoggingMiddleware",
    "TimeoutMiddleware",
    "OTelClientMiddleware",
]
