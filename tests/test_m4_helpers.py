"""
Milestone 4 Test Suite — The 8 Companion Helper Modules & API Endpoints.
Tests:
1. Duplicate Ref Merger (find_duplicate_references, apply_duplicate_merges)
2. Duplicate Name Conflict Resolver (detect_ref_conflicts, apply_conflict_resolution)
3. Dead Link & Wayback Helper (check_dead_links, WaybackClient)
4. Reference Error Scanner (scan_reference_errors)
5. Reference Health Scorecard (calculate_reference_scorecard)
6. ISBN/ISSN/DOI & Date Validator (validate_bibliographic_identifiers)
7. Ref Group Consistency Checker (check_ref_groups)
8. Unreliable Domain Advisory Flag (check_unreliable_domains)
9. Local Draft & Diff Patch Exporter (generate_wikitext_diff_patch)
10. All API Endpoints mounted under /api/helpers/*
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

import datetime
import pytest
import respx
import httpx
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.helpers import (
    find_duplicate_references,
    apply_duplicate_merges,
    detect_ref_conflicts,
    apply_conflict_resolution,
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
from app.services.wayback import parse_wayback_timestamp


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

    def test_unnamed_duplicate_converts_first_to_definition_and_second_to_invocation(self):
        wikitext = "बिंदु 1<ref>https://same-link.org</ref> बिंदु 2<ref>https://same-link.org</ref>"
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        assert len(res["proposed_merges"]) == 2

        first_merge = res["proposed_merges"][0]
        second_merge = res["proposed_merges"][1]

        assert first_merge["is_definition"] is True
        assert first_merge["replacement"] == '<ref name="ref_1">https://same-link.org</ref>'
        assert second_merge["is_definition"] is False
        assert second_merge["replacement"] == '<ref name="ref_1" />'

    def test_repeated_named_definitions_merged(self):
        wikitext = (
            '<ref name="foo">https://same.org</ref> और '
            '<ref name="foo">https://same.org</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        assert len(res["proposed_merges"]) == 1
        assert res["proposed_merges"][0]["replacement"] == '<ref name="foo" />'

    def test_slug_collision_avoidance(self):
        wikitext = (
            '<ref name="ref_1">पुराना स्रोत</ref> '
            'नया 1<ref>https://new.org</ref> नया 2<ref>https://new.org</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        for merge in res["proposed_merges"]:
            assert merge["target_name"] != "ref_1"
            assert merge["target_name"] == "ref_2"

    def test_preserves_group_attribute(self):
        wikitext = (
            '<ref group="टिप्पणी">https://same.org</ref> और '
            '<ref group="टिप्पणी">https://same.org</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        assert 'group="टिप्पणी"' in res["proposed_merges"][0]["replacement"]
        assert 'group="टिप्पणी"' in res["proposed_merges"][1]["replacement"]

    def test_apply_duplicate_merges(self):
        wikitext = "बिंदु 1<ref>https://same-link.org</ref> बिंदु 2<ref>https://same-link.org</ref>"
        res = find_duplicate_references(wikitext)
        merged = apply_duplicate_merges(wikitext, res["proposed_merges"])
        assert '<ref name="ref_1">https://same-link.org</ref>' in merged
        assert '<ref name="ref_1" />' in merged


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
        assert len(conflict["spans"]) == 2

    def test_no_conflict_when_contents_are_identical(self):
        wikitext = '<ref name="bar">समान सामग्री</ref> पाठ <ref name="bar">समान सामग्री</ref>'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0

    def test_self_closing_refs_do_not_cause_conflict(self):
        wikitext = '<ref name="bar">समान सामग्री</ref> पाठ <ref name="bar" />'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0

    def test_group_scoped_conflicts_no_cross_contamination(self):
        wikitext = (
            '<ref group="note" name="1">नोट विवरण 1</ref> और '
            '<ref name="1">सन्दर्भ विवरण 1</ref>'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0

    def test_conflict_spans_and_apply_resolution(self):
        wikitext = '<ref name="x">विवरण 1</ref> मध्य <ref name="x">विवरण 2</ref>'
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 1
        resolved = apply_conflict_resolution(wikitext, res["conflicts"][0]["spans"])
        assert '<ref name="x_1">विवरण 1</ref>' in resolved
        assert '<ref name="x_2">विवरण 2</ref>' in resolved


# --- Helper 3: Dead Link & Wayback Helper ---
class TestDeadLinksHelper:
    @pytest.mark.asyncio
    @respx.mock
    async def test_detects_dead_link_and_returns_wayback_info(self):
        target_url = "https://example.com/dead-link"
        mock_timestamp = "20230515143000"
        expected_date = "2023-05-15"
        mock_archive_url = f"https://web.archive.org/web/{mock_timestamp}/{target_url}"

        respx.get("https://archive.org/wayback/available").mock(
            return_value=httpx.Response(
                200,
                json={
                    "archived_snapshots": {
                        "closest": {
                            "status": "200",
                            "available": True,
                            "url": mock_archive_url,
                            "timestamp": mock_timestamp,
                        }
                    },
                    "url": target_url,
                },
            )
        )

        wikitext = (
            f"कड़ी।<ref>{{{{Cite web |url={target_url} "
            "|title=मृत पृष्ठ}}}}</ref>"
        )
        res = await check_dead_links(wikitext)
        assert res["dead_links_count"] >= 1
        item = res["results"][0]
        assert item["url"] == target_url
        assert item["archive_url"] == mock_archive_url
        assert item["url_status"] == "dead"
        assert item["archive_date"] == expected_date

    @pytest.mark.asyncio
    @respx.mock
    async def test_dead_links_dynamic_fallback_when_offline(self):
        target_url = "https://example.com/dead-link"
        respx.get("https://archive.org/wayback/available").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )

        wikitext = (
            f"कड़ी।<ref>{{{{Cite web |url={target_url} "
            "|title=मृत पृष्ठ}}}}</ref>"
        )
        res = await check_dead_links(wikitext)
        assert res["dead_links_count"] >= 1
        item = res["results"][0]
        assert item["url"] == target_url
        assert "web.archive.org" in item["archive_url"]
        assert item["url_status"] == "dead"
        assert item["archive_date"] == datetime.date.today().isoformat()

    @pytest.mark.asyncio
    async def test_no_false_positive_on_prose_404(self):
        wikitext = "कमरा नंबर 404 में बैठक हुई।<ref>https://news.com/page</ref>"
        res = await check_dead_links(wikitext)
        assert res["dead_links_count"] == 0

    def test_parse_wayback_timestamp(self):
        assert parse_wayback_timestamp("20230815120000") == "2023-08-15"
        assert parse_wayback_timestamp("20211231") == "2021-12-31"
        assert parse_wayback_timestamp("") == datetime.date.today().isoformat()
        assert parse_wayback_timestamp("invalid") == datetime.date.today().isoformat()


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

    def test_html_formatting_tags_inside_ref_not_flagged_as_undefined(self):
        wikitext = (
            '<ref name="book">पुस्तक <i>शीर्षक</i> और <b>महत्वपूर्ण</b> तथ्य</ref> '
            'आगे का पाठ <ref name="book" />\n== सन्दर्भ ==\n{{सन्दर्भ सूची}}'
        )
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "undefined_named_ref" not in err_types

    def test_arbitrary_attribute_order_detected(self):
        wikitext = '<ref group="note" name="ghost_ref" />\n== सन्दर्भ ==\n{{सन्दर्भ सूची}}'
        res = scan_reference_errors(wikitext)
        err_names = [e.get("ref_name") for e in res["errors"] if e["type"] == "undefined_named_ref"]
        assert "ghost_ref" in err_names

    def test_ignores_comments(self):
        wikitext = "पाठ <!-- <ref></ref> -->\n== सन्दर्भ ==\n{{सन्दर्भ सूची}}"
        res = scan_reference_errors(wikitext)
        err_types = [e["type"] for e in res["errors"]]
        assert "empty_ref" not in err_types


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

    def test_proportional_deduction_not_overpenalizing_large_articles(self):
        # 1 bare URL out of 50 citations should not ruin health score
        formatted = "".join(
            f"<ref>{{{{Cite web |title=स्रोत {i} |url=https://s{i}.org}}}}</ref>"
            for i in range(49)
        )
        wikitext = formatted + "<ref>https://bare.org</ref>\n{{सन्दर्भ सूची}}"
        res = calculate_reference_scorecard(wikitext)
        assert res["health_score"] >= 80
        assert res["grade"] == "A"

    def test_grade_d_support_for_low_scores(self):
        # Multiple bare URLs with no reflist container
        wikitext = "<ref>https://b1.com</ref><ref>https://b2.com</ref><ref>https://b3.com</ref>"
        res = calculate_reference_scorecard(wikitext)
        assert res["health_score"] < 40
        assert res["grade"] == "D"
        assert res["status"] == "अति खराब"

    def test_zero_references_returns_100_grade_a(self):
        wikitext = "शुद्ध गद्य जिसमें कोई संदर्भ नहीं है।"
        res = calculate_reference_scorecard(wikitext)
        assert res["health_score"] == 100
        assert res["grade"] == "A"
        assert res["total_refs"] == 0


# --- Helper 6: Bibliographic Validator ---
class TestValidator:
    def test_isbn_13_validation(self):
        assert validate_isbn_13("978-0-306-40615-7") is True
        assert validate_isbn_13("978-0-201-63361-0") is True
        assert validate_isbn_13("978-0-306-40615-9") is False

    def test_isbn_10_validation(self):
        assert validate_isbn_10("0-306-40615-2") is True
        assert validate_isbn_10("0-8044-2957-X") is True
        assert validate_isbn_10("0-306-40615-9") is False

    def test_issn_validation(self):
        assert validate_issn("0028-0836") is True
        assert validate_issn("2434-561X") is True
        assert validate_issn("0028-0837") is False

    def test_doi_validation(self):
        assert validate_doi("10.1000/182") is True
        assert validate_doi("10.1038/nature12373") is True
        assert validate_doi("10.1002/anie.198402971+") is True
        assert validate_doi("https://doi.org/10.1000/182") is True
        assert validate_doi("http://dx.doi.org/10.1000/182") is True
        assert validate_doi("invalid_doi") is False

    def test_devanagari_numerals_normalization(self):
        assert normalize_devanagari_numerals("२०२३-०८-२३") == "2023-08-23"
        assert normalize_devanagari_numerals("९७८-०-३०६-४०६१५-७") == "978-0-306-40615-7"

    def test_devanagari_isbn_extraction_and_validation(self):
        wikitext = "{{Cite book |title=हिन्दी पुस्तक |isbn=९७८-०-३०६-४०६१५-७}}"
        res = validate_bibliographic_identifiers(wikitext)
        assert "isbn" in res["results"]
        assert res["results"]["isbn"]["valid"] is True
        assert res["results"]["isbn"]["type"] == "ISBN-13"

    def test_devanagari_date_sampling_anywhere_in_doc(self):
        long_preamble = "हिन्दी गद्य विवरण " * 40
        wikitext = long_preamble + "{{Cite book |title=किताब |date=२०२४-११-२१}}"
        res = validate_bibliographic_identifiers(wikitext)
        assert res["results"]["date_normalization"]["detected"] is True
        assert "2024-11-21" in res["results"]["date_normalization"]["normalized_sample"]

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

    def test_whitespace_around_equals_and_pipe(self):
        wikitext = (
            'पाठ<ref group = "टिप्पणी">विवरण</ref>\n'
            '== टिप्पणियाँ ==\n{{सन्दर्भ सूची | group = टिप्पणी}}'
        )
        res = check_ref_groups(wikitext)
        assert len(res["orphaned_groups"]) == 0
        assert res["is_consistent"] is True


# --- Helper 8: Domain Advisory ---
class TestDomainAdvisory:
    def test_flags_twitter_source_with_advisory(self):
        wikitext = "दावा।<ref>https://twitter.com/user/status/12345</ref>"
        res = check_unreliable_domains(wikitext)
        assert res["flagged_domains_count"] >= 1
        assert res["flagged"][0]["domain"] == "twitter.com"
        assert res["flagged"][0]["severity"] == "advisory"

    def test_clean_source_not_flagged(self):
        wikitext = "दावा।<ref>https://bbc.com/news/12345</ref>"
        res = check_unreliable_domains(wikitext)
        assert res["flagged_domains_count"] == 0

    def test_no_false_positive_on_tax_or_comedy(self):
        wikitext = "कड़ियाँ: https://tax.com/report और https://complex.comedy.org/show"
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
        assert "--- a/article.wiki\n" in patch["patch"]
        assert "+++ b/article.wiki\n" in patch["patch"]


# --- FastAPI HTTP Endpoints Integration ---
class TestHelperEndpoints:
    @pytest.mark.asyncio
    @respx.mock
    async def test_api_helpers_all_routes(self):
        respx.get("https://archive.org/wayback/available").mock(
            return_value=httpx.Response(
                200,
                json={
                    "archived_snapshots": {
                        "closest": {
                            "status": "200",
                            "available": True,
                            "url": (
                                "https://web.archive.org/web/20230101000000/"
                                "https://example.com/dead"
                            ),
                            "timestamp": "20230101000000",
                        }
                    },
                    "url": "https://example.com/dead",
                },
            )
        )

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
            d1 = r1.json()
            assert d1["duplicates_found"] >= 1

            # 1b. Apply Duplicate Merges
            r1b = await client.post(
                "/api/helpers/apply-duplicate-merges",
                json={
                    "wikitext": (
                        "बिंदु 1<ref>https://same.org</ref> "
                        "बिंदु 2<ref>https://same.org</ref>"
                    ),
                    "proposed_merges": d1["proposed_merges"],
                },
            )
            assert r1b.status_code == 200
            assert '<ref name="ref_1" />' in r1b.json()["wikitext"]

            # 2. Conflict Resolver
            r2 = await client.post(
                "/api/helpers/conflict-resolver",
                json={
                    "wikitext": '<ref name="t">क</ref> और <ref name="t">ख</ref>'
                },
            )
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["conflicts_count"] == 1

            # 2b. Apply Conflict Resolution
            r2b = await client.post(
                "/api/helpers/apply-conflict-resolution",
                json={
                    "wikitext": '<ref name="t">क</ref> और <ref name="t">ख</ref>',
                    "resolutions": d2["conflicts"][0]["spans"],
                },
            )
            assert r2b.status_code == 200
            assert '<ref name="t_1">क</ref>' in r2b.json()["wikitext"]

            # 3. Dead Links
            r3 = await client.post(
                "/api/helpers/dead-links",
                json={
                    "wikitext": (
                        "<ref>{{Cite web |url=https://example.com/dead "
                        "|title=T |deadurl=yes}}</ref>"
                    )
                },
            )
            assert r3.status_code == 200

            # 4. Error Scanner
            r4 = await client.post(
                "/api/helpers/error-scanner",
                json={"wikitext": "<ref>https://bare.com</ref>"},
            )
            assert r4.status_code == 200

            # 5. Scorecard
            r5 = await client.post(
                "/api/helpers/scorecard",
                json={"wikitext": "<ref>https://bare.com</ref>"},
            )
            assert r5.status_code == 200

            # 6. Validator
            r6 = await client.post(
                "/api/helpers/validator",
                json={"wikitext": "{{Cite book |isbn=978-0-306-40615-7}}"},
            )
            assert r6.status_code == 200

            # 7. Group Checker
            r7 = await client.post(
                "/api/helpers/group-checker",
                json={"wikitext": '<ref group="g">पाठ</ref>'},
            )
            assert r7.status_code == 200

            # 8. Domain Advisory
            r8 = await client.post(
                "/api/helpers/domain-advisory",
                json={"wikitext": "दावा।<ref>https://twitter.com/user/status/12345</ref>"},
            )
            assert r8.status_code == 200

            # 9. Export Patch
            r9 = await client.post(
                "/api/helpers/export-patch",
                json={
                    "original_wikitext": "पंक्ति 1",
                    "modified_wikitext": "पंक्ति 1 संशोधित",
                    "filename": "diff.patch",
                },
            )
            assert r9.status_code == 200
