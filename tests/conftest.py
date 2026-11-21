"""
Pytest configuration and global fixtures.
Configures test SQLite database, async HTTP client, and mock environments.
"""

from collections.abc import AsyncGenerator
import os
import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport

from app.core.config import get_settings
from app.db.database import close_db, init_db
from app.main import app
from app.services.http_client import http_client


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    """Configure environment variables for testing."""
    os.environ["ENVIRONMENT"] = "testing"
    os.environ["DEBUG"] = "true"
    os.environ["SECRET_KEY"] = "test-secret-key-for-testing-at-least-32-chars-long"
    os.environ["SUPERADMIN_USERNAMES"] = "विक्रम प्रताप,Vikram Pratap"
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def test_db(tmp_path) -> AsyncGenerator[None, None]:
    """Provide an isolated temporary SQLite database for tests."""
    temp_db_file = str(tmp_path / "test_wiki_ref_fixer.db")

    # Override settings to point to temp db
    settings = get_settings()
    original_db_path = settings.DB_PATH
    settings.DB_PATH = temp_db_file

    # Close any existing engine and re-initialize
    await close_db()
    await init_db()

    yield

    await close_db()
    settings.DB_PATH = original_db_path


@pytest_asyncio.fixture
async def async_client(test_db) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an async test client that manages the FastAPI lifespan."""
    await http_client.init()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    await http_client.close()
