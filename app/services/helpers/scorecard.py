"""
Reference Health Scorecard (Companion Helper 5)
Calculates reference health score (0-100) based on bare URLs, missing templates,
syntax issues, and formatting quality.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.parser import parse_wikitext_references, ParsedDocument


def calculate_reference_scorecard(
    wikitext: str, parsed_doc: Optional[ParsedDocument] = None
) -> Dict[str, Any]:
    """
    Computes reference health score (0-100) and analytical metrics for an article.
    Returns:
        {
            "health_score": int,
            "total_refs": int,
            "bare_urls": int,
            "formatted_citations": int,
            "status": "उत्कृष्ट" | "मध्यम" | "खराब",
            "grade": "A" | "B" | "C" | "D",
        }
    """
    if parsed_doc is None:
        parsed_doc = parse_wikitext_references(wikitext)

    total_refs = parsed_doc.total_refs
    bare_count = len(parsed_doc.bare_url_refs)

    # If no references exist at all
    if total_refs == 0:
        return {
            "health_score": 100,
            "total_refs": 0,
            "bare_urls": 0,
            "formatted_citations": 0,
            "status": "उत्कृष्ट",
            "grade": "A",
        }

    # Deduct points for bare URLs
    score = 100
    if total_refs > 0:
        score -= min(bare_count * 20, 70)

    # Deduct for missing reflist container
    if not parsed_doc.has_reflist:
        score = max(0, score - 20)

    # Deduct for duplicate name conflicts
    if parsed_doc.duplicate_name_conflicts:
        score = max(0, score - (len(parsed_doc.duplicate_name_conflicts) * 15))

    score = max(0, min(100, score))

    if score >= 80:
        status = "उत्कृष्ट"
        grade = "A"
    elif score >= 50:
        status = "मध्यम"
        grade = "B"
    else:
        status = "खराब"
        grade = "C"

    formatted_count = total_refs - bare_count

    return {
        "health_score": score,
        "total_refs": total_refs,
        "bare_urls": bare_count,
        "formatted_citations": max(0, formatted_count),
        "status": status,
        "grade": grade,
    }
