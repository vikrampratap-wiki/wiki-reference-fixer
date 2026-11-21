"""
Unreliable Domain Advisory Flag (Companion Helper 8)
Detects URLs from user-generated content, blogs, or social networks (per WP:RSP)
and flags them with an advisory badge without deleting or altering the source.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, List


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
    Scans references in wikitext for unreliable or UGC domains.
    Returns:
        {
            "flagged_domains_count": int,
            "flagged": List[Dict[str, Any]],
        }
    """
    flagged: List[Dict[str, Any]] = []

    for domain in UNRELIABLE_DOMAINS:
        if domain in wikitext.lower():
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
