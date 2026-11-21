"""
Milestone 3 Unit & Integration Test Suite.
Covers:
1. Bare URL detection & conversion to {{Cite web}} / {{Cite news}}
2. Wikimedia Citoid REST API integration with respx mocks (web, news, journal, book, 404, maxlag)
3. CS1 parameter modernization (deadurl -> url-status, accessdate -> access-date)
4. Syntax repair (bracket balancing, stray pipes, malformed XML ref tags)
5. Descending-span reconstruction invariance & selective application
Authoritative Source: ORIGINAL_REQUEST.md (§3, R1, R2, R5), PROJECT.md (§M3).
"""

from __future__ import annotations

import datetime
import httpx
import pytest
import respx

from app.services.citoid import (
    CitoidClient,
    CitoidRateLimiter,
    fetch_citation_metadata,
    format_cs1_template,
    select_cs1_template,
)
from app.services.parser import (
    Modification,
    apply_granular_modifications,
    parse_wikitext_references,
)
from app.services.syntax_repair import (
    SyntaxRepairEngine,
    repair_syntax_and_modernize,
)

CITOID_ENDPOINT = "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki"


# ==============================================================================
# Class 1: Bare URL Detection Tests
# ==============================================================================
class TestBareUrlDetection:

    def test_bare_url_standard_http(self) -> None:
        """Plain HTTP URL inside <ref> converted to {{Cite web}}."""
        wikitext = "समाचार।<ref>https://timesofindia.indiatimes.com/news</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 1
        assert "url=https://timesofindia.indiatimes.com/news" in bare_mods[0].proposed_snippet
        assert bare_mods[0].proposed_snippet.startswith("<ref>")
        assert bare_mods[0].proposed_snippet.endswith("</ref>")

    def test_bare_url_bracketed_no_title(self) -> None:
        """Bracketed URL without title [https://...] brackets stripped and converted."""
        wikitext = "विवरण।<ref>[https://timesofindia.indiatimes.com]</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 1
        assert "{{Cite web" in bare_mods[0].proposed_snippet
        assert "[" not in bare_mods[0].proposed_snippet.split("url=")[1].split(" ")[0]

    def test_bare_url_bracketed_with_hindi_title(self) -> None:
        """Bracketed URL with Hindi title preserves title in |title=."""
        wikitext = "विवरण।<ref>[https://example.com/page हिन्दी शीर्षक]</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 1
        assert "title=हिन्दी शीर्षक" in bare_mods[0].proposed_snippet
        assert "url=https://example.com/page" in bare_mods[0].proposed_snippet

    def test_bare_url_preserves_ref_attributes(self) -> None:
        """Attributes (name, group) on outer <ref> are strictly preserved."""
        wikitext = 'स्रोत।<ref name="source1" group="टिप्पणी">https://example.com</ref>'
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 1
        assert 'name="source1"' in bare_mods[0].proposed_snippet
        assert 'group="टिप्पणी"' in bare_mods[0].proposed_snippet

    def test_bare_url_devanagari_ref_name(self) -> None:
        """Devanagari ref names like <ref name="दैनिक_भास्कर_2023"> are preserved byte-for-byte."""
        wikitext = 'पाठ।<ref name="दैनिक_भास्कर_2023">https://example.com</ref>'
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 1
        assert 'name="दैनिक_भास्कर_2023"' in bare_mods[0].proposed_snippet

    def test_bare_url_multiple_occurrences(self) -> None:
        """Multiple bare URLs receive distinct IDs and non-overlapping span offsets."""
        wikitext = "<ref>https://example.com/1</ref> मध्य <ref>https://example.com/2</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 2
        assert bare_mods[0].span_start < bare_mods[0].span_end <= bare_mods[1].span_start

    def test_non_url_ref_is_not_flagged_as_bare_url(self) -> None:
        """Plain descriptive text inside <ref> without URLs is not flagged."""
        wikitext = "<ref>यह केवल एक व्याख्यात्मक टिप्पणी है बिना किसी यूआरएल के।</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 0

    def test_valid_template_ref_not_flagged(self) -> None:
        """Reference with existing {{Cite web}} is not flagged as bare URL."""
        wikitext = "<ref>{{Cite web |url=https://example.com |title=टेस्ट}}</ref>"
        doc = parse_wikitext_references(wikitext)
        bare_mods = [m for m in doc.modifications if m.type == "bare_url"]
        assert len(bare_mods) == 0

    def test_bare_url_inside_comment_excluded(self) -> None:
        """Reference tags inside HTML comments <!-- ... --> are excluded from active refs."""
        wikitext = "पाठ।<!-- <ref>https://ignored-comment.com</ref> --> आगे पाठ।"
        doc = parse_wikitext_references(wikitext)
        assert len(doc.references) == 0
        assert len(doc.modifications) == 0

    def test_ast_reflist_container_detection(self) -> None:
        """Detects reference list containers: {{reflist}}, {{सन्दर्भ सूची}}, and <references />."""
        text1 = "लेख। == संदर्भ ==\n{{reflist}}"
        doc1 = parse_wikitext_references(text1)
        assert doc1.has_reflist is True
        assert len(doc1.reflists) == 1

        text2 = "लेख। == संदर्भ ==\n{{सन्दर्भ सूची}}"
        doc2 = parse_wikitext_references(text2)
        assert doc2.has_reflist is True

        text3 = "लेख। == संदर्भ ==\n<references />"
        doc3 = parse_wikitext_references(text3)
        assert doc3.has_reflist is True

    def test_bare_url_generic_fallback_formatting_and_zero_hardcoding(self) -> None:
        """
        Confirms bare URLs use genuine dynamic domain extraction and today's date,
        with zero domain-specific hardcoded branches or canned titles.
        """
        today = datetime.date.today().isoformat()
        sample_urls = [
            ("https://www.jagran.com/sports/cricket-today.html", "jagran.com"),
            (
                "https://timesofindia.indiatimes.com/tech/ai-advances.html",
                "timesofindia.indiatimes.com",
            ),
            ("https://bbc.com/hindi/india", "bbc.com"),
            ("https://example.org/research/doc.pdf", "example.org"),
        ]
        for url, expected_domain in sample_urls:
            doc = parse_wikitext_references(f"विवरण।<ref>{url}</ref>")
            assert len(doc.modifications) == 1
            mod = doc.modifications[0]
            assert mod.type == "bare_url"
            snippet = mod.proposed_snippet
            # Must be Cite web
            assert "{{Cite web" in snippet
            # Must have clean domain title and website
            assert f"title={expected_domain} वेब सन्दर्भ" in snippet
            assert f"website={expected_domain}" in snippet
            assert f"access-date={today}" in snippet
            # Must NOT contain historical hardcoded strings
            assert "2026-11-19" not in snippet
            assert "2026-11-20" not in snippet
            assert "दैनिक जागरण: राष्ट्रीय समाचार" not in snippet
            assert "टाइम्स ऑफ इंडिया: ताज़ा ख़बरें" not in snippet


# ==============================================================================
# Class 2: Citoid REST API Mock Tests (respx)
# ==============================================================================
class TestCitoidRespxMocks:

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_webpage_zotero_mapping(self) -> None:
        """Citoid webpage response maps to {{Cite web}} with title, website, date."""
        target_url = "https://example.com/article"
        encoded = "https%3A%2F%2Fexample.com%2Farticle"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "webpage",
                "title": "परीक्षण पृष्ठ",
                "url": target_url,
                "websiteTitle": "उदाहरण",
                "date": "2026-11-20",
                "accessDate": "2026-11-21",
                "language": "hi",
            }],
        )
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_url)
        template_str = fast_client.format_cs1_template(meta)

        assert "{{Cite web" in template_str
        assert "title=परीक्षण पृष्ठ" in template_str
        assert "website=उदाहरण" in template_str
        assert f"url={target_url}" in template_str

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_newspaper_mapping_to_cite_news(self) -> None:
        """Citoid newspaperArticle maps to {{Cite news}} with |work= parameter."""
        target_url = "https://www.jagran.com/news/national-12345.html"
        encoded = "https%3A%2F%2Fwww.jagran.com%2Fnews%2Fnational-12345.html"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "newspaperArticle",
                "title": "दैनिक जागरण: राष्ट्रीय समाचार",
                "url": target_url,
                "publicationTitle": "दैनिक जागरण",
                "date": "2026-11-19",
                "accessDate": "2026-11-21",
            }],
        )
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_url)
        template_str = fast_client.format_cs1_template(meta)

        assert "{{Cite news" in template_str
        assert "work=दैनिक जागरण" in template_str

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_journal_article_mapping(self) -> None:
        """Citoid journalArticle maps to {{Cite journal}} with volume, issue, DOI."""
        target_doi = "https://doi.org/10.1038/nature12373"
        encoded = "https%3A%2F%2Fdoi.org%2F10.1038%2Fnature12373"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "journalArticle",
                "title": "Nanometre-scale thermometry",
                "publicationTitle": "Nature",
                "url": "https://www.nature.com/articles/nature12373",
                "volume": "500",
                "issue": "7460",
                "pages": "54–58",
                "DOI": "10.1038/nature12373",
                "ISSN": ["1476-4687"],
            }],
        )
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_doi)
        template_str = fast_client.format_cs1_template(meta)

        assert "{{Cite journal" in template_str
        assert "journal=Nature" in template_str
        assert "volume=500" in template_str
        assert "issue=7460" in template_str
        assert "doi=10.1038/nature12373" in template_str
        assert "issn=1476-4687" in template_str

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_book_by_isbn_mapping(self) -> None:
        """Citoid book item maps to {{Cite book}} with publisher, date, ISBN."""
        isbn = "9780131103627"
        respx.get(f"{CITOID_ENDPOINT}/{isbn}").respond(
            status_code=200,
            json=[{
                "itemType": "book",
                "title": "The C Programming Language",
                "publisher": "Prentice Hall",
                "edition": "2nd ed",
                "date": "1988",
                "ISBN": ["978-0-13-110362-7"],
                "author": [["Brian W.", "Kernighan"], ["Dennis M.", "Ritchie"]],
            }],
        )
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(isbn)
        template_str = fast_client.format_cs1_template(meta)

        assert "{{Cite book" in template_str
        assert "publisher=Prentice Hall" in template_str
        assert "isbn=978-0-13-110362-7" in template_str
        assert "last1=Kernighan" in template_str
        assert "first1=Brian W." in template_str
        assert "last2=Ritchie" in template_str
        assert "first2=Dennis M." in template_str

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_multi_author_formatting(self) -> None:
        """Formatted multi-author output maps first and last names correctly."""
        meta = {
            "item_type": "webpage",
            "url": "https://example.com",
            "title": "Author Test",
            "authors": [("विकास", "शर्मा"), ("अमित", "वर्मा")],
        }
        res = format_cs1_template(meta)
        assert "last1=शर्मा" in res
        assert "first1=विकास" in res
        assert "last2=वर्मा" in res
        assert "first2=अमित" in res

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_user_agent_header_compliance(self) -> None:
        """Outgoing Citoid requests must include WikiRefFixer User-Agent header."""
        target_url = "https://example.com/test-ua"
        encoded = "https%3A%2F%2Fexample.com%2Ftest-ua"
        route = respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{"itemType": "webpage", "title": "UA Test", "url": target_url}],
        )
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        await fast_client.fetch_citation_metadata(target_url)

        assert route.called
        sent_headers = route.calls.last.request.headers
        assert "User-Agent" in sent_headers
        assert "WikiRefFixer" in sent_headers["User-Agent"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_maxlag_backoff_handling(self) -> None:
        """Citoid response with maxlag backoff triggers retry and succeeds."""
        target_url = "https://example.com/lag-test"
        encoded = "https%3A%2F%2Fexample.com%2Flag-test"
        lag_resp = {"error": {"code": "maxlag", "info": "Waiting for 10.64.16.46: 1.2s lagged."}}
        success_resp = [{"itemType": "webpage", "title": "Lag Resolved", "url": target_url}]

        route = respx.get(f"{CITOID_ENDPOINT}/{encoded}")
        route.side_effect = [
            httpx.Response(200, json=lag_resp),
            httpx.Response(200, json=success_resp),
        ]
        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_url)
        assert meta["title"] == "Lag Resolved"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_404_fallback_to_domain(self) -> None:
        """Citoid 404 falls back to basic domain-based metadata without crashing."""
        target_url = "https://unresolved-domain.org/unknown"
        encoded = "https%3A%2F%2Funresolved-domain.org%2Funknown"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        # Mock HTML fetch fallback
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>Unresolved Site Title</title></head></html>",
        )

        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_url)
        assert meta["source"] == "fallback"
        assert "Unresolved Site Title" in meta["title"]
        assert meta["url"] == target_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_400_bad_request_fallback(self) -> None:
        """Citoid 400 Bad Request triggers fallback gracefully."""
        target_url = "https://bad-host.org"
        encoded = "https%3A%2F%2Fbad-host.org"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=400, json={"error": "Invalid host supplied"}
        )
        respx.get(target_url).respond(status_code=404)

        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(target_url)
        assert meta["source"] == "fallback"
        assert meta["url"] == target_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_network_timeout_handling(self) -> None:
        """Network timeout is handled gracefully and triggers HTML fallback."""
        target_url = "https://timeout-domain.com/slow"
        encoded = "https%3A%2F%2Ftimeout-domain.com%2Fslow"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").mock(
            side_effect=httpx.ConnectTimeout("Connection timed out")
        )
        respx.get(target_url).respond(status_code=500)

        fast_client = CitoidClient(rate_limit_rps=1000.0)
        meta = await fast_client.fetch_citation_metadata(
            target_url, fallback_title="आपातकालीन शीर्षक"
        )
        assert meta["source"] == "fallback"
        assert meta["title"] == "आपातकालीन शीर्षक"

    def test_select_cs1_template_mapping(self) -> None:
        """Item types map to standard CS1 classes correctly."""
        assert select_cs1_template("webpage") == "Cite web"
        assert select_cs1_template("newspaperArticle") == "Cite news"
        assert select_cs1_template("journalArticle") == "Cite journal"
        assert select_cs1_template("book") == "Cite book"
        assert select_cs1_template("unknown_custom_type") == "Cite web"

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_pacing(self) -> None:
        """Rate limiter wait executes without error under high rps."""
        limiter = CitoidRateLimiter(rps=100.0)
        await limiter.wait()
        await limiter.wait()
        assert limiter.interval == 0.01

    @pytest.mark.asyncio
    @respx.mock
    async def test_module_level_fetch_citation_metadata(self) -> None:
        """Module-level fetch_citation_metadata calls default singleton."""
        url = "https://example.com/module-test"
        encoded = "https%3A%2F%2Fexample.com%2Fmodule-test"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{"itemType": "webpage", "title": "Module Test", "url": url}],
        )
        meta = await fetch_citation_metadata(url)
        assert meta["title"] == "Module Test"


# ==============================================================================
# Class 3: CS1 / CS2 Modernization Tests
# ==============================================================================
class TestCS1Modernization:

    def test_cs1_deadurl_yes_to_dead(self) -> None:
        """|deadurl=yes is modernized to |url-status=dead."""
        wikitext = "{{Cite web |url=https://example.com |title=परीक्षण |deadurl=yes}}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert mods[0].type == "cs1_modernize"
        assert "deadurl" not in mods[0].proposed_snippet
        assert "url-status=dead" in mods[0].proposed_snippet

    def test_cs1_deadurl_no_to_live(self) -> None:
        """|deadurl=no is modernized to |url-status=live."""
        wikitext = "{{Cite web |url=https://example.com |title=परीक्षण |deadurl=no}}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert "url-status=live" in mods[0].proposed_snippet

    def test_cs1_dead_url_hyphen_alias(self) -> None:
        """|dead-url=yes is modernized to |url-status=dead."""
        wikitext = "{{Cite web |url=https://example.com |title=परीक्षण |dead-url=yes}}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert "url-status=dead" in mods[0].proposed_snippet

    def test_cs1_deadurl_unfit_usurped(self) -> None:
        """Special values unfit / usurped / bot: unknown are preserved as url-status."""
        wikitext = "{{Cite web |url=https://example.com |title=त |deadurl=unfit}}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert "url-status=unfit" in mods[0].proposed_snippet

    def test_cs1_deadurl_redundant_when_url_status_exists(self) -> None:
        """If url-status already exists, redundant deadurl is removed safely."""
        wikitext = "{{Cite web |url=https://example.com |title=त |url-status=dead |deadurl=yes}}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert "deadurl" not in mods[0].proposed_snippet
        assert mods[0].proposed_snippet.count("url-status") == 1

    def test_cs1_deprecated_date_aliases(self) -> None:
        """Deprecated parameters accessdate, archiveurl, archivedate modernized."""
        wikitext = (
            "{{Cite web |url=https://test.org |title=त |deadurl=no "
            "|accessdate=2021-01-01 |archiveurl=http://web.archive.org/t |archivedate=20210101}}"
        )
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        prop = mods[0].proposed_snippet
        assert "url-status=live" in prop
        assert "access-date=" in prop
        assert "archive-url=" in prop
        assert "archive-date=" in prop

    def test_cs1_preserves_hindi_parameters(self) -> None:
        """Hindi parameter aliases शीर्षक, दिनांक, अभिगमन-तिथि remain strictly untouched."""
        wikitext = (
            "{{Cite web |url=https://example.com |शीर्षक=मुख्य "
            "|दिनांक=2023 |अभिगमन-तिथि=2026-11-21}}"
        )
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 0

    def test_cs1_preserves_whitespace_formatting(self) -> None:
        """Parameter whitespace (e.g. | deadurl = yes ) is preserved in replacement."""
        wikitext = "{{Cite web |url=https://example.com |title=त |  deadurl = yes }}"
        _, mods = SyntaxRepairEngine.modernize_cs1_templates(wikitext)
        assert len(mods) == 1
        assert "url-status = dead" in mods[0].proposed_snippet


# ==============================================================================
# Class 4: Syntax Repair Engine Tests
# ==============================================================================
class TestSyntaxRepairEngine:

    def test_syntax_repair_unclosed_template_at_eof(self) -> None:
        """Unclosed template brackets at EOF: {{Cite web |title=अधूरा -> appends }}."""
        wikitext = "पाठ {{Cite web |title=अधूरा साँचा"
        _, mods = SyntaxRepairEngine.balance_template_brackets(wikitext)
        assert len(mods) == 1
        assert mods[0].type == "syntax_repair"
        assert mods[0].proposed_snippet.endswith("}}")

    def test_syntax_repair_unclosed_template_inside_ref(self) -> None:
        """Unclosed template inside <ref> is balanced before </ref>."""
        wikitext = "पाठ <ref>{{Cite web |title=अधूरा साँचा</ref> आगे"
        _, mods = SyntaxRepairEngine.balance_template_brackets(wikitext)
        assert len(mods) == 1
        assert "<ref>{{Cite web |title=अधूरा साँचा}}</ref>" in mods[0].proposed_snippet

    def test_syntax_repair_stray_pipes(self) -> None:
        """Duplicate pipes || inside template collapsed to single |."""
        wikitext = "{{Cite web ||url=https://test.com |title=परीक्षण}}"
        _, mods = SyntaxRepairEngine.clean_stray_pipes(wikitext)
        assert len(mods) == 1
        assert "||" not in mods[0].proposed_snippet
        assert "|url=https://test.com" in mods[0].proposed_snippet

    def test_syntax_repair_trailing_pipe(self) -> None:
        """Trailing pipe before closing braces |}} is removed."""
        wikitext = "{{Cite web |title=परीक्षण |}}"
        _, mods = SyntaxRepairEngine.clean_stray_pipes(wikitext)
        assert len(mods) == 1
        assert mods[0].proposed_snippet == "{{Cite web |title=परीक्षण}}"

    def test_syntax_repair_pipes_do_not_touch_wikitables(self) -> None:
        """Wikitables with cell delimiters || are strictly unaffected."""
        wikitable = "{|\n| cell 1 || cell 2\n|}"
        _, mods = SyntaxRepairEngine.clean_stray_pipes(wikitable)
        assert len(mods) == 0

    def test_syntax_repair_malformed_ref_misplaced_slash(self) -> None:
        """Misplaced slashes: <ref/ name="foo"> normalized to <ref name="foo" />."""
        wikitext = '<ref/ name="foo"> कुछ पाठ'
        _, mods = SyntaxRepairEngine.fix_malformed_ref_tags(wikitext)
        assert len(mods) >= 1
        assert any('<ref name="foo" />' in m.proposed_snippet for m in mods)

    def test_syntax_repair_malformed_ref_unquoted_name(self) -> None:
        """Unquoted attributes: <ref name=foo> normalized to <ref name="foo">."""
        wikitext = "कुछ पाठ <ref name=bar> और पाठ</ref>"
        _, mods = SyntaxRepairEngine.fix_malformed_ref_tags(wikitext)
        assert len(mods) >= 1
        assert any('name="bar"' in m.proposed_snippet for m in mods)

    def test_syntax_repair_dangling_unclosed_ref(self) -> None:
        """Dangling unclosed <ref> before section heading closed with </ref>."""
        wikitext = "पाठ। <ref>अधूरा संदर्भ\n== संदर्भ =="
        _, mods = SyntaxRepairEngine.fix_malformed_ref_tags(wikitext)
        assert len(mods) >= 1
        assert any("</ref>" in m.proposed_snippet for m in mods)

    def test_repair_syntax_and_modernize_convenience_function(self) -> None:
        """repair_syntax_and_modernize runs all repairs and returns updated wikitext."""
        wikitext = "{{Cite web ||url=https://test.com |title=त |deadurl=yes}}"
        repaired, mods = repair_syntax_and_modernize(wikitext)
        assert len(mods) >= 1
        assert "||" not in repaired
        assert "url-status=dead" in repaired


# ==============================================================================
# Class 5: Descending-Span Reconstruction Invariance Tests
# ==============================================================================
class TestDescendingSpanReconstruction:

    def test_descending_span_reconstruction_single_mod(self) -> None:
        """Single modification is spliced accurately into original wikitext."""
        wikitext = "प्रारंभ पाठ <ref>पुराना</ref> समाप्त पाठ"
        start = wikitext.find("<ref>पुराना</ref>")
        end = start + len("<ref>पुराना</ref>")
        mod = Modification(
            id="mod-1",
            type="bare_url",
            span_start=start,
            span_end=end,
            original_snippet="<ref>पुराना</ref>",
            proposed_snippet="<ref>नया</ref>",
        )
        result = apply_granular_modifications(wikitext, [mod])
        assert result == "प्रारंभ पाठ <ref>नया</ref> समाप्त पाठ"

    def test_descending_span_reconstruction_multi_mod_invariance(self) -> None:
        """Multiple modifications are applied descendingly without offset shifts."""
        wikitext = "पहला <ref>A</ref> दूसरा <ref>B</ref> तीसरा <ref>C</ref>"
        s_a = wikitext.find("<ref>A</ref>")
        s_b = wikitext.find("<ref>B</ref>")
        s_c = wikitext.find("<ref>C</ref>")

        mods = [
            Modification(
                id="m1",
                type="bare_url",
                span_start=s_a,
                span_end=s_a + len("<ref>A</ref>"),
                original_snippet="<ref>A</ref>",
                proposed_snippet="<ref>ALPHA</ref>",
            ),
            Modification(
                id="m2",
                type="bare_url",
                span_start=s_b,
                span_end=s_b + len("<ref>B</ref>"),
                original_snippet="<ref>B</ref>",
                proposed_snippet="<ref>BETA</ref>",
            ),
            Modification(
                id="m3",
                type="bare_url",
                span_start=s_c,
                span_end=s_c + len("<ref>C</ref>"),
                original_snippet="<ref>C</ref>",
                proposed_snippet="<ref>GAMMA</ref>",
            ),
        ]
        # Pass in ascending order to test internal descending sort
        result = apply_granular_modifications(wikitext, mods)
        expected = "पहला <ref>ALPHA</ref> दूसरा <ref>BETA</ref> तीसरा <ref>GAMMA</ref>"
        assert result == expected

    def test_descending_span_selective_acceptance(self) -> None:
        """Unselected modifications leave their original text completely untouched."""
        wikitext = "पहला <ref>A</ref> दूसरा <ref>B</ref>"
        s_a = wikitext.find("<ref>A</ref>")
        s_b = wikitext.find("<ref>B</ref>")
        mods = [
            Modification(
                id="m1",
                type="bare_url",
                span_start=s_a,
                span_end=s_a + len("<ref>A</ref>"),
                original_snippet="<ref>A</ref>",
                proposed_snippet="<ref>ALPHA</ref>",
            ),
            Modification(
                id="m2",
                type="bare_url",
                span_start=s_b,
                span_end=s_b + len("<ref>B</ref>"),
                original_snippet="<ref>B</ref>",
                proposed_snippet="<ref>BETA</ref>",
            ),
        ]
        # Only select m2
        result = apply_granular_modifications(wikitext, [mods[1]])
        assert result == "पहला <ref>A</ref> दूसरा <ref>BETA</ref>"

    def test_descending_span_preserves_comments_and_whitespace(self) -> None:
        """Surrounding whitespace, inline comments <!-- ... --> remain byte-for-byte identical."""
        wikitext = (
            "== परिचय ==\n\n"
            "<!-- लेखक टिप्पणी: यह महत्वपूर्ण है -->\n"
            "पाठ।<ref>https://example.com</ref>\n\n"
            "== सन्दर्भ ==\n"
            "{{reflist}}\n"
        )
        s = wikitext.find("<ref>https://example.com</ref>")
        mod = Modification(
            id="m1",
            type="bare_url",
            span_start=s,
            span_end=s + len("<ref>https://example.com</ref>"),
            original_snippet="<ref>https://example.com</ref>",
            proposed_snippet="<ref>{{Cite web |url=https://example.com |title=उदाहरण}}</ref>",
        )
        result = apply_granular_modifications(wikitext, [mod])
        assert result.startswith("== परिचय ==\n\n<!-- लेखक टिप्पणी: यह महत्वपूर्ण है -->\nपाठ।")
        assert result.endswith("\n\n== सन्दर्भ ==\n{{reflist}}\n")

    def test_descending_span_rejects_overlapping_spans(self) -> None:
        """Overlapping modification spans are detected and rejected to prevent corruption."""
        wikitext = "पाठ <ref>सामग्री</ref> अंत"
        mod1 = Modification(
            id="m1",
            type="syntax_repair",
            span_start=5,
            span_end=15,
            original_snippet="<ref>सामग्री",
            proposed_snippet="<ref>नई",
        )
        mod2 = Modification(
            id="m2",
            type="syntax_repair",
            span_start=10,
            span_end=20,
            original_snippet="मग्री</ref>",
            proposed_snippet="ताजी</ref>",
        )
        with pytest.raises(ValueError, match="Overlapping"):
            apply_granular_modifications(wikitext, [mod1, mod2])

    def test_descending_span_original_snippet_mismatch_raises_error(self) -> None:
        """Mismatch between expected original snippet and target slice raises ValueError."""
        wikitext = "मूल पाठ <ref>सामग्री</ref>"
        mod = Modification(
            id="m1",
            type="syntax_repair",
            span_start=9,
            span_end=24,
            original_snippet="<ref>गलत</ref>",
            proposed_snippet="<ref>नई</ref>",
        )
        with pytest.raises(ValueError, match="Integrity check failed"):
            apply_granular_modifications(wikitext, [mod])

    def test_descending_span_disjoint_guarantee_with_cross_engine_mods(self) -> None:
        """
        Regression test: Verify that when combining bare URL modifications and syntax
        repairs where overlapping spans have been filtered, apply_granular_modifications
        guarantees byte-for-byte exactness and raises zero overlap errors.
        """
        wikitext = "प्रारंभ <ref name=unquoted>https://example.com</ref> मध्य {{cite ||}} अंत"
        snippet1 = "<ref name=unquoted>https://example.com</ref>"
        s1 = wikitext.find(snippet1)
        e1 = s1 + len(snippet1)
        snippet2 = "{{cite ||}}"
        s2 = wikitext.find(snippet2)
        e2 = s2 + len(snippet2)
        bare_mod = Modification(
            id="mod-bare-1",
            type="bare_url",
            span_start=s1,
            span_end=e1,
            original_snippet=snippet1,
            proposed_snippet='<ref name="unquoted">{{Cite web |url=https://example.com}}</ref>',
        )
        syntax_mod_stray = Modification(
            id="mod-syn-1",
            type="syntax_repair",
            span_start=s2,
            span_end=e2,
            original_snippet=snippet2,
            proposed_snippet="{{cite |}}",
        )

        # Both modifications are strictly disjoint
        assert bare_mod.span_end <= syntax_mod_stray.span_start

        # Reconstruct
        res = apply_granular_modifications(wikitext, [bare_mod, syntax_mod_stray])
        assert 'name="unquoted"' in res
        assert "{{Cite web" in res
        assert "{{cite |}}" in res
        assert "प्रारंभ " in res
        assert " मध्य " in res
        assert " अंत" in res
