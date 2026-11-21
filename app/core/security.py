"""
Security utilities: Session cookie signing, CSRF token validation,
and privacy-preserving IP address hashing for Wikimedia compliance.
"""

import hashlib
import hmac
import secrets
from typing import Any, Dict, Optional
from itsdangerous import BadData, BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.core.config import get_settings


def get_serializer() -> URLSafeTimedSerializer:
    """Return a timed serializer configured with the application secret key."""
    settings = get_settings()
    return URLSafeTimedSerializer(
        secret_key=settings.SECRET_KEY,
        salt="wiki-ref-fixer-session",
    )


def create_session_token(data: Dict[str, Any]) -> str:
    """Create a signed session token containing user identity data."""
    serializer = get_serializer()
    return serializer.dumps(data)


def decode_session_token(
    token: Optional[str],
    max_age: int = 86400 * 7,
) -> Optional[Dict[str, Any]]:
    """
    Decode and verify a signed session token.
    Default max_age is 7 days. Returns None if invalid, None, non-string, or expired.
    """
    if not token or not isinstance(token, str):
        return None
    token_str = token.strip()
    if not token_str:
        return None
    serializer = get_serializer()
    try:
        data = serializer.loads(token_str, max_age=max_age)
        if isinstance(data, dict):
            return data
        return None
    except (BadData, BadSignature, SignatureExpired, Exception):
        return None


def generate_csrf_token() -> str:
    """Generate a cryptographically secure random token for CSRF protection."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(token: str, expected_token: str) -> bool:
    """Constant-time comparison of two CSRF tokens to prevent timing attacks."""
    if not token or not expected_token:
        return False
    return hmac.compare_digest(token, expected_token)


def hash_ip_address(ip: Optional[str]) -> str:
    """
    Hash IP address using SHA-256 with a server salt to adhere to Wikimedia Privacy Policy.
    Raw client IP addresses are never persisted directly into the audit log.
    """
    if not ip:
        return "unknown"
    settings = get_settings()
    hasher = hashlib.sha256()
    hasher.update(settings.SECRET_KEY.encode("utf-8"))
    hasher.update(ip.strip().encode("utf-8"))
    return hasher.hexdigest()
