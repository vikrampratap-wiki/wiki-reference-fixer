"""
User Request System Endpoints.
Authoritative Source: PDF (§4.ख, §4.ग).
Allows standard users to submit batch, rule, or article repair requests to administrators,
and query the status of their requests (Pending / Approved / Completed / Rejected).
"""

from datetime import datetime
import logging
from typing import Any, Dict, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import RequestQueue
from app.services.audit import log_audit_event
from app.services.auth import get_current_user_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/requests", tags=["Requests"])


class RequestCreatePayload(BaseModel):
    page_title: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Article, category, or template title",
    )
    request_type: str = Field(
        ...,
        pattern="^(category_batch|template_rule|article_fix)$",
        description="Type of request: category_batch, template_rule, or article_fix",
    )
    description: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Detailed description of the request",
    )
    requester_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Wikipedia username or requester identifier",
    )


def _format_dt(dt: Any) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


@router.post("", summary="Submit a request to administrators", status_code=status.HTTP_201_CREATED)
async def submit_user_request(
    payload: RequestCreatePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Standard user request submission:
    - Allows requesting batch category scans, template/parameter rules, or article repairs.
    - Persists request in RequestQueue with initial status 'pending'.
    - Logs event to system audit log.
    """
    user = await get_current_user_from_request(request)
    requester = payload.requester_name or (user.username if user else "anonymous")

    request_id = str(uuid.uuid4())
    queue_item = RequestQueue(
        id=request_id,
        user_id=user.id if user else None,
        page_title=payload.page_title.strip(),
        status="pending",
        total_refs=0,
        fixed_refs=0,
        options={
            "request_type": payload.request_type,
            "description": payload.description.strip(),
            "requester_name": requester,
            "submitted_at": datetime.utcnow().isoformat(),
        },
    )
    db.add(queue_item)
    await db.flush()

    await log_audit_event(
        action="user_request_submitted",
        username=requester,
        page_title=payload.page_title,
        details={
            "request_id": request_id,
            "request_type": payload.request_type,
            "description": payload.description[:200],
        },
        user_id=user.id if user else None,
        session=db,
    )

    type_hi_map = {
        "category_batch": "श्रेणी बैच-सुधार",
        "template_rule": "साँचा/पैरामीटर नियम",
        "article_fix": "विशेष लेख सुधार",
    }

    return {
        "status": "success",
        "request_id": request_id,
        "message": f"{type_hi_map.get(payload.request_type, 'अनुरोध')} सफलतापूर्वक दर्ज किया गया।",
        "data": {
            "id": request_id,
            "page_title": queue_item.page_title,
            "request_type": payload.request_type,
            "status": queue_item.status,
            "created_at": _format_dt(queue_item.created_at),
        },
    }


@router.get("", summary="List user submitted requests")
async def list_user_requests(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve public list of submitted requests and their fulfillment status."""
    stmt = select(RequestQueue).order_by(RequestQueue.created_at.desc())
    if status_filter:
        stmt = stmt.where(RequestQueue.status == status_filter)

    total_stmt = select(func.count(RequestQueue.id))
    if status_filter:
        total_stmt = total_stmt.where(RequestQueue.status == status_filter)
    total = await db.scalar(total_stmt) or 0

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    items = result.scalars().all()

    return {
        "requests": [
            {
                "id": it.id,
                "page_title": it.page_title,
                "status": it.status,
                "request_type": (
                    it.options.get("request_type", "article_fix")
                    if it.options
                    else "article_fix"
                ),
                "description": it.options.get("description", "") if it.options else "",
                "requester_name": (
                    it.options.get("requester_name", "अनाम")
                    if it.options
                    else "अनाम"
                ),
                "reject_reason": it.options.get("reject_reason") if it.options else None,
                "created_at": _format_dt(it.created_at),
                "updated_at": _format_dt(it.updated_at),
            }
            for it in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{request_id}", summary="Check status of a specific request")
async def get_request_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Lookup the status of a specific request by ID."""
    stmt = select(RequestQueue).where(RequestQueue.id == request_id)
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="अनुरोध नहीं मिला (Request not found)",
        )

    return {
        "id": item.id,
        "page_title": item.page_title,
        "status": item.status,
        "request_type": (
            item.options.get("request_type", "article_fix")
            if item.options
            else "article_fix"
        ),
        "description": item.options.get("description", "") if item.options else "",
        "requester_name": (
            item.options.get("requester_name", "अनाम")
            if item.options
            else "अनाम"
        ),
        "reject_reason": item.options.get("reject_reason") if item.options else None,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }
