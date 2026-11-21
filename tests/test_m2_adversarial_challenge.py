"""
Adversarial Challenge Test Suite for Milestone 2:
Website Admin RBAC & Strict 404 Concealment.

Adversarial Verification Dimensions:
1. URL and Path Traversal Fuzzing:
   - Traversal tokens: /admin/.., /api/admin/../admin/status, /api/../admin
   - Multi-slash variations: //admin, ///admin, //api//admin//status
   - URL-encoded path components: %2fadmin, %2e%2e, %252f
   - Case variations, trailing slashes, semicolon matrix parameters
   - Strict 404 enforcement and zero info leakage.
2. Arbitrary Wikipedia Sysop / Bureaucrat Username Impersonation:
   - Sysops and bureaucrats from Hindi & English Wikipedia
     (e.g. Hindustanilanguage, आर्यावर्त, SM7, Anamidas)
   - Must strictly receive HTTP 404 across all administrative routes.
3. Unauthorized State Mutation Attempts:
   - POST /api/admin/kill-switch, POST /api/admin/rate-limit,
     POST /api/admin/rules, DELETE /api/admin/rules/{id}
   - Verify 404 response AND confirm that database state remains completely unchanged.
4. Valid Website Administrator Dual Identity (Vikram Pratap / विक्रम प्रताप):
   - Access to all admin GET, POST, DELETE endpoints with both English and Devanagari identities.
   - Cookie-based session and X-Wiki-User header authentication.
5. On-Wiki Subpage Adversarial Payload Fuzzing:
   - Corrupted JSON, unexpected schema types, null elements, API outages.
"""

import json
import urllib.parse
import httpx
import pytest
import pytest_asyncio
import respx

from app.core.config import get_settings
from app.db.database import close_db, init_db

ADMIN_ONWIKI_URL = "https://hi.wikipedia.org/w/api.php"


def make_wiki_user_header(username: str) -> dict[str, str]:
    """Encode header value safely to avoid non-ASCII header issues."""
    return {"X-Wiki-User": urllib.parse.quote(username)}


@pytest_asyncio.fixture
async def challenge_db(tmp_path):
    """Provide an isolated temporary SQLite database for adversarial challenge tests."""
    temp_db_file = str(tmp_path / "test_m2_challenge.db")
    settings = get_settings()
    original_db = settings.DB_PATH
    settings.DB_PATH = temp_db_file

    await close_db()
    await init_db()

    yield

    await close_db()
    settings.DB_PATH = original_db


# ==============================================================================
# 1. URL Path Traversal & Variation Fuzzing (Strict 404 Concealment)
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/admin/..",
        "/admin/../",
        "/admin/../admin",
        "/api/admin/../admin/status",
        "/api/../admin",
        "//admin",
        "///admin",
        "//api//admin//status",
        "/api//admin/status",
        "//admin/settings",
        "/%2fadmin",
        "/api/%2fadmin/status",
        "/admin%2f..",
        "/admin/%2e%2e",
        "/admin/",
        "/api/admin/",
        "/api/admin/status/",
        "/admin/.",
        "/admin/./",
        "/admin;foo=bar",
        "/api/admin;foo=bar/status",
        "/api/admin/status?param=1",
        "/api/admin/status?user=admin",
        "/Admin",
        "/ADMIN",
        "/API/ADMIN/status",
        "/%20admin",
        "/admin%20",
        "/api/admin/kill-switch/",
        "/api/admin/rules/",
    ],
)
async def test_admin_url_path_traversal_and_variations_fuzzing(async_client, path: str):
    """
    Fuzz administrative routes with path traversal tokens, multi-slashes,
    URL-encoding, and case variations.
    MUST strictly return HTTP 404 for unauthenticated requests attempting to access
    admin capabilities, and never leak sensitive administrative interfaces.
    """
    target = f"http://testserver{path}" if path.startswith("//") else path
    resp = await async_client.get(target)

    # Note: RFC 3986 section 5.2.4 dot-segment normalization resolves unencoded
    # '/admin/..' and '/admin/../' strictly to the public root '/' (the homepage).
    if path in ("/admin/..", "/admin/../"):
        assert resp.status_code == 200
        assert "प्रशासनिक नियंत्रण" not in resp.text
    else:
        assert resp.status_code == 404, (
            f"Path traversal/fuzzing variation '{path}' returned {resp.status_code} "
            f"instead of strict 404. Response body: {resp.text[:200]}"
        )
    # Check that response does not leak internal stack traces or admin keys
    assert "Traceback" not in resp.text
    assert "sqlite" not in resp.text.lower()
    assert "sqlalchemy" not in resp.text.lower()


# ==============================================================================
# 2. Wikipedia Sysop / Bureaucrat Username Rejection Tests
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sysop_username",
    [
        "Hindustanilanguage",
        "आर्यावर्त",
        "Anamidas",
        "SM7",
        "Mariya7",
        "WikiSysop_99",
        "Bureaucrat_Special",
        "Admin",
        "Administrator",
        "Sysop",
        "bureaucrat",
        "steward",
        "checkuser",
        "oversight",
        "WikipediaAdmin",
        "hiwiki_sysop",
        "प्रबंधक",
        "प्रशासक",
    ],
)
@respx.mock
async def test_wikipedia_sysops_bureaucrats_arbitrary_rejected(
    async_client, sysop_username: str
):
    """
    Verify that arbitrary Wikipedia sysops and bureaucrats are NOT recognized
    as website administrators and receive strict HTTP 404 on all admin endpoints.
    """
    # Mock MediaWiki Action API subpage returning an empty co-admin list
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )

    headers = make_wiki_user_header(sysop_username)

    # 1. HTML Admin Interface
    r1 = await async_client.get("/admin", headers=headers)
    assert r1.status_code == 404, f"Sysop '{sysop_username}' accessed /admin: {r1.status_code}"

    # 2. Status API
    r2 = await async_client.get("/api/admin/status", headers=headers)
    assert r2.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/status: {r2.status_code}"
    )

    # 3. Kill-Switch API
    r3 = await async_client.post(
        "/api/admin/kill-switch", json={"active": True}, headers=headers
    )
    assert r3.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/kill-switch: {r3.status_code}"
    )

    # 4. Rate-Limit API
    r4 = await async_client.post(
        "/api/admin/rate-limit", json={"rate_limit_per_minute": 100}, headers=headers
    )
    assert r4.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/rate-limit: {r4.status_code}"
    )

    # 5. Audit Log API
    r5 = await async_client.get("/api/admin/audit", headers=headers)
    assert r5.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/audit: {r5.status_code}"
    )

    # 6. Rules API
    r6 = await async_client.post(
        "/api/admin/rules",
        json={"rule_type": "blacklist", "scope": "domain", "pattern": "bad.com", "reason": "test"},
        headers=headers,
    )
    assert r6.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/rules: {r6.status_code}"
    )

    # 7. Queue API
    r7 = await async_client.get("/api/admin/queue", headers=headers)
    assert r7.status_code == 404, (
        f"Sysop '{sysop_username}' accessed /api/admin/queue: {r7.status_code}"
    )


# ==============================================================================
# 3. Unauthorized POST Mutations & Database State Invariance
# ==============================================================================

@pytest.mark.asyncio
@respx.mock
async def test_unauthorized_post_mutations_rejected_and_state_unaltered(async_client):
    """
    Attempt unauthorized POST mutations:
    - POST /api/admin/kill-switch
    - POST /api/admin/rate-limit
    - POST /api/admin/rules
    - DELETE /api/admin/rules/1
    Across unauthenticated, regular user, and sysop actors.
    Verify that every attempt yields 404 and DB state remains strictly untouched.
    """
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(200, json={"query": {"pages": []}})
    )

    unauthorized_actors = [
        {},  # Unauthenticated
        make_wiki_user_header("RegularEditor123"),  # Regular Wikipedia user
        make_wiki_user_header("WikipediaSysopUser"),  # Wikipedia sysop
    ]

    for headers in unauthorized_actors:
        actor_name = headers.get("X-Wiki-User", "Anonymous")

        # 1. Attempt unauthorized kill-switch toggle
        resp_kill = await async_client.post(
            "/api/admin/kill-switch",
            json={"active": True},
            headers=headers,
        )
        assert resp_kill.status_code == 404, (
            f"Unauthorized actor {actor_name} received {resp_kill.status_code} on kill-switch"
        )

        # 2. Attempt unauthorized rate-limit modification
        resp_rate = await async_client.post(
            "/api/admin/rate-limit",
            json={"rate_limit_per_minute": 999},
            headers=headers,
        )
        assert resp_rate.status_code == 404, (
            f"Unauthorized actor {actor_name} received {resp_rate.status_code} on rate-limit"
        )

        # 3. Attempt unauthorized rule injection
        resp_rule = await async_client.post(
            "/api/admin/rules",
            json={
                "rule_type": "blacklist",
                "scope": "domain",
                "pattern": f"attacker-{actor_name}.org",
                "reason": "malicious injection",
            },
            headers=headers,
        )
        assert resp_rule.status_code == 404, (
            f"Unauthorized actor {actor_name} received {resp_rule.status_code} on rules"
        )

        # 4. Attempt unauthorized rule deletion
        resp_del = await async_client.delete(
            "/api/admin/rules/1",
            headers=headers,
        )
        assert resp_del.status_code == 404, (
            f"Unauthorized actor {actor_name} received {resp_del.status_code} on delete rule"
        )

    # NOW: Authenticate as valid website owner Vikram Pratap and inspect DB state
    admin_headers = make_wiki_user_header("विक्रम प्रताप")
    status_resp = await async_client.get("/api/admin/status", headers=admin_headers)
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    # Verify state was NOT mutated
    assert status_data["kill_switch"] is False, "Kill switch was illegally mutated!"
    assert status_data["rate_limit_per_minute"] == 30, "Rate limit was illegally mutated!"
    assert status_data["db_stats"]["rules_count"] == 0, "Rules table was illegally mutated!"


# ==============================================================================
# 4. Valid Admin Dual-Identity Access (Vikram Pratap / विक्रम प्रताप)
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admin_identity",
    [
        "Vikram Pratap",
        "vikram pratap",
        "VIKRAM PRATAP",
        "vikram_pratap",
        " Vikram Pratap ",
        "विक्रम प्रताप",
        "विक्रम_प्रताप",
        "  विक्रम प्रताप  ",
    ],
)
async def test_valid_admin_dual_identity_full_access(async_client, admin_identity: str):
    """
    Verify that website administrator Vikram Pratap in both English and Devanagari
    scripts, with whitespace and underscore variations, successfully accesses
    all administrative endpoints.
    """
    headers = make_wiki_user_header(admin_identity)

    # 1. HTML Admin Page
    r_html = await async_client.get("/admin", headers=headers)
    assert r_html.status_code == 200
    assert "एडमिन पैनल" in r_html.text

    # 2. HTML Subpath Page
    r_sub = await async_client.get("/admin/system-overview", headers=headers)
    assert r_sub.status_code == 200
    assert "system-overview" in r_sub.text

    # 3. Status API
    r_status = await async_client.get("/api/admin/status", headers=headers)
    assert r_status.status_code == 200
    assert r_status.json()["status"] == "operational"

    # 4. Kill-Switch API (Set to True)
    r_k1 = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        headers=headers,
    )
    assert r_k1.status_code == 200
    assert r_k1.json()["kill_switch"] is True

    # Kill-Switch API (Reset to False)
    r_k2 = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": False},
        headers=headers,
    )
    assert r_k2.status_code == 200
    assert r_k2.json()["kill_switch"] is False

    # 5. Rate-Limit API
    r_rate = await async_client.post(
        "/api/admin/rate-limit",
        json={"rate_limit_per_minute": 75},
        headers=headers,
    )
    assert r_rate.status_code == 200
    assert r_rate.json()["rate_limit_per_minute"] == 75

    # 6. Rules Management (Create -> List -> Delete)
    r_rule_create = await async_client.post(
        "/api/admin/rules",
        json={
            "rule_type": "whitelist",
            "scope": "domain",
            "pattern": "trusted-wiki-source.org",
            "reason": "Authorized educational domain",
        },
        headers=headers,
    )
    assert r_rule_create.status_code == 200
    rule_id = r_rule_create.json()["rule"]["id"]

    r_rules_list = await async_client.get("/api/admin/rules", headers=headers)
    assert r_rules_list.status_code == 200
    assert any(r["id"] == rule_id for r in r_rules_list.json()["rules"])

    r_rule_del = await async_client.delete(f"/api/admin/rules/{rule_id}", headers=headers)
    assert r_rule_del.status_code == 200

    # 7. Audit Log APIs
    r_audit = await async_client.get("/api/admin/audit", headers=headers)
    assert r_audit.status_code == 200
    assert "audit_logs" in r_audit.json()

    r_audit_alias = await async_client.get("/api/admin/audit-logs", headers=headers)
    assert r_audit_alias.status_code == 200

    # 8. Queue Inspection API
    r_queue = await async_client.get("/api/admin/queue", headers=headers)
    assert r_queue.status_code == 200
    assert "requests" in r_queue.json()


# ==============================================================================
# 5. Session Cookie Authentication for Valid Admin Dual Identity
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("admin_name", ["विक्रम प्रताप", "Vikram Pratap"])
async def test_valid_admin_session_cookie_access(async_client, admin_name: str):
    """
    Verify dev-login session cookie issuance and full administrative access
    for both Devanagari and English identities.
    """
    # 1. Dev login
    login_resp = await async_client.post(
        "/api/auth/dev-login",
        json={"username": admin_name},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["status"] == "success"
    assert data["is_admin"] is True

    session_cookie = login_resp.cookies["session"]
    cookie_header = {"Cookie": f"session={session_cookie}"}

    # 2. Access /admin and /api/admin/status via session cookie
    page_resp = await async_client.get("/admin", headers=cookie_header)
    assert page_resp.status_code == 200
    assert "एडमिन पैनल" in page_resp.text

    status_resp = await async_client.get("/api/admin/status", headers=cookie_header)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "operational"


# ==============================================================================
# 6. On-Wiki Subpage Adversarial Payload Fuzzing
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupted_payload",
    [
        "INVALID JSON CONTENT { [",
        json.dumps({"admins": "NotAList"}),
        json.dumps({"admins": None}),
        json.dumps({"admins": [None, 12345, {}, []]}),
        json.dumps({"other_keys": True}),
        json.dumps({}),
        "",
        "   ",
    ],
)
@respx.mock
async def test_onwiki_subpage_adversarial_payload_resilience(
    async_client, corrupted_payload: str
):
    """
    Test that malicious or corrupted data on the on-wiki admin subpage
    does not cause crashes, does not raise unhandled 500 exceptions,
    and does not accidentally grant administrative permissions.
    """
    respx.get(ADMIN_ONWIKI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "revisions": [{"slots": {"main": {"content": corrupted_payload}}}],
                        }
                    ]
                }
            },
        )
    )

    # 1. Random user must still receive 404
    headers = make_wiki_user_header("RandomAttackerUser")
    resp = await async_client.get("/api/admin/status", headers=headers)
    assert resp.status_code == 404

    # 2. Site owner Vikram Pratap must STILL be recognized as admin
    owner_headers = make_wiki_user_header("विक्रम प्रताप")
    owner_resp = await async_client.get("/api/admin/status", headers=owner_headers)
    assert owner_resp.status_code == 200


# ==============================================================================
# 7. Unsigned Cookie Spoofing Challenge (Authentication Bypass Vulnerability)
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("spoofed_username", ["Vikram Pratap", "विक्रम प्रताप"])
async def test_unsigned_cookie_username_spoofing_rejected(async_client, spoofed_username: str):
    """
    CRITICAL SECURITY VULNERABILITY TEST:
    An attacker must NOT be able to forge administrative credentials by setting
    an arbitrary unsigned 'Cookie: username=...' header.
    Only cryptographically signed session tokens (Cookie: session=...) or valid OAuth
    credentials should authenticate the user.
    Unsigned username cookies MUST be strictly rejected with HTTP 404 on administrative routes.
    """
    spoofed_cookies = {"username": urllib.parse.quote(spoofed_username)}

    # Attempt access to admin interface with unsigned plain username cookie
    r_admin = await async_client.get("/admin", cookies=spoofed_cookies)
    assert r_admin.status_code == 404, (
        f"CRITICAL VULNERABILITY: Unsigned cookie 'username={spoofed_username}' "
        f"bypassed authentication and accessed /admin with status {r_admin.status_code}!"
    )

    # Attempt access to admin status API with unsigned plain username cookie
    r_status = await async_client.get("/api/admin/status", cookies=spoofed_cookies)
    assert r_status.status_code == 404, (
        f"CRITICAL VULNERABILITY: Unsigned cookie 'username={spoofed_username}' "
        f"bypassed authentication and accessed /api/admin/status "
        f"with status {r_status.status_code}!"
    )

    # Attempt mutation of kill switch with unsigned plain username cookie
    r_kill = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        cookies=spoofed_cookies,
    )
    assert r_kill.status_code == 404, (
        f"CRITICAL VULNERABILITY: Unsigned cookie 'username={spoofed_username}' "
        f"mutated kill-switch with status {r_kill.status_code}!"
    )
