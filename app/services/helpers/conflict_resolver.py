"""
Duplicate Name Conflict Resolver (Companion Helper 2)
Detects cases where references share the same name attribute but contain conflicting,
distinct bodies, preventing silent data loss and alerting the user.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.parser import parse_wikitext_references, ParsedDocument, ParsedReference


def detect_ref_conflicts(
    wikitext: str, parsed_doc: Optional[ParsedDocument] = None
) -> Dict[str, Any]:
    """
    Scans wikitext for conflicting reference contents defined under the identical name attribute.
    Scopes reference names per group, returns span coordinates, and provides suggested renames.
    Returns:
        {
            "conflicts_count": int,
            "conflicts": List[Dict[str, Any]],
        }
    """
    if parsed_doc is None:
        parsed_doc = parse_wikitext_references(wikitext)

    # Group defined references by (name, group)
    named_refs: Dict[Tuple[str, Optional[str]], List[ParsedReference]] = {}

    for ref in parsed_doc.references:
        if ref.name and ref.name.strip() and not ref.self_closing:
            val = ref.name.strip('"\'')
            body = ref.inner_content.strip()
            clean_group = ref.group.strip('"\'') if ref.group and ref.group.strip() else None
            # Only count non-empty bodies (definitions)
            if body:
                named_refs.setdefault((val, clean_group), []).append(ref)

    conflicts: List[Dict[str, Any]] = []
    for (name, group), ref_list in named_refs.items():
        # Unique bodies preserving insertion order
        unique_bodies = list(dict.fromkeys(r.inner_content.strip() for r in ref_list))
        if len(unique_bodies) > 1:
            suggested_renames = [f"{name}_{i+1}" for i in range(len(unique_bodies))]
            body_to_rename = {b: suggested_renames[i] for i, b in enumerate(unique_bodies)}

            group_attr = f' group="{group}"' if group else ""
            spans: List[Dict[str, Any]] = []
            for r in ref_list:
                b = r.inner_content.strip()
                new_name = body_to_rename.get(b, name)
                spans.append({
                    "ref_id": r.id,
                    "span_start": r.span_start,
                    "span_end": r.span_end,
                    "original": r.raw_tag,
                    "replacement": f'<ref name="{new_name}"{group_attr}>{b}</ref>',
                    "content": b,
                    "suggested_name": new_name,
                })

            conflict_dict: Dict[str, Any] = {
                "ref_name": name,
                "group": group,
                "conflicting_contents": unique_bodies,
                "message": f"सन्दर्भ नाम '{name}' में भिन्न सामग्रियाँ पाई गईं (Conflict detected)",
                "suggested_renames": suggested_renames,
                "ref_ids": [r.id for r in ref_list],
                "spans": spans,
            }
            conflicts.append(conflict_dict)

    return {
        "conflicts_count": len(conflicts),
        "conflicts": conflicts,
    }


def apply_conflict_resolution(
    wikitext: str,
    resolutions: Sequence[Dict[str, Any]],
) -> str:
    """
    Applies conflict resolution replacements in descending span order to prevent offset drift.
    Each item in `resolutions` can be a span object with `span_start`, `span_end`,
    `original`, and `replacement`.
    """
    # Flatten spans if full conflict objects were passed
    flat_resolutions: List[Dict[str, Any]] = []
    for item in resolutions:
        if "spans" in item and isinstance(item["spans"], list):
            flat_resolutions.extend(item["spans"])
        elif "span_start" in item and "span_end" in item and "replacement" in item:
            flat_resolutions.append(item)

    sorted_resolutions = sorted(
        flat_resolutions,
        key=lambda m: m["span_start"],
        reverse=True,
    )

    result = wikitext
    for m in sorted_resolutions:
        start = m["span_start"]
        end = m["span_end"]
        orig = m.get("original")
        replacement = m["replacement"]
        if orig is None or result[start:end] == orig:
            result = result[:start] + replacement + result[end:]

    return result


apply_conflict_resolutions = apply_conflict_resolution


__all__ = [
    "detect_ref_conflicts",
    "apply_conflict_resolution",
    "apply_conflict_resolutions",
]
