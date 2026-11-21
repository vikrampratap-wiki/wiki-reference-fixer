"""
Scan Endpoint for Wikitext Reference Analysis and Repair Proposal.
Combines AST parsing, Bare URL to CS1 conversion, Syntax Repair,
and Reference Health Scorecards.
Authoritative Source: ORIGINAL_REQUEST.md (§3, §4), PROJECT.md (§M3, §M5).
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.parser import parse_wikitext_references
from app.services.syntax_repair import SyntaxRepairEngine
from app.services.helpers.scorecard import calculate_reference_scorecard

router = APIRouter(prefix="/api", tags=["Scan"])


class ScanPayload(BaseModel):
    wikitext: Optional[str] = ""
    title: Optional[str] = None


@router.post("/scan", summary="Analyze Wikitext or Wikipedia Article References")
async def scan_wikitext(payload: ScanPayload) -> Dict[str, Any]:
    """
    Comprehensive scan endpoint:
    - Parses AST references and identifies bare URLs.
    - Scans for malformed syntax, stray pipes, unclosed templates, and CS1 modernization.
    - Computes reference health scorecard (0-100).
    - Returns structured proposals for side-by-side granular diff review.
    """
    text = payload.wikitext or ""
    parsed_doc = parse_wikitext_references(text)
    syntax_mods = SyntaxRepairEngine.run_all_repairs(text)

    # Combine modifications from AST parser and syntax repair
    all_mods: List[Dict[str, Any]] = []

    # 1. Bare URL modifications
    for mod in parsed_doc.modifications:
        all_mods.append({
            "id": mod.id,
            "type": mod.type,
            "span_start": mod.span_start,
            "span_end": mod.span_end,
            "original_snippet": mod.original_snippet,
            "proposed_snippet": mod.proposed_snippet,
            "ref_id": mod.ref_id,
            "description": mod.description,
        })

    # 2. Syntax repair modifications
    for sm in syntax_mods:
        mod_id = f"mod-syn-{len(all_mods) + 1}"
        all_mods.append({
            "id": mod_id,
            "type": sm.type,
            "span_start": sm.span_start,
            "span_end": sm.span_end,
            "original_snippet": sm.original_snippet,
            "proposed_snippet": sm.proposed_snippet,
            "description": sm.description,
        })

    # 3. Health scorecard
    scorecard = calculate_reference_scorecard(text, parsed_doc=parsed_doc)

    return {
        "status": "success",
        "title": payload.title,
        "wikitext": text,
        "total_refs": parsed_doc.total_refs,
        "bare_urls": len(parsed_doc.bare_url_refs),
        "fixed_refs": len(all_mods),
        "modifications": all_mods,
        "health_score": scorecard["health_score"],
        "scorecard": scorecard,
    }
