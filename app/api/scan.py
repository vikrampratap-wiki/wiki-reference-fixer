"""
Scan Endpoint for Wikitext Reference Analysis and Repair Proposal.
Combines AST parsing, Bare URL to CS1 conversion, Syntax Repair,
and Reference Health Scorecards.
Authoritative Source: ORIGINAL_REQUEST.md (§3, §4), PROJECT.md (§M3, §M5).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.parser import ParsedDocument, parse_wikitext_references
from app.services.syntax_repair import SyntaxRepairEngine
from app.services.citoid import citoid_client, format_cs1_template
from app.services.helpers.scorecard import calculate_reference_scorecard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Scan"])


class ScanPayload(BaseModel):
    wikitext: Optional[str] = ""
    title: Optional[str] = None


async def enrich_bare_url_modifications(
    parsed_doc: ParsedDocument,
    enrich_citoid: bool = True,
) -> List[Dict[str, Any]]:
    """
    Asynchronously queries Citoid REST API for all bare URLs detected,
    updating modification snippets with authentic CS1 metadata.
    Gracefully catches any timeout or exception and falls back to baseline.
    """
    ref_map = {r.id: r for r in parsed_doc.references}

    async def _enrich_mod(mod: Any) -> Dict[str, Any]:
        mod_dict: Dict[str, Any] = {
            "id": mod.id,
            "type": mod.type,
            "span_start": mod.span_start,
            "span_end": mod.span_end,
            "original_snippet": mod.original_snippet,
            "proposed_snippet": mod.proposed_snippet,
            "ref_id": mod.ref_id,
            "description": mod.description,
        }

        should_enrich = (
            mod.type == "bare_url"
            and enrich_citoid
            and mod.ref_id is not None
            and mod.ref_id in ref_map
        )
        if should_enrich:
            ref = ref_map[mod.ref_id]
            if ref.bare_url:
                try:
                    meta = await citoid_client.fetch_citation_metadata(
                        ref.bare_url,
                        fallback_title=ref.fallback_title,
                    )
                    cs1_str = format_cs1_template(meta)

                    # Reconstruct ref wrapper preserving name/group attributes
                    attr_parts: List[str] = []
                    if ref.name:
                        attr_parts.append(f'name="{ref.name}"')
                    if ref.group:
                        attr_parts.append(f'group="{ref.group}"')
                    attr_str = (
                        (" " + " ".join(attr_parts)) if attr_parts else ""
                    )

                    mod_dict["proposed_snippet"] = (
                        f"<ref{attr_str}>{cs1_str}</ref>"
                    )
                    mod_dict["description"] = (
                        "Citoid द्वारा मेटाडेटा निष्कर्षण व CS1 में परिवर्तन"
                    )
                except Exception as exc:
                    logger.warning(
                        "Citoid enrichment skipped for %s: %s",
                        ref.bare_url,
                        exc,
                    )

        return mod_dict

    tasks = [_enrich_mod(mod) for mod in parsed_doc.modifications]
    results = await asyncio.gather(*tasks) if tasks else []
    return list(results)


@router.post("/scan", summary="Analyze Wikitext or Wikipedia Article References")
async def scan_wikitext(payload: ScanPayload) -> Dict[str, Any]:
    """
    Comprehensive scan endpoint:
    - Parses AST references and identifies bare URLs.
    - Asynchronously enriches bare URLs via Citoid with graceful fallback.
    - Scans for malformed syntax, stray pipes, unclosed templates, etc.
    - Computes reference health scorecard (0-100).
    - Returns structured proposals for side-by-side granular diff review.
    """
    text = payload.wikitext or ""
    parsed_doc = parse_wikitext_references(text)
    syntax_mods = SyntaxRepairEngine.run_all_repairs(text)

    all_mods: List[Dict[str, Any]] = []

    # 1. Bare URL modifications with Citoid enrichment
    bare_mods = await enrich_bare_url_modifications(
        parsed_doc, enrich_citoid=True
    )
    all_mods.extend(bare_mods)

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
