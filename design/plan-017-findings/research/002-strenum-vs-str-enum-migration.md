# Research 002 — StrEnum vs str, Enum: Migration Viability for varco

Date: 2026-08-28 · Freshness matters: **yes** — enum behavior and tooling changed in 3.11 and continues to shift; stdlib docs reflect current (3.12/3.13) behaviour

## Question

Is it safe to migrate varco's nine `class X(str, Enum)` declarations to `StrEnum`, given that several are serialized to JSON (event payloads, HTTP responses), read from environment variables via pydantic BaseSettings, or emitted as OpenTelemetry metric/label values? What is the precise behavioural delta between the two forms on Python 3.12/3.13, and what does ruff's UP042 rule actually declare?

## Findings

### 1. Precise Behavioural Delta: str(X.MEMBER)

- **Python 3.10**: `class X(str, Enum)` with `X.MEMBER = "bar"`:
  - `str(X.MEMBER)` → `"bar"` (mixin's `__str__` used)
  - `f"{X.MEMBER}"` → `"bar"` (mixin's `__format__` used)
  
- **Python 3.11–3.13**: `class X(str, Enum)`:
  - `str(X.MEMBER)` → `"X.MEMBER"` (Enum's `__str__` used; format string becomes `repr`-like)
  - `f"{X.MEMBER}"` → `"X.MEMBER"` (breaking change from 3.10)
  - `X.MEMBER.value` → `"bar"` (unchanged)
  - **Reason**: Python 3.11 changed `Enum.__format__()` and `Enum.__str__()` for mixed-in types to return the fully qualified name — [CPython issue #100458](https://github.com/python/cpython/issues/100458), [CPython issue #93363](https://github.com/python/cpython/issues/93363), [Python 3.11 What's New](https://docs.python.org/3/whatsnew/3.11.html) ("Changed `Enum.__format__()` … for all other enums it will be the enum and member name (e.g. `Color.RED`)")

- **Python 3.11–3.13**: `class X(StrEnum)`:
  - `str(X.MEMBER)` → `"bar"` (StrEnum's `__str__` delegates to `str.__str__()`)
  - `f"{X.MEMBER}"` → `"bar"` (StrEnum's `__format__` delegates to `str.__format__()`)
  - `X.MEMBER.value` → `"bar"` (unchanged)
  - **Reason**: StrEnum was added in Python 3.11 specifically to restore the 3.10 behavior for the common pattern. — [Python 3.11 What's New](https://docs.python.org/3/whatsnew/3.11.html), [enum module docs (3.11+)](https://docs.python.org/3/library/enum.html)

### 2. repr() Behavior

- **`class X(str, Enum)` on 3.11+**: `repr(X.MEMBER)` → `"<X.MEMBER: 'bar'>"` (Enum's repr)
- **`StrEnum` on 3.11+**: `repr(X.MEMBER)` → `"<X.MEMBER: 'bar'>"` (same; StrEnum inherits from Enum)

### 3. Equality and Dict Key Use

- **Both forms**: `X.MEMBER == "bar"` → `True` (because both inherit from str)
- **Both forms**: `{X.MEMBER: "value"}[X.MEMBER]` → works (both are hashable, both equal their str value)
- **No observable difference at this boundary**

### 4. logging Module %s Formatting

- **`class X(str, Enum)` on 3.11+**: `logging.info("%s", X.MEMBER)` → `"X.MEMBER"` (uses `__str__`)
- **`StrEnum` on 3.11+**: `logging.info("%s", X.MEMBER)` → `"bar"` (uses `__str__`)

### 5. json.dumps() Output (Stdlib json Module)

- **Both forms**: `json.dumps({"key": X.MEMBER})` → `{"key": "bar"}`
  - **Why**: Python's stdlib `json.JSONEncoder` does an `isinstance(obj, str)` check — both inherit from str, so both serialize as JSON strings using their string value. — [Python json module docs](https://docs.python.org/3/library/json.html) (encoder treats str subclasses as strings)
  - **No observable difference at this boundary**

### 6. StrEnum Constraints

- **All member values must be strings** — declaring `X.MEMBER = 1` raises `TypeError` at enum creation time. — [enum module docs](https://docs.python.org/3/library/enum.html)
- **`auto()` generates lowercased member name** — `class X(StrEnum): MEMBER = auto()` produces `X.MEMBER = "member"`. All nine varco sites explicitly assign values (e.g. `UP = "up"`, `FIRE_FORGET = "FIRE_FORGET"`), so this is not a concern.

---

## Serialization Boundaries — What Changes on Wire

| Boundary | `class X(str, Enum)` on 3.11+ | `StrEnum` on 3.11+ | Observable Change? |
|---|---|---|---|
| **JSON response body** (varco_core.health.HealthStatus in HTTP 200) | `f"{HealthStatus.UP}"` → `"HealthStatus.UP"` in JSON | `f"{HealthStatus.UP}"` → `"UP"` in JSON | **YES — wire format changes** |
| **Event payload** (varco_core.event.base.ErrorPolicy in Kafka/Redis) | Member name emitted as `"ErrorPolicy.RETRY"` | Member name emitted as `"RETRY"` | **YES — wire format changes** |
| **OTel attribute** (varco_core.resilience.CircuitBreaker state label) | Label value sent as `"CircuitState.OPEN"` | Label value sent as `"OPEN"` | **YES — metric label changes** |
| **Environment variable parsing** (pydantic BaseSettings) | Both accept `"UP"` as input, both deserialize to the enum member | No difference | **NO** |
| **Dict key in code** | `cache[X.MEMBER]` works identically; same hashability and equality | No difference | **NO** |
| **repr() in logs/tracebacks** | `<HealthStatus.UP: 'up'>` | `<HealthStatus.UP: 'up'>` | **NO** |

**Critical**: The first three rows show real, breaking changes to what leaves the varco process. Any downstream consumer (a monitoring dashboard reading HealthStatus labels, an event stream consumer, an HTTP client parsing responses) **will see different values** if varco migrates from `(str, Enum)` to `StrEnum` without coordinating with consumers.

---

## Pydantic v2 Behaviour

- **BaseSettings field parsing**: Both forms accept the same inputs from environment variables — e.g. `DELIVERY_SEMANTICS="GUARANTEED"` deserializes to `KafkaDeliverySemantics.GUARANTEED` for both. — [Pydantic BaseSettings docs](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/), confirmed by pydantic's enum discussion thread [#6466](https://github.com/pydantic/pydantic/discussions/6466)
- **JSON serialization (`model_dump(mode="json")`)**:
  - Default (both forms): Pydantic v2 serializes as the enum member's **value** (the string), not the name — [Pydantic configuration docs](https://docs.pydantic.dev/latest/api/config/)
  - Both `HealthStatus.UP` and `StrEnum` version serialize to the string value `"up"` in JSON
  - **No difference at this boundary with pydantic**
- **Field validation**: Both forms validate identically in pydantic models

**Key caveat**: Pydantic's default is to serialize by value, but this is independent of whether you use `(str, Enum)` or `StrEnum` — the choice affects only **unmediated** `str()` / `f-string` formatting in Python code.

---

## Ruff's UP042 Rule Position

- **Rule**: Detects `class X(str, Enum)` and suggests migrating to `StrEnum` — [ruff UP042 docs](https://docs.astral.sh/ruff/rules/replace-str-enum/)
- **Safety level**: **UNSAFE** — Ruff explicitly documents this as an unsafe fix that may change runtime behaviour
- **Status**: Not in preview; available in stable ruff
- **Exact statement from ruff docs**: "Python 3.11 changed how these enums format. In Python 3.10, `f"{Foo.BAR}"` would output `bar`, but in Python 3.11, the same code outputs `Foo.BAR`. When migrating to `enum.StrEnum`, the formatted representation reverts to the Python 3.10 behavior (returning just `bar`). This means adopting the fix will alter how your enum values display."

Ruff is not claiming the fix is wrong — only that it changes observable behaviour, and developers must evaluate if that change is acceptable for their codebase.

---

## Authoritative Guidance on Acceptability

1. **CPython position**: StrEnum was added to Python 3.11 **specifically to restore the 3.10 formatting behaviour** — the fact that it exists and is documented as the "recommended" approach for string enums signals that the Python core team considers `(str, Enum)` a legacy pattern, not a first-class choice. — [Python 3.11 What's New](https://docs.python.org/3/whatsnew/3.11.html)

2. **No ecosystem consensus that `(str, Enum)` remains acceptable as-is**: The addition of StrEnum in 3.11 and Ruff's UP042 rule both signal a community/stdlib migration toward the new form. However, there is **no published statement declaring `(str, Enum)` incorrect or deprecated** — only that StrEnum is the newer, cleaner idiom.

3. **Production safety**: A codebase that never f-strings or `str()`-formats these enums is unaffected by the 3.11 change. Varco currently does **rely on** this formatting at the boundaries documented above (HTTP response bodies, event payloads, OTel metrics), meaning migration would have observable wire-level effects.

---

## Version/Compatibility Notes

- **Python 3.10**: StrEnum does not exist; `(str, Enum)` is the only option
- **Python 3.11+**: Both exist; `StrEnum` is documented as preferred for new code. Varco targets 3.12 and 3.13, so both are available.
- **Ruff version**: UP042 has been stable since at least 0.3.6 (Feb 2026). No planned deprecation announced.
- **Pydantic v2**: No announced changes to enum serialization behavior; both forms work identically with BaseSettings and JSON serialization

---

## Evidence Gaps

- **No documentation of a varco migration plan**: The nine sites are currently suppressed with `# noqa: UP042`, but no decision brief or ADR exists spelling out whether varco's position is "acceptable legacy pattern" or "planned migration"
- **No benchmarked performance delta**: Unlikely to matter, but unverified
- **No downstream consumer coordination plan**: If varco changes wire formats (health check responses, event payloads, OTel labels), downstream services and dashboards must be updated in lock-step — this is a coordination boundary, not a technical one

---

## Librarian's Note

**What the sources indicate:**

The migration from `(str, Enum)` to `StrEnum` is technically sound **only if varco accepts breaking changes to wire formats** — JSON response bodies, event payloads, and OTel metric labels will all emit different string values. The stdlib and Ruff both signal that StrEnum is the modern idiom (Python 3.11+), but no authoritative source declares the old form "wrong" or "deprecated."

The blocking decision is **coordination scope**, not correctness: if varco's HTTP clients, event consumers, or monitoring dashboards have hardcoded expectations about the string values currently emitted (e.g. `"CircuitState.OPEN"` in metric labels), migration requires updating those parsers. If varco controls all downstream consumers or can coordinate a dual-release (broadcast the new format, then migrate), the migration is safe. If not, it is breaking.

Ruff's "unsafe fix" label accurately reflects this — the fix is not wrong, only operationally risky without coordination.

