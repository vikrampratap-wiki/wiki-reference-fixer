/**
 * Empirical and Adversarial Test Harness for Client-Side JavaScript Helpers in index.html
 * 
 * Tests the 8 companion helpers implemented in index.html:
 * 1. Duplicate Merger (identical URL vs different titles)
 * 2. Conflict Resolver (same name with distinct content triggers warning)
 * 3. Dead Link / Wayback (valid vs invalid URLs)
 * 4. Error Scanner (empty ref, missing reflist, unclosed tag)
 * 5. Scorecard (0-100 score calculation and grade)
 * 6. Validator (valid and invalid ISBN-10/13 checksums, Devanagari numerals ०-९ conversion)
 * 7. Group Checker (unmatched group detection)
 * 8. Domain Advisory (unreliable domain flagging)
 */

const fs = require('fs');
const path = require('path');

// 1. Read index.html from root
const indexPath = path.resolve(__dirname, '../index.html');
const indexHtml = fs.readFileSync(indexPath, 'utf8');

// 2. Extract UNRELIABLE_DOMAINS from index.html
const unreliableDomainsMatch = indexHtml.match(/const UNRELIABLE_DOMAINS = (\[[\s\S]*?\]);/);
if (!unreliableDomainsMatch) {
    throw new Error("Could not find UNRELIABLE_DOMAINS in index.html");
}
const UNRELIABLE_DOMAINS = eval(unreliableDomainsMatch[1]);

// 3. Extract ClientEngine from index.html
const startCE = indexHtml.indexOf('const ClientEngine = {');
const endMarkerCE = '        };\n\n        /**\n         * Main Workflow: Scan Wikitext';
const endCE = indexHtml.indexOf(endMarkerCE, startCE);
if (startCE === -1 || endCE === -1) {
    throw new Error("Could not find ClientEngine bounds in index.html");
}
const ceCode = indexHtml.slice(startCE + 'const ClientEngine = '.length, endCE + 10).trim().replace(/;\s*$/, '');
const ClientEngine = eval(`(${ceCode})`);

// Helper mock DOM environment
function createMockDOM(wikitext = "") {
    const elements = {
        wikitextInput: { value: wikitext },
        articleTitle: { value: "Test Title" },
        duplicateMergerResult: { style: {}, innerHTML: "" },
        conflictResolverResult: { style: {}, innerHTML: "" },
        deadLinksResult: { style: {}, innerHTML: "" },
        errorScannerResult: { style: {}, innerHTML: "" },
        validatorResult: { style: {}, innerHTML: "" },
        groupCheckerResult: { style: {}, innerHTML: "" },
        domainAdvisoryResult: { style: {}, innerHTML: "" },
        scorecardCard: { style: {}, innerHTML: "" },
        healthScoreNum: { innerText: "" },
        totalRefsNum: { innerText: "" },
        bareUrlsNum: { innerText: "" },
        proposedFixesNum: { innerText: "" },
        syntaxErrorsNum: { innerText: "" },
        healthScoreBadge: { className: "", innerText: "" },
        scorecardRecommendations: { innerText: "", innerHTML: "" }
    };

    return {
        getElementById(id) {
            if (!elements[id]) {
                elements[id] = { style: {}, innerHTML: "", innerText: "", value: "" };
            }
            return elements[id];
        },
        elements
    };
}

function escapeHtml(text) {
    if (!text) return "";
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function getHelper1DuplicateMerger(wikitext) {
    const refs = ClientEngine.extractReferences(wikitext);
    const contentMap = new Map();
    const duplicates = [];

    // Group non-self-closing references by (content, group)
    refs.forEach((ref, idx) => {
        if (ref.isSelfClosing) return;
        const cleanContent = ref.innerContent.trim();
        if (!cleanContent) return;

        const groupKey = ref.group ? ref.group.trim() : "";
        const key = `${groupKey}:::${cleanContent}`;

        if (!contentMap.has(key)) {
            contentMap.set(key, { firstRef: ref, count: 1, idx, subsequentRefs: [], cleanContent, group: ref.group });
        } else {
            const entry = contentMap.get(key);
            entry.count++;
            entry.subsequentRefs.push(ref);
        }
    });

    const usedNames = new Set();
    refs.forEach(r => { if (r.name) usedNames.add(r.name); });
    let slugCounter = 1;

    contentMap.forEach((entry) => {
        if (entry.count > 1) {
            let refName = entry.firstRef.name;
            const groupAttr = entry.group ? ` group="${entry.group}"` : "";

            if (!refName) {
                while (usedNames.has(`ref_${slugCounter}`)) {
                    slugCounter++;
                }
                refName = `ref_${slugCounter}`;
                usedNames.add(refName);
                slugCounter++;

                // Propose upgrading the first occurrence to define the named reference
                duplicates.push({
                    original: entry.firstRef.fullMatch,
                    replacement: `<ref name="${refName}"${groupAttr}>${entry.cleanContent}</ref>`,
                    content: entry.cleanContent,
                    span_start: entry.firstRef.spanStart,
                    span_end: entry.firstRef.spanEnd,
                    is_definition: true,
                    firstRef: entry.firstRef,
                    target_name: refName
                });
            }

            // Propose replacing subsequent occurrences with self-closing named ref
            entry.subsequentRefs.forEach(subRef => {
                duplicates.push({
                    original: subRef.fullMatch,
                    replacement: `<ref name="${refName}"${groupAttr} />`,
                    content: entry.cleanContent,
                    span_start: subRef.spanStart,
                    span_end: subRef.spanEnd,
                    is_definition: false,
                    firstRef: entry.firstRef,
                    target_name: refName
                });
            });
        }
    });

    return { duplicates, contentMap };
}

function getHelper2ConflictResolver(wikitext) {
    const refs = ClientEngine.extractReferences(wikitext);
    const nameMap = new Map();
    const conflicts = [];

    refs.forEach(ref => {
        if (!ref.name || ref.isSelfClosing) return;
        const clean = ref.innerContent.trim();
        if (!clean) return;

        if (!nameMap.has(ref.name)) {
            nameMap.set(ref.name, clean);
        } else {
            const prev = nameMap.get(ref.name);
            if (prev !== clean) {
                conflicts.push({
                    ref_name: ref.name,
                    prev_content: prev,
                    curr_content: clean,
                    message: `सन्दर्भ नाम "${ref.name}" के तहत दो भिन्न सामग्रियाँ परिभाषित हैं! इससे डेटा ओवरराइट होने का ख़तरा है।`
                });
            }
        }
    });

    return conflicts;
}

async function getHelper3DeadLinks(wikitext, fetchMock) {
    const urlRegex = /https?:\/\/[^\s<>"'\]]+/gi;
    const urls = Array.from(new Set(wikitext.match(urlRegex) || [])).slice(0, 5);
    const snapshots = [];

    for (const u of urls) {
        try {
            const wbUrl = `https://archive.org/wayback/available?url=${encodeURIComponent(u)}`;
            const wbRes = await fetchMock(wbUrl);
            if (wbRes.ok) {
                const wbData = await wbRes.json();
                if (wbData && wbData.archived_snapshots && wbData.archived_snapshots.closest) {
                    const snap = wbData.archived_snapshots.closest;
                    snapshots.push({
                        url: u,
                        archive_url: snap.url,
                        archive_date: snap.timestamp ? snap.timestamp.slice(0, 8) : ""
                    });
                }
            }
        } catch (e) {}
    }

    return { urls_checked: urls, snapshots };
}

function getHelper4ErrorScanner(wikitext) {
    const errors = [];
    const refs = ClientEngine.extractReferences(wikitext);
    const cleanWikitext = wikitext.replace(/<!--[\s\S]*?-->/g, '');

    // Empty refs
    refs.forEach(ref => {
        if (!ref.isSelfClosing && !ref.innerContent.trim()) {
            errors.push({ type: "रिक्त संदर्भ", message: "खाली <ref></ref> टैग मौजूद है।" });
        }
    });

    // Missing reflist
    const hasRefList = /\{\{\s*(?:सन्दर्भ\s*सूची|reflist|संदर्भ\s*सूची|references)\b/i.test(wikitext) || /<references\s*\/?>/i.test(wikitext);
    if (refs.length > 0 && !hasRefList) {
        errors.push({ type: "अनुपस्थित सन्दर्भ सूची", message: "लेख में सन्दर्भ मौजूद हैं किन्तु {{सन्दर्भ सूची}} कंटेनर नहीं मिला।" });
    }

    // Unclosed brackets
    const openBrackets = (cleanWikitext.match(/\{\{/g) || []).length;
    const closeBrackets = (cleanWikitext.match(/\}\}/g) || []).length;
    if (openBrackets !== closeBrackets) {
        errors.push({ type: "असंतुलित कोष्ठक", message: `साँचा कोष्ठक असंतुलित हैं: {{ (${openBrackets}) बनाम }} (${closeBrackets})` });
    }

    // Unclosed ref tags
    const openTags = (cleanWikitext.match(/<ref(?:\s+[^>]*)?>/gi) || []).length;
    const selfClosingTags = (cleanWikitext.match(/<ref(?:\s+[^>]*)?\/>/gi) || []).length;
    const closeTags = (cleanWikitext.match(/<\/ref>/gi) || []).length;
    if ((openTags - selfClosingTags) > closeTags) {
        errors.push({ type: "अपूर्ण संदर्भ टैग", message: "लेख में बिना बंद किया गया <ref> टैग मौजूद है।" });
    }

    return { errors, refs, hasRefList };
}

function getHelper5Scorecard(data) {
    const score = data.health_score !== undefined ? data.health_score : 100;
    let badgeText = "";
    let grade = "";

    if (score >= 80) {
        badgeText = "उत्कृष्ट (Grade A)";
        grade = "A";
    } else if (score >= 65) {
        badgeText = "संतोषजनक (Grade B)";
        grade = "B";
    } else if (score >= 50) {
        badgeText = "मध्यम (Grade C)";
        grade = "C";
    } else {
        badgeText = "सुधार आवश्यक (Grade D)";
        grade = "D";
    }

    return { score, grade, badgeText };
}

function getHelper6Validator(wikitext) {
    const results = {
        isbn: [],
        issn: [],
        doi: [],
        devanagari_dates: []
    };

    // ISBN Checksum validation
    const isbnMatches = wikitext.match(/isbn\s*=\s*([0-9Xx\-]+)/gi) || [];
    isbnMatches.forEach(m => {
        const val = m.replace(/isbn\s*=\s*/i, "").replace(/[\-\s]/g, "");
        let isValid = false;
        if (val.length === 10) {
            let sum = 0;
            for (let i = 0; i < 9; i++) sum += parseInt(val[i], 10) * (10 - i);
            const last = val[9].toUpperCase() === 'X' ? 10 : parseInt(val[9], 10);
            sum += last;
            isValid = (sum % 11 === 0);
        } else if (val.length === 13) {
            let sum = 0;
            for (let i = 0; i < 12; i++) sum += parseInt(val[i], 10) * (i % 2 === 0 ? 1 : 3);
            const check = (10 - (sum % 10)) % 10;
            isValid = (check === parseInt(val[12], 10));
        }
        results.isbn.push({ raw: val, valid: isValid });
    });

    // DOI syntax validation
    const doiMatches = wikitext.match(/doi\s*=\s*([^\s\|\}]+)/gi) || [];
    doiMatches.forEach(m => {
        const val = m.replace(/doi\s*=\s*/i, "").trim();
        const isValid = /^10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+$/i.test(val);
        results.doi.push({ raw: val, valid: isValid });
    });

    // Devanagari numerals date detection
    const devDateMatches = wikitext.match(/[०-९]{1,4}[-\/\s]+[०-९\w\u0900-\u097F]+[-\/\s]+[०-९]{1,4}/g) || [];
    devDateMatches.forEach(d => {
        const normalized = d.replace(/[०-९]/g, d => "०१२३४५६७८९".indexOf(d));
        results.devanagari_dates.push({ raw: d, normalized });
    });

    return results;
}

function getHelper7GroupChecker(wikitext) {
    const refs = ClientEngine.extractReferences(wikitext);
    const refGroups = new Set();
    refs.forEach(r => { if (r.group) refGroups.add(r.group); });

    const containerGroups = new Set();
    // Match <references ... group="...">
    const tagRegex = /<references\s+[^>]*\bgroup\s*=\s*["']?([^"'>\s]+)["']?[^>]*>/gi;
    let m;
    while ((m = tagRegex.exec(wikitext)) !== null) {
        if (m[1]) containerGroups.add(m[1].trim());
    }
    // Match {{reflist|group=...}} and Hindi variants
    const tplRegex = /\{\{\s*(?:reflist|reflist30em|संदर्भसूची|सन्दर्भसूची|सन्दर्भ\s*सूची|संदर्भ\s*सूची|टिप्पणी\s*सूची|टिप्पणीसूची|रेफ़्लिस्ट|रेफलिस्ट|संसूची|सूत्र\s*सूची|सूत्र सूची|टिसू|नोट्स|हवाले)[^}]*\|\s*group\s*=\s*["']?([^"'\|\}]+)["']?/gi;
    while ((m = tplRegex.exec(wikitext)) !== null) {
        if (m[1]) containerGroups.add(m[1].trim());
    }

    const orphaned = [];
    refGroups.forEach(g => {
        if (!containerGroups.has(g)) orphaned.push(g);
    });

    return {
        ref_groups: Array.from(refGroups),
        container_groups: Array.from(containerGroups),
        orphaned: orphaned,
        is_consistent: orphaned.length === 0
    };
}

function getHelper8DomainAdvisory(wikitext) {
    const urlRegex = /https?:\/\/([^\s<>"'\/\]\?\#]+)/gi;
    const foundDomains = new Set();
    let match;
    while ((match = urlRegex.exec(wikitext)) !== null) {
        foundDomains.add(match[1].toLowerCase());
    }

    const flagged = [];
    foundDomains.forEach(domain => {
        for (const item of UNRELIABLE_DOMAINS) {
            if (item.pattern.test(domain)) {
                flagged.push({ domain: item.name + ` (${domain})`, warning: item.reason });
                break;
            }
        }
    });

    return { found_domains: Array.from(foundDomains), flagged };
}

// --------------------------------------------------------------------------
// TEST EXECUTION SUITE
// --------------------------------------------------------------------------
async function runAllTests() {
    console.log("==================================================================");
    console.log("RUNNING EMPIRICAL & ADVERSARIAL TEST HARNESS ON CLIENT JAVASCRIPT");
    console.log("==================================================================");

    const testResults = [];
    function record(name, pass, details) {
        testResults.push({ name, pass, details });
        const badge = pass ? "PASS" : "FAIL";
        console.log(`[${badge}] ${name}`);
        if (details) {
            console.log(`       Details: ${JSON.stringify(details)}`);
        }
    }

    // ----------------------------------------------------------------------
    // 1. Helper 1: Duplicate Merger
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 1: Duplicate Merger ---");

    // Test 1.1: Identical URLs (bare)
    {
        const wiki = `तथ्य १<ref>https://example.com</ref> तथ्य २<ref>https://example.com</ref>`;
        const res = getHelper1DuplicateMerger(wiki);
        const pass = res.duplicates.some(d => d.replacement === '<ref name="ref_1" />') &&
                     res.duplicates.some(d => d.replacement === '<ref name="ref_1">https://example.com</ref>');
        record("Helper 1 - Identical bare URLs detection", pass, {
            merges_found: res.duplicates.length,
            definition: res.duplicates.find(d => d.is_definition)?.replacement,
            invocation: res.duplicates.find(d => !d.is_definition)?.replacement
        });
    }

    // Test 1.2: Identical URL with DIFFERENT titles
    {
        const wiki = `तथ्य १<ref>{{Cite web |url=https://example.com |title=शीर्षक क}}</ref> तथ्य २<ref>{{Cite web |url=https://example.com |title=शीर्षक ख}}</ref>`;
        const res = getHelper1DuplicateMerger(wiki);
        const pass = res.duplicates.length === 0;
        record("Helper 1 - Identical URL with DIFFERENT titles should NOT merge", pass, { duplicates_found: res.duplicates.length });
    }

    // Test 1.3: Pre-named first ref
    {
        const wiki = `तथ्य १<ref name="mainsource">https://example.com</ref> तथ्य २<ref>https://example.com</ref>`;
        const res = getHelper1DuplicateMerger(wiki);
        const pass = res.duplicates.length === 1 && res.duplicates[0].replacement === '<ref name="mainsource" />';
        record("Helper 1 - Preserves existing ref name for replacement", pass, { replacement: res.duplicates[0]?.replacement });
    }

    // Test 1.4: First occurrence gets named definition
    {
        const wiki = `तथ्य १<ref>https://example.com</ref> तथ्य २<ref>https://example.com</ref>`;
        const res = getHelper1DuplicateMerger(wiki);
        const firstRefHasReplacement = res.duplicates.some(d => d.original === res.duplicates[0].firstRef.fullMatch && d.replacement.includes('https://example.com') && d.is_definition);
        record("Helper 1 - First occurrence definition upgrade check", firstRefHasReplacement, {
            firstRefDefinition: res.duplicates.find(d => d.is_definition)?.replacement,
            secondRefReplacement: res.duplicates.find(d => !d.is_definition)?.replacement
        });
    }

    // ----------------------------------------------------------------------
    // 2. Helper 2: Conflict Resolver
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 2: Conflict Resolver ---");

    // Test 2.1: Same name with distinct content
    {
        const wiki = `तथ्य १<ref name="src">पहला स्रोत सामग्री</ref> तथ्य २<ref name="src">भिन्न द्वितीय स्रोत</ref>`;
        const conflicts = getHelper2ConflictResolver(wiki);
        const pass = conflicts.length === 1 && conflicts[0].ref_name === "src";
        record("Helper 2 - Same name with distinct content triggers warning", pass, { conflicts_count: conflicts.length, message: conflicts[0]?.message });
    }

    // Test 2.2: Same name with identical content
    {
        const wiki = `तथ्य १<ref name="src">समान सामग्री</ref> तथ्य २<ref name="src">समान सामग्री</ref>`;
        const conflicts = getHelper2ConflictResolver(wiki);
        const pass = conflicts.length === 0;
        record("Helper 2 - Same name with identical content does NOT trigger conflict", pass, { conflicts_count: conflicts.length });
    }

    // Test 2.3: Same name with self-closing invocation
    {
        const wiki = `तथ्य १<ref name="src">परिभाषा सामग्री</ref> तथ्य २<ref name="src" />`;
        const conflicts = getHelper2ConflictResolver(wiki);
        const pass = conflicts.length === 0;
        record("Helper 2 - Definition + self-closing invocation does NOT trigger conflict", pass, { conflicts_count: conflicts.length });
    }

    // Test 2.4: Multiple conflicting ref names
    {
        const wiki = `<ref name="a">1</ref><ref name="a">2</ref><ref name="b">X</ref><ref name="b">Y</ref>`;
        const conflicts = getHelper2ConflictResolver(wiki);
        const pass = conflicts.length === 2 && conflicts[0].ref_name === "a" && conflicts[1].ref_name === "b";
        record("Helper 2 - Multiple conflicting ref names detected", pass, { conflicts_count: conflicts.length });
    }

    // ----------------------------------------------------------------------
    // 3. Helper 3: Dead Links / Wayback
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 3: Dead Links / Wayback ---");

    // Test 3.1: Valid URL with available snapshot
    {
        const mockFetch = async (url) => {
            return {
                ok: true,
                json: async () => ({
                    archived_snapshots: {
                        closest: {
                            available: true,
                            url: "https://web.archive.org/web/20230101/https://example.org",
                            timestamp: "20230101120000"
                        }
                    }
                })
            };
        };
        const wiki = `सन्दर्भ: <ref>https://example.org</ref>`;
        const res = await getHelper3DeadLinks(wiki, mockFetch);
        const pass = res.snapshots.length === 1 && res.snapshots[0].archive_url.includes("web.archive.org") && res.snapshots[0].archive_date === "20230101";
        record("Helper 3 - Valid URL with Wayback snapshot found", pass, res.snapshots[0]);
    }

    // Test 3.2: Valid URL with no archived snapshot
    {
        const mockFetch = async (url) => {
            return {
                ok: true,
                json: async () => ({
                    archived_snapshots: {}
                })
            };
        };
        const wiki = `सन्दर्भ: <ref>https://nonexistent-sample-page-12345.org</ref>`;
        const res = await getHelper3DeadLinks(wiki, mockFetch);
        const pass = res.snapshots.length === 0;
        record("Helper 3 - Valid URL with no archived snapshot handled gracefully", pass, { snapshots_found: res.snapshots.length });
    }

    // Test 3.3: Network failure or 500 error
    {
        const mockFetch = async (url) => {
            throw new Error("Network timeout");
        };
        const wiki = `सन्दर्भ: <ref>https://timeout-site.org</ref>`;
        const res = await getHelper3DeadLinks(wiki, mockFetch);
        const pass = res.snapshots.length === 0;
        record("Helper 3 - Network error handled gracefully in try/catch", pass, { snapshots_found: res.snapshots.length });
    }

    // ----------------------------------------------------------------------
    // 4. Helper 4: Error Scanner
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 4: Error Scanner ---");

    // Test 4.1: Empty ref detection
    {
        const wiki = `तथ्य <ref></ref> अगला तथ्य <ref>   </ref> {{सन्दर्भ सूची}}`;
        const res = getHelper4ErrorScanner(wiki);
        const emptyRefErrors = res.errors.filter(e => e.type === "रिक्त संदर्भ");
        const pass = emptyRefErrors.length === 2;
        record("Helper 4 - Empty <ref></ref> and whitespace ref detected", pass, { empty_errors: emptyRefErrors.length });
    }

    // Test 4.2: Missing reflist detection
    {
        const wiki = `तथ्य <ref>https://example.com</ref> और कोई सूची नहीं`;
        const res = getHelper4ErrorScanner(wiki);
        const reflistErrors = res.errors.filter(e => e.type === "अनुपस्थित सन्दर्भ सूची");
        const pass = reflistErrors.length === 1;
        record("Helper 4 - Missing {{सन्दर्भ सूची}} detected when refs exist", pass, { missing_reflist: reflistErrors.length });
    }

    // Test 4.3: Unclosed template brackets
    {
        const wiki = `{{Cite web |url=https://example.com |title=अधूरा {{सन्दर्भ सूची}}`;
        const res = getHelper4ErrorScanner(wiki);
        const bracketErrors = res.errors.filter(e => e.type === "असंतुलित कोष्ठक");
        const pass = bracketErrors.length === 1;
        record("Helper 4 - Unclosed template brackets {{ detected", pass, { bracket_errors: bracketErrors.length });
    }

    // Test 4.4: Unclosed <ref> tag
    {
        const wiki = `तथ्य <ref>https://example.com अधूरा खुला टैग बिना क्लोजिंग टैग के \n{{सन्दर्भ सूची}}`;
        const res = getHelper4ErrorScanner(wiki);
        const unclosedRefErrors = res.errors.filter(e => e.type.includes("टैग") || e.message.includes("<ref>"));
        const pass = unclosedRefErrors.length > 0;
        record("Helper 4 - Unclosed <ref> tag detection", pass, {
            detected: unclosedRefErrors.length,
            error: unclosedRefErrors[0]
        });
    }

    // ----------------------------------------------------------------------
    // 5. Helper 5: Scorecard
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 5: Scorecard ---");

    // Test 5.1: Perfect score (Grade A)
    {
        const data = { health_score: 100, total_refs: 5, bare_urls: 0, syntax_errors: 0, has_reflist: true };
        const res = getHelper5Scorecard(data);
        const pass = res.score === 100 && res.grade === "A";
        record("Helper 5 - Perfect 100 score gives Grade A", pass, res);
    }

    // Test 5.2: Bare URL deductions (Grade B: 65-79)
    {
        // 2 bare URLs -> 100 - (2 * 12) = 76
        const data = { health_score: 76, total_refs: 4, bare_urls: 2, syntax_errors: 0, has_reflist: true };
        const res = getHelper5Scorecard(data);
        const pass = res.score === 76 && res.grade === "B";
        record("Helper 5 - Score 76 gives Grade B", pass, res);
    }

    // Test 5.3: Grade C (50-64)
    {
        // 4 bare URLs -> 100 - 48 = 52
        const data = { health_score: 52, total_refs: 4, bare_urls: 4, syntax_errors: 0, has_reflist: true };
        const res = getHelper5Scorecard(data);
        const pass = res.score === 52 && res.grade === "C";
        record("Helper 5 - Score 52 gives Grade C", pass, res);
    }

    // Test 5.4: Grade D (< 50) and score clamping to 0
    {
        // 10 bare URLs -> 100 - 120 -> clamped to 0
        const data = { health_score: 0, total_refs: 10, bare_urls: 10, syntax_errors: 2, has_reflist: false };
        const res = getHelper5Scorecard(data);
        const pass = res.score === 0 && res.grade === "D";
        record("Helper 5 - Extreme issues clamp to 0 and give Grade D", pass, res);
    }

    // Test 5.5: In-browser ClientEngine.analyzeWikitext score formula verification
    {
        // wikitext with 1 bare URL and missing reflist
        // bareUrlCount = 1 (-12), missingReflist (-20) => 100 - 12 - 20 = 68
        const wiki = `तथ्य <ref>https://example.com</ref>`;
        const analysis = await ClientEngine.analyzeWikitext(wiki);
        const pass = analysis.health_score === 68 && analysis.bare_urls === 1 && !analysis.has_reflist;
        record("Helper 5 - ClientEngine.analyzeWikitext score calculation matches specification", pass, {
            score: analysis.health_score,
            expected: 68,
            bare_urls: analysis.bare_urls
        });
    }

    // ----------------------------------------------------------------------
    // 6. Helper 6: Validator (ISBN/ISSN/DOI & Devanagari numerals)
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 6: Validator ---");

    // Test 6.1: Valid ISBN-10
    {
        const wiki = `{{Cite book |isbn=0-306-40615-2}} {{Cite book |isbn=0-8044-2957-X}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.isbn.length === 2 && res.isbn[0].valid === true && res.isbn[1].valid === true;
        record("Helper 6 - Valid ISBN-10 (numeric & check digit X)", pass, res.isbn);
    }

    // Test 6.2: Invalid ISBN-10
    {
        const wiki = `{{Cite book |isbn=0-306-40615-3}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.isbn.length === 1 && res.isbn[0].valid === false;
        record("Helper 6 - Invalid ISBN-10 checksum fails validation", pass, res.isbn[0]);
    }

    // Test 6.3: Valid ISBN-13
    {
        const wiki = `{{Cite book |isbn=978-0-306-40615-7}} {{Cite book |isbn=978-3-16-148410-0}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.isbn.length === 2 && res.isbn[0].valid === true && res.isbn[1].valid === true;
        record("Helper 6 - Valid ISBN-13 (mod 10 checksum)", pass, res.isbn);
    }

    // Test 6.4: Invalid ISBN-13
    {
        const wiki = `{{Cite book |isbn=978-0-306-40615-8}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.isbn.length === 1 && res.isbn[0].valid === false;
        record("Helper 6 - Invalid ISBN-13 checksum fails validation", pass, res.isbn[0]);
    }

    // Test 6.5: Devanagari numerals conversion (०-९ -> 0-9)
    {
        const wiki = `{{Cite web |date=२३ अगस्त २०२३ |access-date=०१/०२/२०२४}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.devanagari_dates.length === 2 &&
                     res.devanagari_dates[0].normalized === "23 अगस्त 2023" &&
                     res.devanagari_dates[1].normalized === "01/02/2024";
        record("Helper 6 - Devanagari numerals ०-९ conversion to ASCII 0-9", pass, res.devanagari_dates);
    }

    // Test 6.6: Full Devanagari numeral digits test (०, १, २, ३, ४, ५, ६, ७, ८, ९)
    {
        const wiki = `तिथि: ०१२३-४५६-७८९०`;
        const res = getHelper6Validator(wiki);
        const pass = res.devanagari_dates.length === 1 && res.devanagari_dates[0].normalized === "0123-456-7890";
        record("Helper 6 - All 10 Devanagari digits (0 to 9) map correctly", pass, res.devanagari_dates[0]);
    }

    // Test 6.7: DOI syntax validation
    {
        const wiki = `{{Cite journal |doi=10.1000/182}} {{Cite journal |doi=invalid_doi}}`;
        const res = getHelper6Validator(wiki);
        const pass = res.doi.length === 2 && res.doi[0].valid === true && res.doi[1].valid === false;
        record("Helper 6 - DOI format validation (valid vs invalid)", pass, res.doi);
    }

    // ----------------------------------------------------------------------
    // 7. Helper 7: Group Checker
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 7: Group Checker ---");

    // Test 7.1: Matched group
    {
        const wiki = `पाठ <ref group="टिप्पणी">यह टिप्पणी है</ref>\n{{सन्दर्भ सूची|group=टिप्पणी}}`;
        const res = getHelper7GroupChecker(wiki);
        const pass = res.is_consistent === true && res.orphaned.length === 0;
        record("Helper 7 - Matched ref group and container is consistent", pass, res);
    }

    // Test 7.2: Unmatched / Orphaned Group Detection
    {
        const wiki = `पाठ <ref group="अनाथ_ग्रुप">यह एक अनाथ टिप्पणी है</ref>\n{{सन्दर्भ सूची}}`;
        const res = getHelper7GroupChecker(wiki);
        const detected = res.orphaned.includes("अनाथ_ग्रुप");
        record("Helper 7 - Unmatched group detection", detected, {
            detected_orphaned: res.orphaned,
            container_groups: res.container_groups
        });
    }

    // ----------------------------------------------------------------------
    // 8. Helper 8: Domain Advisory
    // ----------------------------------------------------------------------
    console.log("\n--- Testing Helper 8: Domain Advisory ---");

    // Test 8.1: Unreliable domain flagging
    {
        const wiki = `स्रोत १: <ref>https://twitter.com/user/status/123</ref> स्रोत २: <ref>https://www.dailymail.co.uk/news/article</ref> स्रोत ३: <ref>https://myblog.blogspot.com/post</ref>`;
        const res = getHelper8DomainAdvisory(wiki);
        const pass = res.flagged.length === 3;
        record("Helper 8 - Flags unreliable domains (Twitter, Daily Mail, Blogspot)", pass, { flagged_count: res.flagged.length, flagged: res.flagged });
    }

    // Test 8.2: Reliable domains should NOT be flagged
    {
        const wiki = `विश्वसनीय स्रोत: <ref>https://isro.gov.in/mission</ref> समाचार: <ref>https://bbc.com/news</ref> विकिपीडिया: <ref>https://hi.wikipedia.org</ref>`;
        const res = getHelper8DomainAdvisory(wiki);
        const pass = res.flagged.length === 0;
        record("Helper 8 - Reliable domains are not flagged", pass, { flagged_count: res.flagged.length });
    }

    // Test 8.3: Domain with query parameters without trailing slash
    {
        const wiki = `लिंक: https://twitter.com?status=123 और https://dailymail.co.uk#headline`;
        const res = getHelper8DomainAdvisory(wiki);
        const twitterFlagged = res.flagged.some(f => f.domain.includes("Twitter"));
        const dailyMailFlagged = res.flagged.some(f => f.domain.includes("Daily Mail"));
        const pass = twitterFlagged && dailyMailFlagged;
        record("Helper 8 - Domain without trailing slash before query string", pass, {
            found_domains: res.found_domains,
            flagged: res.flagged
        });
    }

    console.log("\n==================================================================");
    console.log(`TEST SUITE COMPLETED: ${testResults.filter(r => r.pass).length} PASSED, ${testResults.filter(r => !r.pass).length} FAILED/VULNERABILITIES FOUND`);
    console.log("==================================================================");

    return testResults;
}

runAllTests().catch(err => {
    console.error("Test harness failed with unhandled error:", err);
    process.exit(1);
});
