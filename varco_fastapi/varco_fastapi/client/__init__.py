"""
varco_fastapi.client
=====================
HTTP client layer for varco services.

Public API::

    from varco_fastapi.client import (
        AsyncVarcoClient,  # alias: VarcoClient
        ClientProfile,
        ClientConfigurator,
        ClientProtocol,
        SyncVarcoClient,
        JobHandle,
        JobFailedError,
        PreparedRequest,
        AbstractClientMiddleware,
        HeadersMiddleware,
        CorrelationIdMiddleware,
        AuthForwardMiddleware,
        JwtMiddleware,
        RetryMiddleware,
        LoggingMiddleware,
        TimeoutMiddleware,
        OTelClientMiddleware,
    )
"""

from __future__ import annotations

from varco_fastapi.client.base import (
    AsyncVarcoClient,
    ClientProfile,
    VarcoClient,
    make_client,
)
from varco_fastapi.client.config import ClientConfig
from varco_fastapi.client.configurator import ClientConfigurator
from varco_fastapi.client.generic import GenericClient
from varco_fastapi.client.handle import JobFailedError, JobHandle
from varco_fastapi.client.openapi import OpenAPIClient
from varco_fastapi.client.openapi_gen import generate_client
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
from varco_fastapi.client.protocol import ClientProtocol
from varco_fastapi.client.sync import SyncClientAsyncError, SyncVarcoClient

__all__ = [
    "AsyncVarcoClient",
    "VarcoClient",
    "ClientProfile",
    "ClientConfig",
    "ClientConfigurator",
    "ClientProtocol",
    "GenericClient",
    "OpenAPIClient",
    "make_client",
    "generate_client",
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
