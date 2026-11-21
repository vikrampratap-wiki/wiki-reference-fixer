"""
Milestone 1 Iteration 2 Security Challenge Test Suite.

Adversarial testing and empirical verification of:
1. Fuzzing decode_session_token (None, empty, non-UTF8, zlib, truncated, corrupted signatures).
2. /health & /api/health transition between 200 and 503 during DB outages/recoveries.
3. Connection pool leak verification during outage/recovery flapping.
"""

import asyncio
import os
import random
import string
import zlib

import httpx
from httpx import ASGITransport
import pytest

from app.core.config import get_settings
from app.core.security import (
    create_session_token,
    decode_session_token,
    get_serializer,
)
from app.db.database import close_db, get_engine, init_db
from app.main import app


# ============================================================================
# PART 1: Fuzzing decode_session_token
# ============================================================================

class TestDecodeSessionTokenFuzzing:
    """Rigorous empirical fuzzing of decode_session_token."""

    def test_fuzz_none_and_empty_inputs(self):
        """Verify None, empty strings, and whitespace return None without exception."""
        assert decode_session_token(None) is None
        assert decode_session_token("") is None
        assert decode_session_token(" ") is None
        assert decode_session_token("   \t  \n  \r\n  ") is None
        assert decode_session_token("\x00") is None
        assert decode_session_token(" \x00 ") is None

    def test_fuzz_non_string_types(self):
        """Verify non-string types return None without exception."""
        invalid_types = [
            123,
            0,
            -1,
            3.14159,
            True,
            False,
            [],
            [1, 2, 3],
            {},
            {"session": "xyz"},
            object(),
            b"bytes_token",
            b"",
            b"\x00\x01\x02",
        ]
        for val in invalid_types:
            assert decode_session_token(val) is None  # type: ignore[arg-type]

    def test_fuzz_non_utf8_and_binary_bytes(self):
        """Verify non-UTF8 byte strings and invalid byte sequences return None."""
        raw_bytes_cases = [
            b"\xff\xfe\xfd",
            b"\x80\x81\x82\x83",
            b"\xc0\xaf",  # Overlong UTF-8
            b"\xed\xa0\x80",  # UTF-16 surrogate in UTF-8
            b"\xf4\x90\x80\x80",  # Out of Unicode range
            os.urandom(64),
            os.urandom(256),
        ]
        for b_val in raw_bytes_cases:
            # Passed directly as bytes
            assert decode_session_token(b_val) is None  # type: ignore[arg-type]
            # Decoded with latin1/surrogateescape to create strange strings
            str_val = b_val.decode("latin1")
            assert decode_session_token(str_val) is None

    def test_fuzz_zlib_compressed_tokens(self):
        """
        Fuzz with zlib compressed payloads.
        In itsdangerous, payloads starting with '.' signify zlib compression.
        Test malformed zlib payloads, raw zlib, and decompression attacks.
        """
        # 1. Raw zlib data passed as string
        raw_zlib = zlib.compress(b'{"username": "vikram", "is_admin": true}')
        assert decode_session_token(raw_zlib.decode("latin1")) is None

        # 2. Simulated itsdangerous zlib token with corrupted zlib stream
        corrupt_zlib_header = ".789c_corrupt_zlib_stream_garbage.timestamp.signature"
        assert decode_session_token(corrupt_zlib_header) is None

        # 3. Truncated zlib stream
        truncated_zlib = raw_zlib[:len(raw_zlib) // 2].decode("latin1")
        assert decode_session_token(f".{truncated_zlib}.12345.signature") is None

        # 4. Validly signed token containing zlib compressed JSON
        serializer = get_serializer()
        valid_payload = {"username": "परीक्षक", "roles": ["admin"]}
        normal_token = serializer.dumps(valid_payload)
        assert decode_session_token(normal_token) == valid_payload

    def test_fuzz_truncated_tokens(self):
        """Verify truncating a valid token at every character boundary returns None."""
        valid_payload = {
            "user_id": 1001,
            "username": "विक्रम प्रताप",
            "is_admin": True,
            "login_time": "2026-11-21T09:00:00Z",
        }
        token = create_session_token(valid_payload)
        assert decode_session_token(token) == valid_payload

        # Truncate at every length from 0 to len-1
        for i in range(len(token)):
            truncated = token[:i]
            assert decode_session_token(truncated) is None, f"Failed at length {i}"

        # Truncate from the left (strip characters from the beginning)
        for i in range(1, len(token)):
            truncated_left = token[i:]
            assert decode_session_token(truncated_left) is None, f"Failed left truncation at {i}"

    def test_fuzz_corrupted_signatures(self):
        """Verify corrupted, forged, tampered signatures are rejected."""
        valid_payload = {"username": "admin", "is_admin": True}
        token = create_session_token(valid_payload)
        parts = token.split(".")
        assert len(parts) >= 3, "Expected timed serializer format: payload.timestamp.signature"

        payload_part, timestamp_part, sig_part = parts[0], parts[1], parts[2]

        # 1. Flip single bits in signature
        for idx in range(len(sig_part)):
            char = sig_part[idx]
            replacement = "A" if char != "A" else "B"
            corrupt_sig = sig_part[:idx] + replacement + sig_part[idx + 1:]
            bad_token = f"{payload_part}.{timestamp_part}.{corrupt_sig}"
            assert decode_session_token(bad_token) is None

        # 2. Inverted signature
        assert decode_session_token(f"{payload_part}.{timestamp_part}.{sig_part[::-1]}") is None

        # 3. Truncated signature
        assert decode_session_token(f"{payload_part}.{timestamp_part}.{sig_part[:5]}") is None

        # 4. Injection strings as signatures
        injections = [
            "' OR '1'='1",
            "'; DROP TABLE users;--",
            "<script>alert(1)</script>",
            "../../../../etc/passwd",
            "\x00\x00\x00",
            "${jndi:ldap://evil.com/x}",
            "{{7*7}}",
            "A" * 10000,
        ]
        for inj in injections:
            bad_token = f"{payload_part}.{timestamp_part}.{inj}"
            assert decode_session_token(bad_token) is None

        # 5. Corrupted timestamp part
        assert decode_session_token(f"{payload_part}.invalid_timestamp.{sig_part}") is None
        assert decode_session_token(f"{payload_part}..{sig_part}") is None

    def test_fuzz_random_mutations(self):
        """Stress-test with 500 pseudo-random mutated tokens."""
        rng = random.Random(42)
        valid_token = create_session_token({"user": "stress"})

        for _ in range(500):
            mutation_type = rng.choice(["replace", "insert", "delete", "scramble", "random_str"])
            if mutation_type == "replace":
                pos = rng.randint(0, len(valid_token) - 1)
                char = rng.choice(string.printable)
                mutated = valid_token[:pos] + char + valid_token[pos + 1:]
            elif mutation_type == "insert":
                pos = rng.randint(0, len(valid_token))
                char = rng.choice(string.printable)
                mutated = valid_token[:pos] + char + valid_token[pos:]
            elif mutation_type == "delete":
                pos = rng.randint(0, len(valid_token) - 1)
                mutated = valid_token[:pos] + valid_token[pos + 1:]
            elif mutation_type == "scramble":
                token_chars = list(valid_token)
                rng.shuffle(token_chars)
                mutated = "".join(token_chars)
            else:
                length = rng.randint(1, 200)
                mutated = "".join(rng.choice(string.printable) for _ in range(length))

            # Must return None safely, NEVER raise an exception
            res = decode_session_token(mutated)
            assert res is None or isinstance(res, dict)

    def test_signed_non_dictionary_payloads(self):
        """Verify that validly signed non-dict payloads return None."""
        serializer = get_serializer()
        non_dict_payloads = [
            "a string payload",
            12345,
            3.14,
            True,
            False,
            None,
            ["list", "of", "items"],
            [{"nested": "dict"}],
        ]
        for p in non_dict_payloads:
            token = serializer.dumps(p)
            assert decode_session_token(token) is None


# ============================================================================
# PART 2: /health 200 <-> 503 Outage & Recovery Transitions
# ============================================================================

@pytest.mark.asyncio
class TestHealthCheckOutageRecoveryTransitions:
    """Verify state transitions between HTTP 200 and HTTP 503 during DB outages."""

    async def test_full_outage_and_recovery_cycle(self, tmp_path):
        """
        Verify transition lifecycle:
        1. Initial state -> 200 OK
        2. Outage (inaccessible DB path) -> 503 Service Unavailable
        3. Recovery (valid DB path restored) -> 200 OK
        4. Outage 2 (corrupted DB file) -> 503 Service Unavailable
        5. Recovery 2 (reinitialized clean DB) -> 200 OK
        """
        test_db = str(tmp_path / "lifecycle.db")
        settings = get_settings()
        orig_db = settings.DB_PATH
        settings.DB_PATH = test_db

        await close_db()
        await init_db()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Initial State: Healthy
            resp1 = await client.get("/health")
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1["status"] == "healthy"
            assert data1["database"] == "connected"

            resp1_api = await client.get("/api/health")
            assert resp1_api.status_code == 200
            assert resp1_api.json()["status"] == "healthy"

            # 2. Outage 1: Inaccessible / Non-existent directory path
            settings.DB_PATH = "/dev/null/impossible_dir/non_existent.db"
            await close_db()

            resp2 = await client.get("/health")
            assert resp2.status_code == 503
            data2 = resp2.json()
            assert data2["status"] == "degraded"
            assert "unhealthy" in data2["database"]

            resp2_api = await client.get("/api/health")
            assert resp2_api.status_code == 503
            assert resp2_api.json()["status"] == "degraded"

            # 3. Recovery 1: Restore valid DB path
            settings.DB_PATH = test_db
            await close_db()
            await init_db()

            resp3 = await client.get("/health")
            assert resp3.status_code == 200
            data3 = resp3.json()
            assert data3["status"] == "healthy"
            assert data3["database"] == "connected"

            resp3_api = await client.get("/api/health")
            assert resp3_api.status_code == 200
            assert resp3_api.json()["status"] == "healthy"

            # 4. Outage 2: Corrupted DB file
            corrupt_db = str(tmp_path / "corrupt_lifecycle.db")
            with open(corrupt_db, "wb") as f:
                f.write(b"CORRUPTED_SQLITE_HEADER\x00\xff" * 50)
            settings.DB_PATH = corrupt_db
            await close_db()

            resp4 = await client.get("/health")
            assert resp4.status_code == 503
            data4 = resp4.json()
            assert data4["status"] == "degraded"
            assert "unhealthy" in data4["database"]

            # 5. Recovery 2: Restore and re-initialize
            settings.DB_PATH = test_db
            await close_db()
            await init_db()

            resp5 = await client.get("/health")
            assert resp5.status_code == 200
            data5 = resp5.json()
            assert data5["status"] == "healthy"
            assert data5["database"] == "connected"

        # Final cleanup
        settings.DB_PATH = orig_db
        await close_db()
        await init_db()

    async def test_flapping_outage_recovery_under_concurrency(self, tmp_path):
        """
        Stress test rapid flapping between healthy and outage states:
        Verify that alternating cycles cleanly transition 200 -> 503 -> 200
        without connection pool leaks or stuck states.
        """
        valid_db = str(tmp_path / "flapping_valid.db")
        settings = get_settings()
        orig_db = settings.DB_PATH
        settings.DB_PATH = valid_db

        await close_db()
        await init_db()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for cycle in range(5):
                # Healthy Phase
                settings.DB_PATH = valid_db
                await close_db()
                await init_db()

                # Concurrent probes during healthy phase
                tasks_healthy = [client.get("/health") for _ in range(10)]
                results_healthy = await asyncio.gather(*tasks_healthy)
                for r in results_healthy:
                    assert r.status_code == 200
                    assert r.json()["status"] == "healthy"

                # Outage Phase
                settings.DB_PATH = "/dev/null/forbidden_flapping.db"
                await close_db()

                # Concurrent probes during outage phase
                tasks_outage = [client.get("/health") for _ in range(10)]
                results_outage = await asyncio.gather(*tasks_outage)
                for r in results_outage:
                    assert r.status_code == 503
                    assert r.json()["status"] == "degraded"

            # Final recovery
            settings.DB_PATH = valid_db
            await close_db()
            await init_db()

            resp_final = await client.get("/health")
            assert resp_final.status_code == 200
            assert resp_final.json()["status"] == "healthy"

            # Verify connection pool has zero checked out connections
            engine = get_engine()
            assert engine.pool.checkedout() == 0, f"Leaked {engine.pool.checkedout()} connections!"

        settings.DB_PATH = orig_db
        await close_db()
        await init_db()
