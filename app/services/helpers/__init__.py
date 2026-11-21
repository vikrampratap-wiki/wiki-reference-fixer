"""
Companion Helper Modules (Milestone 4)
Exports the 8 Companion Helper modules:
1. Duplicate Ref Merger
2. Duplicate Name Conflict Resolver
3. Dead Link & Wayback Helper
4. Reference Error Scanner
5. Reference Health Scorecard
6. ISBN/ISSN/DOI & Date Validator
7. Ref Group Consistency Checker
8. Unreliable Domain Advisory Flag
9. Local Draft & Diff Patch Exporter
"""

from app.services.helpers.duplicate_merger import (
    find_duplicate_references,
    apply_duplicate_merges,
)
from app.services.helpers.conflict_resolver import (
    detect_ref_conflicts,
    apply_conflict_resolution,
    apply_conflict_resolutions,
)
from app.services.helpers.dead_links import check_dead_links
from app.services.helpers.error_scanner import scan_reference_errors
from app.services.helpers.scorecard import calculate_reference_scorecard
from app.services.helpers.validator import (
    validate_bibliographic_identifiers,
    normalize_devanagari_numerals,
    validate_isbn_10,
    validate_isbn_13,
    validate_issn,
    validate_doi,
)
from app.services.helpers.group_checker import check_ref_groups
from app.services.helpers.domain_advisory import check_unreliable_domains, UNRELIABLE_DOMAINS
from app.services.helpers.local_draft import generate_wikitext_diff_patch

__all__ = [
    "find_duplicate_references",
    "apply_duplicate_merges",
    "detect_ref_conflicts",
    "apply_conflict_resolution",
    "apply_conflict_resolutions",
    "check_dead_links",
    "scan_reference_errors",
    "calculate_reference_scorecard",
    "validate_bibliographic_identifiers",
    "normalize_devanagari_numerals",
    "validate_isbn_10",
    "validate_isbn_13",
    "validate_issn",
    "validate_doi",
    "check_ref_groups",
    "check_unreliable_domains",
    "UNRELIABLE_DOMAINS",
    "generate_wikitext_diff_patch",
]
