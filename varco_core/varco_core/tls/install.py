"""
varco_core.tls.install
========================

``install_process_trust`` — the acknowledged, explicit, reversible process-global TLS
context override (Plan 027 / T4c, §D-T4-install). **varco itself never calls this function** —
``varco_core/tests/test_tls_install.py::test_varco_never_calls_install_process_trust_itself``
and a matching ``rg`` sweep (Plan 027 / Step 20) are the mechanical guards.

DESIGN: an explicit, acknowledged, reversible global — or nothing (§D-T4-install)
    ✅ Brief 001 §3/§4: ``truststore.inject_into_ssl()``-style patching is what makes *all*
       stdlib-ssl users (requests, urllib3, httpx, aiohttp) pick up a trust store at once —
       that is the actual capability the BACKLOG's T4 row names. The same brief is equally
       explicit that **libraries must not do this on their own** — only the *application* may
       decide to. An explicit function the application calls, never varco itself, is the
       shape that satisfies both halves of that sentence.
    ✅ The ``acknowledge_global_mutation`` kwarg matches an established varco convention for
       privileged, hard-to-undo surfaces (``mount_tenant_admin(...,
       acknowledge_bundled_admin=True)``, ``mount_reliability_admin(...)``). Consistency here
       is not decoration — it is what makes the danger legible to a reviewer scanning a diff.
    ✅ Returning a ``RestoreHandle`` (usable as a plain object or as a context manager) makes
       the mutation testable and makes an accidental permanent mutation in someone's test
       suite recoverable — call ``.restore()`` (or exit the ``with`` block).
    ❌ ``ssl._create_default_https_context`` is a **private** stdlib name — long-standing
       CPython, but private, and could disappear or change shape in a future release. This
       function asserts the attribute exists *before* touching anything, and raises a clear,
       actionable ``RuntimeError`` naming the running Python version if it does not, rather
       than silently no-op'ing (a silent no-op would be far worse: a caller believing trust is
       installed when it is not).
    ❌ It only affects contexts created **after** the call, and only those created through
       ``ssl.create_default_context()``-style paths that consult the hook (brief 001 §4's own
       caveat about ``truststore.inject_into_ssl()``). A context already constructed before
       this call — or one built directly via ``ssl.SSLContext(...)`` rather than
       ``create_default_context()`` — is unaffected either way. There is no way to detect
       this from inside ``install_process_trust`` itself; it is a documented limitation,
       stated here and in the docstring below, not silently swallowed.
    ❌ Alternative rejected: setting ``SSL_CERT_FILE``/``SSL_CERT_DIR`` process-wide instead.
       That has the replace-not-add semantics varco explicitly rejected for its own env-var
       reading (Plan 026 / §D-T3-env), and mutating the environment of a running process to
       change library behaviour is strictly more surprising than patching one documented-by-
       convention hook that many tools (``truststore``, some http libraries themselves)
       already treat as the accepted "did someone globally override https trust" signal.

Thread safety:  ⚠️ ``ssl._create_default_https_context`` is process-global, mutable state —
                   calling this function from two threads concurrently races on which hook
                   wins, exactly as any other process-global monkeypatch would. Call it once,
                   at application startup, before spawning worker threads that build HTTP
                   clients.
Async safety:   ✅ Synchronous — there is nothing to await; the mutation itself is instant.
"""

from __future__ import annotations

import ssl

from varco_core.tls.reload import ReloadingTrustStore
from varco_core.tls.store import TrustStore


class RestoreHandle:
    """
    Undoes a single ``install_process_trust`` call — usable directly (``handle.restore()``)
    or as a context manager, i.e. ``with`` this module's installation function returning
    ``as handle``.

    Args:
        previous_hook: The value of ``ssl._create_default_https_context`` captured
            immediately before this installation replaced it.

    Edge cases:
        - Calling ``restore()`` more than once is safe — the second call simply re-assigns
          the same ``previous_hook`` value again (idempotent, not an error).
        - Two handles released **out of order** (LIFO violated) each restore the value *they*
          captured — releasing the older handle last can resurrect a hook an intervening,
          still-active installation expected to remain in place. The context-manager form,
          used in normal (nested, LIFO) ``with`` blocks, is the supported usage; releasing by
          hand out of order is a caller error this class does not attempt to detect.
    """

    def __init__(self, previous_hook: object) -> None:
        self._previous_hook = previous_hook

    def restore(self) -> None:
        """Put back the hook value captured at construction time."""
        ssl._create_default_https_context = self._previous_hook  # type: ignore[assignment] # noqa: SLF001

    def __enter__(self) -> RestoreHandle:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.restore()


def install_process_trust(
    store: TrustStore | ReloadingTrustStore, *, acknowledge_global_mutation: bool = False
) -> RestoreHandle:
    """
    Patch ``ssl._create_default_https_context`` so that any stdlib-ssl-backed HTTP client
    created **after** this call, via a ``create_default_context()``-style path, picks up
    ``store``'s trust configuration process-wide — the "inject into everything at once"
    capability brief 001 names, made explicit and reversible instead of automatic
    (§D-T4-install).

    Args:
        store: A ``TrustStore`` (a fresh ``ssl.SSLContext`` is built once, here, at
            installation time) or a ``ReloadingTrustStore`` (its live ``.context`` is read
            **each time** the patched hook is invoked, so a MUTATE rotation is visible to
            every future call of the hook with zero extra action — the same "read at call
            time" discipline as ``varco_core.tls.clients``).
        acknowledge_global_mutation: Must be explicitly ``True``. This is not a convenience
            default — the whole point of the kwarg is that a reviewer sees it in the diff.

    Returns:
        A ``RestoreHandle`` — call ``.restore()`` (or use it as a context manager) to put the
        original hook back exactly as it was.

    Raises:
        ValueError: ``acknowledge_global_mutation`` was not passed as ``True``. No mutation
            occurs in this case — the hook is left completely untouched.
        RuntimeError: The running Python interpreter has no
            ``ssl._create_default_https_context`` attribute at all. This is a private CPython
            name (§D-T4-install ❌) — rather than silently doing nothing (which would be far
            more dangerous than a loud failure), this function refuses to proceed and names
            the running version so the caller knows exactly what changed. The documented
            fallback is the manual, per-client pattern (``verify=store.to_httpx_verify()``,
            etc., via ``varco_core.tls.clients``) — there is no second private hook to fall
            back to.

    Edge cases:
        - Only affects contexts built **after** this call, via a
          ``create_default_context()``-consuming path — an HTTP client (or any code) that
          already constructed its context, or that builds one directly via
          ``ssl.SSLContext(...)`` rather than ``ssl.create_default_context()``, is unaffected
          either way. Call this at the very top of an application's entry point, before
          importing/constructing any HTTP client.
        - Calling this twice nests correctly: the second call's ``RestoreHandle`` captures the
          hook installed by the first call, so releasing in LIFO order restores each layer in
          turn, ending at the true original.
        - varco itself never calls this function — it is an application-level decision only.

    Example::

        from varco_core.tls import TrustStore, install_process_trust

        store = TrustStore(ca_folders=Path("/etc/ssl/private-ca"))
        installer = install_process_trust
        # Call this ONCE, at process startup, before constructing any HTTP client:
        installer(store, acknowledge_global_mutation=True)
    """
    if not acknowledge_global_mutation:
        raise ValueError(
            "This function mutates process-global ssl state, affecting every "
            "stdlib-ssl-backed HTTP client in this process. Pass "
            "acknowledge_global_mutation=True to confirm this is intentional."
        )

    if not hasattr(ssl, "_create_default_https_context"):
        raise RuntimeError(
            "install_process_trust requires the private ssl._create_default_https_context "
            f"hook, which is not present on this interpreter (running {ssl.OPENSSL_VERSION}, "
            "Python's own version-specific ssl module layout may have changed it). Refusing "
            "to silently no-op — use varco_core.tls.clients' per-client adapters "
            "(to_httpx_verify(), to_aiohttp_connector(), ...) instead."
        )

    previous_hook = ssl._create_default_https_context  # noqa: SLF001 — see module docstring

    def _factory(*_args: object, **_kwargs: object) -> ssl.SSLContext:
        # Reads store's context fresh on every call — a ReloadingTrustStore's live .context
        # under MUTATE means every future call sees the rotation with zero extra action.
        if isinstance(store, ReloadingTrustStore):
            return store.context
        return store.build_ssl_context()

    ssl._create_default_https_context = _factory  # noqa: SLF001
    return RestoreHandle(previous_hook)


__all__ = ["RestoreHandle", "install_process_trust"]
