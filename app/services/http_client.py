"""
Resilient HTTPX AsyncClient with connection pooling, Wikimedia User-Agent,
and maxlag=5 exponential backoff retry handling.
"""

import asyncio
import datetime
from email.utils import parsedate_to_datetime
import json
import logging
import math
import random
import re
from typing import Any, Dict, Optional
import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS: float = 60.0


def parse_retry_after(
    header_val: Optional[str],
    default_delay: float = 1.0,
    max_delay: float = MAX_BACKOFF_SECONDS,
) -> float:
    """
    Parse Retry-After header with support for seconds (int/float), whitespace stripping,
    and HTTP-date (RFC 7231). Returns delay bounded by max_delay.
    Guarantees returned delay is a finite positive float.
    """
    safe_default = (
        default_delay
        if isinstance(default_delay, (int, float))
        and math.isfinite(default_delay)
        and default_delay > 0
        else 1.0
    )
    safe_max = (
        max_delay
        if isinstance(max_delay, (int, float))
        and math.isfinite(max_delay)
        and max_delay > 0
        else MAX_BACKOFF_SECONDS
    )
    fallback = min(safe_default, safe_max)

    if not header_val or not isinstance(header_val, str):
        return fallback

    cleaned = header_val.strip()
    try:
        val = float(cleaned)
        if not math.isfinite(val) or val <= 0:
            return fallback
        return min(val, safe_max)
    except (ValueError, TypeError):
        pass

    try:
        target_dt = parsedate_to_datetime(cleaned)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        diff = (target_dt - now_dt).total_seconds()
        if math.isfinite(diff) and diff > 0:
            return min(diff, safe_max)
    except Exception:
        pass

    return fallback


def _extract_maxlag_delay(body: str) -> Optional[float]:
    """
    Extract lag delay seconds from a response body or maxlag error message,
    ensuring that parsed float seconds are finite and positive.
    """
    if not body or not isinstance(body, str):
        return None

    info = body
    try:
        data = json.loads(body)
        if isinstance(data, dict) and "error" in data:
            err = data.get("error", {})
            if isinstance(err, dict):
                info = str(err.get("info", body))
    except Exception:
        pass

    match = re.search(
        r"([+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|nan|inf(?:inity)?))\s*seconds?",
        info,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"lagged\s*([+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|nan|inf(?:inity)?))",
            info,
            re.IGNORECASE,
        )
    if match:
        try:
            val = float(match.group(1))
            if math.isfinite(val) and val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return None


class WikimediaLagError(Exception):
    """Raised when Wikimedia API lag exceeds threshold and max retries are exhausted."""
    pass


class WikimediaHttpClient:
    """
    Singleton HTTPX AsyncClient wrapper complying with Wikimedia API policy.
    Includes keepalive connection pooling, default User-Agent headers,
    and automatic handling of Wikimedia maxlag and HTTP 429 rate limit responses.
    """

    def __init__(self) -> None:
        self.client: Optional[httpx.AsyncClient] = None

    async def init(self) -> None:
        """Initialize the shared httpx.AsyncClient with connection limits."""
        if self.client is None or self.client.is_closed:
            settings = get_settings()
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            )
            timeout = httpx.Timeout(timeout=20.0, connect=5.0)
            headers = {
                "User-Agent": settings.WIKIMEDIA_USER_AGENT,
                "Api-User-Agent": settings.WIKIMEDIA_USER_AGENT,
                "Accept": "application/json",
            }
            self.client = httpx.AsyncClient(
                limits=limits,
                timeout=timeout,
                headers=headers,
                follow_redirects=True,
            )
            logger.info(
                "WikimediaHttpClient initialized with User-Agent: %s",
                settings.WIKIMEDIA_USER_AGENT,
            )

    async def close(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        if self.client and not self.client.is_closed:
            try:
                await self.client.aclose()
            except (RuntimeError, Exception) as exc:
                logger.debug("Exception during client close: %s", exc)
            finally:
                self.client = None
                logger.info("WikimediaHttpClient closed successfully.")

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the active client, raising RuntimeError if not initialized."""
        if self.client is None or self.client.is_closed:
            raise RuntimeError("WikimediaHttpClient is not initialized. Call init() first.")
        return self.client

    async def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        auto_maxlag: bool = True,
        jitter: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Execute an HTTP request with automatic retry on Wikimedia maxlag, HTTP 429/502/503/504,
        and transient network transport errors.
        """
        if self.client is None or self.client.is_closed:
            await self.init()
        client = self._ensure_client()
        settings = get_settings()
        max_retries = retries if retries is not None else settings.MAXLAG_MAX_RETRIES

        request_params = dict(params or {})
        # If targeting a Wikimedia endpoint and auto_maxlag is enabled, inject maxlag parameter
        if auto_maxlag and ("wikipedia.org" in url or "wikimedia.org" in url):
            if "maxlag" not in request_params:
                request_params["maxlag"] = settings.MAXLAG

        for attempt in range(max_retries + 1):
            try:
                response = await client.request(method, url, params=request_params, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= max_retries:
                    logger.error(
                        "Network transport error calling %s; max retries (%d) exhausted: %s",
                        url,
                        max_retries,
                        exc,
                    )
                    raise

                delay = min(MAX_BACKOFF_SECONDS, float(2 ** attempt))
                if jitter:
                    delay = min(MAX_BACKOFF_SECONDS, delay + random.uniform(0.1, 1.0))

                logger.warning(
                    "Transient transport exception (%s: %s) calling %s. "
                    "Sleeping %.1fs (attempt %d/%d)",
                    type(exc).__name__,
                    exc,
                    url,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
                continue

            # Check 1: HTTP 429 (Too Many Requests), 502, 503, 504 (Server Errors)
            if response.status_code in (429, 502, 503, 504):
                if attempt >= max_retries:
                    logger.warning(
                        "HTTP %d error; max retries (%d) reached for %s",
                        response.status_code,
                        max_retries,
                        url,
                    )
                    response.raise_for_status()

                raw_retry_after = response.headers.get("Retry-After")
                default_delay = float(2 ** attempt)
                delay = parse_retry_after(
                    raw_retry_after,
                    default_delay=default_delay,
                    max_delay=MAX_BACKOFF_SECONDS,
                )
                if not math.isfinite(delay) or delay <= 0:
                    delay = default_delay
                if jitter and raw_retry_after is None:
                    delay = min(MAX_BACKOFF_SECONDS, delay + random.uniform(0.1, 1.0))

                logger.warning(
                    "HTTP %d error encountered from %s. Sleeping %.1fs (attempt %d/%d)",
                    response.status_code,
                    url,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
                continue

            # Check 2: MediaWiki API maxlag response in HTTP 200
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    data = None
                    try:
                        data = response.json()
                    except Exception:
                        data = None

                    if isinstance(data, dict) and "error" in data:
                        err = data.get("error", {})
                        if isinstance(err, dict) and err.get("code") == "maxlag":
                            if attempt >= max_retries:
                                lag_info = err.get("info", "lagged server")
                                raise WikimediaLagError(
                                    f"Wikimedia maxlag exceeded ({lag_info}). "
                                    f"Max retries ({max_retries}) exhausted."
                                )
                            raw_retry_after = response.headers.get("Retry-After")
                            default_delay = max(5.0, float(2 ** attempt))
                            retry_delay = parse_retry_after(
                                raw_retry_after,
                                default_delay=default_delay,
                                max_delay=MAX_BACKOFF_SECONDS,
                            )
                            # Check if lag delay was explicitly indicated in maxlag info
                            extracted_delay = _extract_maxlag_delay(str(err.get("info", "")))
                            if extracted_delay is not None and raw_retry_after is None:
                                retry_delay = min(extracted_delay, MAX_BACKOFF_SECONDS)

                            if not math.isfinite(retry_delay) or retry_delay <= 0:
                                retry_delay = default_delay

                            if jitter and raw_retry_after is None:
                                retry_delay = min(
                                    MAX_BACKOFF_SECONDS,
                                    retry_delay + random.uniform(0.1, 1.0),
                                )

                            logger.warning(
                                "Wikimedia server lagged (%s). Sleeping %.1fs before retry "
                                "(attempt %d/%d)",
                                err.get("info"),
                                retry_delay,
                                attempt + 1,
                                max_retries,
                            )
                            await asyncio.sleep(retry_delay)
                            continue

            return response

        # Fallback if loop ends without return
        raise WikimediaLagError(
            f"Request to {url} failed after {max_retries} retries due to server lag or rate limit."
        )

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        auto_maxlag: bool = True,
        jitter: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform a GET request with maxlag resilience."""
        return await self.request(
            "GET",
            url,
            params=params,
            retries=retries,
            auto_maxlag=auto_maxlag,
            jitter=jitter,
            **kwargs,
        )

    async def post(
        self,
        url: str,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        auto_maxlag: bool = True,
        jitter: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform a POST request with maxlag resilience."""
        return await self.request(
            "POST",
            url,
            params=params,
            retries=retries,
            auto_maxlag=auto_maxlag,
            jitter=jitter,
            data=data,
            json=json,
            **kwargs,
        )

    async def fetch_json(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Perform request and decode JSON payload."""
        response = await self.request(method, url, params=params, **kwargs)
        response.raise_for_status()
        return response.json()


# Global client singleton
http_client = WikimediaHttpClient()

__all__ = [
    "WikimediaHttpClient",
    "http_client",
    "WikimediaLagError",
    "MAX_BACKOFF_SECONDS",
    "parse_retry_after",
    "_extract_maxlag_delay",
]
