"""
Adversarial Concurrency Challenge Test Suite for Milestone 2:
High-Throughput SQLite Concurrency, Audit Log Bursts, and Kill-Switch Semantics.

Authored by: challenger_m2_iter3_2_rep (Teamwork Preview Challenger - Instance 2 Replacement)

Challenge Objectives:
1. 100-request burst audit logging without SQLite locks or failures.
2. 50-burst audit logging interleaved with concurrent readers.
3. 100-request burst cold-cache admin verification without UNIQUE constraint failures.
4. Strict 503 emergency maintenance response on /api/scan* with 'Retry-After: 300'
   and authentic Hindi maintenance payload.
5. Admin route stealth (404 Not Found) precedence during active kill-switch bursts.
"""

import asyncio
import json
import urllib.parse
from typing import Any, Dict, List

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.db.database import get_session_maker
from app.db.models import CacheStore
from app.services.audit import count_audit_logs, get_audit_logs, log_audit_event
from app.services.auth import (
    CACHE_KEY_ONWIKI_SUBPAGE,
    set_kill_switch,
    verify_website_admin,
)

ADMIN_ONWIKI_URL = "https://hi.wikipedia.org/w/api.php"
HINDI_MAINTENANCE_SNIPPET = "प्रणाली आपातकालीन स्थिति में रुकी हुई है"


def make_wiki_user_header(username: str) -> Dict[str, str]:
    """Safely encode header value for HTTPX to avoid Latin-1 encoding issues."""
    return {"X-Wiki-User": urllib.parse.quote(username)}


# ==============================================================================
# CHALLENGE 1: 100-Request Burst Audit Logging (SQLite WAL & Retry Resilience)
# ==============================================================================

@pytest.mark.asyncio
async def test_audit_log_100_burst_stress(test_db: None) -> None:
    """
    Adversarial Challenge: Fire 100 concurrent asynchronous log_audit_event writes
    in an unthrottled burst against SQLite WAL mode.
    Verifies:
    1. Zero 'database is locked' or 'database is busy' OperationalErrors.
    2. Zero unique constraint or schema errors.
    3. Exactly 100 audit records persisted into audit_logs.
    4. Each record has a valid pseudonymized SHA-256 IP hash and auto-assigned ID.
    """
    burst_size = 100

    async def write_event(idx: int) -> Any:
        return await log_audit_event(
            action=f"burst_action_{idx % 10}",
            username=f"burst_user_{idx}",
            ip_address=f"10.240.{(idx // 256)}.{idx % 256}",
            page_title=f"विकिपीडिया:परीक्षण_लेख_{idx}",
            diff_summary="अनामित संदर्भ सुधार",
            details={"index": idx, "thread": "asyncio_coroutine"},
        )

    tasks = [asyncio.create_task(write_event(i)) for i in range(burst_size)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Assert zero exceptions occurred across all 100 burst coroutines
    exceptions: List[BaseException] = [r for r in results if isinstance(r, BaseException)]
    assert not exceptions, (
        f"100-burst audit logging failed with {len(exceptions)} exceptions: {exceptions[:3]}"
    )

    # Verify database state consistency
    total_logs = await count_audit_logs()
    assert total_logs == burst_size, f"Expected {burst_size} logs in SQLite, found {total_logs}"

    # Verify retrieved entries structure and IP pseudonymization
    logs = await get_audit_logs(limit=burst_size)
    assert len(logs) == burst_size
    for entry in logs:
        assert entry.id is not None
        assert entry.timestamp is not None
        assert entry.ip_hash is not None
        # SHA-256 hex string is 64 characters
        assert len(entry.ip_hash) == 64
        assert entry.action.startswith("burst_action_")


# ==============================================================================
# CHALLENGE 2: 50-Burst Interleaved Audit Log Readers and Writers
# ==============================================================================

@pytest.mark.asyncio
async def test_audit_log_50_burst_concurrent_readers_and_writers(test_db: None) -> None:
    """
    Adversarial Challenge: Launch 50 concurrent writers alongside 50 concurrent
    readers (total 100 interleaved tasks) querying count_audit_logs and get_audit_logs.
    Verifies that SQLite WAL reader-writer non-blocking properties hold under load.
    """
    num_writers = 50
    num_readers = 50

    async def concurrent_writer(i: int) -> Any:
        return await log_audit_event(
            action="interleaved_50_burst",
            username=f"interleaved_user_{i}",
            ip_address=f"192.168.0.{i}",
            details={"seq": i},
        )

    async def concurrent_reader(i: int) -> tuple[int, int]:
        c = await count_audit_logs(action="interleaved_50_burst")
        entries = await get_audit_logs(limit=10, action="interleaved_50_burst")
        return c, len(entries)

    tasks: List[asyncio.Task[Any]] = []
    for i in range(max(num_writers, num_readers)):
        if i < num_writers:
            tasks.append(asyncio.create_task(concurrent_writer(i)))
        if i < num_readers:
            tasks.append(asyncio.create_task(concurrent_reader(i)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    exceptions: List[BaseException] = [r for r in results if isinstance(r, BaseException)]
    assert not exceptions, (
        f"Interleaved 50-burst reader/writer failed: {exceptions[:3]}"
    )

    # Verify exactly 50 interleaved writes succeeded
    final_count = await count_audit_logs(action="interleaved_50_burst")
    assert final_count == num_writers, f"Expected {num_writers} writes, counted {final_count}"


# ==============================================================================
# CHALLENGE 3: Cold-Cache Concurrent Fetch Race Condition
# ==============================================================================

@pytest.mark.asyncio
async def test_cold_cache_concurrent_admin_fetch_no_unique_constraint_failure(
    test_db: None,
) -> None:
    """
    Adversarial Challenge: 50 coroutines simultaneously call verify_website_admin()
    with cold cache. A network delay is introduced to maximize the window where multiple
    coroutines simultaneously attempt to UPSERT/INSERT the cache entry into cache_store.
    Verifies:
    1. Zero UNIQUE constraint failures on cache_store.key.
    2. All 50 coroutines receive True.
    3. Exactly 1 record is stored in cache_store for CACHE_KEY_ONWIKI_SUBPAGE.
    """
    coadmin = "HighConcurrencyAdmin"
    mock_payload = {
        "query": {
            "pages": [
                {
                    "pageid": 555,
                    "title": "सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
                    "revisions": [
                        {
                            "slots": {
                                "main": {
                                    "content": json.dumps({"admins": [coadmin]})
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    async def delayed_subpage_response(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.04)
        return httpx.Response(200, json=mock_payload)

    with respx.mock(assert_all_called=False) as router:
        router.get(ADMIN_ONWIKI_URL).mock(side_effect=delayed_subpage_response)

        concurrency = 50
        tasks = [
            asyncio.create_task(verify_website_admin(coadmin))
            for _ in range(concurrency)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        exceptions: List[BaseException] = [r for r in results if isinstance(r, BaseException)]
        assert not exceptions, f"Cold cache concurrent verification raised: {exceptions}"
        assert all(r is True for r in results), "Not all coroutines verified co-admin"

        # Assert exactly one cache record exists
        session_factory = get_session_maker()
        async with session_factory() as session:
            stmt = select(func.count(CacheStore.key)).where(
                CacheStore.key == CACHE_KEY_ONWIKI_SUBPAGE
            )
            count = (await session.execute(stmt)).scalar_one()
            assert count == 1, f"Expected 1 cache record, found {count}"


# ==============================================================================
# CHALLENGE 4: 503 Emergency Maintenance on /api/scan* (Retry-After & Hindi Payload)
# ==============================================================================

@pytest.mark.asyncio
async def test_kill_switch_scan_endpoints_strictly_return_503_retry_after_and_hindi_payload(
    async_client: httpx.AsyncClient,
) -> None:
    """
    Adversarial Challenge: Verify that when the emergency kill switch is active:
    1. Every endpoint starting with /api/scan (e.g. /api/scan, /api/scan/batch)
       strictly returns HTTP 503.
    2. Header 'Retry-After: 300' is strictly present in the response headers.
    3. The response body is a JSON object with:
       - status: 'error'
       - code: 503
       - message containing authentic Hindi text: 'प्रणाली आपातकालीन स्थिति में रुकी हुई है'
    4. When kill switch is toggled off, /api/scan immediately returns HTTP 200.
    """
    admin_headers = make_wiki_user_header("विक्रम प्रताप")

    # 1. Activate kill switch
    resp_on = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": True},
        headers=admin_headers,
    )
    assert resp_on.status_code == 200
    assert resp_on.json()["kill_switch"] is True

    # Test all variations of /api/scan* routes
    scan_test_paths = [
        "/api/scan",
        "/api/scan/",
        "/api/scan/batch",
        "/api/scan_article",
    ]

    for path in scan_test_paths:
        resp = await async_client.post(
            path,
            json={"wikitext": "लेख सामग्री <ref>https://example.org</ref>"},
        )
        assert resp.status_code == 503, (
            f"Path {path} did not return 503 under active kill-switch (got {resp.status_code})"
        )

        # Header assertion: Retry-After: 300
        retry_after = resp.headers.get("retry-after")
        assert retry_after == "300", (
            f"Path {path} missing or invalid 'Retry-After: 300' header (got: {retry_after!r})"
        )

        # Body assertions
        data = resp.json()
        assert data.get("status") == "error", f"Path {path} expected status 'error', got {data}"
        assert data.get("code") == 503, f"Path {path} expected code 503, got {data}"
        message = data.get("message", "")
        assert HINDI_MAINTENANCE_SNIPPET in message, (
            f"Path {path} response message missing Hindi snippet: {message!r}"
        )

    # 2. Deactivate kill switch
    resp_off = await async_client.post(
        "/api/admin/kill-switch",
        json={"active": False},
        headers=admin_headers,
    )
    assert resp_off.status_code == 200
    assert resp_off.json()["kill_switch"] is False

    # 3. Verify /api/scan immediately recovers to 200 OK
    resp_recovered = await async_client.post(
        "/api/scan",
        json={"wikitext": "सामान्य लेख सामग्री"},
    )
    assert resp_recovered.status_code == 200, (
        f"Expected 200 OK after kill-switch deactivated, got {resp_recovered.status_code}"
    )
    assert resp_recovered.json()["status"] == "success"


# ==============================================================================
# CHALLENGE 5: RBAC Stealth Precedence Over Kill Switch
# ==============================================================================

@pytest.mark.asyncio
async def test_kill_switch_active_admin_stealth_precedence(
    async_client: httpx.AsyncClient,
) -> None:
    """
    Adversarial Challenge: When the kill switch is active, unauthorized requests
    to /admin and /api/admin/* MUST return strict HTTP 404 (Not Found), NOT 503.
    This ensures administrative endpoints remain completely hidden even during outages.
    """
    await set_kill_switch(True, updated_by="test_admin")

    # Regular user attempting to access admin endpoints
    user_headers = {"X-Wiki-User": "RegularUser"}
    endpoints_to_test = [
        "/admin",
        "/admin/settings",
        "/api/admin/status",
        "/api/admin/audit-logs",
        "/api/admin/kill-switch",
    ]

    for ep in endpoints_to_test:
        resp = await async_client.get(ep, headers=user_headers)
        assert resp.status_code == 404, (
            f"Endpoint {ep} returned status {resp.status_code} instead of stealth 404!"
        )

    # Restore kill switch to False
    await set_kill_switch(False, updated_by="test_admin")


# ==============================================================================
# CHALLENGE 6: Concurrent HTTP Admin Requests (No User Table Locking)
# ==============================================================================

@pytest.mark.asyncio
async def test_50_concurrent_http_admin_requests_burst(
    async_client: httpx.AsyncClient,
) -> None:
    """
    Adversarial Challenge: Burst of 50 concurrent HTTP requests to /api/admin/status
    by the site owner Vikram Pratap to ensure user retrieval / session verification
    does not cause SQLite database lock timeouts under rapid HTTP bursts.
    """
    owner_headers = make_wiki_user_header("विक्रम प्रताप")

    tasks = [
        async_client.get("/api/admin/status", headers=owner_headers)
        for _ in range(50)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    exceptions: List[BaseException] = [r for r in responses if isinstance(r, BaseException)]
    assert not exceptions, f"Concurrent admin requests burst raised: {exceptions}"

    for i, resp in enumerate(responses):
        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 200, (
            f"Request {i} returned status {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["owner"] == "विक्रम प्रताप"
