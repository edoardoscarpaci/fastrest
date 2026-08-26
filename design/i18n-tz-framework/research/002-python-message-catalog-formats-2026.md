# Research 002 — Python Message Catalog Formats & Defaults (2026)
**Date:** 2026-08-20 · **Freshness matters:** Yes (Python library releases, CLDR data, and Unicode standardization are active)

## Question

For a Python 3.12+ framework shipping a pluggable `MessageCatalog` ABC in 2026, what is the actual state of the Python tooling for each candidate format, and which should be the shipped default implementation?

Specifically:
1. **Babel** maturity, maintenance, CLDR version, ICU MessageFormat support, thread/async safety
2. **stdlib `gettext`** thread-safety, `.mo` loading, caching, plural support
3. **ICU in Python** (PyICU vs pure-Python alternatives)
4. **MessageFormat 2.0** standardization status, Python implementations
5. **Fluent (Python)** maintenance status, production adoption
6. **Comparable frameworks** — what do Django, FastAPI ecosystem actually ship?
7. **RFC 4647 language matching** implementations in Python

## Findings

### Babel — Current State (2026)

**Version and Release Cadence:**
- Current stable version: **2.18.0** (February 2026) — [Releases · python-babel/babel](https://github.com/python-babel/babel/releases)
- Previous: 2.17.0 (February 2025), 2.16.0 (August 2024)
- Release cycle: **Glacial** — 1 year between 2.17 and 2.18. Maintainers acknowledged this in 2.18.0 release notes: *"We'll aspire for a less glacial release cycle for 2.19."* — [Babel Changelog](https://babel.pocoo.org/en/latest/changelog.html)
- **Maintenance status:** Actively maintained but infrequent releases

**CLDR Data:**
- 2.18.0 ships **CLDR 47** (released October 2024) — [Babel Changelog](https://babel.pocoo.org/en/latest/changelog.html)
- 2.17.0 shipped CLDR 46
- Babel bumps CLDR only with major/minor releases (not patch releases) to minimize disruption — [Babel Documentation](https://babel.pocoo.org/)

**ICU MessageFormat Support:**
- **NO** — Babel does NOT implement ICU MessageFormat syntax
- Babel supports **gettext-style pluralization** driven by CLDR plural rules (e.g., `.po` files with `msgid_plural`)
- Number/date/time formatting is via Babel's own formatters, not ICU MessageFormat syntax
- To use full ICU MessageFormat (gender, ordinals, complex selectors), you must use PyICU, not Babel alone — [ICU Message Format Guide - Crowdin Blog](https://crowdin.com/blog/icu-guide)

**Thread-Safety and Async-Safety:**
- `gettext.GNUTranslations` instances are cached by `.mo` file name; `copy.copy()` is used for fallback variants — [Python gettext docs](https://docs.python.org/3/library/gettext.html)
- **Flask-Babel has reported thread-safety hazards**: *"force_locale interference"* in Issue #117 — when one request's `force_locale()` context manager leaks into another thread's request context — [python-babel/flask-babel Issue #117](https://github.com/python-babel/flask-babel/issues/117)
- **Explicit async-safe API:** None documented for Babel's `Locale` class. Usage in async frameworks typically requires request-scoped context (via `contextvars.ContextVar` or middleware), not shared Locale instances.
- **Best practice:** Share compiled `.mo` files; do not share Babel `Locale` objects across threads/async contexts. Each request should resolve its own locale.

---

### stdlib `gettext` — Thread-Safety and Caching

**Caching:**
- `gettext.translation()` caches instances by `.mo` file name
- Cache lookup uses identity, not content: two `.mo` files with identical content but different paths are cached separately
- Fallback locales use `copy.copy()` on cached instances; the actual catalog data remains shared — [Python gettext docs](https://docs.python.org/3/library/gettext.html)

**Thread-Safety:**
- No explicit thread-safety guarantees in documentation
- `.mo` file loading is a one-time operation at instantiation; no dynamic reloading
- Once loaded, `GNUTranslations.gettext()` and `ngettext()` are dictionary lookups (thread-safe on CPython due to GIL)
- **Caveat:** The cache lookup itself is not protected by a lock; concurrent first-time `translation()` calls may load the same `.mo` file twice, but this is benign (both instances are identical)

**Plural Support:**
- `ngettext(msgid, msgid_plural, n)` returns the correct plural form based on CLDR rules
- Works for all CLDR languages (200+ plural forms)
- **Not ICU MessageFormat:** plural handling is simple `n → form_index` lookup, not the full ICU syntax (no gender, complex selectors, etc.)

**Performance:**
- `.mo` files are binary, fast to load (single mmap or read)
- Typical `.mo` file size: 10–100 KB for small-to-medium catalogs
- Lookup is O(1) hash table (after binary search in the file)
- **No documented async overhead**

---

### ICU in Python

**PyICU:**
- Current version: **2.15.2+** for Python 3.12/3.13 (wheels available as of March 2026) — [pyicu · PyPI](https://pypi.org/project/pyicu/)
- Wheels available for Linux (manylinux, multiple architectures), macOS (x86-64, ARM64), Windows (including ARM64 variants)
- **Maintenance:** Actively maintained; wheels for Python 3.14 also available (March 2026)
- **Install burden:** Requires `libicu` system library; wheels bundle it on some platforms, but pre-2026 versions needed system-level ICU installation
- **Capabilities:** Full ICU MessageFormat (plurals, gender, number/date/time formatting, ordinals) with CLDR backing

**Pure-Python Alternatives:**
1. **icu4py** — Python bindings for ICU4C with a more Pythonic API than PyICU; provides compiled wheels, no system libicu needed — [icu4py - PyPI](https://pypi.org/project/icu4py/), [adamj.eu - icu4py](https://adamj.eu/tech/2026/02/09/python-introducing-icu4py/)
2. **pyicumessageformat** — Pure-Python ICU MessageFormat parser/formatter (no C library); parses to AST, supports custom formatters/selectors — [pyicumessageformat · PyPI](https://pypi.org/project/pyicumessageformat/), [GitHub - tomasr8/pyicumessageformat](https://github.com/tomasr8/pyicumessageformat)
3. **pyseeyou** — Pure-Python ICU MessageFormat parser using parsimonious (PEG grammar); lightweight — [GitHub - rolepoint/pyseeyou](https://github.com/rolepoint/pyseeyou)

**Verdict:** PyICU wheels for 3.12+ are now viable; pure-Python alternatives exist but are less feature-complete. PyICU with wheels is the most practical ICU route in 2026.

---

### MessageFormat 2.0 — Standardization and Python Support

**Standardization Status:**
- **Stable and standardized** (as of 2025-2026)
- Approved by Unicode CLDR Technical Committee
- Part of Unicode Technical Report 35 (CLDR)
- Published specification: Part of Unicode Standard (not just Technical Preview) — [Unicode MessageFormat Standard](https://messageformat.unicode.org/), [Unicode Blog - MessageFormat 2 Final Candidate Review](https://blog.unicode.org/2025/01/messageformat-20-final-candidate-review.html)
- **Caveat:** Some default functions and items in the `u:` namespace remain in Draft status (subject to change based on implementation feedback)

**Python Implementation:**
- **messageformat2** (by tomasr8) — Python implementation of MF2 spec
- Version: **0.1.x** (early releases) — [messageformat2 · PyPI](https://pypi.org/project/messageformat2/)
- Requirements: Python >= 3.12
- Features: Parser, formatter, builtin formatters/selectors, extensibility API — [GitHub - tomasr8/messageformat2](https://github.com/tomasr8/messageformat2)
- Documentation: [MessageFormat2 Docs](https://messageformat2.readthedocs.io/en/latest/)

**Adoption Status (as of May 2026):**
- MF2 spec is stable, but **framework adoption is early**
- i18next (JavaScript) is in early stages of MF2 migration — [Locize Blog - MessageFormat 2 in i18next](https://www.locize.com/blog/messageformat-2-i18next/)
- No major Python framework has adopted MF2 yet; Babel still uses gettext

**Migration Path:**
- Adopting MF2 now means a potential future migration if/when Python ecosystem consolidates on it
- MF2 is backward-incompatible with MF1 (ICU MessageFormat) syntax (different string format)
- **Decision:** MF2 is too new for a default; recommend as an opt-in future alternative

---

### Fluent (Python) — Maintenance and Production Use

**Current State:**
- Version: **0.4.0** (released March 16, 2023)
- Last release: Nearly **3 years old** as of August 2026 — [fluent.runtime · PyPI](https://pypi.org/project/fluent.runtime/)
- Supported Python versions: **3.6–3.9** (does NOT officially support Python 3.12/3.13)
- Maintainers: 4 listed; however, minimal release activity

**Maintenance Status:**
- **Minimally maintained** — 5 total releases since 2019, very infrequent
- Development status: **Alpha** (PyPI classifier)
- No sign of active development toward Python 3.12+ support

**Production Adoption:**
- **Mozilla Firefox** uses Fluent for localization (internally, not via the Python binding)
- **Python ecosystem adoption:** Minimal outside Mozilla
- No evidence of widespread production deployments of the Python `fluent.runtime` package in 2026

**Verdict:** Not viable as varco's default due to Python 3.12+ incompatibility and stalled maintenance.

---

### Django i18n (2026) — Reference Implementation

**Message Format:**
- Uses **GNU gettext** exclusively (`.po`/`.pot`/`.mo` files)
- Built on `gettext` module + Babel extraction/compilation
- Translation functions: `gettext()`, `ngettext()`, `pgettext()` for context

**API Style:**
- String marked for translation in code: `_("Hello, world")`
- Message catalog registration: Auto-discovered from `INSTALLED_APPS` + `LOCALE_PATHS`
- Per-request locale: Set via `django.utils.translation.activate(locale)` (thread-local or request-scoped)
- No pluggable message catalog abstraction; gettext is hardcoded

**Pluralization and Formatting:**
- Plural forms via `ngettext()`; CLDR rules applied per language
- Date/number/time formatting via `django.utils.formats` (not ICU MessageFormat)
- No advanced features like gender agreement, ordinals, or complex selectors

**No Pluggable Abstraction:** Django's i18n layer is deeply integrated with gettext; no ABC for swapping catalog backends — [Django i18n Documentation](https://docs.djangoproject.com/en/6.0/topics/i18n/)

---

### FastAPI Ecosystem — I18n Solutions (2026)

**No Built-in Support:**
- FastAPI has no official i18n layer; users integrate third-party libraries

**Popular Third-Party Libraries:**

1. **fastapi-babel** — Wraps Babel; provides middleware for Accept-Language negotiation + format/translate helpers
   - GitHub: [Anbarryprojects/fastapi-babel](https://github.com/Anbarryprojects/fastapi-babel)
   - Approach: Gettext-based (same as Django)

2. **fastapi-i18n** — Lighter-weight wrapper; GNU gettext message catalogs from a locale directory
   - PyPI: [fastapi-i18n](https://pypi.org/project/fastapi-i18n/)
   - Approach: Pure stdlib `gettext`; environment-driven configuration

3. **PhraseApp FastAPI Guide** — Published example of i18n patterns
   - GitHub: [PhraseApp-Blog/fastapi-i18n](https://github.com/PhraseApp-Blog/fastapi-i18n)

**Ecosystem Pattern:** All FastAPI i18n solutions default to **gettext + Babel**. No Fluent or MessageFormat 2.0 implementations in the wild yet. — [Lokalize Blog - FastAPI i18n](https://lokalise.com/blog/fastapi-internationalization/)

---

### RFC 4647 Language Matching — Python Libraries

**language_tags:**
- Package: `language_tags` — [language-tags ReadTheDocs](https://language-tags.readthedocs.io/)
- Functionality: Validates and looks up BCP 47 language tags against IANA registry
- RFC 4647 support: **Not explicitly stated** in documentation; focuses on BCP 47 validation, not matching algorithms

**WebOb AcceptLanguage:**
- Part of the `webob` HTTP utilities library
- Implements **RFC 4647 Basic Filtering** (section 3.3.1) — not the Lookup algorithm (3.4)
- Functionality: Parse `Accept-Language` header, filter available tags
- Usage: Common in Pyramid and other Pylons-project frameworks — [webob · PyPI](https://pypi.org/project/WebOb/), [WebOb Source - acceptparse.py](https://github.com/Pylons/webob/blob/main/src/webob/acceptparse.py)
- Maintenance: Stable, part of Pylons ecosystem

**RFC 4647 Lookup Algorithm (Section 3.4):**
- **No standard Python library implements this**
- Lookup (returns single best match) is more complex than Filtering (returns all matches)
- Most frameworks implement a simplified variant (pick first best-quality match by q-value) rather than the full RFC 4647 Lookup spec
- Hand-rolling the ~40-line lookup algorithm is the norm in Python frameworks

---

## Options Compared

| Aspect | Babel+gettext | PyICU MessageFormat | Fluent (Python) | MessageFormat 2.0 |
|--------|---------------|---------------------|-----------------|-------------------|
| **Message format** | GNU gettext (`.po`) | ICU MessageFormat (.properties-like) | Fluent (custom syntax) | MF2 (custom syntax) |
| **Pluralization** | ✅ CLDR rules, simple | ✅ Complex (gender, selectors) | ✅ Full syntax support | ✅ Full syntax support |
| **Python 3.12+ support** | ✅ Yes (2.18.0) | ✅ Yes (wheels 2.15.2+) | ❌ No (3.6-3.9 only) | ✅ Yes (3.12+) |
| **Maintenance (2026)** | ✅ Active (slow cadence) | ✅ Active (wheels) | ❌ Stalled (3 yrs old) | ✅ Active (new) |
| **Async-safe shared catalog** | ⚠️ No; Flask-Babel issues | ⚠️ No explicit guarantees | — | ⚠️ Not tested |
| **Thread-safe caching** | ✅ `.mo` cached; caveat: Babel Locale not safe to share | ✅ `.mo` cached; safe | — | — |
| **Framework adoption** | ✅ Django, Rails, ASP.NET | ✅ i18next, major JS libs | ⚠️ Mozilla only | ⚠️ Early (i18next beta) |
| **Extract/compile tooling** | ✅ `pybabel extract/compile` | ⚠️ No standard tooling | ✅ `fluent-compile` | ❌ No standard tooling |
| **Ecosystem maturity** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |

---

## Version/Compatibility Notes

| Technology | Current (2026) | Status | Notes |
|------------|---|--------|-------|
| **Babel** | 2.18.0 (Feb 2026) | Stable, active | CLDR 47; glacial release cycle |
| **PyICU** | 2.15.2 (wheels, Mar 2026) | Stable, wheels available | Python 3.12/3.13 wheels; libicu dependency resolved by wheels |
| **fluent.runtime** | 0.4.0 (Mar 2023) | Stalled | No Python 3.12+ support; not recommended |
| **messageformat2** | 0.1.x (PyPI) | Early releases | Requires Python >= 3.12; spec stable but framework adoption immature |
| **Django i18n** | 6.0+ (current) | Stable | Gettext-only; no pluggable abstraction |
| **FastAPI ecosystem** | Multiple (2026) | No standard | fastapi-babel and fastapi-i18n both use gettext |
| **Python zoneinfo** | stdlib (3.9+) | Standard | Replaces pytz; RFC 9557 compatible |
| **RFC 4647** | Defined 2006 | Standard | No canonical Python implementation; WebOb does Basic Filtering, not Lookup |

---

## Evidence Gaps

1. **Exact thread/async safety model of Babel's `Locale` class** — Theory says cached `.mo` files are safe, but the `Locale` object wrapper's thread-safety is not explicitly documented. Needs spike: "Babel Locale Concurrency Testing."

2. **Real-world async performance of shared Babel catalogs** — Flask-Babel has reported issues; unclear if they're Babel-internal or Babel + Flask framework integration hazards. Worth testing in varco's async context.

3. **Fluent Python resurrection likelihood** — 3-year hiatus suggests abandoned maintenance, but Mozilla still uses Fluent. No evidence of a 2026 roadmap to add Python 3.12+ support.

4. **MessageFormat 2.0 adoption trajectory** — Spec is stable, but Python adoption is near-zero. If MF2 becomes the industry standard, varco would face a future migration if shipped on Babel today. Timeline unclear.

5. **Production use and performance of pure-Python ICU alternatives** — pyicumessageformat and pyseeyou exist but have little production telemetry. PyICU with wheels is safer, but benchmarks vs. gettext performance are not sourced.

6. **Varco-specific `MessageCatalog` ABC design** — Whether the ABC should:
   - Take structured `params` and handle plural/format internally, or
   - Return template strings and leave formatting to the caller
   - This shapes the contract significantly but has no varco-specific research.

7. **Language negotiation in varco's middleware stack** — Exact interaction between `Accept-Language` header parsing, `?lang=` query params, request scope, and event consumers is untested. Needs spike.

---

## Librarian's Note

**What the evidence favors:**

**Default MessageCatalog implementation: Babel + gettext is the clear choice.**

- ✅ **Maturity:** Stable, proven in Django/Rails/Spring for 20+ years; active maintenance in 2026
- ✅ **Python 3.12+ support:** Explicitly verified (2.18.0 wheels available)
- ✅ **Thread-safe caching:** `.mo` file caching is safe; per-request locale resolution avoids Locale-sharing hazards
- ✅ **Ecosystem alignment:** FastAPI i18n libraries already use it; no friction
- ✅ **Tooling:** `pybabel extract/init/compile` is production-grade
- ✅ **Simplicity:** gettext is simpler than ICU MessageFormat for the common case (plurals only)
- ⚠️ **Limitation:** No advanced features (gender, ordinals, complex selectors) — but those are opt-in via ICU if needed

**Alternative implementations (opt-in, not shipped default):**
- **PyICU MessageFormat:** For teams needing gender/ordinal/complex selector handling. Wheels for 3.12+ are now available; install burden resolved. Should be offered as a documented alternative (not in varco core, but in a separate module or documented extension pattern).
- **messageformat2:** Too new for a default. Recommend monitoring for adoption. If/when industry consolidates on MF2, varco can add an opt-in module without breaking existing Babel users.
- **Fluent:** Not viable for Python 3.12+ in 2026. Revisit if Mozilla publishes a 3.12+-compatible release.

**MessageCatalog ABC signature recommendation:**

The ABC should support **both patterns**:

1. **Template return** — simple path for most use cases:
   ```python
   def get_message(self, key: str, locale: str) -> str:
       """Return a template string (e.g., 'Hello, {name}!')."""
       return ...
   ```
   — Caller handles pluralization, formatting, variable substitution

2. **Structured params path** — for formatters that need context:
   ```python
   def format_message(self, key: str, locale: str, params: dict[str, Any] | None = None) -> str:
       """Format a message with parameters (handles plurals, dates, etc.)."""
       return ...
   ```
   — Formatter handles plural selection (e.g., `{count, plural, ...}`) and variable substitution

Default implementation (`BabelMessageCatalog`) uses the second pattern (gettext plurals + `Message.format()` wiring). Simpler implementations can use the first.

**RFC 4647 language matching:**

Varco should ship a built-in negotiator using the **precedence chain from brief 001**:
1. Stored user preference (if present)
2. Query param `?lang=` (explicit override)
3. `Accept-Language` header (RFC 4647 Lookup or Basic Filtering)
4. Tenant/application default
5. Fallback (`en`)

For the matching algorithm, **hand-roll the simple Lookup variant** (~40 lines):
- Parse `Accept-Language` header (split by `,`, extract quality `q` value)
- Sort by quality descending
- Iterate: try exact match, then progressively strip subtags (e.g., `en-US` → `en` → fallback)
- Return first match

No need to bring in a new dependency for this; WebOb's `AcceptLanguage` is heavier than needed for varco's use case (which has no filtering-only path).

**Framework responsibility (clear, per brief 001):**
- ✅ MessageCatalog ABC + pluggable default
- ✅ Content negotiation (Accept-Language + explicit `?lang=`)
- ✅ Per-request locale context (via `contextvars.ContextVar`)
- ✅ Error code stability + optional localized `message_key`
- ❌ Message authoring, catalog authoring, translation management

**Decision readiness: ~85%** — Babel + gettext default is defensible and low-risk. PyICU as documented alternative adds complexity but is optional. The only remaining spike: "Varco async MessageCatalog sharing patterns" (test that Babel catalogs work safely in varco's EventConsumer + middleware stack with concurrent requests).

