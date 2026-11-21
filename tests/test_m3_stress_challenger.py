"""
Adversarial Stress Test Suite: Span Deduplication & Descending Span Reconstructor.
Empirically tests complex multi-tag mixtures, boundary conditions, and fuzz combinations
to verify zero overlapping spans and zero ValueError in app/api/scan.py and app/services/parser.py.
Author: challenger_m3_iter3_1
"""

import random
import urllib.parse
from typing import Any, Dict, List
import pytest
import respx
import httpx

from app.api.scan import scan_wikitext, ScanPayload
from app.services.citoid import citoid_client
from app.services.parser import apply_granular_modifications


CITOID_ENDPOINT = "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki"


@pytest.fixture(autouse=True)
def mock_citoid_global(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Zero rate limiter interval globally to prevent slow sleep delays during stress testing."""
    monkeypatch.setattr(citoid_client.rate_limiter, "interval", 0.0)
    if "active_mock" in request.node.name or "timeout_and_error" in request.node.name:
        yield
        return

    with respx.mock(assert_all_called=False) as respx_mock:
        def citoid_side_effect(req: httpx.Request) -> httpx.Response:
            path_str = str(req.url.path)
            encoded_url = path_str.split("/mediawiki/")[-1]
            raw_url = urllib.parse.unquote(encoded_url)
            return httpx.Response(
                status_code=200,
                json=[{
                    "itemType": "webpage",
                    "title": f"Citoid शीर्षक {raw_url}",
                    "url": raw_url,
                    "websiteTitle": "परीक्षण जालस्थल",
                }],
            )
        respx_mock.get(url__regex=r"^https://hi\.wikipedia\.org/api/rest_v1/data/citation/mediawiki/.*").mock(
            side_effect=citoid_side_effect
        )
        yield


def _verify_disjoint_and_reconstruct(wikitext: str, mods: List[Dict[str, Any]]) -> str:
    """
    Helper to verify that all modifications have valid spans, are strictly disjoint,
    and reconstruct cleanly via apply_granular_modifications without ValueError.
    """
    if not mods:
        return wikitext

    # Sort ascending to verify non-overlapping intervals
    sorted_mods = sorted(mods, key=lambda m: int(m["span_start"]))
    for i in range(len(sorted_mods) - 1):
        m1 = sorted_mods[i]
        m2 = sorted_mods[i + 1]
        s1, e1 = int(m1["span_start"]), int(m1["span_end"])
        s2, e2 = int(m2["span_start"]), int(m2["span_end"])

        assert s1 >= 0, f"Negative span_start in {m1['id']}"
        assert e1 >= s1, f"span_end < span_start in {m1['id']}"
        assert e1 <= s2, (
            f"Overlap detected between {m1['id']} [{s1}:{e1}] "
            f"and {m2['id']} [{s2}:{e2}]"
        )

    # Reconstruct with all modifications
    reconstructed = apply_granular_modifications(wikitext, mods)
    assert isinstance(reconstructed, str)

    # Reconstruct with each modification individually
    for m in mods:
        single_res = apply_granular_modifications(wikitext, [m])
        assert isinstance(single_res, str)

    # Reconstruct with alternating subsets (even indices, odd indices)
    if len(mods) > 1:
        even_mods = [m for idx, m in enumerate(mods) if idx % 2 == 0]
        odd_mods = [m for idx, m in enumerate(mods) if idx % 2 != 0]
        apply_granular_modifications(wikitext, even_mods)
        apply_granular_modifications(wikitext, odd_mods)

    return reconstructed


@pytest.mark.asyncio
async def test_adversarial_unquoted_attribute_variations() -> None:
    """
    Stress test diverse variations of unquoted attributes on bare URL refs:
    - Single unquoted name
    - Unquoted name with digits, dashes, underscores
    - Unquoted group attribute
    - Both unquoted name and group attributes
    - Reversed order (group before name)
    - Extra whitespace inside ref tag
    """
    test_cases = [
        '<ref name=simple>https://example.com/1</ref>',
        '<ref name=with-dash_123>https://example.com/2</ref>',
        '<ref group=notegroup>https://example.com/3</ref>',
        '<ref name=foo group=bar>https://example.com/4</ref>',
        '<ref group=bar name=foo>https://example.com/5</ref>',
        '<ref   name=spaced   group=notes   >https://example.com/6</ref>',
        '<ref name="quoted" group=unquoted>https://example.com/7</ref>',
        '<ref name=unquoted group="quoted">https://example.com/8</ref>',
    ]

    for raw in test_cases:
        wikitext = f"प्रारंभिक पाठ। {raw} समाप्ति पाठ।"
        payload = ScanPayload(wikitext=wikitext)
        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        mods = response["modifications"]
        assert len(mods) >= 1

        reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
        assert "https://example.com" in reconstructed
        assert "प्रारंभिक पाठ।" in reconstructed
        assert "समाप्ति पाठ।" in reconstructed


@pytest.mark.asyncio
async def test_adversarial_adjacent_and_abutting_spans() -> None:
    """
    Stress test abutting and immediately adjacent tags:
    - Two bare URLs with zero whitespace between them
    - Bare URL immediately followed by template with stray pipes
    - Template with stray pipe immediately followed by bare URL
    - Bare URL directly touching section heading
    """
    wikitext = (
        "<ref name=u1>https://example.com/1</ref>"
        "<ref name=u2>https://example.com/2</ref>"
        "{{cite || title=T}}"
        "<ref group=g1>https://example.com/3</ref>"
        "\n== अनुभाग =="
    )
    payload = ScanPayload(wikitext=wikitext)
    response = await scan_wikitext(payload)

    assert response["status"] == "success"
    mods = response["modifications"]
    assert len(mods) >= 3

    reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
    assert 'name="u1"' in reconstructed
    assert 'name="u2"' in reconstructed
    assert 'group="g1"' in reconstructed
    assert "||" not in reconstructed
    assert "\n== अनुभाग ==" in reconstructed


@pytest.mark.asyncio
async def test_adversarial_duplicate_bare_urls_and_refs() -> None:
    """
    Stress test exact duplicate bare URLs and ref tags:
    - Multiple identical bare URL tags in different locations
    - Ensures progressive cursor in parser correctly identifies distinct spans
      and does not cause overlapping intervals or integrity check failures.
    """
    identical_tag = "<ref name=dup>https://example.com/same</ref>"
    wikitext = (
        f"पहला {identical_tag} मध्य {identical_tag} "
        f"तीसरा {identical_tag} अंत।"
    )
    payload = ScanPayload(wikitext=wikitext)
    response = await scan_wikitext(payload)

    assert response["status"] == "success"
    mods = response["modifications"]
    assert len(mods) == 3

    # All three must have different, strictly ascending spans
    spans = [(m["span_start"], m["span_end"]) for m in mods]
    assert spans[0][1] <= spans[1][0]
    assert spans[1][1] <= spans[2][0]

    reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
    assert "पहला " in reconstructed
    assert " मध्य " in reconstructed
    assert " तीसरा " in reconstructed
    assert " अंत।" in reconstructed
    assert reconstructed.count('name="dup"') == 3


@pytest.mark.asyncio
async def test_adversarial_excluded_zones_comments_and_nowiki() -> None:
    """
    Stress test HTML comments and nowiki tags containing bare URLs and syntax errors:
    - Bare URL inside <!-- --> comment (must be ignored by parser)
    - Bare URL inside <nowiki> tag (must be ignored by parser)
    - Bare URL outside comment with identical content
    - Unquoted attribute inside comment vs outside comment
    """
    wikitext = (
        "<!-- <ref name=unquoted>https://example.com/comment</ref> -->\n"
        "सक्रिय पाठ: <ref name=unquoted>https://example.com/active</ref>\n"
        "<nowiki><ref name=nw>https://example.com/nowiki</ref></nowiki>\n"
        "अन्य: {{cite || title=Outside}}\n"
        "<!-- {{cite || title=InsideComment}} -->"
    )
    payload = ScanPayload(wikitext=wikitext)
    response = await scan_wikitext(payload)

    assert response["status"] == "success"
    mods = response["modifications"]

    # Active bare URL should be modified
    active_mods = [
        m for m in mods
        if "https://example.com/active" in str(m.get("original_snippet", ""))
    ]
    assert len(active_mods) == 1

    # Commented bare URL should NOT be a bare_url mod
    comment_bare = [
        m for m in mods
        if m.get("type") == "bare_url"
        and "https://example.com/comment" in str(m.get("original_snippet", ""))
    ]
    assert len(comment_bare) == 0

    reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
    assert "https://example.com/active" in reconstructed


@pytest.mark.asyncio
async def test_adversarial_mixed_wikitext_megapack() -> None:
    """
    Comprehensive document mixing every type of modification:
    - Bare URLs with bracketed titles: [https://example.com/b Title]
    - Pure bare URLs: https://example.com/pure
    - Bare URLs with unquoted name and group attributes
    - CS1 template with deadurl=yes and accessdate
    - Stray double pipes in templates: {{cite || title=Test}}
    - Trailing pipe in template: {{cite |url=http... |}}
    - Unclosed template before heading
    - Self-closing malformed ref tags: <ref name=selfclose />
    - Devanagari Hindi text and section headings
    """
    wikitext = (
        "== परिचय ==\n"
        "यह एक परीक्षण लेख है जिसमें विभिन्न त्रुटियाँ हैं। "
        "<ref name=unquoted1>https://example.com/item1</ref>\n"
        "दूसरा संदर्भ: "
        "<ref group=notes name=unquoted2>[https://example.com/item2 द्वितीय]</ref>\n\n"
        "== विवरण ==\n"
        "साँचा त्रुटि: {{Cite web |url=https://example.com/cs1 ||title=परीक्षण |deadurl=yes "
        "|accessdate=2024-01-01}}\n"
        "ट्रेलिंग पाइप: {{Cite news |url=https://example.com/news |title=समाचार |}}\n"
        "तीसरा संदर्भ: <ref name=\"quoted3\">https://example.com/item3</ref>\n"
        "स्वयं-समाप्त: <ref name=self1 />\n\n"
        "== संदर्भ सूची ==\n"
        "{{reflist}}\n"
    )

    payload = ScanPayload(wikitext=wikitext)
    response = await scan_wikitext(payload)

    assert response["status"] == "success"
    mods = response["modifications"]
    assert len(mods) >= 5

    reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
    assert "== परिचय ==" in reconstructed
    assert "== विवरण ==" in reconstructed
    assert "== संदर्भ सूची ==" in reconstructed
    assert 'name="unquoted1"' in reconstructed
    assert 'name="unquoted2"' in reconstructed
    assert 'url-status=dead' in reconstructed
    assert 'access-date=2024-01-01' in reconstructed
    assert 'name="self1"' in reconstructed
    assert "{{reflist}}" in reconstructed


@pytest.mark.asyncio
async def test_adversarial_combinatorial_fuzz_stress() -> None:
    """
    Combinatorial fuzz generator:
    Constructs 50 uniquely randomized wikitext documents with random combinations
    of bare URLs, unquoted attributes, stray pipes, unclosed brackets, comments,
    and CS1 modernizations.
    Verifies that every single generated document:
    1. Produces 100% disjoint modification spans.
    2. Reconstructs cleanly without ValueError or IndexError.
    3. Reconstructs clean subsets without corruption.
    """
    rng = random.Random(42)

    bare_url_templates = [
        '<ref name=u{i}>https://example.com/page{i}</ref>',
        '<ref group=g{i} name=u{i}>https://example.com/page{i}</ref>',
        '<ref name="q{i}">https://example.com/page{i}</ref>',
        '<ref>[https://example.com/page{i} शीर्षक {i}]</ref>',
        '<ref>https://example.com/page{i}</ref>',
    ]

    syntax_defect_templates = [
        '{{Cite web |url=https://test.com/{i} ||title=शीर्षक {i}}}',
        '{{Cite web |url=https://test.com/{i} |deadurl=yes |accessdate=2023-05-0{i}}}',
        '{{Cite news |url=https://news.com/{i} |title=खबर {i} |}}',
        '<ref name=synself{i} />',
        '<!-- <ref name=hidden{i}>https://hidden.com/{i}</ref> -->',
    ]

    fillers = [
        " यहाँ कुछ सामान्य हिन्दी पाठ है।",
        "\n\n== अगला भाग ==\n",
        " विकिपीडिया सन्दर्भ सुधार परीक्षण।",
        "\n* सूची प्रविष्टि विवरण।",
        " अन्य विवरण।",
    ]

    for iteration in range(50):
        parts: List[str] = ["== मुख्य शीर्षक =="]
        num_elements = rng.randint(4, 12)

        for i in range(num_elements):
            element_type = rng.choice(["bare", "syntax", "filler"])
            if element_type == "bare":
                tpl = rng.choice(bare_url_templates)
                parts.append(tpl.format(i=f"{iteration}_{i}"))
            elif element_type == "syntax":
                tpl = rng.choice(syntax_defect_templates)
                parts.append(tpl.format(i=f"{iteration}_{i}"))
            else:
                parts.append(rng.choice(fillers))

        doc = " ".join(parts)
        payload = ScanPayload(wikitext=doc)
        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        mods = response["modifications"]

        # Run disjoint span and reconstruction validation
        _verify_disjoint_and_reconstruct(doc, mods)


@pytest.mark.asyncio
async def test_adversarial_citoid_enrichment_active_mock() -> None:
    """
    Verify scan endpoint with Citoid mocked to return valid CS1 metadata:
    - Multiple bare URLs enriched with authentic metadata
    - Unquoted attributes preserved and properly quoted
    - Intermixed with syntax repairs
    - Guarantees zero span collision between Citoid-enriched bare mods and syntax mods
    """
    urls = [
        "https://example.com/article1",
        "https://example.com/article2",
    ]

    with respx.mock(assert_all_called=False) as respx_mock:
        for idx, u in enumerate(urls, start=1):
            enc = urllib.parse.quote(u, safe="")
            respx_mock.get(f"{CITOID_ENDPOINT}/{enc}").respond(
                status_code=200,
                json=[{
                    "itemType": "webpage",
                    "title": f"Citoid शीर्षक {idx}",
                    "url": u,
                    "websiteTitle": "परीक्षण जालस्थल",
                }],
            )

        wikitext = (
            "परिचय। <ref name=bare1>https://example.com/article1</ref>\n"
            "मध्य पाठ। {{Cite web |url=https://example.com/s ||title=Stray}}\n"
            "निष्कर्ष। <ref name=bare2 group=g1>https://example.com/article2</ref>"
        )

        payload = ScanPayload(wikitext=wikitext)
        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        mods = response["modifications"]
        assert len(mods) == 3

        reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
        assert 'name="bare1"' in reconstructed
        assert 'name="bare2"' in reconstructed
        assert 'group="g1"' in reconstructed
        assert "Citoid शीर्षक 1" in reconstructed
        assert "Citoid शीर्षक 2" in reconstructed
        assert "||title" not in reconstructed
        assert "|title=Stray" in reconstructed


@pytest.mark.asyncio
async def test_adversarial_citoid_timeout_and_error_fallback() -> None:
    """
    Verify scan endpoint when Citoid raises timeout/network errors:
    - Gracefully falls back to baseline CS1 format
    - Deduplication against syntax repairs still functions perfectly
    - Zero ValueError in apply_granular_modifications
    """
    url = "https://example.com/timeout"
    with respx.mock(assert_all_called=False) as respx_mock:
        enc = urllib.parse.quote(url, safe="")
        respx_mock.get(f"{CITOID_ENDPOINT}/{enc}").mock(
            side_effect=httpx.ConnectTimeout("Citoid connection timed out")
        )

        wikitext = (
            "दस्तावेज़। <ref name=timeout_ref>https://example.com/timeout</ref>\n"
            "त्रुटि: {{Cite news |url=https://news.com ||title=T}}"
        )

        payload = ScanPayload(wikitext=wikitext)
        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        mods = response["modifications"]
        assert len(mods) == 2

        reconstructed = _verify_disjoint_and_reconstruct(wikitext, mods)
        assert 'name="timeout_ref"' in reconstructed
        assert "{{Cite web" in reconstructed
        assert "||title" not in reconstructed
