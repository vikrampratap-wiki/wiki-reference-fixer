"""
Administrator API and Confidential Management Endpoints.
Guarded by strict 404 RBAC security: non-administrators receive HTTP 404 (Not Found).
Provides emergency kill-switch controls, system audit log queries, rate limiter configuration,
rule management (whitelist/blacklist), request queue inspection, and admin status.
"""

from datetime import datetime
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.database import get_db
from app.db.models import (
    RequestQueue,
    User,
    WhitelistBlacklistRule,
)
from app.services.audit import count_audit_logs, get_audit_logs, log_audit_event
from app.services.auth import (
    fetch_onwiki_admins,
    get_rate_limit,
    is_kill_switch_active,
    require_admin,
    set_kill_switch,
    set_rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin API"])
admin_page_router = APIRouter(prefix="/admin", tags=["Admin Page"])


class KillSwitchRequest(BaseModel):
    active: Optional[bool] = Field(
        default=None,
        description=(
            "Explicit kill-switch state (True=halt, False=operate). "
            "If None, toggles current state."
        ),
    )


class RateLimitRequest(BaseModel):
    rate_limit_per_minute: int = Field(
        ...,
        ge=1,
        le=1000,
        description="Maximum requests per minute allowed by the system",
    )


class RuleCreateRequest(BaseModel):
    rule_type: str = Field(..., pattern="^(whitelist|blacklist)$", description="Type of rule")
    scope: str = Field(..., pattern="^(domain|page|user)$", description="Target scope of rule")
    pattern: str = Field(..., min_length=1, max_length=512, description="Target matching pattern")
    reason: str = Field(..., min_length=1, description="Rationale for rule creation")


# --- Confidential Admin HTML Dashboard ---
@admin_page_router.get("", response_class=HTMLResponse, summary="Hidden Admin Dashboard HTML")
@admin_page_router.get(
    "/{subpath:path}",
    response_class=HTMLResponse,
    summary="Hidden Admin Dashboard Subpaths",
)
async def get_admin_page(
    subpath: str = "",
    user: User = Depends(require_admin),
) -> HTMLResponse:
    """
    Renders the confidential administrator dashboard for verified website administrators.
    Non-administrators strictly receive HTTP 404 (handled by middleware and require_admin).
    """
    html_content = f"""<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>एडमिन पैनल</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.68;
            margin: 2rem;
        }}
        h1 {{ color: #1a1a1a; }}
        .badge {{ background: #2e7d32; color: #fff; padding: 0.25rem 0.5rem; border-radius: 4px; }}
    </style>
</head>
<body>
    <h1>विकिपीडिया सन्दर्भ सुधार - गोपनीय एडमिन पैनल</h1>
    <p>
        सत्यापित व्यवस्थापक: <strong>{user.username}</strong>
        <span class="badge">वेबसाइट एडमिन</span>
    </p>
    <p>उप-पथ: {subpath or 'मुख्य'}</p>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=status.HTTP_200_OK)


# --- Protected Admin APIs ---
@router.get("/status", summary="System Health and Administration Status")
async def admin_status(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve operational status, kill-switch status, rate limits, and database statistics."""
    settings = get_settings()

    kill_active = await is_kill_switch_active(session=db)
    rate_limit = await get_rate_limit(session=db)

    # Co-admin list
    admin_config = await fetch_onwiki_admins(session=db)
    raw_coadmins = admin_config.get("admins", []) if isinstance(admin_config, dict) else []
    coadmins = [
        ca.strip()
        for ca in raw_coadmins
        if isinstance(ca, str) and not isinstance(ca, bool) and ca.strip()
    ]
    superadmins = [
        sa.strip()
        for sa in settings.SUPERADMIN_USERNAMES
        if isinstance(sa, str) and not isinstance(sa, bool) and sa.strip()
    ]
    all_admins = list(dict.fromkeys(superadmins + coadmins))
    owner_name = superadmins[0] if superadmins else "विक्रम प्रताप"

    # DB Stats
    users_cnt = await db.scalar(select(func.count(User.id))) or 0
    requests_cnt = await db.scalar(select(func.count(RequestQueue.id))) or 0
    audit_cnt = await count_audit_logs(session=db)
    rules_cnt = await db.scalar(select(func.count(WhitelistBlacklistRule.id))) or 0

    return {
        "status": "operational",
        "kill_switch": kill_active,
        "rate_limit_per_minute": rate_limit,
        "admins": all_admins,
        "owner": owner_name,
        "db_stats": {
            "users_count": int(users_cnt),
            "requests_count": int(requests_cnt),
            "audit_logs_count": int(audit_cnt),
            "rules_count": int(rules_cnt),
        },
    }


@router.post("/kill-switch", summary="Toggle or Set Emergency Kill-Switch")
async def toggle_kill_switch(
    payload: Optional[KillSwitchRequest] = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Emergency kill-switch: immediately halts all external scanning and API operations.
    If active parameter is omitted, toggles current state.
    """
    current_state = await is_kill_switch_active(session=db)
    new_state = payload.active if (payload and payload.active is not None) else not current_state

    await set_kill_switch(new_state, updated_by=user.username, session=db)

    await log_audit_event(
        action="kill_switch_toggle",
        username=user.username,
        details={"previous_state": current_state, "new_state": new_state, "admin": user.username},
        user_id=user.id,
        session=db,
    )

    action_text = "सक्रिय" if new_state else "निष्क्रिय"
    return {
        "status": "success",
        "kill_switch": new_state,
        "message": f"आपातकालीन किल-स्विच {action_text} किया गया",
    }


@router.post("/rate-limit", summary="Update Request Rate Limit")
async def update_rate_limit(
    payload: RateLimitRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Adjust system requests-per-minute rate limit."""
    limit = payload.rate_limit_per_minute
    await set_rate_limit(limit, updated_by=user.username, session=db)

    await log_audit_event(
        action="rate_limit_update",
        username=user.username,
        details={"rate_limit_per_minute": limit},
        user_id=user.id,
        session=db,
    )

    return {
        "status": "success",
        "rate_limit_per_minute": limit,
    }


def _format_dt(dt: Any) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


@router.get("/audit", summary="Query System Audit Logs")
@router.get("/audit-logs", summary="Query System Audit Logs (alias)")
async def query_audit_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: Optional[str] = Query(default=None),
    username: Optional[str] = Query(default=None),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve paginated audit logs for administrative review."""
    logs = await get_audit_logs(
        limit=limit,
        offset=offset,
        action=action,
        username=username,
        session=db,
    )
    total = await count_audit_logs(action=action, username=username, session=db)

    serialized_logs = [
        {
            "id": item.id,
            "timestamp": _format_dt(item.timestamp),
            "action": item.action,
            "username": item.username,
            "ip_hash": item.ip_hash,
            "page_title": item.page_title,
            "diff_summary": item.diff_summary,
            "details": item.details,
        }
        for item in logs
    ]

    return {
        "audit_logs": serialized_logs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/rules", summary="List Whitelist and Blacklist Rules")
async def list_rules(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """List all domain, page, and user rules."""
    stmt = select(WhitelistBlacklistRule).order_by(WhitelistBlacklistRule.created_at.desc())
    result = await db.execute(stmt)
    rules = result.scalars().all()

    return {
        "rules": [
            {
                "id": r.id,
                "rule_type": r.rule_type,
                "scope": r.scope,
                "pattern": r.pattern,
                "reason": r.reason,
                "added_by": r.added_by,
                "is_active": r.is_active,
                "created_at": _format_dt(r.created_at),
            }
            for r in rules
        ]
    }


@router.post("/rules", summary="Create Whitelist or Blacklist Rule")
async def create_rule(
    payload: RuleCreateRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Add a new rule to the whitelist/blacklist table."""
    rule = WhitelistBlacklistRule(
        rule_type=payload.rule_type,
        scope=payload.scope,
        pattern=payload.pattern.strip(),
        reason=payload.reason.strip(),
        added_by=user.username,
        is_active=True,
    )
    db.add(rule)
    await db.flush()

    await log_audit_event(
        action="rule_created",
        username=user.username,
        details={
            "rule_id": rule.id,
            "rule_type": rule.rule_type,
            "scope": rule.scope,
            "pattern": rule.pattern,
        },
        user_id=user.id,
        session=db,
    )

    return {
        "status": "success",
        "rule": {
            "id": rule.id,
            "rule_type": rule.rule_type,
            "scope": rule.scope,
            "pattern": rule.pattern,
            "reason": rule.reason,
            "added_by": rule.added_by,
            "is_active": rule.is_active,
        },
    }


@router.delete("/rules/{rule_id}", summary="Delete Whitelist or Blacklist Rule")
async def delete_rule(
    rule_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Delete a rule from the whitelist/blacklist table."""
    stmt = select(WhitelistBlacklistRule).where(WhitelistBlacklistRule.id == rule_id)
    result = await db.execute(stmt)
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="नियम नहीं मिला (Rule not found)",
        )

    await db.delete(rule)
    await db.flush()

    await log_audit_event(
        action="rule_deleted",
        username=user.username,
        details={"rule_id": rule_id},
        user_id=user.id,
        session=db,
    )

    return {"status": "success", "message": f"नियम #{rule_id} हटाया गया"}


@router.get("/queue", summary="List Processing Request Queue")
async def list_queue(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """List pending, completed, or failed scan and processing requests."""
    stmt = select(RequestQueue).order_by(RequestQueue.created_at.desc())
    if status_filter:
        stmt = stmt.where(RequestQueue.status == status_filter)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    queue_items = result.scalars().all()

    return {
        "requests": [
            {
                "id": req.id,
                "page_title": req.page_title,
                "status": req.status,
                "total_refs": req.total_refs,
                "fixed_refs": req.fixed_refs,
                "execution_time_ms": req.execution_time_ms,
                "created_at": _format_dt(req.created_at),
            }
            for req in queue_items
        ],
        "limit": limit,
        "offset": offset,
    }
