"""
Milestone 3 Adversarial Challenge & Stress Test Suite.
Adversarial Verification Dimensions:
1. Deeply nested templates inside <ref>...</ref> (multi-level recursion, CS1 + lang-hi).
2. Commented-out and nowiki refs colliding with active refs (same names, identical text).
3. Complex Unicode, Devanagari characters, Hindi numerals (०-९), and unclosed quotes.
4. Malformed XML <ref> tag variations (<ref/ name="x">, <ref name=foo >, dangling refs).
5. Adjacent & multi-span replacements: 100% byte invariance of unselected text/comments.
6. Wikitables with double pipes ({| ... || ... |}) through clean_stray_pipes.
7. Simultaneous multi-category syntax repairs and CS1 modernization stress testing.

Authoritative Reference: ORIGINAL_REQUEST.md (§3, R1, R2), PROJECT.md (§Feature 8, Feature 11).
"""

from __future__ import annotations

import itertools
from typing import List

import pytest

from app.services.parser import (
    Modification,
    ParsedDocument,
    apply_granular_modifications,
    parse_wikitext_references,
)
from app.services.syntax_repair import (
    SyntaxRepairEngine,
    repair_syntax_and_modernize,
)


# ==============================================================================
# 1. Deeply Nested Templates Inside <ref>
# ==============================================================================
class TestDeeplyNestedTemplatesInRefs:
    """Empirical challenge on multi-level nested templates inside <ref> tags."""

    def test_multi_level_nested_templates_inside_ref(self) -> None:
        """Deeply nested templates are parsed without breaking AST or tagging as bare URL."""
        wikitext = (
            "भारत का इतिहास।<ref>{{cite web "
            "|title={{lang-hi|{{transliteration|hi|{{ISO|भारत}}}}}} "
            "|url=https://example.com/india "
            "|author={{cite author |first=विक्रम |last=प्रताप "
            "|org={{institution |name=इतिहास परिषद}}}} "
            "|date=2026-11-21}}</ref> आगे का विवरण।"
        )
        doc: ParsedDocument = parse_wikitext_references(wikitext)

        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.is_bare_url is False
        assert ref.bare_url is None

        # Check template extraction across all nested levels
        template_names = [t.name for t in ref.templates]
        assert "cite web" in template_names
        assert "lang-hi" in template_names
        assert "transliteration" in template_names
        assert "iso" in template_names
        assert "cite author" in template_names
        assert "institution" in template_names

        # Outer cite web template parameters must retain nested template code intact
        cite_web = next(t for t in ref.templates if t.name == "cite web")
        assert "url" in cite_web.params
        assert cite_web.params["url"] == "https://example.com/india"
        assert "{{lang-hi|" in cite_web.params["title"]

        # Span boundaries must match the exact <ref>...</ref> slice
        assert ref.span_start != -1
        assert ref.span_end != -1
        assert wikitext[ref.span_start:ref.span_end] == (
            "<ref>{{cite web "
            "|title={{lang-hi|{{transliteration|hi|{{ISO|भारत}}}}}} "
            "|url=https://example.com/india "
            "|author={{cite author |first=विक्रम |last=प्रताप "
            "|org={{institution |name=इतिहास परिषद}}}} "
            "|date=2026-11-21}}</ref>"
        )

    def test_multiple_refs_with_nested_templates(self) -> None:
        """Multiple <ref> tags with distinct nested templates retain independent spans."""
        wikitext = (
            "पहला भाग।<ref name=\"r1\">"
            "{{Cite news |title={{lang-hi|दैनिक}} |url=https://example.com/1}}</ref>\n"
            "दूसरा भाग।<ref name=\"r2\">"
            "{{Cite web |title={{IPA|hi|/bʱaːrət̪/}} |url=https://example.com/2}}</ref>"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 2
        assert doc.references[0].name == "r1"
        assert doc.references[1].name == "r2"
        assert doc.references[0].span_end <= doc.references[1].span_start
        assert not doc.references[0].is_bare_url
        assert not doc.references[1].is_bare_url


# ==============================================================================
# 2. Commented-out and Nowiki Ref Collisions
# ==============================================================================
class TestCommentedOutAndNowikiRefCollisions:
    """Stress tests for HTML comments and nowiki tags colliding with active references."""

    def test_commented_ref_preceding_active_ref_same_name(self) -> None:
        """Commented-out ref preceding active ref does not interfere with span offset."""
        wikitext = (
            "पाठ।<!-- <ref name=\"refX\">मृत कड़ी https://dead.com</ref> --> "
            "सक्रिय पाठ।<ref name=\"refX\">https://timesofindia.indiatimes.com/active</ref>"
        )
        doc = parse_wikitext_references(wikitext)

        # Only the active reference outside the comment should be extracted
        assert len(doc.references) == 1
        active_ref = doc.references[0]
        assert active_ref.name == "refX"
        assert active_ref.is_bare_url is True
        assert active_ref.bare_url == "https://timesofindia.indiatimes.com/active"

        # Verify exact slice matches active tag
        assert wikitext[active_ref.span_start:active_ref.span_end] == (
            "<ref name=\"refX\">https://timesofindia.indiatimes.com/active</ref>"
        )
        # Excluded comment must not be flagged as a name conflict
        assert len(doc.duplicate_name_conflicts) == 0

    def test_active_ref_preceding_commented_ref_same_name(self) -> None:
        """Active ref followed by commented-out ref with identical name and content."""
        target = "<ref name=\"refY\">https://timesofindia.indiatimes.com/1</ref>"
        wikitext = f"सक्रिय।{target} <!-- {target} -->"
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert doc.references[0].span_start == wikitext.find(target)
        assert len(doc.duplicate_name_conflicts) == 0

    def test_identical_text_inside_comment_and_outside(self) -> None:
        """Comment contains identical raw wikitext; cursor algorithm selects active ref."""
        target_tag = "<ref name=\"samachar\">https://timesofindia.indiatimes.com/news</ref>"
        wikitext = f"आरंभ। <!-- {target_tag} --> मध्य। {target_tag} अंत।"
        doc = parse_wikitext_references(wikitext)

        assert len(doc.references) == 1
        active_ref = doc.references[0]
        # Must match the second occurrence (outside the comment)
        expected_start = wikitext.rfind(target_tag)
        assert active_ref.span_start == expected_start
        assert active_ref.span_end == expected_start + len(target_tag)

    def test_nowiki_and_comment_interleaved_with_multiple_active_refs(self) -> None:
        """Interleaved nowiki, comments, and multiple active refs with identical names."""
        wikitext = (
            "<!-- <ref name=\"c\">टिप्पणी १</ref> -->\n"
            "<ref name=\"c\">सक्रिय १</ref>\n"
            "<nowiki><ref name=\"c\">नोविकी</ref></nowiki>\n"
            "<ref name=\"c\">सक्रिय २</ref>"
        )
        doc = parse_wikitext_references(wikitext)

        assert len(doc.references) == 2
        assert [r.inner_content.strip() for r in doc.references] == ["सक्रिय १", "सक्रिय २"]
        for r in doc.references:
            assert wikitext[r.span_start:r.span_end] == r.raw_tag

        # Since active 1 and active 2 differ, duplicate conflict is detected on active refs only
        assert len(doc.duplicate_name_conflicts) == 1
        conflict = doc.duplicate_name_conflicts[0]
        assert conflict["name"] == "c"
        assert set(conflict["conflicting_bodies"]) == {"सक्रिय १", "सक्रिय २"}

    def test_self_closing_ref_next_to_commented_ref(self) -> None:
        """Self-closing ref <ref name="x" /> next to commented full ref does not cause conflict."""
        wikitext = "<!-- <ref name=\"alpha\">पूर्ण सामग्री</ref> --> पाठ।<ref name=\"alpha\" />"
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        assert doc.references[0].self_closing is True
        assert len(doc.duplicate_name_conflicts) == 0


# ==============================================================================
# 3. Complex Unicode, Devanagari Characters, and Numerals
# ==============================================================================
class TestUnicodeDevanagariAndNumeralsAdversarial:
    """Stress tests with Hindi Devanagari text, numerals (०-९), and special punctuation."""

    def test_devanagari_numerals_in_ref_name_and_content(self) -> None:
        """Devanagari numerals and Hindi script in ref name and contents."""
        wikitext = (
            "जनसंख्या।<ref name=\"जनगणना_२०२१_तालिका_५\">"
            "https://timesofindia.indiatimes.com/census-2021"
            "</ref>"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.name == "जनगणना_२०२१_तालिका_५"
        assert ref.is_bare_url is True

        # Check proposed bare URL modification preserves Devanagari attribute byte-for-byte
        assert len(doc.modifications) == 1
        mod = doc.modifications[0]
        assert 'name="जनगणना_२०२१_तालिका_५"' in mod.proposed_snippet
        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        assert 'name="जनगणना_२०२१_तालिका_५"' in reconstructed
        assert "https://timesofindia.indiatimes.com/census-2021" in reconstructed

    def test_devanagari_group_attribute(self) -> None:
        """Devanagari group attribute like group="टिप्पणी" is preserved."""
        wikitext = (
            'पाठ।<ref name="टी१" group="टिप्पणी">https://timesofindia.indiatimes.com/note</ref>'
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.name == "टी१"
        assert ref.group == "टिप्पणी"
        assert len(doc.modifications) == 1
        assert 'group="टिप्पणी"' in doc.modifications[0].proposed_snippet

    def test_hindi_punctuation_and_zero_width_chars(self) -> None:
        """Preserves zero-width joiners (ZWJ), non-joiners (ZWNJ), and Hindi purna viram (।)."""
        wikitext = (
            "प्रस्तावना।\u200D\u200C "
            "<ref name=\"संस्कृति\">[https://example.com/culture भारतीय संस्कृति एवं धरोहर]</ref> "
            "उपसंहार॥"
        )
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 1
        ref = doc.references[0]
        assert ref.fallback_title == "भारतीय संस्कृति एवं धरोहर"

        reconstructed = apply_granular_modifications(wikitext, doc.modifications)
        # Surrounding Hindi text, ZWJ/ZWNJ, and purna viram must be 100% byte-for-byte untouched
        assert reconstructed.startswith("प्रस्तावना।\u200D\u200C ")
        assert reconstructed.endswith(" उपसंहार॥")
        assert "title=भारतीय संस्कृति एवं धरोहर" in reconstructed


# ==============================================================================
# 4. Malformed XML <ref> Tag Variations
# ==============================================================================
class TestMalformedTagVariations:
    """Stress tests on malformed <ref> XML tag normalization."""

    def test_misplaced_slash_variations(self) -> None:
        """Tests misplaced slash syntax: <ref/ name="foo">, <ref / name="foo">, <ref/ name=foo>."""
        cases = [
            ('<ref/ name="source1">', '<ref name="source1" />'),
            ('<ref / name="source2">', '<ref name="source2" />'),
            ('<ref/ name=source3>', '<ref name="source3" />'),
            ('<ref/ name="दैनिक_भास्कर_१">', '<ref name="दैनिक_भास्कर_१" />'),
            ('<ref / name="दैनिक_भास्कर_२">', '<ref name="दैनिक_भास्कर_२" />'),
            ('<ref/ name=दैनिक_भास्कर_३>', '<ref name="दैनिक_भास्कर_३" />'),
        ]
        for malformed, expected in cases:
            text = f"पाठ।{malformed} आगे पाठ।"
            repaired, mods = repair_syntax_and_modernize(text)
            assert expected in repaired, f"Failed for {malformed}"
            assert len(mods) == 1
            assert mods[0].proposed_snippet == expected

    def test_unquoted_attribute_variations(self) -> None:
        """Tests unquoted name attributes: <ref name=foo>, <ref name=foo >, <ref name=foo/>."""
        cases = [
            ('<ref name=jagran>', '<ref name="jagran">'),
            ('<ref name=jagran >', '<ref name="jagran" >'),
            ('<ref name=jagran/>', '<ref name="jagran"/>'),
            ('<ref name=भास्कर२०२६>', '<ref name="भास्कर२०२६">'),
            ('<ref name=भास्कर२०२६ >', '<ref name="भास्कर२०२६" >'),
        ]
        for malformed, expected in cases:
            text = f"तथ्य।{malformed}कंटेंट</ref>"
            repaired, mods = repair_syntax_and_modernize(text)
            assert expected in repaired, f"Failed for {malformed}"

    def test_slash_whitespace_variations(self) -> None:
        """Tests whitespace before closing slash: <ref name="x"/ >, <ref name="x" / >."""
        cases = [
            ('<ref name="foo"/ >', '<ref name="foo" />'),
            ('<ref name="foo" / >', '<ref name="foo" />'),
            ('<ref name="दैनिक"/ >', '<ref name="दैनिक" />'),
        ]
        for malformed, expected in cases:
            text = f"विवरण।{malformed} अगला।"
            repaired, mods = repair_syntax_and_modernize(text)
            assert expected in repaired

    def test_dangling_unclosed_ref_before_section(self) -> None:
        """Unclosed <ref> before section header == ... == is closed with </ref>."""
        text = "मुख्य जानकारी।<ref>{{Cite web |url=https://example.com}}\n== सन्दर्भ =="
        repaired, mods = repair_syntax_and_modernize(text)
        assert "</ref>\n== सन्दर्भ ==" in repaired
        assert len(mods) == 1
        assert mods[0].proposed_snippet == "<ref>{{Cite web |url=https://example.com}}</ref>"

    def test_unclosed_quote_resilience(self) -> None:
        """Tags with unclosed quotes like <ref name="unclosed> do not crash parser or engine."""
        text = 'प्रमाण।<ref name="unclosed>https://timesofindia.indiatimes.com/a</ref> आगे पाठ।'
        doc = parse_wikitext_references(text)
        assert len(doc.references) == 1
        assert doc.references[0].is_bare_url is True
        # Reconstruct should succeed without exception
        reconstructed = apply_granular_modifications(text, doc.modifications)
        assert "प्रमाण।" in reconstructed
        assert "आगे पाठ।" in reconstructed


# ==============================================================================
# 5. Adjacent and Multi-Span Replacements (100% Invariance)
# ==============================================================================
class TestAdjacentAndMultiSpanByteInvariance:
    """Verifies unselected text, surrounding whitespace, and comments are 100% untouched."""

    def test_strictly_adjacent_spans(self) -> None:
        """Adjacent replacement spans [0:10] and [10:20] splice cleanly without gaps."""
        original = "[FIRST_BLOCK][SECOND_BLOCK] trailing text."
        s1 = 0
        e1 = len("[FIRST_BLOCK]")
        s2 = e1
        e2 = s2 + len("[SECOND_BLOCK]")

        mods = [
            Modification(
                id="m1",
                type="test",
                span_start=s1,
                span_end=e1,
                original_snippet="[FIRST_BLOCK]",
                proposed_snippet="[NEW_FIRST_लम्बा_पाठ]",
            ),
            Modification(
                id="m2",
                type="test",
                span_start=s2,
                span_end=e2,
                original_snippet="[SECOND_BLOCK]",
                proposed_snippet="[NEW_SECOND]",
            ),
        ]

        result = apply_granular_modifications(original, mods)
        assert result == "[NEW_FIRST_लम्बा_पाठ][NEW_SECOND] trailing text."

    def test_powerset_invariance_across_all_selections(self) -> None:
        """Empirically tests powerset of modifications (2^4 = 16 combinations) on wikitext."""
        raw_text = (
            "== प्रस्तावना ==\n"
            "लेख का पहला पैरा।\tविशेष वर्ण: \u0950 ॐ (१२३)\n"
            "<!-- गुप्त टिप्पणी १: <ref>अनदेखा</ref> -->\n"
            "पहला सन्दर्भ <ref>https://timesofindia.indiatimes.com/1</ref> है।\n\n"
            "दूसरा सन्दर्भ <ref>https://timesofindia.indiatimes.com/2</ref> है।\n"
            "<!-- गुप्त टिप्पणी २ -->\n"
            "तीसरा सन्दर्भ <ref>https://timesofindia.indiatimes.com/3</ref> है।\n"
            "चौथा सन्दर्भ <ref>https://timesofindia.indiatimes.com/4</ref> है।\n"
            "== सन्दर्भ ==\n{{reflist}}"
        )

        doc = parse_wikitext_references(raw_text)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 4

        # Test all 16 combinations of 4 modifications
        for k in range(5):
            for subset in itertools.combinations(bare_mods, k):
                reconstructed = apply_granular_modifications(raw_text, list(subset))

                # Build exact expected string for this selection
                sorted_sub = sorted(subset, key=lambda m: m.span_start)
                expected_chunks: List[str] = []
                last_idx = 0
                for m in sorted_sub:
                    expected_chunks.append(raw_text[last_idx:m.span_start])
                    expected_chunks.append(m.proposed_snippet)
                    last_idx = m.span_end
                expected_chunks.append(raw_text[last_idx:])
                expected_str = "".join(expected_chunks)

                # Invariance assertion
                assert reconstructed == expected_str
                # Comments must remain 100% byte-for-byte identical
                assert "<!-- गुप्त टिप्पणी १: <ref>अनदेखा</ref> -->" in reconstructed
                assert "<!-- गुप्त टिप्पणी २ -->" in reconstructed
                # Whitespace, tabs, and headers must remain identical
                assert "== प्रस्तावना ==\n" in reconstructed
                assert "== सन्दर्भ ==\n{{reflist}}" in reconstructed
                assert "लेख का पहला पैरा।\tविशेष वर्ण: \u0950 ॐ (१२३)\n" in reconstructed

    def test_overlapping_spans_strictly_raise_value_error(self) -> None:
        """Overlapping modification spans must be strictly rejected."""
        text = "012345678901234567890"
        mods = [
            Modification(
                id="m1", type="t", span_start=5, span_end=15,
                original_snippet=text[5:15], proposed_snippet="A"
            ),
            Modification(
                id="m2", type="t", span_start=10, span_end=20,
                original_snippet=text[10:20], proposed_snippet="B"
            ),
        ]
        with pytest.raises(ValueError, match="Overlapping modification spans detected"):
            apply_granular_modifications(text, mods)

    def test_original_snippet_mismatch_strictly_raises_value_error(self) -> None:
        """Tampered or desynchronized original_snippet raises ValueError to prevent corruption."""
        text = "प्रारंभिक मूल पाठ।"
        mods = [
            Modification(
                id="m1", type="t", span_start=0, span_end=9,
                original_snippet="गलत स्निपेट", proposed_snippet="नया पाठ"
            )
        ]
        with pytest.raises(ValueError, match="Integrity check failed at span"):
            apply_granular_modifications(text, mods)


# ==============================================================================
# 6. Wikitable Immunity Under clean_stray_pipes
# ==============================================================================
class TestWikitableImmunityUnderPipes:
    """Stress tests proving wikitables ({| ... || ... |}) are never corrupted."""

    def test_standard_wikitable_with_double_pipes_untouched(self) -> None:
        """Standard wikitable containing double pipes || is completely untouched."""
        table = (
            '{| class="wikitable sortable"\n'
            "|+ प्रमुख भारतीय शहर एवं जनसंख्या\n"
            "! क्रम !! शहर !! राज्य !! जनसंख्या (२०११)\n"
            "|-\n"
            "| १ || मुम्बई || महाराष्ट्र || १,२४,४२,३७३\n"
            "|-\n"
            "| २ || दिल्ली || दिल्ली || १,१०,०७,८३५\n"
            "|-\n"
            "| ३ || बंगलुरु || कर्नाटक || ८४,४३,६७५\n"
            "|}"
        )
        _, mods = SyntaxRepairEngine.clean_stray_pipes(table)
        assert len(mods) == 0

        repaired, all_mods = repair_syntax_and_modernize(table)
        assert len(all_mods) == 0
        assert repaired == table

    def test_complex_wikitable_with_inline_attributes(self) -> None:
        """Wikitable with inline styles, colspans, rowspans, and cell styling."""
        table = (
            '{| style="background: #f9f9f9; border: 1px solid #aaa;"\n'
            '|- style="font-weight: bold;"\n'
            '! शीर्षक A !! शीर्षक B !! शीर्षक C\n'
            "|-\n"
            '| rowspan="2" | विस्तृत सेल || style="color: red;" | रंगीन || सामान्य\n'
            "|-\n"
            '| colspan="2" | संयुक्त सेल || अंतिम\n'
            "|}"
        )
        _, mods = SyntaxRepairEngine.clean_stray_pipes(table)
        assert len(mods) == 0
        repaired, _ = repair_syntax_and_modernize(table)
        assert repaired == table

    def test_wikitable_with_nested_table_untouched(self) -> None:
        """Nested wikitables inside a cell are completely preserved."""
        table = (
            '{| class="wikitable"\n'
            "| बाहरी सेल १ ||\n"
            '{| class="wikitable"\n'
            "| भीतरी १ || भीतरी २\n"
            "|}\n"
            "| बाहरी सेल २\n"
            "|}"
        )
        _, mods = SyntaxRepairEngine.clean_stray_pipes(table)
        assert len(mods) == 0
        repaired, _ = repair_syntax_and_modernize(table)
        assert repaired == table

    def test_wikitable_containing_template_with_stray_pipe(self) -> None:
        """Stray pipe in a template cell is repaired while the table pipes are untouched."""
        table = (
            '{| class="wikitable"\n'
            "|-\n"
            "| सेल १ || {{cite web |url=https://example.com ||title=परीक्षण |}} || सेल ३\n"
            "|}"
        )
        repaired, mods = repair_syntax_and_modernize(table)
        assert len(mods) == 1
        # Stray pipe in template repaired
        assert "{{cite web |url=https://example.com |title=परीक्षण}}" in repaired
        # Surrounding table pipes preserved
        assert "| सेल १ || " in repaired
        assert " || सेल ३\n|}" in repaired


# ==============================================================================
# 7. Simultaneous Multi-Category Syntax Repairs and CS1 Modernization
# ==============================================================================
class TestSimultaneousMultiCategoryRepairs:
    """Stress tests when multiple syntax issues and CS1 modernization occur simultaneously."""

    def test_simultaneous_pipes_and_cs1_modernization(self) -> None:
        """Template with stray pipe || and deadurl=yes modernizes both in single merged mod."""
        wikitext = (
            "विवरण। {{cite web |url=https://example.com ||deadurl=yes "
            "|accessdate=2026-11-20 |title=ताज़ा समाचार |}} अंतिम पाठ।"
        )
        repaired, mods = repair_syntax_and_modernize(wikitext)

        assert len(mods) == 1
        mod = mods[0]
        assert mod.original_snippet == (
            "{{cite web |url=https://example.com ||deadurl=yes "
            "|accessdate=2026-11-20 |title=ताज़ा समाचार |}}"
        )
        assert mod.proposed_snippet == (
            "{{cite web |url=https://example.com |url-status=dead "
            "|access-date=2026-11-20 |title=ताज़ा समाचार}}"
        )
        assert "विवरण। " in repaired
        assert " अंतिम पाठ।" in repaired

    def test_cs1_modernization_preserves_surrounding_newlines_and_comments(self) -> None:
        """Multi-line CS1 modernization preserves all parameter indentation and context."""
        wikitext = (
            "<!-- सन्दर्भ खंड प्रारंभ -->\n"
            "{{cite news\n"
            "  | url = https://example.com/news\n"
            "  | title = शीर्षक\n"
            "  | deadurl = yes\n"
            "  | accessdate = 2026-01-01\n"
            "  | language = hi\n"
            "}}\n"
            "<!-- सन्दर्भ खंड समाप्त -->"
        )
        repaired, mods = repair_syntax_and_modernize(wikitext)
        assert len(mods) == 1
        assert "url-status = dead" in repaired
        assert "access-date = 2026-01-01" in repaired
        assert "deadurl" not in repaired
        assert "accessdate" not in repaired
        assert "<!-- सन्दर्भ खंड प्रारंभ -->\n" in repaired
        assert "\n<!-- सन्दर्भ खंड समाप्त -->" in repaired
