"""
Wikitext AST Parser and Descending-Span Reconstruction Engine for Hindi Wikipedia.
Uses mwparserfromhell to provide lossless reference extraction,
Devanagari Unicode support, Excluded-Zone Progressive Cursor algorithm,
and 100% byte-for-byte span re-assembly.
Authoritative Source: ORIGINAL_REQUEST.md (§3, R1, R2), PROJECT.md (§Feature 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import urllib.parse

import mwparserfromhell
from mwparserfromhell.nodes import Tag, Template
from mwparserfromhell.wikicode import Wikicode


REFLIST_TEMPLATE_ALIASES: Set[str] = {
    "reflist",
    "reflist30em",
    "सन्दर्भसूची",
    "संदर्भसूची",
    "सन्दर्भ सूची",
    "संदर्भ सूची",
    "टिप्पणी सूची",
    "टिप्पणीसूची",
    "सन्दर्भ",
    "संदर्भ",
    "रेफ़्लिस्ट",
    "रेफलिस्ट",
    "संसूची",
    "सूत्र सूची",
    "टिसू",
    "references",
}

BRACKETED_URL_PATTERN = re.compile(
    r"^\s*\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\]\s*$", re.UNICODE
)
PURE_URL_PATTERN = re.compile(
    r"^\s*(https?://[^\s<>\"\]\[]+)\s*$", re.UNICODE
)


@dataclass
class Modification:
    """
    Represents a proposed modification to a slice of wikitext.
    Supports both attribute access (mod.type) and dictionary indexing (mod['type']).
    """
    id: str
    type: str  # "bare_url" | "syntax_repair" | "cs1_modernize" | ...
    span_start: int
    span_end: int
    original_snippet: str
    proposed_snippet: str
    ref_id: Optional[str] = None
    description: Optional[str] = ""

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert modification to standard dictionary representation."""
        return {
            "id": self.id,
            "type": self.type,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "original_snippet": self.original_snippet,
            "proposed_snippet": self.proposed_snippet,
            "ref_id": self.ref_id,
            "description": self.description,
        }


@dataclass
class ParsedTemplate:
    """Represents a template parsed inside a reference or document."""
    name: str
    raw_name: str
    raw: str
    params: Dict[str, str]
    raw_params: Dict[str, str]
    node: Template


@dataclass
class ParsedReference:
    """Represents a <ref> tag in the wikitext AST."""
    id: str
    raw_tag: str
    name: Optional[str]
    group: Optional[str]
    self_closing: bool
    span_start: int
    span_end: int
    inner_content: str
    templates: List[ParsedTemplate] = field(default_factory=list)
    is_bare_url: bool = False
    bare_url: Optional[str] = None
    fallback_title: Optional[str] = None
    tag_node: Optional[Tag] = None


@dataclass
class ParsedRefList:
    """Represents a reference display container (<references /> or {{reflist}})."""
    type: str  # "tag" or "template"
    name: str
    group: Optional[str]
    span_start: int
    span_end: int
    raw: str


@dataclass
class ParsedDocument:
    """Comprehensive AST representation of wikitext references and containers."""
    original_wikitext: str
    wikicode: Wikicode
    references: List[ParsedReference]
    reflists: List[ParsedRefList]
    has_reflist: bool
    total_refs: int
    bare_url_refs: List[ParsedReference]
    named_refs: Dict[str, List[ParsedReference]]
    duplicate_name_conflicts: List[Dict[str, Any]]
    modifications: List[Modification] = field(default_factory=list)


def normalize_template_name(name: str) -> str:
    """Normalizes template names by trimming and replacing underscores with spaces."""
    return str(name).strip().lower().replace("_", " ")


def extract_bare_url_info(contents: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Extracts bare URL and optional fallback title from reference text.
    Returns (url, fallback_title) or None if content is not a bare URL.
    """
    stripped = contents.strip()
    m_bracket = BRACKETED_URL_PATTERN.match(stripped)
    if m_bracket:
        return m_bracket.group(1), m_bracket.group(2)
    m_pure = PURE_URL_PATTERN.match(stripped)
    if m_pure:
        return m_pure.group(1), None
    return None


def _format_bare_url_replacement(
    url: str,
    fallback_title: Optional[str] = None,
    attrs: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Format replacement <ref> tag with standard CS1 template wikitext."""
    today = datetime.date.today().isoformat()
    parsed_domain = ""
    try:
        parsed_domain = urllib.parse.urlparse(url).hostname or ""
        if parsed_domain.startswith("www."):
            parsed_domain = parsed_domain[4:]
    except Exception:
        pass

    # Authentic metadata formatting: fallback_title or domain-based title
    if fallback_title:
        template = (
            f"{{{{Cite web |url={url} |title={fallback_title} "
            f"|access-date={today}}}}}"
        )
    else:
        title = f"{parsed_domain} वेब सन्दर्भ" if parsed_domain else "वेब सन्दर्भ"
        website_part = f" |website={parsed_domain}" if parsed_domain else ""
        template = (
            f"{{{{Cite web |url={url} |title={title}{website_part} "
            f"|access-date={today}}}}}"
        )

    # Reconstruct outer <ref> attributes preserving name and group
    attr_str = ""
    if attrs:
        attr_parts = [f'{k}="{v}"' for k, v in attrs]
        attr_str = " " + " ".join(attr_parts)

    return f"<ref{attr_str}>{template}</ref>"


def parse_wikitext_references(wikitext: str) -> ParsedDocument:
    """
    Parses wikitext using mwparserfromhell and extracts all <ref> tags,
    nested templates, bare URLs, and reference lists with character offsets.
    Uses the Excluded-Zone Progressive Cursor algorithm to avoid duplicate tag collisions
    and comment false-positives.
    """
    if not wikitext:
        return ParsedDocument(
            original_wikitext="",
            wikicode=mwparserfromhell.parse(""),
            references=[],
            reflists=[],
            has_reflist=False,
            total_refs=0,
            bare_url_refs=[],
            named_refs={},
            duplicate_name_conflicts=[],
            modifications=[],
        )

    parsed = mwparserfromhell.parse(wikitext)

    # Step 1: Pre-compute excluded zones (HTML comments and nowiki tags)
    excluded_spans: List[Tuple[int, int]] = []
    cursor = 0
    for comment in parsed.filter_comments():
        raw_c = str(comment)
        idx = wikitext.find(raw_c, cursor)
        if idx != -1:
            excluded_spans.append((idx, idx + len(raw_c)))
            cursor = idx + len(raw_c)

    cursor = 0
    for nw in parsed.filter_tags(
        matches=lambda t: str(t.tag).strip().lower() == "nowiki"
    ):
        raw_nw = str(nw)
        idx = wikitext.find(raw_nw, cursor)
        if idx != -1:
            excluded_spans.append((idx, idx + len(raw_nw)))
            cursor = idx + len(raw_nw)

    def is_in_excluded_zone(pos: int) -> bool:
        return any(start <= pos < end for start, end in excluded_spans)

    # Step 2: Extract active <ref> tags using progressive cursor
    ref_tags = parsed.filter_tags(
        matches=lambda t: str(t.tag).strip().lower() == "ref"
    )
    references: List[ParsedReference] = []
    modifications: List[Modification] = []
    ref_cursor = 0
    ref_idx = 1

    for tag in ref_tags:
        raw_tag = str(tag)

        # Locate tag outside excluded zones
        span_start = -1
        span_end = -1
        while True:
            idx = wikitext.find(raw_tag, ref_cursor)
            if idx == -1:
                break
            if not is_in_excluded_zone(idx):
                span_start = idx
                span_end = idx + len(raw_tag)
                ref_cursor = span_end
                break
            else:
                ref_cursor = idx + 1

        # Extract attributes (name, group) with Devanagari Unicode preservation
        ref_name: Optional[str] = None
        ref_group: Optional[str] = None
        attrs_list: List[Tuple[str, str]] = []
        for attr in tag.attributes:
            aname = str(attr.name).strip()
            aval = str(attr.value).strip()
            if (
                aval.startswith(('"', "'"))
                and aval.endswith(('"', "'"))
                and len(aval) >= 2
            ):
                aval = aval[1:-1]
            attrs_list.append((aname, aval))
            if aname.lower() == "name":
                ref_name = aval
            elif aname.lower() == "group":
                ref_group = aval

        self_closing = bool(tag.self_closing)
        inner_content = (
            "" if self_closing else "".join(str(n) for n in tag.contents)
        )

        # Parse nested templates inside ref contents
        nested_templates: List[ParsedTemplate] = []
        if not self_closing and tag.contents:
            for tm in tag.contents.filter_templates():
                raw_p = {str(p.name): str(p.value) for p in tm.params}
                clean_p = {
                    str(p.name).strip(): str(p.value).strip() for p in tm.params
                }
                nested_templates.append(
                    ParsedTemplate(
                        name=normalize_template_name(str(tm.name)),
                        raw_name=str(tm.name),
                        raw=str(tm),
                        params=clean_p,
                        raw_params=raw_p,
                        node=tm,
                    )
                )

        # Detect Bare URL
        is_bare = False
        bare_url: Optional[str] = None
        fallback_title: Optional[str] = None
        if not self_closing and len(nested_templates) == 0:
            url_info = extract_bare_url_info(inner_content)
            if url_info:
                is_bare = True
                bare_url, fallback_title = url_info

        ref_id_str = f"ref-{ref_idx}"
        parsed_ref = ParsedReference(
            id=ref_id_str,
            raw_tag=raw_tag,
            name=ref_name,
            group=ref_group,
            self_closing=self_closing,
            span_start=span_start,
            span_end=span_end,
            inner_content=inner_content,
            templates=nested_templates,
            is_bare_url=is_bare,
            bare_url=bare_url,
            fallback_title=fallback_title,
            tag_node=tag,
        )
        references.append(parsed_ref)

        # Generate modification for bare URL
        if is_bare and bare_url and span_start != -1:
            proposed_tag = _format_bare_url_replacement(
                bare_url,
                fallback_title=fallback_title,
                attrs=attrs_list,
            )
            modifications.append(
                Modification(
                    id=f"mod-{len(modifications) + 1}",
                    type="bare_url",
                    span_start=span_start,
                    span_end=span_end,
                    original_snippet=raw_tag,
                    proposed_snippet=proposed_tag,
                    ref_id=ref_id_str,
                    description="नग्न URL को मानक CS1 साँचे में बदला",
                )
            )

        ref_idx += 1

    # Step 3: Extract reference list containers
    reflists: List[ParsedRefList] = []

    # 3a. <references /> tags
    ref_list_tags = parsed.filter_tags(
        matches=lambda t: str(t.tag).strip().lower() == "references"
    )
    for r_tag in ref_list_tags:
        r_raw = str(r_tag)
        group_val: Optional[str] = None
        for a in r_tag.attributes:
            if str(a.name).strip().lower() == "group":
                group_val = str(a.value).strip()
                if (
                    group_val.startswith(('"', "'"))
                    and group_val.endswith(('"', "'"))
                    and len(group_val) >= 2
                ):
                    group_val = group_val[1:-1]
        t_start = wikitext.find(r_raw)
        t_end = t_start + len(r_raw) if t_start != -1 else -1
        reflists.append(
            ParsedRefList(
                type="tag",
                name="references",
                group=group_val,
                span_start=t_start,
                span_end=t_end,
                raw=r_raw,
            )
        )

    # 3b. {{reflist}} and Hindi template aliases
    for tm in parsed.filter_templates():
        norm_tname = normalize_template_name(str(tm.name))
        if norm_tname in REFLIST_TEMPLATE_ALIASES:
            group_val = None
            if tm.has("group"):
                group_val = str(tm.get("group").value).strip()
            elif tm.params and "=" not in str(tm.params[0]):
                val = str(tm.params[0].value).strip()
                if not re.match(r"^\d+(em|px|%)?$", val):
                    group_val = val

            tm_raw = str(tm)
            tm_start = wikitext.find(tm_raw)
            tm_end = tm_start + len(tm_raw) if tm_start != -1 else -1
            reflists.append(
                ParsedRefList(
                    type="template",
                    name=norm_tname,
                    group=group_val,
                    span_start=tm_start,
                    span_end=tm_end,
                    raw=tm_raw,
                )
            )

    # Step 4: Group named references and detect content conflicts
    named_map: Dict[str, List[ParsedReference]] = {}
    for r in references:
        if r.name:
            named_map.setdefault(r.name, []).append(r)

    conflicts: List[Dict[str, Any]] = []
    for name, refs in named_map.items():
        bodies = [
            r.inner_content.strip()
            for r in refs
            if not r.self_closing and r.inner_content.strip()
        ]
        unique_bodies = set(bodies)
        if len(unique_bodies) > 1:
            conflicts.append({
                "name": name,
                "count": len(refs),
                "conflicting_bodies": list(unique_bodies),
                "ref_ids": [r.id for r in refs],
            })

    bare_refs = [r for r in references if r.is_bare_url]

    return ParsedDocument(
        original_wikitext=wikitext,
        wikicode=parsed,
        references=references,
        reflists=reflists,
        has_reflist=len(reflists) > 0,
        total_refs=len(references),
        bare_url_refs=bare_refs,
        named_refs=named_map,
        duplicate_name_conflicts=conflicts,
        modifications=modifications,
    )


def apply_granular_modifications(
    original_wikitext: str,
    selected_modifications: Sequence[Union[Modification, Dict[str, Any]]],
) -> str:
    """
    Reconstructs wikitext by splicing selected modifications into original wikitext.
    Modifications are sorted in strictly descending order of span_start, guaranteeing
    that subsequent replacements operate on undisturbed character offsets.
    Unselected text, surrounding whitespace, inline comments, and section headers
    remain 100% byte-for-byte untouched.
    """
    if not selected_modifications:
        return original_wikitext

    # Normalize modification dicts / objects
    norm_mods: List[Dict[str, Any]] = []
    for m in selected_modifications:
        if isinstance(m, Modification):
            norm_mods.append(m.to_dict())
        elif isinstance(m, dict):
            norm_mods.append(m)
        else:
            raise TypeError(f"Unsupported modification type: {type(m)}")

    # Sort strictly descending by span_start
    sorted_mods = sorted(
        norm_mods, key=lambda m: int(m["span_start"]), reverse=True
    )

    # Validate non-overlapping invariant
    for i in range(len(sorted_mods) - 1):
        later_mod = sorted_mods[i]
        earlier_mod = sorted_mods[i + 1]
        if earlier_mod["span_end"] > later_mod["span_start"]:
            raise ValueError(
                f"Overlapping modification spans detected: '{earlier_mod.get('id')}' "
                f"[{earlier_mod['span_start']}:{earlier_mod['span_end']}] overlaps with "
                f"'{later_mod.get('id')}' [{later_mod['span_start']}:{later_mod['span_end']}]."
            )

    reconstructed = original_wikitext
    for mod in sorted_mods:
        s = int(mod["span_start"])
        e = int(mod["span_end"])
        proposed = str(mod["proposed_snippet"])

        if s < 0 or e > len(original_wikitext) or s > e:
            raise IndexError(
                f"Invalid modification span [{s}:{e}] for wikitext "
                f"of length {len(original_wikitext)}."
            )

        # Integrity check: verify that original snippet matches target slice
        expected_orig = mod.get("original_snippet")
        if expected_orig is not None:
            actual_orig = original_wikitext[s:e]
            if actual_orig != expected_orig:
                raise ValueError(
                    f"Integrity check failed at span [{s}:{e}]. Expected snippet: "
                    f"{repr(expected_orig)}, but found in original: {repr(actual_orig)}."
                )

        # Splice replacement
        reconstructed = reconstructed[:s] + proposed + reconstructed[e:]

    return reconstructed


__all__ = [
    "Modification",
    "ParsedTemplate",
    "ParsedReference",
    "ParsedRefList",
    "ParsedDocument",
    "REFLIST_TEMPLATE_ALIASES",
    "normalize_template_name",
    "extract_bare_url_info",
    "parse_wikitext_references",
    "apply_granular_modifications",
]
