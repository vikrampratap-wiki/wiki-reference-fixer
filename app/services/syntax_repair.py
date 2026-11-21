"""
Syntax Repair & CS1/CS2 Parameter Modernization Engine for Hindi Wikipedia.
Handles:
1. Template bracket balancing ({{ and }})
2. Stray and redundant pipe cleanup (||, trailing |}})
3. Malformed XML <ref> tag normalization
4. CS1 parameter modernization (deadurl -> url-status, accessdate -> access-date)
Authoritative Source: ORIGINAL_REQUEST.md (§3, R1), PROJECT.md (§Feature 11).
"""

from __future__ import annotations

import re
from typing import List, Tuple
import mwparserfromhell

from app.services.parser import Modification, apply_granular_modifications


class SyntaxRepairEngine:
    """Core algorithmic engine for syntax repairs and CS1 modernization."""

    @staticmethod
    def balance_template_brackets(
        wikitext: str, start_id: int = 1
    ) -> Tuple[str, List[Modification]]:
        """
        Detects unclosed template brackets:
        1. Inside <ref>...</ref> tags where {{ is not closed with }}
        2. At end of text or before section headings == ... ==
        """
        modifications: List[Modification] = []
        mod_id = start_id

        # Pattern 1: Inside <ref>...</ref> tags
        ref_pattern = re.compile(
            r"(<ref(?: [^/>]*)?>)(.*?)(</ref>)", re.DOTALL | re.IGNORECASE
        )
        for match in ref_pattern.finditer(wikitext):
            inner = match.group(2)
            open_count = inner.count("{{")
            close_count = inner.count("}}")
            if open_count > close_count:
                missing = "}}" * (open_count - close_count)
                repaired_inner = inner + missing
                full_orig = match.group(0)
                full_proposed = (
                    f"{match.group(1)}{repaired_inner}{match.group(3)}"
                )
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=full_orig,
                        proposed_snippet=full_proposed,
                        description="संदर्भ में साँचा कोष्ठक मरम्मत: missing }} जोड़ा",
                    )
                )
                mod_id += 1

        # Pattern 2: Unclosed template at EOF or before next section heading
        # Matches {{Cite ... without matching }} before next \n== or EOF
        unclosed_pattern = re.compile(
            r"\{\{(?:[^{}]+?)(?=(?:\n\s*==|$))", re.DOTALL
        )
        for match in unclosed_pattern.finditer(wikitext):
            # If this match is already covered by a modification inside <ref>, skip
            if any(
                m.span_start <= match.start() < m.span_end
                for m in modifications
            ):
                continue

            snippet = match.group(0)
            open_count = snippet.count("{{")
            close_count = snippet.count("}}")
            if open_count > close_count:
                missing = "}}" * (open_count - close_count)
                proposed = snippet + missing
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=snippet,
                        proposed_snippet=proposed,
                        description="कोष्ठक मरम्मत: अधूरा साँचा बंद किया ({{...}} पूर्ण किया)",
                    )
                )
                mod_id += 1

        return wikitext, modifications

    @staticmethod
    def clean_stray_pipes(
        wikitext: str, start_id: int = 1
    ) -> Tuple[str, List[Modification]]:
        """
        Detects redundant double pipes (||) and trailing pipes (|}}) inside templates.
        Guarantees wikitables ({| ... || ... |}) are strictly untouched.
        """
        modifications: List[Modification] = []
        mod_id = start_id

        # Search specifically inside templates: {{...}}
        template_pattern = re.compile(r"\{\{[^{}]*\|\|[^{}]*\}\}")
        for match in template_pattern.finditer(wikitext):
            orig = match.group(0)
            # 1. Replace multiple consecutive pipes with a single pipe
            repaired = re.sub(r"\|\s*\|+", "|", orig)
            # 2. Clean trailing pipes before closing }}
            repaired = re.sub(r"\s*\|\s*\}\}", "}}", repaired)
            if repaired != orig:
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=orig,
                        proposed_snippet=repaired,
                        description="अनावश्यक पाइप हटाए (|| -> |)",
                    )
                )
                mod_id += 1

        # Check for trailing pipes (|}}) in templates without double pipes
        trailing_pipe_pattern = re.compile(r"\{\{[^{}]+\|\s*\}\}")
        for match in trailing_pipe_pattern.finditer(wikitext):
            orig = match.group(0)
            repaired = re.sub(r"\s*\|\s*\}\}", "}}", orig)
            # Avoid duplicate modification if already matched above
            if repaired != orig and not any(
                m.span_start == match.start() for m in modifications
            ):
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=orig,
                        proposed_snippet=repaired,
                        description="अंतिम अनावश्यक पाइप हटाया (|}} -> }})",
                    )
                )
                mod_id += 1

        return wikitext, modifications

    @staticmethod
    def fix_malformed_ref_tags(
        wikitext: str, start_id: int = 1
    ) -> Tuple[str, List[Modification]]:
        """
        Normalizes malformed <ref> XML tags:
        1. Misplaced slashes: <ref/ name="foo"> or <ref / name="foo"> -> <ref name="foo" />
        2. Misplaced slash whitespace: <ref name="foo"/ > -> <ref name="foo" />
        3. Unquoted attributes: <ref name=foo> -> <ref name="foo">
        4. Dangling unclosed <ref> before section headings == ... == or EOF
        """
        modifications: List[Modification] = []
        mod_id = start_id

        # 1. Misplaced slashes: <ref/ name="...">
        misplaced_slash_pattern = re.compile(
            r"<ref\s*/\s*name\s*=\s*(?:[\"']([^\"']+)[\"']|([^\s/>]+))\s*>",
            re.IGNORECASE,
        )
        for match in misplaced_slash_pattern.finditer(wikitext):
            orig = match.group(0)
            val = match.group(1) or match.group(2)
            proposed = f'<ref name="{val}" />'
            modifications.append(
                Modification(
                    id=f"mod-{mod_id}",
                    type="syntax_repair",
                    span_start=match.start(),
                    span_end=match.end(),
                    original_snippet=orig,
                    proposed_snippet=proposed,
                    description="खराब <ref> टैग सिंटैक्स सुधारा (स्लैश स्थिति सामान्यीकृत)",
                )
            )
            mod_id += 1

        # 2. Whitespace before closing bracket: <ref name="foo"/ > or <ref name="foo" / >
        slash_ws_pattern = re.compile(
            r"<ref\s+([^>]*?)\s*/\s*>", re.IGNORECASE
        )
        for match in slash_ws_pattern.finditer(wikitext):
            orig = match.group(0)
            # Skip if already perfectly formatted as '<ref ... />'
            if not orig.endswith("/ >") and not orig.endswith("/  >"):
                continue
            attrs = match.group(1).rstrip()
            proposed = f"<ref {attrs} />"
            if proposed != orig and not any(
                m.span_start == match.start() for m in modifications
            ):
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=orig,
                        proposed_snippet=proposed,
                        description="टैग रिक्ति सामान्यीकृत (<ref.../ > -> <ref... />)",
                    )
                )
                mod_id += 1

        # 3. Unquoted attributes: <ref name=foo> or <ref name=foo />
        unquoted_attr_pattern = re.compile(
            r"<ref\s+name=([^\"'\s/>]+)(\s*/?>)", re.IGNORECASE
        )
        for match in unquoted_attr_pattern.finditer(wikitext):
            orig = match.group(0)
            val = match.group(1)
            suffix = match.group(2)
            if not any(m.span_start == match.start() for m in modifications):
                proposed = f'<ref name="{val}"{suffix}'
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=orig,
                        proposed_snippet=proposed,
                        description=f'गुण मान उद्धृत किया (name="{val}")',
                    )
                )
                mod_id += 1

        # 4. Dangling unclosed <ref> before section headings or EOF
        unclosed_ref_pattern = re.compile(
            r"(<ref(?: [^/>]*)?>)([^<>\n]+)(?=\n\s*==|$)",
            re.IGNORECASE,
        )
        for match in unclosed_ref_pattern.finditer(wikitext):
            orig = match.group(0)
            # If it already contains </ref>, it is not dangling
            if "</ref>" not in orig:
                proposed = f"{orig}</ref>"
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="syntax_repair",
                        span_start=match.start(),
                        span_end=match.end(),
                        original_snippet=orig,
                        proposed_snippet=proposed,
                        description="अपूर्ण संदर्भ टैग बंद किया (</ref> जोड़ा)",
                    )
                )
                mod_id += 1

        return wikitext, modifications

    @staticmethod
    def modernize_cs1_templates(
        wikitext: str, start_id: int = 1
    ) -> Tuple[str, List[Modification]]:
        """
        Modernizes CS1/CS2 parameters using mwparserfromhell AST:
        - deadurl / dead-url / dead_url -> url-status
        - accessdate -> access-date
        - archiveurl -> archive-url
        - archivedate -> archive-date
        - airdate -> air-date
        Preserves parameter formatting and spacing 100%.
        """
        modifications: List[Modification] = []
        mod_id = start_id
        parsed = mwparserfromhell.parse(wikitext)

        curr_search_offset = 0

        for tmpl in parsed.filter_templates():
            orig_tmpl_str = str(tmpl)
            tmpl_start = wikitext.find(orig_tmpl_str, curr_search_offset)
            if tmpl_start == -1:
                tmpl_start = wikitext.find(orig_tmpl_str)
            if tmpl_start != -1:
                curr_search_offset = tmpl_start + len(orig_tmpl_str)

            modified = False

            # Check deadurl / dead-url / dead_url
            deadurl_param = None
            for alias in ["deadurl", "dead-url", "dead_url"]:
                if tmpl.has(alias):
                    deadurl_param = tmpl.get(alias)
                    break

            if deadurl_param:
                raw_val = str(deadurl_param.value)
                clean_val = raw_val.strip().lower()

                # Determine mapped url-status
                if clean_val in ("yes", "y", "true", "1"):
                    new_status = "dead"
                elif clean_val in ("no", "n", "false", "0"):
                    new_status = "live"
                elif clean_val in ("unfit", "usurped", "bot: unknown"):
                    new_status = clean_val
                else:
                    new_status = "dead"

                if tmpl.has("url-status"):
                    # url-status already exists; remove redundant deadurl
                    tmpl.remove(deadurl_param)
                else:
                    # Rename parameter and preserve whitespace in name and value
                    raw_name = str(deadurl_param.name)
                    pfx_n = raw_name[: len(raw_name) - len(raw_name.lstrip())]
                    sfx_n = raw_name[len(raw_name.rstrip()) :]
                    deadurl_param.name = f"{pfx_n}url-status{sfx_n}"
                    prefix = raw_val[: len(raw_val) - len(raw_val.lstrip())]
                    suffix = raw_val[len(raw_val.rstrip()) :]
                    deadurl_param.value = f"{prefix}{new_status}{suffix}"

                modified = True

            # Modernize parameter names
            param_renames = {
                "accessdate": "access-date",
                "archiveurl": "archive-url",
                "archivedate": "archive-date",
                "airdate": "air-date",
            }
            for old_p, new_p in param_renames.items():
                if tmpl.has(old_p) and not tmpl.has(new_p):
                    p_node = tmpl.get(old_p)
                    raw_pname = str(p_node.name)
                    pfx_p = raw_pname[: len(raw_pname) - len(raw_pname.lstrip())]
                    sfx_p = raw_pname[len(raw_pname.rstrip()) :]
                    p_node.name = f"{pfx_p}{new_p}{sfx_p}"
                    modified = True

            if modified and tmpl_start != -1:
                proposed_tmpl_str = str(tmpl)
                modifications.append(
                    Modification(
                        id=f"mod-{mod_id}",
                        type="cs1_modernize",
                        span_start=tmpl_start,
                        span_end=tmpl_start + len(orig_tmpl_str),
                        original_snippet=orig_tmpl_str,
                        proposed_snippet=proposed_tmpl_str,
                        description="CS1 साँचा आधुनिकीकरण (url-status व मानक पैरामीटर)",
                    )
                )
                mod_id += 1

        return wikitext, modifications

    @classmethod
    def run_all_repairs(cls, wikitext: str) -> List[Modification]:
        """Runs all syntax repairs and modernizations, returning non-overlapping modifications."""
        all_mods: List[Modification] = []
        next_id = 1

        # 1. Bracket balancing
        _, b_mods = cls.balance_template_brackets(wikitext, start_id=next_id)
        all_mods.extend(b_mods)
        next_id += len(b_mods)

        # 2. Stray pipes
        _, p_mods = cls.clean_stray_pipes(wikitext, start_id=next_id)
        all_mods.extend(p_mods)
        next_id += len(p_mods)

        # 3. Malformed ref tags
        _, r_mods = cls.fix_malformed_ref_tags(wikitext, start_id=next_id)
        all_mods.extend(r_mods)
        next_id += len(r_mods)

        # 4. CS1 parameter modernization
        _, c_mods = cls.modernize_cs1_templates(wikitext, start_id=next_id)
        all_mods.extend(c_mods)

        # Group by (span_start, span_end) to merge multiple edits on identical slices
        span_groups: dict[tuple[int, int], List[Modification]] = {}
        for m in all_mods:
            span_groups.setdefault((m.span_start, m.span_end), []).append(m)

        merged_mods: List[Modification] = []
        for (s, e), group in span_groups.items():
            if len(group) == 1:
                merged_mods.append(group[0])
            else:
                first_m = group[0]
                cur_snippet = first_m.proposed_snippet
                desc_parts = [first_m.description]
                for next_m in group[1:]:
                    if next_m.type == "cs1_modernize":
                        _, sub_c_mods = cls.modernize_cs1_templates(cur_snippet)
                        if sub_c_mods:
                            cur_snippet = sub_c_mods[0].proposed_snippet
                    elif next_m.type == "syntax_repair":
                        _, sub_p_mods = cls.clean_stray_pipes(cur_snippet)
                        if sub_p_mods:
                            cur_snippet = sub_p_mods[0].proposed_snippet
                    if next_m.description:
                        desc_parts.append(next_m.description)

                merged_mods.append(
                    Modification(
                        id=first_m.id,
                        type=first_m.type,
                        span_start=s,
                        span_end=e,
                        original_snippet=first_m.original_snippet,
                        proposed_snippet=cur_snippet,
                        description=" व ".join(d for d in desc_parts if d),
                    )
                )

        # Filter to ensure non-overlapping intervals
        sorted_m = sorted(merged_mods, key=lambda m: m.span_start)
        valid_mods: List[Modification] = []
        last_end = -1
        for m in sorted_m:
            if m.span_start >= last_end:
                valid_mods.append(m)
                last_end = m.span_end

        return valid_mods


def repair_syntax_and_modernize(
    wikitext: str,
) -> Tuple[str, List[Modification]]:
    """
    Applies all automated syntax repairs and CS1 modernizations to wikitext.
    Returns (repaired_wikitext, modifications_applied).
    """
    mods = SyntaxRepairEngine.run_all_repairs(wikitext)
    repaired = apply_granular_modifications(wikitext, mods)
    return repaired, mods


__all__ = [
    "SyntaxRepairEngine",
    "repair_syntax_and_modernize",
]
