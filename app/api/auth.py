"""
Authentication and Wikimedia OAuth 2.0 API Endpoints.
Supports Wikimedia OAuth 2.0 login, callback, session cookie issuance,
session inspection (/me), logout, and fast development login (/dev-login).
"""

import logging
from typing import Any, Dict, List, Optional
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_session_token, generate_csrf_token, verify_csrf_token
from app.db.database import get_db
from app.services.audit import log_audit_event
from app.services.auth import (
    get_current_user_from_request,
    get_or_create_user,
    verify_website_admin,
)
from app.services.http_client import http_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class DevLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Wikipedia username to authenticate as")


class UserProfileResponse(BaseModel):
    authenticated: bool
    username: Optional[str] = None
    is_admin: bool = False
    groups: List[str] = Field(default_factory=list)


@router.get("/login", summary="Initiate Wikimedia OAuth 2.0 flow")
async def oauth_login(
    redirect: bool = Query(
        default=False,
        description="Redirect browser directly to OAuth provider",
    ),
) -> Response:
    """
    Constructs the Wikimedia OAuth 2.0 authorization URL and generates a secure CSRF state token.
    If redirect=true, issues a 307 redirect directly to Wikimedia Meta-Wiki.
    """
    settings = get_settings()
    state = generate_csrf_token()

    client_id = settings.WIKIMEDIA_OAUTH_CLIENT_ID or "wiki-ref-fixer-dev"
    redirect_uri = settings.WIKIMEDIA_OAUTH_REDIRECT_URI

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    encoded_params = urllib.parse.urlencode(params)
    auth_url = f"https://meta.wikimedia.org/w/rest.php/oauth2/authorize?{encoded_params}"

    if redirect:
        resp: Response = RedirectResponse(
            url=auth_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    else:
        resp = JSONResponse(content={"auth_url": auth_url, "state": state})

    resp.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return resp


@router.get("/callback", summary="Handle Wikimedia OAuth 2.0 callback")
async def oauth_callback(
    request: Request,
    code: str = Query(..., description="OAuth 2.0 authorization code"),
    state: Optional[str] = Query(default=None, description="CSRF state token"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Exchanges authorization code for an access token, fetches user identity profile,
    registers or updates the user in SQLite, checks website admin status,
    and issues signed session cookies.
    """
    settings = get_settings()

    # Verify CSRF state token if cookie exists
    stored_state = request.cookies.get("oauth_state")
    if stored_state and state:
        if not verify_csrf_token(state, stored_state):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="अमान्य OAuth CSRF स्टेट टोकन (Invalid OAuth state token)",
            )

    token_url = "https://meta.wikimedia.org/w/rest.php/oauth2/access_token"
    profile_url = "https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile"

    username = "unknown"
    wiki_user_id: Optional[int] = None
    groups: List[str] = []

    # Exchange authorization code for token
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.WIKIMEDIA_OAUTH_REDIRECT_URI,
        "client_id": settings.WIKIMEDIA_OAUTH_CLIENT_ID or "",
        "client_secret": settings.WIKIMEDIA_OAUTH_CLIENT_SECRET or "",
    }

    try:
        token_resp = await http_client.post(token_url, data=token_data)
        if token_resp.status_code == 200:
            token_json = token_resp.json()
            access_token = token_json.get("access_token")

            if access_token:
                profile_resp = await http_client.get(
                    profile_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if profile_resp.status_code == 200:
                    profile = profile_resp.json()
                    username = str(profile.get("username", username))
                    wiki_user_id = profile.get("sub")
                    groups = profile.get("groups", [])
    except Exception as exc:
        logger.warning(
            "OAuth exchange encountered exception: %s. Using mock fallback if testing.",
            exc,
        )
        if settings.ENVIRONMENT in ("development", "testing") and code.startswith("mock_"):
            username = code.replace("mock_", "")

    if username == "unknown":
        if settings.ENVIRONMENT in ("development", "testing") and code.startswith("mock_"):
            username = code.replace("mock_", "")
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="उपयोगकर्ता पहचान प्राप्त करने में विफल (Failed to retrieve user identity)",
            )

    # Upsert user record
    user = await get_or_create_user(
        username=username,
        wiki_user_id=wiki_user_id,
        groups=groups,
        session=db,
    )

    is_admin = await verify_website_admin(username, session=db)
    user.is_admin = is_admin

    # Issue signed session token
    token_payload = {
        "username": user.username,
        "user_id": user.id,
        "is_admin": is_admin,
    }
    session_token = create_session_token(token_payload)

    # Audit log
    await log_audit_event(
        action="oauth_login",
        username=user.username,
        details={"is_admin": is_admin, "wiki_user_id": wiki_user_id},
        ip_address=request.client.host if request.client else None,
        user_id=user.id,
        session=db,
    )

    # Response with cookies
    accept_header = request.headers.get("Accept", "")
    if "application/json" in accept_header:
        resp: Response = JSONResponse(
            content={
                "status": "success",
                "username": user.username,
                "is_admin": is_admin,
                "session_token": session_token,
            }
        )
    else:
        resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    resp.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    resp.set_cookie(
        key="username",
        value=urllib.parse.quote(user.username),
        httponly=False,
        samesite="lax",
        max_age=86400 * 7,
    )
    resp.delete_cookie(key="oauth_state")
    return resp


@router.post("/logout", summary="Logout and clear session cookies")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Clear session cookie and log logout event."""
    user = await get_current_user_from_request(request, session=db)
    if user:
        await log_audit_event(
            action="logout",
            username=user.username,
            details={},
            ip_address=request.client.host if request.client else None,
            user_id=user.id,
            session=db,
        )

    response = JSONResponse(
        content={"status": "success", "message": "सफलतापूर्वक लॉगआउट किया गया"}
    )
    response.delete_cookie(key="session")
    response.delete_cookie(key="username")
    return response


@router.get("/me", response_model=UserProfileResponse, summary="Get current user profile")
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return authenticated profile information for current session."""
    user = await get_current_user_from_request(request, session=db)
    if not user:
        return {
            "authenticated": False,
            "username": None,
            "is_admin": False,
            "groups": [],
        }

    return {
        "authenticated": True,
        "username": user.username,
        "is_admin": user.is_admin,
        "groups": user.groups,
    }


@router.post("/dev-login", summary="Development-only fast login")
async def dev_login(
    payload: DevLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Development and test endpoint allowing rapid authentication as any username.
    Strictly disabled and masked as 404 in production environments.
    """
    settings = get_settings()
    if settings.ENVIRONMENT not in ("development", "testing") and not settings.DEBUG:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="पृष्ठ उपलब्ध नहीं है (404 Not Found)",
        )

    target_user = payload.username.strip()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="उपयोगकर्ता नाम रिक्त नहीं हो सकता",
        )

    user = await get_or_create_user(username=target_user, session=db)
    is_admin = await verify_website_admin(target_user, session=db)
    user.is_admin = is_admin

    token_payload = {
        "username": user.username,
        "user_id": user.id,
        "is_admin": is_admin,
    }
    session_token = create_session_token(token_payload)

    await log_audit_event(
        action="dev_login",
        username=user.username,
        details={"is_admin": is_admin},
        ip_address=request.client.host if request.client else None,
        user_id=user.id,
        session=db,
    )

    response = JSONResponse(
        content={
            "status": "success",
            "username": user.username,
            "is_admin": is_admin,
            "session_token": session_token,
        }
    )
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    response.set_cookie(
        key="username",
        value=urllib.parse.quote(user.username),
        httponly=False,
        samesite="lax",
        max_age=86400 * 7,
    )
    return response
