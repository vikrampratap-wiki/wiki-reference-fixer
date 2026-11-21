# Project: Hindi Wikipedia Reference Fixer Web App (विकिपीडिया सन्दर्भ सुधार उपकरण)

## Architecture
- **Backend**: Python 3.11+ / FastAPI, Uvicorn, Pydantic, HTTPX AsyncClient with connection pooling, Wikimedia `User-Agent` and `maxlag=5` exponential backoff.
- **Database**: SQLite with `aiosqlite` and WAL mode (`PRAGMA journal_mode=WAL`), storing users, requests, audit logs, caches, rules, and system settings.
- **Parsing Engine**: `mwparserfromhell` AST parsing for lossless ref extraction, syntax repair, and CS1/CS2 modernization.
- **Security & RBAC**: Website Admin RBAC verifying owner (Vikram Pratap / विक्रम प्रताप), dynamic on-wiki co-admin subpage (`सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json`) with 10-minute cache, `.env` superadmin override, and strict 404 Not Found masking for non-admins.
- **Frontend**: Zero-build Vanilla ES6+, modern CSS3, HTML5, 100% Hindi interface, mobile-first responsive layout, side-by-side granular diff with per-reference checkboxes (स्वीकार/अस्वीकार), dynamic Hindi edit summary generator, and Zero Auto-Save confirmation workflow.
- **Deployment**: Wikimedia Toolforge (`Procfile`, `webservice.json`) & Docker / Linux compatibility.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | FastAPI Scaffolding & Lifespan | App initialization, CORS, HTTPX AsyncClient lifecycle, error handlers | M1 | ORIGINAL_REQUEST §1, R1 |
| 2 | Async SQLite Database & Schema | Async SQLite connection, WAL mode, tables (users, requests, audit, cache, settings) | M1 | ORIGINAL_REQUEST §1, R1 |
| 3 | Config & Wikimedia API Policy | Pydantic Settings, .env.example, custom User-Agent, maxlag=5 exponential backoff | M1 | ORIGINAL_REQUEST §1, R5 |
| 4 | Wikimedia OAuth 2.0 Flow | Login redirect, callback exchange, user identity extraction, secure session cookies | M2 | ORIGINAL_REQUEST §2, R3 |
| 5 | Website-Native Admin Verification | Authenticated check for Vikram Pratap (विक्रम प्रताप), dynamic subpage fetch, superadmin fallback | M2 | CLARIFICATION 2026-11-21, R3 |
| 6 | Strict 404 Admin Concealment | Middleware masking /admin and /api/admin/* with strict 404 for non-admins | M2 | ORIGINAL_REQUEST §2, R3 |
| 7 | Audit Logging & Admin Controls | SQLite audit logger, emergency kill-switch, queue manager, rate limiter | M2 | ORIGINAL_REQUEST §2, R3 |
| 8 | Lossless AST Wikitext Parser | `mwparserfromhell` AST traversal, `<ref>` and `{{सन्दर्भ सूची}}` extraction without breaking templates | M3 | ORIGINAL_REQUEST §3, R1 |
| 9 | Citoid REST API Integration | Asynchronous Citoid client (`/api/rest_v1/data/citation/mediawiki/{url}`), 1 rps pacing | M3 | ORIGINAL_REQUEST §3, R1 |
| 10 | Bare URL to CS1/CS2 Conversion | Transform `<ref>https://...</ref>` or `[url title]` into standard `{{Cite web}}`/`{{Cite news}}` | M3 | ORIGINAL_REQUEST §3, R1 |
| 11 | Syntax Repair & Normalization | Repair unclosed `{{ }}`, stray pipes `|`, malformed `<ref>` tags, CS1 `deadurl` modernization | M3 | ORIGINAL_REQUEST §3, R1 |
| 12 | Non-destructive Safety Guards | Zero changes to valid templates, preserve redirects, no cosmetic whitespace alterations | M3 | ORIGINAL_REQUEST §3, R1 |
| 13 | Duplicate Ref Merger | Detect identical URLs/contents and merge into `<ref name="...">` | M4 | ORIGINAL_REQUEST §4, R4 |
| 14 | Duplicate Name Conflict Resolver | Detect different references with identical name attribute and warn user | M4 | ORIGINAL_REQUEST §4, R4 |
| 15 | Dead Link & Archive Helper | 404 URL detection, Internet Archive Wayback Machine API query, inject archive parameters | M4 | ORIGINAL_REQUEST §4, R4 |
| 16 | Reference Error Scanner | Identify empty refs, undefined ref names, missing reflist containers | M4 | ORIGINAL_REQUEST §4, R4 |
| 17 | Reference Health Scorecard | 0-100 reference health score calculation, breakdown metrics, Hindi grading rubric | M4 | ORIGINAL_REQUEST §4, R4 |
| 18 | ISBN/ISSN/DOI & Date Validator | Checksum validation for ISBN/ISSN/DOI, Devanagari numeral & month date normalization | M4 | ORIGINAL_REQUEST §4, R4 |
| 19 | Ref Group Consistency Checker | Match `<ref group="...">` with corresponding group reference containers | M4 | ORIGINAL_REQUEST §4, R4 |
| 20 | Unreliable Domain Advisory Flag | Advisory warnings and badges for unreliable/social media domains per WP:RSP | M4 | ORIGINAL_REQUEST §4, R4 |
| 21 | Local Draft & Patch Export | Browser localStorage persistence, .txt / .patch download, direct clipboard copy | M4 | ORIGINAL_REQUEST §4, R4 |
| 22 | 100% Hindi Mobile-First UI | Clean Devanagari typography (line-height 1.68), responsive mobile-first HTML5+CSS3 | M5 | ORIGINAL_REQUEST §5, R2 |
| 23 | Granular Diff & Zero Auto-Save | Side-by-side diff with per-reference checkboxes (स्वीकार/अस्वीकार), pre-flight dry run, confirm modal | M5 | ORIGINAL_REQUEST §5, R2 |
| 24 | Dynamic Hindi Edit Summary Gen | Auto-generate concise Hindi edit summary based on actively selected improvements | M5 | ORIGINAL_REQUEST §5, R2 |
| 25 | Hidden Admin Dashboard UI | Isolated admin HTML/JS bundle: kill-switch toggle, queue manager, audit log viewer | M5 | ORIGINAL_REQUEST §5, R3 |
| 26 | Unit & Mock Test Suite (25+ Tests) | Pytest suite covering AST, Citoid/Wayback mocks, RBAC 404 security, Zero Auto-Save | M6 | ORIGINAL_REQUEST §6, R5 |
| 27 | Code Quality & Typing | Flake8/Ruff and Mypy compliance with zero errors | M6 | ORIGINAL_REQUEST §6, R5 |
| 28 | Documentation & Toolforge Deployment | Dual-language README (Hindi/English), Procfile, webservice.json, .env.example | M7 | ORIGINAL_REQUEST §7, R5 |
| 29 | Git Repository Readiness | Clean Git commit history, proper .gitignore, ready for GitHub remote sync | M7 | ORIGINAL_REQUEST §7, R5 |
| 30 | Opaque-Box E2E Test Suite (T1-T4) | 4-tier requirement-driven E2E test suite published with TEST_READY.md | E2E_TRACK | Project Pattern Dual Track |
| 31 | 100% E2E Pass & Tier 5 Hardening | Final verification: 100% pass of Tiers 1-4 + Tier 5 adversarial coverage hardening | FINAL | Project Pattern Final Milestone |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Track | Independent requirement-driven test suite (Tiers 1-4) & runner, publish TEST_READY.md | none | DONE |
| M1 | Scaffolding & Core Architecture | FastAPI setup, SQLite DB with WAL mode, HTTPX async client, config management | none | DONE |
| M2 | Website Admin RBAC & Security | Owner auth (Vikram Pratap), on-wiki subpage fetch, 404 middleware, audit logger | M1 | DONE |
| M3 | AST Parsing & Citoid Engine | mwparserfromhell AST parser, Citoid client, Bare URL fix, syntax repair, CS1 update | M1 | IN_PROGRESS |
| M4 | 8 Companion Helper Modules | Duplicate merger, conflict resolver, dead link/Wayback, scanner, scorecard, validator | M3 | PLANNED |
| M5 | Frontend UI & Zero Auto-Save | 100% Hindi mobile-first UI, granular diff checkboxes, summary gen, hidden admin UI | M2, M4 | PLANNED |
| M6 | Automated Testing & QA Suite | 25+ pytest tests (AST, mock APIs, RBAC 404, Zero Auto-Save), linting & mypy | M1-M5 | PLANNED |
| M7 | Documentation & Toolforge | README (Hindi/English), Toolforge Procfile/webservice.json, git commit/push | M6 | PLANNED |
| FINAL | E2E Pass & Tier 5 Hardening | Pass 100% of E2E test suite (T1-T4) + Tier 5 adversarial coverage hardening | M7, E2E | PLANNED |

## Interface Contracts
### `app.core.config` ↔ All Modules
- `get_settings() -> Settings`: Returns immutable application configuration (OAuth, DB path, Superadmin usernames, User-Agent, maxlag, port).

### `app.db.database` ↔ `app.services.audit` & `app.services.auth`
- `get_db() -> AsyncGenerator[AsyncSession, None]`: Provides async SQLite session with WAL mode.
- `log_audit_event(event_type: str, username: str, details: dict, ip_address: str) -> None`: Appends structured audit record.

### `app.services.auth` ↔ `app.middleware.rbac`
- `verify_website_admin(username: str) -> bool`: Checks if username is `विक्रम प्रताप` or in cached `सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json` or `.env` `SUPERADMIN_USERNAMES`.
- `require_admin(request: Request) -> User`: Dependency returning authenticated admin or raising HTTP 404 (Not Found).

### `app.services.citoid` ↔ `app.services.parser`
- `fetch_citation_metadata(url: str) -> dict`: Queries Citoid REST API with 1 rps rate limiting, maxlag=5 handling, and returns structured citation fields.

### `app.services.parser` ↔ `app.services.helpers`
- `parse_wikitext_references(wikitext: str) -> ParsedDocument`: Parses AST, extracts `<ref>` nodes with character offsets, content, and templates.
- `apply_granular_modifications(original_wikitext: str, selected_modifications: list[Modification]) -> str`: Reconstructs wikitext in descending span order without corrupting unselected text.

### `app.services.helpers.*` ↔ `app.api.helpers`
- Each helper implements `analyze(doc: ParsedDocument, options: HelperOptions) -> HelperResult`.

## Code Layout
```
zealous-meitner/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application entrypoint & lifespan
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               # Pydantic Settings & environment variables
│   │   └── security.py             # Session cookies, hashing, token management
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py             # aiosqlite / SQLAlchemy async engine & session
│   │   └── models.py               # DB tables (User, Request, AuditLog, Cache, Setting)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── http_client.py          # HTTPX AsyncClient with maxlag=5 & custom User-Agent
│   │   ├── auth.py                 # MediaWiki Action API & on-wiki subpage admin verifier
│   │   ├── citoid.py               # Wikimedia Citoid REST API integration
│   │   ├── wayback.py              # Internet Archive Wayback Machine client
│   │   ├── parser.py               # mwparserfromhell AST parser & wikitext reconstructor
│   │   ├── syntax_repair.py        # Bracket, pipe, and ref syntax repair
│   │   └── helpers/                # The 8 Companion Helper Modules
│   │       ├── __init__.py
│   │       ├── duplicate_merger.py # Helper 1: Duplicate ref merger
│   │       ├── conflict_resolver.py# Helper 2: Duplicate name conflict resolver
│   │       ├── dead_links.py       # Helper 3: Dead link & archive helper
│   │       ├── error_scanner.py    # Helper 4: Reference error scanner
│   │       ├── scorecard.py        # Helper 5: Reference health scorecard (0-100)
│   │       ├── validator.py        # Helper 6: ISBN/ISSN/DOI & date validator
│   │       ├── group_checker.py    # Helper 7: Ref group consistency checker
│   │       └── domain_advisory.py  # Helper 8: Unreliable domain advisory badges
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── rbac.py                 # Strict 404 security middleware for admin routes
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py               # API route aggregator
│   │   ├── scan.py                 # Wikitext & article scan endpoints
│   │   ├── diff.py                 # Granular diff & modification preview endpoints
│   │   ├── save.py                 # Zero Auto-Save publish endpoint (human-confirmed)
│   │   ├── helpers.py              # Companion helper toggle endpoints
│   │   ├── auth.py                 # OAuth login, callback, logout
│   │   └── admin.py                # Protected admin endpoints (hidden behind 404)
│   ├── static/
│   │   ├── css/
│   │   │   ├── style.css           # Mobile-first Hindi UI styling (line-height 1.68)
│   │   │   └── admin.css           # Admin styling (served only to admin)
│   │   └── js/
│   │       ├── app.js              # Main frontend application logic
│   │       ├── diff.js             # Granular diff viewer with checkbox toggles
│   │       ├── summary.js          # Dynamic Hindi edit summary generator
│   │       ├── helpers.js          # Helper UI controls and advisory badges
│   │       └── admin.js            # Admin dashboard logic (served only to admin)
│   └── templates/
│       ├── index.html              # Main user interface (100% Hindi)
│       └── admin.html              # Isolated admin dashboard template
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Pytest fixtures, mock HTTPX clients, test DB
│   ├── test_ast_parsing.py         # mwparserfromhell & syntax repair tests
│   ├── test_citoid_wayback.py      # Citoid & Wayback API mock tests
│   ├── test_rbac_security.py       # Website Admin RBAC & strict 404 tests
│   ├── test_zero_autosave.py       # Zero auto-save, diff, and edit summary tests
│   └── test_companion_helpers.py   # Tests for all 8 companion helper modules
├── e2e_tests/                      # Dual Track E2E test suite (Tiers 1-4)
│   ├── runner.py                   # Opaque-box test runner
│   ├── tier1_feature_coverage.py   # Tier 1 tests (5+ per feature)
│   ├── tier2_boundary_corner.py    # Tier 2 boundary & corner tests
│   ├── tier3_pairwise.py           # Tier 3 cross-feature interaction tests
│   └── tier4_real_world.py         # Tier 4 real-world Wikipedia article tests
├── Procfile                        # Toolforge deployment Procfile
├── webservice.json                 # Toolforge webservice definition
├── requirements.txt                # Python dependencies
├── pyproject.toml                  # Project packaging & tooling configuration
├── .env.example                    # Environment template
└── README.md                       # Comprehensive documentation (Hindi & English)
```
