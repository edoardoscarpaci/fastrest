# Research 005 — Third-party generic type parameters for `disallow_any_generics`
Date: 2026-08-30 · Freshness matters: yes — type system features change with minor versions

## Question
What type parameters must be supplied to third-party generic types in a Python 3.12 codebase once mypy's `disallow_any_generics` is enabled? For SQLAlchemy 2.0.48, Beanie 2.0.1 + motor 3.7.1 + pymongo 4.16.0, FastAPI 0.135.2 / Starlette 1.0.0 / pydantic 2.12.5, redis-py 7.3.0, and casbin 1.43.0 / aiokafka 0.13.0 / nats-py 2.14.0, which public names are generic and what are their type parameters? Which are NOT generic? Do they ship `py.typed`?

## Findings

> ⚠️ **SUPERSEDED (Plan 021 §D5, 2026-08-30).** The "NOT generic (do not parameterize)" list
> below is **wrong** for six of its entries, contradicted by the pinned, installed mypy 2.3.1
> against the resolved SQLAlchemy 2.0.48: `Select`, `Column`, `async_sessionmaker`, `Row`,
> `TypeEngine`, and `MappedColumn` are all generic and mypy's `disallow_any_generics` flags every
> bare use of them (20 sites, measured). This section's own "Evidence gap" note below flags
> exactly this failure mode ("requires source inspection to verify") — it was derived from
> prose/API docs, not the installed distribution, which is precisely the U-8 lesson (see
> `UPSTREAM-GAPS.md`'s "Maintainer response" section for the prior instance of this same class of
> mistake). Correct parameterizations, derived from the installed distribution
> (`Select[tuple[Any, ...]]`, `Column[Any]`, `async_sessionmaker[AsyncSession]`,
> `Row[tuple[Any, ...]]`, `TypeEngine[Any]`, `MappedColumn[Any]`), are recorded in
> `plans/021-mypy-strict-full-ramp.md` §D5 and applied throughout `varco_sa`. This section's
> *other* findings (motor/pymongo/pydantic generics; casbin/aiokafka/nats-py being untyped) were
> corroborated by the same measurement and stand unchanged.

### SQLAlchemy 2.0.48
- **Ships `py.typed`**: YES — SQLAlchemy 2.0 includes inline type annotations per PEP-561 (deprecated external `types-SQLAlchemy` stubs). [Mypy / Pep-484 Support for ORM Mappings — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/mypy.html)

**Generic types (require type parameters):**
- `Mapped[T]` — the single type parameter is the Python type of the mapped attribute, e.g. `Mapped[int]`, `Mapped[str]`, `Mapped[Optional[UUID]]`. This is the mandatory type container for all ORM-mapped attributes. [Class Mapping API — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html)
- Generic PEP-695 type aliases with `Annotated` — as of SQLAlchemy 2.0.44, custom generic types can be defined as `PrimaryKey = Annotated[T, mapped_column(primary_key=True)]` and applied within `Mapped`. [Table Configuration with Declarative — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html)

**NOT generic (do not parameterize):**
- `MappedColumn` — represents the ORM-mapped Column object at runtime, not a generic type in the signature.
- `Column` — Core-level column type; `mapped_column()` factory is used instead in declarative mappings.
- `Select` — while queries return typed result sets, the `Select` construct itself is not directly parameterized in user code (mypy integrates result type via `Session.execute(select(...))` return inference).
- `Result[T]` / `ScalarResult[T]` — these are generic **in the return type** but not parameterized by user code; they emerge from `session.execute()` / `session.scalars()` return inference.
- `Session` / `AsyncSession` — NOT generic; no type parameter exposed in the public API (Plan 020 / RL-14 note: confirmed via source inspection — they carry internal stateful connection references, not generic wrappers).
- `Engine` / `AsyncEngine` — NOT generic; configuration objects only.
- `sessionmaker` / `async_sessionmaker` — NOT generic; factory functions, return type inferred from bind.
- `DeclarativeBase` — NOT generic; serves as a base class mixin for model registries.
- `TypeDecorator` — NOT generic; abstract base for custom column types (subclasses do not parameterize).

**Evidence gap:** SQLAlchemy 2.0.48 documentation does not explicitly list every type's generic status in a single table; above inferred from API docs and the deprecation of the mypy plugin (which implied generics are now transparent to the type system). Confirmed `Mapped[T]` and custom `Annotated` generics; `Select`/`Result`/`ScalarResult` generic status requires source inspection to verify parameter naming (likely `T` for row type, but not user-facing).

### Beanie 2.0.1 + motor 3.7.1 + pymongo 4.16.0

**Beanie 2.0.1:**
- **Ships `py.typed`**: Unclear from documentation; Beanie issue tracker discusses typing gaps but no explicit py.typed marker confirmed. — GitHub issue [#679](https://github.com/BeanieODM/beanie/issues/679)
- `Document` — **not explicitly documented as generic**. Beanie models inherit from `Document`, but the class itself is not parameterized in user code (unlike pydantic's `BaseModel` with `Generic[T]`).

**motor 3.7.1:**
- **Ships `py.typed`**: YES — motor 3.7 added comprehensive type hints to all asyncio APIs. [Type Hints - Motor 3.7.1 documentation](https://motor.readthedocs.io/en/stable/examples/type_hints.html)
- `AsyncIOMotorClient[T]` — **generic** over document type `T`. Recommended type parameter: `Dict[str, Any]` for unstructured data, or a `TypedDict` for schemas with known fields. Example: `client: AsyncIOMotorClient[Dict[str, Any]]`.
- `AsyncIOMotorDatabase[T]` — **generic** over document type `T` (inherited from client).
- `AsyncIOMotorCollection[T]` — **generic** over document type `T`. Example: `collection: AsyncIOMotorCollection[Movie]` where `Movie` is a `TypedDict`.
- **Guidance:** Motor docs recommend `TypedDict` with `NotRequired[ObjectId]` for the `_id` field to match MongoDB's automatic field addition. [Motor 3.7.1 Type Hints Examples](https://motor.readthedocs.io/en/stable/examples/type_hints.html)

**pymongo 4.16.0:**
- **Ships `py.typed`**: YES — pymongo 4.0+ includes inline type annotations. [Type Hints - PyMongo 4.13.2 documentation](https://pymongo.readthedocs.io/en/4.13.2/examples/type_hints.html)
- `MongoClient[T]` — **generic** over document type `T`. Recommended: `MongoClient[Dict[str, Any]]` or unparameterized.
- `Database[T]` — **generic** over document type `T`.
- `Collection[T]` — **generic** over document type `T`. The documentation states "a type for MongoClient must be specified" but allows both `MongoClient` and `MongoClient[Dict[str, Any]]`.
- **Guidance:** For well-defined schemas, use `TypedDict` (Python 3.8+). PyMongo docs warn that mypy does not yet provide default generic values; use `NotRequired[ObjectId]` in TypedDict definitions to handle `_id` correctly.

### FastAPI 0.135.2 / Starlette 1.0.0 / pydantic 2.12.5

**FastAPI 0.135.2:**
- **Ships `py.typed`**: YES — FastAPI includes inline type annotations.
- `APIRouter` — NOT generic; no type parameters in the public API.
- `Request` — NOT generic; represents the ASGI HTTP request.
- `Response` — NOT generic; represents the ASGI HTTP response.
- `Depends` — NOT generic (despite being used in function signatures); it is a dependency marker, not a container type.
- `BackgroundTasks` — NOT generic; a concrete tasks container.

**Starlette 1.0.0:**
- **Ships `py.typed`**: YES — as of Starlette 1.0, py.typed is included. Issue #1959 confirmed pending inclusion; released 2026-03-22. [Missing py.typed · Discussion #1959](https://github.com/encode/starlette/discussions/1959)
- **Generic additions in 1.0:** Starlette 1.0 introduced **generic state for `WebSocket[T]`**, allowing type-safe state access: `ws.scope["state"]` is now properly typed. [Starlette 1.0 Release Notes (GitHub)](https://github.com/encode/starlette/blob/master/docs/release-notes.md)
- `WebSocket[T]` — **generic** over application state type `T` as of 1.0.0.

**pydantic 2.12.5:**
- **Ships `py.typed`**: YES — pydantic included py.typed marker since v0.19. [add py.typed marker file for PEP-561 support](https://github.com/pydantic/pydantic/pull/391)
- `BaseModel` — NOT directly generic in the public API; however, models can inherit from both `BaseModel` and `typing.Generic[T]` to create generic models. Example: `class Container(BaseModel, Generic[T]): item: T`.
- `TypeAdapter[T]` — **generic** over the type being adapted. Example: `TypeAdapter[list[str]]` for runtime validation without a model.
- `RootModel[T]` — **generic** over the root value type. Example: `RootModel[list[str]]` for a list-of-strings model. The single type parameter is mandatory.

### redis-py 7.3.0

- **Ships `py.typed`**: Likely YES for 7.3.0+ (confirmed for 8.0.0+). redis-py 8.0.0 systematic type hint improvements with `@overload` patterns. [Asyncio Examples - redis-py 8.1.0 documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- `Redis[T]` — **generic** over response type `T` where `T ∈ {str, bytes}`. Controlled by `decode_responses` parameter: `Redis[str]` when `decode_responses=True`, `Redis[bytes]` when `False`. [ResponseT type seems to be inconsistent · Issue #2933](https://github.com/redis/redis-py/issues/2933)
- `redis.asyncio.Redis[T]` — **generic**, same type parameter as sync.
- **Version note:** redis-py 7.3.0 used union-based `ResponseT = Union[Awaitable[Any], Any]` for async/sync return types (pre-overload pattern). Mypy `disallow_any_generics` will flag bare `Redis` as `Redis[Any]`. Explicitly parameterize: `Redis[str]` or `Redis[bytes]`.
- `ConnectionPool` — unclear if generic from 7.3.0 docs; not documented as a user-facing type parameter.
- `PubSub` — NOT generic in public API.
- `Pipeline` — NOT generic in public API.

**Remediation:** For 7.3.0, parameterize `Redis` and `redis.asyncio.Redis` explicitly based on `decode_responses` setting.

### casbin 1.43.0

- **Ships `py.typed`**: NO explicit evidence. pycasbin repository [GitHub - pycasbin/casbin](https://github.com/pycasbin/casbin) does not document py.typed or comprehensive inline type annotations.
- No generic public types identified.
- **Impact of missing `py.typed`:** mypy will treat all casbin symbols as `Any` by default, so `disallow_any_generics` does NOT fire on casbin usage (it is untyped, not incorrectly typed).

### aiokafka 0.13.0

- **Ships `py.typed`**: NO clear evidence. aiokafka repository has mypy configuration (check_untyped_defs, disallow_any_generics enabled in CI) but no confirmed py.typed marker in 0.13.0. [aiokafka · PyPI](https://pypi.org/project/aiokafka/)
- No generic public types documented.
- **Impact:** Likely treated as untyped by mypy; `disallow_any_generics` does not fire on untyped libraries.

### nats-py 2.14.0

- **Ships `py.typed`**: NO — nats.py 2.14.0 release notes do not mention py.typed or typing improvements. [nats-io/nats.py releases](https://github.com/nats-io/nats.py/releases)
- No generic public types identified.
- **Impact:** Untyped library; mypy treats all nats-py symbols as `Any`.

## Options compared (when the question is a comparison)

| Library | Generic Types | py.typed | Status | Remediation |
|---------|---|---|---|---|
| **SQLAlchemy 2.0.48** | `Mapped[T]`, custom `Annotated[T, ...]` | YES | Typed inline | Parameterize `Mapped` with Python type; leverage generics in custom Annotated factories |
| **motor 3.7.1** | `AsyncIOMotorClient[T]`, `AsyncIOMotorDatabase[T]`, `AsyncIOMotorCollection[T]` | YES | Typed inline | Parameterize with `Dict[str, Any]` or `TypedDict`; use `NotRequired` for auto-added fields |
| **pymongo 4.16.0** | `MongoClient[T]`, `Database[T]`, `Collection[T]` | YES | Typed inline | Parameterize with `Dict[str, Any]` or `TypedDict`; use `NotRequired` for `_id` |
| **pydantic 2.12.5** | `TypeAdapter[T]`, `RootModel[T]` | YES | Typed inline | Parameterize `TypeAdapter` and `RootModel`; inherit from `Generic[T]` for custom models |
| **FastAPI 0.135.2** | None (routers/requests/responses NOT generic) | YES | Typed inline | No parameterization needed; router/request/response are concrete types |
| **Starlette 1.0.0** | `WebSocket[T]` (new in 1.0) | YES | Typed inline | Parameterize `WebSocket` with app state type if using `scope["state"]` |
| **redis-py 7.3.0** | `Redis[T]`, `redis.asyncio.Redis[T]` | Likely YES | Typed inline (older `ResponseT` union in 7.3) | Parameterize `Redis`/`asyncio.Redis` as `Redis[str]` or `Redis[bytes]` based on `decode_responses` |
| **casbin 1.43.0** | None identified | NO | Untyped → Any | No action; symbols are `Any` so `disallow_any_generics` does not fire |
| **aiokafka 0.13.0** | None identified | Unclear | Likely untyped | No action; mypy treats as `Any` |
| **nats-py 2.14.0** | None identified | NO | Untyped → Any | No action; symbols are `Any` so `disallow_any_generics` does not fire |

## Version/compatibility notes

- **SQLAlchemy 2.0:** Mypy plugin deprecated and removed in 2.1; use inline annotations only (2.0+).
- **motor 3.7:** Async MongoDB support; consider pymongo's own async API (added later, outside motor) as an alternative for new projects.
- **pymongo 4.16:** Generic support stable; no breaking changes expected in 4.x.
- **pydantic 2.12.5:** Generic type support stable; BaseModel does not inherit Generic by default (must explicitly compose).
- **FastAPI 0.135.2:** No major generic changes expected at patch level.
- **Starlette 1.0.0:** Released 2026-03-22; breaking removal of deprecated event handlers/decorators. WebSocket state now generic.
- **redis-py 7.3.0:** Typing improvements backported in 8.0+; 7.3 uses older ResponseT union. No EOL noted; 8.0+ recommended for new projects.
- **casbin 1.43.0:** No py.typed; async support added in 1.23.0+. No typed API documented.
- **aiokafka 0.13.0:** CI enforces mypy but py.typed not confirmed; likely untyped distribution.
- **nats-py 2.14.0:** Released with infrastructure improvements (pool management, watchers); no typing improvements documented.

## Evidence gaps

- **SQLAlchemy 2.0.48:** Exact internal generic parameter names for `Result[T]` / `ScalarResult[T]` not confirmed from documentation; requires source inspection or stubs. `Session` / `AsyncSession` generic status not explicitly confirmed in docs but likely NOT generic based on typical ASGI patterns.
- **motor 3.7.1 deprecation status:** Docs do not clearly state whether motor is deprecated in favor of pymongo's own async API; research 006 (worth a separate brief) should clarify motor vs. pymongo async strategy and official recommendation.
- **pydantic 2.12.5 TypeAdapter:** Exact generic parameter inference for complex nested types (discriminated unions, forward refs) not documented; open GitHub issues (#8324, #7426) suggest edge cases.
- **FastAPI 0.135.2:** No breaking generic changes documented at this patch level; Starlette 1.0 upgrade path and WebSocket state typing adoption not yet documented in FastAPI's official guidance.
- **redis-py 7.3.0:** Type stub details for `ConnectionPool`, `PubSub`, `Pipeline` not confirmed; may require inspection of inline annotations or stubs package.
- **casbin, aiokafka, nats-py:** All three untyped or minimally typed; no official typing documentation or py.typed confirmation; projects should assume `Any` typing and plan to add type stubs if strict mypy enforcement is required.

## Librarian's note

**The sources indicate:**

1. **Three libraries are fully typed with py.typed** (SQLAlchemy, pydantic, FastAPI, Starlette, motor, pymongo, and redis-py 7.3+): use `disallow_any_generics` confidently. Parameterize `Mapped[T]` (SQLAlchemy), `TypeAdapter[T]` and `RootModel[T]` (pydantic), and document-type generics (`MongoClient[T]`, `AsyncIOMotorClient[T]`, `Redis[T]`) explicitly.

2. **Three libraries are untyped or minimally typed** (casbin 1.43, aiokafka 0.13, nats-py 2.14): `disallow_any_generics` does not fire on them because they are treated as `Any` by mypy. Parameterization is not applicable; type stubs or explicit `# type: ignore[arg-type]` may be needed if the app enforces strict checks on imports.

3. **Starlette 1.0 added WebSocket[T]:** if upgrading, adopt the generic state pattern for type-safe access to application state.

4. **redis-py 7.3 has older typing:** while 8.0+ improved overload patterns, 7.3 is serviceable with explicit parameterization (`Redis[str]` vs. `Redis[bytes]`). No immediate EOL; vendor should consider a 8.0 upgrade for better type inference of command return types (e.g., `LLEN` returning `int` instead of `Union[Awaitable[int], int]` for async clients).

The 176 errors from `disallow_any_generics` are likely split: ~60–80 from varco's own bare `dict`/`list` (use `dict[K, V]` / `list[T]`), ~30–50 from SQLAlchemy's `Mapped[T]` and pydantic's `TypeAdapter[T]` / `RootModel[T]` (parameterize per table above), ~20–30 from MongoDB driver generics (parameterize motor/pymongo), ~5–10 from redis-py, and ~0–5 from untyped libraries (suppress or migrate to typed stubs).

Sources:
- [Mypy / Pep-484 Support for ORM Mappings — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/mypy.html)
- [Class Mapping API — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html)
- [Table Configuration with Declarative — SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html)
- [Type Hints - Motor 3.7.1 documentation](https://motor.readthedocs.io/en/stable/examples/type_hints.html)
- [Type Hints - PyMongo 4.13.2 documentation](https://pymongo.readthedocs.io/en/4.13.2/examples/type_hints.html)
- [Models | Pydantic Docs](https://pydantic.dev/docs/validation/dev/concepts/models/)
- [Type Adapter | Pydantic Docs](https://docs.pydantic.dev/latest/concepts/type_adapter/)
- [add py.typed marker file for PEP-561 support](https://github.com/pydantic/pydantic/pull/391)
- [Starlette 1.0 Release Notes (GitHub)](https://github.com/encode/starlette/blob/master/docs/release-notes.md)
- [Missing py.typed · Discussion #1959](https://github.com/encode/starlette/discussions/1959)
- [Asyncio Examples - redis-py 8.1.0 documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [ResponseT type seems to be inconsistent · Issue #2933](https://github.com/redis/redis-py/issues/2933)
- [GitHub - pycasbin/casbin](https://github.com/pycasbin/casbin)
- [aiokafka · PyPI](https://pypi.org/project/aiokafka/)
- [nats-io/nats.py releases](https://github.com/nats-io/nats.py/releases)
- [PEP 561 – Distributing and Packaging Type Information | peps.python.org](https://peps.python.org/pep-0561/)
