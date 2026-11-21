"""
Milestone 1 Core Architecture & Scaffolding Test Suite.
Verifies config loading, security primitives, async SQLite WAL persistence,
resilient HTTPX client (maxlag retry & User-Agent), and FastAPI endpoints.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import respx
import httpx
from sqlalchemy import text, select

from app.core.config import Settings, get_settings
from app.core.security import (
    create_session_token,
    decode_session_token,
    generate_csrf_token,
    verify_csrf_token,
    hash_ip_address,
)
from app.db.database import get_db, get_engine
from app.db.models import (
    User,
    RequestQueue,
    AuditLog,
    CacheStore,
    WhitelistBlacklistRule,
    SystemSetting,
)
from app.services.http_client import WikimediaHttpClient, WikimediaLagError


# ============================================================================
# 1. Configuration & Settings Tests
# ============================================================================

def test_settings_superadmin_parsing():
    """Verify superadmin usernames are parsed and normalized correctly."""
    settings = Settings(
        SUPERADMIN_USERNAMES="विक्रम प्रताप, Vikram_Pratap , OtherAdmin",
        PORT=9000,
    )
    assert settings.PORT == 9000
    assert "विक्रम प्रताप" in settings.SUPERADMIN_USERNAMES
    assert "Vikram_Pratap" in settings.SUPERADMIN_USERNAMES
    assert "OtherAdmin" in settings.SUPERADMIN_USERNAMES

    # Test is_superadmin helper with case and space/underscore variations
    assert settings.is_superadmin("विक्रम प्रताप") is True
    assert settings.is_superadmin("Vikram Pratap") is True
    assert settings.is_superadmin("vikram_pratap") is True
    assert settings.is_superadmin("otheradmin") is True
    assert settings.is_superadmin("सामान्य सदस्य") is False
    assert settings.is_superadmin(None) is False


def test_wikimedia_policy_settings():
    """Verify Wikimedia bot/API policy defaults."""
    settings = get_settings()
    assert "WikiRefFixer" in settings.WIKIMEDIA_USER_AGENT
    assert "wiki-reference-fixer" in settings.WIKIMEDIA_USER_AGENT
    assert settings.MAXLAG == 5
    assert settings.MAXLAG_MAX_RETRIES == 3


# ============================================================================
# 2. Security Utilities Tests
# ============================================================================

def test_session_token_lifecycle():
    """Verify creation, decoding, and expiration of signed session tokens."""
    payload = {"username": "विक्रम प्रताप", "user_id": 42, "is_admin": True}
    token = create_session_token(payload)
    assert isinstance(token, str)
    assert len(token) > 20

    # Successful decode
    decoded = decode_session_token(token, max_age=3600)
    assert decoded == payload

    # Tampered token fails
    tampered_token = token[:-4] + "abcd"
    assert decode_session_token(tampered_token) is None

    # Expired token fails (max_age=-1)
    assert decode_session_token(token, max_age=-1) is None


def test_csrf_token_verification():
    """Verify CSRF token generation and constant-time validation."""
    token1 = generate_csrf_token()
    token2 = generate_csrf_token()
    assert token1 != token2
    assert len(token1) >= 32

    assert verify_csrf_token(token1, token1) is True
    assert verify_csrf_token(token1, token2) is False
    assert verify_csrf_token("", token1) is False
    assert verify_csrf_token(token1, "") is False


def test_privacy_preserving_ip_hash():
    """Verify IP addresses are hashed using SHA-256 for Wikimedia privacy."""
    raw_ip = "192.0.2.1"
    hashed = hash_ip_address(raw_ip)
    assert len(hashed) == 64  # SHA-256 hex string
    assert hashed != raw_ip
    # Reproducible for identical IP
    assert hash_ip_address(raw_ip) == hashed
    # None/empty handling
    assert hash_ip_address(None) == "unknown"


# ============================================================================
# 3. Asynchronous SQLite Database & WAL Mode Tests
# ============================================================================

@pytest.mark.asyncio
async def test_sqlite_wal_mode_enabled(test_db):
    """Verify that SQLite connection applies PRAGMA journal_mode=WAL."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode;"))
        mode = result.scalar()
        assert mode.lower() == "wal"

        result = await conn.execute(text("PRAGMA foreign_keys;"))
        fk_status = result.scalar()
        assert fk_status == 1


@pytest.mark.asyncio
async def test_database_crud_operations(test_db):
    """Verify full CRUD functionality on models using async session."""
    session_gen = get_db()
    session = await anext(session_gen)
    try:
        # 1. Create User
        user = User(
            username="विक्रम प्रताप",
            wiki_user_id=101,
            groups=["sysop", "interface-admin"],
            is_admin=True,
        )
        session.add(user)
        await session.flush()
        assert user.id is not None

        # 2. Create RequestQueue item linked to User
        req = RequestQueue(
            user_id=user.id,
            page_title="भारत का इतिहास",
            status="pending",
            total_refs=15,
            fixed_refs=5,
            options={"duplicate_merger": True, "dead_links": True},
        )
        session.add(req)

        # 3. Create AuditLog
        audit = AuditLog(
            user_id=user.id,
            username="विक्रम प्रताप",
            ip_hash=hash_ip_address("127.0.0.1"),
            action="PAGE_SCAN",
            page_title="भारत का इतिहास",
            details={"found_bare_urls": 4},
        )
        session.add(audit)

        # 4. Create CacheStore entry
        cache = CacheStore(
            key="hash_test_12345",
            category="citoid",
            identifier="https://example.com/news",
            data={"title": "परीक्षण समाचार", "author": "विक्रम"},
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(cache)

        # 5. Create Rule
        rule = WhitelistBlacklistRule(
            rule_type="blacklist",
            scope="domain",
            pattern="unreliable-blog.example.com",
            reason="WP:RSP blacklisted blog",
            added_by="विक्रम प्रताप",
        )
        session.add(rule)

        # 6. Create SystemSetting
        setting = SystemSetting(
            key="kill_switch",
            value={"enabled": False, "reason": "Normal operation"},
            updated_by="विक्रम प्रताप",
        )
        session.add(setting)

        await session.commit()

        # Query back and verify relations
        result = await session.execute(
            select(User).where(User.username == "विक्रम प्रताप")
        )
        fetched_user = result.scalar_one()
        assert fetched_user.is_admin is True
        assert len(fetched_user.requests) == 1
        assert fetched_user.requests[0].page_title == "भारत का इतिहास"

        # Verify CacheStore query
        result = await session.execute(
            select(CacheStore).where(CacheStore.key == "hash_test_12345")
        )
        fetched_cache = result.scalar_one()
        assert fetched_cache.data["title"] == "परीक्षण समाचार"

    finally:
        try:
            await session_gen.aclose()
        except Exception:
            pass


# ============================================================================
# 4. Resilient HTTPX Client (Wikimedia Policy & Maxlag) Tests
# ============================================================================

@pytest.mark.asyncio
@respx.mock
async def test_http_client_user_agent_and_maxlag():
    """Verify HTTP client automatically sets User-Agent and maxlag param."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(200, json={"batchcomplete": ""})
        )

        response = await client.get(
            "https://hi.wikipedia.org/w/api.php",
            params={"action": "query", "meta": "siteinfo"},
        )
        assert response.status_code == 200
        assert route.called
        last_request = route.calls.last.request

        # Verify User-Agent header conforms to Wikimedia standard
        assert "WikiRefFixer/1.0" in last_request.headers["User-Agent"]
        # Verify maxlag query parameter was automatically injected
        assert "maxlag=5" in str(last_request.url)
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_maxlag_retry_success():
    """Verify HTTP client retries when MediaWiki returns maxlag error."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            side_effect=[
                # Attempt 1: Server lagged
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json", "Retry-After": "1"},
                    json={
                        "error": {
                            "code": "maxlag",
                            "info": "Waiting for 10.0.0.1: 5.5 seconds lagged",
                        }
                    },
                ),
                # Attempt 2: Recovered
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={"query": {"pages": {}}},
                ),
            ]
        )

        # Patch sleep to not wait real seconds during unit tests
        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0.01)

        asyncio.sleep = fast_sleep
        try:
            response = await client.get(
                "https://hi.wikipedia.org/w/api.php",
                params={"action": "query"},
                retries=2,
            )
            assert response.status_code == 200
            data = response.json()
            assert "query" in data
            assert route.call_count == 2
        finally:
            asyncio.sleep = original_sleep
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_rate_limit_429_retry():
    """Verify HTTP client respects HTTP 429 Retry-After and retries."""
    client = WikimediaHttpClient()
    await client.init()

    citoid_test_url = (
        "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki/"
        "https%3A%2F%2Fexample.com"
    )

    try:
        route = respx.get(citoid_test_url).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json=[{"title": "Example News"}]),
            ]
        )

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0.01)

        asyncio.sleep = fast_sleep
        try:
            response = await client.get(citoid_test_url, retries=2)
            assert response.status_code == 200
            assert route.call_count == 2
            assert response.json()[0]["title"] == "Example News"
        finally:
            asyncio.sleep = original_sleep
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_maxlag_exhausted_raises_error():
    """Verify HTTP client raises WikimediaLagError when maxlag retries are exhausted."""
    client = WikimediaHttpClient()
    await client.init()

    try:
        respx.get("https://hi.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Retry-After": "1"},
                json={
                    "error": {
                        "code": "maxlag",
                        "info": "Waiting for 10.0.0.1: 15.0 seconds lagged",
                    }
                },
            )
        )

        original_sleep = asyncio.sleep

        async def fast_sleep(_):
            await original_sleep(0.01)

        asyncio.sleep = fast_sleep
        try:
            with pytest.raises(WikimediaLagError):
                await client.get(
                    "https://hi.wikipedia.org/w/api.php",
                    params={"action": "query"},
                    retries=1,
                )
        finally:
            asyncio.sleep = original_sleep
    finally:
        await client.close()


# ============================================================================
# 5. FastAPI Endpoints & Lifespan Health Check Tests
# ============================================================================

@pytest.mark.asyncio
async def test_health_check_endpoints(async_client):
    """Verify /health and /api/health return healthy status with Wikimedia policy details."""
    # Test /health
    resp1 = await async_client.get("/health")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "healthy"
    assert data1["database"] == "connected"
    assert data1["version"] == "1.0.0"
    assert data1["wikimedia_policy"]["maxlag"] == 5

    # Test /api/health
    resp2 = await async_client.get("/api/health")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "healthy"
    assert data2["database"] == "connected"


@pytest.mark.asyncio
async def test_404_error_handler(async_client):
    """Verify unmapped routes return structured JSON error message."""
    resp = await async_client.get("/nonexistent-endpoint-test-route")
    assert resp.status_code == 404
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == 404
