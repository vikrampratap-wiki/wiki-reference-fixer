"""
Milestone 1 Iteration 3 Resilience Tests.
Validates math.isfinite protection for Retry-After NaN/Inf and database connection pool sizing.
"""

import asyncio
import math
import httpx
import pytest
import respx

from app.db.database import get_engine
from app.services.http_client import (
    WikimediaHttpClient,
    WikimediaLagError,
    _extract_maxlag_delay,
    parse_retry_after,
)


def test_parse_retry_after_nan_and_inf_resilience():
    """Verify parse_retry_after returns safe finite delays for NaN, Inf, and malformed inputs."""
    # Test NaN representations
    assert parse_retry_after("NaN") == 1.0
    assert parse_retry_after("nan") == 1.0
    assert parse_retry_after("-nan") == 1.0
    assert parse_retry_after("  NaN  ") == 1.0
    assert math.isfinite(parse_retry_after("NaN"))

    # Test Inf representations
    assert parse_retry_after("Inf") == 1.0
    assert parse_retry_after("-Inf") == 1.0
    assert parse_retry_after("infinity") == 1.0
    assert parse_retry_after("-Infinity") == 1.0
    assert math.isfinite(parse_retry_after("Inf"))

    # Test zero and negative
    assert parse_retry_after("0") == 1.0
    assert parse_retry_after("0.0") == 1.0
    assert parse_retry_after("-5") == 1.0
    assert parse_retry_after("-999.9") == 1.0

    # Test valid floats and ints
    assert parse_retry_after("15") == 15.0
    assert parse_retry_after(" 15.5 ") == 15.5
    assert parse_retry_after("120", max_delay=60.0) == 60.0

    # Test empty or None
    assert parse_retry_after(None) == 1.0
    assert parse_retry_after("") == 1.0
    assert parse_retry_after("   ") == 1.0
    assert parse_retry_after("invalid_header") == 1.0

    # Test custom default_delay
    assert parse_retry_after("NaN", default_delay=5.0) == 5.0
    assert parse_retry_after("Inf", default_delay=10.0) == 10.0

    # Test defense against non-finite default_delay or max_delay
    assert parse_retry_after("NaN", default_delay=float("nan")) == 1.0
    assert parse_retry_after("NaN", default_delay=-5.0) == 1.0
    assert parse_retry_after("NaN", max_delay=float("nan")) == 1.0


def test_extract_maxlag_delay_patterns_and_resilience():
    """Verify _extract_maxlag_delay accurately parses delays and discards NaN/Inf/invalid values."""
    # Standard text formats
    assert _extract_maxlag_delay("Waiting for 10.0.0.1: 5.5 seconds lagged") == 5.5
    assert _extract_maxlag_delay("Host db1055 lagged 12 seconds") == 12.0
    assert _extract_maxlag_delay("Waiting for db: 8 second lagged") == 8.0

    # JSON formatted strings
    json_body = '{"error": {"code": "maxlag", "info": "Waiting for db: 6.5 seconds lagged"}}'
    assert _extract_maxlag_delay(json_body) == 6.5

    # NaN / Inf in body
    nan_body = '{"error": {"code": "maxlag", "info": "Waiting for db: NaN seconds lagged"}}'
    assert _extract_maxlag_delay(nan_body) is None
    assert _extract_maxlag_delay("Waiting for db: NaN seconds lagged") is None
    assert _extract_maxlag_delay("Waiting for db: Inf seconds lagged") is None

    # Zero, negative, empty
    assert _extract_maxlag_delay("Waiting for db: 0 seconds lagged") is None
    assert _extract_maxlag_delay("Waiting for db: -5 seconds lagged") is None
    assert _extract_maxlag_delay("") is None
    assert _extract_maxlag_delay("Server is overloaded") is None


@pytest.mark.asyncio
async def test_http_client_429_retry_after_nan_recovery():
    """Verify client survives Retry-After: NaN on HTTP 429 without crashing."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        with respx.mock(base_url="https://api.wikimedia.org") as mock:
            mock.get("/rate-limit-nan").mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "NaN"}),
                    httpx.Response(200, json={"success": True}),
                ]
            )

            # Fast sleep for unit tests
            orig_sleep = asyncio.sleep

            async def fast_sleep(_):
                await orig_sleep(0.001)

            asyncio.sleep = fast_sleep
            try:
                resp = await client.get("https://api.wikimedia.org/rate-limit-nan", retries=2)
                assert resp.status_code == 200
                assert resp.json() == {"success": True}
            finally:
                asyncio.sleep = orig_sleep
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http_client_maxlag_retry_after_nan_recovery():
    """Verify maxlag response with Retry-After: NaN retries and does NOT return error JSON."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        with respx.mock(base_url="https://api.wikimedia.org") as mock:
            mock.get("/maxlag-nan").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        headers={"Content-Type": "application/json", "Retry-After": "nan"},
                        json={"error": {"code": "maxlag", "info": "Lagged server"}},
                    ),
                    httpx.Response(
                        200,
                        headers={"Content-Type": "application/json"},
                        json={"recovered": True},
                    ),
                ]
            )

            orig_sleep = asyncio.sleep

            async def fast_sleep(_):
                await orig_sleep(0.001)

            asyncio.sleep = fast_sleep
            try:
                resp = await client.get("https://api.wikimedia.org/maxlag-nan", retries=2)
                assert resp.status_code == 200
                data = resp.json()
                assert "error" not in data
                assert data == {"recovered": True}
            finally:
                asyncio.sleep = orig_sleep
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http_client_maxlag_retry_after_nan_exhaustion():
    """Verify persistent maxlag with Retry-After: NaN properly raises WikimediaLagError."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        with respx.mock(base_url="https://api.wikimedia.org") as mock:
            mock.get("/maxlag-exhaust").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "application/json", "Retry-After": "NaN"},
                    json={"error": {"code": "maxlag", "info": "Persistent lag"}},
                )
            )

            orig_sleep = asyncio.sleep

            async def fast_sleep(_):
                await orig_sleep(0.001)

            asyncio.sleep = fast_sleep
            try:
                with pytest.raises(WikimediaLagError) as exc_info:
                    await client.get("https://api.wikimedia.org/maxlag-exhaust", retries=2)
                assert "Persistent lag" in str(exc_info.value)
            finally:
                asyncio.sleep = orig_sleep
    finally:
        await client.close()


def test_database_connection_pool_burst_settings():
    """Verify SQLite async engine is configured with pool_size=20, max_overflow=30, timeout=60.0."""
    engine = get_engine()
    pool = engine.pool
    assert pool.size() == 20
    assert pool._max_overflow == 30
    assert pool._timeout == 60.0
