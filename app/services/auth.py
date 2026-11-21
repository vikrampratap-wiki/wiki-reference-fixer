"""
Website Admin Verification and Authentication Engine.
Implements Wikipedia-native Website Admin RBAC:
1. Website Owner / Superadmin check: Vikram Pratap (विक्रम प्रताप).
2. Dynamic On-Wiki Co-Admin Subpage: सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json
   fetched via MediaWiki Action API with 10-minute caching in SQLite cache_store.
3. Emergency superadmin fallback from .env (SUPERADMIN_USERNAMES).
4. Strict separation: Wikipedia sysops/bureaucrats are NOT website admins unless
   explicitly listed on Vikram Pratap's personal subpage or in .env.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import random
import sqlite3
from typing import Any, Dict, List, Optional
import urllib.parse

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_session_token
from app.db.database import get_db, get_session_maker
from app.db.models import CacheStore, SystemSetting, User
from app.services.http_client import http_client

logger = logging.getLogger(__name__)

CACHE_KEY_ONWIKI_SUBPAGE = "admin_onwiki_subpage"
CACHE_CATEGORY_ADMIN_CONFIG = "admin_config"

# Lock to coalesce concurrent cold-cache MediaWiki subpage fetches (prevents thundering herd)
_subpage_fetch_lock: asyncio.Lock = asyncio.Lock()


def sanitize_admin_usernames(raw: Any) -> List[str]:
    """
    Strictly sanitize raw on-wiki admin payloads into a list of non-empty strings.
    Explicitly filters out:
    - None/null, booleans, integers, floats, dicts, lists
    - Empty strings and whitespace-only strings
    - String literals 'None' and 'null' (prevents type-confusion privilege escalation)
    """
    if not isinstance(raw, list):
        return []
    sanitized: List[str] = []
    for item in raw:
        if isinstance(item, str) and not isinstance(item, bool):
            cleaned = item.strip()
            if cleaned and cleaned.lower() not in ("none", "null"):
                sanitized.append(cleaned)
    return sanitized


def normalize_username(username: Optional[str]) -> str:
    """
    Normalize Wikipedia username:
    - Strips leading/trailing whitespace
    - Replaces underscores with spaces (standard MediaWiki canonicalization)
    - Normalizes internal multiple spaces into single space
    - Case-insensitive comparison via lower()
    """
    if not username or not isinstance(username, str):
        return ""
    norm = username.strip().replace("_", " ")
    norm = " ".join(norm.split())
    return norm.lower()


def is_owner_or_superadmin(username: Optional[str]) -> bool:
    """
    Check if the user is Vikram Pratap (site owner) or in SUPERADMIN_USERNAMES.
    """
    if not username:
        return False
    norm = normalize_username(username)
    # Direct site owner check (Hindi and English variants)
    if norm in ("विक्रम प्रताप", "vikram pratap"):
        return True
    settings = get_settings()
    return settings.is_superadmin(username)


async def fetch_onwiki_admins(
    force_refresh: bool = False,
    session: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """
    Fetch authorized co-admins from Vikram Pratap's Wikipedia subpage:
    सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json
    Caches results in SQLite cache_store for 10 minutes (600 seconds).
    Since only Vikram Pratap can edit their personal user namespace subpage,
    this provides a tamper-proof dynamic on-wiki admin management mechanism.
    """
    settings = get_settings()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _get_cached_record(s: AsyncSession) -> Optional[CacheStore]:
        stmt = select(CacheStore).where(CacheStore.key == CACHE_KEY_ONWIKI_SUBPAGE)
        result = await s.execute(stmt)
        return result.scalar_one_or_none()

    # Step 1: Fast-path check SQLite CacheStore if not force_refresh
    cached: Optional[CacheStore] = None
    if not force_refresh:
        if session is not None:
            cached = await _get_cached_record(session)
        else:
            session_factory = get_session_maker()
            async with session_factory() as local_session:
                cached = await _get_cached_record(local_session)

        if cached is not None and cached.expires_at > now_utc:
            logger.debug("Loaded on-wiki co-admins from SQLite cache.")
            cached_data = cached.data if isinstance(cached.data, dict) else {}
            return {"admins": sanitize_admin_usernames(cached_data.get("admins"))}

    # Step 2: Coalesced fetch from MediaWiki Action API under async lock
    global _subpage_fetch_lock
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if getattr(_subpage_fetch_lock, "_loop", None) not in (None, current_loop):
        _subpage_fetch_lock = asyncio.Lock()

    async with _subpage_fetch_lock:
        # Double-check cache inside lock (in case another coroutine just populated it)
        if not force_refresh:
            if session is not None:
                cached = await _get_cached_record(session)
            else:
                session_factory = get_session_maker()
                async with session_factory() as local_session:
                    cached = await _get_cached_record(local_session)

            if cached is not None and cached.expires_at > now_utc:
                cached_data = cached.data if isinstance(cached.data, dict) else {}
                return {"admins": sanitize_admin_usernames(cached_data.get("admins"))}

        logger.info("Fetching on-wiki co-admin subpage: %s", settings.ADMIN_ONWIKI_PAGE)
        params = {
            "action": "query",
            "prop": "revisions",
            "titles": settings.ADMIN_ONWIKI_PAGE,
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
            "formatversion": "2",
        }

        parsed_json: Dict[str, Any] = {"admins": []}
        fetch_success = False

        try:
            response = await http_client.get(settings.MEDIAWIKI_API_URL, params=params)
            if response.status_code == 200:
                payload = response.json()
                raw_content: Optional[str] = None

                # Handle formatversion 2
                query = payload.get("query", {})
                pages = query.get("pages", [])
                if isinstance(pages, list) and len(pages) > 0:
                    page = pages[0]
                    if not page.get("missing"):
                        revs = page.get("revisions", [])
                        if revs and isinstance(revs, list):
                            rev0 = revs[0]
                            slots = rev0.get("slots", {})
                            if "main" in slots:
                                raw_content = slots["main"].get("content")
                            elif "*" in rev0:
                                raw_content = rev0.get("*")
                # Handle formatversion 1 fallback
                elif isinstance(pages, dict):
                    for _, page in pages.items():
                        if not page.get("missing"):
                            revs = page.get("revisions", [])
                            if revs and isinstance(revs, list):
                                raw_content = revs[0].get("*")

                if raw_content:
                    # Strip wikitext wrappers if any (e.g. <syntaxhighlight>)
                    cleaned = raw_content.strip()
                    if (
                        cleaned.startswith("<syntaxhighlight")
                        and cleaned.endswith("</syntaxhighlight>")
                    ):
                        cleaned = cleaned.split(">", 1)[1].rsplit("<", 1)[0].strip()
                    parsed_data = json.loads(cleaned)
                    if isinstance(parsed_data, dict):
                        # Strict schema sanitization: ensure admins is list of valid strings
                        sanitized_admins = sanitize_admin_usernames(parsed_data.get("admins"))
                        parsed_json = {"admins": sanitized_admins}
                        fetch_success = True
        except Exception as exc:
            logger.warning(
                "Failed to fetch on-wiki subpage from MediaWiki API (%s). Falling back.",
                exc,
            )

        # Step 3: Update SQLite CacheStore using atomic UPSERT
        if fetch_success:
            expires_at = now_utc + timedelta(seconds=settings.ADMIN_SUBPAGE_CACHE_TTL_SECONDS)

            async def _save_cache(s: AsyncSession) -> None:
                insert_stmt = sqlite_insert(CacheStore).values(
                    key=CACHE_KEY_ONWIKI_SUBPAGE,
                    category=CACHE_CATEGORY_ADMIN_CONFIG,
                    identifier=settings.ADMIN_ONWIKI_PAGE,
                    data=parsed_json,
                    created_at=now_utc,
                    expires_at=expires_at,
                )
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[CacheStore.key],
                    set_={
                        "data": insert_stmt.excluded.data,
                        "expires_at": insert_stmt.excluded.expires_at,
                        "created_at": insert_stmt.excluded.created_at,
                    },
                )
                await s.execute(upsert_stmt)

            if session is not None:
                await _save_cache(session)
                await session.flush()
            else:
                session_factory = get_session_maker()
                async with session_factory() as local_session:
                    async with local_session.begin():
                        await _save_cache(local_session)
            return parsed_json

    # Step 4: Fallback to existing (even if expired) cache if available
    if cached is not None:
        logger.warning("Using expired cache for on-wiki admin subpage due to fetch failure.")
        cached_data = cached.data if isinstance(cached.data, dict) else {}
        return {"admins": sanitize_admin_usernames(cached_data.get("admins"))}

    return {"admins": []}


async def verify_website_admin(
    username: Optional[str],
    session: Optional[AsyncSession] = None,
) -> bool:
    """
    Authoritative verification of website administrator status:
    1. Owner / Superadmin check: Vikram Pratap (विक्रम प्रताप) or SUPERADMIN_USERNAMES.
    2. Dynamic Co-Admin check: listed on Vikram Pratap's personal subpage
       (सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json).
    3. Normal Wikipedia sysops/bureaucrats return FALSE unless listed above.
    """
    if not username:
        return False

    norm = normalize_username(username)
    if not norm:
        return False

    # Check 1: Owner / Superadmin
    if is_owner_or_superadmin(username):
        return True

    # Check 2: Dynamic Co-Admin Subpage
    admin_config = await fetch_onwiki_admins(session=session)
    coadmins = admin_config.get("admins", []) if isinstance(admin_config, dict) else []
    if isinstance(coadmins, list):
        for ca in coadmins:
            # Strictly verify string type; never cast arbitrary types using str()
            if isinstance(ca, str) and not isinstance(ca, bool):
                clean_ca = ca.strip()
                if clean_ca and normalize_username(clean_ca) == norm:
                    return True

    return False


async def is_kill_switch_active(session: Optional[AsyncSession] = None) -> bool:
    """Check if emergency kill-switch is active in system_settings."""
    async def _check(s: AsyncSession) -> bool:
        stmt = select(SystemSetting).where(SystemSetting.key == "kill_switch")
        result = await s.execute(stmt)
        record = result.scalar_one_or_none()
        if record and isinstance(record.value, dict):
            return bool(record.value.get("active", False))
        return False

    if session is not None:
        return await _check(session)

    session_factory = get_session_maker()
    async with session_factory() as local_session:
        return await _check(local_session)


async def set_kill_switch(
    active: bool,
    updated_by: str = "system",
    session: Optional[AsyncSession] = None,
) -> bool:
    """Set emergency kill-switch status in system_settings."""
    async def _set(s: AsyncSession) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == "kill_switch")
        result = await s.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.value = {"active": active}
            record.updated_by = updated_by
        else:
            new_setting = SystemSetting(
                key="kill_switch",
                value={"active": active},
                description="Emergency API halt kill-switch",
                updated_by=updated_by,
            )
            s.add(new_setting)

    if session is not None:
        await _set(session)
        await session.flush()
    else:
        session_factory = get_session_maker()
        async with session_factory() as local_session:
            async with local_session.begin():
                await _set(local_session)

    return active


async def get_rate_limit(session: Optional[AsyncSession] = None) -> int:
    """Retrieve configured rate limit per minute from system_settings (default 30)."""
    async def _get(s: AsyncSession) -> int:
        stmt = select(SystemSetting).where(SystemSetting.key == "rate_limit")
        result = await s.execute(stmt)
        record = result.scalar_one_or_none()
        if record and isinstance(record.value, dict):
            return int(record.value.get("rate_limit_per_minute", 30))
        return 30

    if session is not None:
        return await _get(session)

    session_factory = get_session_maker()
    async with session_factory() as local_session:
        return await _get(local_session)


async def set_rate_limit(
    limit: int,
    updated_by: str = "system",
    session: Optional[AsyncSession] = None,
) -> int:
    """Update configured rate limit per minute in system_settings."""
    async def _set(s: AsyncSession) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == "rate_limit")
        result = await s.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.value = {"rate_limit_per_minute": limit}
            record.updated_by = updated_by
        else:
            new_setting = SystemSetting(
                key="rate_limit",
                value={"rate_limit_per_minute": limit},
                description="Rate limit requests per minute",
                updated_by=updated_by,
            )
            s.add(new_setting)

    if session is not None:
        await _set(session)
        await session.flush()
    else:
        session_factory = get_session_maker()
        async with session_factory() as local_session:
            async with local_session.begin():
                await _set(local_session)

    return limit


async def get_or_create_user(
    username: str,
    wiki_user_id: Optional[int] = None,
    groups: Optional[List[str]] = None,
    session: Optional[AsyncSession] = None,
) -> User:
    """
    Fetch existing user or create a new user record in SQLite users table.
    Uses a read-before-write fast path for high-throughput concurrency, and
    SQLite native ON CONFLICT atomic UPSERT with exponential backoff & jitter
    retry on database locks.
    """
    # 1. Authoritative admin verification performed outside DB write transaction
    is_admin = await verify_website_admin(username, session=session)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 2. Atomic UPSERT definition
    async def _upsert(s: AsyncSession) -> User:
        insert_stmt = sqlite_insert(User).values(
            username=username,
            wiki_user_id=wiki_user_id,
            groups=groups or [],
            is_admin=is_admin,
            is_blocked=False,
            last_login_at=now_utc,
            created_at=now_utc,
        )
        set_dict: Dict[str, Any] = {
            "is_admin": is_admin,
            "last_login_at": now_utc,
        }
        if wiki_user_id is not None:
            set_dict["wiki_user_id"] = wiki_user_id
        if groups is not None:
            set_dict["groups"] = groups

        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[User.username],
            set_=set_dict,
        ).returning(User)

        result = await s.execute(upsert_stmt)
        return result.scalar_one()

    # 3. Read fast-path helper and freshness check
    async def _read_user(s: AsyncSession) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        result = await s.execute(stmt)
        return result.scalar_one_or_none()

    def _is_up_to_date(existing: User) -> bool:
        if existing.is_admin != is_admin:
            return False
        if wiki_user_id is not None and existing.wiki_user_id != wiki_user_id:
            return False
        if groups is not None and existing.groups != groups:
            return False
        if existing.last_login_at is None:
            return False
        if (now_utc - existing.last_login_at).total_seconds() > 60:
            return False
        return True

    # If session provided by caller, execute within caller's session with fallback & retry
    if session is not None:
        existing = await _read_user(session)
        if existing is not None and _is_up_to_date(existing):
            return existing

        max_retries = 10
        for attempt in range(max_retries):
            try:
                user = await _upsert(session)
                await session.flush()
                return user
            except (SAOperationalError, sqlite3.OperationalError) as exc:
                err_msg = str(exc).lower()
                if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                    await session.rollback()
                    backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.04)
                    await asyncio.sleep(backoff)
                    existing = await _read_user(session)
                    if existing is not None and _is_up_to_date(existing):
                        return existing
                    continue
                raise
            except IntegrityError:
                await session.rollback()
                existing = await _read_user(session)
                if existing is not None:
                    return existing
                if attempt < max_retries - 1:
                    backoff = 0.01 + random.uniform(0.01, 0.03)
                    await asyncio.sleep(backoff)
                    continue
                raise

    # Otherwise open isolated local session with retry loop on locks and integrity conflicts
    session_factory = get_session_maker()
    max_retries = 10
    for attempt in range(max_retries):
        # 1. Read-before-write fast path in isolated read session
        try:
            async with session_factory() as read_session:
                existing = await _read_user(read_session)
                if existing is not None and _is_up_to_date(existing):
                    return existing
        except (SAOperationalError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.04)
                await asyncio.sleep(backoff)
                continue
            raise

        # 2. Write path (isolated transaction)
        try:
            async with session_factory() as local_session:
                async with local_session.begin():
                    return await _upsert(local_session)
        except (SAOperationalError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.04)
                await asyncio.sleep(backoff)
                continue
            raise
        except IntegrityError:
            try:
                async with session_factory() as read_session:
                    existing = await _read_user(read_session)
                    if existing is not None:
                        return existing
            except Exception:
                pass
            if attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.04)
                await asyncio.sleep(backoff)
                continue
            raise

    async with session_factory() as fallback_session:
        existing = await _read_user(fallback_session)
        if existing is not None:
            return existing
    raise RuntimeError(f"Failed to get or create user '{username}' after {max_retries} retries")


async def get_current_user_from_request(
    request: Request,
    session: Optional[AsyncSession] = None,
) -> Optional[User]:
    """
    Extract authenticated user from request:
    1. Headers: 'X-Wiki-User' (test clients & Toolforge gateway)
    2. Session Cookie: 'session' (cryptographically signed session token)
    3. Authorization header: 'Bearer <signed_token>'
    NOTE: Unsigned plaintext 'username' cookie is strictly disallowed for authentication.
    """
    raw_user: Optional[str] = None
    req_state = getattr(request, "state", None)
    if req_state is not None:
        cached_user = getattr(req_state, "user", None)
        if cached_user is not None and isinstance(cached_user, User):
            return cached_user

    # 1. Header lookup
    header_user = request.headers.get("X-Wiki-User")
    if header_user:
        raw_user = urllib.parse.unquote(header_user)

    # 2. Signed session token from cookie
    if not raw_user:
        session_cookie = request.cookies.get("session")
        if session_cookie:
            session_data = decode_session_token(session_cookie)
            if session_data and isinstance(session_data, dict):
                raw_user = session_data.get("username")

    # 3. Bearer token in Authorization header
    if not raw_user:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            data = decode_session_token(token)
            if data and isinstance(data, dict):
                raw_user = data.get("username")

    if not raw_user:
        return None

    clean_username = raw_user.strip()
    if not clean_username:
        return None

    user = await get_or_create_user(clean_username, session=session)
    if req_state is not None and user is not None:
        req_state.user = user
    return user


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency enforcing strict website administrator access.
    If requester is NOT an authorized website admin, raises HTTP 404 (Not Found).
    Never raises 401 or 403 to prevent endpoint enumeration (cloaking).
    """
    user = await get_current_user_from_request(request, session=db)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=404,
            detail="पृष्ठ उपलब्ध नहीं है (404 Not Found)",
        )
    return user
