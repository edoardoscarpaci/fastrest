# Plan 031 — Outbound webhooks (D4)

Covers the single 🟡 **should** row **D4** of BACKLOG's *"3.1 — API surface & interop (discover,
2026-09-04)"* cycle: subscription registry, signing, retry into the existing DLQ, replay from the
admin surface.

## Scope and siblings

One of four plans covering that cycle; see plan 029's *Scope and siblings* table. D4 gets its own
plan because it is **the largest genuinely-new surface area in the cycle** (`BACKLOG.md`, D4 row)
— a new entity, two repositories, a dispatcher, a signing scheme, an SSRF-hardened HTTP path, and
an admin surface.

It is independent of 029, 030 and 032. Its only cross-reference is documentary: consumer-side
idempotency guidance points at plan 029's `Idempotency-Key` middleware as the recommended way for
a *receiver* to deduplicate, which is advice, not an import.

**Research brief backing this plan:** `design/research/005-idempotency-webhooks-and-cloudevents.md`
§2 and its "Webhook Delivery Semantics & SSRF Protection" section.

## Goal

An application registers an endpoint against an event type and varco delivers signed, retried,
replayable HTTP callbacks to it — assembled from the outbox, DLQ, retry-policy and redrive
machinery varco already ships, with no new reliability primitive invented.

## Non-goals

- **No inbound webhook receiving.** Verifying someone else's webhook is the mirror problem and a
  separate row. This plan is outbound only.
- **No new reliability machinery.** The case for D4 is that varco "already ships outbox, DLQ, retry
  policy, and redrive — a `WebhookDispatcher` is assembly over parts that exist"
  (`BACKLOG.md`, D4 row). If a phase here starts inventing a retry model, that is the signal it has
  gone wrong.
- **No webhook UI.** Admin is a REST surface behind `mount_*`, consistent with RD-9.
- **No fan-out to thousands of endpoints per event.** Correct delivery for a normal SaaS
  subscription count. High-fan-out sharding is parked.
- **No exactly-once delivery.** Nobody ships it (brief 005 §2: *"no vendor can ship exactly-once"*).
  At-least-once plus documented consumer-side idempotency.

---

## Design

### Phase order

```
P0  D4a  🟡 M  WebhookSubscription entity + repositories + SA migration
P1  D4b  🟡 M  Signing — Standard Webhooks default, RFC 9421 optional
P2  D4c  🟡 M  WebhookDispatcher — SSRF guard, retry, DLQ on exhaustion
P3  D4d  🟡 S  Admin surface + replay + endpoint disablement
```

**P2 carries the security-critical work.** If the plan is cut short, it must not be cut between P2
and its SSRF tests.

### §D-D4-home — `varco_core.webhook` for everything portable

`varco_core/varco_core/webhook/` holds the entity, the store ABC, the signer ABC and its
implementations, and the dispatcher. `varco_sa`/`varco_beanie` hold repositories.
`varco_fastapi/varco_fastapi/webhook/` holds only the admin mount.

Rationale: the same seam rule as `varco_core.tls` and `varco_core.tenancy`. A webhook dispatcher is
not FastAPI-specific — a job runner or a CLI can drive one — so it must not live where only a
FastAPI app can reach it.

⚠️ **The dispatcher must not hold `AbstractEventBus`.** CLAUDE.md's rule allows exactly four
exceptions (`OutboxRelay`, `EventConsumer.register_to()`, `DlqRedriver`, and services via
`AbstractEventProducer`). The dispatcher is driven **as an `EventConsumer`** — it subscribes with
`@listen` and is wired from `@PostConstruct` via `register_to()`, which is the sanctioned path and
gives retry/DLQ integration for free rather than by reimplementation.

### §D-D4-signing — Standard Webhooks by default; RFC 9421 opt-in. This is the load-bearing call.

Brief 005 §2 is decisive and contradicts the naive reading of the backlog row (which names
"HMAC/RFC 9421 signing" as if they were one thing):

> **RFC 9421 interoperability gap**: A consumer expecting Standard Webhooks headers will not
> understand RFC 9421 `Signature-Input` / `Signature`; shipping RFC 9421 alone leaves naive
> consumers unable to verify.

and

> **Real-world consensus**: Standard Webhooks and Stripe's simpler scheme are more widely adopted
> than RFC 9421 as of 2026.

A webhook signature is only worth what the *receiver* can verify. A receiver is typically someone
else's application, often not written in Python, frequently using an off-the-shelf verification
snippet. Shipping only the standards-purest option would produce signatures nobody checks — the
worst security outcome, because it looks signed.

**Decision: a `WebhookSigner` ABC with two implementations.**

| Implementation | Default | Headers | Notes |
|---|---|---|---|
| `StandardWebhooksSigner` | ✅ **yes** | `webhook-id`, `webhook-timestamp`, `webhook-signature` | HMAC-SHA256 over `msg_id.timestamp.payload` (brief 005 §2). Multiple space-delimited signatures give zero-downtime rotation natively |
| `Rfc9421Signer` | opt-in | `Signature-Input`, `Signature`, `Content-Digest` | Covers `@method`, `@target-uri`, `@authority`, `content-digest`; `created`/`keyid`/`alg` params; `hmac-sha256` to start (brief 005 §2's registered algorithms) |

DESIGN: Standard Webhooks default, RFC 9421 available, ABC between them
  ✅ Default signature is verifiable by every consumer using an off-the-shelf library, which is the
     entire point of signing.
  ✅ Key rotation is native to the default: Standard Webhooks carries multiple space-delimited
     signatures and the consumer accepts any (brief 005 §2). Stripe's and GitHub's schemes have no
     documented rotation story — a real reason to prefer Standard Webhooks over Stripe's shape.
  ✅ RFC 9421 is there for consumers who require a Standards-Track scheme, without imposing it.
  ✅ The ABC means a third scheme (a consumer's bespoke format) is an implementation, not a fork.
  ❌ Two signing paths to test and document. Accepted — this is the interop reality, not our
     complication.
  ❌ RFC 9421 needs RFC 9530 `Content-Digest` alongside it (brief 005 §2: *"required alongside for
     complete content-integrity protection"*), so the opt-in path is meaningfully more code.
  Rejected — **RFC 9421 only**: ❌ brief 005 §2's interoperability gap; signatures nobody verifies.
  Rejected — **Stripe's `t=,v1=` shape as the default**: ❌ same HMAC strength, but rotation is
  undocumented/single-key (brief 005 §2's table) and it is one vendor's format rather than a
  published spec.

**Replay protection**: sign a timestamp and reject outside a tolerance window. Default **300
seconds** — the value brief 005 §2 records as Stripe's observed behaviour and as the common
Standard Webhooks implementation choice; the specs themselves leave the width unspecified, and the
docs must say the default is convention, not normative. Signing the canonical concatenated form
(`id.timestamp.payload`) rather than a hand-built string is what prevents extension attacks
(brief 005 §2).

**Secrets** are per-subscription, stored encrypted at rest via varco's existing
`FieldEncryptor`/`EncryptionKeyManager` — not a new crypto path. Multiple active secrets per
subscription support rotation; all active secrets sign, the consumer accepts any.

### §D-D4-ssrf — the security-critical decision, and it is an allowlist-first design

A webhook URL is **user-supplied input that the server then fetches**. That is SSRF by
construction. Brief 005 §2 names the requirements: block private ranges (`10/8`, `172.16/12`,
`192.168/16`, `127/8`, `169.254/16` including the **AWS metadata endpoint `169.254.169.254`**),
resolve the hostname and validate the *resolved IP* before connecting, and re-resolve or pin at
connection time to defeat **DNS rebinding**. It also warns that blocklists are incomplete and an
allowlist is preferable where possible.

**Design — layered, and it fails closed:**

1. **Scheme allowlist**: `https` only by default. `http` requires explicit opt-in per deployment
   (`allow_insecure_http`), never per subscription — a tenant must not be able to downgrade its
   own delivery to plaintext.
2. **Resolve-then-validate-then-pin.** Resolve the hostname, reject if *any* resolved address is in
   a blocked range, then **connect to the validated IP** with the original `Host` header preserved
   (TLS SNI set to the hostname). This closes the rebinding window that resolve-then-connect leaves
   open — validating one address and letting the HTTP client re-resolve to another is the exact bug
   brief 005 §2 warns about.
3. **Blocked by default**, an operator-extensible deny list plus an **optional allow list** that,
   when set, is exclusive — the allowlist-first posture brief 005 §2 recommends, available to
   deployments that can enumerate their consumers.
4. **No redirect following.** A 3xx response is a delivery failure, not a hop. Following redirects
   re-opens every check above at a URL the validation never saw.
5. **Deny link-local, loopback, multicast, unspecified, and IPv6 equivalents** (`::1`, `fc00::/7`,
   `fe80::/10`, and IPv4-mapped forms like `::ffff:169.254.169.254` — the mapped form is a
   classic bypass and needs an explicit test).

DESIGN: resolve-validate-pin with no redirects, over a URL-pattern blocklist
  ✅ Defeats DNS rebinding, which a validate-the-URL-string approach does not even attempt.
  ✅ The cloud metadata endpoint — the highest-value SSRF target — is blocked by address, not by
     hostname string, so `metadata.google.internal` and every alias fail too.
  ✅ Refusing redirects removes a whole bypass class for a feature (redirected webhooks) nobody
     needs.
  ❌ Pinning the IP breaks a consumer behind round-robin DNS failover mid-delivery. Accepted: the
     retry re-resolves, so the failover works across attempts rather than within one.
  ❌ More code than a regex over the URL. That is the point.
  Rejected — **trusting the HTTP client's own proxy/redirect config**: ❌ varco does not control
  the consumer's deployment and a misconfigured client is a silent hole.

### §D-D4-delivery — reuse the retry policy and the DLQ; invent nothing

Per the row's own rationale, delivery composes existing parts:

- **Retry**: `varco_core.resilience.RetryPolicy` — the same model `@listen` already uses. Default
  schedule modelled on brief 005 §2's survey (Svix: immediate, 5s, 5m, 30m, 2h, 5h, 10h, 10h, with
  exponential backoff **plus jitter**; Stripe: ~16 attempts over ~3 days). Default to a Svix-shaped
  8-attempt schedule with jitter, configurable.
- **Timeout**: 10 seconds per attempt, following Stripe (brief 005 §2).
- **Exhaustion → DLQ**: the existing `AbstractDeadLetterQueue`. This is what makes replay free —
  `DlqRedriver` already exists and is already a sanctioned bus-holder.
- **Endpoint disablement**: after N consecutive failures across distinct events, mark the
  subscription disabled and stop attempting. Brief 005 §2 records this as universal (Stripe, Svix).
  Re-enable is an explicit admin action.
- **At-least-once, documented.** Every delivery carries a stable id (`webhook-id`) so the consumer
  can deduplicate — and the docs point at plan 029's `Idempotency-Key` middleware as the
  receiving-side tool when the consumer is itself a varco app.

⚠️ **`push()` must never raise** — the DLQ contract (CLAUDE.md). The dispatcher's DLQ write follows
the same discipline as `OutboxRelay` and `JobRunner`.

### §D-D4-entity — one entity, two repositories, one framework table

`WebhookSubscription` (`DomainModel`): id, tenant, target URL, event-type patterns, active
secrets (encrypted), status (`ACTIVE`/`DISABLED`), consecutive-failure count, timestamps, signer
choice, and custom headers.

Tenant-scoped (`TenantScope.TENANT`, the default) — a subscription belongs to a tenant and must
never fan out across them. Repositories in `varco_sa` and `varco_beanie`; SA gets a migration
revision plus `register_framework_metadata()` (`varco_sa/varco_sa/metadata.py:55`).

### §D-D4-admin — `mount_webhook_admin`, gated exactly like its neighbours

`varco_fastapi.webhook.mount_webhook_admin(app, *, acknowledge_bundled_admin=True, server_auth=…,
admin_role="webhook-admin", prefix="/webhooks", dependencies=…)` — the signature shape of
`mount_reliability_admin` (`varco_fastapi/varco_fastapi/admin/mount.py:46-76`), including the
`ValueError` when `acknowledge_bundled_admin` is not `True`.

Per CLAUDE.md's `mount_*` taxonomy and RD-9: **never** a `create_varco_app()` kwarg, and **never**
an env var that mounts it. Routes: CRUD subscriptions, list deliveries, replay a delivery (through
`DlqRedriver`), rotate a secret, disable/re-enable an endpoint.

---

## Steps

### Phase 0 — D4a: entity and storage

1. [x] `varco_core/varco_core/webhook/` — `models.py` (`WebhookSubscription`, `WebhookDelivery`),
       `base.py` (`WebhookSubscriptionRepository` ABC), `settings.py`
       (`WebhookSettings(BaseSettings)`, `@Provider`-registered, `VARCO_WEBHOOK_` prefix).
2. [x] Secrets encrypted at rest through the existing `FieldEncryptor` — **no new crypto path**.
       Multiple active secrets per subscription (§D-D4-signing).
3. [x] `varco_sa` + `varco_beanie` repositories; SA migration revision +
       `register_framework_metadata()`.
4. [x] Unit tests with an in-memory repository; integration tests for both backends.
5. [x] API surface snapshot + import budget (`--warn-only`).

⛔ **CHECKPOINT**

### Phase 1 — D4b: signing

6. [x] `WebhookSigner` ABC; `StandardWebhooksSigner` (default) per §D-D4-signing —
       HMAC-SHA256 over `{id}.{timestamp}.{payload}`, emitting `webhook-id`/`webhook-timestamp`/
       `webhook-signature`, space-delimited multi-signature for rotation.
7. [x] `Rfc9421Signer` — `Signature-Input`/`Signature` per RFC 9421 plus RFC 9530 `Content-Digest`;
       covered components `@method`, `@target-uri`, `@authority`, `content-digest`; `created`,
       `keyid`, `alg=hmac-sha256`. Signature base built by structured serialization, never string
       concatenation (brief 005 §2 — this is what prevents extension attacks).
8. [x] Tests: known-answer vectors for both schemes (**take the Standard Webhooks vectors from the
       spec itself, not from our own output** — a self-generated vector proves nothing);
       constant-time comparison in any verification helper; rotation produces two valid signatures;
       tolerance-window rejection at ±301s and acceptance at ±299s.
9. [x] A `verify()` helper per signer, so a varco app receiving a varco webhook has a supported path
       and the test suite can verify what it signs.

⛔ **CHECKPOINT**

### Phase 2 — D4c: dispatcher and SSRF hardening

10. [x] `varco_core/webhook/ssrf.py` — `validate_target()` implementing all five layers of
        §D-D4-ssrf.
11. [x] **SSRF tests first, and they are not optional**: `169.254.169.254`; the IPv4-mapped IPv6
        form `::ffff:169.254.169.254`; `127.0.0.1`, `::1`, `10.x`, `172.16.x`, `192.168.x`,
        `fc00::`, `fe80::`; a hostname resolving to a private address; a **DNS-rebinding
        simulation** where resolution 1 is public and resolution 2 is private, asserting the pinned
        connection is used; `http://` rejected unless `allow_insecure_http`; a 302 to a private
        address rejected because redirects are not followed.
12. [x] `WebhookDispatcher` as an `EventConsumer` (§D-D4-home) — `@listen`-decorated, wired via
        `register_to()` from `@PostConstruct`, **never holding `AbstractEventBus`**.
13. [x] Delivery per §D-D4-delivery: `RetryPolicy` with jitter, 10s timeout, DLQ on exhaustion
        (`push()` never raises), consecutive-failure counting and auto-disable.
14. [x] The HTTP send path uses a function-body import of `httpx` and takes no hard dependency —
        the same rule and the same mechanical guard as `varco_core.tls.clients`
        (`test_tls_no_hard_client_deps.py`). Add an equivalent guard test.
15. [x] Tests: successful delivery; 5xx retried; timeout retried; exhaustion lands in the DLQ;
        auto-disable after N failures; a disabled subscription is skipped.
16. [x] `install_webhook_metrics()` following `install_cache_metrics`/`install_reliability_metrics`
        (CLAUDE.md's `install_*` shape (a) — process-global, no container).

⛔ **CHECKPOINT** — **do not proceed without Step 11 green.**

### Phase 3 — D4d: admin surface and docs

17. [x] `varco_fastapi/varco_fastapi/webhook/` — `mount_webhook_admin` per §D-D4-admin.
18. [x] Replay through the existing `DlqRedriver`; secret rotation; disable/re-enable.
19. [x] Tests: the `acknowledge_bundled_admin` `ValueError`; role enforcement; **a cross-tenant
        read is refused** (a subscription list must never leak across tenants).
20. [x] `technical_docs/features/outbound-webhooks.md` — the signing-scheme choice and why
        (§D-D4-signing), the SSRF model, the retry schedule, consumer-side idempotency guidance
        pointing at plan 029, and a Pitfalls table.
21. [x] README section; CLAUDE.md gets a Decision-Tree branch and a one-line pointer only.
22. [x] `testkit/varco_conformance` — a suite for `WebhookSubscriptionRepository`, or a
        `COVERAGE.md` row justifying its absence.
23. [x] API surface snapshot; import budget.

⛔ **CHECKPOINT** — full `make test`, `make lint`, `make type-check`, plus the integration legs.

---

## Parked

| Item | Why | Un-park trigger |
|---|---|---|
| Inbound webhook verification | Mirror problem, separate row | A consumer receives third-party webhooks in a varco app |
| High-fan-out sharding | Correct delivery first | A deployment exceeds what a single dispatcher handles |
| Ed25519 asymmetric signing (Standard Webhooks `v1a`) | HMAC covers the common case; asymmetric matters when the consumer must verify without holding a shared secret | A consumer requires it |
| Per-subscription rate limiting | varco has a rate limiter; wiring it per subscription is additive | A consumer is overwhelmed by delivery volume |
| Webhook UI | RD-9 / API-first position | Never, on current evidence |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **SSRF** — this feature fetches user-supplied URLs by design | **Critical** | §D-D4-ssrf's five layers; Step 11's test list is a merge gate, not a nice-to-have; no redirect following; IP pinning against rebinding |
| ⚠️ **ASSUMPTION** — that connecting to a pinned IP with a preserved `Host` header and hostname SNI works cleanly through `httpx`. Brief 005 §2 establishes the *requirement* but not the client mechanics | Medium — could force a transport-level workaround | Verify at Step 10 before building on it. If `httpx` cannot express it, the fallback is a custom transport/resolver, not dropping the pin |
| Signature scheme choice ages badly if RFC 9421 wins adoption | Low | The `WebhookSigner` ABC makes the default a one-line change; both ship |
| Secrets leaked via admin API or logs | High | Encrypted at rest; never returned by any read endpoint (only on creation/rotation); explicit test |
| Auto-disable triggers on a consumer's transient outage | Medium | Threshold counts *consecutive* failures across distinct events; re-enable is one admin call; documented |
| A tenant registers another tenant's internal URL | High | Tenant-scoped subscriptions plus §D-D4-ssrf's address validation; `allow_insecure_http` is deployment-level, never per-tenant |
| ⚠️ **ASSUMPTION** — the Svix-shaped retry schedule suits varco's consumers. Brief 005 §2 documents what three vendors do, which is convention, not evidence about *our* users | Low | Fully configurable; the default is documented as a convention with its source |
| Scope: this is the cycle's largest row and may not fit | Medium | Four independently-shippable phases; P0+P1 alone (registry + signing, no dispatcher) is a coherent partial release if it comes to that |

## Open questions

1. **Does the dispatcher run in-process or as a separate deployable?** In-process as an
   `EventConsumer` is the design above and needs no new runtime. A dedicated relay process (the
   `OutboxRelay` shape) isolates delivery latency from request handling. Decide at Step 12 — lean
   in-process for 3.1, with the ABC placement leaving a separate runner possible later.
2. **Should delivery attempts be persisted, or only failures?** Persisting every attempt gives a
   full audit trail and an expensive write path; persisting failures only is cheap but makes
   "prove you delivered it" unanswerable. Decide at Step 13 — lean persist-all behind a
   `WebhookSettings` flag defaulting to failures-only.
