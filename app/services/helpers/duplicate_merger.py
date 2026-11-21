"""
Duplicate Ref Merger (Companion Helper 1)
Identifies references with identical content/URLs across Wikitext and proposes consolidation
into a single named reference (<ref name="name">...</ref>) at the first occurrence
and self-closing reference tags (<ref name="name" />) at subsequent occurrences.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.parser import parse_wikitext_references, ParsedDocument


def find_duplicate_references(
    wikitext: str, parsed_doc: Optional[ParsedDocument] = None
) -> Dict[str, Any]:
    """
    Scans wikitext for duplicate reference contents and proposes consolidation.
    Returns:
        {
            "duplicates_found": int,
            "proposed_merges": List[Dict[str, Any]],
        }
    """
    if parsed_doc is None:
        parsed_doc = parse_wikitext_references(wikitext)

    # Track seen bodies and their designated reference names
    seen_content: Dict[str, str] = {}
    merges: List[Dict[str, Any]] = []
    slug_counter = 1

    # Also map any pre-existing ref names
    for ref in parsed_doc.references:
        body = ref.inner_content.strip()
        if not body:
            continue
        if ref.name and ref.name.strip():
            # Use existing named reference as target name if available
            clean_name = ref.name.strip('"\'')
            if body not in seen_content:
                seen_content[body] = clean_name

    for ref in parsed_doc.references:
        body = ref.inner_content.strip()
        if not body:
            continue

        raw = ref.raw_tag
        if body in seen_content:
            existing_name = seen_content[body]
            # If this is already a self-closing reference with the same name, skip
            if ref.self_closing and (ref.name and ref.name.strip('"\'') == existing_name):
                continue
            # If this is the definition itself, do not propose merging onto itself
            if not ref.self_closing and ref.name and ref.name.strip('"\'') == existing_name:
                # First instance with the name, keep as definition
                continue

            merges.append({
                "original": raw,
                "replacement": f'<ref name="{existing_name}" />',
                "target_name": existing_name,
                "span_start": ref.span_start,
                "span_end": ref.span_end,
                "content": body,
            })
        else:
            name_slug = f"ref_{slug_counter}"
            seen_content[body] = name_slug
            slug_counter += 1

    return {
        "duplicates_found": len(merges),
        "proposed_merges": merges,
    }


def apply_duplicate_merges(wikitext: str, proposed_merges: List[Dict[str, Any]]) -> str:
    """
    Applies proposed duplicate reference merges in descending span order.
    """
    # Sort merges in descending order of span_start to prevent index drift
    sorted_merges = sorted(
        [m for m in proposed_merges if "span_start" in m and "span_end" in m],
        key=lambda m: m["span_start"],
        reverse=True,
    )
    result = wikitext
    for m in sorted_merges:
        start = m["span_start"]
        end = m["span_end"]
        # Verify original text matches
        if result[start:end] == m["original"]:
            result = result[:start] + m["replacement"] + result[end:]
    return result
