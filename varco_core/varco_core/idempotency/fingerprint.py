"""
varco_core.idempotency.fingerprint
===================================
``compute_fingerprint()`` — binds an ``Idempotency-Key`` to the request that
first used it (§D-D1-fingerprint, Plan 029 / D1a, Step 4).

Brief 005 §1 (the expired IETF draft) recommends the server derive a
fingerprint from method, target URI, and body contents, and Stripe (the
de-facto standard the draft defers to) rejects a reused key carrying a
different payload with a 422-shaped error. This module computes exactly
that fingerprint; the ABC (``AbstractIdempotencyStore``) never inspects it
and the middleware (``varco_fastapi``) is the only caller that compares two
fingerprints for equality.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode


def _normalize_query(query: str) -> str:
    """
    Re-encode ``query`` with its key/value pairs sorted.

    DESIGN: sort-then-reencode over hashing the raw query string
        ✅ ``a=1&b=2`` and ``b=2&a=1`` are the same request semantically —
           reordering must never trigger a spurious fingerprint mismatch
           (§D-D1-fingerprint's explicit requirement).
        ❌ Two semantically-different-but-canonicalize-to-the-same-string
           query strings (e.g. differing only in percent-encoding of an
           already-decoded character) would collide. Accepted: this is the
           same trade-off any query-string canonicalization makes, and the
           the draft does not specify a canonical form to begin with.

    Args:
        query: The raw query string (no leading ``?``), e.g. ``"a=1&b=2"``.
               Empty string is valid (no query params).

    Returns:
        The re-encoded, key/value-sorted query string. Empty string in,
        empty string out.
    """
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode(sorted(pairs))


def compute_fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """
    Compute a stable fingerprint binding an ``Idempotency-Key`` to a request.

    fingerprint = ``sha256(method || "\\n" || path || "\\n" || sorted_query
    || "\\n" || sha256(body))``, hex-encoded (§D-D1-fingerprint). The raw
    body bytes are hashed directly — never parsed and re-serialized — so
    JSON key ordering or incidental whitespace in the body can never
    produce a spurious mismatch in either direction.

    Args:
        method: The HTTP method, e.g. ``"POST"``. Case-sensitive — callers
                should pass the method exactly as received (or consistently
                upper-cased) since this function does no normalization.
        path:   The raw request target path (no query string), e.g.
                ``"/orders"``.
        query:  The raw query string with no leading ``?``. Order-
                insensitive — see ``_normalize_query()``.
        body:   The raw request body bytes, exactly as received.

    Returns:
        A 64-character lowercase hex-encoded SHA-256 digest.

    Edge cases:
        - Reordering query parameters never changes the result.
        - An empty body (``b""``) is valid and hashes to a stable,
          well-known SHA-256 value (the hash of the empty string).

    Example::

        fp = compute_fingerprint("POST", "/orders", "a=1&b=2", b'{"x": 1}')
    """
    body_hash = hashlib.sha256(body).hexdigest()
    normalized_query = _normalize_query(query)
    material = f"{method}\n{path}\n{normalized_query}\n{body_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = ["compute_fingerprint"]
