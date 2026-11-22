"""
Empirical Adversarial Stress Test Suite for Milestone 4 Helper Algorithms.
Challenger Agent: challenger_m4_1

Focus Areas:
1. Duplicate Ref Merger:
   - Multiple unnamed duplicate refs with differing groups:
     <ref group="note">https://a.com</ref> vs <ref>https://a.com</ref>.
   - Slug collision stress: wikitext already containing ref_1 ... ref_10 (and ref_1 ... ref_50).
   - Verify that first duplicate becomes a valid named ref definition in output.
2. Conflict Resolver:
   - Verify that identical names in different groups do NOT flag false conflicts.
   - Verify apply_conflict_resolution produces valid wikitext with renamed tags.
3. Error Scanner:
   - Refs containing complex HTML formatting (<b>, <i>, <span>, <code>).
   - Self-closing refs with group before name: <ref group="x" name="y" />.
   - Comments containing pseudo-ref tags <!-- <ref> -->.
4. Dead Links:
   - Text with word '404' or 'dead' in main prose outside refs (must NOT trigger false positive).
   - Simulated Wayback API offline/timeout fallback.
"""

import datetime
import pytest
import httpx
import respx

from app.services.parser import parse_wikitext_references
from app.services.helpers.duplicate_merger import (
    find_duplicate_references,
    apply_duplicate_merges,
)
from app.services.helpers.conflict_resolver import (
    detect_ref_conflicts,
    apply_conflict_resolution,
)
from app.services.helpers.error_scanner import scan_reference_errors
from app.services.helpers.dead_links import check_dead_links
from app.services.wayback import parse_wayback_timestamp


# ==============================================================================
# 1. DUPLICATE REF MERGER STRESS TESTS
# ==============================================================================

class TestDuplicateMergerAdversarial:
    """Empirical tests for Duplicate Ref Merger edge cases."""

    def test_differing_groups_isolated_are_not_merged(self):
        """
        Unnamed duplicate refs with differing groups must NOT be merged together.
        <ref group="note">https://a.com</ref> vs <ref>https://a.com</ref>
        belong to separate citation namespaces and must remain distinct.
        """
        wikitext = (
            'पहला तथ्य<ref group="note">https://a.com</ref> '
            'दूसरा तथ्य<ref>https://a.com</ref> '
            'तीसरा तथ्य<ref group="editorial">https://a.com</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 0, (
            "Refs with identical URL but differing groups must NOT be considered duplicates."
        )
        assert len(res["proposed_merges"]) == 0

    def test_multi_group_unnamed_duplicates_merged_independently(self):
        """
        When there are 2 unnamed refs in group 'note', 2 in 'fn', and 2 in default group,
        each group must merge its duplicates independently, preserving group attributes
        and assigning distinct slugs.
        """
        wikitext = (
            'नोट १<ref group="note">https://a.com</ref> '
            'नोट २<ref group="note">https://a.com</ref> '
            'पादटिप्पणी १<ref group="fn">https://a.com</ref> '
            'पादटिप्पणी २<ref group="fn">https://a.com</ref> '
            'मुख्य १<ref>https://a.com</ref> '
            'मुख्य २<ref>https://a.com</ref>'
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 3, (
            "Expected 3 independent duplicate pairs (1 per group)."
        )

        merges = res["proposed_merges"]
        # Each pair should produce 2 merges (1 definition + 1 invocation) = 6 total merges
        assert len(merges) == 6

        # Verify generated target names are all distinct
        target_names = {m["target_name"] for m in merges}
        assert len(target_names) == 3, f"Expected 3 distinct target slugs, got {target_names}"

        # Apply merges and verify group preservation in output
        merged_wikitext = apply_duplicate_merges(wikitext, merges)
        assert 'group="note"' in merged_wikitext
        assert 'group="fn"' in merged_wikitext

        # Re-parse to verify no duplicates remain in any group
        recheck = find_duplicate_references(merged_wikitext)
        assert recheck["duplicates_found"] == 0

    def test_slug_collision_stress_ref_1_to_ref_10_already_present(self):
        """
        When wikitext already contains named refs ref_1 through ref_10,
        the merger must not overwrite or collide with any of them.
        It must safely pick ref_11 for the first duplicate cluster.
        """
        pre_existing = " ".join([f'<ref name="ref_{i}">स्रोत {i}</ref>' for i in range(1, 11)])
        wikitext = (
            f"{pre_existing} "
            f"नया अ<ref>https://unique-content.org</ref> "
            f"नया ब<ref>https://unique-content.org</ref>"
        )

        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 1
        assert len(res["proposed_merges"]) == 2

        chosen_slug = res["proposed_merges"][0]["target_name"]
        assert chosen_slug == "ref_11", f"Expected slug 'ref_11', got '{chosen_slug}'"

        # Apply merges and verify pre-existing refs 1..10 are unaltered
        output = apply_duplicate_merges(wikitext, res["proposed_merges"])
        for i in range(1, 11):
            assert f'<ref name="ref_{i}">स्रोत {i}</ref>' in output
        assert '<ref name="ref_11">https://unique-content.org</ref>' in output
        assert '<ref name="ref_11" />' in output

    def test_slug_collision_large_scale_ref_1_to_ref_50_multi_clusters(self):
        """
        Stress test slug generation with ref_1 through ref_50 pre-existing,
        and 3 separate duplicate clusters to merge.
        """
        pre_existing = " ".join([f'<ref name="ref_{i}">डेटा {i}</ref>' for i in range(1, 51)])
        wikitext = (
            f"{pre_existing} "
            f"क१<ref>https://cluster1.org</ref> क२<ref>https://cluster1.org</ref> "
            f"ख१<ref>https://cluster2.org</ref> ख२<ref>https://cluster2.org</ref> "
            f"ग१<ref>https://cluster3.org</ref> ग२<ref>https://cluster3.org</ref>"
        )

        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 3
        slugs = {m["target_name"] for m in res["proposed_merges"]}
        expected_slugs = {"ref_51", "ref_52", "ref_53"}
        assert slugs == expected_slugs, f"Expected {expected_slugs}, got {slugs}"

        output = apply_duplicate_merges(wikitext, res["proposed_merges"])
        recheck = find_duplicate_references(output)
        assert recheck["duplicates_found"] == 0

    def test_first_duplicate_becomes_valid_named_definition_in_output(self):
        """
        Verify that apply_duplicate_merges transforms the first duplicate into a full
        named ref definition, while all subsequent duplicates become self-closing citations.
        """
        wikitext = (
            "शुरुआत <ref>https://duplicate-article.org/sample</ref> "
            "मध्य <ref>https://duplicate-article.org/sample</ref> "
            "अंत <ref>https://duplicate-article.org/sample</ref>"
        )
        res = find_duplicate_references(wikitext)
        assert res["duplicates_found"] == 2
        assert len(res["proposed_merges"]) == 3

        output = apply_duplicate_merges(wikitext, res["proposed_merges"])

        # Check AST of output
        parsed = parse_wikitext_references(output)
        assert len(parsed.references) == 3

        # First ref must be the definition
        first = parsed.references[0]
        assert first.name == "ref_1"
        assert first.self_closing is False
        assert first.inner_content.strip() == "https://duplicate-article.org/sample"

        # Second and third refs must be self-closing invocations
        second = parsed.references[1]
        assert second.name == "ref_1"
        assert second.self_closing is True
        assert second.inner_content.strip() == ""

        third = parsed.references[2]
        assert third.name == "ref_1"
        assert third.self_closing is True
        assert third.inner_content.strip() == ""


# ==============================================================================
# 2. CONFLICT RESOLVER STRESS TESTS
# ==============================================================================

class TestConflictResolverAdversarial:
    """Empirical tests for Duplicate Name Conflict Resolver edge cases."""

    def test_identical_names_in_differing_groups_do_not_flag_false_conflicts(self):
        """
        In MediaWiki, <ref name="apple" group="note">...</ref> and <ref name="apple">...</ref>
        reside in separate group scopes. They must NOT be flagged as conflicting.
        """
        wikitext = (
            'विवरण १<ref name="shared" group="note">टिप्पणी का विवरण</ref> '
            'विवरण २<ref name="shared">मुख्य लेख का आधिकारिक सन्दर्भ</ref>'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0, (
            "Identical ref names in different groups must NOT trigger false conflicts."
        )
        assert res["conflicts"] == []

    def test_identical_names_across_three_groups_no_false_conflict(self):
        """
        Stress test identical name across 3 groups: group="fn", group="ref", and default.
        """
        wikitext = (
            '<ref name="data" group="fn">पादटिप्पणी सामग्री</ref> '
            '<ref name="data" group="ref">विशेष सन्दर्भ सामग्री</ref> '
            '<ref name="data">मुख्य संदर्भ सामग्री</ref>'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 0

    def test_real_conflict_within_same_group_detected_properly(self):
        """
        Two refs with identical name AND identical group, but different contents,
        MUST be detected as a conflict with correct group attribution.
        """
        wikitext = (
            '<ref name="x" group="note">सामग्री प्रथम</ref> '
            '<ref name="x" group="note">सामग्री द्वितीय (भिन्न)</ref>'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 1
        conflict = res["conflicts"][0]
        assert conflict["ref_name"] == "x"
        assert conflict["group"] == "note"
        assert len(conflict["conflicting_contents"]) == 2

    def test_apply_conflict_resolution_produces_valid_wikitext_with_renamed_tags(self):
        """
        Verifies that apply_conflict_resolution cleanly updates tag names
        without corrupting wikitext.
        """
        wikitext = (
            'शुरुआती पाठ। <ref name="info">प्रथम सूचना</ref> '
            'मध्यवर्ती पाठ। <ref name="info">द्वितीय भिन्न सूचना</ref> '
            'अंतिम पाठ।'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 1

        resolved = apply_conflict_resolution(wikitext, res["conflicts"])

        # Check that original names are renamed to info_1 and info_2
        assert '<ref name="info_1">प्रथम सूचना</ref>' in resolved
        assert '<ref name="info_2">द्वितीय भिन्न सूचना</ref>' in resolved
        assert 'शुरुआती पाठ। ' in resolved
        assert 'मध्यवर्ती पाठ। ' in resolved
        assert 'अंतिम पाठ।' in resolved

        # Re-check to verify zero conflicts remain
        recheck = detect_ref_conflicts(resolved)
        assert recheck["conflicts_count"] == 0

    def test_apply_conflict_resolution_preserves_group_attributes(self):
        """
        Verifies that apply_conflict_resolution preserves the group attribute during renames.
        """
        wikitext = (
            '<ref name="dup" group="टिप्पणी">वर्णन १</ref> '
            '<ref name="dup" group="टिप्पणी">वर्णन २</ref>'
        )
        res = detect_ref_conflicts(wikitext)
        assert res["conflicts_count"] == 1

        resolved = apply_conflict_resolution(wikitext, res["conflicts"])
        assert '<ref name="dup_1" group="टिप्पणी">वर्णन १</ref>' in resolved
        assert '<ref name="dup_2" group="टिप्पणी">वर्णन २</ref>' in resolved


# ==============================================================================
# 3. ERROR SCANNER STRESS TESTS
# ==============================================================================

class TestErrorScannerAdversarial:
    """Empirical tests for Reference Error Scanner edge cases."""

    def test_refs_containing_complex_html_formatting(self):
        """
        Refs containing complex HTML formatting (<b>, <i>, <span>, <code>, <small>, <sup>)
        must NOT trigger empty_ref, unclosed_ref, or undefined_named_ref errors.
        """
        wikitext = (
            'पाठ <ref name="styled_ref">यह <b>बोल्ड</b>, <i>इटैलिक</i>, '
            '<span style="color: blue;">नीला पाठ</span>, और '
            '<code>console.log("ok")</code> सहित <sup>सुपर</sup> है।</ref> '
            'दोबारा संदर्भ <ref name="styled_ref" /> {{सन्दर्भ सूची}}'
        )
        res = scan_reference_errors(wikitext)
        assert res["error_count"] == 0, (
            f"Expected 0 errors for valid HTML formatted ref, got {res['errors']}"
        )

    def test_self_closing_ref_group_before_name(self):
        """
        Self-closing ref with group before name: <ref group="x" name="y" />
        must be correctly recognized and not flagged as undefined or unclosed.
        """
        wikitext = (
            'परिभाषा: <ref group="g1" name="my_source">आधिकारिक पुस्तक पृष्ठ ५</ref> '
            'आह्वान: <ref group="g1" name="my_source" /> '
            '{{सन्दर्भ सूची}}'
        )
        res = scan_reference_errors(wikitext)
        assert res["error_count"] == 0, f"Expected 0 errors, got {res['errors']}"

    def test_self_closing_ref_arbitrary_spacing_and_quote_permutations(self):
        """
        Test variations: single quotes, unspaced slash, trailing whitespace.
        """
        wikitext = (
            "<ref name='s1'>स्रोत १</ref>"
            "<ref name='s1'/>"
            "<ref group='g' name='s2'>स्रोत २</ref>"
            "<ref group='g' name='s2' />"
            "{{सन्दर्भ सूची}}"
        )
        res = scan_reference_errors(wikitext)
        assert res["error_count"] == 0, f"Expected 0 errors, got {res['errors']}"

    def test_comments_containing_pseudo_ref_tags_ignored(self):
        """
        Wikitext comments containing pseudo-ref tags like <!-- <ref> -->
        or <!-- <ref name="foo" /> --> must be strictly stripped before error scanning
        and NOT trigger false errors.
        """
        wikitext = (
            'वास्तविक पाठ '
            '<!-- <ref>यह अधूरा और खाली टैग टिप्पणी में है</ref> --> '
            '<!-- <ref name="phantom" /> --> '
            '<!-- <ref name="broken"> --> '
            '<!-- </ref> --> '
            'सच्चा संदर्भ <ref name="real_ref">सच्चा सन्दर्भ विवरण</ref> '
            '{{reflist}}'
        )
        res = scan_reference_errors(wikitext)
        assert res["error_count"] == 0, (
            f"Commented pseudo-refs caused false errors: {res['errors']}"
        )

    def test_real_errors_detected_alongside_safe_html_and_comments(self):
        """
        Ensure real errors (e.g. genuinely undefined ref, genuine empty ref)
        are still correctly detected even when comments and HTML are present.
        """
        wikitext = (
            '<!-- <ref>टिप्पणी</ref> --> '
            'वास्तविक खाली <ref></ref> '
            'वास्तविक अपरिभाषित <ref name="missing_target" /> '
            '{{reflist}}'
        )
        res = scan_reference_errors(wikitext)
        err_types = {e["type"] for e in res["errors"]}
        assert "empty_ref" in err_types
        assert "undefined_named_ref" in err_types


# ==============================================================================
# 4. DEAD LINKS & WAYBACK RESILIENCE STRESS TESTS
# ==============================================================================

class TestDeadLinksAdversarial:
    """Empirical tests for Dead Link helper and Wayback API resilience."""

    @pytest.mark.asyncio
    async def test_word_404_or_dead_in_main_prose_outside_refs_no_false_positive(self):
        """
        Prose containing words like '404' or 'dead' outside of reference tags
        must NOT trigger false positives for dead link detection.
        """
        wikitext = (
            "इतिहास: वर्ष 404 ईसा पूर्व में पेलोपोनेशियन युद्ध समाप्त हुआ और "
            "एक dead language का अध्ययन किया गया। कमरे का नंबर 404 था। "
            "<ref>https://live-verified-site.org/history</ref> "
            "{{सन्दर्भ सूची}}"
        )
        res = await check_dead_links(wikitext)
        assert res["dead_links_count"] == 0, (
            f"Expected 0 dead links from prose tokens '404' and 'dead', "
            f"got {res['dead_links_count']}"
        )
        assert res["results"] == []

    @pytest.mark.asyncio
    async def test_dead_indicators_inside_ref_detected(self):
        """
        When dead indicators (e.g. deadurl=yes or url-status=dead) ARE inside <ref>,
        the link must be correctly flagged for Wayback verification.
        """
        wikitext = (
            'सामान्य पाठ '
            '<ref>{{Cite web |url=https://broken-archive-candidate.org/item '
            '|title=Test |deadurl=yes}}</ref> '
            '{{reflist}}'
        )
        snapshot_url = (
            "https://web.archive.org/web/20200101120000/"
            "https://broken-archive-candidate.org/item"
        )
        with respx.mock(base_url="https://archive.org") as respx_mock:
            respx_mock.get("/wayback/available").respond(
                status_code=200,
                json={
                    "archived_snapshots": {
                        "closest": {
                            "available": True,
                            "url": snapshot_url,
                            "timestamp": "20200101120000",
                            "status": "200",
                        }
                    }
                },
            )
            res = await check_dead_links(wikitext)
            assert res["dead_links_count"] == 1
            entry = res["results"][0]
            assert entry["url"] == "https://broken-archive-candidate.org/item"
            assert entry["is_dead"] is True
            assert entry["available"] is True
            assert entry["archive_date"] == "2020-01-01"

    @pytest.mark.asyncio
    async def test_simulated_wayback_api_timeout_fallback(self):
        """
        Simulate Wayback API network timeout.
        The client must handle it gracefully, return available=False,
        and provide a dynamic fallback archive URL and current date without crashing.
        """
        url = "https://timeout-test-domain.org/broken-page"
        wikitext = f'<ref>{{{{Cite web |url={url} |dead-url=yes}}}}</ref> {{reflist}}'

        with respx.mock(base_url="https://archive.org") as respx_mock:
            respx_mock.get("/wayback/available").mock(
                side_effect=httpx.TimeoutException("Read timed out after 10.0s")
            )
            res = await check_dead_links(wikitext)
            assert res["dead_links_count"] == 1
            item = res["results"][0]
            assert item["url"] == url
            assert item["available"] is False
            assert item["url_status"] == "dead"
            assert item["archive_url"].startswith("https://web.archive.org/web/")
            assert item["archive_date"] == datetime.date.today().isoformat()

    @pytest.mark.asyncio
    async def test_simulated_wayback_api_http_503_and_500_fallback(self):
        """
        Simulate Wayback API returning HTTP 500 or 503 Service Unavailable.
        Must gracefully degrade to dynamic fallback without crash.
        """
        url = "https://server-error-test.org/dead"
        wikitext = f'<ref>{{{{Cite web |url={url} |url-status=dead}}}}</ref>'

        with respx.mock(base_url="https://archive.org") as respx_mock:
            respx_mock.get("/wayback/available").respond(
                status_code=503,
                text="Service Unavailable",
            )
            res = await check_dead_links(wikitext)
            assert res["dead_links_count"] == 1
            item = res["results"][0]
            assert item["available"] is False
            assert item["archive_url"].startswith("https://web.archive.org/web/")
            assert item["archive_date"] == datetime.date.today().isoformat()

    @pytest.mark.asyncio
    async def test_simulated_wayback_api_connect_error_fallback(self):
        """
        Simulate Wayback API complete network outage / ConnectError.
        """
        url = "https://offline-test.org/broken"
        wikitext = f'<ref>{{{{Cite web |url={url} |मृत कड़ी=हाँ}}}}</ref>'

        with respx.mock(base_url="https://archive.org") as respx_mock:
            respx_mock.get("/wayback/available").mock(
                side_effect=httpx.ConnectError("Connection refused: [Errno 111]")
            )
            res = await check_dead_links(wikitext)
            assert res["dead_links_count"] == 1
            item = res["results"][0]
            assert item["available"] is False
            assert item["url_status"] == "dead"
            assert item["archive_url"].startswith("https://web.archive.org/web/")

    def test_parse_wayback_timestamp_edge_cases(self):
        """
        Directly verify timestamp parsing edge cases: 14-digit, 8-digit, empty,
        corrupted, and invalid month/day values.
        """
        assert parse_wayback_timestamp("20210515123045") == "2021-05-15"
        assert parse_wayback_timestamp("20180228") == "2018-02-28"
        today = datetime.date.today().isoformat()
        assert parse_wayback_timestamp("") == today
        assert parse_wayback_timestamp(None) == today
        assert parse_wayback_timestamp("invalid_timestamp") == today
        assert parse_wayback_timestamp("20219999") == today
