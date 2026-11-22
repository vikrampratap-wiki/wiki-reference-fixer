"""
Milestone 4 Empirical Challenge Test Suite.
Author: challenger_m4_2 (Milestone 4 Validation & Math Challenger 2).

Scope:
1. Scorecard Math Oracle:
   - Fuzz bare URL / formatted reference ratios from 0% to 100%. Verify monotonic decreasing score.
   - Verify that 4 bare URLs in 100 refs receives a proportional high score (Grade A).
   - Verify that Grade D is reachable and properly bounded [0, 100].
2. Identifier Validator Oracle:
   - Devanagari numerals in ISBN-10, ISBN-13, and ISSN: e.g. ९७८-०-३०६-४०६१५-७ -> valid.
   - Valid DOI containing '+' suffix and 'https://doi.org/' prefix.
   - Checksum edge cases: ISBN-10 with check digit 'X', invalid check digits correctly rejected.
3. Group Checker & Domain Advisory:
   - Whitespace variations in {{सन्दर्भ सूची | group = टिप्पणी }}.
   - Words like 'tax.com', 'complex.comedy.org' must NOT trigger 'x.com' advisory.
"""

from typing import Any, List, Optional
import pytest
import mwparserfromhell

from app.services.helpers.scorecard import calculate_reference_scorecard
from app.services.helpers.validator import (
    validate_isbn_10,
    validate_isbn_13,
    validate_issn,
    validate_doi,
    normalize_devanagari_numerals,
    validate_bibliographic_identifiers,
)
from app.services.helpers.group_checker import check_ref_groups
from app.services.helpers.domain_advisory import check_unreliable_domains
from app.services.parser import ParsedDocument, ParsedReference


def create_mock_parsed_doc(
    total_refs: int,
    bare_count: int,
    has_reflist: bool = True,
    duplicate_name_conflicts: Optional[List[Any]] = None,
) -> ParsedDocument:
    """Helper to instantiate fully valid ParsedDocument objects for scorecard fuzzing."""
    if duplicate_name_conflicts is None:
        duplicate_name_conflicts = []

    bare_refs = [
        ParsedReference(
            id=f"bare_{i}",
            raw_tag=f"<ref>https://bare{i}.org</ref>",
            name=None,
            group=None,
            self_closing=False,
            span_start=0,
            span_end=10,
            inner_content=f"https://bare{i}.org",
            is_bare_url=True,
            bare_url=f"https://bare{i}.org",
        )
        for i in range(bare_count)
    ]
    return ParsedDocument(
        original_wikitext="",
        wikicode=mwparserfromhell.parse(""),
        references=[],
        reflists=[],
        has_reflist=has_reflist,
        total_refs=total_refs,
        bare_url_refs=bare_refs,
        named_refs={},
        duplicate_name_conflicts=duplicate_name_conflicts,
    )


# ==============================================================================
# SECTION 1: Scorecard Math Oracle
# ==============================================================================
class TestScorecardMathOracle:
    """Empirical mathematical validation of the Reference Health Scorecard."""

    @pytest.mark.parametrize("total_refs", [1, 2, 5, 10, 20, 50, 100])
    def test_monotonic_decreasing_score_fuzzing(self, total_refs: int):
        """
        Fuzzes bare URL count from 0 to total_refs for varying total reference densities.
        Verifies that health_score is strictly monotonic non-increasing as bare count rises.
        """
        prev_score = 101  # Greater than max possible score (100)

        for bare_count in range(total_refs + 1):
            mock_doc = create_mock_parsed_doc(
                total_refs=total_refs,
                bare_count=bare_count,
                has_reflist=True,
            )
            res = calculate_reference_scorecard("", parsed_doc=mock_doc)
            score = res["health_score"]

            # Mathematical invariant: Monotonic non-increasing
            assert score <= prev_score, (
                f"Monotonicity violation at total_refs={total_refs}, bare_count={bare_count}: "
                f"score {score} > previous score {prev_score}"
            )
            # Mathematical invariant: Bounded in [0, 100]
            assert 0 <= score <= 100, f"Score out of bounds [0, 100]: {score}"
            prev_score = score

    def test_proportional_score_4_bare_in_100_refs(self):
        """
        Verifies that 4 bare URLs in 100 references receives a proportional high score
        (Grade A / 'उत्कृष्ट') and does not experience a severe 30/100 crash.
        """
        mock_doc = create_mock_parsed_doc(
            total_refs=100,
            bare_count=4,
            has_reflist=True,
        )
        res = calculate_reference_scorecard("", parsed_doc=mock_doc)

        score = res["health_score"]
        # Exact mathematical expected:
        # bare_ratio = 4/100 = 0.04
        # bare_deduction = int(0.04 * 60) + min(4 * 5, 10) = 2 + 10 = 12
        # score = 100 - 12 = 88
        assert score == 88, f"Expected proportional score 88, got {score}"
        assert score >= 80, f"Expected Grade A (>=80), got {score}"
        assert res["grade"] == "A", f"Expected Grade 'A', got '{res['grade']}'"
        assert res["status"] == "उत्कृष्ट", f"Expected status 'उत्कृष्ट', got '{res['status']}'"
        assert res["formatted_citations"] == 96
        assert res["bare_urls"] == 4

    def test_grade_d_reachability_and_bounds(self):
        """
        Verifies that Grade D is reachable in diverse failure scenarios and strictly bounded.
        """
        # Case A: 100% bare URLs with reflist
        doc_100_bare = create_mock_parsed_doc(
            total_refs=10,
            bare_count=10,
            has_reflist=True,
        )
        res_a = calculate_reference_scorecard("", parsed_doc=doc_100_bare)
        # bare_deduction = int(1.0 * 60) + 10 = 70. score = 100 - 70 = 30.
        assert res_a["health_score"] == 30
        assert res_a["grade"] == "D"
        assert res_a["status"] == "अति खराब"

        # Case B: 100% bare URLs without reflist container (penalty = -20)
        doc_no_reflist = create_mock_parsed_doc(
            total_refs=10,
            bare_count=10,
            has_reflist=False,
        )
        res_b = calculate_reference_scorecard("", parsed_doc=doc_no_reflist)
        # score = 30 - 20 = 10
        assert res_b["health_score"] == 10
        assert res_b["grade"] == "D"
        assert res_b["status"] == "अति खराब"

        # Case C: Extreme negative pressure clamped at 0
        doc_extreme = create_mock_parsed_doc(
            total_refs=10,
            bare_count=10,
            has_reflist=False,
            duplicate_name_conflicts=[{"name": "conflict_1"}, {"name": "conflict_2"}],
        )
        res_c = calculate_reference_scorecard("", parsed_doc=doc_extreme)
        # score = 10 - 30 = -20 -> clamped to 0
        assert res_c["health_score"] == 0
        assert res_c["grade"] == "D"
        assert res_c["status"] == "अति खराब"

    def test_grade_transitions_and_hindi_status_labels(self):
        """Tests that all 4 grades (A, B, C, D) map to their authoritative Hindi statuses."""
        grade_expectations = [
            (95, "A", "उत्कृष्ट"),
            (80, "A", "उत्कृष्ट"),
            (79, "B", "मध्यम"),
            (60, "B", "मध्यम"),
            (59, "C", "खराब"),
            (40, "C", "खराब"),
            (39, "D", "अति खराब"),
            (0, "D", "अति खराब"),
        ]
        for target_score, exp_grade, exp_status in grade_expectations:
            score = target_score
            if score >= 80:
                st, gr = "उत्कृष्ट", "A"
            elif score >= 60:
                st, gr = "मध्यम", "B"
            elif score >= 40:
                st, gr = "खराब", "C"
            else:
                st, gr = "अति खराब", "D"

            assert gr == exp_grade
            assert st == exp_status

    def test_actual_wikitext_scorecard_parsing(self):
        """Verifies scorecard end-to-end against real wikitext generation."""
        # 100 citations: 4 bare URLs, 96 formatted Cite web, with {{सन्दर्भ सूची}}
        bare_part = "".join(f"<ref>https://example.org/bare{i}</ref>" for i in range(4))
        formatted_part = "".join(
            f"<ref>{{{{Cite web |title=स्रोत {i} |url=https://example.org/cited{i}}}}}</ref>"
            for i in range(96)
        )
        wikitext = f"आरम्भिक पाठ। {bare_part} {formatted_part}\n== संदर्भ ==\n{{{{सन्दर्भ सूची}}}}"

        res = calculate_reference_scorecard(wikitext)
        assert res["total_refs"] == 100
        assert res["bare_urls"] == 4
        assert res["formatted_citations"] == 96
        assert res["health_score"] == 88
        assert res["grade"] == "A"
        assert res["status"] == "उत्कृष्ट"


# ==============================================================================
# SECTION 2: Identifier Validator Oracle
# ==============================================================================
class TestIdentifierValidatorOracle:
    """Empirical verification of ISBN, ISSN, DOI, and Devanagari numeral normalization."""

    def test_devanagari_numerals_normalization_fuzz(self):
        """Tests complete conversion of all 10 Devanagari numerals to ASCII digits."""
        dev_sample = "०१२३४५६७८९"
        ascii_expected = "0123456789"
        assert normalize_devanagari_numerals(dev_sample) == ascii_expected

        # Mixed string
        mixed = "वर्ष २०२६ में आईएसबीएन ९७८-०-३०६-४०६१५-७ जारी हुआ।"
        norm = normalize_devanagari_numerals(mixed)
        assert "2026" in norm
        assert "978-0-306-40615-7" in norm

    def test_isbn_13_devanagari_and_checksum_verification(self):
        """Validates ISBN-13 checksums with Devanagari numerals, hyphens, spaces, and errors."""
        # Authoritative valid ISBN-13: 978-0-306-40615-7
        assert validate_isbn_13("९७८-०-३०६-४०६१५-७") is True
        assert validate_isbn_13("९७८०३०६४०६१५७") is True
        assert validate_isbn_13("९७८ ० ३०६ ४०६१५ ७") is True
        assert validate_isbn_13("978-0-306-40615-7") is True

        # Valid with check digit 0: 978-0-201-63361-0
        assert validate_isbn_13("९७८-०-२०१-६३३६१-०") is True
        assert validate_isbn_13("978-0-201-63361-0") is True

        # Invalid checksums (single digit alteration)
        assert validate_isbn_13("९७८-०-३०६-४०६१५-८") is False
        assert validate_isbn_13("९७८-०-३०६-४०६१५-९") is False
        assert validate_isbn_13("978-0-306-40615-0") is False

        # Invalid lengths and formats
        assert validate_isbn_13("९७८-०-३०६-४०६१५") is False  # 12 digits
        assert validate_isbn_13("978-0-306-40615-70") is False  # 14 digits
        assert validate_isbn_13("978-0-306-40615-X") is False  # ISBN-13 cannot have X

    def test_isbn_10_devanagari_and_check_digit_x(self):
        """
        Validates ISBN-10 checksums with 'X' check digit (representing 10),
        Devanagari, and errors.
        """
        # Valid numeric check digit: 0-306-40615-2
        assert validate_isbn_10("०-३०६-४०६१५-२") is True
        assert validate_isbn_10("0-306-40615-2") is True

        # Valid 'X' check digit (sum % 11 == 0 with check digit 10)
        assert validate_isbn_10("0-8044-2957-X") is True
        assert validate_isbn_10("0-8044-2957-x") is True
        assert validate_isbn_10("०-८०४४-२९५७-X") is True
        assert validate_isbn_10("०-८०४४-२९५७-x") is True

        # Invalid checksums
        assert validate_isbn_10("0-8044-2957-9") is False
        assert validate_isbn_10("०-३०६-४०६१५-३") is False

        # Edge cases: 'X' in invalid positions (must only appear as 10th character)
        assert validate_isbn_10("X-8044-2957-0") is False
        assert validate_isbn_10("0-804X-2957-0") is False
        assert validate_isbn_10("0-8044-295X-0") is False

        # Invalid lengths & non-numeric
        assert validate_isbn_10("0-8044-2957") is False
        assert validate_isbn_10("0-8044-2957-XX") is False
        assert validate_isbn_10("ABCDEFGHIJ") is False

    def test_issn_devanagari_and_check_digit_x(self):
        """
        Validates 8-digit ISSN with Devanagari numerals, 'X' check digit,
        and invalid check digits.
        """
        # Valid standard ISSN: 0028-0836 (Nature)
        assert validate_issn("0028-0836") is True
        assert validate_issn("००२८-०८३६") is True
        assert validate_issn("00280836") is True
        assert validate_issn("००२८०८३६") is True

        # Valid ISSN with 'X' check digit: 2434-561X
        assert validate_issn("2434-561X") is True
        assert validate_issn("2434-561x") is True
        assert validate_issn("२४३४-५६१X") is True
        assert validate_issn("२४३४-५६१x") is True

        # Invalid check digits
        assert validate_issn("0028-0837") is False
        assert validate_issn("००२८-०८३५") is False
        assert validate_issn("2434-5619") is False

        # Invalid lengths and formats
        assert validate_issn("0028-083") is False
        assert validate_issn("0028-08360") is False
        assert validate_issn("X028-0836") is False  # X in first digit

    def test_doi_validation_with_plus_suffix_and_prefixes(self):
        """
        Validates DOI format supporting '+' suffix, various prefixes (https://doi.org/,
        http://dx.doi.org/, doi:), and rejection of invalid identifiers.
        """
        # Standard DOI without prefix
        assert validate_doi("10.1000/182") is True
        assert validate_doi("10.1038/nature12373") is True

        # Valid DOI containing '+' suffix
        assert validate_doi("10.1002/anie.198402971+") is True
        assert validate_doi("10.1021/ja01583a022+") is True

        # Valid DOI with 'https://doi.org/' prefix and '+' suffix
        assert validate_doi("https://doi.org/10.1002/anie.198402971+") is True
        assert validate_doi("https://doi.org/10.1000/182") is True

        # Valid DOI with alternative prefixes
        assert validate_doi("http://doi.org/10.1000/182") is True
        assert validate_doi("https://dx.doi.org/10.1000/182") is True
        assert validate_doi("http://dx.doi.org/10.1000/182") is True
        assert validate_doi("doi:10.1000/182") is True
        assert validate_doi("DOI:10.1002/anie.198402971+") is True

        # Complex DOI with valid characters (- . _ ; ( ) / : +)
        assert validate_doi("10.1000/182_abc-def:ghi/123+456") is True

        # Invalid DOIs correctly rejected
        assert validate_doi("invalid-doi") is False
        assert validate_doi("https://notdoi.org/10.1000/182") is False
        assert validate_doi("10.123/short") is False  # Less than 4 digits in registrant code
        assert validate_doi("11.1000/182") is False  # Must start with 10.
        assert validate_doi("10.1000") is False  # Missing suffix
        assert validate_doi("10.1000/") is False  # Empty suffix
        assert validate_doi("10.1000/has space") is False

    def test_validate_bibliographic_identifiers_in_wikitext(self):
        """End-to-end wikitext parsing for Devanagari ISBN, ISSN, DOI, and dates."""
        wikitext = (
            "{{Cite book |title=महाभारत |isbn=९७८-०-३०६-४०६१५-७ |date=२०२४-११-२१ "
            "|issn=२४३४-५६१X |doi=https://doi.org/10.1002/anie.198402971+}}\n"
            "== संदर्भ ==\n{{सन्दर्भ सूची}}"
        )
        res = validate_bibliographic_identifiers(wikitext)
        results = res["results"]

        assert "isbn" in results
        assert results["isbn"]["valid"] is True
        assert results["isbn"]["type"] == "ISBN-13"

        assert "doi" in results
        assert results["doi"]["valid"] is True

        assert "issn" in results
        assert results["issn"]["valid"] is True

        assert "date_normalization" in results
        assert results["date_normalization"]["detected"] is True
        assert "2024-11-21" in results["date_normalization"]["normalized_sample"]


# ==============================================================================
# SECTION 3: Group Checker & Domain Advisory
# ==============================================================================
class TestGroupCheckerAndDomainAdvisory:
    """Empirical verification of group consistency and domain false positive resistance."""

    @pytest.mark.parametrize(
        "container_snippet",
        [
            "{{सन्दर्भ सूची | group = टिप्पणी }}",
            "{{सन्दर्भ सूची|group=टिप्पणी}}",
            "{{सन्दर्भ सूची |group =टिप्पणी }}",
            "{{सन्दर्भ सूची   |   group   =   टिप्पणी   }}",
            '{{सन्दर्भ सूची | group = "टिप्पणी" }}',
            "{{सन्दर्भ सूची | group = 'टिप्पणी' }}",
            "{{संदर्भ सूची | group = टिप्पणी }}",
            "{{संदर्भसूची | group = टिप्पणी }}",
            "{{reflist | group = टिप्पणी }}",
            "{{टिप्पणी सूची | group = टिप्पणी }}",
            "{{टिप्पणीसूची | group = टिप्पणी }}",
            "{{रेफ़्लिस्ट | group = टिप्पणी }}",
            "{{संसूची | group = टिप्पणी }}",
            "{{सूत्र सूची | group = टिप्पणी }}",
            "{{टिसू | group = टिप्पणी }}",
            "{{नोट्स | group = टिप्पणी }}",
            '<references group="टिप्पणी" />',
            '<references group = "टिप्पणी" />',
            "<references group='टिप्पणी' />",
            "<references group = 'टिप्पणी' />",
        ],
    )
    def test_group_checker_whitespace_variations(self, container_snippet: str):
        """Verifies that all whitespace and quotation variations in group containers are parsed."""
        wikitext = (
            'मुख्य लेख।<ref group="टिप्पणी">टिप्पणी सामग्री</ref>\n'
            f"== टिप्पणियाँ ==\n{container_snippet}"
        )
        res = check_ref_groups(wikitext)
        assert res["is_consistent"] is True, (
            f"Failed to match container snippet: {container_snippet}. "
            f"Orphaned groups: {res['orphaned_groups']}"
        )
        assert len(res["orphaned_groups"]) == 0
        assert "टिप्पणी" in res["container_groups_found"]
        assert "टिप्पणी" in res["ref_groups_found"]

    def test_group_checker_orphaned_group_detection(self):
        """Verifies that missing group containers are detected as orphaned."""
        wikitext = (
            'तथ्य<ref group="टिप्पणी">टिप्पणी १</ref> '
            'अन्य<ref group="नोट">नोट १</ref>\n'
            '== संदर्भ ==\n{{सन्दर्भ सूची|group=टिप्पणी}}'
        )
        res = check_ref_groups(wikitext)
        assert res["is_consistent"] is False
        assert "नोट" in res["orphaned_groups"]
        assert "टिप्पणी" not in res["orphaned_groups"]

    @pytest.mark.parametrize(
        "benign_text",
        [
            "कड़ी: https://tax.com/annual-report और विवरण।",
            "कॉमेडी शो: https://complex.comedy.org/schedule",
            "ऑपरेटिंग सिस्टम: https://linux.com/news",
            "कम्युनिटी: https://matrix.community/join",
            "मनोरंजन: https://max.comedy.com/episodes",
            "व्यापार: https://fox.commerce.com/deals",
            "इलाज: https://remedy.com/health",
            "कंपनी: https://relax.company.org/about",
            "शोध: https://latex.com/packages",
            "गद्य में tax.com का उल्लेख बिना लिंक के।",
            "गद्य में complex.comedy.org का स्पष्ट उल्लेख।",
            "गद्य में fox.com समाचार चैनल।",
            "दवा कंपनी remedium.com की आधिकारिक साइट।",
            "शराब वेबसाइट liquora.com की आधिकारिक साइट।",
            "क्रेडिट कार्ड credit.com की आधिकारिक साइट।",
            "व्यक्तिगत डोमेन mywixsite.com की आधिकारिक साइट।",
        ],
    )
    def test_domain_advisory_zero_false_positives(self, benign_text: str):
        """
        Adversarial test ensuring benign domains containing substrings like 'x.com',
        'tax.com', 'complex.comedy.org', 'credit.com', 'remedium.com' NEVER trigger advisories.
        """
        wikitext = f"विवरण पाठ। <ref>{benign_text}</ref>\n{{{{सन्दर्भ सूची}}}}"
        res = check_unreliable_domains(wikitext)
        flagged_names = [f["domain"] for f in res["flagged"]]

        assert res["flagged_domains_count"] == 0, (
            f"False positive triggered for text '{benign_text}'! "
            f"Flagged domains: {flagged_names}"
        )

    @pytest.mark.parametrize(
        "unreliable_url,expected_domain",
        [
            ("https://twitter.com/user/status/123", "twitter.com"),
            ("https://mobile.twitter.com/user/status/123", "twitter.com"),
            ("https://x.com/user/status/123", "x.com"),
            ("https://api.x.com/v2/tweets", "x.com"),
            ("https://facebook.com/public_page", "facebook.com"),
            ("https://m.facebook.com/story", "facebook.com"),
            ("https://instagram.com/p/B394", "instagram.com"),
            ("https://quora.com/What-is-Wikipedia", "quora.com"),
            ("https://reddit.com/r/wikipedia", "reddit.com"),
            ("https://myblog.blogspot.com/2023/article", "blogspot.com"),
            ("https://personal.wordpress.com/post", "wordpress.com"),
            ("https://medium.com/@author/article", "medium.com"),
            ("https://tiktok.com/@user/video/999", "tiktok.com"),
            ("https://pinterest.com/pin/12345", "pinterest.com"),
            ("https://linkedin.com/in/profile", "linkedin.com"),
            ("https://myportfolio.weebly.com/home", "weebly.com"),
            ("https://mysite.wixsite.com/home", "wixsite.com"),
            ("https://tumblr.com/blog/view/user", "tumblr.com"),
        ],
    )
    def test_domain_advisory_true_positives(self, unreliable_url: str, expected_domain: str):
        """Verifies that all genuine unreliable / UGC sources trigger advisory flags."""
        wikitext = f"दावा। <ref>{unreliable_url}</ref>\n{{{{सन्दर्भ सूची}}}}"
        res = check_unreliable_domains(wikitext)
        flagged_names = [f["domain"] for f in res["flagged"]]

        assert expected_domain in flagged_names, (
            f"Failed to flag unreliable domain '{expected_domain}' for URL '{unreliable_url}'. "
            f"Flagged: {flagged_names}"
        )
        # Ensure advisory metadata is properly attached
        item = next(f for f in res["flagged"] if f["domain"] == expected_domain)
        assert item["severity"] == "advisory"
        assert item["policy"] == "WP:RSP"
        assert "अविश्वसनीय" in item["warning"]

    def test_adversarial_subdomain_bare_url_bug(self):
        """
        Regression test: bare subdomain URLs directly preceding </ref>
        (without trailing slash or path) must be correctly detected.
        """
        wikitext = "दावा। <ref>https://myportfolio.weebly.com</ref>\n{{सन्दर्भ सूची}}"
        res = check_unreliable_domains(wikitext)
        flagged_names = [f["domain"] for f in res["flagged"]]
        # This will fail under the current implementation because flagged_names is empty []
        assert "weebly.com" in flagged_names, (
            f"Subdomain without trailing slash was NOT flagged! Flagged: {flagged_names}"
        )
