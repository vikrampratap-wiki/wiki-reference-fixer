"""
Comprehensive Test Suite for Milestone 2:
Website Admin RBAC, Authentication, Strict 404 Security, and Audit Logging.
Validates:
1. Owner authentication (विक्रम प्रताप / Vikram Pratap).
2. Dynamic on-wiki co-admin subpage fetch with MediaWiki Action API mock and 10-minute caching.
3. Strict 404 Not Found cloaking on /admin and /api/admin/* for non-admins (NEVER 401/403).
4. Separation of Wikipedia sysops/bureaucrats from website administrators.
5. Co-admin access grant from on-wiki subpage.
6. Emergency superadmin fallback from .env (SUPERADMIN_USERNAMES).
7. System audit logging with privacy IP hashing and administrative log queries.
8. Emergency kill-switch toggle, SQLite persistence, and 503 halt / 200 restoration on /api/scan.
9. Rate limiter controls and whitelist/blacklist rules CRUD.
10. Authentication endpoints: /login, /callback, /me, /logout, and /dev-login.
"""

from datetime import datetime, timedelta, timezone
import json
from typing import AsyncGenerator
import urllib.parse
import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import close_db, get_session_maker, init_db
from app.db.models import CacheStore
from app.services.audit import log_audit_event
from app.services.auth import (
    CACHE_KEY_ONWIKI_SUBPAGE,
    verify_website_admin,
)

ADMIN_ONWIKI_URL = "https://hi.wikipedia.org/w/api.php"


def make_wiki_user_header(username: str) -> dict[str, str]:
    """Encode header value safely to avoid non-ASCII header issues."""
    return {"X-Wiki-User": urllib.parse.quote(username)}


@pytest_asyncio.fixture
async def isolated_db(tmp_path) -> AsyncGenerator[None, None]:
    """Provide an isolated temporary SQLite database for M2 tests."""
    temp_db_file = str(tmp_path / "test_m2_rbac.db")
    settings = get_settings()
    original_db = settings.DB_PATH
    settings.DB_PATH = temp_db_file

    await close_db()
    await init_db()

    yield

    await close_db()
    settings.DB_PATH = original_db


# ==============================================================================
# 1. Website Owner & Superadmin Authentication Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_owner_vikram_pratap_recognition(isolated_db):
    """
    Verify website owner Vikram Pratap (विक्रम प्रताप) is recognized as administrator.
    Tests case-insensitivity, space/underscore normalization, and Devanagari script.
    """
    assert await verify_website_admin("विक्रम प्रताप") is True
    assert await verify_website_admin("विक्रम_प्रताप") is True
    assert await verify_website_admin("  विक्रम प्रताप  ") is True
    assert await verify_website_admin("Vikram Pratap") is True
    assert await verify_website_admin("vikram_pratap") is True
    assert await verify_website_admin("VIKRAM PRATAP") is True


@pytest.mark.asyncio
@respx.mock
async def test_non_admin_rejection(isolated_db):
    """
    Verify anonymous, regular editors, and Wikipedia sysops/bureaucrats are NOT
    granted website admin authority unless specifically designated.
    """
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )
    assert await verify_website_admin(None) is False
    assert await verify_website_admin("") is False
    assert await verify_website_admin("   ") is False
    assert await verify_website_admin("StandardEditor") is False
    assert await verify_website_admin("RandomWikipediaUser123") is False
    # Explicit check: Wikipedia sysops/bureaucrats must NOT be website admins
    assert await verify_website_admin("RandomWikiSysop") is False
    assert await verify_website_admin("HindiWikipediaBureaucrat") is False


# ==============================================================================
# 2. Dynamic On-Wiki Subpage Fetch & 10-Minute Caching Tests
# ==============================================================================

@pytest.mark.asyncio
@respx.mock
async def test_onwiki_subpage_fetch_and_caching(isolated_db):
    """
    Verify MediaWiki Action API subpage fetch:
    1. Fetches सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json
    2. Recognizes co-admins (CoAdminUser1, सहायक_प्रशासक)
    3. Caches result in SQLite cache_store for 10 minutes
    4. Subsequent checks use SQLite cache without hitting the network
    """
    subpage_payload = {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": 88888,
                    "ns": 2,
                    "title": "सदस्य:विक्रम प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "contentmodel": "json",
                                    "contentformat": "application/json",
                                    "content": json.dumps({
                                        "admins": ["CoAdminUser1", "सहायक_प्रशासक"],
                                        "kill_switch": False,
                                        "rate_limit_per_minute": 30,
                                    }),
                                }
                            }
                        }
                    ],
                }
            ]
        },
    }

    mock_route = respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json=subpage_payload)
    )

    # Initial call: must query MediaWiki API
    is_coadmin = await verify_website_admin("CoAdminUser1")
    assert is_coadmin is True
    assert mock_route.call_count == 1

    # Verify second user from same subpage is verified via SQLite cache (no 2nd HTTP call)
    is_second_coadmin = await verify_website_admin("सहायक_प्रशासक")
    assert is_second_coadmin is True
    assert mock_route.call_count == 1  # Hit count must still be 1 (cached!)

    # Verify SQLite cache_store row
    session_maker = get_session_maker()
    async with session_maker() as session:
        cached = await session.scalar(
            select(CacheStore).where(CacheStore.key == CACHE_KEY_ONWIKI_SUBPAGE)
        )
        assert cached is not None
        assert cached.data["admins"] == ["CoAdminUser1", "सहायक_प्रशासक"]
        # Expiration must be approximately 10 minutes in the future
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        remaining_ttl = (cached.expires_at - now_utc).total_seconds()
        assert 500 <= remaining_ttl <= 600


@pytest.mark.asyncio
@respx.mock
async def test_onwiki_subpage_formatversion1_compatibility(isolated_db):
    """Verify backwards-compatibility with MediaWiki formatversion=1 payload structure."""
    v1_payload = {
        "batchcomplete": "",
        "query": {
            "pages": {
                "99999": {
                    "pageid": 99999,
                    "ns": 2,
                    "title": "सदस्य:विक्रम प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "*": json.dumps({"admins": ["V1LegacyCoAdmin"]})
                        }
                    ],
                }
            }
        },
    }

    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json=v1_payload)
    )

    is_admin = await verify_website_admin("V1LegacyCoAdmin")
    assert is_admin is True


@pytest.mark.asyncio
@respx.mock
async def test_onwiki_subpage_syntaxhighlight_wrapper(isolated_db):
    """Verify parsing when wikitext content is wrapped in <syntaxhighlight lang='json'>."""
    wrapped_content = (
        "<syntaxhighlight lang=\"json\">\n"
        "{\n"
        "    \"admins\": [\"WrappedAdminUser\"]\n"
        "}\n"
        "</syntaxhighlight>"
    )
    payload = {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": 101,
                    "revisions": [{"slots": {"main": {"content": wrapped_content}}}],
                }
            ]
        },
    }

    respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(200, json=payload))

    assert await verify_website_admin("WrappedAdminUser") is True


@pytest.mark.asyncio
@respx.mock
async def test_onwiki_subpage_cache_expiry(isolated_db):
    """Verify that after cache expiration (10 min), a new HTTP request is made."""
    content_1 = json.dumps({"admins": ["AdminV1"]})
    payload_1 = {
        "query": {
            "pages": [{"pageid": 1, "revisions": [{"slots": {"main": {"content": content_1}}}]}]
        }
    }
    content_2 = json.dumps({"admins": ["AdminV2"]})
    payload_2 = {
        "query": {
            "pages": [{"pageid": 1, "revisions": [{"slots": {"main": {"content": content_2}}}]}]
        }
    }

    route = respx.get(ADMIN_ONWIKI_URL)
    route.side_effect = [
        httpx.Response(200, json=payload_1),
        httpx.Response(200, json=payload_2),
    ]

    # First fetch populates cache
    assert await verify_website_admin("AdminV1") is True
    assert route.call_count == 1

    # Manually expire cache in SQLite
    session_maker = get_session_maker()
    async with session_maker() as session:
        cached = await session.scalar(
            select(CacheStore).where(CacheStore.key == CACHE_KEY_ONWIKI_SUBPAGE)
        )
        assert cached is not None
        cached.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        await session.commit()

    # Next check should re-fetch and find AdminV2
    assert await verify_website_admin("AdminV2") is True
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_onwiki_network_failure_resilience(isolated_db):
    """Verify graceful handling when MediaWiki API is unavailable."""
    # Pre-populate expired cache
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20)
    session_maker = get_session_maker()
    async with session_maker() as session:
        session.add(
            CacheStore(
                key=CACHE_KEY_ONWIKI_SUBPAGE,
                category="admin_config",
                identifier="test",
                data={"admins": ["ResilientCoAdmin"]},
                created_at=past,
                expires_at=past,
            )
        )
        await session.commit()

    # Network fails with 500
    respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(500))

    # Should fall back to expired cache rather than crashing
    assert await verify_website_admin("ResilientCoAdmin") is True

    # Owner should always work even with network failure
    assert await verify_website_admin("विक्रम प्रताप") is True


# ==============================================================================
# 3. Strict 404 Security RBAC Tests (HTTP Integration)
# ==============================================================================

@pytest.mark.asyncio
async def test_strict_404_unauthenticated_requests(async_client):
    """
    Verify strict HTTP 404 on administrative routes for unauthenticated users.
    Must NEVER return 401 Unauthorized or 403 Forbidden (complete cloaking).
    """
    # HTML admin paths
    resp1 = await async_client.get("/admin")
    assert resp1.status_code == 404, f"Expected 404, got {resp1.status_code}"

    resp2 = await async_client.get("/admin/settings")
    assert resp2.status_code == 404

    resp3 = await async_client.get("/admin/random123/xyz")
    assert resp3.status_code == 404

    # API admin paths
    resp4 = await async_client.get("/api/admin/status")
    assert resp4.status_code == 404

    resp5 = await async_client.get("/api/admin/audit")
    assert resp5.status_code == 404

    resp6 = await async_client.post("/api/admin/kill-switch", json={"active": True})
    assert resp6.status_code == 404

    resp7 = await async_client.post("/api/admin/rate-limit", json={"rate_limit_per_minute": 50})
    assert resp7.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_strict_404_regular_wikipedia_editor(async_client):
    """Verify regular authenticated Wikipedia editor strictly receives 404 on admin routes."""
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )
    headers = {"X-Wiki-User": "StandardEditor"}

    resp1 = await async_client.get("/admin", headers=headers)
    assert resp1.status_code == 404

    resp2 = await async_client.get("/api/admin/status", headers=headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_strict_404_wikipedia_sysop_not_website_admin(async_client):
    """
    Verify Wikipedia sysop (RandomWikiSysop) is NOT recognized as website admin
    and strictly receives HTTP 404 on /admin and /api/admin/*.
    """
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )
    headers = {"X-Wiki-User": "RandomWikiSysop"}

    resp1 = await async_client.get("/admin", headers=headers)
    assert resp1.status_code == 404

    resp2 = await async_client.get("/api/admin/audit", headers=headers)
    assert resp2.status_code == 404


# ==============================================================================
# 4. Authorized Admin Access Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_authorized_owner_access(async_client):
    """Verify website owner विक्रम प्रताप receives 200 OK and admin dashboard HTML."""
    headers = make_wiki_user_header("विक्रम प्रताप")

    resp = await async_client.get("/admin", headers=headers)
    assert resp.status_code == 200
    assert "एडमिन पैनल" in resp.text
    assert "विक्रम प्रताप" in resp.text


@pytest.mark.asyncio
@respx.mock
async def test_authorized_coadmin_access(async_client):
    """Verify dynamic co-admin listed on on-wiki subpage receives 200 OK."""
    coadmin_content = json.dumps({"admins": ["CoAdminUser1"]})
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 2,
                    "revisions": [{"slots": {"main": {"content": coadmin_content}}}],
                }
            ]
        }
    }
    respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(200, json=payload))

    headers = {"X-Wiki-User": "CoAdminUser1"}
    resp = await async_client.get("/admin", headers=headers)
    assert resp.status_code == 200
    assert "एडमिन पैनल" in resp.text


@pytest.mark.asyncio
async def test_admin_status_api(async_client):
    """Verify /api/admin/status returns system status and admin metadata for website owner."""
    headers = make_wiki_user_header("विक्रम प्रताप")

    resp = await async_client.get("/api/admin/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "operational"
    assert "kill_switch" in data
    assert "rate_limit_per_minute" in data
    assert "db_stats" in data
    assert "admins" in data


# ==============================================================================
# 5. Kill-Switch, Rate Limiter & Rule Management Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_kill_switch_lifecycle_and_scan_interception(async_client):
    """
    Test full emergency kill-switch lifecycle:
    1. Turn kill switch ON via POST /api/admin/kill-switch
    2. Verify subsequent scan request to /api/scan halts with HTTP 503
    3. Turn kill switch OFF
    4. Verify scan request resumes normal operation (HTTP 200)
    """
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    # 1. Activate kill-switch
    on_resp = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        headers=admin_headers,
    )
    assert on_resp.status_code == 200
    assert on_resp.json()["kill_switch"] is True

    # 2. Attempt scan while kill-switch is active -> 503 Service Unavailable
    scan_resp1 = await async_client.post(
        "/api/scan",
        json={"wikitext": "परीक्षण <ref>https://example.com</ref>"},
    )
    assert scan_resp1.status_code == 503
    assert scan_resp1.headers.get("retry-after") == "300"
    assert "प्रणाली आपातकालीन स्थिति में रुकी हुई है" in scan_resp1.json()["message"]

    # 3. Deactivate kill-switch
    off_resp = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": False},
        headers=admin_headers,
    )
    assert off_resp.status_code == 200
    assert off_resp.json()["kill_switch"] is False

    # 4. Attempt scan after restoration -> 200 OK
    scan_resp2 = await async_client.post(
        "/api/scan",
        json={"wikitext": "परीक्षण <ref>https://example.com</ref>"},
    )
    assert scan_resp2.status_code == 200


@pytest.mark.asyncio
async def test_rate_limiter_adjustment(async_client):
    """Test updating rate limit via /api/admin/rate-limit."""
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    resp = await async_client.post(
        "/api/admin/rate-limit",
        json={"rate_limit_per_minute": 45},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["rate_limit_per_minute"] == 45

    # Check persistence
    status_resp = await async_client.get("/api/admin/status", headers=admin_headers)
    assert status_resp.json()["rate_limit_per_minute"] == 45


@pytest.mark.asyncio
async def test_rule_management_crud(async_client):
    """Test creating, listing, and deleting whitelist/blacklist rules."""
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    # Create rule
    create_resp = await async_client.post(
        "/api/admin/rules",
        json={
            "rule_type": "blacklist",
            "scope": "domain",
            "pattern": "spam-domain.com",
            "reason": "Known spam farm",
        },
        headers=admin_headers,
    )
    assert create_resp.status_code == 200
    rule_data = create_resp.json()["rule"]
    rule_id = rule_data["id"]
    assert rule_data["pattern"] == "spam-domain.com"

    # List rules
    list_resp = await async_client.get("/api/admin/rules", headers=admin_headers)
    assert list_resp.status_code == 200
    rules = list_resp.json()["rules"]
    assert any(r["id"] == rule_id for r in rules)

    # Delete rule
    del_resp = await async_client.delete(f"/api/admin/rules/{rule_id}", headers=admin_headers)
    assert del_resp.status_code == 200


# ==============================================================================
# 6. Audit Logging Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_audit_logging_with_ip_hashing(isolated_db):
    """
    Verify audit logging records action and hashes IP addresses
    to strictly adhere to Wikimedia privacy guidelines.
    """
    raw_ip = "192.168.1.100"
    log = await log_audit_event(
        action="article_scan",
        username="TestUser",
        details={"page": "भारत"},
        ip_address=raw_ip,
    )

    assert log.id is not None
    assert log.username == "TestUser"
    assert log.ip_hash is not None
    assert log.ip_hash != raw_ip  # Raw IP must NOT be stored
    assert len(log.ip_hash) == 64  # SHA-256 hash length


@pytest.mark.asyncio
async def test_admin_audit_log_query_api(async_client):
    """Verify /api/admin/audit retrieves logged events for admin."""
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    await log_audit_event(
        action="manual_test_event",
        username="विक्रम प्रताप",
        details={"info": "verification"},
    )

    resp = await async_client.get("/api/admin/audit", headers=admin_headers)
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    assert len(logs) > 0
    assert any(entry["action"] == "manual_test_event" for entry in logs)


# ==============================================================================
# 7. Authentication Flow & Session Management Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_auth_login_endpoints(async_client):
    """Test OAuth login URL generation and redirection."""
    # JSON response
    resp1 = await async_client.get("/api/auth/login")
    assert resp1.status_code == 200
    data = resp1.json()
    assert "auth_url" in data
    assert "meta.wikimedia.org" in data["auth_url"]
    assert "state" in data

    # Redirect response
    resp2 = await async_client.get("/api/auth/login?redirect=true")
    assert resp2.status_code == 307
    assert "meta.wikimedia.org" in resp2.headers["Location"]


@pytest.mark.asyncio
async def test_auth_dev_login_and_me_lifecycle(async_client):
    """
    Test rapid development login (/api/auth/dev-login), session cookie issuance,
    profile inspection (/api/auth/me), and logout (/api/auth/logout).
    """
    # 1. Dev login as website owner
    login_resp = await async_client.post(
        "/api/auth/dev-login",
        json={"username": "विक्रम प्रताप"},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["status"] == "success"
    assert data["is_admin"] is True
    session_val = login_resp.cookies["session"]
    cookie_header = {"Cookie": f"session={session_val}"}

    # 2. Check /api/auth/me with session cookie
    me_resp = await async_client.get(
        "/api/auth/me",
        headers=cookie_header,
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["authenticated"] is True
    assert me_data["username"] == "विक्रम प्रताप"
    assert me_data["is_admin"] is True

    # 3. Access /admin using session cookie
    admin_resp = await async_client.get(
        "/admin",
        headers=cookie_header,
    )
    assert admin_resp.status_code == 200
    assert "एडमिन पैनल" in admin_resp.text

    # 4. Logout
    logout_resp = await async_client.post(
        "/api/auth/logout",
        headers=cookie_header,
    )
    assert logout_resp.status_code == 200

    # 5. Check /api/auth/me after logout
    anon_resp = await async_client.get("/api/auth/me")
    assert anon_resp.status_code == 200
    assert anon_resp.json()["authenticated"] is False


@pytest.mark.asyncio
@respx.mock
async def test_dev_login_regular_user_masked_from_admin(async_client):
    """
    Test dev login as a regular user: user is authenticated but is_admin=False,
    and attempting to access /admin still strictly results in HTTP 404.
    """
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )
    login_resp = await async_client.post(
        "/api/auth/dev-login",
        json={"username": "SomeRegularEditor"},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["is_admin"] is False
    session_cookie = login_resp.cookies["session"]

    # Access /admin with regular user session -> STRICT 404
    admin_resp = await async_client.get(
        "/admin",
        headers={"Cookie": f"session={session_cookie}"},
    )
    assert admin_resp.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_auth_callback_flow(async_client):
    """Test OAuth callback endpoint handling."""
    respx.post("https://meta.wikimedia.org/w/rest.php/oauth2/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "valid_token_123"})
    )
    respx.get("https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile").mock(
        return_value=httpx.Response(
            200,
            json={"username": "विक्रम प्रताप", "sub": 1001, "groups": ["*"]},
        )
    )
    resp = await async_client.get(
        "/api/auth/callback?code=valid_auth_code_123",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["is_admin"] is True
    assert "session" in resp.cookies


@pytest.mark.asyncio
async def test_admin_access_via_signed_session_cookie(async_client):
    """Verify admin access succeeds using cryptographically signed session cookie."""
    from app.core.security import create_session_token
    token = create_session_token({"username": "विक्रम प्रताप", "is_admin": True})
    headers = {"Cookie": f"session={token}"}
    resp = await async_client.get("/admin", headers=headers)
    assert resp.status_code == 200
    assert "एडमिन पैनल" in resp.text

    api_resp = await async_client.get("/api/admin/status", headers=headers)
    assert api_resp.status_code == 200
    assert api_resp.json()["status"] == "operational"


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_name", ["विक्रम प्रताप", "Vikram Pratap"])
async def test_unsigned_username_cookie_strictly_rejected(async_client, forged_name: str):
    """Regression Test: Unsigned username cookie MUST strictly return 404 on admin endpoints."""
    cookies = {"username": urllib.parse.quote(forged_name)}
    resp_admin = await async_client.get("/admin", cookies=cookies)
    assert resp_admin.status_code == 404

    resp_status = await async_client.get("/api/admin/status", cookies=cookies)
    assert resp_status.status_code == 404
