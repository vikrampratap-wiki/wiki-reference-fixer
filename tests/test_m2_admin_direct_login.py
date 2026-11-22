"""
Unit tests for direct admin login without Wikipedia OAuth.
Verifies credentials check, superadmin validation, session token issuance, and cookies.
"""

import pytest
import httpx


@pytest.mark.asyncio
async def test_admin_direct_login_success(async_client: httpx.AsyncClient):
    """Admin Vikram Pratap logs in directly with valid password."""
    resp = await async_client.post(
        "/api/auth/admin-login",
        json={"username": "विक्रम प्रताप", "password": "admin@vikram2026"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["username"] == "विक्रम प्रताप"
    assert data["is_admin"] is True
    assert "session_token" in data
    assert "session" in resp.cookies


@pytest.mark.asyncio
async def test_admin_direct_login_ascii_name_success(async_client: httpx.AsyncClient):
    """Admin Vikram Pratap logs in with ASCII variant name."""
    resp = await async_client.post(
        "/api/auth/admin-login",
        json={"username": "Vikram Pratap", "password": "admin@vikram2026"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True


@pytest.mark.asyncio
async def test_admin_direct_login_invalid_password(async_client: httpx.AsyncClient):
    """Admin login fails with invalid password."""
    resp = await async_client.post(
        "/api/auth/admin-login",
        json={"username": "विक्रम प्रताप", "password": "incorrect_password"}
    )
    assert resp.status_code == 401
    assert "अमान्य" in resp.json().get("message", "")


@pytest.mark.asyncio
async def test_admin_direct_login_non_admin_user(async_client: httpx.AsyncClient):
    """Regular user cannot authenticate as admin even with admin password."""
    resp = await async_client.post(
        "/api/auth/admin-login",
        json={"username": "सामान्य_सदस्य", "password": "admin@vikram2026"}
    )
    assert resp.status_code == 401
    assert "अमान्य" in resp.json().get("message", "")
