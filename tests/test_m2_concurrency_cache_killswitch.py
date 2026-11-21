"""
Adversarial Challenge Test Suite for Milestone 2:
Concurrency, Cache, Kill-Switch, and Audit Log Concurrency.
Authored by: challenger_m2_2 (Teamwork Preview Challenger - Instance 2)

Empirical Challenge Objectives:
1. Concurrent calls to verify_website_admin / /admin while cache is cold
   and multiple coroutines fetch the on-wiki subpage simultaneously.
2. Malformed JSON payloads from Wikipedia Action API (corrupted on-wiki subpage content).
3. Rapid toggling of the kill switch: /api/scan responds with HTTP 503 and Retry-After: 300
   immediately, and recovers immediately when deactivated.
4. SQLite audit log write concurrency under rapid bursts.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import urllib.parse
import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.db.database import get_session_maker
from app.db.models import CacheStore
from app.services.audit import count_audit_logs, get_audit_logs, log_audit_event
from app.services.auth import (
    CACHE_KEY_ONWIKI_SUBPAGE,
    fetch_onwiki_admins,
    set_kill_switch,
    verify_website_admin,
)

ADMIN_ONWIKI_URL = "https://hi.wikipedia.org/w/api.php"


def make_wiki_user_header(username: str) -> dict[str, str]:
    """Encode header value safely to avoid non-ASCII header issues."""
    return {"X-Wiki-User": urllib.parse.quote(username)}


# ==============================================================================
# CHALLENGE 1: Cold Cache Concurrency & Race Conditions
# ==============================================================================

@pytest.mark.asyncio
async def test_cold_cache_concurrent_fetch_race_condition(test_db):
    """
    Adversarial test: Multiple coroutines simultaneously calling
    verify_website_admin() while cache is cold.
    Simulates network latency to force coroutine interleaving during DB insert.
    Verifies:
    1. No coroutine crashes with IntegrityError (UNIQUE constraint on cache_store.key).
    2. All coroutines return the correct boolean verdict.
    3. The cache_store table ends up with exactly 1 clean record.
    """
    coadmin_name = "ConcurrentCoAdmin"
    subpage_payload = {
        "query": {
            "pages": [
                {
                    "pageid": 101,
                    "title": "सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "content": json.dumps({"admins": [coadmin_name]})
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    async def delayed_response(request):
        await asyncio.sleep(0.05)  # 50ms latency to maximize concurrency window
        return httpx.Response(200, json=subpage_payload)

    with respx.mock(assert_all_called=False) as router:
        router.get(ADMIN_ONWIKI_URL).mock(side_effect=delayed_response)

        # Launch 20 concurrent coroutines with cold cache
        concurrency = 20
        tasks = [
            asyncio.create_task(verify_website_admin(coadmin_name))
            for _ in range(concurrency)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, f"Concurrent fetch raised exceptions: {exceptions}"

        # All coroutines should successfully verify the co-admin
        assert all(r is True for r in results), f"Inconsistent verdicts: {results}"

        # Verify SQLite cache_store state has exactly 1 entry
        session_factory = get_session_maker()
        async with session_factory() as session:
            stmt = select(func.count(CacheStore.key)).where(
                CacheStore.key == CACHE_KEY_ONWIKI_SUBPAGE
            )
            count = (await session.execute(stmt)).scalar_one()
            assert count == 1, f"Expected 1 cache record, found {count}"


@pytest.mark.asyncio
async def test_cold_cache_concurrent_http_admin_requests_race_condition(async_client):
    """
    Adversarial test: 10 concurrent HTTP requests to /admin by a co-admin
    while the cache is cold.
    Verifies that get_or_create_user + verify_website_admin does not trigger
    an unhandled server exception:
    (sqlite3.IntegrityError) UNIQUE constraint failed: users.username.
    """
    coadmin_name = "ConcurrentHttpAdmin"
    subpage_payload = {
        "query": {
            "pages": [
                {
                    "pageid": 102,
                    "title": "सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "content": json.dumps({"admins": [coadmin_name]})
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    async def delayed_resp(request):
        await asyncio.sleep(0.04)
        return httpx.Response(200, json=subpage_payload)

    with respx.mock(assert_all_called=False) as router:
        router.get(ADMIN_ONWIKI_URL).mock(side_effect=delayed_resp)

        headers = make_wiki_user_header(coadmin_name)
        tasks = [
            async_client.get("/admin", headers=headers)
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Check for unhandled exceptions or 500 status codes
        for i, resp in enumerate(responses):
            assert not isinstance(resp, Exception), (
                f"Request {i} raised unhandled exception: {resp}"
            )
            assert resp.status_code == 200, (
                f"Request {i} failed with status {resp.status_code}: {resp.text}"
            )


# ==============================================================================
# CHALLENGE 2: Malformed JSON Payloads from Wikipedia Action API
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupted_content,expected_admin",
    [
        # 1. Truncated JSON
        ('{"admins": ["CoadminA", "CoadminB"', False),
        # 2. Raw wikitext heading instead of JSON
        ('== विकिपीडिया एडमिन सूची ==\n* CoadminA\n* CoadminB', False),
        # 3. HTML 502/Error page content
        ('<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1></body></html>', False),
        # 4. JSON array at root instead of dict
        ('["CoadminA", "CoadminB"]', False),
        # 5. Raw string at root
        ('"CoadminA"', False),
        # 6. Raw integer at root
        ('12345', False),
        # 7. Null/None
        ('null', False),
        # 8. Dict with admins as a string instead of a list
        ('{"admins": "CoadminA"}', False),
        # 9. Dict with admins as an integer
        ('{"admins": 9999}', False),
        # 10. Dict with admins as null
        ('{"admins": null}', False),
        # 11. Dict with empty object
        ('{}', False),
        # 12. Valid JSON with extra whitespace around admin names
        ('{"admins": ["  CoadminA  "]}', True),
    ],
)
async def test_malformed_json_subpage_resilience(test_db, corrupted_content, expected_admin):
    """
    Adversarial test: Feed various malformed, corrupted, and unexpected JSON structures
    from Wikipedia Action API revisions and verify fetch_onwiki_admins does not crash.
    """
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 200,
                    "title": "सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "content": corrupted_content
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    with respx.mock:
        respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(200, json=payload))
        # Ensure cache is bypassed with force_refresh
        result = await fetch_onwiki_admins(force_refresh=True)
        assert isinstance(result, dict), f"Result must be dict, got {type(result)}"
        assert "admins" in result

        is_admin = await verify_website_admin("CoadminA")
        assert is_admin is expected_admin, (
            f"Expected {expected_admin} for content {corrupted_content!r}, got {is_admin}"
        )


@pytest.mark.asyncio
async def test_type_confusion_null_in_admins_list_security(test_db):
    """
    Adversarial test: {"admins": [null, 123, {"evil": "data"}]}.
    Security Check: Does an attacker logging in with username 'None', 'none',
    or '123' gain admin privileges via str(ca) conversion?
    """
    corrupted_content = json.dumps({"admins": [None, 123, {"evil": "data"}]})
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 201,
                    "title": "सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "content": corrupted_content
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    with respx.mock:
        respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(200, json=payload))

        # Check if username "None" or "none" gains admin privileges
        none_admin = await verify_website_admin("None")
        assert none_admin is False, (
            "CRITICAL VULNERABILITY: Username 'None' granted admin via str(None)!"
        )

        # Check if username "123" gains admin privileges
        int_admin = await verify_website_admin("123")
        assert int_admin is False, (
            "CRITICAL VULNERABILITY: Username '123' granted admin via str(123)!"
        )


@pytest.mark.asyncio
async def test_malformed_json_fallback_to_expired_cache(test_db):
    """
    Adversarial test: When MediaWiki returns malformed JSON, verify that
    fetch_onwiki_admins falls back to an existing expired cached list.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    expired_time = now_utc - timedelta(minutes=15)

    # Seed an expired cache entry with a legitimate co-admin
    session_factory = get_session_maker()
    async with session_factory() as session:
        async with session.begin():
            cache_entry = CacheStore(
                key=CACHE_KEY_ONWIKI_SUBPAGE,
                category="admin_config",
                identifier="test_page",
                data={"admins": ["ValidExpiredCoAdmin"]},
                created_at=expired_time - timedelta(minutes=10),
                expires_at=expired_time,
            )
            session.add(cache_entry)

    corrupted_payload = {
        "query": {
            "pages": [
                {
                    "pageid": 202,
                    "revisions": [{"slots": {"main": {"content": "CORRUPTED_NON_JSON"}}}]
                }
            ]
        }
    }

    with respx.mock:
        respx.get(ADMIN_ONWIKI_URL).mock(return_value=httpx.Response(200, json=corrupted_payload))
        # Should attempt fetch, fail parse, and fall back to expired cache
        result = await fetch_onwiki_admins()
        assert result.get("admins") == ["ValidExpiredCoAdmin"]
        # Co-admin should still be recognized
        assert await verify_website_admin("ValidExpiredCoAdmin") is True


# ==============================================================================
# CHALLENGE 3: Kill Switch Rapid Toggling & Retry-After: 300 Header
# ==============================================================================

@pytest.mark.asyncio
async def test_kill_switch_rapid_toggling_and_retry_after_header(async_client):
    """
    Adversarial test: Rapid toggling of kill switch.
    Verifies that:
    1. When active, /api/scan responds with HTTP 503 AND 'Retry-After: 300' header.
    2. When deactivated, /api/scan immediately recovers to HTTP 200.
    3. Admin endpoints (/api/admin/status, /admin) remain accessible regardless of kill switch.
    """
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    for cycle in range(3):
        # 1. Activate kill switch
        resp = await async_client.post(
            "/api/admin/kill-switch",
            json={"active": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["kill_switch"] is True

        # Verify /api/scan returns 503 AND Retry-After: 300 header
        scan_blocked = await async_client.post(
            "/api/scan",
            json={"wikitext": f"Cycle {cycle} <ref>https://test.org</ref>"},
        )
        assert scan_blocked.status_code == 503, (
            f"Cycle {cycle}: Expected 503, got {scan_blocked.status_code}"
        )
        # CRITICAL ASSERTION: Retry-After: 300 header must be present
        retry_after = scan_blocked.headers.get("retry-after")
        assert retry_after == "300", (
            f"Cycle {cycle}: Missing or invalid Retry-After header! Got: {retry_after!r}"
        )

        # Verify admin endpoint remains accessible during kill switch
        admin_resp = await async_client.get("/api/admin/status", headers=admin_headers)
        assert admin_resp.status_code == 200, "Admin API blocked by kill switch!"

        # 2. Deactivate kill switch
        resp_off = await async_client.post(
            "/api/admin/kill-switch",
            json={"active": False},
            headers=admin_headers,
        )
        assert resp_off.status_code == 200
        assert resp_off.json()["kill_switch"] is False

        # Verify /api/scan immediately recovers to 200
        scan_ok = await async_client.post(
            "/api/scan",
            json={"wikitext": f"Cycle {cycle} <ref>https://test.org</ref>"},
        )
        assert scan_ok.status_code == 200, (
            f"Cycle {cycle}: Expected 200 on recovery, got {scan_ok.status_code}"
        )


@pytest.mark.asyncio
async def test_kill_switch_admin_cloaking_precedence(async_client):
    """
    Adversarial test: When kill switch is active, an unauthorized user requesting
    an admin route (/admin or /api/admin/status) must STILL receive strict 404,
    not 503 or 401/403. Admin RBAC cloaking must take precedence.
    """
    # Activate kill switch
    await set_kill_switch(True, updated_by="test")

    # Regular user tries to access /admin
    resp = await async_client.get(
        "/admin",
        headers={"X-Wiki-User": "RegularUser"},
    )
    assert resp.status_code == 404, f"Expected 404 for regular user, got {resp.status_code}"

    # Unauthenticated user tries to access /api/admin/status
    resp_api = await async_client.get("/api/admin/status")
    assert resp_api.status_code == 404, (
        f"Expected 404 for unauthenticated user, got {resp_api.status_code}"
    )


# ==============================================================================
# CHALLENGE 4: SQLite Audit Log Write Concurrency Under Rapid Bursts
# ==============================================================================

@pytest.mark.asyncio
async def test_audit_log_write_concurrency_burst(test_db):
    """
    Adversarial test: Fire 100 concurrent log_audit_event calls in a rapid burst
    without shared sessions to test SQLite WAL mode concurrency and connection pooling.
    Verifies that:
    1. Zero 'database is locked' OperationalErrors occur.
    2. Exactly 100 audit entries are persisted.
    3. All entries have valid SHA-256 IP hashes and action values.
    """
    burst_count = 100

    async def log_single_event(idx: int):
        ip = f"192.168.1.{idx % 250}"
        return await log_audit_event(
            action=f"burst_event_{idx % 5}",
            username=f"user_{idx}",
            ip_address=ip,
            details={"index": idx, "payload": "burst_test"},
        )

    tasks = [asyncio.create_task(log_single_event(i)) for i in range(burst_count)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    exceptions = [r for r in results if isinstance(r, Exception)]
    assert not exceptions, f"Audit log burst raised exceptions: {exceptions[:5]}"

    # Verify total count in database
    total = await count_audit_logs()
    assert total == burst_count, f"Expected {burst_count} audit logs, found {total}"

    # Verify log contents and IP pseudonymization
    logs = await get_audit_logs(limit=burst_count)
    assert len(logs) == burst_count
    for log in logs:
        assert log.ip_hash is not None
        # Verify IP hash is non-empty SHA-256 string
        assert len(log.ip_hash) in (16, 64), (
            f"IP hash unexpected length: {len(log.ip_hash)}"
        )
        assert log.action.startswith("burst_event_")


@pytest.mark.asyncio
async def test_audit_log_interleaved_readers_and_writers(test_db):
    """
    Adversarial test: Concurrently execute 40 write tasks and 40 read/count tasks
    to verify SQLite WAL mode allows concurrent readers without blocking writers.
    """
    write_count = 40
    read_count = 40

    async def writer(i: int):
        return await log_audit_event(
            action="interleaved_write",
            username=f"writer_{i}",
            ip_address=f"10.0.0.{i}",
            details={"i": i},
        )

    async def reader(i: int):
        c = await count_audit_logs()
        logs = await get_audit_logs(limit=10)
        return c, len(logs)

    tasks = []
    for i in range(max(write_count, read_count)):
        if i < write_count:
            tasks.append(asyncio.create_task(writer(i)))
        if i < read_count:
            tasks.append(asyncio.create_task(reader(i)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    exceptions = [r for r in results if isinstance(r, Exception)]
    assert not exceptions, f"Interleaved audit read/write raised exceptions: {exceptions[:5]}"

    final_count = await count_audit_logs(action="interleaved_write")
    assert final_count == write_count, f"Expected {write_count} writes, counted {final_count}"
