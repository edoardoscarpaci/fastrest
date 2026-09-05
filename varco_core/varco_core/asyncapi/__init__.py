"""
varco_core.asyncapi
====================
AsyncAPI 3.1.0 document generation for varco's event system.

Backend-agnostic by construction: this package reads only ``varco_core.event``
metadata and Pydantic schemas, and imports no bus backend — the same placement
logic that keeps the SQLAlchemy query applicator inside
``varco_core.query.applicator`` (Plan 022 §D-AA4, reserved-seams RS-3).

Usage::

    from varco_core.asyncapi import generate_asyncapi

    doc = generate_asyncapi(container, title="Orders", version="1.0.0")

The CLI wrapper — ``varco export-asyncapi``, with the ``--check`` snapshot gate —
lives in ``varco_core.cli.asyncapi``.  Full narrative:
``technical_docs/features/asyncapi-export.md``.
"""

from __future__ import annotations

from varco_core.asyncapi.generator import ASYNCAPI_VERSION, generate_asyncapi

__all__ = ["ASYNCAPI_VERSION", "generate_asyncapi"]
