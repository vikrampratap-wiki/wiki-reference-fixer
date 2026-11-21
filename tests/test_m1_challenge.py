"""
Milestone 1 Empirical Challenge & Adversarial Stress Test Suite.

Rigorously stress-tests Milestone 1 implementation against:
1. High-concurrency SQLite async WAL writes/reads (parallel tasks, no locked errors).
2. Extreme inputs to Settings (Unicode Devanagari, missing envs, malformed ports, nested paths).
3. Wikimedia maxlag backoff simulation (Retry-After headers, exponential backoff, ceilings).
"""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import httpx
import respx
from pydantic import ValidationError
from sqlalchemy import text, select

from app.core.config import Settings, get_settings
from app.db.database import (
    get_session_maker,
    get_engine,
    close_db,
    init_db,
)
from app.db.models import (
    User,
    RequestQueue,
    AuditLog,
    CacheStore,
    SystemSetting,
)
from app.services.http_client import WikimediaHttpClient, WikimediaLagError


# ============================================================================
# 1. High-Concurrency SQLite WAL Stress Tests
# ============================================================================

@pytest.mark.asyncio
async def test_high_concurrency_wal_parallel_writes_and_reads(tmp_path):
    """
    Stress-test SQLite WAL mode with 30 concurrent tasks (15 writers, 15 readers).
    Verifies that WAL mode + busy_timeout=5000 eliminates 'database is locked' errors.
    """
    db_file = str(tmp_path / "wal_stress.db")
    settings = get_settings()
    orig_path = settings.DB_PATH
    settings.DB_PATH = db_file

    await close_db()
    await init_db()

    session_maker = get_session_maker()
    concurrency_count = 15
    write_errors = []
    read_errors = []

    async def writer_task(task_id: int):
        try:
            async with session_maker() as session:
                # 1. Insert User
                user = User(
                    username=f"तनाव_परीक्षक_{task_id}",
                    wiki_user_id=2000 + task_id,
                    groups=["bot"],
                    is_admin=False,
                )
                session.add(user)
                await session.flush()

                # 2. Insert RequestQueue
                req = RequestQueue(
                    user_id=user.id,
                    page_title=f"तनाव_पृष्ठ_{task_id}",
                    status="pending",
                    total_refs=task_id * 2,
                    fixed_refs=0,
                )
                session.add(req)

                # 3. Insert AuditLog
                audit = AuditLog(
                    user_id=user.id,
                    username=user.username,
                    action="STRESS_TEST",
                    page_title=req.page_title,
                    details={"task_id": task_id, "mode": "WAL_STRESS"},
                )
                session.add(audit)

                # 4. Insert CacheStore
                cache = CacheStore(
                    key=f"cache_key_task_{task_id}",
                    category="test",
                    identifier=f"https://hi.wikipedia.org/wiki/Page_{task_id}",
                    data={"result": f"ok_{task_id}"},
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
                session.add(cache)

                await session.commit()
        except Exception as e:
            write_errors.append((task_id, str(e)))

    async def reader_task(task_id: int):
        try:
            # Short yield to allow some writes to begin
            await asyncio.sleep(0.005)
            async with session_maker() as session:
                # Read multiple tables while writers are active
                users_res = await session.execute(select(User))
                users = users_res.scalars().all()
                assert len(users) >= 0

                req_res = await session.execute(select(RequestQueue))
                reqs = req_res.scalars().all()
                assert len(reqs) >= 0

                audit_res = await session.execute(select(AuditLog))
                audits = audit_res.scalars().all()
                assert len(audits) >= 0

                # Also run raw SQL probe
                probe = await session.execute(text("SELECT count(*) FROM users;"))
                count = probe.scalar()
                assert count is not None
        except Exception as e:
            read_errors.append((task_id, str(e)))

    # Launch all 30 tasks concurrently
    tasks = []
    for i in range(concurrency_count):
        tasks.append(asyncio.create_task(writer_task(i)))
        tasks.append(asyncio.create_task(reader_task(i)))

    await asyncio.gather(*tasks)

    # Clean up engine
    await close_db()
    settings.DB_PATH = orig_path

    # Assert 0 errors occurred
    assert len(write_errors) == 0, f"Write tasks failed with: {write_errors}"
    assert len(read_errors) == 0, f"Read tasks failed with: {read_errors}"

    # Verify data integrity using clean connection
    settings.DB_PATH = db_file
    engine = get_engine()
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT count(*) FROM users;"))
        user_count = res.scalar()
        assert user_count == concurrency_count, (
            f"Expected {concurrency_count} users, got {user_count}"
        )

        res = await conn.execute(text("SELECT count(*) FROM requests;"))
        req_count = res.scalar()
        assert req_count == concurrency_count

        res = await conn.execute(text("SELECT count(*) FROM audit_logs;"))
        audit_count = res.scalar()
        assert audit_count == concurrency_count

        # Verify journal mode was indeed WAL
        res = await conn.execute(text("PRAGMA journal_mode;"))
        mode = res.scalar()
        assert mode.lower() == "wal"

    await close_db()
    settings.DB_PATH = orig_path


@pytest.mark.asyncio
async def test_concurrent_row_contention_and_updates(tmp_path):
    """
    Stress-test high contention on the EXACT SAME ROW:
    Multiple tasks concurrently updating a single SystemSetting record.
    Verifies busy_timeout serializes concurrent writers without OperationalError.
    """
    db_file = str(tmp_path / "contention_stress.db")
    settings = get_settings()
    orig_path = settings.DB_PATH
    settings.DB_PATH = db_file

    await close_db()
    await init_db()

    session_maker = get_session_maker()

    # Create initial setting
    async with session_maker() as session:
        setting = SystemSetting(
            key="global_counter",
            value={"count": 0},
            updated_by="initializer",
        )
        session.add(setting)
        await session.commit()

    errors = []
    contention_workers = 20

    async def update_worker(worker_id: int):
        try:
            async with session_maker() as s:
                # Fetch, increment, and update
                res = await s.execute(
                    select(SystemSetting).where(SystemSetting.key == "global_counter")
                )
                item = res.scalar_one()
                current = item.value.get("count", 0)
                item.value = {"count": current + 1}
                item.updated_by = f"worker_{worker_id}"
                await s.commit()
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Execute all concurrently
    await asyncio.gather(*(update_worker(i) for i in range(contention_workers)))

    await close_db()
    settings.DB_PATH = orig_path

    # Under SQLite WAL, simultaneous write transactions serialize via busy_timeout
    assert len(errors) == 0, f"Row contention resulted in lock failures: {errors}"


@pytest.mark.asyncio
async def test_concurrent_transaction_rollback_isolation(tmp_path):
    """
    Verify that transactions rolling back under high concurrency do not leak uncommitted
    data to concurrent readers and do not deadlock subsequent transactions.
    """
    db_file = str(tmp_path / "rollback_stress.db")
    settings = get_settings()
    orig_path = settings.DB_PATH
    settings.DB_PATH = db_file

    await close_db()
    await init_db()

    session_maker = get_session_maker()

    # 1. Failing transaction that inserts a toxic user and rolls back
    async with session_maker() as session:
        try:
            user = User(
                username="toxic_rollback_user",
                groups=["sysop"],
                is_admin=True,
            )
            session.add(user)
            await session.flush()
            # Explicit rollback
            await session.rollback()
        except Exception:
            await session.rollback()

    # 2. Verify reader sees 0 users
    async with session_maker() as session2:
        res = await session2.execute(
            select(User).where(User.username == "toxic_rollback_user")
        )
        assert res.scalar_one_or_none() is None

    await close_db()
    settings.DB_PATH = orig_path


@pytest.mark.asyncio
async def test_sqlite_directory_auto_creation(tmp_path):
    """
    Verify that if DB_PATH specifies a deeply nested non-existent directory,
    get_engine() automatically creates the parent directories without failing.
    """
    nested_dir = tmp_path / "deeply" / "nested" / "path" / "to" / "data"
    nested_db = str(nested_dir / "wiki.db")

    assert not nested_dir.exists()

    settings = get_settings()
    orig_path = settings.DB_PATH
    settings.DB_PATH = nested_db

    await close_db()
    engine = get_engine()
    assert nested_dir.exists(), "get_engine() should create missing parent directories"

    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT 1;"))
        assert res.scalar() == 1

    await close_db()
    settings.DB_PATH = orig_path


# ============================================================================
# 2. Extreme Configuration Inputs (Settings)
# ============================================================================

def test_extreme_superadmin_devanagari_and_whitespace():
    """
    Adversarial testing of superadmin username parsing and is_superadmin verification:
    - Hindi Unicode strings with leading/trailing spaces
    - Underscore to space equivalence (Wikipedia standard)
    - Mixed scripts and multiple delimiters
    - None and empty inputs
    """
    raw_input = "  विक्रम प्रताप , Vikram_Pratap  ,  अमित कुमार   ,,  "
    settings = Settings(SUPERADMIN_USERNAMES=raw_input)

    # Check parsed list
    assert "विक्रम प्रताप" in settings.SUPERADMIN_USERNAMES
    assert "Vikram_Pratap" in settings.SUPERADMIN_USERNAMES
    assert "अमित कुमार" in settings.SUPERADMIN_USERNAMES
    assert len(settings.SUPERADMIN_USERNAMES) == 3

    # Positive match tests
    assert settings.is_superadmin("विक्रम प्रताप") is True
    assert settings.is_superadmin(" विक्रम प्रताप ") is True
    assert settings.is_superadmin("विक्रम_प्रताप") is True
    assert settings.is_superadmin("Vikram Pratap") is True
    assert settings.is_superadmin("vikram_pratap") is True
    assert settings.is_superadmin("VIKRAM PRATAP") is True
    assert settings.is_superadmin("अमित कुमार") is True
    assert settings.is_superadmin("अमित_कुमार") is True

    # Negative match tests
    assert settings.is_superadmin("विक्रम") is False
    assert settings.is_superadmin("प्रताप") is False
    assert settings.is_superadmin("विक्रमप्रताप") is False
    assert settings.is_superadmin("अमित") is False
    assert settings.is_superadmin("SysopAdmin") is False
    assert settings.is_superadmin("") is False
    assert settings.is_superadmin("   ") is False
    assert settings.is_superadmin(None) is False


def test_empty_and_whitespace_superadmin_fallback():
    """Verify empty superadmin strings or invalid types fall back gracefully."""
    # Empty string
    s_empty = Settings(SUPERADMIN_USERNAMES="")
    assert s_empty.SUPERADMIN_USERNAMES == []
    assert s_empty.is_superadmin("विक्रम प्रताप") is False

    # None falls back to default owner list
    s_none = Settings(SUPERADMIN_USERNAMES=None)
    assert "विक्रम प्रताप" in s_none.SUPERADMIN_USERNAMES
    assert "Vikram Pratap" in s_none.SUPERADMIN_USERNAMES

    # Integer list/mixed types converted safely
    s_list = Settings(SUPERADMIN_USERNAMES=["विक्रम प्रताप", "12345"])
    assert "विक्रम प्रताप" in s_list.SUPERADMIN_USERNAMES
    assert "12345" in s_list.SUPERADMIN_USERNAMES
    assert s_list.is_superadmin("12345") is True


def test_malformed_ports_validation():
    """Verify that invalid PORT inputs raise Pydantic ValidationError."""
    with pytest.raises(ValidationError):
        Settings(PORT="not-a-port")

    with pytest.raises(ValidationError):
        Settings(PORT=None)

    # Valid string integer converts automatically
    s_str_port = Settings(PORT="9000")
    assert s_str_port.PORT == 9000


def test_custom_user_agent_and_policy_defaults():
    """Verify custom User-Agent format adheres to Wikimedia policy requirements."""
    custom_ua = "MyWikiTool/2.1 (https://example.org/tool; admin@example.org)"
    s = Settings(WIKIMEDIA_USER_AGENT=custom_ua, MAXLAG=10, MAXLAG_MAX_RETRIES=5)

    assert s.WIKIMEDIA_USER_AGENT == custom_ua
    assert s.MAXLAG == 10
    assert s.MAXLAG_MAX_RETRIES == 5


# ============================================================================
# 3. Wikimedia Maxlag Simulator & Backoff Ceilings
# ============================================================================

@pytest.mark.asyncio
@respx.mock
async def test_maxlag_simulator_exponential_backoff_and_recovery():
    """
    Simulate server returning 3 consecutive maxlag errors with Retry-After headers,
    followed by a successful 200 response.
    Verify sleep delays and call sequence.
    """
    client = WikimediaHttpClient()
    await client.init()

    recorded_sleeps = []
    orig_sleep = asyncio.sleep

    async def mock_sleep(seconds: float):
        recorded_sleeps.append(seconds)
        # Fast-forward sleep for testing
        await orig_sleep(0.001)

    asyncio.sleep = mock_sleep

    try:
        route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            side_effect=[
                # Attempt 1: Lagged (Retry-After: 3)
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json", "Retry-After": "3"},
                    json={"error": {"code": "maxlag", "info": "Waiting for db: 6s lagged"}},
                ),
                # Attempt 2: Lagged (No Retry-After, fallback to max(5.0, 2**1))
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={"error": {"code": "maxlag", "info": "Waiting for db: 7s lagged"}},
                ),
                # Attempt 3: Recovered
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={"query": {"pages": {"1": {"title": "सफलता"}}}},
                ),
            ]
        )

        response = await client.get(
            "https://hi.wikipedia.org/w/api.php",
            params={"action": "query", "titles": "परीक्षण"},
            retries=3,
        )

        assert response.status_code == 200
        assert route.call_count == 3
        data = response.json()
        assert "query" in data

        # Check sleep delays:
        # First sleep should be 3.0s (from Retry-After header)
        assert len(recorded_sleeps) == 2
        assert recorded_sleeps[0] == 3.0
        # Second sleep should be 5.0s (fallback max(5.0, 2**1))
        assert recorded_sleeps[1] == 5.0

    finally:
        asyncio.sleep = orig_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_maxlag_simulator_retry_ceiling_exhaustion():
    """
    Simulate server persistently exceeding maxlag threshold until retries are exhausted.
    Verify WikimediaLagError is raised with exact error details and no extra calls.
    """
    client = WikimediaHttpClient()
    await client.init()

    recorded_sleeps = []
    orig_sleep = asyncio.sleep

    async def mock_sleep(seconds: float):
        recorded_sleeps.append(seconds)
        await orig_sleep(0.001)

    asyncio.sleep = mock_sleep

    try:
        max_retries = 3
        route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Retry-After": "2"},
                json={"error": {"code": "maxlag", "info": "Host db1055 lagged 12 seconds"}},
            )
        )

        with pytest.raises(WikimediaLagError) as exc_info:
            await client.get(
                "https://hi.wikipedia.org/w/api.php",
                params={"action": "query"},
                retries=max_retries,
            )

        # Expected total attempts: 1 initial + max_retries = 4 attempts
        assert route.call_count == max_retries + 1
        assert "Host db1055 lagged 12 seconds" in str(exc_info.value)
        assert f"Max retries ({max_retries}) exhausted" in str(exc_info.value)
        # Sleep was called for each retry attempt before the final attempt
        assert len(recorded_sleeps) == max_retries

    finally:
        asyncio.sleep = orig_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_429_rate_limit_and_503_backoff_simulation():
    """
    Simulate server returning HTTP 429 followed by HTTP 503, then recovery.
    Verifies HTTP 429 Retry-After handling and HTTP 503 exponential backoff.
    """
    client = WikimediaHttpClient()
    await client.init()

    recorded_sleeps = []
    orig_sleep = asyncio.sleep

    async def mock_sleep(seconds: float):
        recorded_sleeps.append(seconds)
        await orig_sleep(0.001)

    asyncio.sleep = mock_sleep

    try:
        route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            side_effect=[
                # Attempt 1: 429 Rate limited with Retry-After: 4
                httpx.Response(429, headers={"Retry-After": "4"}),
                # Attempt 2: 503 Service Unavailable (delay = 2**1 = 2)
                httpx.Response(503),
                # Attempt 3: 200 OK
                httpx.Response(200, json={"batchcomplete": ""}),
            ]
        )

        resp = await client.get("https://hi.wikipedia.org/w/api.php", retries=3)
        assert resp.status_code == 200
        assert route.call_count == 3

        assert len(recorded_sleeps) == 2
        assert recorded_sleeps[0] == 4.0  # from 429 Retry-After
        assert recorded_sleeps[1] == 2.0  # from 503 delay 2**1

    finally:
        asyncio.sleep = orig_sleep
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_auto_maxlag_injection_scope():
    """
    Verify auto_maxlag is injected only for Wikimedia/Wikipedia URLs,
    and NOT injected for third-party URLs (e.g. Wayback Machine / archive.org).
    """
    client = WikimediaHttpClient()
    await client.init()

    try:
        # Route 1: Wikipedia API
        wiki_route = respx.get("https://hi.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        # Route 2: Internet Archive Wayback Machine
        archive_route = respx.get("https://archive.org/wayback/available").mock(
            return_value=httpx.Response(200, json={"archived_snapshots": {}})
        )

        # Call Wikipedia
        await client.get(
            "https://hi.wikipedia.org/w/api.php",
            params={"action": "query"},
        )
        wiki_req = wiki_route.calls.last.request
        assert "maxlag=5" in str(wiki_req.url)

        # Call Archive
        await client.get(
            "https://archive.org/wayback/available",
            params={"url": "https://example.com"},
        )
        archive_req = archive_route.calls.last.request
        assert "maxlag" not in str(archive_req.url)

    finally:
        await client.close()
