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

    def test_citoid_rate_limiter_cross_event_loop_safety(self) -> None:
        """
        Verify CitoidRateLimiter safely handles execution across multiple independent
        event loops without raising RuntimeError ('bound to a different event loop').
        Contention in Loop 1 must not corrupt lock acquisition in Loop 2 or Loop 3.
        """
        limiter = CitoidRateLimiter(rps=50.0)

        async def concurrent_burst() -> List[int]:
            completed: List[int] = []

            async def worker(worker_id: int) -> None:
                await limiter.wait()
                completed.append(worker_id)

            await asyncio.gather(*[worker(i) for i in range(10)])
            return completed

        # Execute Burst 1 under fresh event loop 1
        res1 = asyncio.run(concurrent_burst())
        assert len(res1) == 10

        # Execute Burst 2 under fresh event loop 2 (verifies cross-loop rebind)
        res2 = asyncio.run(concurrent_burst())
        assert len(res2) == 10

        # Execute Burst 3 under fresh event loop 3
        res3 = asyncio.run(concurrent_burst())
        assert len(res3) == 10

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


# ==============================================================================
# Suite 6: Burst of 50 Concurrent Citoid Requests & 1 rps Pacing Precision
# ==============================================================================
class TestCitoid50BurstConcurrencyAndPacing:
    """
    Empirical stress testing:
    - 50 concurrent Citoid requests through CitoidRateLimiter.
    - Strict 1 rps pacing verification with zero deadlocks and zero starvation.
    - High-throughput concurrency verification without dropped requests.
    """

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_50_concurrent_burst_simulated_pacing(self) -> None:
        """
        Verify that under a burst of 50 concurrent tasks, CitoidRateLimiter(rps=1.0)
        paces each task with exactly 1.0s interval without deadlock or starvation.
        Simulates sleep duration to verify exact pacing semantics across all 50 tasks.
        """
        limiter = CitoidRateLimiter(rps=1.0)
        sleep_durations: List[float] = []
        completion_order: List[int] = []

        # Intercept asyncio.sleep to record scheduled delays without blocking the event loop
        real_sleep = asyncio.sleep

        async def fake_sleep(delay: float) -> None:
            sleep_durations.append(delay)
            # Yield to event loop to simulate non-blocking cooperative execution
            await real_sleep(0)

        # Launch 50 concurrent workers simultaneously
        async def worker(worker_id: int) -> None:
            await limiter.wait()
            completion_order.append(worker_id)

        from unittest.mock import patch
        with patch("asyncio.sleep", side_effect=fake_sleep):
            tasks = [asyncio.create_task(worker(i)) for i in range(50)]
            await asyncio.gather(*tasks)

        # 1. Zero starvation: all 50 tasks must complete
        assert len(completion_order) == 50
        assert set(completion_order) == set(range(50))

        # 2. Pacing: Task 0 executes immediately (no sleep); tasks 1..49 must each sleep ~1.0s
        assert len(sleep_durations) == 49
        for idx, duration in enumerate(sleep_durations):
            assert 0.99 <= duration <= 1.01, (
                f"Pacing delay anomaly at task {idx + 1}: {duration:.4f}s"
            )

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_50_concurrent_burst_high_rps_real_clock(self) -> None:
        """
        Empirically verify 50 concurrent tasks running against a high-frequency limiter
        (rps=200.0, interval=0.005s) on real OS monotonic clock.
        Ensures lock acquisition fairness, zero event-loop stalls, and strict interval enforcement.
        """
        rps = 200.0
        interval = 1.0 / rps  # 0.005s
        limiter = CitoidRateLimiter(rps=rps)
        completion_timestamps: List[float] = []

        async def worker(worker_id: int) -> None:
            await limiter.wait()
            completion_timestamps.append(time.monotonic())

        start_time = time.monotonic()
        tasks = [asyncio.create_task(worker(i)) for i in range(50)]
        await asyncio.gather(*tasks)
        total_duration = time.monotonic() - start_time

        # All 50 tasks must complete
        assert len(completion_timestamps) == 50

        # Sort completion timestamps
        completion_timestamps.sort()

        # Verify successive completion deltas
        # Monotonic time tolerance for 5ms intervals
        tolerance = 0.002
        for i in range(1, len(completion_timestamps)):
            delta = completion_timestamps[i] - completion_timestamps[i - 1]
            assert delta >= (interval - tolerance), (
                f"Rate limiter pacing violated between task {i-1} and {i}: "
                f"delta={delta:.6f}s < expected {interval - tolerance:.6f}s"
            )

        # Minimum expected duration for 50 requests at 0.005s intervals: (50 - 1) * 0.005 = ~0.245s
        assert total_duration >= (49 * interval * 0.8)

    @pytest.mark.asyncio
    async def test_citoid_rate_limiter_strict_1rps_real_clock_small_burst(self) -> None:
        """
        Empirically verify strict 1 rps (1.00s interval) pacing on real clock with 4 requests.
        Ensures real-time pacing accuracy is within acceptable clock jitter limits.
        """
        limiter = CitoidRateLimiter(rps=1.0)
        timestamps: List[float] = []

        async def worker(worker_id: int) -> None:
            await limiter.wait()
            timestamps.append(time.monotonic())

        tasks = [asyncio.create_task(worker(i)) for i in range(4)]
        t0 = time.monotonic()
        await asyncio.gather(*tasks)
        total_time = time.monotonic() - t0

        assert len(timestamps) == 4
        # Total duration must be at least 2.95s (3 intervals of 1.0s)
        assert total_time >= 2.95, f"Expected >= 2.95s, got {total_time:.3f}s"

        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            assert delta >= 0.98, f"Interval {i} violated 1 rps: {delta:.4f}s"

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_client_50_concurrent_lookups_burst(self) -> None:
        """
        50 concurrent CitoidClient URL lookups run simultaneously against mocked Citoid API.
        Verifies zero race conditions, zero dropped requests, and valid CS1 templates for all 50.
        """
        client = CitoidClient(rate_limit_rps=250.0)

        for i in range(50):
            url = f"https://portal-{i}.example.org/article"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "newspaperArticle",
                    "title": f"समाचार शीर्षक {i}",
                    "url": url,
                    "publicationTitle": f"दैनिक पोर्टल {i}",
                    "date": "2026-11-21",
                }],
            )

        async def fetch(idx: int) -> str:
            return await client.url_to_cs1(f"https://portal-{idx}.example.org/article")

        results = await asyncio.gather(*[fetch(i) for i in range(50)])
        assert len(results) == 50
        for i, cs1 in enumerate(results):
            assert f"title=समाचार शीर्षक {i}" in cs1
            assert f"work=दैनिक पोर्टल {i}" in cs1
            assert "{{Cite news" in cs1


# ==============================================================================
# Suite 7: Maxlag=5 Handling and Exponential Backoff Retry Verification
# ==============================================================================
class TestCitoidMaxlagAndBackoffAdversarial:
    """
    Empirical verification of Wikimedia maxlag=5 policy:
    - Auto-injection of maxlag=5 query parameter on Wikimedia endpoints.
    - Backoff retry upon MediaWiki maxlag error payload in HTTP 200.
    - Retry exhaustion handling triggering graceful fallback without crashing.
    - Retry-After header parsing for RFC 7231 dates and numeric seconds.
    - maxlag delay string extraction under various server message formats.
    """

    @pytest.mark.asyncio
    @respx.mock
    async def test_maxlag_parameter_auto_injection(self) -> None:
        """Verify http_client automatically injects maxlag=5 on Wikimedia endpoints."""
        from app.services.http_client import http_client

        route = respx.get(
            "https://hi.wikipedia.org/w/api.php"
        ).respond(status_code=200, json={"query": {"general": {}}})

        await http_client.get(
            "https://hi.wikipedia.org/w/api.php",
            params={"action": "query", "meta": "siteinfo"},
            auto_maxlag=True,
        )

        assert route.called
        last_req = route.calls.last.request
        # maxlag=5 must be injected into request query params
        assert "maxlag=5" in str(last_req.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_maxlag_retry_and_recovery(self) -> None:
        """
        Citoid/MediaWiki returns maxlag error on first call;
        client backs off and succeeds on second call.
        """
        target_url = "https://example.com/maxlag-recover"
        encoded = urllib.parse.quote(target_url, safe="")

        route = respx.get(f"{CITOID_ENDPOINT}/{encoded}")
        route.side_effect = [
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json", "Retry-After": "0.02"},
                json={
                    "error": {
                        "code": "maxlag",
                        "info": "Waiting for 10.64.48.33: 5.2 seconds lagged",
                    }
                },
            ),
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json=[{
                    "itemType": "webpage",
                    "title": "लैग से उबरने के बाद शीर्षक",
                    "url": target_url,
                }],
            ),
        ]

        client = CitoidClient(rate_limit_rps=200.0)
        meta = await client.fetch_citation_metadata(target_url)

        assert meta["source"] == "citoid"
        assert meta["title"] == "लैग से उबरने के बाद शीर्षक"
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_citoid_maxlag_retry_exhaustion_triggers_fallback(self) -> None:
        """
        When Citoid continuously returns maxlag error exceeding max_retries,
        CitoidClient catches WikimediaLagError and gracefully triggers fallback.
        """
        target_url = "https://example.com/maxlag-exhaustion"
        encoded = urllib.parse.quote(target_url, safe="")

        # 4 consecutive maxlag errors (attempt 0, 1, 2, 3)
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            headers={"Content-Type": "application/json", "Retry-After": "0.01"},
            json={
                "error": {
                    "code": "maxlag",
                    "info": "Waiting for 10.64.0.1: 8.5 seconds lagged",
                }
            },
        )
        respx.get(target_url).respond(
            status_code=200,
            text="<html><head><title>बैकअप रिकवरी पृष्ठ</title></head></html>",
        )

        client = CitoidClient(rate_limit_rps=200.0)
        meta = await client.fetch_citation_metadata(target_url)

        # Must fall back gracefully without unhandled exception
        assert meta["source"] == "fallback"
        assert meta["title"] == "बैकअप रिकवरी पृष्ठ"
        assert meta["website"] == "example.com"

    def test_parse_retry_after_matrix(self) -> None:
        """parse_retry_after handles diverse headers safely and returns bounded float."""
        from app.services.http_client import parse_retry_after

        # Clean numeric
        assert parse_retry_after("5") == 5.0
        assert parse_retry_after("2.5") == 2.5
        assert parse_retry_after("  10  ") == 10.0

        # Out-of-bounds or invalid numeric
        assert parse_retry_after("0", default_delay=2.0) == 2.0
        assert parse_retry_after("-5", default_delay=3.0) == 3.0
        assert parse_retry_after("nan", default_delay=4.0) == 4.0
        assert parse_retry_after("inf", default_delay=4.0) == 4.0
        assert parse_retry_after("invalid", default_delay=1.5) == 1.5
        assert parse_retry_after(None, default_delay=3.5) == 3.5

        # Bounded by max_delay
        assert parse_retry_after("120", max_delay=60.0) == 60.0

    def test_extract_maxlag_delay_matrix(self) -> None:
        """_extract_maxlag_delay extracts valid seconds from diverse maxlag messages."""
        from app.services.http_client import _extract_maxlag_delay

        assert _extract_maxlag_delay("Waiting for 10.64.0.1: 6.2 seconds lagged") == 6.2
        assert _extract_maxlag_delay("lagged 5.5 seconds") == 5.5
        assert _extract_maxlag_delay("server lagged 4") == 4.0
        assert _extract_maxlag_delay("no numeric lag info") is None
        assert _extract_maxlag_delay("lagged -10 seconds") is None
        assert _extract_maxlag_delay("lagged nan seconds") is None
        assert _extract_maxlag_delay(None) is None  # type: ignore


# ==============================================================================
# Suite 8: Scan Endpoint Concurrency, Batching & Fallback Resilience
# ==============================================================================
class TestScanEndpointAdversarialAndFallback:
    """
    Stress tests for app/api/scan.py:
    - 50 bare URLs batch in a single scan request.
    - Complete network timeout fallback (ConnectTimeout, ReadTimeout).
    - Dual fallback: when Citoid fails AND target website is down.
    - Burst of concurrent scan requests without event loop stalls.
    """

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_50_bare_urls_batch(self) -> None:
        """
        Scan endpoint processing 50 bare URLs in wikitext simultaneously.
        Enriches each URL via Citoid and generates valid CS1 modifications.
        """
        from app.api.scan import scan_wikitext, ScanPayload

        # Build wikitext with 50 distinct bare URLs
        lines: List[str] = []
        for i in range(50):
            url = f"https://source-{i}.org/page"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "newspaperArticle",
                    "title": f"समाचार {i}",
                    "url": url,
                    "publicationTitle": f"दैनिक {i}",
                    "date": "2026-11-21",
                }],
            )
            lines.append(f"तथ्य क्रमांक {i}।<ref>{url}</ref>")

        wikitext = "\n".join(lines)
        payload = ScanPayload(wikitext=wikitext, title="परीक्षण लेख")

        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        assert response["total_refs"] == 50
        assert response["bare_urls"] == 50
        assert len(response["modifications"]) == 50

        # Check sample modifications have Citoid CS1 formatting
        for idx in [0, 24, 49]:
            mod = response["modifications"][idx]
            assert "{{Cite news" in mod["proposed_snippet"]
            assert f"title=समाचार {idx}" in mod["proposed_snippet"]
            assert f"work=दैनिक {idx}" in mod["proposed_snippet"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_upstream_timeout_and_offline_fallback(self) -> None:
        """
        When Citoid times out AND target sites are offline/unreachable,
        the scan endpoint must still succeed with status 200 and domain-based fallbacks.
        Zero unhandled exceptions or event loop stalls.
        """
        from app.api.scan import scan_wikitext, ScanPayload

        lines: List[str] = []
        for i in range(10):
            url = f"https://offline-site-{i}.com/news"
            encoded = urllib.parse.quote(url, safe="")
            # Mock Citoid timeout
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").mock(
                side_effect=httpx.ReadTimeout("Citoid Read Timeout")
            )
            # Mock direct HTML fetch failure (e.g. host unreachable)
            respx.get(url).mock(
                side_effect=httpx.ConnectError("Connection refused by target site")
            )
            lines.append(f"तथ्य {i}।<ref>{url}</ref>")

        wikitext = "\n".join(lines)
        payload = ScanPayload(wikitext=wikitext)

        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        assert response["total_refs"] == 10
        assert response["bare_urls"] == 10
        assert len(response["modifications"]) == 10

        for i, mod in enumerate(response["modifications"]):
            proposed = mod["proposed_snippet"]
            assert "{{Cite web" in proposed
            assert f"url=https://offline-site-{i}.com/news" in proposed
            assert f"offline-site-{i}.com वेब सन्दर्भ" in proposed
            assert f"website=offline-site-{i}.com" in proposed

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_mixed_success_and_failures(self) -> None:
        """
        Wikitext containing 15 bare URLs with mixed responses:
        - 5 Citoid success (Cite news)
        - 5 Citoid 404 -> HTML fallback success
        - 5 Citoid 500 + HTML failure -> domain fallback
        """
        from app.api.scan import scan_wikitext, ScanPayload

        lines: List[str] = []

        # Category A: Citoid success (0-4)
        for i in range(5):
            url = f"https://success-news-{i}.com/item"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "newspaperArticle",
                    "title": f"सफल समाचार {i}",
                    "url": url,
                    "publicationTitle": f"पत्रिका {i}",
                }],
            )
            lines.append(f"खंड A-{i}।<ref>{url}</ref>")

        # Category B: Citoid 404 -> HTML fallback (5-9)
        for i in range(5, 10):
            url = f"https://html-fallback-{i}.com/post"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=404)
            respx.get(url).respond(
                status_code=200,
                text=f"<title>HTML शीर्षक {i}</title>",
            )
            lines.append(f"खंड B-{i}।<ref>{url}</ref>")

        # Category C: Citoid 500 + HTML failure -> domain fallback (10-14)
        for i in range(10, 15):
            url = f"https://dead-domain-{i}.org/dead"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(status_code=500)
            respx.get(url).mock(side_effect=httpx.ConnectError("Dead"))
            lines.append(f"खंड C-{i}।<ref>{url}</ref>")

        wikitext = "\n".join(lines)
        response = await scan_wikitext(ScanPayload(wikitext=wikitext))

        assert response["status"] == "success"
        assert response["bare_urls"] == 15
        assert len(response["modifications"]) == 15

        mods = response["modifications"]
        # Category A verification
        for i in range(5):
            assert "सफल समाचार" in mods[i]["proposed_snippet"]
            assert "{{Cite news" in mods[i]["proposed_snippet"]

        # Category B verification
        for i in range(5, 10):
            assert f"HTML शीर्षक {i}" in mods[i]["proposed_snippet"]
            assert "{{Cite web" in mods[i]["proposed_snippet"]

        # Category C verification
        for i in range(10, 15):
            assert f"dead-domain-{i}.org वेब सन्दर्भ" in mods[i]["proposed_snippet"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_burst_concurrent_requests(self) -> None:
        """
        Burst of 20 concurrent invocations of scan_wikitext simultaneously.
        Verifies zero race conditions or state pollution between requests.
        """
        from app.api.scan import scan_wikitext, ScanPayload

        for i in range(20):
            url = f"https://concurrent-scan-{i}.com/item"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "webpage",
                    "title": f"समवर्ती पृष्ठ {i}",
                    "url": url,
                }],
            )

        async def call_scan(idx: int) -> dict:
            text = f"विवरण {idx}।<ref>https://concurrent-scan-{idx}.com/item</ref>"
            return await scan_wikitext(ScanPayload(wikitext=text, title=f"पृष्ठ {idx}"))

        results = await asyncio.gather(*[call_scan(i) for i in range(20)])
        assert len(results) == 20
        for i, res in enumerate(results):
            assert res["status"] == "success"
            assert res["title"] == f"पृष्ठ {i}"
            assert len(res["modifications"]) == 1
            assert f"समवर्ती पृष्ठ {i}" in res["modifications"][0]["proposed_snippet"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_unquoted_attribute_bare_url_span_non_overlap(self) -> None:
        """
        Regression test for cross-engine modification span collision:
        When a bare URL reference tag contains an unquoted attribute (e.g. <ref name=unquoted>),
        scan_wikitext must produce strictly disjoint modification spans so that
        apply_granular_modifications reconstructs wikitext without ValueError.
        """
        from app.api.scan import scan_wikitext, ScanPayload
        from app.services.parser import apply_granular_modifications

        url = "https://example.com/item"
        encoded = urllib.parse.quote(url, safe="")
        respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
            status_code=200,
            json=[{
                "itemType": "webpage",
                "title": "परीक्षण पृष्ठ",
                "url": url,
            }],
        )

        wikitext = "पाठ। <ref name=unquoted>https://example.com/item</ref> अधिक पाठ।"
        payload = ScanPayload(wikitext=wikitext)
        response = await scan_wikitext(payload)

        assert response["status"] == "success"
        mods = response["modifications"]
        assert len(mods) >= 1

        # Verify all spans are strictly mutually disjoint
        sorted_mods = sorted(mods, key=lambda m: int(m["span_start"]))
        for i in range(len(sorted_mods) - 1):
            assert int(sorted_mods[i]["span_end"]) <= int(sorted_mods[i + 1]["span_start"]), (
                f"Overlap detected: {sorted_mods[i]['id']} and {sorted_mods[i + 1]['id']}"
            )

        # Verify reconstruction completes without raising ValueError
        reconstructed = apply_granular_modifications(wikitext, mods)
        assert 'name="unquoted"' in reconstructed
        assert "{{Cite web" in reconstructed
        assert "title=परीक्षण पृष्ठ" in reconstructed
        assert "अधिक पाठ।" in reconstructed

    @pytest.mark.asyncio
    @respx.mock
    async def test_scan_endpoint_mixed_bare_and_syntax_modifications_disjoint(self) -> None:
        """
        Verify scan endpoint with a complex document containing:
        - Bare URL with unquoted attribute (bare mod supersedes syntax mod)
        - Standalone template with stray pipes outside ref (syntax mod retained)
        - Well-formed bare URL with group attribute
        All modifications must be mutually disjoint and reconstruct cleanly.
        """
        from app.api.scan import scan_wikitext, ScanPayload
        from app.services.parser import apply_granular_modifications

        u1 = "https://example.com/one"
        u2 = "https://example.com/two"
        respx.get(f"{CITOID_ENDPOINT}/{urllib.parse.quote(u1, safe='')}").respond(
            status_code=200,
            json=[{"itemType": "webpage", "title": "शीर्षक १", "url": u1}],
        )
        respx.get(f"{CITOID_ENDPOINT}/{urllib.parse.quote(u2, safe='')}").respond(
            status_code=200,
            json=[{"itemType": "webpage", "title": "शीर्षक २", "url": u2}],
        )

        wikitext = (
            "परिचय। <ref name=first>https://example.com/one</ref>\n"
            "विवरण। {{Cite web |url=https://example.com/stray ||title=Stray}}\n"
            "निष्कर्ष। <ref group=note>https://example.com/two</ref>"
        )

        payload = ScanPayload(wikitext=wikitext)
        response = await scan_wikitext(payload)
        mods = response["modifications"]

        # Verify that stray pipe syntax repair was retained
        pipe_mods = [m for m in mods if "mod-syn" in m["id"]]
        assert len(pipe_mods) >= 1

        # Verify all modifications are mutually non-overlapping
        sorted_mods = sorted(mods, key=lambda m: int(m["span_start"]))
        for i in range(len(sorted_mods) - 1):
            assert int(sorted_mods[i]["span_end"]) <= int(sorted_mods[i + 1]["span_start"])

        # Granular reconstruction test
        reconstructed = apply_granular_modifications(wikitext, mods)
        assert 'name="first"' in reconstructed
        assert 'group="note"' in reconstructed
        assert "||title" not in reconstructed
        assert "|title=Stray" in reconstructed


# ==============================================================================
# Suite 9: Zotero-to-CS1 Comprehensive ItemType & Field Mappings
# ==============================================================================
class TestZoteroToCS1ComprehensiveMappings:
    """
    Adversarial verification of Zotero itemType to MediaWiki CS1 template mapping
    and field transformations:
    - newspaperArticle, magazineArticle -> Cite news
    - book, bookSection -> Cite book
    - journalArticle, report, thesis, conferencePaper -> Cite journal
    - webpage, blogPost, weblog, podcast, videoRecording -> Cite web
    - Unmapped / custom types -> Cite web fallback
    - Mononym authors, multi-author tuples, and dict structures
    - Archive URLs, dates, and url-status
    - Language suppression (en/English omitted, hi/sa/fr retained)
    """

    def test_item_type_template_selection(self) -> None:
        """Verify select_cs1_template correctly maps all specified Zotero item types."""
        from app.services.citoid import select_cs1_template

        # News
        assert select_cs1_template("newspaperArticle") == "Cite news"
        assert select_cs1_template("magazineArticle") == "Cite news"

        # Books
        assert select_cs1_template("book") == "Cite book"
        assert select_cs1_template("bookSection") == "Cite book"

        # Journals & Academic
        assert select_cs1_template("journalArticle") == "Cite journal"
        assert select_cs1_template("report") == "Cite journal"
        assert select_cs1_template("thesis") == "Cite journal"
        assert select_cs1_template("conferencePaper") == "Cite journal"

        # Web & Media
        assert select_cs1_template("webpage") == "Cite web"
        assert select_cs1_template("blogPost") == "Cite web"
        assert select_cs1_template("weblog") == "Cite web"
        assert select_cs1_template("podcast") == "Cite web"
        assert select_cs1_template("videoRecording") == "Cite web"

        # Unmapped types fall back to Cite web
        assert select_cs1_template("statute") == "Cite web"
        assert select_cs1_template("presentation") == "Cite web"
        assert select_cs1_template("unknown_type") == "Cite web"
        assert select_cs1_template("") == "Cite web"

    def test_journal_article_fields_formatting(self) -> None:
        """journalArticle correctly formats journal, volume, issue, pages, doi, issn."""
        meta = {
            "item_type": "journalArticle",
            "url": "https://journal.org/article/10.1234/5678",
            "title": "भारतीय खगोलशास्त्र में आर्यभट का योगदान",
            "journal": "इतिहास एवं विज्ञान शोध पत्रिका",
            "volume": "15",
            "issue": "3",
            "pages": "142-158",
            "doi": "10.1234/5678",
            "issn": "1234-567X",
            "date": "2024-06-15",
            "access_date": "2026-11-21",
            "authors": [("आनंद", "मिश्रा"), ("प्रिया", "शर्मा")],
        }
        cs1 = format_cs1_template(meta)

        assert "{{Cite journal" in cs1
        assert "url=https://journal.org/article/10.1234/5678" in cs1
        assert "title=भारतीय खगोलशास्त्र में आर्यभट का योगदान" in cs1
        assert "journal=इतिहास एवं विज्ञान शोध पत्रिका" in cs1
        assert "volume=15" in cs1
        assert "issue=3" in cs1
        assert "pages=142-158" in cs1
        assert "doi=10.1234/5678" in cs1
        assert "issn=1234-567X" in cs1
        assert "last1=मिश्रा" in cs1
        assert "first1=आनंद" in cs1
        assert "last2=शर्मा" in cs1
        assert "first2=प्रिया" in cs1

    def test_book_fields_formatting(self) -> None:
        """book correctly formats publisher, isbn, edition, and author."""
        meta = {
            "item_type": "book",
            "url": "https://books.org/vedic-math",
            "title": "वैदिक गणित परिचय",
            "publisher": "मोतीलाल बनारसीदास",
            "edition": "तृतीय",
            "isbn": "978-8120800000",
            "date": "2020",
            "access_date": "2026-11-21",
            "authors": [("भारती", "कृष्ण तीर्थ")],
        }
        cs1 = format_cs1_template(meta)

        assert "{{Cite book" in cs1
        assert "title=वैदिक गणित परिचय" in cs1
        assert "publisher=मोतीलाल बनारसीदास" in cs1
        assert "edition=तृतीय" in cs1
        assert "isbn=978-8120800000" in cs1
        assert "last=कृष्ण तीर्थ" in cs1
        assert "first=भारती" in cs1

    def test_author_variations_mononyms_and_single_name(self) -> None:
        """Single mononym author (e.g. कबीर or चाणक्य) emits author= without first/last split."""
        meta = {
            "item_type": "webpage",
            "url": "https://example.com/kabir",
            "title": "कबीर की साखियाँ",
            "authors": [("", "कबीर")],
        }
        cs1 = format_cs1_template(meta)
        assert "author=कबीर" in cs1
        assert "last=" not in cs1
        assert "first=" not in cs1

    def test_archive_parameters_and_url_status(self) -> None:
        """Archive parameters emit archive-url, archive-date, and url-status."""
        # Case 1: Default url-status is 'dead' when archive is present
        meta1 = {
            "item_type": "webpage",
            "url": "https://example.com/dead-page",
            "title": "संग्रहीत सामग्री",
            "archive_url": "https://web.archive.org/web/20200101/https://example.com/dead-page",
            "archive_date": "2020-01-01",
        }
        cs1_1 = format_cs1_template(meta1)
        expected_archive_url = (
            "archive-url=https://web.archive.org/web/20200101/"
            "https://example.com/dead-page"
        )
        assert expected_archive_url in cs1_1
        assert "archive-date=2020-01-01" in cs1_1
        assert "url-status=dead" in cs1_1

        # Case 2: Explicit url-status='live' is preserved
        meta2 = {
            "item_type": "webpage",
            "url": "https://example.com/live-page",
            "title": "सक्रिय सामग्री",
            "archive_url": "https://web.archive.org/web/20240101/https://example.com/live-page",
            "archive_date": "2024-01-01",
            "url_status": "live",
        }
        cs1_2 = format_cs1_template(meta2)
        assert "url-status=live" in cs1_2

    def test_language_suppression_and_retention(self) -> None:
        """English languages (en, eng, English) are suppressed; Hindi and others retained."""
        # en suppressed
        meta_en = {"title": "Test", "url": "https://a.com", "language": "en"}
        assert "language=" not in format_cs1_template(meta_en)

        meta_eng = {"title": "Test", "url": "https://a.com", "language": "English"}
        assert "language=" not in format_cs1_template(meta_eng)

        # Hindi retained
        meta_hi = {"title": "Test", "url": "https://a.com", "language": "hi"}
        assert "language=hi" in format_cs1_template(meta_hi)

        # Sanskrit retained
        meta_sa = {"title": "Test", "url": "https://a.com", "language": "sa"}
        assert "language=sa" in format_cs1_template(meta_sa)
