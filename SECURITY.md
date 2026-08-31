# Security Policy

## Supported versions

Only the **latest minor release of the current major version** receives security fixes.

| Version | Supported |
|---|---|
| 3.0.x | ✅ |
| < 3.0 | ❌ |

This window is deliberately narrow: varco is released in lockstep across all ten distribution
packages (see `CONTRIBUTING.md`'s versioning policy), so "current major" always means one concrete
set of ten package versions, not a range to reason about per package.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Use GitHub's private
vulnerability reporting instead: open the repository's **Security** tab →
**"Report a vulnerability"**. This creates a private advisory visible only to the maintainer and
the reporter until a fix is coordinated and published.

⚠️ This channel only exists once "Private vulnerability reporting" is enabled in the repository's
Settings → Code security page (a manual, one-time repository setting — see
`plans/023-release-version-freeze-and-supply-chain.md`'s Appendix A row 36). If the Security tab
does not offer "Report a vulnerability" when you read this, the setting has not yet been applied;
email the maintainer address in `pyproject.toml`'s `authors` field as a fallback.

We aim to acknowledge a new report within **5 business days**, and we follow a
**90-day coordinated-disclosure embargo** from acknowledgement to public disclosure (extendable by
mutual agreement if a fix genuinely needs more time — never to indefinitely suppress a report).

## Scope — security-bearing subsystems

The following subsystems carry the most security-relevant surface area and deserve extra scrutiny
in any report or review:

- **JWT / authority** (`varco_core.authority`, `varco_core.jwt`) — token signing, verification,
  multi-issuer trust, JWKS caching, audience/issuer enforcement.
- **Field-level encryption / crypto-shredding** (`varco_core.encryption`,
  `varco_core.encryption_store`) — DEK management, key rotation, tombstoning.
- **Multitenant isolation / Row-Level Security** (`varco_core.tenancy`, `varco_sa.rls`) —
  tenant-scoped queries, schema/database isolation strategies, RLS DDL generation.
- **CORS defaults** (`varco_fastapi.middleware`, `CORSConfig`) — cross-origin policy applied by
  `create_varco_app()`; see `CHANGELOG.md`'s `[3.0.0]` entry (AB-5) for a worked example of a past
  CORS-default security fix.

A report about any of these is treated with the highest priority regardless of severity
self-assessment.

## What this document does not claim

Neither the presence of a branch/tag ruleset on this repository nor PEP 740 build attestations on
a released artifact should be read as a claim that this repository or its releases are immune to
compromise — see `plans/023-release-version-freeze-and-supply-chain.md`'s §RL-SEC-hardening for
the honest limits of a solo-maintainer repository's GitHub-side hardening (in particular: an admin
bypass actor exists on both rulesets, which is necessary for a one-person project to function, and
means those rulesets do not enforce anything against that admin).
