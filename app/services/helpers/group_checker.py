"""
Ref Group Consistency Checker (Companion Helper 7)
Verifies that references assigned to specific groups (<ref group="...">) have
corresponding reference list display containers.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set


def check_ref_groups(wikitext: str) -> Dict[str, Any]:
    """
    Checks reference groups against reflist container groups.
    Handles optional whitespace around '=', '|', and unquoted attribute values.
    Returns:
        {
            "ref_groups_found": List[str],
            "container_groups_found": List[str],
            "orphaned_groups": List[str],
            "is_consistent": bool,
        }
    """
    # Find all group attributes in <ref> tags
    ref_group_pattern = re.compile(
        r'<ref\s+[^>]*\bgroup\s*=\s*["\']?([^"\'\s>]+)["\']?',
        re.IGNORECASE,
    )
    group_refs: Set[str] = {
        g.strip().strip('"\'')
        for g in ref_group_pattern.findall(wikitext)
        if g.strip()
    }

    # Find all group parameters in reflist templates or <references> tags
    container_template_pattern = re.compile(
        r'\{\{(?:reflist|reflist30em|संदर्भसूची|सन्दर्भसूची|सन्दर्भ\s*सूची|'
        r'संदर्भ\s*सूची|टिप्पणी\s*सूची|टिप्पणीसूची|रेफ़्लिस्ट|रेफलिट|रेफलिस्ट|'
        r'संसूची|सूत्र\s*सूची|सूत्र सूची|टिसू|नोट्स|हवाले)'
        r'[^}]*\|\s*group\s*=\s*["\']?([^"\'\|\}]+)["\']?',
        re.IGNORECASE,
    )
    container_tag_pattern = re.compile(
        r'<references\s+[^>]*\bgroup\s*=\s*["\']?([^"\'\s>]+)["\']?',
        re.IGNORECASE,
    )

    container_groups: Set[str] = set()
    for g in container_template_pattern.findall(wikitext):
        cleaned = g.strip().strip('"\'')
        if cleaned:
            container_groups.add(cleaned)
    for g in container_tag_pattern.findall(wikitext):
        cleaned = g.strip().strip('"\'')
        if cleaned:
            container_groups.add(cleaned)

    orphaned = list(group_refs - container_groups)

    return {
        "ref_groups_found": sorted(list(group_refs)),
        "container_groups_found": sorted(list(container_groups)),
        "orphaned_groups": sorted(orphaned),
        "is_consistent": len(orphaned) == 0,
    }


__all__ = [
    "check_ref_groups",
]
