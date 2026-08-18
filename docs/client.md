# Calling another varco service — `client_for`

`client_for()` (`varco_fastapi.client`) is the documented way to call another
varco service from Python. It returns a ready-to-call client instance — no
class to subclass, no manual httpx wiring.

## Quick start

```python
from varco_fastapi.client import client_for
from orders_service.routers import OrderRouter   # the peer's router class — importable

client = client_for(OrderRouter, "https://orders.internal")

order = await client.read(order_id)          # typed CRUD method
orders = await client.list(limit=20)          # paginated list
await client.cancel(order_id, reason="oos")   # custom @route method
```

`client_for(router_cls, base_url=None, *, profile=None, timeout=None,
verify=True, middleware=None, headers=None)` builds (and internally memoizes)
a generated `AsyncVarcoClient` subclass for `router_cls`, then eagerly
constructs its `httpx.AsyncClient` so the returned instance is call-ready —
no `async with` ceremony required for a one-off call. Use `async with` (or
call `.aclose()`) if you want the connection pool closed deterministically.

```python
async with client_for(OrderRouter, url) as client:
    order = await client.read(order_id)
# pool closed on exit
```

## Deferred base URL

`base_url=None` is valid — resolution is deferred to the first request. If
nothing ever supplies a URL (no `base_url`, no peer registry — see
`docs/peer-service-integration.md`), the first call raises `RuntimeError`
naming both `client_for(..., base_url=)` and `VARCO_PEER_<NAME>_URL`.

## Getting a client via DI

```python
from varco_fastapi.di import bind_clients_from
from providify import Inject
from varco_fastapi.client import VarcoClient

bind_clients_from(container, OrderRouter, UserRouter)

class ReportService:
    def __init__(self, orders: Inject[VarcoClient[OrderRouter]]) -> None:
        self._orders = orders
```

`client_class_for(router_cls)` is the class-only counterpart `bind_clients_from`
uses internally — reach for it directly only when you need the *class*
(e.g. to subclass it), not an instance.

## What moved — the "advanced" shelf

Before Plan 009, this module's documented surface was `make_client`,
`GenericClient`, `OpenAPIClient`, `ClientConfigurator`, `generate_client` —
all still fully supported, but no longer the first thing you reach for:

| Still first-class (`varco_fastapi.client`) | Moved to `varco_fastapi.client.advanced` |
|---|---|
| `client_for`, `client_class_for` | `make_client` |
| `AsyncVarcoClient` / `VarcoClient` | `GenericClient` — a no-router / third-party-service client |
| `SyncVarcoClient` | `OpenAPIClient` — build a client from a raw OpenAPI document |
| `ClientProfile`, `ClientConfig` | `ClientConfigurator` |
| the `middleware` module | `generate_client` |
| `JobHandle`, `JobFailedError`, `ClientProtocol` | |

```python
# Old (breaks now with a legible AttributeError naming the new path):
from varco_fastapi.client import GenericClient

# New:
from varco_fastapi.client.advanced import GenericClient
```

Reach for `GenericClient`/`OpenAPIClient` when there is no `VarcoRouter` to
introspect (a third-party API, a data-pipeline service) — `client_for()`
cannot help there, it requires an importable `VarcoRouter` subclass.

## Custom `@route` methods — current typing status

`client_for()`'s custom-route methods (anything beyond the generated CRUD
set) currently accept `**kwargs: Any` at the Python level — they are **not**
yet built through the new `build_client_method`/contract machinery (see
`technical_docs/features/portable-contracts.md`'s status note). If you need
a fully typed, IDE-checkable signature for a custom route today, generate a
standalone client module instead:

```bash
varco gen-client -c order.contract.json -o order_client.py --class-name OrderClient
```

See `docs/client-code-generation.md` for the full cross-repo story, and
`docs/peer-service-integration.md` for `PeerRegistry` — the "one env var,
one inject" pattern for a fleet of peer services.
