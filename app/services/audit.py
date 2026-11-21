"""
System Audit Logging Service.
Records security events, scans, administrative actions, and configuration changes
into the SQLite audit_logs table, adhering to Wikimedia privacy policies with IP hashing.
"""

import asyncio
import random
import sqlite3
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_ip_address
from app.db.database import get_session_maker
from app.db.models import AuditLog


async def log_audit_event(
    action: str,
    username: str = "anonymous",
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    page_title: Optional[str] = None,
    diff_summary: Optional[str] = None,
    user_id: Optional[int] = None,
    session: Optional[AsyncSession] = None,
    event_type: Optional[str] = None,
) -> AuditLog:
    """
    Append an audit log record into SQLite audit_logs table.
    Supports both 'action' and 'event_type' parameter names for interface compatibility.
    Hashes the client IP address to prevent persisting raw PII.
    Handles SQLite concurrency bursts with retry and exponential backoff.
    """
    effective_action = event_type if event_type else action
    clean_username = username.strip() if username else "anonymous"
    ip_hash = hash_ip_address(ip_address) if ip_address else None
    log_details = details if details is not None else {}

    audit_entry = AuditLog(
        action=effective_action,
        username=clean_username,
        user_id=user_id,
        ip_hash=ip_hash,
        page_title=page_title,
        diff_summary=diff_summary,
        details=log_details,
    )

    if session is not None:
        session.add(audit_entry)
        await session.flush()
        return audit_entry

    session_factory = get_session_maker()
    max_retries = 10
    for attempt in range(max_retries):
        try:
            async with session_factory() as local_session:
                async with local_session.begin():
                    local_session.add(audit_entry)
                return audit_entry
        except (SAOperationalError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.04)
                await asyncio.sleep(backoff)
                audit_entry = AuditLog(
                    action=effective_action,
                    username=clean_username,
                    user_id=user_id,
                    ip_hash=ip_hash,
                    page_title=page_title,
                    diff_summary=diff_summary,
                    details=log_details,
                )
                continue
            raise
    return audit_entry


async def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
    action: Optional[str] = None,
    username: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> List[AuditLog]:
    """
    Retrieve paginated audit log entries ordered by timestamp descending.
    """
    async def _query(s: AsyncSession) -> List[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc())
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if username:
            stmt = stmt.where(AuditLog.username == username)
        stmt = stmt.limit(limit).offset(offset)
        result = await s.execute(stmt)
        return list(result.scalars().all())

    if session is not None:
        return await _query(session)

    session_factory = get_session_maker()
    max_retries = 10
    for attempt in range(max_retries):
        try:
            async with session_factory() as local_session:
                return await _query(local_session)
        except (SAOperationalError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.03)
                await asyncio.sleep(backoff)
                continue
            raise
    return []


async def count_audit_logs(
    action: Optional[str] = None,
    username: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> int:
    """
    Return the total count of audit logs matching optional criteria.
    """
    async def _count(s: AsyncSession) -> int:
        stmt = select(func.count(AuditLog.id))
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if username:
            stmt = stmt.where(AuditLog.username == username)
        result = await s.execute(stmt)
        return int(result.scalar_one() or 0)

    if session is not None:
        return await _count(session)

    session_factory = get_session_maker()
    max_retries = 10
    for attempt in range(max_retries):
        try:
            async with session_factory() as local_session:
                return await _count(local_session)
        except (SAOperationalError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                backoff = 0.02 * (1.5 ** attempt) + random.uniform(0.01, 0.03)
                await asyncio.sleep(backoff)
                continue
            raise
    return 0
