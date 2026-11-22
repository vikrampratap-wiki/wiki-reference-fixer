"""
Pytest wrapper for empirical verification of client-side JavaScript helpers in index.html.
Invokes tests/test_client_helpers_harness.js and asserts execution and findings.
"""

import subprocess
from pathlib import Path


def test_client_side_helpers_empirical_harness():
    project_root = Path(__file__).resolve().parent.parent
    script_path = project_root / "tests" / "test_client_helpers_harness.js"

    assert script_path.exists(), f"Harness script missing at {script_path}"

    result = subprocess.run(
        ["node", str(script_path)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )

    # The test harness executes all 8 helpers and prints structured PASS/FAIL
    stdout = result.stdout
    assert "RUNNING EMPIRICAL & ADVERSARIAL TEST HARNESS ON CLIENT JAVASCRIPT" in stdout
    assert "TEST SUITE COMPLETED:" in stdout

    # Verify that all 8 helper tests pass 100% with 0 failures
    assert "[FAIL]" not in stdout
    assert "0 FAILED/VULNERABILITIES FOUND" in stdout
    assert "[PASS] Helper 1 - Identical bare URLs detection" in stdout
    assert "[PASS] Helper 1 - Identical URL with DIFFERENT titles should NOT merge" in stdout
    assert "[PASS] Helper 1 - Preserves existing ref name for replacement" in stdout
    assert "[PASS] Helper 1 - First occurrence definition upgrade check" in stdout
    assert "[PASS] Helper 2 - Same name with distinct content triggers warning" in stdout
    assert "[PASS] Helper 2 - Same name with identical content does NOT trigger conflict" in stdout
    assert "[PASS] Helper 3 - Valid URL with Wayback snapshot found" in stdout
    assert "[PASS] Helper 4 - Empty <ref></ref> and whitespace ref detected" in stdout
    assert "[PASS] Helper 4 - Missing {{सन्दर्भ सूची}} detected when refs exist" in stdout
    assert "[PASS] Helper 4 - Unclosed template brackets {{ detected" in stdout
    assert "[PASS] Helper 4 - Unclosed <ref> tag detection" in stdout
    assert "[PASS] Helper 5 - Perfect 100 score gives Grade A" in stdout
    assert "[PASS] Helper 5 - ClientEngine.analyzeWikitext score calculation matches specification" in stdout
    assert "[PASS] Helper 6 - Valid ISBN-10 (numeric & check digit X)" in stdout
    assert "[PASS] Helper 6 - Invalid ISBN-10 checksum fails validation" in stdout
    assert "[PASS] Helper 6 - Valid ISBN-13 (mod 10 checksum)" in stdout
    assert "[PASS] Helper 6 - Invalid ISBN-13 checksum fails validation" in stdout
    assert "[PASS] Helper 6 - Devanagari numerals ०-९ conversion to ASCII 0-9" in stdout
    assert "[PASS] Helper 7 - Matched ref group and container is consistent" in stdout
    assert "[PASS] Helper 7 - Unmatched group detection" in stdout
    assert "[PASS] Helper 8 - Flags unreliable domains (Twitter, Daily Mail, Blogspot)" in stdout
    assert "[PASS] Helper 8 - Reliable domains are not flagged" in stdout
    assert "[PASS] Helper 8 - Domain without trailing slash before query string" in stdout
