"""
Tier 1: Feature Coverage E2E Tests.
Contains at least 5 tests per core feature area across all 7 functional domains:
1. AST Parsing & Traversal (5 tests)
2. Bare URL to CS1 Conversion (5 tests)
3. Syntax Repair & Normalization (5 tests)
4. Website Admin RBAC & 404 Security (6 tests)
5. The 8 Companion Helper Modules (8 tests)
6. Zero Auto-Save & Granular Diff Workflow (5 tests)
7. Admin Controls & System Health (5 tests)

Total: 39 comprehensive test cases.
Authoritative source: ORIGINAL_REQUEST.md (§1-§5, R1-R5), PROJECT.md.
"""

from e2e_tests.client import get_test_client


# ==============================================================================
# Feature Area 1: AST Parsing & Lossless Wikitext Traversal
# ==============================================================================

def test_ast_basic_ref_extraction(client=None):
    """
    Feature: AST Wikitext Traversal
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: Wikitext with standard bare <ref> tag
    Expected: Status 200, ref extracted with valid span offsets and bare_url type.
    """
    client = client or get_test_client()
    wikitext = "यह एक उदाहरण लेख है।<ref>https://example.com/test</ref> लेख का अंत।"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "modifications" in data
    assert len(data["modifications"]) == 1
    mod = data["modifications"][0]
    assert mod["type"] == "bare_url"
    assert mod["span_start"] == wikitext.find("<ref>")
    assert mod["span_end"] == wikitext.find("</ref>") + len("</ref>")
    assert "https://example.com/test" in mod["proposed_snippet"]


def test_ast_devanagari_named_refs(client=None):
    """
    Feature: AST Wikitext Traversal
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1; survey_spec_miner_1 §4
    Input: Self-closing ref with Unicode Devanagari name attribute <ref name="आजतक23">
    Expected: Character encoding and Unicode Devanagari name attribute preserved without mojibake.
    """
    client = client or get_test_client()
    wikitext = 'चंद्रयान-3 सफल रहा।<ref name="आजतक23">https://aajtak.in/science</ref> आगे पाठ।'
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["modifications"]) == 1
    mod = data["modifications"][0]
    assert 'name="आजतक23"' in mod["proposed_snippet"]
    assert "aajtak.in" in mod["proposed_snippet"]


def test_ast_nested_templates_inside_ref(client=None):
    """
    Feature: AST Wikitext Traversal
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: Nested template {{lang-hi|गांधी}} inside reference
    Expected: Lossless parsing; nested inner template is preserved without corruption.
    """
    client = client or get_test_client()
    wikitext = (
        'गांधीजी का जीवन।<ref>{{Cite web '
        '|title={{lang-hi|महात्मा गांधी}} |url=https://example.org/gandhi}}</ref>'
    )

    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    # Pre-existing valid Cite web with nested template should not be corrupted or broken
    assert data["scorecard"]["total_refs"] == 1
    assert data["scorecard"]["broken_syntax"] == 0


def test_ast_html_comments_preservation(client=None):
    """
    Feature: AST Wikitext Traversal
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §4.21; ORIGINAL_REQUEST.md §3
    Input: Reference inside HTML comments: <!-- <ref>https://ignored.com</ref> -->
    Expected: Commented-out URL must not be extracted or modified as an active reference.
    """
    client = client or get_test_client()
    wikitext = 'सक्रिय पाठ।<!-- <ref>https://ignored.com</ref> --> अन्य सक्रिय पाठ।'
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    # Ignored inside comment
    assert len(data["modifications"]) == 0
    assert data["scorecard"]["bare_urls"] == 0


def test_ast_reflist_container_detection(client=None):
    """
    Feature: AST Wikitext Traversal
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1; survey_spec_miner_1 §4.4
    Input: Article with <ref> tags and {{Reflist|30em}} container
    Expected: Scorecard flags has_reflist=True; no penalty for missing container.
    """
    client = client or get_test_client()
    wikitext = "तथ्य।<ref>https://example.com</ref>\n== संदर्भ ==\n{{Reflist|30em}}"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scorecard"]["has_reflist"] is True
    assert data["scorecard"]["total_refs"] == 1


# ==============================================================================
# Feature Area 2: Bare URL to CS1/CS2 Conversion
# ==============================================================================

def test_bare_url_standard_http(client=None):
    """
    Feature: Bare URL to CS1 Conversion
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: <ref>https://timesofindia.indiatimes.com/news</ref>
    Expected: Converted to {{Cite web}} with url, title, websiteTitle/website, access-date.
    """
    client = client or get_test_client()
    wikitext = "समाचार।<ref>https://timesofindia.indiatimes.com/news</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = data["modifications"][0]
    assert mod["type"] == "bare_url"
    assert "{{Cite web" in mod["proposed_snippet"]
    assert "url=https://timesofindia.indiatimes.com/news" in mod["proposed_snippet"]
    assert "access-date=" in mod["proposed_snippet"]


def test_bare_url_bracketed_no_title(client=None):
    """
    Feature: Bare URL to CS1 Conversion
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §4.17; ORIGINAL_REQUEST.md §3
    Input: Bracketed URL without title: <ref>[https://timesofindia.indiatimes.com]</ref>
    Expected: Brackets stripped; converted to standard {{Cite web}}.
    """
    client = client or get_test_client()
    wikitext = "विवरण।<ref>[https://timesofindia.indiatimes.com]</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["modifications"]) == 1
    assert "{{Cite web" in data["modifications"][0]["proposed_snippet"]


def test_bare_url_bracketed_with_title(client=None):
    """
    Feature: Bare URL to CS1 Conversion
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §4.18; ORIGINAL_REQUEST.md §3
    Input: <ref>[https://example.com/page हिन्दी शीर्षक]</ref>
    Expected: Extracted title preserved in |title= parameter of Cite web.
    """
    client = client or get_test_client()
    wikitext = "विवरण।<ref>[https://example.com/page हिन्दी शीर्षक]</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = data["modifications"][0]
    assert "title=हिन्दी शीर्षक" in mod["proposed_snippet"]
    assert "url=https://example.com/page" in mod["proposed_snippet"]


def test_bare_url_newspaper_to_cite_news(client=None):
    """
    Feature: Bare URL to CS1 Conversion
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §3.15; ORIGINAL_REQUEST.md §3
    Input: News URL (jagran.com) mapped by Citoid as newspaperArticle
    Expected: Proposed template is {{Cite news}} with |work= parameter.
    """
    client = client or get_test_client()
    wikitext = "राष्ट्रीय खबर।<ref>https://www.jagran.com/news/national-12345.html</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = data["modifications"][0]
    assert "{{Cite news" in mod["proposed_snippet"]
    assert "work=दैनिक जागरण" in mod["proposed_snippet"]


def test_bare_url_preserves_ref_attributes(client=None):
    """
    Feature: Bare URL to CS1 Conversion
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: <ref name="source1">https://example.com</ref>
    Expected: Attributes like name="source1" preserved in outer <ref> wrapper.
    """
    client = client or get_test_client()
    wikitext = 'स्रोत।<ref name="source1">https://example.com</ref>'
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = data["modifications"][0]
    assert 'name="source1"' in mod["proposed_snippet"]
    assert "{{Cite web" in mod["proposed_snippet"]


# ==============================================================================
# Feature Area 3: Syntax Repair & Normalization
# ==============================================================================

def test_syntax_repair_unclosed_template_brackets(client=None):
    """
    Feature: Syntax Repair & Normalization
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: Unclosed template brackets: {{Cite web |title=अधूरा साँचा
    Expected: Proposed modification balances and appends missing }} brackets.
    """
    client = client or get_test_client()
    wikitext = "पाठ {{Cite web |title=अधूरा साँचा"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    types = [m["type"] for m in data["modifications"]]
    assert "syntax_repair" in types
    repair = [m for m in data["modifications"] if m["type"] == "syntax_repair"][0]
    assert repair["proposed_snippet"].endswith("}}")


def test_syntax_repair_stray_pipes(client=None):
    """
    Feature: Syntax Repair & Normalization
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: Stray duplicate pipes: {{Cite web ||url=https://test.com |title=परीक्षण}}
    Expected: Repaired to single valid pipe delimiter |.
    """
    client = client or get_test_client()
    wikitext = "{{Cite web ||url=https://test.com |title=परीक्षण}}"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = [m for m in data["modifications"] if m["type"] == "syntax_repair"][0]
    assert "||" not in mod["proposed_snippet"]
    assert "|url=https://test.com" in mod["proposed_snippet"]


def test_syntax_repair_malformed_ref_tags(client=None):
    """
    Feature: Syntax Repair & Normalization
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §3, R1
    Input: Reference with unclosed or broken tag structure
    Expected: Identified by scorecard and scanner.
    """
    client = client or get_test_client()
    wikitext = "पाठ <ref></ref> अतिरिक्त पाठ"
    resp = client.post("/api/helpers/error-scanner", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["error_count"] >= 1
    assert any(err["type"] == "empty_ref" for err in data["errors"])


def test_syntax_repair_cs1_deadurl_modernization(client=None):
    """
    Feature: Syntax Repair & Normalization
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §4.13; ORIGINAL_REQUEST.md §3, R1
    Input: {{Cite web |url=https://example.com |title=टेस्ट |deadurl=yes}}
    Expected: |deadurl=yes modernized to |url-status=dead.
    """
    client = client or get_test_client()
    wikitext = "{{Cite web |url=https://example.com |title=टेस्ट |deadurl=yes}}"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = [m for m in data["modifications"] if m["type"] == "cs1_modernize"][0]
    assert "deadurl" not in mod["proposed_snippet"]
    assert "url-status=dead" in mod["proposed_snippet"]


def test_syntax_repair_deprecated_date_aliases(client=None):
    """
    Feature: Syntax Repair & Normalization
    Tier: 1
    Authoritative Source: survey_spec_miner_1 §4.16; ORIGINAL_REQUEST.md §3, R1
    Input: {{Cite web |url=https://test.org |deadurl=no |accessdate=... |archiveurl=...}}
    Expected: Parameters modernized to access-date, archive-url, archive-date, url-status=live.
    """
    client = client or get_test_client()
    wikitext = (
        "{{Cite web |url=https://test.org |title=त |deadurl=no |accessdate=2021-01-01 "
        "|archiveurl=http://web.archive.org/t |archivedate=20210101}}"
    )

    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    mod = [m for m in data["modifications"] if m["type"] == "cs1_modernize"][0]
    assert "url-status=live" in mod["proposed_snippet"]
    assert "access-date=" in mod["proposed_snippet"]
    assert "archive-url=" in mod["proposed_snippet"]
    assert "archive-date=" in mod["proposed_snippet"]


# ==============================================================================
# Feature Area 4: Website Admin RBAC & Strict 404 Security
# ==============================================================================

def test_rbac_superadmin_vikram_pratap_access(client=None):
    """
    Feature: Website Admin RBAC
    Tier: 1
    Authoritative Source: CLARIFICATION 2026-11-21; ORIGINAL_REQUEST.md §2, R3
    Input: Authenticated request with username 'विक्रम प्रताप' to /admin
    Expected: HTTP 200 OK granted (Site owner/superadmin authority).
    """
    client = client or get_test_client()
    resp = client.get("/admin", headers={"X-Wiki-User": "विक्रम प्रताप"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert "एडमिन पैनल" in resp.text


def test_rbac_unauthenticated_admin_strict_404(client=None):
    """
    Feature: Strict 404 Security
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Anonymous unauthenticated request to /admin
    Expected: HTTP 404 Not Found (STRICT 404 masking; NEVER 401 or 403).
    """
    client = client or get_test_client()
    resp = client.get("/admin")
    assert resp.status_code == 404, f"Expected strict 404, got {resp.status_code}"


def test_rbac_regular_user_admin_strict_404(client=None):
    """
    Feature: Strict 404 Security
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Authenticated regular Wikipedia editor (StandardEditor) to /admin
    Expected: Strict HTTP 404 Not Found.
    """
    client = client or get_test_client()
    resp = client.get("/admin", headers={"X-Wiki-User": "StandardEditor"})
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_rbac_wikipedia_sysop_not_website_admin(client=None):
    """
    Feature: Website Admin RBAC Clarification
    Tier: 1
    Authoritative Source: CLARIFICATION 2026-11-21:
    'विकिपीडिया के एडमिन की बात नहीं कर रहा मैं वेबसाइट एडमिन यानि मेरी चर्चा कर रहा है'
    Input: Request from a Wikipedia sysop (RandomWikiSysop)
    Expected: Strict HTTP 404 Not Found. Wikipedia sysops are regular users of this tool.
    """
    client = client or get_test_client()
    resp = client.get("/admin", headers={"X-Wiki-User": "RandomWikiSysop"})
    assert resp.status_code == 404, "Wikipedia sysop must receive strict 404 on website admin panel"


def test_rbac_onwiki_subpage_coadmin_access(client=None):
    """
    Feature: Dynamic Co-Admin Subpage
    Tier: 1
    Authoritative Source: CLARIFICATION 2026-11-21; ORIGINAL_REQUEST.md §2, R3
    Input: User listed on सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json (CoAdminUser1)
    Expected: HTTP 200 OK granted as authorized co-admin.
    """
    client = client or get_test_client()
    resp = client.get("/admin", headers={"X-Wiki-User": "CoAdminUser1"})
    assert resp.status_code == 200
    assert "एडमिन पैनल" in resp.text


def test_rbac_admin_api_endpoints_strict_404(client=None):
    """
    Feature: Strict 404 Security for Admin APIs
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Non-admin request to /api/admin/audit and /api/admin/kill-switch
    Expected: Strict HTTP 404 Not Found.
    """
    client = client or get_test_client()
    resp1 = client.get("/api/admin/audit")
    assert resp1.status_code == 404
    resp2 = client.post("/api/admin/kill-switch", json={"active": True})
    assert resp2.status_code == 404


# ==============================================================================
# Feature Area 5: The 8 Companion Helper Modules
# ==============================================================================

def test_helper_duplicate_merger(client=None):
    """
    Feature: Duplicate Ref Merger (Helper 1)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Wikitext with two references containing the exact same body
    Expected: Detects duplicate, proposes merger to <ref name="ref_1" />.
    """
    client = client or get_test_client()
    wikitext = "बिंदु 1<ref>https://same-link.org</ref> बिंदु 2<ref>https://same-link.org</ref>"
    resp = client.post("/api/helpers/duplicate-merger", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["duplicates_found"] >= 1
    assert any('<ref name="' in m["replacement"] for m in data["proposed_merges"])


def test_helper_conflict_resolver(client=None):
    """
    Feature: Duplicate Name Conflict Resolver (Helper 2)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Two refs with same name="foo" but differing contents
    Expected: Conflict alert triggered without corrupting or deleting either content.
    """
    client = client or get_test_client()
    wikitext = '<ref name="foo">सामग्री A</ref> पाठ <ref name="foo">भिन्न सामग्री B</ref>'
    resp = client.post("/api/helpers/conflict-resolver", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflicts_count"] == 1
    assert data["conflicts"][0]["ref_name"] == "foo"
    assert len(data["conflicts"][0]["conflicting_contents"]) == 2


def test_helper_dead_links_wayback(client=None):
    """
    Feature: Dead Link & Archive Helper (Helper 3)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Wikitext containing dead/404 URL
    Expected: Retrieves Wayback snapshot; provides archive-url, archive-date, url-status=dead.
    """
    client = client or get_test_client()
    wikitext = "कड़ी।<ref>{{Cite web |url=https://example.com/dead-link |title=मृत पृष्ठ}}</ref>"
    resp = client.post("/api/helpers/dead-links", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["dead_links_count"] >= 1
    res = data["results"][0]
    assert "web.archive.org" in res["archive_url"]
    assert res["url_status"] == "dead"


def test_helper_error_scanner(client=None):
    """
    Feature: Reference Error Scanner (Helper 4)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Wikitext with references but missing reference list container
    Expected: Scans and flags missing_reflist error.
    """
    client = client or get_test_client()
    wikitext = "लेख पाठ।<ref>https://example.com</ref> कोई संदर्भ सूची नहीं।"
    resp = client.post("/api/helpers/error-scanner", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    err_types = [e["type"] for e in data["errors"]]
    assert "missing_reflist" in err_types


def test_helper_scorecard_metric(client=None):
    """
    Feature: Reference Health Scorecard (Helper 5)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Wikitext with bare URLs
    Expected: Score calculated from 0 to 100 with deduction for bare URLs.
    """
    client = client or get_test_client()
    wikitext = "तथ्य 1<ref>https://bare1.com</ref> तथ्य 2<ref>https://bare2.com</ref>"
    resp = client.post("/api/helpers/scorecard", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert 0 <= data["health_score"] <= 100
    assert data["bare_urls"] == 2
    assert data["total_refs"] == 2


def test_helper_validator_isbn_doi(client=None):
    """
    Feature: ISBN/ISSN/DOI & Date Validator (Helper 6)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Book ref with ISBN-13 checksum and Devanagari numerals in date: |date=२०२३-०८-२३
    Expected: Validates ISBN checksum, detects Devanagari numerals for normalization.
    """
    client = client or get_test_client()
    # 978-0-306-40615-7 is a valid ISBN-13
    wikitext = "{{Cite book |title=पुस्तक |isbn=978-0-306-40615-7 |date=२०२३-०८-२३}}"
    resp = client.post("/api/helpers/validator", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["isbn"]["valid"] is True
    assert data["results"]["date_normalization"]["detected"] is True


def test_helper_group_checker(client=None):
    """
    Feature: Ref Group Consistency Checker (Helper 7)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: <ref group="टिप्पणी"> without corresponding {{Reflist|group=टिप्पणी}}
    Expected: Identifies orphaned ref group.
    """
    client = client or get_test_client()
    wikitext = 'पाठ<ref group="टिप्पणी">टिप्पणी विवरण</ref>\n== संदर्भ ==\n{{Reflist}}'
    resp = client.post("/api/helpers/group-checker", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert "टिप्पणी" in data["orphaned_groups"]
    assert data["is_consistent"] is False


def test_helper_domain_advisory(client=None):
    """
    Feature: Unreliable Domain Advisory Flag (Helper 8)
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: Reference using Twitter/X social media source
    Expected: Advisory badge flagged per WP:RSP; zero automatic deletion.
    """
    client = client or get_test_client()
    wikitext = "दावा।<ref>https://twitter.com/user/status/12345</ref>"
    resp = client.post("/api/helpers/domain-advisory", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["flagged_domains_count"] == 1
    assert data["flagged"][0]["domain"] == "twitter.com"
    assert "WP:RSP" in data["flagged"][0]["policy"]


# ==============================================================================
# Feature Area 6: Zero Auto-Save & Human Verification Workflow
# ==============================================================================

def test_zero_autosave_unconfirmed_rejected(client=None):
    """
    Feature: Zero Auto-Save Principle
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §5, R2
    Input: POST /api/save with confirmed=False or missing confirmed flag
    Expected: Request rejected with HTTP 400/422; absolute prohibition on unconfirmed publish.
    """
    client = client or get_test_client()
    payload = {"title": "परीक्षण पृष्ठ", "wikitext": "सुधारे गए सन्दर्भ", "confirmed": False}
    resp = client.post("/api/save", json=payload)
    assert resp.status_code in (400, 422), f"Expected rejection, got {resp.status_code}"
    assert "पुष्टि अनिवार्य" in resp.text or "confirmation required" in resp.text.lower()


def test_zero_autosave_dry_run_mode(client=None):
    """
    Feature: Zero Auto-Save & Dry-Run
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §5, R2
    Input: POST /api/save with confirmed=True and dry_run=True
    Expected: HTTP 200 dry-run preview success without modifying Wikipedia.
    """
    client = client or get_test_client()
    payload = {
        "title": "परीक्षण पृष्ठ",
        "wikitext": "सुधारे गए सन्दर्भ",
        "confirmed": True,
        "dry_run": True,
    }
    resp = client.post("/api/save", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dry_run_success"


def test_granular_diff_partial_selection(client=None):
    """
    Feature: Granular Diff Re-assembly
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §5, R2; survey_explorer_2 §2.2
    Input: 2 modifications; only modification #1 is checked (selected_ids=['mod-1'])
    Expected: Reconstructed wikitext applies mod-1 and leaves mod-2 untouched.
    """
    client = client or get_test_client()
    orig = "पहला <ref>https://a.com</ref> दूसरा <ref>https://b.com</ref>"
    mods = [
        {
            "id": "mod-1",
            "type": "bare_url",
            "span_start": 6,
            "span_end": 30,
            "proposed_snippet": "<ref>{{Cite web |url=https://a.com |title=A}}</ref>",
        },
        {
            "id": "mod-2",
            "type": "bare_url",
            "span_start": 38,
            "span_end": 62,
            "proposed_snippet": "<ref>{{Cite web |url=https://b.com |title=B}}</ref>",
        },
    ]
    resp = client.post(
        "/api/diff",
        json={"original_wikitext": orig, "selected_ids": ["mod-1"], "modifications": mods},
    )
    assert resp.status_code == 200
    data = resp.json()
    reconstructed = data["reconstructed_wikitext"]
    assert "https://a.com |title=A" in reconstructed
    assert "<ref>https://b.com</ref>" in reconstructed  # mod-2 rejected and unchanged


def test_dynamic_hindi_edit_summary_generation(client=None):
    """
    Feature: Dynamic Hindi Edit Summary Generator
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §5, R2; survey_explorer_2 §2.3
    Input: Diff request with 1 bare URL fix and 1 syntax repair checked
    Expected: Generates precise Hindi summary mentioning only checked categories.
    """
    client = client or get_test_client()
    mods = [
        {"id": "mod-1", "type": "bare_url", "span_start": 0, "span_end": 5,
         "proposed_snippet": "A"},
        {"id": "mod-2", "type": "syntax_repair", "span_start": 6, "span_end": 10,
         "proposed_snippet": "B"},
    ]

    resp = client.post(
        "/api/diff",
        json={
            "original_wikitext": "text",
            "selected_ids": ["mod-1", "mod-2"],
            "modifications": mods,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    summary = data["summary"]
    assert "नग्न URL को मानक साँचों में बदला" in summary
    assert "खराब साँचों की मरम्मत" in summary
    assert "सन्दर्भ सुधार उपकरण" in summary


def test_local_draft_and_patch_export(client=None):
    """
    Feature: Local Draft & Patch Export
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §4, R4
    Input: POST /api/diff with modified wikitext
    Expected: Unified .patch payload returned with diff headers (--- a/ +++ b/).
    """
    client = client or get_test_client()
    orig = "पुराना पाठ"
    mods = [
        {"id": "mod-1", "type": "syntax_repair", "span_start": 0, "span_end": 10,
         "proposed_snippet": "नया पाठ"}
    ]
    resp = client.post(
        "/api/diff",
        json={"original_wikitext": orig, "selected_ids": ["mod-1"], "modifications": mods},
    )

    assert resp.status_code == 200
    data = resp.json()
    patch = data["patch"]
    assert "--- a/wikitext" in patch
    assert "+++ b/wikitext" in patch


# ==============================================================================
# Feature Area 7: Admin Controls & System Health
# ==============================================================================

def test_admin_kill_switch_activation(client=None):
    """
    Feature: Emergency Kill-Switch
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Website admin toggles kill-switch ON via /api/admin/kill-switch
    Expected: Subsequent scan requests return HTTP 503 / Service Unavailable.
    """
    client = client or get_test_client()
    # Turn ON kill switch
    t_resp = client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        headers={"X-Wiki-User": "विक्रम प्रताप"},
    )
    assert t_resp.status_code == 200
    assert t_resp.json()["kill_switch"] is True

    # Attempt scan while kill switch active
    s_resp = client.post("/api/scan", json={"wikitext": "पाठ <ref>https://test.com</ref>"})
    assert s_resp.status_code == 503, "Scan must halt with 503 during kill-switch"


def test_admin_kill_switch_deactivation(client=None):
    """
    Feature: Emergency Kill-Switch Restoration
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Website admin toggles kill-switch OFF
    Expected: System resumes normal scanning operation (HTTP 200).
    """
    client = client or get_test_client()
    # Turn OFF kill switch
    t_resp = client.post(
        "/api/admin/kill-switch",
        json={"active": False},
        headers={"X-Wiki-User": "विक्रम प्रताप"},
    )
    assert t_resp.status_code == 200
    assert t_resp.json()["kill_switch"] is False

    # Attempt scan after restoration
    s_resp = client.post("/api/scan", json={"wikitext": "पाठ <ref>https://test.com</ref>"})
    assert s_resp.status_code == 200, "System must resume normal scanning"


def test_admin_rate_limiter_adjustment(client=None):
    """
    Feature: Admin Rate Limiter Controls
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Admin sets rate limit to 45 requests/minute
    Expected: Successfully updated and acknowledged in response.
    """
    client = client or get_test_client()
    resp = client.post(
        "/api/admin/rate-limit",
        json={"rate_limit_per_minute": 45},
        headers={"X-Wiki-User": "विक्रम प्रताप"},
    )
    assert resp.status_code == 200
    assert resp.json()["rate_limit_per_minute"] == 45


def test_admin_audit_log_recording(client=None):
    """
    Feature: Audit Logging & Monitoring
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §2, R3
    Input: Query /api/admin/audit after scan and administrative operations
    Expected: Returns list of recorded audit actions.
    """
    client = client or get_test_client()
    resp = client.get("/api/admin/audit", headers={"X-Wiki-User": "विक्रम प्रताप"})
    assert resp.status_code == 200
    data = resp.json()
    assert "audit_logs" in data
    assert isinstance(data["audit_logs"], list)


def test_system_health_endpoint(client=None):
    """
    Feature: System Health & Wikimedia Policy
    Tier: 1
    Authoritative Source: ORIGINAL_REQUEST.md §1, R5
    Input: GET /health and GET /api/health
    Expected: HTTP 200 with status healthy, DB connected, user_agent, maxlag=5.
    """
    client = client or get_test_client()
    resp1 = client.get("/health")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "healthy"
    assert data1["database"] == "connected"
    assert data1["wikimedia_policy"]["maxlag"] == 5

    resp2 = client.get("/api/health")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "healthy"
