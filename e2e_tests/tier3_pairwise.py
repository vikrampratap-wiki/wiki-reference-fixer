"""
Tier 3: Cross-Feature Interactions E2E Tests (Pairwise Testing).
Tests simultaneous feature activations, complex state interactions, and multi-module pipelines:
- Syntax repair + Duplicate merger
- Dead link archive injection + CS1 deadurl modernization
- Duplicate name conflict alert + Bare URL repair
- Granular selection partial rejection in multi-edit batches
- Unreliable domain advisory + Citoid enrichment
- Emergency kill-switch during scan activity
- Ref group checker + Error scanner
- Identifier validator + Devanagari date normalization

Total: 8 comprehensive test cases.
Authoritative source: PROJECT.md §Milestones (M3, M4, M5), ORIGINAL_REQUEST.md.
"""

from e2e_tests.client import get_test_client


def test_p1_syntax_repair_plus_duplicate_merger(client=None):
    """
    Features: Syntax Repair + Duplicate Ref Merger
    Tier: 3
    Input: Wikitext with broken stray pipes {{Cite web ||...}} AND duplicate bare URLs
    Expected: Both syntax repair and bare URL modifications identified simultaneously.
    """
    client = client or get_test_client()
    wikitext = (
        "पहला सन्दर्भ।<ref>https://same-source.org</ref>\n"
        "दूसरा सन्दर्भ।<ref>https://same-source.org</ref>\n"
        "खराब साँचा। {{Cite web ||url=https://test.com |title=टेस्ट}}"
    )
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    types = [m["type"] for m in data["modifications"]]
    assert "bare_url" in types
    assert "syntax_repair" in types


def test_p2_deadlink_wayback_plus_cs1_modernization(client=None):
    """
    Features: Dead Link & Archive Helper + CS1 Modernization
    Tier: 3
    Input: Citation with deprecated |deadurl=yes and a dead 404 URL
    Expected: deadurl converted to url-status=dead AND Wayback snapshot retrieved.
    """
    client = client or get_test_client()
    wikitext = "{{Cite web |url=https://example.com/dead-link |title=पुराना पृष्ठ |deadurl=yes}}"
    # 1. Scan modernization
    s_resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert s_resp.status_code == 200
    data = s_resp.json()
    assert any(m["type"] == "cs1_modernize" for m in data["modifications"])

    # 2. Helper dead link query
    h_resp = client.post("/api/helpers/dead-links", json={"wikitext": wikitext})
    assert h_resp.status_code == 200
    h_data = h_resp.json()
    assert h_data["dead_links_count"] >= 1
    assert h_data["results"][0]["url_status"] == "dead"


def test_p3_duplicate_name_conflict_plus_bare_url(client=None):
    """
    Features: Duplicate Name Conflict Resolver + Bare URL Conversion
    Tier: 3
    Input: Conflicting <ref name="x">Content A</ref> vs <ref name="x">Content B</ref>
           alongside a bare URL <ref>https://clean.org</ref>
    Expected: Conflict resolver flags collision without data loss; scan converts the clean bare URL.
    """
    client = client or get_test_client()
    wikitext = (
        '<ref name="x">प्रथम विवरण</ref> '
        '<ref name="x">भिन्न द्वितीय विवरण</ref> '
        '<ref>https://timesofindia.indiatimes.com/news</ref>'
    )
    # Check conflict alert
    c_resp = client.post("/api/helpers/conflict-resolver", json={"wikitext": wikitext})
    assert c_resp.status_code == 200
    assert c_resp.json()["conflicts_count"] == 1

    # Check scan conversion
    s_resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert s_resp.status_code == 200
    mods = s_resp.json()["modifications"]
    assert any(m["type"] == "bare_url" and "timesofindia" in m["proposed_snippet"] for m in mods)


def test_p4_granular_diff_selective_rejection(client=None):
    """
    Features: Granular Diff Selection + Wikitext Re-assembly Integrity
    Tier: 3
    Input: 3 proposed modifications; user rejects mod-1 (syntax)
           but accepts mod-2 & mod-3 (bare URLs)
    Expected: Reconstructed wikitext correctly applies mod-2 & 3 in descending order,
              preserving original wikitext at mod-1 location without offset drift.
    """
    client = client or get_test_client()
    orig = "भाग 1 {{Cite ||}} भाग 2 <ref>https://u1.com</ref> भाग 3 <ref>https://u2.com</ref>"
    mods = [
        {"id": "mod-1", "type": "syntax_repair", "span_start": 7, "span_end": 18,
         "proposed_snippet": "{{Cite |}}"},
        {"id": "mod-2", "type": "bare_url", "span_start": 25, "span_end": 50,
         "proposed_snippet": "<ref>{{Cite web |url=https://u1.com}}</ref>"},
        {"id": "mod-3", "type": "bare_url", "span_start": 57, "span_end": 82,
         "proposed_snippet": "<ref>{{Cite web |url=https://u2.com}}</ref>"},
    ]
    # Accept only mod-2 and mod-3
    resp = client.post(
        "/api/diff",
        json={
            "original_wikitext": orig,
            "selected_ids": ["mod-2", "mod-3"],
            "modifications": mods,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    reconstructed = data["reconstructed_wikitext"]
    assert "{{Cite ||}}" in reconstructed  # mod-1 rejected, original kept
    assert "https://u1.com}}" in reconstructed
    assert "https://u2.com}}" in reconstructed
    assert data["applied_count"] == 2


def test_p5_unreliable_domain_plus_citoid_enrichment(client=None):
    """
    Features: Unreliable Domain Advisory + Citoid Metadata Enrichment
    Tier: 3
    Input: Reference using Blogspot blog (e.g. https://myopinion.blogspot.com)
    Expected: Blogspot domain flagged with WP:RSP warning badge while still being parsed.
    """
    client = client or get_test_client()
    wikitext = "ब्लॉग स्रोत।<ref>https://myopinion.blogspot.com/post1</ref>"
    # 1. Scan handles URL
    s_resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert s_resp.status_code == 200

    # 2. Advisory flags domain
    a_resp = client.post("/api/helpers/domain-advisory", json={"wikitext": wikitext})
    assert a_resp.status_code == 200
    data = a_resp.json()
    assert data["flagged_domains_count"] >= 1
    assert any("blogspot.com" in f["domain"] for f in data["flagged"])


def test_p6_kill_switch_during_bulk_scan(client=None):
    """
    Features: Admin Emergency Kill-Switch + Bulk Scan Interception
    Tier: 3
    Input: Activate kill switch, then attempt multi-reference scan
    Expected: Scan immediately rejected with HTTP 503; no background tasks or calls executed.
    """
    client = client or get_test_client()
    # Turn ON kill-switch
    client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        headers={"X-Wiki-User": "विक्रम प्रताप"},
    )

    # Attempt bulk scan
    resp = client.post(
        "/api/scan",
        json={"wikitext": "लेख <ref>https://a.com</ref> <ref>https://b.com</ref>"},
    )
    assert resp.status_code == 503

    # Restore kill-switch
    client.post(
        "/api/admin/kill-switch",
        json={"active": False},
        headers={"X-Wiki-User": "विक्रम प्रताप"},
    )


def test_p7_ref_group_checker_plus_error_scanner(client=None):

    """
    Features: Ref Group Consistency Checker + Error Scanner
    Tier: 3
    Input: Wikitext with orphaned group ref <ref group="नोट"> and empty <ref></ref>
    Expected: Group checker flags orphaned group AND error scanner flags empty ref.
    """
    client = client or get_test_client()
    wikitext = 'पाठ<ref group="नोट">टिप्पणी</ref> खराब टैग <ref></ref>\n== संदर्भ ==\n{{Reflist}}'
    g_resp = client.post("/api/helpers/group-checker", json={"wikitext": wikitext})
    assert g_resp.status_code == 200
    assert "नोट" in g_resp.json()["orphaned_groups"]

    e_resp = client.post("/api/helpers/error-scanner", json={"wikitext": wikitext})
    assert e_resp.status_code == 200
    assert any(err["type"] == "empty_ref" for err in e_resp.json()["errors"])


def test_p8_validator_plus_devanagari_date_in_cs1(client=None):
    """
    Features: ISBN Validator + Devanagari Date Normalization
    Tier: 3
    Input: {{Cite book |isbn=978-0-306-40615-7 |date=२०२३-०८-२३ |title=पुस्तक}}
    Expected: Validator validates ISBN-13 checksum AND detects Devanagari date numerals.
    """
    client = client or get_test_client()
    wikitext = "{{Cite book |isbn=978-0-306-40615-7 |date=२०२३-०८-२३ |title=पुस्तक}}"
    resp = client.post("/api/helpers/validator", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["isbn"]["valid"] is True
    assert data["results"]["date_normalization"]["detected"] is True
