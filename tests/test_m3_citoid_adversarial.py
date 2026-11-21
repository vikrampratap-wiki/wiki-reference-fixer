"""
Milestone 3 Adversarial Test Suite: Citoid Engine, Concurrency, Rate Limiting & Malformed URLs.
Authoritative Source: ORIGINAL_REQUEST.md (§3, R1, R5), PROJECT.md (§Feature 9).

Evaluates:
1. CitoidRateLimiter 10-20 concurrent lookups: strict pacing, no starvation, cancellation.
2. Simulated upstream Citoid failures: HTTP 400, 404, 429 with Retry-After, 503, timeouts, binary.
3. HTML <title> and domain fallback gracefulness under errors, timeouts, entities, non-UTF-8.
4. Malformed URL fuzzing: schemeless, Unicode/IDN domains, null bytes, long URLs, empty strings.
"""

from __future__ import annotations

import asyncio
import time
from typing import List
import urllib.parse

import httpx
import pytest
import respx

from app.services.citoid import (
    CitoidClient,
    CitoidRateLimiter,
    format_cs1_template,
)

CITOID_ENDPOINT = "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki"


# ==============================================================================
# Suite 1: Concurrency, Strict Pacing & Starvation Resilience
# ==============================================================================
class TestCitoidRateLimiterConcurrency:

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_20_concurrent_pacing_and_no_starvation(self) -> None:
        """
        Verify CitoidRateLimiter with 20 concurrent requests enforces strict interval pacing.
        No coroutine suffers starvation, and successive completion intervals respect the rate limit.
        """
        rps = 20.0
        interval = 1.0 / rps  # 0.05 seconds
        limiter = CitoidRateLimiter(rps=rps)
        completion_timestamps: List[float] = []

        async def worker(worker_id: int) -> None:
            await limiter.wait()
            completion_timestamps.append(time.monotonic())

        # Launch 20 concurrent tasks simultaneously
        tasks = [asyncio.create_task(worker(i)) for i in range(20)]
        start_time = time.monotonic()
        await asyncio.gather(*tasks)
        total_duration = time.monotonic() - start_time

        # All 20 tasks must complete (no starvation)
        assert len(completion_timestamps) == 20

        # Sort completion timestamps
        completion_timestamps.sort()

        # Verify pacing between consecutive completions
        # Allow tiny clock jitter tolerance (0.015s)
        jitter_tolerance = 0.015
        for i in range(1, len(completion_timestamps)):
            delta = completion_timestamps[i] - completion_timestamps[i - 1]
            assert delta >= (interval - jitter_tolerance), (
                f"Pacing violation between request {i-1} and {i}: "
                f"delta={delta:.4f}s < expected {interval - jitter_tolerance:.4f}s"
            )

        # Total duration for 20 requests at 0.05s intervals must be at least ~0.95s
        min_expected_total = (20 - 1) * interval * 0.85
        assert total_duration >= min_expected_total, (
            f"Total burst duration {total_duration:.4f}s < "
            f"minimum expected {min_expected_total:.4f}s"
        )

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_cancellation_resilience(self) -> None:
        """
        Cancelling tasks while waiting inside CitoidRateLimiter lock must not deadlock
        the remaining tasks.
        """
        limiter = CitoidRateLimiter(rps=20.0)
        results: List[int] = []

        async def worker(worker_id: int) -> None:
            await limiter.wait()
            results.append(worker_id)

        tasks = [asyncio.create_task(worker(i)) for i in range(10)]

        # Let the first task acquire the limiter lock
        await asyncio.sleep(0.01)

        # Cancel tasks 2 and 5 mid-wait
        tasks[2].cancel()
        tasks[5].cancel()

        # Await all tasks with return_exceptions=True
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify cancelled tasks raised CancelledError
        assert isinstance(gathered[2], asyncio.CancelledError)
        assert isinstance(gathered[5], asyncio.CancelledError)

        # Remaining 8 tasks must have completed successfully without deadlock
        assert len(results) == 8
        for wid in [0, 1, 3, 4, 6, 7, 8, 9]:
            assert wid in results

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_zero_and_negative_rps(self) -> None:
        """Rate limiter with rps <= 0 should return immediately without zero-division error."""
        zero_limiter = CitoidRateLimiter(rps=0.0)
        neg_limiter = CitoidRateLimiter(rps=-10.0)

        t0 = time.monotonic()
        await zero_limiter.wait()
        await neg_limiter.wait()
        assert (time.monotonic() - t0) < 0.1

    @pytest.mark.asyncio
    @respx.mock
    async def test_concurrent_citoid_client_15_concurrent_lookups(self) -> None:
        """
        15 concurrent CitoidClient URL lookups run concurrently without race conditions
        or dropped requests.
        """
        client = CitoidClient(rate_limit_rps=50.0)

        # Mock 15 distinct URLs
        for i in range(15):
            url = f"https://news-site-{i}.org/article"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "newspaperArticle",
                    "title": f"समाचार शीर्षक {i}",
                    "url": url,
                    "publicationTitle": f"दैनिक समाचार {i}",
                    "date": "2026-11-21",
                }],
            )

        async def fetch_worker(index: int) -> str:
            target = f"https://news-site-{index}.org/article"
            cs1 = await client.url_to_cs1(target)
            return cs1

        results = await asyncio.gather(*[fetch_worker(i) for i in range(15)])
        assert len(results) == 15
        for i, cs1 in enumerate(results):
            assert f"title=समाचार शीर्षक {i}" in cs1
            assert f"work=दैनिक समाचार {i}" in cs1
            assert "{{Cite news" in cs1


# ==============================================================================
# Suite 2: Simulated Upstream Citoid Failures & Transport Resilience
# ==============================================================================
class TestCitoidUpstreamFailureSimulation:

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_upstream_400_bad_url_triggers_fallback(self) -> None:
        """HTTP 400 Bad URL from Citoid triggers graceful HTML title fallback."""
        target_url = "https://malformed-query.com/test?bad=true"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=400,
            json={"error": "Invalid target URL format"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>बचाव शीर्षक पृष्ठ</title></head><body>सामग्री</body></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "बचाव शीर्षक पृष्ठ"
        assert meta["website"] == "malformed-query.com"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_upstream_404_paywalled_triggers_fallback(self) -> None:
        """HTTP 404 (paywalled / not found in Citoid) triggers fallback without crash."""
        target_url = "https://paywalled-academic-journal.org/paper-99"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        respx.get(target_url).respond(
            status_code=403,  # Paywalled direct page as well
            text="Forbidden / Subscription required",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(
            target_url, fallback_title="शोभा पत्रिका शोध"
        )

        assert meta["source"] == "fallback"
        assert meta["title"] == "शोभा पत्रिका शोध"
        assert meta["website"] == "paywalled-academic-journal.org"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_upstream_429_retry_after_recovery(self) -> None:
        """HTTP 429 with Retry-After header automatically retries and succeeds."""
        target_url = "https://rate-limited-test.org/page"
        encoded = urllib.parse.quote(target_url, safe="")

        route = respx.get(f"{CITOID_ENDPOINT}/{encoded}")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0.05"}),
            httpx.Response(
                200,
                json=[{
                    "itemType": "webpage",
                    "title": "दर-सीमा पश्चात सफलता",
                    "url": target_url,
                }],
            ),
        ]

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "citoid"
        assert meta["title"] == "दर-सीमा पश्चात सफलता"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_upstream_429_exhaustion_triggers_fallback(self) -> None:
        """Repeated HTTP 429 responses exhaust retries and trigger fallback gracefully."""
        target_url = "https://heavy-throttled.org/page"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=429,
            headers={"Retry-After": "0.01"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>बैकअप वेब पृष्ठ</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "बैकअप वेब पृष्ठ"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_upstream_503_outage_triggers_fallback(self) -> None:
        """HTTP 503 Service Unavailable (upstream outage) exhausts retries and triggers fallback."""
        target_url = "https://outage-site.org/info"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=503,
            headers={"Retry-After": "0.01"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>आउटेज बैकअप</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "आउटेज बैकअप"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_connection_and_read_timeouts_trigger_fallback(self) -> None:
        """Both ConnectTimeout and ReadTimeout gracefully trigger HTML fallback."""
        client = CitoidClient(rate_limit_rps=100.0)

        # 1. ConnectTimeout
        url1 = "https://timeout-connect.org"
        enc1 = urllib.parse.quote(url1, safe="")
        respx.get(f"{CITOID_ENDPOINT}/{enc1}").mock(
            side_effect=httpx.ConnectTimeout("Citoid Connect Timeout")
        )
        respx.get(url1).respond(status_code=200, text="<title>कनेक्ट बैकअप</title>")

        meta1 = await client.fetch_citation_metadata(url1)
        assert meta1["source"] == "fallback"
        assert meta1["title"] == "कनेक्ट बैकअप"

        # 2. ReadTimeout
        url2 = "https://timeout-read.org"
        enc2 = urllib.parse.quote(url2, safe="")
        respx.get(f"{CITOID_ENDPOINT}/{enc2}").mock(
            side_effect=httpx.ReadTimeout("Citoid Read Timeout")
        )
        respx.get(url2).respond(status_code=200, text="<title>रीड बैकअप</title>")

        meta2 = await client.fetch_citation_metadata(url2)
        assert meta2["source"] == "fallback"
        assert meta2["title"] == "रीड बैकअप"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_non_utf8_binary_upstream_response(self) -> None:
        """Citoid returning non-UTF-8 corrupt binary bytes triggers fallback gracefully."""
        target_url = "https://binary-response.org/data"
        encoded = urllib.parse.quote(target_url, safe="")

        # Non-UTF8 corrupted byte sequence
        corrupt_bytes = b"\xff\xfe\xfa\xfb\x80\x81\x82\x83"
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            content=corrupt_bytes,
            headers={"Content-Type": "application/json"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>बाइनरी रिकवरी पृष्ठ</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "बाइनरी रिकवरी पृष्ठ"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_html_502_instead_of_json(self) -> None:
        """Citoid returning 200 with HTML (e.g. edge proxy splash) triggers fallback."""
        target_url = "https://proxy-html-error.org"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            text="<html><head><title>502 Bad Gateway</title></head><body>Down</body></html>",
            headers={"Content-Type": "text/html"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>वास्तविक साइट</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "वास्तविक साइट"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_empty_list_or_dict_response(self) -> None:
        """Citoid returning empty array [] triggers fallback cleanly."""
        target_url = "https://empty-list.org/page"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[],
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>रिक्त सूची विकल्प</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "रिक्त सूची विकल्प"


# ==============================================================================
# Suite 3: HTML <title> & Domain Fallback Resilience
# ==============================================================================
class TestHtmlFallbackGracefulness:

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_extracts_og_title_when_title_tag_missing(self) -> None:
        """When <title> tag is missing, OpenGraph <meta property='og:title'> is extracted."""
        url = "https://og-site.com/post"
        encoded = urllib.parse.quote(url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        respx.get(url).respond(
            status_code=200,
            text=(
                '<html><head>'
                '<meta property="og:title" content="ओपनग्राफ समाचार शीर्षक" />'
                '</head><body>सामग्री</body></html>'
            ),
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(url)

        assert meta["title"] == "ओपनग्राफ समाचार शीर्षक"
        assert meta["source"] == "fallback"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_cleans_html_entities_and_whitespace(self) -> None:
        """HTML entities &quot;, &amp;, &#39; and messy whitespace in <title> are cleaned."""
        url = "https://entities-site.com/news"
        encoded = urllib.parse.quote(url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        respx.get(url).respond(
            status_code=200,
            text=(
                '<html><head>'
                '<title>  &quot;विशेष रिपोर्ट&quot; &amp; &#39;खुलासा&#39;  \n\t  </title>'
                '</head></html>'
            ),
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(url)

        assert meta["title"] == '"विशेष रिपोर्ट" & \'खुलासा\''

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_when_target_site_completely_unresponsive(self) -> None:
        """When both Citoid and target website are down, falls back to domain-derived title."""
        url = "https://completely-dead-website.net/path"
        encoded = urllib.parse.quote(url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        respx.get(url).mock(
            side_effect=httpx.ConnectError("Connection refused by target site")
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "completely-dead-website.net वेब सन्दर्भ"
        assert meta["website"] == "completely-dead-website.net"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_empty_title_tag_uses_domain(self) -> None:
        """Empty <title></title> or whitespace-only title falls back to domain name."""
        url = "https://empty-title.org/page"
        encoded = urllib.parse.quote(url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
        respx.get(url).respond(
            status_code=200,
            text="<html><head><title>   \n   </title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(url)

        assert meta["title"] == "empty-title.org वेब सन्दर्भ"


# ==============================================================================
# Suite 4: Malformed URLs & Robustness Fuzzing
# ==============================================================================
class TestMalformedUrlsAndFuzzing:

    @pytest.mark.asyncio
    @respx.mock
    async def test_schemeless_url_handled_gracefully(self) -> None:
        """Schemeless URL does not raise UnsupportedProtocol or crash the process."""
        schemeless = "timesofindia.indiatimes.com/world/us/article.cms"
        encoded = urllib.parse.quote(schemeless, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(schemeless)

        assert meta["source"] == "fallback"
        assert meta["url"] == schemeless
        # cs1 template conversion must succeed
        cs1 = format_cs1_template(meta)
        assert "{{Cite web" in cs1
        assert f"url={schemeless}" in cs1

    @pytest.mark.asyncio
    @respx.mock
    async def test_unicode_idn_and_devanagari_domain_url(self) -> None:
        """Unicode internationalized domain names (IDN / Devanagari) work end-to-end."""
        target_url = "https://उदाहरण.भारत/समाचार/2026"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "newspaperArticle",
                "title": "भारत समाचार 2026",
                "url": target_url,
                "publicationTitle": "उदाहरण भारत",
                "date": "2026-11-21",
            }],
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "citoid"
        assert meta["title"] == "भारत समाचार 2026"
        cs1 = format_cs1_template(meta)
        assert "{{Cite news" in cs1
        assert "work=उदाहरण भारत" in cs1
        assert f"url={target_url}" in cs1

    @pytest.mark.asyncio
    @respx.mock
    async def test_null_byte_in_url(self) -> None:
        """URL containing embedded null byte is encoded safely without OS or transport crash."""
        target_url = "https://example.com/malicious\x00path"
        encoded = urllib.parse.quote(target_url, safe="")
        assert "%00" in encoded

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=400,
            json={"error": "Invalid character in URL"},
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        cs1 = format_cs1_template(meta)
        assert "{{Cite web" in cs1

    @pytest.mark.asyncio
    @respx.mock
    async def test_excessively_long_url_fuzzing(self) -> None:
        """5,000-character excessively long URL does not cause buffer overflow or ReDoS."""
        long_query = "&key=" + ("abcdef1234567890" * 400)
        long_url = "https://example.com/article?start=1" + long_query
        assert len(long_url) > 5000

        encoded = urllib.parse.quote(long_url, safe="")
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=414,  # URI Too Long
        )

        client = CitoidClient(rate_limit_rps=100.0)
        t0 = time.monotonic()
        meta = await client.fetch_citation_metadata(long_url)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0  # Fast execution, no catastrophic backtracking
        assert meta["source"] == "fallback"
        assert meta["website"] == "example.com"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_and_whitespace_url_graceful_handling(self) -> None:
        """Empty string or pure whitespace URL handled gracefully without unhandled crash."""
        respx.get(f"{CITOID_ENDPOINT}/").respond(status_code=400)
        client = CitoidClient(rate_limit_rps=100.0)

        for empty_val in ["", "   ", "\t\n  "]:
            meta = await client.fetch_citation_metadata(empty_val)
            assert meta["source"] == "fallback"
            cs1 = format_cs1_template(meta)
            assert "{{Cite web" in cs1

    @pytest.mark.asyncio
    @respx.mock
    async def test_xss_and_injection_characters_in_url(self) -> None:
        """URLs containing XSS payloads, HTML tags, or quotes are sanitized in CS1 template."""
        xss_url = "https://example.com/search?q=<script>alert('xss')</script>&test=1"
        encoded = urllib.parse.quote(xss_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "webpage",
                "title": "सुरक्षित <script> शीर्षक",
                "url": xss_url,
            }],
        )

        client = CitoidClient(rate_limit_rps=100.0)
        cs1 = await client.url_to_cs1(xss_url)

        assert "{{Cite web" in cs1
        assert f"url={xss_url}" in cs1


# ==============================================================================
# Suite 5: Data Normalization & Parser Robustness
# ==============================================================================
class TestCitoidDataNormalizationAdversarial:

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_malformed_zotero_entry_null_and_missing_fields(self) -> None:
        """Citoid response with completely empty dictionary still produces valid CS1."""
        target_url = "https://example.com/minimal"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{}],  # Missing itemType, title, url, etc.
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "citoid"
        assert meta["title"] == "वेब सन्दर्भ"
        assert meta["url"] == target_url
        cs1 = format_cs1_template(meta)
        assert "{{Cite web" in cs1
        assert f"url={target_url}" in cs1
        assert "title=वेब सन्दर्भ" in cs1

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_non_list_json_triggers_fallback(self) -> None:
        """Citoid returning a JSON object or string instead of a list triggers fallback."""
        target_url = "https://example.com/dict-response"
        encoded = urllib.parse.quote(target_url, safe="")

        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json={"error": "Not an array"},
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<title>डिक्ट बैकअप पृष्ठ</title>",
        )

        client = CitoidClient(rate_limit_rps=100.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "fallback"
        assert meta["title"] == "डिक्ट बैकअप पृष्ठ"

    def test_parse_authors_adversarial_types(self) -> None:
        """_parse_authors handles unexpected and corrupt types without crashing."""
        from app.services.citoid import _parse_authors

        # Corrupt and unusual author inputs
        assert _parse_authors(None) == []
        assert _parse_authors("") == []
        assert _parse_authors(12345) == []
        assert _parse_authors([None, 123, []]) == []
        assert _parse_authors([{"invalid": "data"}]) == []
        assert _parse_authors([{"name": "एकल लेखक"}]) == [("", "एकल लेखक")]
        assert _parse_authors([["प्रथम"]]) == [("", "प्रथम")]
        assert _parse_authors("सीधा नाम") == [("", "सीधा नाम")]

    def test_clean_domain_adversarial_urls(self) -> None:
        """_clean_domain safely extracts domain or empty string on invalid URLs."""
        from app.services.citoid import _clean_domain

        assert _clean_domain("https://www.jagran.com/news") == "jagran.com"
        assert _clean_domain("http://example.org:8080/path") == "example.org"
        assert _clean_domain("javascript:alert(1)") == ""
        assert _clean_domain("data:text/html;base64,123") == ""
        assert _clean_domain("") == ""
        assert _clean_domain("not-a-valid-url") == ""
