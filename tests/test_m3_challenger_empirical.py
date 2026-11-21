"""
Adversarial Empirical Verification Suite for Milestone 3.
Authored by: challenger_m3_iter2_1
Testing targets:
- app/services/parser.py
- app/services/syntax_repair.py

Challenges Covered:
1. Zero Hardcoding & Dynamic Generic Domain Fallback (60+ arbitrary domains).
2. Huge Wikitext Scale Stress Test (150KB-300KB) with 100% byte-for-byte non-corruption.
3. Boundary & Contiguity Torture (0-gap adjacent spans, 1-char separators, start/end).
4. Devanagari Unicode, Conjuncts, ZWJ/ZWNJ, Numerals, Quotes & Attributes Torture.
5. Excluded Zone (Nowiki & HTML Comments) Collisions & Interleaving.
6. Syntax Repair & CS1 Modernization Complex Edge Cases.
7. Reconstructor Invariant & Corrupted Input Rejection.
"""

from __future__ import annotations

import datetime
import random
from typing import Tuple
import pytest

from app.services.parser import (
    Modification,
    apply_granular_modifications,
    parse_wikitext_references,
)
from app.services.syntax_repair import repair_syntax_and_modernize


# ==============================================================================
# 1. Zero Hardcoding & Dynamic Generic Fallback across 60+ Arbitrary Domains
# ==============================================================================
ARBITRARY_DOMAINS = [
    # Flagged in prior audits
    "jagran.com",
    "www.jagran.com",
    "timesofindia.indiatimes.com",
    "www.timesofindia.indiatimes.com",
    "bbc.com",
    "www.bbc.com",
    "hi.wikipedia.org",
    "en.wikipedia.org",
    # Indian news & media
    "amarujala.com",
    "navbharattimes.indiatimes.com",
    "aajtak.in",
    "ndtv.com",
    "thehindu.com",
    "indianexpress.com",
    "hindustantimes.com",
    "patrika.com",
    "bhaskar.com",
    "news18.com",
    "hindi.news18.com",
    "zeenews.india.com",
    "abplive.com",
    "livehindustan.com",
    "bbc.com/hindi",
    "dw.com/hi",
    # International & Academic / Gov
    "reuters.com",
    "apnews.com",
    "nytimes.com",
    "washingtonpost.com",
    "theguardian.com",
    "aljazeera.com",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "britannica.com",
    "loc.gov",
    "un.org",
    "who.int",
    "isro.gov.in",
    "iitk.ac.in",
    "delhi.gov.in",
    "archive.org",
    "github.com",
    "stackoverflow.com",
    "doi.org",
    # Complex / Multi-level subdomains / TLDs
    "subdomain.deep.example.co.uk",
    "news.service.state.gov.in",
    "unicode.org",
    "w3.org",
    "rfc-editor.org",
    "zenodo.org",
    "jstor.org",
    "arxiv.org",
    "ncbi.nlm.nih.gov",
    "pib.gov.in",
    "mea.gov.in",
    "prsindia.org",
    "thewire.in",
    "scroll.in",
    "newslaundry.com",
    "altnews.in",
]


class TestZeroHardcodingGenericFallback:
    """Stress tests verifying zero canned test titles and clean generic domain fallback."""

    PROHIBITED_STRINGS = [
        "दैनिक जागरण: राष्ट्रीय समाचार",
        "टाइम्स ऑफ इंडिया: ताज़ा ख़बरें और विश्लेषण",
        "2026-11-19",
        "2026-11-20",
    ]

    @pytest.mark.parametrize("domain_entry", ARBITRARY_DOMAINS)
    def test_pure_bare_url_zero_hardcoding_and_dynamic_fallback(
        self, domain_entry: str
    ) -> None:
        today = datetime.date.today().isoformat()
        clean_domain = domain_entry.split("/")[0]
        if clean_domain.startswith("www."):
            clean_domain = clean_domain[4:]

        url = f"https://{domain_entry}/articles/random-test-slug-{random.randint(1000, 9999)}.html"
        wikitext = f"आरम्भिक पाठ।<ref>{url}</ref> अन्तिम पाठ।"

        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.is_bare_url is True
        assert ref.bare_url == url

        assert len(doc.modifications) == 1
        mod = doc.modifications[0]
        proposed = mod.proposed_snippet

        # Prohibited pattern check: absolute ban on canned strings
        for bad_str in self.PROHIBITED_STRINGS:
            assert bad_str not in proposed, (
                f"Prohibited string '{bad_str}' found in proposed replacement: {proposed}"
            )

        # Dynamic fallback verification
        assert f"|url={url}" in proposed
        assert f"|access-date={today}" in proposed
        assert f"|title={clean_domain} वेब सन्दर्भ" in proposed
        assert f"|website={clean_domain}" in proposed
        assert proposed.startswith("<ref>{{Cite web")
        assert proposed.endswith("}}</ref>")

    @pytest.mark.parametrize("domain_entry", ARBITRARY_DOMAINS[:15])
    def test_bracketed_bare_url_with_custom_fallback_title(
        self, domain_entry: str
    ) -> None:
        today = datetime.date.today().isoformat()
        custom_title = f"अनुकूलित शीर्षक {domain_entry} परीक्षण"
        url = f"https://{domain_entry}/page/custom"
        wikitext = f"मुख्य पाठ।<ref>[{url} {custom_title}]</ref> अगला पाठ।"

        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.is_bare_url is True
        assert ref.bare_url == url
        assert ref.fallback_title == custom_title

        assert len(doc.modifications) == 1
        proposed = doc.modifications[0].proposed_snippet

        for bad_str in self.PROHIBITED_STRINGS:
            assert bad_str not in proposed

        assert f"|url={url}" in proposed
        assert f"|title={custom_title}" in proposed
        assert f"|access-date={today}" in proposed
        assert proposed.startswith("<ref>{{Cite web")
        assert proposed.endswith("}}</ref>")


# ==============================================================================
# 2. Huge Wikitext Scale Stress Test (150KB - 300KB) with Byte Invariance
# ==============================================================================
class TestHugeWikitextScaleStress:
    """Stress tests running against huge wikitext inputs (100KB-300KB+) with 100+ references."""

    @pytest.fixture
    def huge_wikitext(self) -> Tuple[str, int, int]:
        """Generates a ~200KB wikitext article with realistic sections, templates, and refs."""
        sections = []
        sections.append("{{ज्ञानसन्दूक देश |नाम = भारत गणराज्य |राजधानी = नई दिल्ली}}\n")
        sections.append("'''भारत''' दक्षिण एशिया में स्थित एक विशाल देश है।\n")

        total_refs = 0
        bare_url_count = 0

        sample_domains = ["bbc.com", "thehindu.com", "nature.com", "britannica.com", "isro.gov.in"]

        for s_idx in range(1, 41):
            sections.append(f"\n== अनुभाग {s_idx}: ऐतिहासिक एवं सामाजिक परिप्रेक्ष्य ==\n")
            for p_idx in range(1, 5):
                text_chunk = (
                    f"अनुभाग {s_idx} का अनुच्छेद {p_idx}। भारतीय संस्कृति, साहित्य, भाषा, "
                    f"दर्शन और विज्ञान का विस्तृत इतिहास रहा है। यह अनुच्छेद विविध ऐतिहासिक "
                    f"प्रमाणों और समकालीन शोध-पत्रों पर आधारित है।"
                )

                choice = (s_idx + p_idx) % 4
                if choice == 0:
                    total_refs += 1
                    bare_url_count += 1
                    dom = sample_domains[(s_idx + p_idx) % len(sample_domains)]
                    text_chunk += f"<ref>https://{dom}/section{s_idx}/article{p_idx}.html</ref>"
                elif choice == 1:
                    total_refs += 1
                    text_chunk += (
                        f'<ref name="सन्दर्भ_{s_idx}_{p_idx}">'
                        f'{{{{Cite web |url=https://example.org/doc{s_idx} '
                        f'|title=अनुसंधान पत्र {s_idx} |date=2024-01-15 |access-date=2026-11-21}}}}'
                        f'</ref>'
                    )
                elif choice == 2:
                    text_chunk += (
                        f"<!-- <ref>https://ignored.com/fake{s_idx}_{p_idx}</ref> -->"
                    )
                elif choice == 3:
                    text_chunk += (
                        f"<nowiki><ref>https://nowiki.org/test{s_idx}_{p_idx}</ref></nowiki>"
                    )

                text_chunk += " यह वाक्य अनुच्छेद के समापन को दर्शाता है।\n"
                sections.append(text_chunk)

        sections.append("\n== सन्दर्भ ==\n{{सन्दर्भ सूची}}\n")
        full_wikitext = "".join(sections)
        return full_wikitext, total_refs, bare_url_count

    def test_huge_wikitext_parsing_and_invariance(
        self, huge_wikitext: Tuple[str, int, int]
    ) -> None:
        wikitext, expected_total_refs, expected_bare_refs = huge_wikitext
        size_kb = len(wikitext.encode("utf-8")) / 1024
        assert size_kb >= 100, f"Generated wikitext should be >= 100KB, got {size_kb:.2f}KB"

        # 1. Parse document
        start_time = datetime.datetime.now()
        doc = parse_wikitext_references(wikitext)
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        assert elapsed < 5.0, f"Parsing took too long: {elapsed:.2f}s"

        assert doc.total_refs == expected_total_refs
        assert len(doc.bare_url_refs) == expected_bare_refs
        assert len(doc.modifications) == expected_bare_refs

        # Verify excluded zones were respected: 0 ignored.com or nowiki.org refs parsed
        for r in doc.references:
            assert "ignored.com" not in r.raw_tag
            assert "nowiki.org" not in r.raw_tag

        # 2. Descending-span reconstruction with ALL modifications
        reconstructed_all = apply_granular_modifications(wikitext, doc.modifications)
        assert len(reconstructed_all) > len(wikitext)

        # 3. Descending-span reconstruction with 50% randomly selected modifications
        random.seed(42)
        sample_size = len(doc.modifications) // 2
        selected_subset = sorted(
            random.sample(doc.modifications, sample_size),
            key=lambda m: m.span_start
        )
        unselected_subset = [m for m in doc.modifications if m not in selected_subset]

        reconstructed_partial = apply_granular_modifications(wikitext, selected_subset)

        # Invariance Verification:
        for unselected_mod in unselected_subset:
            assert unselected_mod.original_snippet in reconstructed_partial

        for selected_mod in selected_subset:
            assert selected_mod.proposed_snippet in reconstructed_partial
            assert selected_mod.original_snippet not in reconstructed_partial


# ==============================================================================
# 3. Boundary & Contiguity Torture
# ==============================================================================
class TestBoundaryAndContiguityTorture:
    """Stress tests on touching spans, 1-char separators, and document limits."""

    def test_zero_gap_touching_bare_urls(self) -> None:
        """Adjacent <ref> tags with 0 gap between them (span_end == next span_start)."""
        wikitext = (
            "पाठ<ref>https://domain1.org</ref>"
            "<ref>https://domain2.org</ref>"
            "<ref>https://domain3.org</ref>अन्त।"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 3
        assert len(doc.modifications) == 3

        # Spans must touch exactly
        assert doc.modifications[0].span_end == doc.modifications[1].span_start
        assert doc.modifications[1].span_end == doc.modifications[2].span_start

        # Reconstruct all 3
        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert reconstructed.startswith("पाठ<ref>{{Cite web")
        assert reconstructed.endswith("}}</ref>अन्त।")
        assert "domain1.org" in reconstructed
        assert "domain2.org" in reconstructed
        assert "domain3.org" in reconstructed

        # Selective reconstruction: accept only middle tag [1]
        reconstructed_mid = apply_granular_modifications(wikitext, [doc.modifications[1]])
        expected = (
            f"पाठ<ref>https://domain1.org</ref>"
            f"{doc.modifications[1].proposed_snippet}"
            f"<ref>https://domain3.org</ref>अन्त।"
        )
        assert reconstructed_mid == expected

    def test_one_char_separators_preserved_byte_for_byte(self) -> None:
        """Single character separators (space, newline, Devanagari danda, comma)."""
        separators = [" ", "\n", "।", ",", "\t", "-"]
        for sep in separators:
            wikitext = (
                f"पूर्व<ref>https://domainA.com</ref>{sep}"
                f"<ref>https://domainB.com</ref>उत्तर"
            )
            doc = parse_wikitext_references(wikitext)
            assert len(doc.modifications) == 2

            reconstructed = apply_granular_modifications(wikitext, doc.modifications)
            expected = (
                f"पूर्व{doc.modifications[0].proposed_snippet}{sep}"
                f"{doc.modifications[1].proposed_snippet}उत्तर"
            )
            assert reconstructed == expected

    def test_ref_at_absolute_start_and_absolute_end_of_document(self) -> None:
        """Ref starting at index 0 and another ending at the very last character."""
        wikitext = "<ref>https://start.com</ref>मध्य पाठ<ref>https://end.com</ref>"
        doc = parse_wikitext_references(wikitext)
        assert len(doc.modifications) == 2
        assert doc.modifications[0].span_start == 0
        assert doc.modifications[1].span_end == len(wikitext)

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert reconstructed.startswith(doc.modifications[0].proposed_snippet)
        assert reconstructed.endswith(doc.modifications[1].proposed_snippet)
        assert "मध्य पाठ" in reconstructed

    def test_empty_wikitext_and_whitespace_only(self) -> None:
        """Empty or whitespace wikitext returns clean ParsedDocument with 0 modifications."""
        for ws in ["", "   ", "\n\n\t  \n"]:
            doc = parse_wikitext_references(ws)
            assert doc.total_refs == 0
            assert len(doc.modifications) == 0
            assert apply_granular_modifications(ws, []) == ws

    def test_ref_nested_inside_complex_infobox_template_parameter(self) -> None:
        """Bare URL ref inside infobox parameter replaced without breaking template."""
        wikitext = (
            "{{ज्ञानसन्दूक व्यक्ति\n"
            "|नाम = विक्रम प्रताप\n"
            "|जन्म = 1990\n"
            "|उपाधि = मुख्य विकासकर्ता<ref>https://example.org/bio</ref>\n"
            "|नागरिकता = भारतीय\n"
            "}}"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert doc.references[0].is_bare_url is True

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert (
            "|उपाधि = मुख्य विकासकर्ता<ref>{{Cite web"
            in reconstructed
        )
        assert "|नागरिकता = भारतीय\n}}" in reconstructed

    def test_ref_nested_inside_wikitable_cell(self) -> None:
        """Bare URL ref inside a table cell replaced without disrupting table syntax."""
        wikitext = (
            '{| class="wikitable"\n'
            "|+ ऐतिहासिक आंकड़े\n"
            "! वर्ष !! उपलब्धि !! स्रोत\n"
            "|-\n"
            "| 2026 || सफल प्रक्षेपण || <ref>https://isro.gov.in/mission</ref>\n"
            "|}"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert doc.references[0].is_bare_url is True

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert (
            "| 2026 || सफल प्रक्षेपण || <ref>{{Cite web"
            in reconstructed
        )
        assert "|}" in reconstructed


# ==============================================================================
# 4. Devanagari Unicode, Conjuncts, ZWJ/ZWNJ & Attribute Torture
# ==============================================================================
class TestDevanagariUnicodeAndAttributeTorture:
    """Stress tests on Devanagari script attributes, zero-width chars, and Unicode."""

    def test_complex_devanagari_conjuncts_and_nukta_in_ref_name(self) -> None:
        """Ref names with nukta, conjuncts, and virama."""
        ref_name = "सन्दर्भ_क़ाफ़िया_क्षत्रिय_श्रद्धा_२०२६"
        group_name = "विशिष्ट_टिप्पणी"
        wikitext = (
            f'<ref name="{ref_name}" group="{group_name}">'
            f'https://unicode-hindi.org/test</ref>'
        )

        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.name == ref_name
        assert ref.group == group_name
        assert ref.is_bare_url is True

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert f'name="{ref_name}"' in reconstructed
        assert f'group="{group_name}"' in reconstructed
        assert "<ref " in reconstructed
        assert "{{Cite web" in reconstructed

    def test_zero_width_joiner_and_non_joiner_resilience(self) -> None:
        """Devanagari text containing ZWJ (U+200D) and ZWNJ (U+200C)."""
        zwj_text = "क्\u200Dय और क्\u200Cत"
        wikitext = f'<ref name="zwj_परीक्षण">{zwj_text} <ref>https://example.com/zwj</ref></ref>'
        doc = parse_wikitext_references(wikitext)
        assert doc.total_refs >= 1

    def test_dangling_unclosed_ref_with_devanagari_content(self) -> None:
        """Dangling unclosed ref with rich Devanagari text before a heading."""
        wikitext = (
            "प्रस्तावना।\n"
            "<ref name=\"देवनागरी\">यह एक असमाप्त संदर्भ है जिसमें पूर्ण विराम और कोष्ठक हैं\n"
            "== आगामी अनुभाग =="
        )
        repaired, mods = repair_syntax_and_modernize(wikitext)
        assert len(mods) == 1
        assert mods[0].type == "syntax_repair"
        assert "</ref>" in mods[0].proposed_snippet
        expected_start = (
            "प्रस्तावना।\n<ref name=\"देवनागरी\">"
            "यह एक असमाप्त संदर्भ है जिसमें पूर्ण विराम और कोष्ठक हैं</ref>\n=="
        )
        assert repaired.startswith(expected_start)


# ==============================================================================
# 5. Excluded Zone (Nowiki & HTML Comments) Collisions & Interleaving
# ==============================================================================
class TestExcludedZoneCollisionsAndInterleaving:
    """Stress tests on comments, nowiki tags, and deceptive matching patterns."""

    def test_identical_bare_url_inside_comment_and_outside(self) -> None:
        """Identical URL inside comment ignored; identical URL outside converted."""
        url = "https://example.com/identical-target"
        wikitext = (
            f"<!-- <ref>{url}</ref> -->\n"
            f"सक्रिय पाठ।<ref>{url}</ref>\n"
            f"<!-- पुनः टिप्पणी: <ref>{url}</ref> -->"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert len(doc.modifications) == 1

        mod = doc.modifications[0]
        reconstructed = apply_granular_modifications(wikitext, [mod])

        assert f"<!-- <ref>{url}</ref> -->" in reconstructed
        assert f"<!-- पुनः टिप्पणी: <ref>{url}</ref> -->" in reconstructed
        assert f"सक्रिय पाठ।{mod.proposed_snippet}" in reconstructed

    def test_nowiki_and_comments_interleaving_adjacent_to_ref(self) -> None:
        """Nowiki and comment blocks positioned directly adjacent to active refs."""
        wikitext = (
            "<nowiki><ref>https://fake1.com</ref></nowiki>"
            "<ref>https://real.com</ref>"
            "<!-- <ref>https://fake2.com</ref> -->"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert doc.references[0].bare_url == "https://real.com"
        assert len(doc.modifications) == 1

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert reconstructed.startswith("<nowiki><ref>https://fake1.com</ref></nowiki>")
        assert reconstructed.endswith("<!-- <ref>https://fake2.com</ref> -->")
        assert doc.modifications[0].proposed_snippet in reconstructed


# ==============================================================================
# 6. Syntax Repair & CS1 Modernization Complex Edge Cases
# ==============================================================================
class TestSyntaxRepairAndCS1EdgeCases:
    """Stress tests on pipe repair, bracket balancing, and CS1 modernization interactions."""

    def test_template_stray_pipes_with_wikitable_preservation(self) -> None:
        """Template containing || inside a wikitable that also uses || for cell separation."""
        wikitext = (
            '{| class="wikitable"\n'
            "|-\n"
            "| डेटा 1 || {{Cite web |url=https://example.com ||title=परीक्षण |}} || डेटा 3\n"
            "|}"
        )
        repaired, mods = repair_syntax_and_modernize(wikitext)
        assert repaired.startswith('{| class="wikitable"\n|-\n| डेटा 1 ||')
        assert "|| डेटा 3\n|}" in repaired
        assert "{{Cite web |url=https://example.com |title=परीक्षण}}" in repaired

    def test_cs1_modernization_redundant_deadurl_when_url_status_already_present(self) -> None:
        """When template has both deadurl=yes and url-status=dead, remove redundant deadurl."""
        wikitext = (
            "<ref>{{Cite web |url=https://example.com |title=परीक्षण "
            "|deadurl=yes |url-status=dead |accessdate=2024-05-01}}</ref>"
        )
        repaired, mods = repair_syntax_and_modernize(wikitext)
        assert "deadurl" not in repaired
        assert "url-status=dead" in repaired
        assert "access-date=2024-05-01" in repaired

    def test_cs1_all_deadurl_truthy_and_falsy_variants(self) -> None:
        """Verify deadurl values: yes, y, true, 1 -> dead; no, n, false, 0 -> live."""
        truthy_cases = ["yes", "y", "true", "1"]
        falsy_cases = ["no", "n", "false", "0"]

        for val in truthy_cases:
            wikitext = f"{{{{Cite web |url=https://x.com |title=T |deadurl={val}}}}}"
            repaired, _ = repair_syntax_and_modernize(wikitext)
            assert f"url-status={val[:0]}dead" in repaired
            assert "deadurl" not in repaired

        for val in falsy_cases:
            wikitext = f"{{{{Cite web |url=https://x.com |title=T |deadurl={val}}}}}"
            repaired, _ = repair_syntax_and_modernize(wikitext)
            assert "url-status=live" in repaired
            assert "deadurl" not in repaired

        special_cases = ["unfit", "usurped", "bot: unknown"]
        for val in special_cases:
            wikitext = f"{{{{Cite web |url=https://x.com |title=T |deadurl={val}}}}}"
            repaired, _ = repair_syntax_and_modernize(wikitext)
            assert f"url-status={val}" in repaired
            assert "deadurl" not in repaired


# ==============================================================================
# 7. Reconstructor Invariant & Corrupted Input Rejection
# ==============================================================================
class TestReconstructorInvariantAndCorruptedInputs:
    """Adversarial attempts to violate descending span reconstructor invariants."""

    def test_overlapping_spans_rejected_with_value_error(self) -> None:
        """Overlapping spans [10:30] and [25:40] must raise ValueError."""
        wikitext = "01234567890123456789012345678901234567890123456789"
        mod1 = Modification(
            id="m1", type="t", span_start=10, span_end=30,
            original_snippet="a", proposed_snippet="b"
        )
        mod2 = Modification(
            id="m2", type="t", span_start=25, span_end=40,
            original_snippet="c", proposed_snippet="d"
        )
        with pytest.raises(ValueError, match="Overlapping modification spans"):
            apply_granular_modifications(wikitext, [mod1, mod2])

    def test_out_of_bounds_and_inverted_spans_raise_index_error(self) -> None:
        """Spans extending beyond text length or with negative starts must raise IndexError."""
        wikitext = "लघु पाठ"
        mod_oob = Modification(
            id="m1", type="t", span_start=2, span_end=50,
            original_snippet="x", proposed_snippet="y"
        )
        with pytest.raises(IndexError, match="Invalid modification span"):
            apply_granular_modifications(wikitext, [mod_oob])

        mod_neg = Modification(
            id="m2", type="t", span_start=-2, span_end=4,
            original_snippet="x", proposed_snippet="y"
        )
        with pytest.raises(IndexError, match="Invalid modification span"):
            apply_granular_modifications(wikitext, [mod_neg])

        mod_inv = Modification(
            id="m3", type="t", span_start=5, span_end=2,
            original_snippet="x", proposed_snippet="y"
        )
        with pytest.raises(IndexError, match="Invalid modification span"):
            apply_granular_modifications(wikitext, [mod_inv])

    def test_original_snippet_mismatch_raises_value_error(self) -> None:
        """Mismatched expected snippet at specified span must raise ValueError."""
        wikitext = "मूल पाठ यहाँ है।"
        mod = Modification(
            id="m1",
            type="test",
            span_start=0,
            span_end=8,
            original_snippet="गलत पाठ",
            proposed_snippet="नया पाठ",
        )
        with pytest.raises(ValueError, match="Integrity check failed at span"):
            apply_granular_modifications(wikitext, [mod])
