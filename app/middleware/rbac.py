"""
Strict 404 Security RBAC Middleware and Route Guards.
Masks all administrator interfaces (/admin, /admin/*, /api/admin/*) with strict HTTP 404
(Not Found) responses for non-administrators, preventing information leakage and endpoint
enumeration. Also provides emergency kill-switch interception for scanning operations.
"""

import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.services.auth import (
    get_current_user_from_request,
    is_kill_switch_active,
    require_admin,
)

logger = logging.getLogger(__name__)


class AdminRBACMiddleware(BaseHTTPMiddleware):
    """
    Middleware intercepting administrative route accesses.
    If the requesting client is not an authenticated website administrator,
    a strict HTTP 404 Not Found is immediately returned without revealing
    the existence of the administrative subsystem.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        # 1. Admin path strict 404 cloaking
        is_admin_path = (
            path == "/admin"
            or path.startswith("/admin/")
            or path.startswith("/api/admin")
        )

        if is_admin_path:
            user = await get_current_user_from_request(request)
            if not user or not user.is_admin:
                logger.warning(
                    "Strict 404 masked access attempt to '%s' from %s",
                    path,
                    request.client.host if request.client else "unknown",
                )
                if path.startswith("/api/"):
                    return JSONResponse(
                        status_code=404,
                        content={
                            "status": "error",
                            "code": 404,
                            "message": "पृष्ठ उपलब्ध नहीं है (404 Not Found)",
                        },
                    )
                return Response(
                    content="पृष्ठ उपलब्ध नहीं है (404 Not Found)",
                    status_code=404,
                    media_type="text/plain; charset=utf-8",
                )
            request.state.user = user

        # 2. Emergency Kill-Switch Interception for scan endpoints
        if path.startswith("/api/scan"):
            if await is_kill_switch_active():
                logger.warning("Scan request blocked due to active emergency kill-switch.")
                return JSONResponse(
                    status_code=503,
                    headers={"Retry-After": "300"},
                    content={
                        "status": "error",
                        "code": 503,
                        "message": (
                            "प्रणाली आपातकालीन स्थिति में रुकी हुई है "
                            "(System under emergency maintenance / kill-switch active)"
                        ),
                    },
                )

        return await call_next(request)


__all__ = [
    "AdminRBACMiddleware",
    "require_admin",
]
