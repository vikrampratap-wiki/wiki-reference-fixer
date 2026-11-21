"""
ISBN/ISSN/DOI & Date Validator (Companion Helper 6)
Validates bibliographic identifiers (ISBN-10, ISBN-13, ISSN, DOI) and detects
Devanagari numerals in dates or identifiers for normalization.
Authoritative Source: ORIGINAL_REQUEST.md §4, R4.
"""

from __future__ import annotations

import re
from typing import Any, Dict


DEVANAGARI_DIGITS = {
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9"
}


def normalize_devanagari_numerals(text: str) -> str:
    """Normalizes Devanagari numerals to ASCII Arabic digits."""
    normalized = text
    for dev, latin in DEVANAGARI_DIGITS.items():
        normalized = normalized.replace(dev, latin)
    return normalized


def validate_isbn_10(isbn: str) -> bool:
    """Validates an ISBN-10 checksum."""
    normalized = normalize_devanagari_numerals(isbn)
    clean = normalized.replace("-", "").replace(" ", "").strip()
    if len(clean) != 10:
        return False
    try:
        total = 0
        for i in range(9):
            total += int(clean[i]) * (10 - i)
        last = 10 if clean[9].upper() == "X" else int(clean[9])
        total += last
        return total % 11 == 0
    except ValueError:
        return False


def validate_isbn_13(isbn: str) -> bool:
    """Validates an ISBN-13 checksum."""
    normalized = normalize_devanagari_numerals(isbn)
    clean = normalized.replace("-", "").replace(" ", "").strip()
    if len(clean) != 13 or not clean.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(clean[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(clean[12])


def validate_issn(issn: str) -> bool:
    """Validates an 8-digit ISSN."""
    normalized = normalize_devanagari_numerals(issn)
    clean = normalized.replace("-", "").replace(" ", "").strip()
    if len(clean) != 8:
        return False
    try:
        total = sum(int(clean[i]) * (8 - i) for i in range(7))
        last = 10 if clean[7].upper() == "X" else int(clean[7])
        total += last
        return total % 11 == 0
    except ValueError:
        return False


def validate_doi(doi: str) -> bool:
    """
    Validates standard DOI format (10.xxxx/yyyy), supporting
    https://doi.org/ prefix and valid DOI characters (+, -, ., _, etc.).
    """
    clean = doi.strip()
    prefixes = (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    )
    for prefix in prefixes:
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):].strip()
            break
    doi_pattern = re.compile(r'^10\.\d{4,9}/[-._;()/:+A-Za-z0-9]+$')
    return bool(doi_pattern.match(clean))


def validate_bibliographic_identifiers(wikitext: str) -> Dict[str, Any]:
    """
    Scans wikitext for ISBN, ISSN, DOI, and dates with Devanagari numerals.
    Returns:
        {
            "identifiers_scanned": int,
            "results": Dict[str, Any],
        }
    """
    results: Dict[str, Any] = {}

    # 1. ISBN check (supports ASCII and Devanagari numerals)
    isbn_match = re.search(r'isbn\s*=\s*([0-9०-९\-xX\s]+)', wikitext, re.IGNORECASE)
    if isbn_match:
        raw_isbn = isbn_match.group(1).strip()
        normalized_isbn = (
            normalize_devanagari_numerals(raw_isbn)
            .replace("-", "")
            .replace(" ", "")
            .strip()
        )
        if len(normalized_isbn) == 13:
            valid = validate_isbn_13(normalized_isbn)
            results["isbn"] = {
                "raw": raw_isbn,
                "valid": valid,
                "type": "ISBN-13",
            }
        elif len(normalized_isbn) == 10:
            valid = validate_isbn_10(normalized_isbn)
            results["isbn"] = {
                "raw": raw_isbn,
                "valid": valid,
                "type": "ISBN-10",
            }
        else:
            results["isbn"] = {"raw": raw_isbn, "valid": False, "type": "Invalid Length"}

    # 2. DOI check
    doi_match = re.search(r'doi\s*=\s*([^\s\|\}]+)', wikitext, re.IGNORECASE)
    if doi_match:
        raw_doi = doi_match.group(1).strip()
        results["doi"] = {
            "raw": raw_doi,
            "valid": validate_doi(raw_doi),
        }

    # 3. ISSN check (supports ASCII and Devanagari numerals)
    issn_match = re.search(r'issn\s*=\s*([0-9०-९\-xX\s]+)', wikitext, re.IGNORECASE)
    if issn_match:
        raw_issn = issn_match.group(1).strip()
        normalized_issn = (
            normalize_devanagari_numerals(raw_issn)
            .replace("-", "")
            .replace(" ", "")
            .strip()
        )
        results["issn"] = {
            "raw": raw_issn,
            "valid": validate_issn(normalized_issn),
        }

    # 4. Devanagari Date Normalization
    has_dev_digits = any(d in wikitext for d in DEVANAGARI_DIGITS)
    if has_dev_digits:
        # Search for explicit date parameter first across the whole document
        date_pattern = (
            r'(?:date|access-date|archive-date|year|दिनांक|तिथि|वर्ष)'
            r'\s*=\s*([^\s\|\}]+[०-९][^\s\|\}]*)'
        )
        date_match = re.search(date_pattern, wikitext, re.IGNORECASE)
        if date_match:
            sample = date_match.group(0)
        else:
            first_idx = min(wikitext.index(d) for d in DEVANAGARI_DIGITS if d in wikitext)
            start = max(0, first_idx - 20)
            end = min(len(wikitext), first_idx + 80)
            sample = wikitext[start:end]

        normalized_sample = normalize_devanagari_numerals(sample)
        results["date_normalization"] = {
            "detected": True,
            "normalized_sample": normalized_sample,
        }

    return {
        "identifiers_scanned": len(results),
        "results": results,
    }


__all__ = [
    "DEVANAGARI_DIGITS",
    "normalize_devanagari_numerals",
    "validate_isbn_10",
    "validate_isbn_13",
    "validate_issn",
    "validate_doi",
    "validate_bibliographic_identifiers",
]
