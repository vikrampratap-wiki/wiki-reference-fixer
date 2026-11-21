"""
Duplicate Ref Merger (Companion Helper 1)
Identifies references with identical content/URLs across Wikitext and proposes consolidation
into a single named reference (<ref name="name">...</ref>) at the first occurrence
and self-closing reference tags (<ref name="name" />) at subsequent occurrences.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.parser import parse_wikitext_references, ParsedDocument, ParsedReference


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

    # 1. Collect all pre-existing reference names to prevent collisions
    used_names: Set[str] = {
        ref.name.strip('"\'')
        for ref in parsed_doc.references
        if ref.name and ref.name.strip()
    }

    slug_counter = 1

    def generate_unique_slug() -> str:
        nonlocal slug_counter
        while f"ref_{slug_counter}" in used_names:
            slug_counter += 1
        slug = f"ref_{slug_counter}"
        used_names.add(slug)
        slug_counter += 1
        return slug

    # 2. Group non-empty reference bodies by (body, group)
    groups: Dict[Tuple[str, Optional[str]], List[ParsedReference]] = {}
    for ref in parsed_doc.references:
        body = ref.inner_content.strip()
        if not body:
            continue
        clean_group = ref.group.strip('"\'') if ref.group and ref.group.strip() else None
        groups.setdefault((body, clean_group), []).append(ref)

    merges: List[Dict[str, Any]] = []

    for (body, group), ref_list in groups.items():
        if len(ref_list) < 2:
            continue

        group_attr = f' group="{group}"' if group else ""

        # Determine target name: prefer first pre-existing name, else generate a safe slug
        target_name: Optional[str] = None
        for r in ref_list:
            if r.name and r.name.strip():
                target_name = r.name.strip('"\'')
                break

        if not target_name:
            target_name = generate_unique_slug()

        # First instance should be the definition
        first_ref = ref_list[0]
        first_clean_name = (
            first_ref.name.strip('"\'')
            if first_ref.name and first_ref.name.strip()
            else None
        )

        if first_clean_name != target_name or first_ref.self_closing:
            # Must convert first instance into a named full definition
            merges.append({
                "original": first_ref.raw_tag,
                "replacement": f'<ref name="{target_name}"{group_attr}>{body}</ref>',
                "target_name": target_name,
                "span_start": first_ref.span_start,
                "span_end": first_ref.span_end,
                "content": body,
                "is_definition": True,
            })

        # Subsequent instances become self-closing citations
        for subsequent_ref in ref_list[1:]:
            sub_clean_name = (
                subsequent_ref.name.strip('"\'')
                if subsequent_ref.name and subsequent_ref.name.strip()
                else None
            )
            # If already self-closing with the exact target name, skip
            if subsequent_ref.self_closing and sub_clean_name == target_name:
                continue

            merges.append({
                "original": subsequent_ref.raw_tag,
                "replacement": f'<ref name="{target_name}"{group_attr} />',
                "target_name": target_name,
                "span_start": subsequent_ref.span_start,
                "span_end": subsequent_ref.span_end,
                "content": body,
                "is_definition": False,
            })

    # Count how many duplicate references were found
    duplicates_count = sum(len(refs) - 1 for refs in groups.values() if len(refs) >= 2)

    return {
        "duplicates_found": duplicates_count,
        "proposed_merges": merges,
    }


def apply_duplicate_merges(wikitext: str, proposed_merges: List[Dict[str, Any]]) -> str:
    """
    Applies proposed duplicate reference merges in descending span order.
    """
    sorted_merges = sorted(
        [m for m in proposed_merges if "span_start" in m and "span_end" in m],
        key=lambda m: m["span_start"],
        reverse=True,
    )
    result = wikitext
    for m in sorted_merges:
        start = m["span_start"]
        end = m["span_end"]
        if result[start:end] == m["original"]:
            result = result[:start] + m["replacement"] + result[end:]
    return result


__all__ = [
    "find_duplicate_references",
    "apply_duplicate_merges",
]
