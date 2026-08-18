"""
varco_fastapi.client.advanced
================================
Re-export shelf for the client surface demoted out of
``varco_fastapi.client``'s documented ``__all__`` (Plan 009, Phase 3 / C1).

``client_for()`` (``varco_fastapi.client.front_door``) is the documented way
to call another varco service now. These names solve real problems it
cannot — a third-party OpenAPI-described API (``OpenAPIClient``), a
no-router service (``GenericClient``) — and remain fully supported, just
moved out of the front door so the 90% path is one function.

Import from here, or from each name's own module — both work:

    from varco_fastapi.client.advanced import make_client, GenericClient
    from varco_fastapi.client.generic import GenericClient   # same class
"""

from __future__ import annotations

from varco_fastapi.client.base import make_client
from varco_fastapi.client.configurator import ClientConfigurator
from varco_fastapi.client.generic import GenericClient
from varco_fastapi.client.openapi import OpenAPIClient
from varco_fastapi.client.openapi_gen import generate_client

__all__ = [
    "ClientConfigurator",
    "GenericClient",
    "OpenAPIClient",
    "generate_client",
    "make_client",
]
