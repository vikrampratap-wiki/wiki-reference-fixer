"""
Reference Error Scanner (Companion Helper 4)
Scans Wikitext for syntax anomalies: empty <ref> tags, unclosed tags,
undefined named references, and missing reference list containers.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.parser import parse_wikitext_references, ParsedDocument


REFLIST_ALIASES = [
    "reflist",
    "संदर्भसूची",
    "सन्दर्भ सूची",
    "टिप्पणी सूची",
    "रेफ़्लिस्ट",
    "references",
    "संदर्भ",
    "सन्दर्भ",
    "टिप्पणियाँ",
]


def scan_reference_errors(
    wikitext: str, parsed_doc: Optional[ParsedDocument] = None
) -> Dict[str, Any]:
    """
    Scans wikitext for reference syntax errors and structural deficiencies.
    Returns:
        {
            "error_count": int,
            "errors": List[Dict[str, Any]],
        }
    """
    errors: List[Dict[str, Any]] = []

    if parsed_doc is None:
        parsed_doc = parse_wikitext_references(wikitext)

    # 1. Check for empty ref tags
    empty_ref_pattern = re.compile(
        r'<ref[^>]*>\s*</ref>|<ref\s*/>|<ref\s*>\s*</ref>', re.IGNORECASE
    )
    empty_matches = empty_ref_pattern.findall(wikitext)
    if empty_matches:
        errors.append({
            "type": "empty_ref",
            "message": "रिक्त सन्दर्भ टैग मिला (Empty <ref> tag found)",
            "count": len(empty_matches),
            "severity": "error",
        })

    # 2. Check for missing reflist container using AST analysis
    if parsed_doc.total_refs > 0 and not parsed_doc.has_reflist:
        errors.append({
            "type": "missing_reflist",
            "message": (
                "लेख में सन्दर्भ मौजूद हैं किन्तु सन्दर्भ सूची साँचा अनुपस्थित है (Missing Reflist)"
            ),
            "severity": "error",
        })

    # 3. Check for undefined named references
    # Self-closing refs: <ref name="xyz" />
    self_closing_names = re.findall(
        r'<ref\s+name=["\']([^"\']+)["\']\s*/>', wikitext, re.IGNORECASE
    )
    # Defined refs: <ref name="xyz">content</ref>
    defined_names = set(
        re.findall(
            r'<ref\s+name=["\']([^"\']+)["\']\s*>[^<]+</ref>', wikitext, re.IGNORECASE
        )
    )

    for name in set(self_closing_names):
        if name not in defined_names:
            errors.append({
                "type": "undefined_named_ref",
                "message": (
                    f"अपरिभाषित सन्दर्भ नाम '{name}' मिला जिसका मुख्य विवरण अनुपस्थित है"
                ),
                "ref_name": name,
                "severity": "warning",
            })

    # 4. Check for unclosed <ref> tags
    open_count = len(re.findall(r'<ref(?:\s+[^>]*)?>', wikitext, re.IGNORECASE))
    self_closing_count = len(re.findall(r'<ref(?:\s+[^>]*)?/>', wikitext, re.IGNORECASE))
    close_count = len(re.findall(r'</ref>', wikitext, re.IGNORECASE))
    if (open_count - self_closing_count) > close_count:
        errors.append({
            "type": "unclosed_ref",
            "message": "अधूरा या खुला सन्दर्भ टैग मिला (Unclosed <ref> tag)",
            "severity": "error",
        })

    return {
        "error_count": len(errors),
        "errors": errors,
    }
