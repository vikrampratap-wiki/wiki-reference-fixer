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
    Returns:
        {
            "ref_groups_found": List[str],
            "container_groups_found": List[str],
            "orphaned_groups": List[str],
            "is_consistent": bool,
        }
    """
    # Find all group attributes in <ref> tags
    ref_group_pattern = re.compile(r'<ref\s+[^>]*group=["\']([^"\']+)["\']', re.IGNORECASE)
    group_refs: Set[str] = set(ref_group_pattern.findall(wikitext))

    # Find all group parameters in reflist templates or <references> tags
    # e.g., {{Reflist|group=foo}}, {{सन्दर्भ सूची|group=foo}}, <references group="foo" />
    container_template_pattern = re.compile(
        r'\{\{(?:reflist|संदर्भसूची|सन्दर्भ सूची|टिप्पणी सूची|रेफ़्लिस्ट)[^}]*\|group=([^}|]+)',
        re.IGNORECASE
    )
    container_tag_pattern = re.compile(
        r'<references\s+[^>]*group=["\']([^"\']+)["\']',
        re.IGNORECASE
    )

    container_groups: Set[str] = set()
    for g in container_template_pattern.findall(wikitext):
        container_groups.add(g.strip())
    for g in container_tag_pattern.findall(wikitext):
        container_groups.add(g.strip())

    orphaned = list(group_refs - container_groups)

    return {
        "ref_groups_found": sorted(list(group_refs)),
        "container_groups_found": sorted(list(container_groups)),
        "orphaned_groups": sorted(orphaned),
        "is_consistent": len(orphaned) == 0,
    }
