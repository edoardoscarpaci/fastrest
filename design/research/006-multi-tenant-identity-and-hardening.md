# Research 006 — Multi-Tenant Identity Provenance and Security Hardening Table Stakes

Date: 2026-09-05 · Freshness matters: **YES** — OAuth 2.1, DPoP, CAEP/SSF, and workload identity standards are actively evolving; OWASP Top 10 shifted in 2025-2026.

## Question

How should a multi-tenant backend framework establish and enforce a **trusted tenant identity per request** (not a client-supplied header), and what security hardening is table stakes vs. differentiator in that space? Specifically: what patterns are accepted for deriving tenant from a request, ranked by trust; how to bind authenticated subjects to tenants; how to authorize service-to-service impersonation safely; what data-layer defenses exist; and which hardening features comparable platforms ship as expected baseline in 2026.

## Findings

### 1. Trusted Tenant Provenance — Patterns Ranked by Trust

| Pattern | Trust Level | Use Cases | Caveats |
|---------|------------|-----------|---------|
| **JWT/OIDC `org_id` or `tid` claim (signed, issuer-bound)** | ✅ HIGHEST | Most SaaS platforms; enterprise federation | Require `iss` validation, `aud` binding, explicit allowlist per tenant (Entra ID guidance); claim name is **not standardized** — varies by IdP |
| **OAuth token exchange w/ actor claim (RFC 8693 `act`)** | ✅ HIGHEST | Service-to-service delegation & impersonation; Google Cloud IAM, Entra ID backends | Requires **explicit authorization check** of delegation; audit logs **must capture both** principal and actor; no silent fallback to impersonation |
| **Subdomain routing** (e.g., `tenant-a.example.com`) | ✅ HIGH | Common in SaaS; fast routing via cached domain→tenant lookup in Redis | Requires TLS SNI and HSTS; DNS rebinding risk unless paired with auth binding; cannot serve multiple tenants on same domain (limits API reuse) |
| **mTLS client certificate + CN/SAN field** | ✅ HIGH | Internal service-to-service; zero-trust infrastructure; workload identity (SPIFFE) | Rarely for end-user requests (browser certs impractical); primarily M2M; must validate CN/SAN against allowlist, never trust cert data blindly |
| **Path-based routing** (e.g., `/api/v1/tenant-id/...`) | ⚠️ MEDIUM | Fallback when subdomains unavailable; API aggregators | Lower precedent than subdomain; must still bind to authenticated principal; sequential/guessable tenant IDs are a BOLA risk (RFC 9449 §3 pattern: use UUIDs) |
| **Trusted proxy header + stripped at edge** (e.g., `X-Tenant-Id` from ingress only) | ⚠️ MEDIUM | Only within internal, mesh-controlled perimeters (Kubernetes, API Gateway with header stripping); NEVER across untrusted networks | **Legitimate only if**:  ingress controller strips all user-supplied `X-Tenant-Id` headers AND re-appends with verified value from routing; mutual TLS between proxy & backend validates the proxy itself |
| **Header only, no auth binding** | ❌ LOWEST | **VULNERABLE** — current varco baseline | Allows any caller to claim any tenant; no connection to authenticated identity; opens confused deputy attacks |

**Claim names across vendors:**
- **Auth0 Organizations**: `org_id`, `org_name` (if configured) — [Auth0 Docs](https://auth0.com/docs/manage-users/organizations/using-tokens)
- **Microsoft Entra ID**: `tid` (tenant GUID); also `oid` (object ID) but `tid` is the tenant discriminator — [Entra Access Token Claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
- **Okta**: `org_id` (custom claim, not OIDC standard) — [Okta Multi-Tenant Docs](https://developer.okta.com/docs/concepts/multi-tenancy/)
- **Keycloak**: `org_id`, `organization` (via Client Scopes Organization mapper, toggled "Add organization id" ON) — [Keycloak Orgs](https://www.keycloak.org/2024/06/announcement-keycloak-organizations)
- **AWS Cognito**: Custom attribute (no standard claim name; `custom:tenant_id` or similar); immutable attributes recommended — [Cognito Multi-Tenant Docs](https://docs.aws.amazon.com/cognito/latest/developerguide/multi-tenant-application-best-practices.html)

**No OIDC standard for org/tenant claim exists.** OpenID Connect specs define `sub`, `iss`, `aud`, `email`, etc., but organization identity is a custom claim. Collision-resistant naming (e.g., prefixed with application name) is best practice to avoid conflicts with other custom claims — [OpenID Connect Standard Claims](https://www.cerberauth.com/blog/openid-connect-standard-claims/).

### 2. Tenant/Subject Binding — Multi-Org Membership and Authorization

**Core Pattern:** Tenant identity must be **verified against the authenticated subject** (user, service account, client), not blindly trusted from a header or claim.

**Multi-Org Membership:**
- A user (or service) may be a member of multiple tenants.
- **Explicit org/tenant selection** is required at authentication time or before execution of tenant-scoped operations.
- Patterns:
  - **Choice at login** (Auth0 / Okta / Keycloak): User selects tenant during sign-in; platform includes chosen tenant in issued token(s).
  - **Query user's membership** (after auth): Backend verifies `(authenticated_subject, tenant_id)` is a valid membership before executing; allows switching org/tenant **per request** without re-auth.
  - **Ambient tenant from context** (not recommended for multi-org): Tenant derived from subdomain or path; user must be a member of that tenant, verified once.

**Confused Deputy Attack & Cross-Tenant Authorization:**

According to OWASP, a confused deputy flaw is "improper delegation—a trusted component using broad power without enough context to decide whether the request is legitimate." Cross-tenant variants include:
- **BOLA (Broken Object Level Authorization)**: Endpoint exposes a resource by ID but never checks the caller owns it OR that the caller belongs to the resource's tenant. Attacking: increment resource ID, access other tenant's data. — [OWASP API1:2023](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)
- **Lateral access across services**: A service trusts a peer's claim of tenant without re-verifying against the original authenticated subject.
- **Cache key confusion**: Shared cache missing tenant discriminator (`cache_key = "user:123"` instead of `"tenant:456:user:123"`) → one tenant reads another's cached data.

**Defenses (OWASP Multi-Tenant Security Cheat Sheet):**
1. **Verify on every operation**: Check `(authenticated_subject, target_tenant_id)` membership before each query.
2. **Use unpredictable resource IDs**: UUIDs, not sequential integers.
3. **Include tenant scope in all lookups**: `SELECT * FROM resources WHERE id = $1 AND tenant_id = current_tenant()`
4. **Test authorization thoroughly**: Include test cases for cross-tenant access attempts.
5. **Fail closed on missing tenant**: If tenant context is not set, reject the request (not "assume default tenant").

### 3. Service-to-Service / Internal Callers — Impersonation and Act-As

**Mechanism: OAuth 2.0 Token Exchange (RFC 8693, standardized 2019).**

A backend service can act **on behalf of** an arbitrary tenant by:
1. Service obtains its own credentials (client cert, service account token, or API key).
2. Service requests a token exchange from the authorization server, specifying:
   - `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`
   - `subject_token` (its own service token or user token for delegation cases)
   - `actor_token` (optional, for delegation where the actor is explicitly stated)
   - `requested_subject` (the principal/tenant it wants to act as)
3. Authorization server issues a new token with:
   - `sub` = requested principal
   - `act` = actor claim (if delegation, not impersonation) — structure: `{"sub": "<service-id>"}` — [RFC 8693 §4.1](https://datatracker.ietf.org/doc/html/rfc8693)

**Two patterns:**
- **Impersonation**: Issued token has `sub = <requested_principal>`, no `act` claim. Service is indistinguishable from the principal. ⚠️ Risky; rarely used for tenant scoping.
- **Delegation**: Issued token has `sub = <original_principal>`, `act = {sub: <service>}`. Both parties are visible in the token. **Preferred for audit.**

**Authorization of delegation:**
- Authorization server must verify the **requesting service is authorized** to act on behalf of that tenant.
- OWASP & RFC 8693 both emphasize: **never grant unrestricted delegation**. Scope it: service A can act as tenant B only for specific operations (read user data, not delete).

**Audit expectations (CRITICAL):**
- Every `act` delegation must be logged with both principal and actor.
- **Microsoft Entra ID actor token vulnerability (CVE-2025-55241)**: Undocumented backend actor tokens issued with **no logs**; no audit trail of who asked to impersonate whom. This was the attack vector for global admin escalation. — [Dirk-jan Mollema: Entra ID Actor Tokens](https://dirkjanm.io/obtaining-global-admin-in-every-entra-id-tenant-with-actor-tokens/)
- **Lesson**: Impersonation must be logged at issuance AND use; silent actor tokens are a critical risk.

**Practical implementation:** Use RFC 8693 token exchange natively if your IdP supports it (Okta, Entra, Keycloak, ZITADEL do). If not, create an internal token issuer that validates the service's credentials and then issues a delegation token with `act` claim.

### 4. Defense in Depth at the Data Layer

**PostgreSQL Row-Level Security (RLS) as a Backstop:**

RLS is a **database-enforced policy** that rewrites queries to silently append a tenant filter, independent of application code.

```sql
CREATE POLICY tenant_isolation ON orders
  USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
  
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
```

When an app queries `SELECT * FROM orders WHERE id = $1`, PostgreSQL rewrites it internally to:
```sql
SELECT * FROM orders WHERE id = $1 AND tenant_id = current_setting('app.current_tenant_id')::uuid
```

**Key properties:**
- **Enforced at plan time**, not post-fetch → uses indexes, efficient.
- **Survives ORM bugs and SQL injection** (if parameterized) — if the app forgets a `WHERE tenant_id =` clause, RLS still filters.
- **Does NOT prevent queries without tenant context** — if `app.current_tenant_id` is not set, the policy **denies all rows** (default USING clause behavior). Fail-closed. — [Crunchy Data Blog](https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres)

**Critical rule (OWASP Multi-Tenant Cheat Sheet):**
- **Never use a BYPASSRLS-privileged connection for tenant-scoped reads.** BYPASSRLS roles bypass all policies. Operate only through a role that has RLS enforced.

**Non-Postgres data layers:**
- **MongoDB (via Beanie/PyMongo)**: No native RLS; tenant filter must be enforced in every query (convention + linting). Risk of ORM bypass.
- **SQLAlchemy with Postgres RLS**: Best practice — define models with `@event.listens_for()` to always filter by `current_tenant()`, AND enable RLS as a backstop.

**Mechanical guard against tenant-less queries:**
- **Static query analysis** (rare, not shipped by frameworks): A linter that flags queries without a tenant filter. Difficult to implement reliably across ORMs.
- **Runtime check (event-driven, PostgreSQL + SQLAlchemy)**: Use SQLAlchemy `before_execute` event to verify tenant context is set before any table with RLS is queried. Reject the query with `RuntimeError` if not. — Example: varco's own `@TenantAwareService` mixin could implement this check.
- **Fail-closed default**: If tenant context is None, assume it is unset and reject all data access.

### 5. Neighbouring Security Features — Table Stakes vs. Differentiator (2026)

**Baseline Expected by Comparable Platforms:**

| Feature | Purpose | Table Stakes in 2026? | Evidence |
|---------|---------|----------------------|----------|
| **HTTPS + HSTS** | Encrypted transit, prevent downgrade | ✅ YES | OWASP Top 10 2025, every platform; HSTS required for subdomain routing |
| **Security response headers** (CSP, X-Frame-Options, X-Content-Type-Options) | XSS/clickjacking/MIME-sniffing mitigation | ✅ YES | OWASP Secure Headers Project; 70% of top 1M sites still score D or below (2024 SecurityHeaders.com survey), so non-compliance is common but negligent |
| **Rate limiting (global + per-tenant)** | Prevent DDoS; "noisy neighbor" abuse | ✅ YES | OWASP API Top 10 2023 A4 (Unrestricted Resource Consumption); multi-tenant services MUST isolate tenant quota |
| **Request size/body limits** | Prevent memory exhaustion, slowloris | ✅ YES | OWASP API Top 10 2023; FastAPI has `max_body_size` support |
| **Input validation + parameterized queries** | SQL injection, XSS, buffer overflow | ✅ YES | OWASP Top 10 2025 unchanged; parameterized queries are non-negotiable |
| **Audit logging of authz decisions** | Compliance, incident investigation | ✅ MOSTLY | HIPAA, PCI-DSS, SOC 2 require it; not all frameworks ship it by default (varco does via `AuditLogMixin`) |
| **Secrets management** (no hardcoded keys in code/logs) | Prevent credential leakage | ✅ YES | OWASP Top 10 2025 A2 (Broken Authentication); Platform defaults to env vars, never git-committed secrets |
| **CSRF protection** | Prevent cross-site form submission | ⚠️ CONTEXT | Required for session-cookie auth; irrelevant for stateless JWT/bearer tokens (most modern APIs). Varco is stateless, so not strictly required, but POST/PUT/DELETE with side effects may benefit from token-binding (DPoP). |
| **Mass assignment / object property bypass** | Prevent unsanitized bulk-update of fields | ✅ YES | OWASP API Top 10 2023 A3 (Broken Object Property Level Authorization); ORMs (SQLAlchemy, Pydantic) help, but must explicitly whitelist fields on updates |
| **PII redaction in logs** | Privacy compliance (GDPR, CCPA) | ✅ YES | Legally required for regulated data; observability frameworks (OpenTelemetry) now have built-in redaction support |
| **Admin surface protection** (separate auth realm, IP allowlist, MFA) | Prevent privilege escalation | ✅ YES | OWASP Top 10 2025 §A1 (Broken Access Control); examples: varco's `mount_reliability_admin()`, `mount_tenant_admin()` require explicit `acknowledge_*` kwargs and `server_auth=` parameter |
| **Supply-chain attestation** (SLSA, PEP 740 attestations, SBOM) | Verify build integrity, dependency audit | ✅ EMERGING | OWASP Top 10 2025 A3 (Supply Chain Attack) newly ranked; varco ships SBOM per distribution, PEP 740 attestations in releases — [Plan 023/Plan 030 SBOM](https://github.com/edoardoscarpaci/varco/blob/main/scripts/sbom.py) |
| **DPoP (Demonstrating Proof-of-Possession)** | Bind token to client's public key; prevent token replay | ⚠️ EMERGING | RFC 9449 (2024); Okta, Auth0, Entra now support it; **not yet universal** — primarily for high-security APIs (open banking FAPI 2.0) |
| **Shared Signals Framework (SSF/CAEP)** | Real-time session revocation, event-driven auth state | ⚠️ EMERGING | Google Workspace beta (2025); IETF CAEP spec finalized; **adoption is slow** — most platforms still poll for token revocation or use stateless tokens with short TTLs |
| **Workload identity (SPIFFE/SVID)** | Decentralized, cryptographic M2M identity; zero-trust infrastructure | ⚠️ EMERGING | Production at Stripe, Netflix, Uber (2024); primarily Kubernetes-first; not required for single-instance or traditional cloud deployments yet |

**Table-stakes summary:** HTTPS, headers, rate-limiting, input validation, audit logging, secrets management, mass-assignment protection, PII redaction, and admin-surface gating are non-negotiable. Everything else is prioritized by deployment context (FAPI 2.0 compliance → DPoP; zero-trust infrastructure → SPIFFE; SaaS with strict revocation → SSF/CAEP).

### 6. Ecosystem Shifts (2024-2026)

**New standards & opportunities:**
- **RFC 9449 (DPoP, March 2024)**: Standardized sender-constraining for OAuth tokens. Applicable to SaaS APIs where token leakage is a risk (e.g., via compromised browser, network interception). Prevents replay of stolen tokens. — [RFC 9449](https://datatracker.ietf.org/doc/html/rfc9449)
- **OWASP Top 10 2025 (finalized Jan 2026)**: Elevated supply-chain attacks to #3; security misconfiguration to #2. Shifted focus from code-level flaws to systemic risks. Confirmed parameterized queries, security headers, rate-limiting as baseline.
- **OAuth 2.1 (draft, not yet RFC)**: Planned to require PKCE for all clients, deprecate implicit flow, discourage password grant. Still in draft; adoption timeline unclear.
- **CAEP / Shared Signals Framework (OpenID spec, IETF draft)**: Real-time session revocation signaling. Google Workspace closed beta (2025); adoption by enterprise IdPs slower than expected. Useful for zero-trust but not required for typical SaaS today.
- **SPIFFE (Secure Production Identity Framework, CNCF Incubating)**: Standardized workload identity for service-to-service in Kubernetes/cloud-native. Production deployments at scale (Stripe, Netflix, Uber); not required for single-instance apps.
- **CVE-2025-55241 (Microsoft Entra actor tokens)**: Highlighted that **undocumented impersonation mechanisms without audit trails are a critical risk**. Lesson: any service-to-service delegation MUST be logged and authorized explicitly.

**No deprecations of JWT/OIDC or subdomain routing.**

## Options Compared (When the Question is a Choice)

### Multi-Tenant Identity Provenance: Choosing a Primary Pattern

**Scenario: Greenfield SaaS platform, distributed user base, multiple authentication sources.**

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|--------|------------|--------------|----------|
| **JWT `org_id` claim (signed, issuer-bound)** | Single source of truth (token); works across federated IdPs; audit trail in token; no session state needed; works for service-to-service (RFC 8693) | Claim name is not standardized (varies by vendor); requires custom claim setup at each IdP; token TTL limits revocation speed; cannot switch orgs without new token | Auth0, Okta, Keycloak, Entra all support; OWASP recommends; RFC 8693 standard for delegation. |
| **Subdomain routing** | Fast lookup (domain → tenant_id cached in Redis); familiar to users (`tenant.example.com`); no custom claims needed | DNS rebinding risk (mitigate: HSTS, HTTPS-only); cannot serve multiple tenants on same domain; requires wildcard DNS/TLS cert; not suitable for API aggregators | Common in SaaS (Slack, GitHub, Figma); AWS describes as standard routing strategy. |
| **Path-based** (e.g., `/api/tenant/{id}/...`) | Flexible; single domain; works anywhere | Lower security precedent; must validate tenant is accessible by authenticated principal (BOLA risk if ID is guessable); less familiar to users | Works but requires explicit authorization on every request; no framework default. |
| **Hybrid (subdomain + JWT claim binding)** | Best of both: subdomain for routing speed + JWT claim for cross-platform auth binding | Complexity; two sources of truth (must match or fail) | Recommended by OWASP Multi-Tenant Cheat Sheet; Okta hub-and-spoke pattern. |

**Recommendation favoured by evidence**: **Hybrid (subdomain + JWT claim binding)** for greenfield. Subdomain routes to the backend; JWT `org_id` claim binds the authenticated user/service to that tenant. Fail if subdomain ≠ claim tenant.

---

## Version/Compatibility Notes

- **OWASP API Top 10 2023** (current, published 2023, reaffirmed in 2025): BOLA still #1. Multi-Tenant Cheat Sheet (updated Feb 2024) advises fail-closed defaults.
- **RFC 8693 (OAuth 2.0 Token Exchange)**: Published Jan 2019. Implemented by major IdPs (Okta, Entra, Keycloak 25+, ZITADEL, Auth0 [partially, via custom rules]). **Not yet adopted universally for SaaS tenant delegation** — most SaaS still uses JWT claims alone.
- **RFC 9449 (DPoP)**: Published March 2024. Early adoption: Okta (GA), Auth0 (available), Entra (available). Primarily for open-banking / high-security APIs; not yet table-stakes for typical SaaS.
- **CAEP / Shared Signals Framework**: OpenID spec; IETF CAEP-Interop Profile still draft (Sept 2025). Google Workspace closed beta (2025). **Not production-default yet** — most platforms rely on short-lived tokens (5-60 min TTL) instead.
- **SPIFFE**: CNCF Incubating (stable since ~2021). Standardized workload identity; production at Stripe, Netflix, Uber. **Kubernetes-first** — adoption outside cloud-native is slow.
- **Keycloak Organizations**: Available since Keycloak 25 (Feb 2024). `org_id` claim available via Client Scopes mapper.
- **AWS Cognito multi-tenancy**: Stable, unchanged since ~2020; custom attributes (non-standard claim names) are the path.
- **PostgreSQL RLS**: Available since PG 9.5 (2016). Mature, widely used. No planned breaking changes.

---

## Evidence Gaps

1. **Standardized claim name for organization identity**: No OIDC RFC reserves `org_id`, `org`, `organization`, or `tid` for multi-tenant tenant IDs. Each vendor uses a different name. A future OIDC extension or OpenID Connect for Multi-Tenant (hypothetical) would help, but none exists; this remains a per-vendor configuration. ← Worth a future brief: "OIDC standard claim for org_id".

2. **Mechanical guards against tenant-less queries**: No framework (Django, FastAPI, Rails, Spring) ships a built-in query analyzer that enforces "all queries on RLS-enabled tables must set tenant context." OWASP advises it, no open-source tool implements it widely. SQLAlchemy event hooks can do it, but it's a per-deployment choice. ← Worth a future brief on "static and runtime query safety enforcement".

3. **RFC 9449 (DPoP) adoption rates and real-world token replay incidents**: RFC is 2024; adoption by SaaS platforms is reported but unquantified. No public incident database of "token leaked, DPoP would have prevented this" — evidence for DPoP as table-stakes is still weak. ← Worth a revisit in 2027.

4. **Comparative survey of multi-tenant hardening in framework defaults**: Django-tenants, FastAPI-tenancy, Spring Cloud for Azure, Laravel Octane — none have been audited side-by-side for default auth binding behavior, RLS use, confusion deputy defenses. This brief relies on OWASP cheat sheet (authoritative but generic) and vendor docs (biased). ← Worth a comprehensive framework audit brief.

5. **Cost/complexity trade-off: schema-based vs. row-level isolation**: OWASP mentions all three (separate DB, separate schema, RLS), but no published benchmark of migration cost, query performance, or operational overhead per pattern. ← Worth a performance brief.

---

## Librarian's Note

The evidence **strongly favours a hybrid approach** for varco's replacement of the unsafe `X-Tenant-Id` header-only pattern:

1. **Derive tenant from multiple sources (rank by trust):**
   - Primary: JWT `org_id` or `tid` claim (signed, issuer-bound), validated against user's authenticated subject ← RFC 8693-compliant; all major IdPs support.
   - Secondary: Subdomain or path (for routing/UX); validated to match the claim.
   - Tertiary: Never trust a header alone; only trust a header if it came from a **stripped-and-reapplied** middleware/proxy within a zero-trust mesh (mTLS between proxy and backend, header stripping at edge).

2. **Enforce at service layer via `TenantAwareService`** with `current_tenant()` returning the verified, immutable tenant context. Fail-closed if not set.

3. **Backstop at data layer with Postgres RLS** (if Postgres is in use). An application bug that forgets the WHERE clause will still be caught.

4. **Service-to-service impersonation (if needed later):** Use RFC 8693 token exchange with `act` claim. Audit every delegation. No silent impersonation.

5. **Neighbouring hardening:** Security headers, rate-limiting per tenant, audit logging of authz decisions, input validation, mass-assignment protection, and admin-surface gating are all table-stakes as of 2026. DPoP and CAEP are emerging; defer them until a specific threat (token leakage at scale, revocation latency) is observed.

The decision to **actually implement** (vs. evidence-gathering) is upstream; this brief provides the feature design constraints and precedent from Auth0, Okta, Keycloak, Entra, AWS, and OWASP.

