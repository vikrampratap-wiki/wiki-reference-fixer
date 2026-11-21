"""
FastAPI Main Application Entrypoint and Lifespan Management.
Hindi Wikipedia Reference Fixer Web App (विकिपीडिया सन्दर्भ सुधार उपकरण)
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.admin import admin_page_router, router as admin_router
from app.api.auth import router as auth_router
from app.api.diff import router as diff_router
from app.api.helpers import router as helpers_router
from app.api.scan import router as scan_router
from app.core.config import get_settings
from app.db.database import close_db, get_session_maker, init_db
from app.middleware.rbac import AdminRBACMiddleware
from app.services.http_client import http_client

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wiki_ref_fixer")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    Initializes SQLite database tables and async HTTP client on startup;
    cleanly releases resources and closes connections on shutdown.
    """
    settings = get_settings()
    logger.info(
        "Starting Hindi Wikipedia Reference Fixer v%s (%s)",
        __version__,
        settings.ENVIRONMENT,
    )

    # Startup sequence
    try:
        await init_db()
        logger.info("Async SQLite database initialized with WAL mode.")
    except Exception as e:
        logger.critical("Failed to initialize database: %s", e)
        raise

    try:
        await http_client.init()
        logger.info(
            "HTTPX AsyncClient initialized with User-Agent: %s",
            settings.WIKIMEDIA_USER_AGENT,
        )
    except Exception as e:
        logger.critical("Failed to initialize HTTP client: %s", e)
        raise

    logger.info("Application startup complete. Ready to receive requests.")
    yield

    # Shutdown sequence
    logger.info("Initiating graceful shutdown...")
    try:
        await http_client.close()
        logger.info("HTTP client closed.")
    except Exception as e:
        logger.error("Error closing HTTP client: %s", e)

    try:
        await close_db()
        logger.info("Database engine closed.")
    except Exception as e:
        logger.error("Error closing database engine: %s", e)

    logger.info("Application shutdown complete.")


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="Hindi Wikipedia Reference Fixer (विकिपीडिया सन्दर्भ सुधार उपकरण)",
        description=(
            "हिन्दी विकिपीडिया के लिए सुरक्षित, मानवीय नियंत्रण-युक्त और पूर्ण वेब एप्लिकेशन, "
            "जो विकिपीडिया लेखों में मौजूद नग्न URLs और खराब साँचों का विश्लेषण कर मानक Wikitext "
            "सुधार प्रस्तुत करता है।"
        ),
        version=__version__,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # Admin RBAC & Strict 404 Cloaking Middleware
    app.add_middleware(AdminRBACMiddleware)

    # Configure Cross-Origin Resource Sharing (CORS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception Handlers
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "error",
                "code": exc.status_code,
                "message": exc.detail,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "status": "error",
                "code": 422,
                "message": "अनुरोध डेटा अमान्य है (Request validation failed)",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "code": 500,
                "message": "आंतरिक सर्वर त्रुटि (Internal server error)",
            },
        )

    # Health Check Endpoints
    async def _health_check(response: Optional[Response] = None) -> Dict[str, Any]:
        """Verify database connectivity and application status."""
        db_status = "connected"
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                res = await session.execute(text("SELECT 1;"))
                if res.scalar() != 1:
                    db_status = "degraded"
        except Exception as e:
            logger.error("Health check DB probe failed: %s", e)
            db_status = f"unhealthy: {e}"

        is_healthy = (db_status == "connected")
        if not is_healthy and response is not None:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {
            "status": "healthy" if is_healthy else "degraded",
            "database": db_status,
            "version": __version__,
            "environment": settings.ENVIRONMENT,
            "wikimedia_policy": {
                "user_agent": settings.WIKIMEDIA_USER_AGENT,
                "maxlag": settings.MAXLAG,
            },
        }

    @app.get("/health", tags=["System"])
    async def health_check(response: Response) -> Dict[str, Any]:
        return await _health_check(response)

    @app.get("/api/health", tags=["System"])
    async def api_health_check(response: Response) -> Dict[str, Any]:
        return await _health_check(response)

    @app.get("/", response_class=HTMLResponse, tags=["Frontend"])
    async def index_page() -> HTMLResponse:
        """Serves the primary Hindi Wikipedia Reference Fixer web interface."""
        template_file = Path(__file__).resolve().parent.parent / "templates" / "index.html"
        if template_file.exists():
            return HTMLResponse(content=template_file.read_text(encoding="utf-8"), status_code=200)
        return HTMLResponse(content="<h1>विकिपीडिया सन्दर्भ सुधार उपकरण</h1>", status_code=200)

    # Register API Routers
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(admin_page_router)
    app.include_router(scan_router)
    app.include_router(helpers_router)
    app.include_router(diff_router)

    return app


app = create_app()
