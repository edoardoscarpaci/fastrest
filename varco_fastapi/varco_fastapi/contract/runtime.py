"""
varco_fastapi.contract.runtime
=================================
``contract_client`` — build a live client from an exported contract, no
service import required (Plan 009, Phase 8 / C3 part 2).

The "one-liner for scripts/notebooks" counterpart to ``varco gen-client``'s
"fully typed, checked into the consumer repo" path — both go through
``build_client_method`` (via ``SynthesizedTypeResolver``), so they cannot
diverge from each other or from the in-process ``client_for()`` path.

Thread safety:  ✅ Pure/synchronous construction.
Async safety:   ✅ The returned client's own async methods are unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from varco_fastapi.client.base import AsyncVarcoClient, ClientProfile
    from varco_fastapi.contract.model import ServiceContract


def contract_client_class(contract: ServiceContract, *, name: str | None = None) -> type:
    """
    Build (not memoized — call sites typically call this once per contract)
    an ``AsyncVarcoClient`` subclass whose methods are synthesized entirely
    from ``contract`` via ``SynthesizedTypeResolver`` + ``build_client_method``
    — no router import.

    Args:
        contract: The ``ServiceContract`` to build a client class from.
        name:     Class name. Defaults to ``f"{contract.service_name}Client"``.

    Returns:
        A fresh ``AsyncVarcoClient`` subclass with one method per route.
    """
    from varco_fastapi.client.base import AsyncVarcoClient, _VarcoClientMeta
    from varco_fastapi.client.method import SynthesizedTypeResolver, build_client_method

    resolver = SynthesizedTypeResolver(contract)
    namespace: dict[str, Any] = {}
    for route in contract.routes:
        namespace[route.name] = build_client_method(route, resolver)

    class_name = name or f"{contract.service_name}Client"
    # AsyncVarcoClient's metaclass (_VarcoClientMeta) must build the subclass
    # — a plain `type(name, (AsyncVarcoClient,), ns)` call bypasses it and
    # raises a metaclass conflict (AsyncVarcoClient's metaclass is not `type`
    # itself). No `__orig_bases__` here — there is no router class to
    # resolve CRUD methods from; every method in `namespace` is already a
    # fully-built custom method.
    return _VarcoClientMeta(class_name, (AsyncVarcoClient,), namespace)


def contract_client(
    contract: ServiceContract | str | Path,
    base_url: str | None = None,
    *,
    profile: ClientProfile | None = None,
    **kwargs: Any,
) -> AsyncVarcoClient[Any]:
    """
    Build a live client from an exported contract — no service import.

    Args:
        contract: A ``ServiceContract``, or a path (``str``/``Path``) to a
                  ``.contract.json`` file to load via ``ServiceContract.from_json``.
        base_url: Target service base URL.
        profile:  Optional ``ClientProfile``.
        **kwargs: Forwarded to the generated class's constructor
                  (``verify=``, ``timeout=``, ``middleware=``, ...).

    Returns:
        A ready-to-call client instance.
    """
    from varco_fastapi.contract.model import ServiceContract

    resolved_contract = (
        contract
        if isinstance(contract, ServiceContract)
        else ServiceContract.from_json(Path(contract).read_text())
    )

    cls = contract_client_class(resolved_contract)
    # why: cls is a metaclass-constructed type built at runtime (see
    # contract_client_class() / _VarcoClientMeta above) — mypy cannot
    # statically prove the instance is an AsyncVarcoClient[Any] subclass.
    instance = cast("AsyncVarcoClient[Any]", cls(base_url, profile=profile, **kwargs))
    instance._client = instance._build_httpx_client()
    return instance


__all__ = ["contract_client", "contract_client_class"]
