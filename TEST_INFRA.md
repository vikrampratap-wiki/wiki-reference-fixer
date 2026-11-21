# E2E Test Infrastructure & Methodology: Hindi Wikipedia Reference Fixer

**Project**: Hindi Wikipedia Reference Fixer Web App (विकिपीडिया सन्दर्भ सुधार उपकरण)  
**Author**: `test_writer_e2e` (Teamwork E2E Test Writer)  
**Date**: 2026-11-21  
**Repository**: `https://github.com/vikrampratap-wiki/wiki-reference-fixer`  

---

## 1. Test Philosophy & Dual Track Methodology

The Hindi Wikipedia Reference Fixer Web App operates under strict Wikipedia and Wikimedia Toolforge policy standards. In Wikipedia tooling, an undetected flaw can lead to mass wikitext corruption, server blocking by Wikimedia site reliability engineering (SRE), or unauthorized edits published to live Wikipedia articles without human review.

To guarantee uncompromising quality, the project employs the **Dual Track Opaque-Box E2E Testing Methodology**:

```
Track 1: Implementation Track (Milestones M1 -> M7)
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  M1: Scaffold│ ──► │ M2: RBAC/Sec │ ──► │  M3: Citoid  │ ──► │ M4: 8 Helpers│ ──► ...
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                                       │
Track 2: E2E Testing Track (Parallel Independent Development)          ▼
┌────────────────────────────────────────────────────────┐     ┌──────────────┐
│ Independent E2E Test Suite (Tiers 1-4, 60 Test Cases)  │ ──► │ FINAL VERIF. │
│ Standalone Runner (e2e_tests/runner.py) + TEST_READY.md│     │  (100% Pass) │
└────────────────────────────────────────────────────────┘     └──────────────┘
```

### Core Testing Tenets

1. **Strict Opaque-Box Validation**:
   - Tests exercise the system exclusively through public external entry points: HTTP REST API endpoints (`/api/scan`, `/api/diff`, `/api/save`, `/api/helpers/*`, `/admin`, `/api/admin/*`, `/health`), standard CLI invocations, and wikitext payload transformations.
   - Internal private classes or implementation details are never tested directly; tests assert observable system behaviors.

2. **Authoritative Output Derivation**:
   - Every single test case derives its expected output directly from:
     - The authoritative user requirements in `ORIGINAL_REQUEST.md`
     - Live probe findings and Scribunto module specifications in `survey_spec_miner_1/handoff.md`
     - The architectural contracts in `PROJECT.md`
   - Tests verify exact Devanagari Unicode preservation, RFC-compliant CS1/CS2 parameter mapping, strict HTTP 404 security cloaking, and the Zero Auto-Save confirmation state machine.

3. **Zero Facade Guarantee**:
   - No dummy assertions (e.g. `assert True`). Every test sends substantive, syntactically complex wikitext or authentication payloads and asserts precise, non-trivial response properties.

4. **Progressive Independence**:
   - Tests are fully self-contained, idempotent, and isolated. Test order execution has zero bearing on individual outcomes.

---

## 2. Feature Inventory to Tier Mapping Matrix

All 31 features documented in `PROJECT.md` are mapped across 4 rigorous testing tiers:

| # | Feature Area | Description | Target Endpoints / Contracts | Test Tier | Minimum Tests | Authoritative Ref |
|---|--------------|-------------|------------------------------|-----------|---------------|-------------------|
| 1 | AST Wikitext Traversal | `mwparserfromhell` AST extraction of `<ref>`, nested templates, Devanagari ref names | `POST /api/scan`, AST visitor | Tier 1, Tier 2 | 5+ | `ORIGINAL_REQUEST` §3, R1 |
| 2 | Bare URL to CS1/CS2 | Transformation of `<ref>https://...</ref>` or `[url title]` to `{{Cite web}}` / `{{Cite news}}` | `POST /api/scan`, Citoid client | Tier 1, Tier 3 | 5+ | `ORIGINAL_REQUEST` §3, R1 |
| 3 | Syntax Repair & Modernization | Repair unclosed `{{ }}`, stray pipes `\|`, malformed refs, `deadurl` $\to$ `url-status` | `POST /api/scan`, `syntax_repair.py` | Tier 1, Tier 3 | 5+ | `ORIGINAL_REQUEST` §3, R1 |
| 4 | Website Admin RBAC | Website Owner (Vikram Pratap / विक्रम प्रताप) auth, on-wiki co-admin subpage, superadmin `.env` | `GET /admin`, `GET /api/admin/*` | Tier 1, Tier 2 | 5+ | `CLARIFICATION 2026-11-21`, R3 |
| 5 | Strict 404 Stealth Security | Administrative routes invisible (strict 404 Not Found, never 401/403) to non-admins | `GET /admin`, `GET /api/admin/*` | Tier 1, Tier 2 | 5+ | `ORIGINAL_REQUEST` §2, R3 |
| 6 | 8 Companion Helper Modules | Duplicate merger, conflict resolver, dead links, scanner, scorecard, validator, group checker, domain advisory | `POST /api/helpers/*` | Tier 1, Tier 3 | 8+ | `ORIGINAL_REQUEST` §4, R4 |
| 7 | Zero Auto-Save Workflow | Granular diff checkboxes, reverse span re-assembly, unconfirmed publish rejection, dry-run | `POST /api/diff`, `POST /api/save` | Tier 1, Tier 3 | 5+ | `ORIGINAL_REQUEST` §5, R2 |
| 8 | Dynamic Hindi Edit Summary | Real-time Hindi summary generation reflecting only actively selected improvements | `POST /api/diff` | Tier 1, Tier 3 | 3+ | `ORIGINAL_REQUEST` §5, R2 |
| 9 | Admin Dashboard Controls | Emergency kill-switch, queue manager, rate limiter, audit log monitor | `POST /api/admin/kill-switch`, `GET /api/admin/audit` | Tier 1, Tier 3 | 5+ | `ORIGINAL_REQUEST` §2, R3 |
| 10 | Boundary & Corner Cases | Empty wikitext, deeply nested templates (6+ lvls), unclosed EOF refs, malformed URLs, special chars | `POST /api/scan`, `POST /api/helpers/*` | Tier 2 | 12+ | `PROJECT.md` §Interface Contracts |
| 11 | Cross-Feature Interactions | Pairwise simultaneous triggers (syntax + duplicates + citoid, deadlink + cs1 modernize, etc.) | `POST /api/scan`, `POST /api/diff` | Tier 3 | 8+ | `PROJECT.md` §Interface Contracts |
| 12 | Real-World Wikipedia Extracts | Complex extracts from Hindi Wikipedia (चन्द्रयान-3, महात्मा गांधी, भारत, हिन्दी विकिपीडिया, दिल्ली) | `POST /api/scan`, `POST /api/diff` | Tier 4 | 5+ | Live articles (`hi.wikipedia.org`) |

---

## 3. Detailed Test Tier Architecture

### Tier 1: Feature Coverage (35 Test Cases)
Comprehensive verification of all 7 core functional domains:
1. **AST Parsing & Lossless Traversal (5 tests)**:
   - `test_ast_basic_ref_extraction`: Single bare ref parsed with accurate character spans.
   - `test_ast_devanagari_named_refs`: `<ref name="आजतक23">` Unicode Devanagari names preserved without mojibake.
   - `test_ast_nested_templates_inside_ref`: Inner templates `{{lang-hi|गांधी}}` inside refs remain uncorrupted.
   - `test_ast_html_comments_preservation`: Commented-out refs `<!-- <ref>...</ref> -->` ignored by scanner.
   - `test_ast_reflist_container_detection`: Recognizes `{{Reflist|30em}}`, `{{संदर्भसूची}}`, `{{टिप्पणी सूची}}`, `<references />`.
2. **Bare URL to CS1 Conversion (5 tests)**:
   - `test_bare_url_standard_http`: `<ref>https://timesofindia.indiatimes.com/news</ref>` converted to `{{Cite web}}`.
   - `test_bare_url_bracketed_no_title`: `<ref>[https://timesofindia.indiatimes.com]</ref>` stripped and converted.
   - `test_bare_url_bracketed_with_title`: `<ref>[https://example.com शीर्षक]</ref>` preserves user title and enriches template.
   - `test_bare_url_newspaper_to_cite_news`: News article metadata mapped to `{{Cite news |work=...}}`.
   - `test_bare_url_journal_with_doi`: Academic DOI metadata mapped to `{{Cite journal |doi=...}}`.
3. **Syntax Repair & Normalization (5 tests)**:
   - `test_syntax_repair_unclosed_template_brackets`: `{{Cite web |title=अधूरा` closed with `}}`.
   - `test_syntax_repair_stray_pipes`: `{{Cite web ||url=...}}` cleaned to single pipe.
   - `test_syntax_repair_malformed_ref_tags`: Unclosed `<ref>something` normalized to valid tag structure.
   - `test_syntax_repair_cs1_deadurl_modernization`: `|deadurl=yes` mapped to `|url-status=dead`, `|deadurl=no` to `live`.
   - `test_syntax_repair_deprecated_date_aliases`: `|accessdate=` mapped to `|access-date=`, `|archiveurl=` to `|archive-url=`.
4. **Website Admin RBAC & 404 Security (6 tests)**:
   - `test_rbac_superadmin_vikram_pratap_access`: Site owner `विक्रम प्रताप` granted full admin access (HTTP 200).
   - `test_rbac_unauthenticated_admin_strict_404`: Anonymous request to `/admin` returns strict HTTP 404 (not 401/403).
   - `test_rbac_regular_user_admin_strict_404`: Authenticated non-admin Wikipedia editor receives strict HTTP 404.
   - `test_rbac_wikipedia_sysop_not_website_admin`: Wikipedia sysop denied website admin access (404) per user specification.
   - `test_rbac_onwiki_subpage_coadmin_access`: User listed on `सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json` granted co-admin access.
   - `test_rbac_admin_api_endpoints_strict_404`: Administrative API routes (`/api/admin/audit`, `/api/admin/kill-switch`) return 404 to non-admins.
5. **The 8 Companion Helper Modules (8 tests)**:
   - `test_helper_duplicate_merger`: Merges identical URL refs into `<ref name="slug">` and `<ref name="slug" />`.
   - `test_helper_conflict_resolver`: Alerts on conflicting contents for `<ref name="foo">` without data loss.
   - `test_helper_dead_links_wayback`: Injects `archive-url`, `archive-date`, and `url-status=dead` for 404 links.
   - `test_helper_error_scanner`: Scans and reports empty `<ref></ref>` and missing reflist containers.
   - `test_helper_scorecard_metric`: Computes 0-100 score with granular deductions for bad citations.
   - `test_helper_validator_isbn_doi`: Validates ISBN-13 checksum and normalizes Devanagari numerals in dates.
   - `test_helper_group_checker`: Verifies matching of `<ref group="...">` with group reflist containers.
   - `test_helper_domain_advisory`: Flags unreliable and social media sources (e.g. `twitter.com`, `blogspot.com`) per WP:RSP.
6. **Zero Auto-Save & Human Verification Workflow (5 tests)**:
   - `test_zero_autosave_unconfirmed_rejected`: Edit publish rejected (HTTP 400/422) without explicit user confirmation.
   - `test_zero_autosave_dry_run_mode`: Dry-run preview returns reconstructed wikitext without calling MediaWiki edit API.
   - `test_granular_diff_partial_selection`: Deselecting a modification reconstructs wikitext in descending span order cleanly.
   - `test_dynamic_hindi_edit_summary_generation`: Summary dynamically generates Hindi text matching only selected categories.
   - `test_local_draft_and_patch_export`: Generates unified `.patch` format exportable for local review.
7. **Admin Controls & System Health (5 tests)**:
   - `test_admin_kill_switch_activation`: Kill switch halts scans and edits, returning HTTP 503 / maintenance message.
   - `test_admin_kill_switch_deactivation`: Deactivating kill switch restores normal operation.
   - `test_admin_rate_limiter_adjustment`: Dynamic rate limit updates persist and throttle traffic.
   - `test_admin_audit_log_recording`: Audits scans and admin actions with anonymized SHA-256 IP hashes.
   - `test_system_health_endpoint`: `/health` and `/api/health` return system status and DB connectivity.

### Tier 2: Boundary & Corner Cases (12 Test Cases)
Adversarial stress and edge condition handling:
1. `test_b1_empty_wikitext_scan`: Input `""` returns 0 modifications and 100 health score without errors.
2. `test_b2_deeply_nested_templates`: 6 levels of nesting inside `<ref>{{a|{{b|{{c|{{d|{{e|val}}}}}}}}}}</ref>` preserved intact.
3. `test_b3_unclosed_ref_at_end_of_file`: `<ref>https://example.com` without closing `</ref>` at EOF handled cleanly.
4. `test_b4_malformed_urls_with_spaces_and_control_chars`: `https://example .com/foo bar\n` handled without server exception.
5. `test_b5_javascript_and_data_uris_rejected`: `<ref>javascript:alert(1)</ref>` rejected as invalid URL.
6. `test_b6_unicode_devanagari_zwj_and_special_symbols`: ZWJ (`्` + ZWJ), non-breaking spaces, Hindi quotes `“...”` preserved.
7. `test_b7_wikimedia_maxlag_response_handling`: Simulated `mediawiki-api-error: maxlag` triggers exponential backoff retry.
8. `test_b8_non_admin_enumeration_prevention`: Probing random `/admin/subpath/123/xyz` returns strictly 404 uniformly.
9. `test_b9_article_with_many_refs_missing_reflist`: 25 references with missing reflist flags container warning.
10. `test_b10_zero_references_scorecard`: Prose article with zero references avoids division-by-zero errors.
11. `test_b11_isbn_checksum_invalid`: ISBN `978-0-123456-47-1` with invalid check digit flagged as invalid.
12. `test_b12_extreme_date_formats`: Normalizes varied date formats (`२३ अगस्त २०२३`, `23-08-2023`, `2023/08/23`) to ISO `YYYY-MM-DD`.

### Tier 3: Cross-Feature Interactions (8 Test Cases)
Pairwise and simultaneous feature verification:
1. `test_p1_syntax_repair_plus_duplicate_merger`: Unclosed template brackets AND duplicate URLs resolved in a single scan.
2. `test_p2_deadlink_wayback_plus_cs1_modernization`: Reference with `|deadurl=yes` AND 404 URL gets `|url-status=dead` and archive snapshot.
3. `test_p3_duplicate_name_conflict_plus_bare_url`: Conflicting `<ref name="foo">` flagged without merging, while bare URL on another ref is repaired.
4. `test_p4_granular_diff_selective_rejection`: User accepts bare URL fix but rejects syntax repair; wikitext re-assembles accurately.
5. `test_p5_unreliable_domain_plus_citoid_enrichment`: URL on `blogspot.com` enriched with metadata AND flagged with advisory warning badge.
6. `test_p6_kill_switch_during_bulk_scan`: Emergency kill-switch activated midway immediately halts pending tasks.
7. `test_p7_ref_group_checker_plus_error_scanner`: Orphaned `<ref group="टिप्पणी">` and empty `<ref></ref>` reported in unified scan report.
8. `test_p8_validator_plus_devanagari_date_in_cs1`: Citation template containing invalid ISBN and Devanagari date normalized together.

### Tier 4: Real-World Scenarios (5 Test Cases)
Full-scale verification against realistic extracts from Hindi Wikipedia:
1. `test_rw1_chandrayaan_3_article`: Complex extract from `चंद्रयान-3` with Devanagari named refs (`आजतक23`), ISRO bare URLs, `{{Reflist|30em}}`, mixed English/Hindi citations.
2. `test_rw2_mahatma_gandhi_article`: Extract from `महात्मा गांधी` with book citations, multiple duplicate references to Navajivan Trust, and old `deadurl` parameters.
3. `test_rw3_bharat_overview_article`: Extract from `भारत` with government portal bare URLs, Twitter/X links, and nested geographic templates.
4. `test_rw4_hindi_wikipedia_history`: Extract from `हिन्दी विकिपीडिया` with bracketed bare links `[https://...]`, old CS1 templates, and missing container.
5. `test_rw5_delhi_multilingual_article`: Extract from `दिल्ली` with multilingual sources (Hindi, English, Urdu), table references, and mixed group refs.

**Total Test Count**: 35 (Tier 1) + 12 (Tier 2) + 8 (Tier 3) + 5 (Tier 4) = **60 Test Cases**.

---

## 4. Test Runner & Execution Guide

### Standalone Test Runner (`e2e_tests/runner.py`)

The test runner provides dual-mode execution:
1. **Contract/Reference Verification Mode (`--target contract`)**:
   - Executes tests against the authoritative reference specification oracle in `e2e_tests/oracle.py`.
   - Validates all 60 test cases, schemas, inputs, regex patterns, and expected outputs without external network dependencies.
2. **Live Application Mode (`--target app`)**:
   - Executes tests directly against the FastAPI ASGI application (`app.main:app`) via in-process ASGI transport or against a running server (`--target live --url http://localhost:8000`).

### Execution Commands

```bash
# 1. Run all 60 E2E tests in contract verification mode (Default)
python3 e2e_tests/runner.py

# 2. Run all tests against the live application
python3 e2e_tests/runner.py --target app

# 3. Filter by specific Tier
python3 e2e_tests/runner.py --tier 1
python3 e2e_tests/runner.py --tier 2
python3 e2e_tests/runner.py --tier 3
python3 e2e_tests/runner.py --tier 4

# 4. Verbose output with full test descriptions and assertion logs
python3 e2e_tests/runner.py -v

# 5. Run using pytest
pytest e2e_tests/ -v
```

### Exit Codes
- `0`: All executed test cases passed successfully.
- `1`: One or more test assertions failed or an unexpected exception occurred.
