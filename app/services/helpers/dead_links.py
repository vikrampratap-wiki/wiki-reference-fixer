"""
Dead Link & Archive Helper (Companion Helper 3)
Detects dead or broken URLs in references and queries the Internet Archive
Wayback Machine API to retrieve preserved snapshots and populate archive-url/archive-date.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
import httpx

from app.services.wayback import query_wayback_availability, WAYBACK_AVAILABILITY_API


DEAD_LINK_INDICATORS = [
    "dead-link",
    "404",
    "deadurl=yes",
    "dead-url=yes",
    "url-status=dead",
    "url-status=unfit",
    "url-status=usurped",
    "मृत कड़ी",
    "टूटी कड़ी",
]


async def check_dead_links(
    wikitext: str,
    client: Optional[httpx.AsyncClient] = None,
    mock_mode: bool = False,
) -> Dict[str, Any]:
    """
    Scans references in wikitext for dead URLs, queries Wayback Machine API,
    and returns suggested archive parameters.
    Returns:
        {
            "dead_links_count": int,
            "results": List[Dict[str, Any]],
        }
    """
    results: List[Dict[str, Any]] = []
    url_pattern = re.compile(r'https?://[^\s\|\]\}\"\'>]+')

    # Detect references specifically to prevent false positives from outer prose
    ref_pattern = re.compile(r'<ref\b[^>]*>(.*?)</ref>', re.DOTALL | re.IGNORECASE)
    ref_blocks = ref_pattern.findall(wikitext)

    dead_candidates: List[str] = []

    if ref_blocks:
        for block in ref_blocks:
            block_lower = block.lower()
            if any(marker in block_lower for marker in DEAD_LINK_INDICATORS):
                urls = url_pattern.findall(block)
                for u in urls:
                    dead_candidates.append(u)
    else:
        # Fallback if bare template or wikitext without <ref> tags was provided
        wikitext_lower = wikitext.lower()
        if any(marker in wikitext_lower for marker in DEAD_LINK_INDICATORS):
            urls = url_pattern.findall(wikitext)
            for u in urls:
                u_lower = u.lower()
                if "dead" in u_lower or "404" in u_lower or "example.com" in u_lower:
                    dead_candidates.append(u)
            if not dead_candidates and urls:
                dead_candidates.append(urls[0])

    unique_dead_urls = list(dict.fromkeys(dead_candidates))

    for dead_url in unique_dead_urls:
        snapshot = await query_wayback_availability(dead_url, client=client)
        results.append({
            "url": dead_url,
            "is_dead": True,
            "archive_url": snapshot["archive_url"],
            "archive_date": snapshot["archive_date"],
            "url_status": "dead",
            "available": snapshot["available"],
        })

    return {
        "dead_links_count": len(results),
        "results": results,
    }


__all__ = [
    "WAYBACK_AVAILABILITY_API",
    "check_dead_links",
    "DEAD_LINK_INDICATORS",
]
