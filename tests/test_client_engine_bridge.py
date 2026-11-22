"""Bridge test to execute the Node.js adversarial client engine suite as part of pytest."""
import os
import subprocess


def test_client_engine_adversarial_suite():
    """Runs tests/test_client_engine_adversarial.js via Node.js."""
    js_test_path = os.path.join(os.path.dirname(__file__), "test_client_engine_adversarial.js")
    assert os.path.exists(js_test_path), f"JS test file missing: {js_test_path}"

    result = subprocess.run(
        ["node", js_test_path],
        capture_output=True,
        text=True
    )
    print("\n--- Node.js Test Output ---")
    print(result.stdout)
    if result.stderr:
        print("--- Node.js Test Stderr ---")
        print(result.stderr)

    assert result.returncode == 0, f"Node.js adversarial suite failed:\n{result.stderr}\n{result.stdout}"
