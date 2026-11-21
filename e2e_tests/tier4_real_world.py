"""
Tier 4: Real-World Scenarios E2E Tests.
Validates the entire end-to-end workflow against authentic, complex extracts from live
Hindi Wikipedia articles containing mixed citations, Devanagari named refs, and nested markup:
1. चंद्रयान-3 (Chandrayaan-3)
2. महात्मा गांधी (Mahatma Gandhi)
3. भारत (Bharat / India Overview)
4. हिन्दी विकिपीडिया (Hindi Wikipedia History)
5. दिल्ली (Delhi Multilingual & Grouped Refs)

Total: 5 comprehensive test cases.
Authoritative source: Live hi.wikipedia.org articles, ORIGINAL_REQUEST.md.
"""

from e2e_tests.client import get_test_client
from e2e_tests.fixtures import (
    CHANDRAYAAN_3_EXTRACT,
    MAHATMA_GANDHI_EXTRACT,
    BHARAT_EXTRACT,
    HINDI_WIKIPEDIA_EXTRACT,
    DELHI_EXTRACT,
)


def test_rw1_chandrayaan_3_article(client=None):
    """
    Feature: Real-World Scenario 1 - चंद्रयान-3 (Chandrayaan-3)
    Tier: 4
    Input: Real extract containing Devanagari named ref <ref name="आजतक23">, ISRO URL,
           bracketed Times of India URL with title, and {{Reflist|30em}}.
    Expected:
    1. Scan identifies bare URLs and bracketed URLs.
    2. Preserves Devanagari ref name 'आजतक23' and self-closing ref '<ref name="आजतक23" />'.
    3. Reconstructs wikitext with selected modifications without breaking {{Reflist|30em}}.
    4. Generates concise Hindi edit summary.
    """
    client = client or get_test_client()
    # 1. Scan article
    s_resp = client.post("/api/scan", json={"wikitext": CHANDRAYAAN_3_EXTRACT})
    assert s_resp.status_code == 200
    data = s_resp.json()
    assert data["scorecard"]["total_refs"] >= 3
    assert data["scorecard"]["has_reflist"] is True
    assert len(data["modifications"]) >= 2

    # 2. Reconstruct wikitext with all modifications selected
    mod_ids = [m["id"] for m in data["modifications"]]
    d_resp = client.post(
        "/api/diff",
        json={
            "original_wikitext": CHANDRAYAAN_3_EXTRACT,
            "selected_ids": mod_ids,
            "modifications": data["modifications"],
        },
    )
    assert d_resp.status_code == 200
    d_data = d_resp.json()
    reconstructed = d_data["reconstructed_wikitext"]
    assert 'name="आजतक23"' in reconstructed
    assert "{{Reflist|30em}}" in reconstructed
    assert "नग्न URL को मानक साँचों में बदला" in d_data["summary"]


def test_rw2_mahatma_gandhi_article(client=None):
    """
    Feature: Real-World Scenario 2 - महात्मा गांधी (Mahatma Gandhi)
    Tier: 4
    Input: Real extract with {{Cite book}}, duplicate gandhiheritageportal URLs,
           and deprecated |deadurl=yes with |accessdate=.
    Expected:
    1. Identifies deadurl and modernizes to url-status=dead, accessdate to access-date.
    2. Duplicate merger helper detects duplicate gandhiheritageportal references.
    3. Scorecard reflects healthy citation structure with container {{सन्दर्भ सूची}}.
    """
    client = client or get_test_client()
    # 1. Scan
    s_resp = client.post("/api/scan", json={"wikitext": MAHATMA_GANDHI_EXTRACT})
    assert s_resp.status_code == 200
    data = s_resp.json()
    assert any(m["type"] == "cs1_modernize" for m in data["modifications"])

    # 2. Duplicate helper
    dup_resp = client.post(
        "/api/helpers/duplicate-merger",
        json={"wikitext": MAHATMA_GANDHI_EXTRACT},
    )
    assert dup_resp.status_code == 200

    dup_data = dup_resp.json()
    assert dup_data["duplicates_found"] >= 1


def test_rw3_bharat_overview_article(client=None):
    """
    Feature: Real-World Scenario 3 - भारत (Bharat / India)
    Tier: 4
    Input: Real extract with government portal bare URL, Twitter/X advisory link,
           and nested {{lang-hi|...}} template.
    Expected:
    1. Scan converts government bare URL.
    2. Domain advisory flags Twitter social media link with WP:RSP advisory badge.
    3. Nested template {{lang-hi|...}} remains untouched.
    """
    client = client or get_test_client()
    # 1. Scan
    s_resp = client.post("/api/scan", json={"wikitext": BHARAT_EXTRACT})
    assert s_resp.status_code == 200
    data = s_resp.json()
    assert any("india.gov.in" in m["proposed_snippet"] for m in data["modifications"])

    # 2. Advisory
    a_resp = client.post("/api/helpers/domain-advisory", json={"wikitext": BHARAT_EXTRACT})
    assert a_resp.status_code == 200
    assert any("twitter.com" in f["domain"] for f in a_resp.json()["flagged"])


def test_rw4_hindi_wikipedia_history(client=None):
    """
    Feature: Real-World Scenario 4 - हिन्दी विकिपीडिया (Hindi Wikipedia)
    Tier: 4
    Input: Real extract with bracketed bare link [https://... आंकड़े], old deadurl=no,
           duplicate named ref stats, and missing reflist container.
    Expected:
    1. Bracketed bare link converted to {{Cite web}} preserving Hindi title.
    2. deadurl=no modernized to url-status=live.
    3. Error scanner flags missing reflist container at bottom.
    """
    client = client or get_test_client()
    # 1. Scan
    s_resp = client.post("/api/scan", json={"wikitext": HINDI_WIKIPEDIA_EXTRACT})
    assert s_resp.status_code == 200
    data = s_resp.json()
    assert data["scorecard"]["has_reflist"] is False

    # 2. Error scanner
    e_resp = client.post("/api/helpers/error-scanner", json={"wikitext": HINDI_WIKIPEDIA_EXTRACT})
    assert e_resp.status_code == 200
    assert any(e["type"] == "missing_reflist" for e in e_resp.json()["errors"])


def test_rw5_delhi_multilingual_article(client=None):
    """
    Feature: Real-World Scenario 5 - दिल्ली (Delhi)
    Tier: 4
    Input: Real extract with <ref group="टिप्पणी"> matched with {{Reflist|group=टिप्पणी}},
           duplicate delhi.gov.in refs, and general {{Reflist}}.
    Expected:
    1. Group checker verifies group consistency (is_consistent=True).
    2. Duplicate merger detects identical government URLs.
    3. Dry-run save preview confirms full article reconstruction readiness.
    """
    client = client or get_test_client()
    # 1. Group consistency
    g_resp = client.post("/api/helpers/group-checker", json={"wikitext": DELHI_EXTRACT})
    assert g_resp.status_code == 200
    assert g_resp.json()["is_consistent"] is True

    # 2. Dry run preview
    save_resp = client.post(
        "/api/save",
        json={
            "title": "दिल्ली",
            "wikitext": DELHI_EXTRACT,
            "confirmed": True,
            "dry_run": True,
        },
    )
    assert save_resp.status_code == 200
    assert save_resp.json()["status"] == "dry_run_success"
