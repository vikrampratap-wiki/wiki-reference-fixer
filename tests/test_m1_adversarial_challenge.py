"""
Adversarial Challenge Test Suite for Milestone 1.
Empirically challenges:
1. Session Security & Cookie Handling (tampering, signatures, expiry, type handling)
2. Health Check Probe Resiliency (disconnection, permission denial, corrupt SQLite)
3. WikimediaHttpClient Network Resilience (resets, timeouts, HTTP 429 Retry-After)
"""

import asyncio
import os
import tempfile
import time
import pytest
import httpx
import respx
from itsdangerous import URLSafeTimedSerializer

from app.core.config import get_settings
from app.core.security import (
    create_session_token,
    decode_session_token,
    get_serializer,
)
from app.db.database import close_db, init_db
from app.main import create_app
from app.services.http_client import WikimediaHttpClient


# ============================================================================
# PART 1: Session Serialization & Security Challenges
# ============================================================================

def test_session_token_tampering_payload():
    """Adversarial test: Modify payload data to forge admin privileges."""
    # Test with standard ASCII payload
    ascii_token = create_session_token({"username": "user1", "is_admin": False})
    # Tamper payload
    tampered_ascii = "eyJuYW1lIjoiYWRtaW4ifQ." + ".".join(ascii_token.split(".")[1:])
    assert decode_session_token(tampered_ascii) is None

    # Test with Devanagari payload (which triggers itsdangerous zlib compression prefix)
    devanagari_token = create_session_token({"username": "सामान्य_सदस्य", "is_admin": False})
    # Tampering any segment of the token should fail verification
    tampered_dev = devanagari_token[:-5] + "XXXXX"
    assert decode_session_token(tampered_dev) is None


def test_session_token_tampering_signature():
    """Adversarial test: Bit-flip and corruption of signature."""
    token = create_session_token({"username": "विक्रम प्रताप", "is_admin": True})
    parts = token.split(".")

    # 1. Flip signature character (flip first char of signature to avoid base64 padding collision)
    flipped_char = "X" if parts[-1][0] != "X" else "Y"
    bad_sig_token = f"{parts[0]}.{parts[1]}.{flipped_char}{parts[-1][1:]}"
    assert decode_session_token(bad_sig_token) is None

    # 2. Empty signature
    assert decode_session_token(f"{parts[0]}.{parts[1]}.") is None

    # 3. Stripped signature
    assert decode_session_token(".".join(parts[:-1])) is None

    # 4. Truncated token
    assert decode_session_token(token[:10]) is None


def test_session_token_timestamp_tampering_and_expiration():
    """Adversarial test: Timestamp manipulation, replay attacks, and expiration."""
    payload = {"username": "विक्रम प्रताप", "user_id": 1}
    token = create_session_token(payload)

    # 1. Standard valid decode
    assert decode_session_token(token, max_age=3600) == payload

    # 2. Expired token via negative max_age
    assert decode_session_token(token, max_age=-1) is None

    # 3. itsdangerous timestamp resolution edge case:
    # Within the same clock second (age == 0), max_age=0 does not expire (0 > 0 is False).
    # But after sleeping 1.1s (age >= 1), max_age=0 expires!
    time.sleep(1.1)
    assert decode_session_token(token, max_age=0) is None

    # 4. Manipulate timestamp part in token
    parts = token.split(".")
    # Replace timestamp segment with arbitrary forged string
    parts[-2] = "AAAAAA"
    forged_token = ".".join(parts)
    assert decode_session_token(forged_token) is None


def test_session_token_secret_key_isolation():
    """Adversarial test: Tokens signed with different secret or salt must be rejected."""
    payload = {"username": "विक्रम प्रताप", "is_admin": True}

    # Serializer with rogue secret key
    rogue_serializer = URLSafeTimedSerializer(
        secret_key="attacker-controlled-secret-key-that-is-long-enough",
        salt="wiki-ref-fixer-session",
    )
    forged_token = rogue_serializer.dumps(payload)
    assert decode_session_token(forged_token) is None

    # Serializer with rogue salt
    settings = get_settings()
    rogue_salt_serializer = URLSafeTimedSerializer(
        secret_key=settings.SECRET_KEY,
        salt="rogue-salt-session",
    )
    forged_salt_token = rogue_salt_serializer.dumps(payload)
    assert decode_session_token(forged_salt_token) is None


def test_session_token_edge_inputs_and_type_vulnerability():
    """
    Adversarial test: Non-string, empty, null bytes, huge payloads.
    Vulnerability finding: decode_session_token(None) raises uncaught TypeError
    instead of gracefully returning None.
    """
    # Empty string
    assert decode_session_token("") is None

    # Whitespace only
    assert decode_session_token("   ") is None
    assert decode_session_token("\t\n") is None

    # Null bytes
    assert decode_session_token("foo\x00bar.baz.qux") is None

    # Garbage base64
    assert decode_session_token("!!!.@@@.###") is None

    # Non-dictionary payload serialization rejection
    serializer = get_serializer()
    assert decode_session_token(serializer.dumps(["not", "a", "dict"])) is None
    assert decode_session_token(serializer.dumps(12345)) is None
    assert decode_session_token(serializer.dumps("just a string")) is None

    # REMEDIATED: None and non-string inputs safely return None without throwing TypeError
    assert decode_session_token(None) is None
    assert decode_session_token(12345) is None  # type: ignore[arg-type]
    assert decode_session_token(["token"]) is None  # type: ignore[arg-type]
    assert decode_session_token({"token": "val"}) is None  # type: ignore[arg-type]


def test_session_token_unicode_devanagari_fidelity():
    """Stress test: Complex Unicode and Devanagari preserving integrity."""
    complex_payload = {
        "username": "विक्रम प्रताप (प्रशासक)",
        "roles": ["सम्पादक", "प्रबंधक", "परीक्षक"],
        "symbols": "हिन्दी विकिपीडिया! 🚀 100% #&@ {test}",
        "nested": {"key": "मानक सन्दर्भ सुधार"},
    }
    token = create_session_token(complex_payload)
    decoded = decode_session_token(token)
    assert decoded == complex_payload


# ============================================================================
# PART 2: Health Check Probes Under Failure & Corruption
# ============================================================================

@pytest.mark.asyncio
async def test_health_check_when_database_is_disconnected():
    """
    Adversarial test: Health check probe under filesystem permission denial.
    Verifies that /health and /api/health report degraded status without unhandled 500 crash.
    Also documents that HTTP status code remains 200 OK despite degraded status.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        forbidden_dir = os.path.join(tmpdir, "inaccessible")
        os.mkdir(forbidden_dir, 0o000)

        settings = get_settings()
        original_db = settings.DB_PATH
        try:
            settings.DB_PATH = os.path.join(forbidden_dir, "test.db")
            await close_db()

            app = create_app()
            from httpx import ASGITransport
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                resp = await client.get("/health")
                # Health probe returns HTTP 503 Service Unavailable when DB is degraded
                assert resp.status_code == 503
                data = resp.json()
                assert data["status"] == "degraded"
                assert "unhealthy" in data["database"]

                resp_api = await client.get("/api/health")
                assert resp_api.status_code == 503
                data_api = resp_api.json()
                assert data_api["status"] == "degraded"
                assert "unhealthy" in data_api["database"]
        finally:
            os.chmod(forbidden_dir, 0o755)
            settings.DB_PATH = original_db
            await close_db()
            await init_db()


@pytest.mark.asyncio
async def test_health_check_when_database_file_is_corrupt(tmp_path):
    """
    Adversarial test: SQLite file filled with corrupted binary garbage.
    Verifies that corrupted SQLite header doesn't crash the server.
    """
    corrupt_db_file = str(tmp_path / "corrupt.db")
    with open(corrupt_db_file, "wb") as f:
        f.write(b"CORRUPTED_NON_SQLITE_BINARY_DATA\x00\xff\xfe" * 50)

    settings = get_settings()
    original_db = settings.DB_PATH
    try:
        settings.DB_PATH = corrupt_db_file
        await close_db()

        app = create_app()
        from httpx import ASGITransport
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/health")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert "unhealthy" in data["database"]

            resp_api = await client.get("/api/health")
            assert resp_api.status_code == 503
            data_api = resp_api.json()
            assert data_api["status"] == "degraded"
            assert "unhealthy" in data_api["database"]
    finally:
        settings.DB_PATH = original_db
        await close_db()
        await init_db()


# ============================================================================
# PART 3: WikimediaHttpClient Resilience (Resets, Timeouts, 429)
# ============================================================================

@pytest.mark.asyncio
@respx.mock
async def test_http_client_connection_reset_vulnerability():
    """
    Adversarial test: Server resets connection (httpx.RemoteProtocolError).
    BUG DISCOVERY: WikimediaHttpClient does NOT catch network/transport exceptions
    in its retry loop. A connection reset immediately aborts on attempt 0 (call_count 1)
    without retrying, ignoring the retries=2 parameter.
    """
    client = WikimediaHttpClient()
    await client.init()

    test_url = "https://hi.wikipedia.org/w/api.php"

    # Transient reset on attempt 1, recovery on attempt 2
    route = respx.get(test_url).mock(
        side_effect=[
            httpx.RemoteProtocolError("Connection reset by peer"),
            httpx.Response(200, json={"batchcomplete": ""}),
        ]
    )

    original_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await original_sleep(0.001)

    asyncio.sleep = fast_sleep

    try:
        resp = await client.get(test_url, retries=2)
        assert resp.status_code == 200
        assert resp.json() == {"batchcomplete": ""}
        assert route.call_count == 2
    finally:
        asyncio.sleep = original_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_connect_timeout_vulnerability():
    """
    Adversarial test: ConnectTimeout behavior.
    Verifies that ConnectTimeout is caught and retried up to max_retries.
    """
    client = WikimediaHttpClient()
    await client.init()

    test_url = "https://hi.wikipedia.org/w/api.php"

    route = respx.get(test_url).mock(
        side_effect=[
            httpx.ConnectTimeout("Connection timed out"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    original_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await original_sleep(0.001)

    asyncio.sleep = fast_sleep

    try:
        resp = await client.get(test_url, retries=2)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert route.call_count == 2
    finally:
        asyncio.sleep = original_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_read_timeout_vulnerability():
    """
    Adversarial test: ReadTimeout behavior.
    Verifies that ReadTimeout is caught and retried up to max_retries.
    """
    client = WikimediaHttpClient()
    await client.init()

    test_url = "https://hi.wikipedia.org/w/api.php"

    route = respx.get(test_url).mock(
        side_effect=[
            httpx.ReadTimeout("Read timed out"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    original_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await original_sleep(0.001)

    asyncio.sleep = fast_sleep

    try:
        resp = await client.get(test_url, retries=2)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert route.call_count == 2
    finally:
        asyncio.sleep = original_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_rate_limit_429_edge_cases():
    """Adversarial test: HTTP 429 with malformed, non-numeric, or missing Retry-After."""
    client = WikimediaHttpClient()
    await client.init()

    citoid_url = (
        "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki/https%3A%2F%2Ftest.org"
    )

    # Patch sleep to record sleep durations without waiting
    sleep_calls = []
    original_sleep = asyncio.sleep

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    asyncio.sleep = mock_sleep

    try:
        # Case A: Retry-After is HTTP date (RFC 7231 non-digit format)
        respx.get(citoid_url).mock(
            side_effect=[
                httpx.Response(
                    429,
                    headers={"Retry-After": "Wed, 21 Nov 2026 09:15:00 GMT"},
                ),
                httpx.Response(200, json=[{"title": "Recovered"}]),
            ]
        )

        resp = await client.get(citoid_url, retries=2)
        assert resp.status_code == 200
        # In implementation: if not isdigit(), falls back to 2 ** attempt = 2 ** 0 = 1.0s
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 1.0

        # Case B: Retry-After missing completely
        sleep_calls.clear()
        respx.get(citoid_url).mock(
            side_effect=[
                httpx.Response(429),  # No Retry-After header
                httpx.Response(200, json=[{"title": "Recovered"}]),
            ]
        )

        resp = await client.get(citoid_url, retries=2)
        assert resp.status_code == 200
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 1.0  # 2 ** 0

        # Case C: 429 with max retries exceeded
        sleep_calls.clear()
        respx.get(citoid_url).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get(citoid_url, retries=2)
        assert exc_info.value.response.status_code == 429
        # Retried 2 times (sleeping twice)
        assert len(sleep_calls) == 2

        # Case D: Uncapped Retry-After (e.g. 86400 seconds)
        sleep_calls.clear()
        respx.get(citoid_url).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "86400"}),
                httpx.Response(200, json=[{"title": "Recovered"}]),
            ]
        )
        await client.get(citoid_url, retries=1)
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 60.0  # Verifies backoff ceiling is strictly enforced

    finally:
        asyncio.sleep = original_sleep
        await client.close()
