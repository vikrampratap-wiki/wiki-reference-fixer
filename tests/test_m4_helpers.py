"""
Milestone 4 Test Suite — The 8 Companion Helper Modules.
Tests:
1. Duplicate Ref Merger (find_duplicate_references, apply_duplicate_merges)
2. Duplicate Name Conflict Resolver (detect_ref_conflicts)
3. Dead Link & Wayback Helper (check_dead_links)
4. Reference Error Scanner (scan_reference_errors)
5. Reference Health Scorecard (calculate_reference_scorecard)
6. ISBN/ISSN/DOI & Date Validator (validate_bibliographic_identifiers)
7. Ref Group Consistency Checker (check_ref_groups)
8. Unreliable Domain Advisory Flag (check_unreliable_domains)
9. Local Draft & Diff Patch Exporter (generate_wikitext_diff_patch)
10. All API Endpoints mounted under /api/helpers/*
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.helpers import (
    find_duplicate_references,
    apply_duplicate_merges,
    detect_ref_conflicts,
    check_dead_links,
    scan_reference_errors,
    calculate_reference_scorecard,
    validate_bibliographic_identifiers,
    validate_isbn_10,
    validate_isbn_13,
    validate_issn,
    validate_doi,
    normalize_devanagari_numerals,
    check_ref_groups,
    check_unreliable_domains,
    generate_wikitext_diff_patch,
)


# --- Helper 1: Duplicate Ref Merger ---
class TestDuplicateMerger:
    def test_detects_identical_bare_urls(self):
        wikitext = "बिंदु 1<ref>https://same-link.org</ref> बिंदु 2<ref>https://same-link.org</ref>"
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] >= 1
        assert len(res["proposed_merges"]) >= 1
        assert '<ref name="' in res["proposed_merges"][0]["replacement"]

    def test_reuses_existing_ref_name(self):
        wikitext = (
            'शुरुआत<ref name="source1">https://example.com/item</ref>'
            ' मध्य<ref>https://example.com/item</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        merge = res["proposed_merges"][0]
        assert merge["target_name"] == "source1"
        assert merge["replacement"] == '<ref name="source1" />'

    def test_apply_duplicate_merges(self):
        wikitext = "बिंदु 1<ref>https://same-link.org</ref> बिंदु 2<ref>https://same-link.org</ref>"
        res = find_duplicate_references(wikitext)
        merged = apply_duplicate_merges(wikitext, res["proposed_merges"])
        assert '<ref name="ref_1" />' in merged
        assert "बिंदु 1<ref>https://same-link.org</ref>" in merged


# --- Helper 2: Conflict Resolver ---
class TestConflictResolver:
    def test_detects_conflicting_contents_under_same_name(self):
        wikitext = '<ref name="foo">सामग्री A</ref> पाठ <ref name="foo">भिन्न सामग्री B</ref>'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 1
        conflict = res["conflicts"][0]
        assert conflict["ref_name"] == "foo"
        assert len(conflict["conflicting_contents"]) == 2
        assert "foo_1" in conflict["suggested_renames"]

    def test_no_conflict_when_contents_are_identical(self):
        wikitext = '<ref name="bar">समान सामग्री</ref> पाठ <ref name="bar">समान सामग्री</ref>'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0

    def test_self_closing_refs_do_not_cause_conflict(self):
        wikitext = '<ref name="bar">समान सामग्री</ref> पाठ <ref name="bar" />'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0


# --- Helper 3: Dead Link & Wayback Helper ---
class TestDeadLinksHelper:
    @pytest.mark.asyncio
    async def test_detects_dead_link_and_returns_wayback_info(self):
        wikitext = (
            "कड़ी।<ref>{{Cite web |url=https://example.com/dead-link "
            "|title=मृत पृष्ठ}}</ref>"
        )
        res = await check_dead_links(wikitext)
        assert res["dead_links_count"] >= 1
        item = res["results"][0]
        assert "web.archive.org" in item["archive_url"]
        assert item["url_status"] == "dead"
        assert item["archive_date"] == "2026-11-20"


# --- Helper 4: Error Scanner ---
class TestErrorScanner:
    def test_detects_missing_reflist(self):
        wikitext = "लेख पाठ।<ref>https://example.com</ref> कोई संदर्भ सूची नहीं।"
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "missing_reflist" in err_types

    def test_no_missing_reflist_when_hindi_container_present(self):
        wikitext = "लेख पाठ।<ref>https://example.com</ref>\n== सन्दर्भ ==\n{{सन्दर्भ सूची}}"
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "missing_reflist" not in err_types

    def test_detects_empty_ref_tags(self):
        wikitext = "खाली टैग<ref></ref> और यह भी <ref/>"
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "empty_ref" in err_types

    def test_detects_undefined_named_ref(self):
        wikitext = 'लेख पाठ<ref name="ghost_ref" /> बिना परिभाषा के।'
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "undefined_named_ref" in err_types


# --- Helper 5: Health Scorecard ---
class TestScorecard:
    def test_scorecard_deducts_for_bare_urls(self):
        wikitext = "तथ्य 1<ref>https://bare1.com</ref> तथ्य 2<ref>https://bare2.com</ref>"
        res = calculate_reference_scorecard(wikitext)
        assert 0 <= res["health_score"] <= 100
        assert res["bare_urls"] == 2
        assert res["total_refs"] == 2
        assert res["health_score"] < 100

    def test_clean_references_receive_high_score(self):
        wikitext = (
            "तथ्य<ref>{{Cite web |title=साहित्य |url=https://sahitya.org}}</ref>\n"
            "== सन्दर्भ ==\n{{सन्दर्भ सूची}}"
        )
        res = calculate_reference_scorecard(wikitext)
        assert res["health_score"] >= 80
        assert res["status"] == "उत्कृष्ट"
        assert res["grade"] == "A"


# --- Helper 6: Bibliographic Validator ---
class TestValidator:
    def test_isbn_13_validation(self):
        assert validate_isbn_13("978-0-306-40615-7") is True
        assert validate_isbn_13("978-0-306-40615-0") is False

    def test_isbn_10_validation(self):
        assert validate_isbn_10("0-306-40615-2") is True
        assert validate_isbn_10("0-306-40615-9") is False

    def test_issn_validation(self):
        assert validate_issn("0028-0836") is True
        assert validate_issn("1234-5678") is False

    def test_doi_validation(self):
        assert validate_doi("10.1000/182") is True
        assert validate_doi("invalid-doi") is False

    def test_devanagari_numerals_normalization(self):
        normalized = normalize_devanagari_numerals("२०२३-०८-२३")
        assert normalized == "2023-08-23"

    def test_validate_bibliographic_identifiers(self):
        wikitext = (
            "{{Cite book |title=पुस्तक |isbn=978-0-306-40615-7 "
            "|date=२०२३-०८-२३ |doi=10.1000/182}}"
        )
        res = validate_bibliographic_identifiers(wikitext)
        assert res["results"]["isbn"]["valid"] is True
        assert res["results"]["doi"]["valid"] is True
        assert res["results"]["date_normalization"]["detected"] is True


# --- Helper 7: Group Checker ---
class TestGroupChecker:
    def test_detects_orphaned_ref_group(self):
        wikitext = 'पाठ<ref group="टिप्पणी">टिप्पणी विवरण</ref>\n== संदर्भ ==\n{{Reflist}}'
        res = check_ref_groups(wikitext)
        assert "टिप्पणी" in res["orphaned_groups"]
        assert res["is_consistent"] is False

    def test_consistent_group_matches(self):
        wikitext = (
            'पाठ<ref group="टिप्पणी">टिप्पणी विवरण</ref>\n'
            '== टिप्पणियाँ ==\n{{सन्दर्भ सूची|group=टिप्पणी}}'
        )
        res = check_ref_groups(wikitext)
        assert len(res["orphaned_groups"]) == 0
        assert res["is_consistent"] is True


# --- Helper 8: Domain Advisory ---
class TestDomainAdvisory:
    def test_flags_twitter_source_with_advisory(self):
        wikitext = "दावा।<ref>https://twitter.com/user/status/12345</ref>"
        res = check_unreliable_domains(wikitext)
        assert res["flagged_domains_count"] == 1
        assert res["flagged"][0]["domain"] == "twitter.com"
        assert "WP:RSP" in res["flagged"][0]["policy"]

    def test_clean_source_not_flagged(self):
        wikitext = "दावा।<ref>https://bbc.com/news/world-12345</ref>"
        res = check_unreliable_domains(wikitext)
        assert res["flagged_domains_count"] == 0


# --- Helper 9: Local Draft & Diff Patch ---
class TestLocalDraftPatch:
    def test_diff_patch_generation(self):
        orig = "पंक्ति 1\nपंक्ति 2\n"
        mod = "पंक्ति 1\nसंशोधित पंक्ति 2\nपंक्ति 3\n"
        patch = generate_wikitext_diff_patch(orig, mod, filename="test.patch")
        assert patch["has_changes"] is True
        assert patch["lines_added"] >= 1
        assert "diff" in patch["patch"] or "---" in patch["patch"]


# --- FastAPI HTTP Endpoints Integration ---
class TestHelperEndpoints:
    @pytest.mark.asyncio
    async def test_api_helpers_all_routes(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Duplicate Merger
            r1 = await client.post(
                "/api/helpers/duplicate-merger",
                json={
                    "wikitext": (
                        "बिंदु 1<ref>https://same.org</ref> "
                        "बिंदु 2<ref>https://same.org</ref>"
                    )
                },
            )
            assert r1.status_code == 200
            assert r1.json()["duplicates_found"] >= 1

            # 2. Conflict Resolver
            r2 = await client.post(
                "/api/helpers/conflict-resolver",
                json={
                    "wikitext": (
                        '<ref name="foo">सामग्री A</ref>'
                        '<ref name="foo">सामग्री B</ref>'
                    )
                },
            )
            assert r2.status_code == 200
            assert r2.json()["conflicts_count"] == 1

            # 3. Dead Links
            r3 = await client.post(
                "/api/helpers/dead-links",
                json={
                    "wikitext": (
                        "कड़ी।<ref>{{Cite web |url=https://example.com/dead-link "
                        "|title=मृत}}</ref>"
                    )
                },
            )
            assert r3.status_code == 200
            assert r3.json()["dead_links_count"] >= 1

            # 4. Error Scanner
            r4 = await client.post(
                "/api/helpers/error-scanner",
                json={"wikitext": "पाठ।<ref>https://example.com</ref>"},
            )
            assert r4.status_code == 200
            assert any(e["type"] == "missing_reflist" for e in r4.json()["errors"])

            # 5. Scorecard
            r5 = await client.post(
                "/api/helpers/scorecard",
                json={
                    "wikitext": (
                        "तथ्य 1<ref>https://bare1.com</ref> "
                        "तथ्य 2<ref>https://bare2.com</ref>"
                    )
                },
            )
            assert r5.status_code == 200
            assert r5.json()["bare_urls"] == 2

            # 6. Validator
            r6 = await client.post(
                "/api/helpers/validator",
                json={
                    "wikitext": (
                        "{{Cite book |title=पुस्तक |isbn=978-0-306-40615-7 "
                        "|date=२०२३-०८-२३}}"
                    )
                },
            )
            assert r6.status_code == 200
            assert r6.json()["results"]["isbn"]["valid"] is True

            # 7. Group Checker
            r7 = await client.post(
                "/api/helpers/group-checker",
                json={"wikitext": 'पाठ<ref group="टिप्पणी">विवरण</ref>\n== संदर्भ ==\n{{Reflist}}'}
            )
            assert r7.status_code == 200
            assert "टिप्पणी" in r7.json()["orphaned_groups"]

            # 8. Domain Advisory
            r8 = await client.post(
                "/api/helpers/domain-advisory",
                json={"wikitext": "दावा।<ref>https://twitter.com/user/status/12345</ref>"}
            )
            assert r8.status_code == 200
            assert r8.json()["flagged_domains_count"] == 1

            # 9. Export Patch
            r9 = await client.post(
                "/api/helpers/export-patch",
                json={"original_wikitext": "मूल पाठ", "modified_wikitext": "संशोधित पाठ"}
            )
            assert r9.status_code == 200
            assert r9.json()["has_changes"] is True
