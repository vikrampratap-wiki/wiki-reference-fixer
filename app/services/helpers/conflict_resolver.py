"""
Duplicate Name Conflict Resolver (Companion Helper 2)
Detects cases where references share the same name attribute but contain conflicting,
distinct bodies, preventing silent data loss and alerting the user.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.parser import parse_wikitext_references, ParsedDocument


def detect_ref_conflicts(
    wikitext: str, parsed_doc: Optional[ParsedDocument] = None
) -> Dict[str, Any]:
    """
    Scans wikitext for conflicting reference contents defined under the identical name attribute.
    Returns:
        {
            "conflicts_count": int,
            "conflicts": List[Dict[str, Any]],
        }
    """
    if parsed_doc is None:
        parsed_doc = parse_wikitext_references(wikitext)

    named_refs: Dict[str, List[str]] = {}

    for ref in parsed_doc.references:
        if ref.name and ref.name.strip():
            val = ref.name.strip('"\'')
            body = ref.inner_content.strip()
            # Only count non-empty bodies (definitions)
            if body:
                named_refs.setdefault(val, []).append(body)

    conflicts: List[Dict[str, Any]] = []
    for name, bodies in named_refs.items():
        unique_bodies = list(dict.fromkeys(bodies))  # preserve insertion order, unique
        if len(unique_bodies) > 1:
            conflicts.append({
                "ref_name": name,
                "conflicting_contents": unique_bodies,
                "message": f"सन्दर्भ नाम '{name}' में भिन्न सामग्रियाँ पाई गईं (Conflict detected)",
                "suggested_renames": [f"{name}_{i+1}" for i in range(len(unique_bodies))],
            })

    return {
        "conflicts_count": len(conflicts),
        "conflicts": conflicts,
    }
