"""
FastAPI Routes for the 8 Companion Helper Modules.
Mounted under /api/helpers/*.
Interception by emergency kill-switch handled via middleware when active.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.helpers import (
    find_duplicate_references,
    apply_duplicate_merges,
    detect_ref_conflicts,
    apply_conflict_resolution,
    check_dead_links,
    scan_reference_errors,
    calculate_reference_scorecard,
    validate_bibliographic_identifiers,
    check_ref_groups,
    check_unreliable_domains,
    generate_wikitext_diff_patch,
)

router = APIRouter(prefix="/api/helpers", tags=["Companion Helpers"])


class WikitextPayload(BaseModel):
    wikitext: str = Field(default="", description="Wikitext of the Wikipedia article to analyze")


class PatchPayload(BaseModel):
    original_wikitext: str = Field(default="", description="Original article wikitext")
    modified_wikitext: str = Field(default="", description="Modified article wikitext")
    filename: Optional[str] = Field(default="article.patch", description="Patch filename")


class ApplyMergesPayload(BaseModel):
    wikitext: str = Field(default="", description="Original wikitext")
    proposed_merges: List[Dict[str, Any]] = Field(
        default_factory=list, description="Proposed merges to apply"
    )


class ApplyResolutionsPayload(BaseModel):
    wikitext: str = Field(default="", description="Original wikitext")
    resolutions: List[Dict[str, Any]] = Field(
        default_factory=list, description="Conflict resolutions to apply"
    )


@router.post("/duplicate-merger", summary="Helper 1: Duplicate Ref Merger")
async def api_duplicate_merger(payload: WikitextPayload) -> Dict[str, Any]:
    """Scans for duplicate reference bodies and proposes consolidation into <ref name="..."/>."""
    return find_duplicate_references(payload.wikitext)


@router.post("/apply-duplicate-merges", summary="Helper 1: Apply Duplicate Ref Merges")
async def api_apply_duplicate_merges(payload: ApplyMergesPayload) -> Dict[str, Any]:
    """Applies proposed duplicate reference merges to the wikitext."""
    merged_wikitext = apply_duplicate_merges(payload.wikitext, payload.proposed_merges)
    return {"wikitext": merged_wikitext}


@router.post("/conflict-resolver", summary="Helper 2: Duplicate Name Conflict Resolver")
async def api_conflict_resolver(payload: WikitextPayload) -> Dict[str, Any]:
    """Identifies conflicting reference bodies assigned to identical ref name attributes."""
    return detect_ref_conflicts(payload.wikitext)


@router.post("/apply-conflict-resolution", summary="Helper 2: Apply Conflict Resolution")
async def api_apply_conflict_resolution(payload: ApplyResolutionsPayload) -> Dict[str, Any]:
    """Applies conflict resolutions to rename conflicting references in the wikitext."""
    resolved_wikitext = apply_conflict_resolution(payload.wikitext, payload.resolutions)
    return {"wikitext": resolved_wikitext}


@router.post("/dead-links", summary="Helper 3: Dead Link & Archive Helper")
async def api_dead_links(payload: WikitextPayload) -> Dict[str, Any]:
    """Detects dead links and retrieves Wayback Machine snapshot information."""
    return await check_dead_links(payload.wikitext)


@router.post("/error-scanner", summary="Helper 4: Reference Error Scanner")
async def api_error_scanner(payload: WikitextPayload) -> Dict[str, Any]:
    """Scans for empty refs, missing reflists, unclosed tags, and undefined named refs."""
    return scan_reference_errors(payload.wikitext)


@router.post("/scorecard", summary="Helper 5: Reference Health Scorecard")
async def api_scorecard(payload: WikitextPayload) -> Dict[str, Any]:
    """Calculates reference quality health score (0-100) and metrics."""
    return calculate_reference_scorecard(payload.wikitext)


@router.post("/validator", summary="Helper 6: ISBN/ISSN/DOI & Date Validator")
async def api_validator(payload: WikitextPayload) -> Dict[str, Any]:
    """Validates bibliographic identifiers and detects Devanagari numerals for normalization."""
    return validate_bibliographic_identifiers(payload.wikitext)


@router.post("/group-checker", summary="Helper 7: Ref Group Consistency Checker")
async def api_group_checker(payload: WikitextPayload) -> Dict[str, Any]:
    """Checks for orphaned ref groups that lack corresponding reflist containers."""
    return check_ref_groups(payload.wikitext)


@router.post("/domain-advisory", summary="Helper 8: Unreliable Domain Advisory Flag")
async def api_domain_advisory(payload: WikitextPayload) -> Dict[str, Any]:
    """Checks references against known unreliable/UGC domains and provides advisory badges."""
    return check_unreliable_domains(payload.wikitext)


@router.post("/export-patch", summary="Helper 9: Local Draft & Diff Patch Exporter")
async def api_export_patch(payload: PatchPayload) -> Dict[str, Any]:
    """Generates a downloadable diff patch between original and modified wikitext."""
    return generate_wikitext_diff_patch(
        original_wikitext=payload.original_wikitext,
        modified_wikitext=payload.modified_wikitext,
        filename=payload.filename or "article.patch",
    )
