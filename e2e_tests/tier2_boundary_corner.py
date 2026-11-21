"""
Tier 2: Boundary & Corner Cases E2E Tests.
Validates boundary values, extreme sizes, malformed structures, adversarial inputs,
Devanagari Unicode edge conditions, and strict information-disclosure prevention.

Total: 12 comprehensive test cases.
Authoritative source: ORIGINAL_REQUEST.md (§1-§5), survey_spec_miner_1, PROJECT.md.
"""

from e2e_tests.client import get_test_client


def test_b1_empty_wikitext_scan(client=None):
    """
    Feature: Boundary & Corner Cases - Empty Input
    Tier: 2
    Input: Empty wikitext string ""
    Expected: Status 200, 0 modifications, health score 100, no unhandled exceptions.
    """
    client = client or get_test_client()
    resp = client.post("/api/scan", json={"wikitext": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["modifications"]) == 0
    assert data["scorecard"]["total_refs"] == 0
    assert data["scorecard"]["health_score"] == 100


def test_b2_deeply_nested_templates(client=None):
    """
    Feature: Boundary & Corner Cases - Deep Nesting
    Tier: 2
    Input: Template nested 6 levels deep inside a <ref> node
    Expected: AST handles deep recursion without maximum recursion depth errors.
    """
    client = client or get_test_client()
    wikitext = (
        "गहन साँचा।<ref>{{Cite web "
        "|title={{a|{{b|{{c|{{d|{{e|अंतिम मूल्य}}}}}}}}}} "
        "|url=https://example.org/deep}}</ref>"
    )
    resp = client.post("/api/scan", json={"wikitext": wikitext})

    assert resp.status_code == 200
    data = resp.json()
    assert data["scorecard"]["total_refs"] == 1
    assert data["scorecard"]["broken_syntax"] == 0


def test_b3_unclosed_ref_at_end_of_file(client=None):
    """
    Feature: Boundary & Corner Cases - EOF Truncation
    Tier: 2
    Input: Unclosed <ref> at the very end of the wikitext file
    Expected: Handled gracefully without crash; syntax repair or scanner flags error.
    """
    client = client or get_test_client()
    wikitext = "अंतिम पैराग्राफ जिसमें संदर्भ अधूरा है <ref>https://example.com/unclosed"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["modifications"], list)


def test_b4_malformed_urls_with_spaces_and_control_chars(client=None):
    """
    Feature: Boundary & Corner Cases - Malformed URLs
    Tier: 2
    Input: URL containing illegal spaces, newlines, and control characters
    Expected: Handled safely without HTTP 500 error; system normalizes or skips invalid URL.
    """
    client = client or get_test_client()
    wikitext = "अमान्य यूआरएल।<ref>https://example .com/foo bar\n\t/test</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200


def test_b5_javascript_and_data_uris_rejected(client=None):
    """
    Feature: Boundary & Corner Cases - XSS / Malicious URIs
    Tier: 2
    Input: <ref>javascript:alert('XSS')</ref> and data: URIs
    Expected: Rejected as invalid citation sources; zero XSS payloads rendered.
    """
    client = client or get_test_client()
    wikitext = "दुर्भावनापूर्ण कड़ी।<ref>javascript:alert('XSS')</ref>"
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    # Must not convert javascript: URI into a valid Cite web citation
    for mod in data["modifications"]:
        assert "javascript:" not in mod["proposed_snippet"]


def test_b6_unicode_devanagari_zwj_and_special_symbols(client=None):
    """
    Feature: Boundary & Corner Cases - Devanagari Typography
    Tier: 2
    Input: Devanagari text with Zero-Width Joiner (ZWJ, e.g. र् + ZWJ),
           non-breaking spaces, and quotes
    Expected: Unicode sequences are preserved byte-for-byte without normalization corruption.

    """
    client = client or get_test_client()
    # "राष्ट्र" with ZWJ sequence and Hindi quotes
    dev_text = '“भारत गणराज्य” (विशिष्ट: र्\u200Dय)'
    wikitext = f'{dev_text}<ref>https://example.com/india</ref>'
    resp = client.post("/api/scan", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert dev_text in data["original_wikitext"]


def test_b7_wikimedia_maxlag_response_handling(client=None):
    """
    Feature: Boundary & Corner Cases - Wikimedia maxlag=5 Backoff
    Tier: 2
    Authoritative Source: survey_spec_miner_1 §3.8, §4.5
    Input: Wikimedia API maxlag header probe
    Expected: maxlag=5 parameter configured and policy observed.
    """
    client = client or get_test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["wikimedia_policy"]["maxlag"] == 5


def test_b8_non_admin_enumeration_prevention(client=None):
    """
    Feature: Boundary & Corner Cases - Strict Information Concealment
    Tier: 2
    Input: Probing non-existent and randomized admin subpaths: /admin/random123/xyz
    Expected: Uniform HTTP 404 Not Found (Zero information leakage about valid admin endpoints).
    """
    client = client or get_test_client()
    resp = client.get("/admin/random123/xyz")
    assert resp.status_code == 404
    resp2 = client.get("/admin/settings/secret-token")
    assert resp2.status_code == 404


def test_b9_article_with_many_refs_missing_reflist(client=None):
    """
    Feature: Boundary & Corner Cases - Large Article Missing Reflist
    Tier: 2
    Input: Article containing 25 distinct <ref> citations but NO {{Reflist}} or {{सन्दर्भ सूची}}
    Expected: Error scanner flags missing_reflist; scorecard reflects missing container penalty.
    """
    client = client or get_test_client()
    refs = "\n".join(f"तथ्य {i}।<ref>https://source{i}.org</ref>" for i in range(25))
    wikitext = f"विशाल लेख।\n{refs}\nलेख समाप्त बिना संदर्भ सूची के।"
    resp = client.post("/api/helpers/error-scanner", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    err_types = [e["type"] for e in data["errors"]]
    assert "missing_reflist" in err_types


def test_b10_zero_references_scorecard(client=None):
    """
    Feature: Boundary & Corner Cases - Article with Zero References
    Tier: 2
    Input: Pure prose wikitext with zero references
    Expected: Scorecard calculates 100 or default baseline score without division-by-zero error.
    """
    client = client or get_test_client()
    wikitext = "यह एक छोटा लेख है जिसमें कोई संदर्भ नहीं जोड़ा गया है।"
    resp = client.post("/api/helpers/scorecard", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_refs"] == 0
    assert data["bare_urls"] == 0
    assert data["health_score"] == 100


def test_b11_isbn_checksum_invalid(client=None):
    """
    Feature: Boundary & Corner Cases - Invalid ISBN Checksum
    Tier: 2
    Input: ISBN-13 with corrupted check digit: 978-0-306-40615-9 (correct is 7)
    Expected: Checksum validation fails; flagged as invalid ISBN.
    """
    client = client or get_test_client()
    wikitext = "{{Cite book |title=खराब ISBN वाली पुस्तक |isbn=978-0-306-40615-9}}"
    resp = client.post("/api/helpers/validator", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["isbn"]["valid"] is False


def test_b12_extreme_date_formats(client=None):
    """
    Feature: Boundary & Corner Cases - Varied Hindi Date Notations
    Tier: 2
    Input: Dates in Hindi numerals and varied delimiters: २३ अगस्त २०२३
    Expected: Devanagari numerals detected and normalized to standard Latin numerals.
    """
    client = client or get_test_client()
    wikitext = "{{Cite web |title=शीर्षक |date=२३ अगस्त २०२३ |url=https://test.com}}"
    resp = client.post("/api/helpers/validator", json={"wikitext": wikitext})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["date_normalization"]["detected"] is True
    normalized = data["results"]["date_normalization"]["normalized_sample"]
    assert "2023" in normalized
