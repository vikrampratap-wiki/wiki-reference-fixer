"""
Local Draft & Patch Export (Companion Helper 9)
Provides helpers for creating downloadable diff patch files, wikitext exports,
and structured draft payloads for human verification and offline restoration.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict


def generate_wikitext_diff_patch(
    original_wikitext: str,
    modified_wikitext: str,
    filename: str = "article.patch"
) -> Dict[str, Any]:
    """
    Generates a unified diff patch between original and modified wikitext.
    """
    original_lines = original_wikitext.splitlines(keepends=True)
    modified_lines = modified_wikitext.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile="a/article.wiki",
        tofile="b/article.wiki",
        lineterm=""
    )
    patch_text = "".join(diff)

    lines_added = sum(
        1 for line in patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    lines_removed = sum(
        1 for line in patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )

    return {
        "filename": filename,
        "patch": patch_text,
        "has_changes": original_wikitext != modified_wikitext,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }
