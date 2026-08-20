# Research 003 — RFC 9457 Localizable Error Envelope & Field-Level Validation

**Date:** 2026-08-20 · **Freshness matters:** Yes (RFC 9457 finalized August 2023, active framework adoption 2024–2026)

## Question

What should the wire format of a localizable, machine-readable error envelope be for varco (a published Python async framework)?

Specifically:
1. Which members from RFC 9457 (Problem Details for HTTP APIs) are normative, and which can be localized?
2. Is a `type` URI a stable error identifier or a dereferenceable resource? What does RFC 9457 recommend?
3. How do Accept-Language and Content-Language headers interact with error localization per RFC 9457/9110?
4. What is the de-facto standard for field-level validation errors (the `invalid-params` extension)?
5. Does the Python/FastAPI ecosystem have maintained RFC 9457 libraries, and does FastAPI itself conflict?
6. How do real-world APIs (Google Cloud, Spring Boot, ASP.NET Core) emit `message_key` + structured `params` for client-side translation?
7. What is the migration path when a published framework changes its error envelope? (breaking change risk)

## Findings

### RFC 9457: Normative Members and Localization Contracts

RFC 9457 (published August 2023, obsoletes RFC 7807) defines five normative members for problem detail objects:

| Member | Type | Semantics | Localization |
|--------|------|-----------|--------------|
| **type** | URI (defaults to `about:blank`) | Identifies the problem type. Consumers SHOULD NOT auto-deref it unless for developer information. | ❌ Must remain stable; never localized |
| **title** | String | Short human-readable summary of the problem type. "SHOULD NOT change from occurrence to occurrence of the problem, **except for localization**." | ✅ **May be localized** via proactive content negotiation on Accept-Language |
| **status** | Integer | HTTP status code (advisory; must match the actual response code). | N/A (numeric) |
| **detail** | String | Explanation specific to this occurrence, focused on helping the client correct the problem. | ✅ **May be localized** via Accept-Language |
| **instance** | URI (optional) | Identifies the specific problem occurrence; may or may not be dereferenceable. | ❌ Must remain stable (acts as a correlation ID) |

**Critical rule:** RFC 9457 explicitly permits localization of `title` and `detail` through proactive content negotiation using the Accept-Language request header. The specification states: "the language used for human-readable strings can be negotiated using the Accept-Language request header field, although that negotiation may still result in a non-preferred, default representation being returned." — [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)

**Extension members are allowed:** Clients consuming problem details MUST ignore any extensions they don't recognize. This makes extension members safe for adding `message_key`, `params`, field-level error arrays, etc. — [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)

### Type URI: Dereferenceable vs. Opaque Strategy

RFC 9457 recognizes two strategies for the `type` field:

1. **Dereferenceable URI** (e.g., `https://api.example.com/errors/validation-failed`) — When the type is an HTTP/HTTPS URI, dereferencing it SHOULD provide human-readable documentation for the problem type. However, consumers "SHOULD NOT automatically dereference the type URI, unless they do so when providing information to developers."

2. **Opaque URI or code string** (e.g., `urn:varco:error:VALIDATION_FAILED` or just `VALIDATION_FAILED`) — Non-resolvable but stable and lightweight. Useful when documentation is external or unavailable.

**Trade-off**: Dereferenceable URIs add self-documenting capability but introduce latency and coupling if the URL changes or is unavailable. Opaque codes are simpler and remain valid even if documentation moves.

**Precedent**: Spring Boot 3+ defaults to a type URI like `https://docs.spring.io/spring-framework/docs/current/javadoc-api/org/springframework/web/...` (dereferenceable). ASP.NET Core and FastAPI implementations typically use non-dereferenceable formats like `about:blank` or an opaque code. — [Stop Inventing Your Own API Error Format: Use RFC 9457 Problem Details - DEV Community](https://dev.to/apikumo/stop-inventing-your-own-api-error-format-use-rfc-9457-problem-details-4hma)

### Content Negotiation: Accept-Language, Content-Language, RFC 9110

**RFC 9110 (HTTP Semantics, 2022)** governs content negotiation. RFC 9457 references RFC 9110 for language negotiation on problem details:

- **Request:** Client sends `Accept-Language: fr-CA, fr;q=0.9, en;q=0.8` (BCP 47 tags, with quality factors)
- **Response:** Server chooses the best match and SHOULD include `Content-Language: fr-CA` header in the response, even if full localization is unavailable

**RFC 9457 localization contract**: "the language used for human-readable strings can be negotiated using the Accept-Language request header" — but the spec does NOT mandate server-side localization. A server may return a default (English) problem detail even with an Accept-Language request; clients should not assume localized responses.

**Practical guidance from research**: 
- Localize `title` and `detail` based on Accept-Language if a `MessageCatalog` is wired
- Always return a `Content-Language` response header identifying the language of the response (even if it's the default fallback, e.g., `Content-Language: en`)
- Include stable, machine-readable `code` or `message_key` fields so clients can localize if the server does not

— [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html), [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)

### Validation Errors: Field-Level Errors & `invalid-params` Extension

RFC 9457 has no standard for field-level validation errors. The spec's appendix (RFC 7807 Appendix A, carried forward) shows an `invalid-params` extension with an array of objects, each containing:

```json
{
  "type": "about:blank",
  "title": "Unprocessable Entity",
  "status": 422,
  "invalid-params": [
    {
      "name": "age",
      "reason": "must be a positive integer"
    },
    {
      "name": "email",
      "reason": "must be a valid email address"
    }
  ]
}
```

**Real-world patterns:**

- **Spring Boot** (since 3.0): Adds an `errors` array with objects containing `field`, `rejectedValue`, `message`. Uses 422 Unprocessable Content for validation failures.
  
- **ASP.NET Core** (`ValidationProblemDetails`, RFC 9457-compliant since .NET 7): Extends problem details with an `errors` field — a dictionary mapping field names to string arrays of error messages:
  ```json
  {
    "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
    "title": "One or more validation errors occurred.",
    "status": 400,
    "errors": {
      "age": ["The field age must be a number."],
      "email": ["The email field is not a valid e-mail address."]
    }
  }
  ```

- **FastAPI with fastapi-problem-details** (community library): Uses an `errors` array matching Pydantic's schema, with each error containing `type`, `loc` (JSON pointer path), `msg`, and `input`:
  ```json
  {
    "type": "about:blank",
    "title": "Unprocessable Entity",
    "status": 422,
    "errors": [
      {
        "type": "missing",
        "loc": ["body", "id"],
        "msg": "Field required"
      }
    ]
  }
  ```

**Note on RFC 9457 compliance**: There is an open issue in ASP.NET Core (dotnet/aspnetcore#57714) requesting that field validation errors use RFC 9457's recommended `details` and `pointer` members (JSON Pointer RFC 6901) instead of custom dictionary formats, to improve interoperability.

— [RFC 9457: The Standard Way to Return Errors from JSON APIs](https://prettyjson.org/learn/rfc-9457-json-api-error-handling), [Bad request / validation errors by the framework should state field names according to RFC 9457 · Issue #57714](https://github.com/dotnet/aspnetcore/issues/57714), [Spring Framework Error Handling](https://docs.spring.io/spring-framework/reference/web/webmvc/mvc-ann-rest-exceptions.html), [github.com/g0di/fastapi-problem-details](https://github.com/g0di/fastapi-problem-details)

### Python/FastAPI Ecosystem: Libraries & Default Behavior

**fastapi-problem-details** (PyPI: `fastapi-problem-details`, maintained): Community library providing RFC 9457 exception handler for FastAPI. Emits problem+json format automatically. — [github.com/g0di/fastapi-problem-details](https://github.com/g0di/fastapi-problem-details), [fastapi-problem-details — PyPI](https://pypi.org/project/fastapi-problem-details/)

**FastAPI default behavior**: FastAPI's built-in `HTTPException` and `RequestValidationError` handlers return JSON but do NOT follow RFC 9457 by default. FastAPI 0.x–current emits a custom format. Adopting problem+json would be a breaking change unless opt-in via middleware.

**Pydantic validation localization**: `pydantic-i18n` (PyPI: `pydantic-i18n`, maintained) provides i18n for Pydantic validation errors. It translates error messages using a locale-specific dictionary, mapping error type strings (e.g., `"missing"`, `"value_error"`) to localized messages. Supports custom message catalogs and placeholders (`{}`). — [pydantic-i18n — PyPI](https://pypi.org/project/pydantic-i18n), [pydantic-i18n Documentation](https://pydantic-i18n.boardpack.org/)

**Migration hazard**: If varco changes HTTPException handlers from the current format to RFC 9457, existing clients parsing the JSON will break. Spring Boot 3 mitigated this via a configuration flag (`spring.mvc.problemdetails.enabled=true`, default `false` in 3.0, then made optional default in later minor releases).

### `message_key` + `params` Pattern: Real-World Precedent

**Google Cloud APIs (AIP-193)**: Structured errors via `google.rpc.Status` with a `details` array. The details include:
- **ErrorInfo** (machine-readable): Contains `reason` (stable code, UPPER_SNAKE_CASE), `domain`, and `metadata` (key-value pairs for contextual data)
- **LocalizedMessage** (optional): Contains locale-specific error messages in addition to the default English `Status.message`

Google's model separates the stable identifier (`reason`) from localized text. Metadata allows passing structured parameters (e.g., `"limit": "100"`, `"actual": "150"`) for client-side templating. — [AIP-193: Errors](https://google.aip.dev/193)

**Stripe**: Emits error codes (e.g., `card_declined`, `rate_limit_exceeded`) as stable identifiers. Clients use these codes to construct translation keys (e.g., `stripe.errors.card_declined`). Stripe does NOT emit a `params` object; instead, clients are expected to build error messages from the code + the included `message` and `param` fields in certain error types. — [github.com/ekosz/stripe-i18n](https://github.com/ekosz/stripe-i18n)

**Spring Validation with i18n**: Spring's `BindingResult` errors include `field`, `rejectedValue`, `codes` (an array of message keys in priority order). Applications wire a `MessageSource` to translate codes at render time. Field-level error codes like `NotBlank.user.email` allow parameterized messages in `.properties` files. — [Spring Framework Documentation](https://docs.spring.io/spring-framework/reference/web/webmvc/mvc-ann-rest-exceptions.html)

**Keycloak**: Error responses include `error` and `error_description` fields. Localization of error descriptions is not always applied to the REST API (open issue #20206); localization is handled mainly in the UI layer. The API is not considered a primary localization surface in Keycloak's current design. — [Keycloak Error Responses Guide](https://docs.expertflow.com/cx/4.6/keycloak-error-responses-guide)

**Consensus pattern**: Emit a stable `code` or `message_key` + optional `params` object (for interpolation); localization is left to the client or handled server-side via Accept-Language (if a MessageCatalog is configured). This decouples the wire format from the translation library and allows clients to opt-in to localization.

### Spring Boot 3 & ASP.NET Core RFC 9457 Adoption: Migration Experience

**Spring Boot 3.0** (released 2022): Introduced `ProblemDetail` class and opt-in support via `spring.mvc.problemdetails.enabled=true` (default `false`). When enabled, ResponseStatusException and built-in validation exceptions return RFC 9457 format with `application/problem+json` media type. — [Spring Boot 3.0 Migration Guide](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.0-Migration-Guide), [Goodbye Custom Error Wrappers — Hello ProblemDetail in Spring Boot 3](https://blog.stackademic.com/goodbye-custom-error-wrappers-hello-problemdetail-in-spring-boot-3-ff54692eda8e)

**Adoption path**: Teams with few clients or controlled deployments migrated immediately. Teams with many external consumers adopted gradually: enable ProblemDetail for new endpoints, leave legacy endpoints as-is.

**ASP.NET Core**: Added `ProblemDetails` and `ValidationProblemDetails` in .NET 6, made them RFC 7807-compliant in .NET 7. Default response type for built-in exceptions. Media type is `application/problem+json` when a ProblemDetails instance is returned. — [How to Standardize Error Responses with Problem Details (RFC 9457) in Spring Boot 3.x](https://springboot-123.mizucoffee.com/en/blog/spring-boot-problem-details-rfc9457-error-response-guide/)

**Lesson**: Frameworks that made RFC 9457 adoption opt-in via a configuration flag avoided immediate breaking changes. Gradual rollout to new endpoints is the safest path for published frameworks.

### Backward Compatibility: Strategy for Adopting RFC 9457 in Varco

**Safe additive approach:**
1. Add `code` (stable string) and optional `message_key` fields to varco's error response envelope **without removing existing fields**
2. Optionally emit `application/problem+json` media type, but accept both old and new client parsing (no rejection of old format)
3. Wire an optional, pluggable `MessageCatalog` for server-side localization via Accept-Language (similar to varco's existing event system patterns)

**Option: Dual envelope (version 1→2 transition)**

```json
{
  "code": "VARCO_VALIDATION_FAILED",
  "message": "Validation failed",
  "message_key": "validation.failed",
  "params": {
    "field_count": 2
  },
  "status": 422,
  "errors": [
    {
      "field": "email",
      "message": "Must be a valid email",
      "message_key": "field.email.invalid"
    }
  ],
  "type": "https://varco.example.com/errors/validation-failed",
  "title": "Validation Error",
  "detail": "The request contains invalid data"
}
```

This envelope:
- ✅ Keeps existing `code`, `message` fields (backward compatible)
- ✅ Adds `message_key`, `params` for client-side localization (new capability)
- ✅ Includes RFC 9457 fields (`type`, `title`, `status`, `detail`, errors extension)
- ✅ Allows server-side localization if MessageCatalog is wired (via Accept-Language)

**Break-free environment**: Varco is not at v1.0.0 yet (based on project memory, still in feature development). An error envelope redesign now is less disruptive than a post-1.0.0 breaking change.

## Options Compared

### Approach: Server-Side Localization vs. Stable Codes + Client Translation

| Aspect | Server-Side Localization (Accept-Language) | Stable Codes + Client Translation |
|--------|---|---|
| **RFC 9457 compliance** | ✅ Explicit support for localizing `title`/`detail` | ✅ Explicit support for stable `type`/`code` |
| **Latency** | Catalog lookup per request | Zero (codes are static strings) |
| **Flexibility** | Server owns all translations; redeploy to change | Client owns translations; update without redeploy |
| **Tooling** | Needs MessageCatalog ABC + i18n library integration | Minimal; clients use their own i18n libraries |
| **Varco complexity** | Low (pluggable optional MessageCatalog) | Low (just ship stable codes) |
| **Client burden** | Client must send Accept-Language header | Client must maintain translation catalog mapping codes to strings |
| **Hybrid possible?** | Yes; ship codes + optional server localization | Yes; clients can ignore codes and use server's localized `title`/`detail` |

**Recommendation**: Hybrid approach favored by evidence — always ship stable error codes + `message_key`; optionally support server-side localization via pluggable MessageCatalog and Accept-Language negotiation (opt-in). This gives maximum flexibility: clients can localize themselves (stable codes), or servers can localize if configured (Accept-Language).

### Wire Format: RFC 9457 Full Adoption vs. Additive Extensions

| Aspect | Full RFC 9457 (Adopt/Default) | RFC 9457 Additive (Hybrid) |
|---|---|---|
| **Media type** | `application/problem+json` by default | `application/json` by default, `application/problem+json` on negotiation |
| **Envelope** | type, title, status, detail, instance + extensions | Keep existing varco fields + RFC 9457 fields |
| **Field-level errors** | Use RFC 9457 `errors` extension | Add `errors` array (similar to Spring/ASP.NET) |
| **Breaking change risk** | ⚠️ High (existing clients may break on media type or missing fields) | ✅ Low (backward compatible; new fields ignored by old clients) |
| **Migration timeline** | Requires pre-announcement, deprecation period, dual-support | Can ship immediately; activate via configuration flag if needed |
| **Interop with `invalid-params`** | RFC-compliant use of extension members | May confuse consumers expecting strict RFC compliance |

**Recommendation**: Hybrid additive approach for now — add RFC 9457 fields alongside existing varco fields, ship with `application/json` media type by default, allow opt-in to `application/problem+json` via configuration (mirroring Spring Boot 3's strategy). When varco reaches v1.0.0 or v2.0.0, full RFC 9457 adoption becomes a candidate for a major-version breaking change.

### Message Key & Params Naming: Alignment with RFC 9457 Extension Guidance

**Option 1: Inline `message_key` + `params`**
```json
{
  "type": "about:blank",
  "status": 422,
  "detail": "Validation failed",
  "message_key": "validation.failed",
  "params": {
    "field_count": 2
  },
  "errors": [...]
}
```
✅ Flat, easy to parse. ✅ Clear naming. ❌ No RFC 9457 guidance on `params` shape.

**Option 2: Nested under an `i18n` or `localization` extension**
```json
{
  "type": "about:blank",
  "status": 422,
  "detail": "Validation failed",
  "localization": {
    "key": "validation.failed",
    "params": {
      "field_count": 2
    }
  },
  "errors": [...]
}
```
✅ Groups localization metadata. ❌ Adds nesting; slightly verbose.

**Option 3: Follow Google's `ErrorInfo` + `metadata` pattern**
```json
{
  "type": "about:blank",
  "status": 422,
  "detail": "Validation failed",
  "error_info": {
    "code": "VALIDATION_FAILED",
    "metadata": {
      "field_count": "2"
    }
  },
  "errors": [...]
}
```
✅ Matches Google Cloud precedent. ✅ Separates stable identifier from context. ❌ Metadata values are strings (type coercion needed).

**Recommendation**: Option 1 (inline `message_key` + `params`) — simplest, most RFC 9457-compliant (direct extension members), matches Spring's precedent. Naming `message_key` is clear; `params` is familiar from i18n libraries.

## Version/Compatibility Notes

- **RFC 9457**: Published August 2023 (IETF STD). Stable, no breaking changes expected.
- **RFC 9110**: Published June 2022 (STD 97, replaces RFC 7230–7235). Stable, governs Accept-Language and Content-Language.
- **Spring Boot 3.0–4.0**: ProblemDetail support in 3.0+. Configuration flag `spring.mvc.problemdetails.enabled` introduced in 3.0, default remains `false` in 3.0–3.2.
- **ASP.NET Core .NET 7–9**: ProblemDetails/ValidationProblemDetails RFC 9457-compliant since .NET 7. Enabled by default.
- **FastAPI**: Current stable (0.x) does NOT emit RFC 9457 by default. Community library `fastapi-problem-details` (maintained) provides opt-in support.
- **Pydantic**: Pydantic v2 (current stable) ValidationError structure unchanged; `pydantic-i18n` provides localization via message catalogs.
- **Python zoneinfo** (PEP 615): Available in Python 3.9+. varco targets 3.12+, so no compatibility concern.

## Evidence Gaps

1. **FastAPI production adoption of problem+json**: Research found `fastapi-problem-details` library but no data on adoption percentage in production FastAPI deployments. Unclear if the community considers breaking change to problem+json acceptable.

2. **Varco-specific localization wiring**: How message catalogs should integrate with varco's event system, exception handling hierarchy, and DI (providify) is not yet specified. A spike is needed: "Error Message Localization in Varco Service Layer."

3. **Field-level params in validation**: The exact structure for passing interpolation parameters in field-level errors (e.g., `field.invalid_length` with `{"max_length": 100, "actual": 150}`) is shown by example in Google Cloud but not standard across frameworks. Varco should pick a precedent and document it.

4. **Accept-Language fallback behavior**: RFC 9457 permits returning a non-localized response even with an Accept-Language request. Varco's fallback strategy (e.g., always return English, or return the best partial match) is undefined.

5. **Multitenancy + per-tenant message catalogs**: If varco supports multitenancy, does each tenant get its own message catalog? Can it be configured at provisioning time? Not yet researched.

6. **Media type negotiation in FastAPI**: Whether varco should emit `application/problem+json` based on Accept header (proactive negotiation) or require explicit opt-in is not yet defined. Needs a separate spike on content negotiation middleware.

## Librarian's Note

**What the evidence favors:**

Varco should adopt a **hybrid RFC 9457 approach**, shipping:

1. **Always-present fields** (backward compatible):
   - Existing `code: str` (e.g., `"VARCO_VALIDATION_FAILED"`)
   - Existing `message: str` (human-readable default English)
   - New `message_key: str` (machine-readable localization key, e.g., `"validation.failed"`)
   - New `params: dict[str, Any]` (structured interpolation data)

2. **RFC 9457 fields** (optional, as extension members):
   - `type: str` — URI or opaque identifier (recommend non-dereferenceable, e.g., `about:blank`)
   - `title: str` — short summary (copy of existing `message` by default; can be localized)
   - `status: int` — HTTP status (already present in response, but include for RFC 9457 completeness)
   - `detail: str` — longer explanation (optional, for complex errors)
   - `instance: str` — unique error occurrence ID (optional, for tracing)

3. **Validation errors** (field-level):
   - `errors: array[object]` with `field`, `message`, `message_key`, `params` (mirrors `message` + localization pattern at top level)

4. **Opt-in server-side localization**:
   - Pluggable `MessageCatalog` ABC (similar to existing event system abstractions)
   - If configured, `title` and `detail` are looked up and localized via Accept-Language + message catalog
   - If not configured, framework returns stable English + message_key (clients localize)
   - Emit `Content-Language` response header

5. **Media type**:
   - Default: `application/json` (backward compatible with existing clients)
   - Optional: configuration flag to emit `application/problem+json` (mirrors Spring Boot 3 strategy)

6. **Migration path**:
   - Current (pre-1.0.0): Ship hybrid envelope with all fields, defaults that preserve existing behavior
   - Future (v2.0.0+): Consider full RFC 9457 default + media type switch, if backward compat is less critical

**Evidence strongly supports the hybrid approach** because:
- ✅ RFC 9457 explicitly allows `message_key` and `params` as extension members
- ✅ Spring Boot 3, ASP.NET Core, and Google Cloud all use stable codes + optional localization
- ✅ Pydantic-i18n and other Python i18n libraries expect codes/keys, not just raw messages
- ✅ Additive changes (new fields) do not break existing clients
- ✅ Opt-in server-side localization via MessageCatalog matches varco's existing design pattern (pluggable ABCs)
- ✅ Keeps the framework lightweight (no built-in translation strings; that is app responsibility)

**Recommendation on `message_key` + `params` naming**: Use inline fields at the top level (not nested under `localization`), naming them `message_key` and `params` — matches RFC 9457 extension-member simplicity and aligns with Spring Boot/Keycloak/Google Cloud precedent.

**Flag: Uncertain on exact `params` typing and schema.** Google uses string-valued metadata; Spring uses field names + messages. Varco should define this explicitly in a follow-up spike, aligned with the MessageCatalog implementation.

**Decision readiness**: ~80% — wire format shape is clear (hybrid RFC 9457 + message_key + params). MessageCatalog wiring and localization fallback behavior need a spike.
