"""
Unreliable Domain Advisory Flag (Companion Helper 8)
Detects URLs from user-generated content, blogs, or social networks (per WP:RSP)
and flags them with an advisory badge without deleting or altering the source.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set


UNRELIABLE_DOMAINS: List[str] = [
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "quora.com",
    "reddit.com",
    "blogspot.com",
    "wordpress.com",
    "medium.com",
    "tiktok.com",
    "pinterest.com",
    "linkedin.com",
    "weebly.com",
    "wixsite.com",
    "tumblr.com",
]


def check_unreliable_domains(wikitext: str) -> Dict[str, Any]:
    """
    Scans references in wikitext for unreliable or UGC domains using
    hostname parsing and word boundaries rather than raw substring matching.
    Returns:
        {
            "flagged_domains_count": int,
            "flagged": List[Dict[str, Any]],
        }
    """
    flagged: List[Dict[str, Any]] = []

    # Extract all hostnames from URLs in the wikitext
    url_pattern = re.compile(r'https?://([^\s/\|\]\}\"\'>]+)', re.IGNORECASE)
    found_raw = url_pattern.findall(wikitext)
    found_hostnames: Set[str] = set()
    for raw in found_raw:
        host = raw.split(":")[0].strip().lower()
        if host:
            found_hostnames.add(host)

    for domain in UNRELIABLE_DOMAINS:
        # 1. Match domain or subdomains (e.g., twitter.com or mobile.twitter.com)
        is_in_hosts = any(
            h == domain or h.endswith("." + domain)
            for h in found_hostnames
        )

        # 2. Or match domain with strict word boundaries in case of bare domain references
        wb_pattern = re.compile(
            r'(?<![a-zA-Z0-9.-])' + re.escape(domain) + r'(?![a-zA-Z0-9.-])',
            re.IGNORECASE,
        )
        is_in_wb = bool(wb_pattern.search(wikitext))

        if is_in_hosts or is_in_wb:
            flagged.append({
                "domain": domain,
                "warning": f"अविश्वसनीय / सोशल मीडिया स्रोत ({domain}) पर सलाहकार बैज",
                "policy": "WP:RSP",
                "severity": "advisory",
                "message": (
                    f"डोमेन '{domain}' विकिपीडिया नीति (WP:RSP) के तहत प्रायः गैर-विश्वसनीय या "
                    "सामग्री माना जाता है। कृपया प्रतिष्ठित स्रोत से पुष्टि करें।"
                ),
            })

    return {
        "flagged_domains_count": len(flagged),
        "flagged": flagged,
    }


__all__ = [
    "UNRELIABLE_DOMAINS",
    "check_unreliable_domains",
]
