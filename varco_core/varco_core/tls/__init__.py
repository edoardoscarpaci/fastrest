"""
varco_core.tls
===============

One unified TLS trust model (Plan 026 / T3, T5, T7): ``TrustStore`` (a strict superset of
``varco_core.connection.ssl.SSLConfig`` and 3.0's ``varco_fastapi.auth.trust_store.TrustStore``),
``ReloadingTrustStore`` (built on ``varco_core.watch``/``varco_core.reload``, Plan 025), and the
shared ``iter_cert_files`` cert-glob helper.

**Layer rule (mechanically enforced by ``varco_core/tests/test_tls_layering.py``)**: this
package imports NOTHING from ``varco_core.connection``, ``varco_fastapi``, or any backend
package (``varco_kafka``/``varco_redis``/``varco_sa``/``varco_beanie``/...). The bridge the
other direction — ``SSLConfig.to_trust_store()`` — lives in ``varco_core.connection.ssl``, not
here, precisely so this package can stay a leaf. ``varco_fastapi.auth.trust_store.TrustStore``
(the deprecated 3.0-semantics shim, §D-T3-oq1) is the one place in ``varco_fastapi`` that
imports *this* package — never the reverse.

**No ``@Configuration`` here, ever (§D-T3-oq3).** ``container.scan("varco_core", recursive=True)``
is a documented, in-use pattern (``README.md``) that auto-activates scanned ``@Configuration``
classes. A ``TlsConfiguration`` here would start a filesystem watcher in every app that scans
``varco_core`` — the opposite of the locked "Auto-injection: explicit, opt-in, never implicit"
decision. ``varco_core.tls.di.bind_trust_store`` is a plain ``bind_*`` function instead: it has
no lifecycle side effect, and the caller always starts (or ``async with``s) the store itself.

Usage::

    from varco_core.tls import TrustStore, ReloadingTrustStore, bind_trust_store

    spec = TrustStore(ca_folders=Path("/etc/ssl/private-ca"))
    store = ReloadingTrustStore(spec)
    async with store:
        ctx = store.context
"""

from __future__ import annotations

from varco_core.tls.clients import (
    MissingClientDependencyError,
    to_aiohttp_connector,
    to_httpx_verify,
    to_requests_adapter,
    to_urllib3_poolmanager,
)
from varco_core.tls.di import bind_trust_store
from varco_core.tls.discovery import CERT_FILE_PATTERNS, iter_cert_files
from varco_core.tls.install import RestoreHandle, install_process_trust
from varco_core.tls.pkcs12 import Pkcs12LoadError
from varco_core.tls.reload import ReloadingTrustStore, ReloadStrategy
from varco_core.tls.store import TrustStore

__all__ = [
    "CERT_FILE_PATTERNS",
    "MissingClientDependencyError",
    "Pkcs12LoadError",
    "ReloadStrategy",
    "ReloadingTrustStore",
    "RestoreHandle",
    "TrustStore",
    "bind_trust_store",
    "install_process_trust",
    "iter_cert_files",
    "to_aiohttp_connector",
    "to_httpx_verify",
    "to_requests_adapter",
    "to_urllib3_poolmanager",
]
