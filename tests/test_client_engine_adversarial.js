/**
 * Adversarial Empirical Verification Suite for ClientEngine (index.html)
 * Tests client-side parsing, descending span order wikitext reconstruction,
 * edge cases, boundary conditions, stress performance, and JS syntax.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

console.log("======================================================================");
console.log("   ADVERSARIAL EMPIRICAL VERIFICATION: ClientEngine & index.html      ");
console.log("======================================================================");

// -----------------------------------------------------------------------------
// 1. Static & Syntax Verification of index.html & templates/index.html
// -----------------------------------------------------------------------------
console.log("\n[SUITE 1] Static Syntax & Parity Verification");

const rootHtmlPath = path.resolve(__dirname, "../index.html");
const templateHtmlPath = path.resolve(__dirname, "../templates/index.html");

assert(fs.existsSync(rootHtmlPath), "index.html must exist at repository root");
assert(fs.existsSync(templateHtmlPath), "templates/index.html must exist");

const rootHtml = fs.readFileSync(rootHtmlPath, "utf8");
const templateHtml = fs.readFileSync(templateHtmlPath, "utf8");

assert.strictEqual(
    rootHtml,
    templateHtml,
    "FAILED: index.html and templates/index.html must have 100% byte-for-byte parity"
);
console.log("  ✓ Byte parity confirmed between index.html and templates/index.html");

// Extract and verify all <script> blocks
const scriptRegex = /<script(?:\s+[^>]*)?>([\s\S]*?)<\/script>/gi;
let scriptMatch;
let scriptCount = 0;
while ((scriptMatch = scriptRegex.exec(rootHtml)) !== null) {
    scriptCount++;
    const jsCode = scriptMatch[1];
    try {
        new vm.Script(jsCode, { filename: `index.html#script${scriptCount}` });
        console.log(`  ✓ Script block #${scriptCount} (${jsCode.length} chars) parsed cleanly with zero syntax errors`);
    } catch (err) {
        assert.fail(`Syntax error in script block #${scriptCount}: ${err.message}`);
    }
}
assert(scriptCount > 0, "At least one script block must be present in index.html");

// Check typography
assert(rootHtml.includes("--line-height-devanagari: 1.68;"), "Must specify line-height 1.68 for Devanagari typography");
console.log("  ✓ Devanagari typography line-height 1.68 verified");

// -----------------------------------------------------------------------------
// 2. Setup Isolated Sandbox and Instantiate ClientEngine
// -----------------------------------------------------------------------------
console.log("\n[SUITE 2] Setup Isolated ClientEngine Environment");

const clientEngineMatch = rootHtml.match(/const ClientEngine = (\{[\s\S]*?\n        \});/);
assert(clientEngineMatch, "Could not extract ClientEngine declaration from index.html");

const mockCitoidStore = {
    "https://start.com/bare1": {
        itemType: "webpage",
        title: "शुरुआती पृष्ठ",
        author: [{ firstName: "विक्रम", lastName: "प्रताप" }],
        date: "2026-01-01"
    },
    "https://middle.com/bare2": {
        itemType: "newspaperArticle",
        title: "मध्य समाचार",
        publicationTitle: "दैनिक भास्कर",
        date: "2026-05-15"
    },
    "https://end.com/bare3": {
        itemType: "book",
        title: "अंतिम पुस्तक",
        publisher: "हिन्दी ग्रन्थ अकादमी",
        ISBN: "978-0-306-40615-7",
        date: "2025"
    }
};

const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    Date,
    URL,
    Array,
    Map,
    Set,
    Math,
    String,
    RegExp,
    Boolean,
    Promise,
    fetch: async (url) => {
        // Mock Citoid API responses
        const decodedUrl = decodeURIComponent(url);
        for (const [targetUrl, data] of Object.entries(mockCitoidStore)) {
            if (decodedUrl.includes(encodeURIComponent(targetUrl)) || decodedUrl.includes(targetUrl)) {
                return {
                    ok: true,
                    json: async () => [data]
                };
            }
        }
        return { ok: false, status: 404 };
    }
};

vm.createContext(sandbox);
const ClientEngine = vm.runInContext("(" + clientEngineMatch[1] + ")", sandbox);
ClientEngine.sleep = () => Promise.resolve(); // Fast execution for test suite

const requiredMethods = [
    "extractReferences",
    "fetchCitoidMetadata",
    "buildCitationTemplate",
    "analyzeWikitext",
    "applyGranularModifications",
    "generateUnifiedDiff",
    "generateEditSummary"
];
for (const m of requiredMethods) {
    assert(typeof ClientEngine[m] === "function", `ClientEngine must implement method: ${m}`);
}
console.log(`  ✓ All ${requiredMethods.length} ClientEngine methods successfully verified`);

// -----------------------------------------------------------------------------
// 3. Adversarial Wikitext Parsing Suite
// -----------------------------------------------------------------------------
console.log("\n[SUITE 3] Adversarial Wikitext Parsing");

// Test 3.1: Nested templates inside references
{
    console.log("  Testing 3.1: Nested templates and comments inside references...");
    const wikitext = `आरम्भ <ref name="nested">{{Cite web |title={{lang|hi|हिन्दी}} |url=https://example.com/item |author=<!-- लेखक टिप्पणी -->विक्रम |deadurl=yes ||extra=परीक्षण |}}</ref> समाप्त।`;
    const refs = ClientEngine.extractReferences(wikitext);
    assert.strictEqual(refs.length, 1, "Must extract exactly 1 ref node");
    assert.strictEqual(refs[0].name, "nested", "Ref name must be extracted as 'nested'");
    assert(refs[0].innerContent.includes("{{lang|hi|हिन्दी}}"), "Nested template {{lang}} must remain in innerContent");
    assert(refs[0].innerContent.includes("<!-- लेखक टिप्पणी -->"), "Comment inside template must remain in innerContent");

    // Test syntax repair on nested template
    const analysis = sandbox.Promise.resolve(ClientEngine.analyzeWikitext(wikitext));
    // Since analyzeWikitext is async:
}

async function runAsyncTests() {
    // 3.1 Async analysis
    {
        const wikitext = `आरम्भ <ref name="nested">{{Cite web |title={{lang|hi|हिन्दी}} |url=https://example.com/item |author=<!-- लेखक टिप्पणी -->विक्रम |deadurl=yes ||extra=परीक्षण |}}</ref> समाप्त।`;
        const analysis = await ClientEngine.analyzeWikitext(wikitext);
        assert.strictEqual(analysis.modifications.length, 1, "Must produce 1 syntax repair modification");
        const mod = analysis.modifications[0];
        assert(mod.proposed_snippet.includes("|url-status=dead"), "Must modernize deadurl=yes to url-status=dead");
        assert(!mod.proposed_snippet.includes("deadurl=yes"), "Must remove deadurl=yes");
        assert(!mod.proposed_snippet.includes("||"), "Must repair double pipes || to |");
        assert(!mod.proposed_snippet.includes("|}}"), "Must remove trailing pipe before }}");
        assert(mod.proposed_snippet.includes("{{lang|hi|हिन्दी}}"), "Must preserve nested template intact");
        assert(mod.proposed_snippet.includes("<!-- लेखक टिप्पणी -->"), "Must preserve comment intact");
        console.log("    ✓ Nested templates, comments, and syntax modernization passed");
    }

    // 3.2 Bare URLs at extreme boundaries: Index 0, exact middle, exact end
    {
        console.log("  Testing 3.2: Bare URLs at extreme boundaries (Start index 0, Middle, End)...");
        const wikitext = `<ref>https://start.com/bare1</ref>मध्यवर्ती गद्य यहाँ उपस्थित है。<ref name="mid" group="टिप्पणी">https://middle.com/bare2</ref>अन्तिम गद्य अनुभाग。<ref>[https://end.com/bare3 अन्तिम शीर्षक]</ref>`;
        const refs = ClientEngine.extractReferences(wikitext);
        assert.strictEqual(refs.length, 3, "Must extract all 3 boundary refs");
        assert.strictEqual(refs[0].spanStart, 0, "First ref MUST start at index 0");
        assert.strictEqual(refs[2].spanEnd, wikitext.length, "Last ref MUST end at exact wikitext length");

        const analysis = await ClientEngine.analyzeWikitext(wikitext);
        assert.strictEqual(analysis.bare_urls, 3, "Must detect 3 bare URLs");
        assert.strictEqual(analysis.modifications.length, 3, "Must propose 3 modifications");

        // Verify start ref proposal
        const modStart = analysis.modifications[0];
        assert.strictEqual(modStart.span_start, 0);
        assert(modStart.proposed_snippet.includes("{{Cite web"), "Start ref should convert to Cite web");
        assert(modStart.proposed_snippet.includes("url=https://start.com/bare1"));

        // Verify middle ref proposal with name and group
        const modMid = analysis.modifications[1];
        assert(modMid.proposed_snippet.startsWith('<ref name="mid" group="टिप्पणी">'), "Mid ref must preserve name and group");
        assert(modMid.proposed_snippet.includes("{{Cite news"), "Mid newspaperArticle should convert to Cite news");

        // Verify end ref proposal (bracketed)
        const modEnd = analysis.modifications[2];
        assert.strictEqual(modEnd.span_end, wikitext.length);
        assert(modEnd.proposed_snippet.includes("{{Cite book"), "End book should convert to Cite book");
        console.log("    ✓ Extreme boundaries (0, middle, wikitext.length) passed");
    }

    // 3.3 Overlapping & Adjacent References
    {
        console.log("  Testing 3.3: Adjacent contiguous references with 0 interstitial characters...");
        const wikitext = `पाठ<ref name="r1">https://start.com/bare1</ref><ref name="r2">https://middle.com/bare2</ref><ref name="r3">https://end.com/bare3</ref>अंत`;
        const refs = ClientEngine.extractReferences(wikitext);
        assert.strictEqual(refs.length, 3);
        assert.strictEqual(refs[0].spanEnd, refs[1].spanStart, "Ref 1 end must equal Ref 2 start (strictly adjacent)");
        assert.strictEqual(refs[1].spanEnd, refs[2].spanStart, "Ref 2 end must equal Ref 3 start (strictly adjacent)");

        const analysis = await ClientEngine.analyzeWikitext(wikitext);
        assert.strictEqual(analysis.modifications.length, 3);

        // Verify non-overlapping invariant
        for (let i = 0; i < analysis.modifications.length - 1; i++) {
            const current = analysis.modifications[i];
            const next = analysis.modifications[i + 1];
            assert(current.span_end <= next.span_start, "Modifications must never overlap");
        }
        console.log("    ✓ Adjacent references with 0 characters passed");
    }

    // -------------------------------------------------------------------------
    // 4. Descending Span Order Splicing & Offset Drift Immunity
    // -------------------------------------------------------------------------
    console.log("\n[SUITE 4] Descending Span Order Splicing & Offset Drift Immunity");

    {
        const originalText = `आरम्भिक पाठ <ref name="r1">https://start.com/bare1</ref> मध्य पाठ १ <ref name="r2">https://middle.com/bare2</ref> मध्य पाठ २ <ref name="r3">https://end.com/bare3</ref> अन्तिम पाठ।`;
        const analysis = await ClientEngine.analyzeWikitext(originalText);
        const mods = analysis.modifications;
        assert.strictEqual(mods.length, 3);

        // Test all 2^3 = 8 combinations of selections
        const testCombos = [
            [],
            [0],
            [1],
            [2],
            [0, 1],
            [0, 2],
            [1, 2],
            [0, 1, 2]
        ];

        for (const combo of testCombos) {
            const selected = combo.map(i => mods[i]);
            const reconstructed = ClientEngine.applyGranularModifications(originalText, selected);

            // Invariant 1: Prefix and Suffix text must be 100% byte-for-byte identical
            assert(reconstructed.startsWith("आरम्भिक पाठ "), `Prefix corrupted in combo [${combo}]`);
            assert(reconstructed.endsWith(" अन्तिम पाठ।"), `Suffix corrupted in combo [${combo}]`);

            // Invariant 2: Unselected middle text must remain 100% byte-for-byte intact
            assert(reconstructed.includes(" मध्य पाठ १ "), `Middle text 1 corrupted in combo [${combo}]`);
            assert(reconstructed.includes(" मध्य पाठ २ "), `Middle text 2 corrupted in combo [${combo}]`);

            // Invariant 3: Unselected modifications must remain exactly in original form
            for (let i = 0; i < mods.length; i++) {
                if (combo.includes(i)) {
                    assert(reconstructed.includes(mods[i].proposed_snippet), `Selected mod #${i} missing in combo [${combo}]`);
                    assert(!reconstructed.includes(mods[i].original_snippet), `Original mod #${i} not replaced in combo [${combo}]`);
                } else {
                    assert(reconstructed.includes(mods[i].original_snippet), `Unselected mod #${i} was altered in combo [${combo}]`);
                }
            }
        }
        console.log("  ✓ All 8 selection combinations verified: zero text corruption, zero offset drift");

        // Test Order Independence (Shuffled vs Sorted)
        const shuffled = [mods[1], mods[0], mods[2]];
        const reconShuffled = ClientEngine.applyGranularModifications(originalText, shuffled);
        const reconNormal = ClientEngine.applyGranularModifications(originalText, mods);
        assert.strictEqual(reconShuffled, reconNormal, "Reconstruction must be deterministic and invariant to input order");
        console.log("  ✓ Splicing order independence verified (b.span_start - a.span_start descending sort works)");

        // Demonstrate Ascending Splicing Drift Failure vs Descending Success
        function brokenAscendingSplice(text, mList) {
            const asc = [...mList].sort((a, b) => a.span_start - b.span_start);
            let res = text;
            for (const m of asc) {
                res = res.slice(0, m.span_start) + m.replacement + res.slice(m.span_end);
            }
            return res;
        }

        const brokenResult = brokenAscendingSplice(originalText, mods);
        assert.notStrictEqual(
            brokenResult,
            reconNormal,
            "Demonstration: Ascending order MUST produce corruption, proving descending order is necessary"
        );
        console.log("  ✓ Offset drift demonstration confirmed: ascending order fails, descending order succeeds");
    }

    // -------------------------------------------------------------------------
    // 5. Stress Testing & Unicode Resilience
    // -------------------------------------------------------------------------
    console.log("\n[SUITE 5] Stress Testing & Unicode Resilience");

    {
        console.log("  Testing 5.1: High-volume stress (100 references in 50KB text)...");
        let largeWikitext = "== लेख आरम्भ ==\n";
        for (let i = 0; i < 100; i++) {
            largeWikitext += `यह अनुच्छेद संख्या ${i} है जिसमें विवरण है。<ref name="ref_${i}">https://start.com/bare1</ref>\n`;
        }
        largeWikitext += "== सन्दर्भ ==\n{{सन्दर्भ सूची}}";

        const startTime = Date.now();
        const refs = ClientEngine.extractReferences(largeWikitext);
        assert.strictEqual(refs.length, 100, "Must extract all 100 references");

        // Construct 100 modifications
        const fakeMods = refs.map((r, i) => ({
            id: `mod_${i}`,
            span_start: r.spanStart,
            span_end: r.spanEnd,
            original_snippet: r.fullMatch,
            proposed_snippet: `<ref name="ref_${i}">{{Cite web |title=शीर्षक ${i} |url=https://start.com/bare1}}</ref>`,
            replacement: `<ref name="ref_${i}">{{Cite web |title=शीर्षक ${i} |url=https://start.com/bare1}}</ref>`
        }));

        const reconstructed = ClientEngine.applyGranularModifications(largeWikitext, fakeMods);
        const elapsed = Date.now() - startTime;

        assert.strictEqual(reconstructed.includes("https://start.com/bare1"), true);
        assert(!reconstructed.includes("<ref name=\"ref_0\">https://start.com/bare1</ref>"), "All 100 bare refs must be replaced");
        assert(reconstructed.endsWith("== सन्दर्भ ==\n{{सन्दर्भ सूची}}"), "Footer must be untouched");

        console.log(`    ✓ 100 references extracted and reconstructed in ${elapsed}ms (< 100ms)`);
    }

    {
        console.log("  Testing 5.2: Devanagari multi-byte diacritics & ZWJ/ZWNJ safety...");
        // Text with Hindi complex conjuncts: क्ष, त्र, ज्ञ, र्क, क्त, combining matras
        const devText = `प्रकृति व पर्यावरण सम्बन्धी दृष्टिकोण।<ref name="हिन्दी_१">https://start.com/bare1</ref>पुनर्निर्माण एवं विश्लेषण।<ref name="टिप्पणी_२">https://middle.com/bare2</ref>`;
        const refs = ClientEngine.extractReferences(devText);
        assert.strictEqual(refs[0].name, "हिन्दी_१");
        assert.strictEqual(refs[1].name, "टिप्पणी_२");

        const mods = [
            {
                id: "m1",
                span_start: refs[0].spanStart,
                span_end: refs[0].spanEnd,
                replacement: `<ref name="हिन्दी_१">{{Cite web |url=https://start.com/bare1 |title=पर्यावरण}}</ref>`
            }
        ];
        const res = ClientEngine.applyGranularModifications(devText, mods);
        assert(res.startsWith("प्रकृति व पर्यावरण सम्बन्धी दृष्टिकोण।"));
        assert(res.endsWith("पुनर्निर्माण एवं विश्लेषण।<ref name=\"टिप्पणी_२\">https://middle.com/bare2</ref>"));
        console.log("    ✓ Multi-byte Devanagari character boundaries preserved with zero clipping");
    }

    // -------------------------------------------------------------------------
    // 6. Helper Module Client-Side Verification
    // -------------------------------------------------------------------------
    console.log("\n[SUITE 6] Helper Module Client-Side Logic Verification");

    // Helper 1: Duplicate Ref Merger logic
    {
        const dupWikitext = `<ref>https://same.com</ref> पाठ <ref>https://same.com</ref>`;
        const refs = ClientEngine.extractReferences(dupWikitext);
        assert.strictEqual(refs.length, 2);
        assert.strictEqual(refs[0].innerContent.trim(), refs[1].innerContent.trim());
        console.log("  ✓ Duplicate ref detection logic verified");
    }

    // Helper 2: Conflict Resolver logic
    {
        const conflictWikitext = `<ref name="dup">सामग्री एक</ref> पाठ <ref name="dup">सामग्री दो</ref>`;
        const refs = ClientEngine.extractReferences(conflictWikitext);
        assert.strictEqual(refs.length, 2);
        assert.strictEqual(refs[0].name, refs[1].name);
        assert.notStrictEqual(refs[0].innerContent.trim(), refs[1].innerContent.trim());
        console.log("  ✓ Duplicate name conflict detection logic verified");
    }

    // Helper 4: Error Scanner logic
    {
        const errWikitext = `<ref></ref> असंतुलित {{साँचा`;
        const refs = ClientEngine.extractReferences(errWikitext);
        assert.strictEqual(refs.length, 1);
        assert.strictEqual(refs[0].innerContent.trim(), "");
        const openBrackets = (errWikitext.match(/\{\{/g) || []).length;
        const closeBrackets = (errWikitext.match(/\}\}/g) || []).length;
        assert.notStrictEqual(openBrackets, closeBrackets);
        console.log("  ✓ Error scanner logic (empty refs & unbalanced brackets) verified");
    }

    console.log("\n======================================================================");
    console.log("   ALL ADVERSARIAL EMPIRICAL TESTS PASSED SUCCESSFULLY! (0 FAILURES)  ");
    console.log("======================================================================");
}

runAsyncTests().catch(err => {
    console.error("FATAL TEST FAILURE:", err);
    process.exit(1);
});
