"""
Application Configuration Module using Pydantic Settings.
Complies with Wikimedia Toolforge environment specifications and bot/API policies.
"""

from functools import lru_cache
from typing import List, Optional, Union
import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server & Network Configuration (Toolforge dynamically sets $PORT)
    PORT: int = Field(default=8000, description="Web server listening port")
    HOST: str = Field(default="0.0.0.0", description="Web server binding host")
    ENVIRONMENT: str = Field(default="development", description="Application environment")
    DEBUG: bool = Field(default=True, description="Debug mode")
    SECRET_KEY: str = Field(
        default="dev-insecure-secret-key-change-in-production-min-32-chars",
        description="Cryptographic secret key for session cookies and CSRF protection",
    )

    # Database Configuration (Async SQLite with WAL mode)
    DB_PATH: str = Field(
        default="data/wiki_ref_fixer.db",
        description="Path to local SQLite database file",
    )

    # Wikimedia API Policy Configuration
    WIKIMEDIA_USER_AGENT: str = Field(
        default=(
            "WikiRefFixer/1.0 "
            "(https://github.com/vikrampratap-wiki/wiki-reference-fixer; contact: toolforge)"
        ),
        description="Mandatory User-Agent conforming to Wikimedia API policy",
    )
    MAXLAG: int = Field(default=5, description="Wikimedia maxlag parameter threshold")
    MAXLAG_MAX_RETRIES: int = Field(default=3, description="Maximum retry attempts on maxlag/429")

    # Website Admin RBAC Configuration
    # Website owner is Vikram Pratap (विक्रम प्रताप).
    SUPERADMIN_USERNAMES: Union[List[str], str] = Field(
        default=["विक्रम प्रताप", "Vikram Pratap"],
        description="Explicit website superadmin usernames with ultimate site authority",
    )
    ADMIN_ONWIKI_PAGE: str = Field(
        default="सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json",
        description="Wikipedia user subpage containing dynamic authorized co-admins",
    )
    ADMIN_SUBPAGE_CACHE_TTL_SECONDS: int = Field(
        default=600,
        description="Cache TTL in seconds (10 minutes) for on-wiki admin subpage",
    )

    # Wikimedia OAuth 2.0 Credentials
    WIKIMEDIA_OAUTH_CLIENT_ID: Optional[str] = Field(
        default=None, description="Wikimedia OAuth 2.0 Client ID"
    )
    WIKIMEDIA_OAUTH_CLIENT_SECRET: Optional[str] = Field(
        default=None, description="Wikimedia OAuth 2.0 Client Secret"
    )
    WIKIMEDIA_OAUTH_REDIRECT_URI: str = Field(
        default="http://localhost:8000/api/auth/callback",
        description="Wikimedia OAuth 2.0 Redirect URI",
    )

    # External APIs
    CITOID_API_URL: str = Field(
        default="https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki",
        description="Wikimedia Citoid REST API base URL",
    )
    MEDIAWIKI_API_URL: str = Field(
        default="https://hi.wikipedia.org/w/api.php",
        description="MediaWiki Action API base URL",
    )
    WAYBACK_API_URL: str = Field(
        default="https://archive.org/wayback/available",
        description="Internet Archive Wayback Machine Availability API URL",
    )

    @field_validator("SUPERADMIN_USERNAMES", mode="before")
    @classmethod
    def parse_superadmin_usernames(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            # Comma-separated or whitespace
            names = [name.strip() for name in v.split(",") if name.strip()]
            return names
        if isinstance(v, list):
            return [str(name).strip() for name in v if str(name).strip()]
        return ["विक्रम प्रताप", "Vikram Pratap"]

    @property
    def database_url(self) -> str:
        # Resolve absolute or relative path for SQLite
        db_path = os.path.abspath(self.DB_PATH)
        return f"sqlite+aiosqlite:///{db_path}"

    def is_superadmin(self, username: Optional[str]) -> bool:
        """
        Check if a given Wikipedia username matches any configured superadmin username.
        Performs normalized comparison (stripping whitespace, underscore-space equivalence).
        """
        if not username:
            return False

        normalized_target = username.strip().replace("_", " ").lower()
        for admin in self.SUPERADMIN_USERNAMES:
            normalized_admin = admin.strip().replace("_", " ").lower()
            if normalized_target == normalized_admin:
                return True
        return False


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton instance of application settings."""
    return Settings()
