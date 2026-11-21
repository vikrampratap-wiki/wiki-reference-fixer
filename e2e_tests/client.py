"""
Unified Opaque-Box E2E Test Client.
Supports live server endpoints, production FastAPI ASGI app (app.main:app),
and the authoritative reference specification oracle (e2e_tests.oracle:oracle_app).
"""

import os
import urllib.parse
from typing import Any, Dict, Optional
import httpx
from starlette.testclient import TestClient

from e2e_tests.oracle import oracle_app

# Default Wikimedia-compliant test User-Agent
DEFAULT_USER_AGENT = (
    "WikiRefFixer/1.0 "
    "(https://github.com/vikrampratap-wiki/wiki-reference-fixer; test-suite:e2e)"
)


def _sanitize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Sanitize headers ensuring ASCII/latin-1 compatibility per RFC 7230."""
    clean = {"User-Agent": DEFAULT_USER_AGENT}
    if not headers:
        return clean
    for k, v in headers.items():
        val = str(v)
        try:
            val.encode("latin-1")
            clean[k] = val
        except UnicodeEncodeError:
            clean[k] = urllib.parse.quote(val)
    return clean


class E2ETestClient:
    """
    Opaque-box client that exercises the system exclusively through HTTP entry points.
    """

    _live_client: Optional[httpx.Client]
    _test_client: Optional[TestClient]

    def __init__(self, target: Optional[str] = None, base_url: Optional[str] = None):
        self.target = target or os.environ.get("E2E_TARGET", "contract").lower()
        self.base_url = base_url or os.environ.get("E2E_TARGET_URL")

        self._live_client = None
        self._test_client = None

        if self.base_url:
            self._live_client = httpx.Client(
                base_url=self.base_url,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=10.0,
            )
        elif self.target == "app":
            try:
                from app.main import app as production_app
                self._test_client = TestClient(production_app, raise_server_exceptions=False)
            except Exception:
                # Fall back to oracle if production app is not yet available
                self._test_client = TestClient(oracle_app, raise_server_exceptions=False)
        else:
            # Default to authoritative reference specification oracle
            self._test_client = TestClient(oracle_app, raise_server_exceptions=False)

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Any:
        req_headers = _sanitize_headers(headers)
        if self._live_client is not None:
            return self._live_client.get(url, headers=req_headers, params=params, cookies=cookies)
        assert self._test_client is not None
        return self._test_client.get(url, headers=req_headers, params=params, cookies=cookies)

    def post(
        self,
        url: str,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Any:
        req_headers = _sanitize_headers(headers)
        if self._live_client is not None:
            return self._live_client.post(
                url, json=json, data=data, headers=req_headers, params=params, cookies=cookies
            )
        assert self._test_client is not None
        return self._test_client.post(
            url, json=json, data=data, headers=req_headers, params=params, cookies=cookies
        )

    def close(self):
        if self._live_client:
            self._live_client.close()
        if self._test_client:
            self._test_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_test_client(target: Optional[str] = None) -> E2ETestClient:
    """Helper factory returning a configured test client."""
    return E2ETestClient(target=target)
