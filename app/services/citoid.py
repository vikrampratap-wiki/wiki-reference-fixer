"""
Wikimedia Citoid REST API Integration for Hindi Wikipedia.
Handles asynchronous metadata extraction, Zotero-to-CS1 mapping,
1 rps rate limiting, maxlag=5 handling, and HTML title/domain fallback.
Authoritative Source: ORIGINAL_REQUEST.md (§3, R1, R5), PROJECT.md (§Feature 9).
"""

from __future__ import annotations

import asyncio
import datetime
import html
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

import httpx

from app.core.config import get_settings
from app.services.http_client import WikimediaHttpClient, http_client

logger = logging.getLogger(__name__)

# Template mapping from Zotero itemType to MediaWiki CS1 template name
ZOTERO_ITEM_TYPE_TO_CS1: Dict[str, str] = {
    "webpage": "Cite web",
    "blogPost": "Cite web",
    "weblog": "Cite web",
    "podcast": "Cite web",
    "videoRecording": "Cite web",
    "newspaperArticle": "Cite news",
    "magazineArticle": "Cite news",
    "journalArticle": "Cite journal",
    "report": "Cite journal",
    "thesis": "Cite journal",
    "conferencePaper": "Cite journal",
    "book": "Cite book",
    "bookSection": "Cite book",
}


def select_cs1_template(item_type: str) -> str:
    """
    Map Zotero itemType string to the corresponding MediaWiki CS1 template.
    Defaults to 'Cite web' if item_type is unmapped or unknown.
    """
    return ZOTERO_ITEM_TYPE_TO_CS1.get(item_type.strip(), "Cite web")


class CitoidRateLimiter:
    """
    Asynchronous rate limiter enforcing request spacing.
    Strictly complies with Wikimedia Citoid 1 rps policy.
    Loop-aware to support multiple event loops without cross-loop lock binding.
    """

    def __init__(self, rps: float = 1.0) -> None:
        self.interval: float = 1.0 / rps if rps > 0 else 0.0
        self._last_call: float = 0.0
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock bound to the current running event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or (
            loop is not None and getattr(self._lock, "_loop", None) not in (None, loop)
        ):
            self._lock = asyncio.Lock()
        return self._lock

    async def wait(self) -> None:
        """Wait until sufficient interval has elapsed since the last request."""
        if self.interval <= 0:
            return
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self._last_call = time.monotonic()


def _clean_text(val: Optional[str]) -> Optional[str]:
    """Clean whitespace, newlines, and unescape HTML entities in text strings."""
    if not val:
        return None
    unescaped = html.unescape(val)
    cleaned = re.sub(r"\s+", " ", unescaped).strip()
    return cleaned if cleaned else None


def _clean_domain(url: str) -> str:
    """Extract clean domain/hostname from URL, stripping www. prefix."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or ""
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return ""


def _parse_authors(raw_authors: Any) -> List[Tuple[str, str]]:
    """
    Parse author field into a list of (first_name, last_name) tuples.
    Handles lists of [first, last], dicts with firstName/lastName,
    and single name strings.
    """
    authors: List[Tuple[str, str]] = []
    if not raw_authors:
        return authors

    if isinstance(raw_authors, str):
        cleaned = _clean_text(raw_authors)
        if cleaned:
            authors.append(("", cleaned))
        return authors

    if isinstance(raw_authors, list):
        for item in raw_authors:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                first = _clean_text(str(item[0])) or ""
                last = _clean_text(str(item[1])) or ""
                if first or last:
                    authors.append((first, last))
            elif isinstance(item, (list, tuple)) and len(item) == 1:
                last = _clean_text(str(item[0])) or ""
                if last:
                    authors.append(("", last))
            elif isinstance(item, dict):
                first = _clean_text(item.get("firstName") or "") or ""
                last = (
                    _clean_text(item.get("lastName") or item.get("name") or "")
                    or ""
                )
                if first or last:
                    authors.append((first, last))
            elif isinstance(item, str):
                cleaned = _clean_text(item)
                if cleaned:
                    authors.append(("", cleaned))

    return authors


def _first_element_or_str(val: Any) -> Optional[str]:
    """Return first string element if list, else string representation."""
    if val is None:
        return None
    if isinstance(val, list):
        for elem in val:
            cleaned = _clean_text(str(elem))
            if cleaned:
                return cleaned
        return None
    return _clean_text(str(val))


def format_cs1_template(meta: Dict[str, Any]) -> str:
    """
    Format citation metadata dictionary into standard MediaWiki CS1 template wikitext.
    Emits canonical English parameter keys to guarantee 100% clean rendering
    on Hindi Wikipedia without 'Empty citation' Lua errors.
    """
    template_name = select_cs1_template(meta.get("item_type", "webpage"))
    params: List[str] = [template_name]

    url = meta.get("url")
    if url:
        params.append(f"url={url}")

    title = meta.get("title")
    if title:
        params.append(f"title={title}")

    # Authors formatting
    authors: List[Tuple[str, str]] = meta.get("authors") or []
    if len(authors) == 1:
        first, last = authors[0]
        if last and first:
            params.append(f"last={last}")
            params.append(f"first={first}")
        elif last:
            params.append(f"author={last}")
        elif first:
            params.append(f"author={first}")
    elif len(authors) > 1:
        for idx, (first, last) in enumerate(authors, start=1):
            if last and first:
                params.append(f"last{idx}={last}")
                params.append(f"first{idx}={first}")
            elif last:
                params.append(f"author{idx}={last}")
            elif first:
                params.append(f"author{idx}={first}")

    # Work / Website / Publication title
    if template_name == "Cite journal":
        journal = meta.get("journal") or meta.get("work") or meta.get("website")
        if journal:
            params.append(f"journal={journal}")
    elif template_name == "Cite news":
        work = meta.get("work") or meta.get("website") or meta.get("publisher")
        if work:
            params.append(f"work={work}")
    elif meta.get("website"):
        params.append(f"website={meta['website']}")

    if meta.get("publisher"):
        # For Cite news, avoid duplicate work and publisher if identical
        if template_name != "Cite news" or meta.get("publisher") != meta.get("work"):
            params.append(f"publisher={meta['publisher']}")

    if meta.get("date"):
        params.append(f"date={meta['date']}")

    if meta.get("access_date"):
        params.append(f"access-date={meta['access_date']}")

    language = meta.get("language")
    if language and str(language).lower() not in ("en", "eng", "english"):
        params.append(f"language={language}")

    # Journal / Book identifiers
    if meta.get("volume"):
        params.append(f"volume={meta['volume']}")
    if meta.get("issue"):
        params.append(f"issue={meta['issue']}")
    if meta.get("pages"):
        params.append(f"pages={meta['pages']}")
    if meta.get("edition"):
        params.append(f"edition={meta['edition']}")
    if meta.get("doi"):
        params.append(f"doi={meta['doi']}")
    if meta.get("isbn"):
        params.append(f"isbn={meta['isbn']}")
    if meta.get("issn"):
        params.append(f"issn={meta['issn']}")

    # Archive parameters
    if meta.get("archive_url"):
        params.append(f"archive-url={meta['archive_url']}")
        if meta.get("archive_date"):
            params.append(f"archive-date={meta['archive_date']}")
        params.append(f"url-status={meta.get('url_status', 'dead')}")

    return "{{" + " |".join(params) + "}}"


class CitoidClient:
    """
    Wikimedia Citoid REST API Client.
    Provides paced querying of Wikimedia's Citoid service, maxlag backoff,
    Zotero-to-CS1 metadata normalization, and dual fallback to HTML title/domain.
    """

    def __init__(
        self,
        client: Optional[WikimediaHttpClient] = None,
        rate_limit_rps: float = 1.0,
    ) -> None:
        self._http_client = client or http_client
        self.rate_limiter = CitoidRateLimiter(rps=rate_limit_rps)

    def _normalize_citoid_entry(
        self,
        entry: Dict[str, Any],
        original_url: str,
    ) -> Dict[str, Any]:
        """Normalize a raw Citoid Zotero JSON object into standard citation metadata."""
        item_type = str(entry.get("itemType", "webpage"))
        url = _clean_text(entry.get("url")) or original_url
        title = _clean_text(entry.get("title")) or "वेब सन्दर्भ"

        website = (
            _clean_text(entry.get("websiteTitle"))
            or _clean_text(entry.get("publicationTitle"))
        )
        work = (
            _clean_text(entry.get("publicationTitle"))
            or _clean_text(entry.get("websiteTitle"))
        )
        publisher = _clean_text(entry.get("publisher"))
        date = _clean_text(entry.get("date"))

        access_date = _clean_text(entry.get("accessDate"))
        if not access_date:
            access_date = datetime.date.today().isoformat()

        authors = _parse_authors(entry.get("author"))
        doi = _clean_text(entry.get("DOI"))
        isbn = _first_element_or_str(entry.get("ISBN"))
        issn = _first_element_or_str(entry.get("ISSN"))
        volume = _clean_text(entry.get("volume"))
        issue = _clean_text(entry.get("issue"))
        pages = _clean_text(entry.get("pages"))
        edition = _clean_text(entry.get("edition"))
        language = _clean_text(entry.get("language"))

        archive_url = _clean_text(entry.get("archiveUrl"))
        archive_date = _clean_text(entry.get("archiveDate"))
        url_status = _clean_text(entry.get("urlStatus"))
        if archive_url and not url_status:
            url_status = "dead"

        return {
            "item_type": item_type,
            "url": url,
            "title": title,
            "website": website,
            "work": work,
            "journal": work,
            "publisher": publisher,
            "date": date,
            "access_date": access_date,
            "authors": authors,
            "doi": doi,
            "isbn": isbn,
            "issn": issn,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "edition": edition,
            "language": language,
            "archive_url": archive_url,
            "archive_date": archive_date,
            "url_status": url_status,
            "source": "citoid",
        }

    async def _extract_html_fallback(
        self,
        url: str,
        fallback_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dual fallback when Citoid returns 400, 404, or fails:
        Fetches web page HTML to extract <title> and domain hostname.
        """
        domain = _clean_domain(url)
        extracted_title: Optional[str] = None

        try:
            # Perform lightweight GET request to read page <title>
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            }
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=True, headers=headers
            ) as direct_client:
                resp = await direct_client.get(url)
                if resp.status_code == 200:
                    text = resp.text
                    # Search for <title> tag
                    title_match = re.search(
                        r"<title[^>]*>(.*?)</title>",
                        text,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if title_match:
                        extracted_title = _clean_text(title_match.group(1))

                    # If title not found, search for og:title
                    if not extracted_title:
                        og_match = re.search(
                            r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
                            text,
                            re.IGNORECASE,
                        )
                        if og_match:
                            extracted_title = _clean_text(og_match.group(1))
        except Exception as exc:
            logger.debug(
                "Direct HTML title extraction failed for %s: %s", url, exc
            )

        final_title = (
            extracted_title
            or fallback_title
            or (f"{domain} वेब सन्दर्भ" if domain else "वेब सन्दर्भ")
        )

        today_str = datetime.date.today().isoformat()
        return {
            "item_type": "webpage",
            "url": url,
            "title": final_title,
            "website": domain if domain else None,
            "work": domain if domain else None,
            "journal": None,
            "publisher": domain if domain else None,
            "date": None,
            "access_date": today_str,
            "authors": [],
            "doi": None,
            "isbn": None,
            "issn": None,
            "volume": None,
            "issue": None,
            "pages": None,
            "edition": None,
            "language": None,
            "archive_url": None,
            "archive_date": None,
            "url_status": None,
            "source": "fallback",
        }

    async def fetch_citation_metadata(
        self,
        url_or_identifier: str,
        fallback_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch citation metadata from Wikimedia Citoid REST API with 1 rps pacing.
        Falls back to HTML <title> tag and domain on 400/404 or connection failures.
        """
        clean_target = url_or_identifier.strip()
        encoded = urllib.parse.quote(clean_target, safe="")
        settings = get_settings()
        base_url = settings.CITOID_API_URL.rstrip("/")
        endpoint = f"{base_url}/{encoded}"

        headers = {
            "User-Agent": settings.WIKIMEDIA_USER_AGENT,
            "Api-User-Agent": settings.WIKIMEDIA_USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "hi, en;q=0.8",
        }

        try:
            await self.rate_limiter.wait()
            response = await self._http_client.get(
                endpoint,
                headers=headers,
                auto_maxlag=True,
            )

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    entry = data[0]
                    if isinstance(entry, dict):
                        return self._normalize_citoid_entry(
                            entry, clean_target
                        )

            logger.info(
                "Citoid returned status %d for %s; triggering fallback.",
                response.status_code,
                clean_target,
            )
            return await self._extract_html_fallback(
                clean_target, fallback_title=fallback_title
            )
        except Exception as exc:
            logger.warning(
                "Citoid API request exception for %s (%s); triggering fallback.",
                clean_target,
                exc,
            )
            return await self._extract_html_fallback(
                clean_target, fallback_title=fallback_title
            )

    async def url_to_cs1(
        self,
        url: str,
        fallback_title: Optional[str] = None,
    ) -> str:
        """Fetch metadata for URL and format into standard CS1 template string."""
        meta = await self.fetch_citation_metadata(
            url, fallback_title=fallback_title
        )
        return format_cs1_template(meta)

    def format_cs1_template(self, meta: Dict[str, Any]) -> str:
        """Format metadata into CS1 template string."""
        return format_cs1_template(meta)


# Singleton Citoid client instance
citoid_client = CitoidClient()


async def fetch_citation_metadata(
    url_or_identifier: str,
    fallback_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Module-level convenience function querying the global Citoid client."""
    return await citoid_client.fetch_citation_metadata(
        url_or_identifier, fallback_title=fallback_title
    )


async def url_to_cs1(
    url: str,
    fallback_title: Optional[str] = None,
) -> str:
    """Module-level convenience function transforming URL to CS1 template."""
    return await citoid_client.url_to_cs1(
        url, fallback_title=fallback_title
    )


__all__ = [
    "CitoidClient",
    "citoid_client",
    "CitoidRateLimiter",
    "fetch_citation_metadata",
    "format_cs1_template",
    "select_cs1_template",
    "url_to_cs1",
    "ZOTERO_ITEM_TYPE_TO_CS1",
]
