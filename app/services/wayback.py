"""
Internet Archive Wayback Machine Availability API Client.
Provides authentic asynchronous querying of Wayback snapshots,
parses real timestamps into ISO YYYY-MM-DD dates, and provides
resilient dynamic fallbacks when offline or when no snapshot is found.
Authoritative Source: ORIGINAL_REQUEST.md (§4, R4), PROJECT.md (§Feature 15).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional
import httpx

from app.core.config import get_settings
from app.services.http_client import WikimediaHttpClient, http_client

logger = logging.getLogger(__name__)

WAYBACK_AVAILABILITY_API = "https://archive.org/wayback/available"


def parse_wayback_timestamp(timestamp: Optional[str]) -> str:
    """
    Parses a 14-digit (YYYYMMDDhhmmss) or 8-digit (YYYYMMDD) Wayback timestamp
    into an ISO date string (YYYY-MM-DD).
    If timestamp is missing or malformed, dynamically falls back to current UTC date.
    """
    if not timestamp or not isinstance(timestamp, str):
        return datetime.date.today().isoformat()

    cleaned = timestamp.strip()
    if len(cleaned) >= 8 and cleaned[:8].isdigit():
        year = cleaned[:4]
        month = cleaned[4:6]
        day = cleaned[6:8]
        try:
            parsed_date = datetime.date(int(year), int(month), int(day))
            return parsed_date.isoformat()
        except ValueError:
            pass

    return datetime.date.today().isoformat()


class WaybackClient:
    """
    Internet Archive Wayback Machine Availability API Client.
    Complies with Wikimedia bot policy, handles network failures gracefully,
    and dynamically falls back to current date when offline or unavailable.
    """

    def __init__(self, client: Optional[WikimediaHttpClient] = None) -> None:
        self._http_client = client or http_client

    async def query_availability(
        self,
        url: str,
        timestamp: Optional[str] = None,
        custom_client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """
        Queries Wayback Availability API (https://archive.org/wayback/available?url=...).
        Returns:
            {
                "url": str,
                "available": bool,
                "archive_url": str,
                "archive_date": str (YYYY-MM-DD),
                "timestamp": str,
                "status": str,
            }
        """
        clean_url = url.strip()
        settings = get_settings()
        api_base = getattr(settings, "WAYBACK_API_URL", WAYBACK_AVAILABILITY_API)

        params: Dict[str, Any] = {"url": clean_url}
        if timestamp:
            params["timestamp"] = timestamp

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        dynamic_ts = now_utc.strftime("%Y%m%d%H%M%S")
        dynamic_date = datetime.date.today().isoformat()
        fallback_archive_url = f"https://web.archive.org/web/{dynamic_ts}/{clean_url}"

        try:
            if custom_client is not None:
                resp = await custom_client.get(
                    api_base,
                    params=params,
                    headers={"User-Agent": settings.WIKIMEDIA_USER_AGENT},
                    timeout=10.0,
                )
            else:
                resp = await self._http_client.get(
                    api_base,
                    params=params,
                    retries=1,
                    auto_maxlag=False,
                )

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    snapshots = data.get("archived_snapshots", {})
                    closest = snapshots.get("closest")
                    if isinstance(closest, dict) and closest.get("available") in (True, "true"):
                        raw_ts = str(closest.get("timestamp", ""))
                        snapshot_url = str(closest.get("url") or fallback_archive_url)
                        parsed_date = parse_wayback_timestamp(raw_ts)
                        return {
                            "url": clean_url,
                            "available": True,
                            "archive_url": snapshot_url,
                            "archive_date": parsed_date,
                            "timestamp": raw_ts or dynamic_ts,
                            "status": str(closest.get("status", "200")),
                        }

            logger.info(
                "No Wayback snapshot available for %s (status %s)",
                clean_url,
                resp.status_code,
            )
            return {
                "url": clean_url,
                "available": False,
                "archive_url": fallback_archive_url,
                "archive_date": dynamic_date,
                "timestamp": dynamic_ts,
                "status": "unavailable",
            }

        except Exception as exc:
            logger.warning(
                "Wayback Availability API query failed for %s (%s); using dynamic fallback",
                clean_url,
                exc,
            )
            return {
                "url": clean_url,
                "available": False,
                "archive_url": fallback_archive_url,
                "archive_date": dynamic_date,
                "timestamp": dynamic_ts,
                "status": "unavailable",
            }


# Singleton instance
wayback_client = WaybackClient()


async def query_wayback_availability(
    url: str,
    timestamp: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Module-level convenience function querying the Wayback availability client."""
    return await wayback_client.query_availability(
        url, timestamp=timestamp, custom_client=client
    )


__all__ = [
    "WAYBACK_AVAILABILITY_API",
    "WaybackClient",
    "wayback_client",
    "query_wayback_availability",
    "parse_wayback_timestamp",
]
