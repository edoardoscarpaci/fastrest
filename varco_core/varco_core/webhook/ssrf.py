"""
varco_core.webhook.ssrf
=========================
``validate_target`` — the security-critical five-layer SSRF guard
(Plan 031 / D4c, Step 10, §D-D4-ssrf). **Merge gate**: every test in
``test_webhook_ssrf.py`` must be green before P2 proceeds past this module.

Design recap (§D-D4-ssrf — see the plan for the full DESIGN block and
rejected alternatives):

1. Scheme allowlist — ``https`` only unless ``allow_insecure_http=True``
   (deployment-wide, never per-subscription/per-tenant).
2. Resolve-then-validate-then-pin — the hostname is resolved *once*, every
   resolved address is checked, and the **first** resolution is returned as
   the address the caller must connect to. A second, later resolution
   (e.g. the HTTP client re-resolving at connect time) must never be
   substituted — that is exactly the DNS-rebinding window this closes.
3. Blocked-by-default deny list (private/loopback/link-local/multicast/
   unspecified/reserved, IPv4 and IPv6, including the IPv4-mapped bypass
   form) plus an optional exclusive allowlist.
4. No redirect following — enforced by the dispatcher's transport, not
   this function (this function only validates one URL at a time).
5. IPv6 equivalents explicitly covered (``::1``, ``fc00::/7``, ``fe80::/10``,
   ``::ffff:<private-v4>``).

Thread safety:  N/A — no shared state.
Async safety:   ✅ ``validate_target``/``_resolve_host`` are ``async def``
                   (DNS resolution is I/O).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = ["validate_target", "SSRFValidationError", "TargetValidation"]


class SSRFValidationError(Exception):
    """Raised when a webhook target URL fails any §D-D4-ssrf layer."""


@dataclass(frozen=True)
class TargetValidation:
    """
    The outcome of a successful ``validate_target()`` call.

    Attributes:
        scheme:    The validated scheme (``"https"`` or ``"http"``).
        hostname:  The original hostname from the URL — used for the
                   ``Host`` header and TLS SNI at connect time, never for
                   the actual socket connection (§D-D4-ssrf layer 2).
        port:      The resolved port (scheme default if unspecified).
        path_qs:   Path + query string, unchanged from the input URL.
        pinned_ip: The validated IP address the caller MUST connect to.
                   Re-resolving ``hostname`` at connect time and using a
                   *different* address defeats the entire rebinding
                   defense — see the module docstring's layer 2.
    """

    scheme: str
    hostname: str
    port: int
    path_qs: str
    pinned_ip: str


# ── Blocked-range check ──────────────────────────────────────────────────────


def _unwrap_ipv4_mapped(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """
    Unwrap an IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) to its embedded
    IPv4 form before range-checking it.

    Without this, ``::ffff:169.254.169.254`` would be checked as a generic
    IPv6 address (not flagged private/link-local by Python's ``ipaddress``
    module for the mapped form) and slip past every other check — the
    "classic bypass" §D-D4-ssrf calls out explicitly.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_blocked_address(addr_str: str, *, extra_deny_ranges: tuple[str, ...] = ()) -> bool:
    """Return ``True`` if ``addr_str`` falls in any blocked range."""
    ip = ipaddress.ip_address(addr_str)
    ip = _unwrap_ipv4_mapped(ip)

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        return True

    for cidr in extra_deny_ranges:
        network = ipaddress.ip_network(cidr, strict=False)
        # Cross-family containment (e.g. an IPv4 network against an
        # unwrapped-from-IPv6 IPv4Address) — ipaddress raises TypeError for
        # a family mismatch; treat that as "does not apply", not blocked.
        try:
            if ip in network:
                return True
        except TypeError:
            continue
    return False


def _is_allowed_by_allowlist(
    hostname: str, resolved_ips: list[str], allow_list: tuple[str, ...] | None
) -> bool:
    """
    §D-D4-ssrf layer 3's exclusive allowlist: when set, a target must match
    it (by hostname or by one of its resolved addresses falling in an
    allowed CIDR) or be rejected outright — the allowlist-first posture.
    """
    if allow_list is None:
        return True
    if hostname in allow_list:
        return True
    for entry in allow_list:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        for addr in resolved_ips:
            try:
                if ipaddress.ip_address(addr) in network:
                    return True
            except (ValueError, TypeError):
                continue
    return False


# ── DNS resolution (monkeypatched by tests) ──────────────────────────────────


async def _resolve_host(host: str) -> list[str]:
    """
    Resolve ``host`` to a list of IP address strings.

    Runs the blocking ``socket.getaddrinfo`` call in a thread — DNS
    resolution is I/O the event loop must not be blocked on.

    Args:
        host: A hostname (not a literal IP — callers check that first).

    Returns:
        A list of resolved address strings (IPv4 and/or IPv6), in the
        order the resolver returned them. The **first** entry is what
        ``validate_target`` pins the connection to (§D-D4-ssrf layer 2).

    Raises:
        SSRFValidationError: The hostname could not be resolved at all.
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SSRFValidationError(f"Could not resolve host {host!r}: {exc}") from exc
    # Preserve order, dedupe.
    seen: dict[str, None] = {}
    for info in infos:
        addr = str(info[4][0])
        seen.setdefault(addr, None)
    return list(seen)


# ── Public entry point ────────────────────────────────────────────────────────


async def validate_target(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_list: tuple[str, ...] | None = None,
    extra_deny_ranges: tuple[str, ...] = (),
) -> TargetValidation:
    """
    Validate a webhook target URL against every §D-D4-ssrf layer.

    Args:
        url:                 The subscription's target URL.
        allow_insecure_http: Deployment-wide opt-in for ``http://``
                              (§D-D4-ssrf layer 1). Never per-tenant.
        allow_list:           Optional exclusive allowlist (hostnames
                              and/or CIDRs) — when set, the target must
                              match it or be rejected (layer 3).
        extra_deny_ranges:    Additional CIDR ranges to block, beyond the
                              built-in private/loopback/link-local/
                              multicast/unspecified/reserved set.

    Returns:
        A ``TargetValidation`` carrying the **pinned** IP the caller must
        connect to — never re-resolve ``hostname`` at connect time.

    Raises:
        SSRFValidationError: Any layer rejects the target — disallowed
            scheme, unresolvable host, every resolved address blocked, or
            an allowlist miss.

    Edge cases:
        - A literal IP address in the URL skips DNS resolution entirely —
          it IS the address to validate and pin.
        - Redirect following is explicitly out of scope here (layer 4) —
          this function validates one URL; the dispatcher's transport
          must be configured to never follow a 3xx.
    """
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()

    if scheme == "https":
        pass
    elif scheme == "http" and allow_insecure_http:
        pass
    else:
        raise SSRFValidationError(
            f"Scheme {scheme!r} is not permitted for webhook delivery. "
            "Only https:// is allowed by default (set allow_insecure_http=True "
            "at the deployment level to permit http://)."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError(f"URL {url!r} has no hostname to validate.")

    default_port = 443 if scheme == "https" else 80
    port = parsed.port or default_port
    path_qs = parsed.path or "/"
    if parsed.query:
        path_qs = f"{path_qs}?{parsed.query}"

    # Literal IP in the URL (including bracketed IPv6) — no DNS involved.
    try:
        literal = ipaddress.ip_address(hostname)
        resolved_ips = [str(literal)]
    except ValueError:
        resolved_ips = await _resolve_host(hostname)

    if not resolved_ips:
        raise SSRFValidationError(f"Host {hostname!r} did not resolve to any address.")

    if not _is_allowed_by_allowlist(hostname, resolved_ips, allow_list):
        raise SSRFValidationError(f"Host {hostname!r} is not on the configured allowlist.")

    for addr in resolved_ips:
        if _is_blocked_address(addr, extra_deny_ranges=extra_deny_ranges):
            raise SSRFValidationError(
                f"Host {hostname!r} resolves to a blocked address ({addr}) — "
                "private, loopback, link-local, multicast, unspecified, or "
                "reserved ranges are never valid webhook delivery targets."
            )

    # §D-D4-ssrf layer 2: pin to the FIRST resolution — a later, different
    # resolution (DNS rebinding) must never be substituted at connect time.
    pinned_ip = resolved_ips[0]

    return TargetValidation(
        scheme=scheme,
        hostname=hostname,
        port=port,
        path_qs=path_qs,
        pinned_ip=pinned_ip,
    )
