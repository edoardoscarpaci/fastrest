"""
varco_core.webhook.transport
==============================
The HTTP send path for outbound webhook delivery (Plan 031 / D4c, Step 14).

⚠️ **``httpx`` is imported inside a function body, never at module scope**
— the same rule and the same mechanical guard
(``test_webhook_no_hard_client_deps.py``) as
``varco_core.tls.clients``/``test_tls_no_hard_client_deps.py``. Importing
``varco_core.webhook`` (or any of its submodules) must never pull ``httpx``
into ``sys.modules`` — a caller who wires a Kafka-only or Redis-only app
that happens to import this package for its dispatcher must not gain a
transitive ``httpx`` dependency just from the import.

⚠️ **ASSUMPTION (plan Risks table)**: connecting to the SSRF-validated
*pinned* IP while preserving the original ``Host`` header and TLS SNI is
implemented via httpx's ``extensions={"sni_hostname": ...}`` request
extension (httpx ≥ 0.28) plus rewriting the connection URL's host to the
pinned IP. This has NOT been verified against a real TLS-terminating
receiver in this plan's test suite (only the structural
no-hard-dependency guard exercises this module) — if a production
deployment finds this insufficient, the documented fallback is a custom
``httpx.AsyncHTTPTransport``, not dropping the pin.

Thread safety:  N/A — one client per call, no shared state.
Async safety:   ✅ ``send_webhook`` is ``async def``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varco_core.webhook.ssrf import TargetValidation

__all__ = ["send_webhook", "WebhookResponse"]


class WebhookResponse:
    """
    Minimal response shape returned by ``send_webhook`` — deliberately NOT
    ``httpx.Response`` itself, so nothing outside this module needs to
    import ``httpx`` even for type hints.

    Attributes:
        status_code: The HTTP status code returned by the receiver.
    """

    __slots__ = ("status_code",)

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


async def send_webhook(
    target: TargetValidation,
    *,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> WebhookResponse:
    """
    Send one signed webhook delivery attempt.

    Args:
        target:  A ``TargetValidation`` from
                 ``varco_core.webhook.ssrf.validate_target`` — connects to
                 ``target.pinned_ip``, never re-resolving ``target.hostname``
                 (§D-D4-ssrf layer 2).
        headers: Signature + custom headers to send. The ``Host`` header is
                 set from ``target.hostname``, overriding anything the
                 caller passed for it.
        body:    Raw request body bytes (the exact JSON that was signed).
        timeout: Per-attempt timeout in seconds (§D-D4-delivery — 10s
                 default, set by the caller).

    Returns:
        A ``WebhookResponse`` carrying the receiver's status code.

    Raises:
        Exception: Any ``httpx`` transport-level error (timeout, connection
            refused, TLS failure) propagates to the caller — the
            dispatcher's retry loop is what interprets these, not this
            function.

    Edge cases:
        - ``follow_redirects=False`` is set explicitly — a 3xx response is
          returned as-is (its ``status_code`` in the 300s), never followed
          (§D-D4-ssrf layer 4).
    """
    # DESIGN: import here, not at module scope (Step 14) — see module
    # docstring. ✅ varco_core.webhook stays httpx-free for apps that never
    # configure webhooks. ❌ Every call pays a (cheap, already-cached-after-
    # first-import) import lookup — negligible next to the network I/O this
    # function performs.
    import httpx

    url = httpx.URL(
        scheme=target.scheme,
        host=target.pinned_ip,
        port=target.port,
        raw_path=target.path_qs.encode(),
    )
    send_headers = dict(headers)
    send_headers["Host"] = target.hostname

    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        request = client.build_request(
            "POST",
            url,
            headers=send_headers,
            content=body,
            extensions={"sni_hostname": target.hostname},
        )
        response = await client.send(request)

    return WebhookResponse(status_code=response.status_code)
