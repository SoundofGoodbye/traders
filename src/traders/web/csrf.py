"""Stateless CSRF protection for the web UI's POST actions.

Double-submit pattern, stdlib-only (no new dependencies): a signed token
is stored in a cookie and echoed in a hidden form field. A POST is
accepted only when the cookie's signature verifies *and* the form token
matches the cookie token. Single-user, local tool — no sessions/accounts.
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256

COOKIE_NAME = "csrftoken"
FIELD_NAME = "csrf_token"


def new_secret() -> str:
    """A fresh signing secret. Stable across restarts only if pinned via env."""
    return secrets.token_hex(32)


def _sign(secret: str, token: str) -> str:
    return hmac.new(secret.encode(), token.encode(), sha256).hexdigest()


def issue(secret: str) -> tuple[str, str]:
    """Return (raw_token, cookie_value). The cookie carries token + signature."""
    token = secrets.token_urlsafe(32)
    return token, f"{token}.{_sign(secret, token)}"


def token_from_cookie(secret: str, cookie_value: str | None) -> str | None:
    """Return the raw token if the cookie's signature verifies, else None."""
    if not cookie_value or "." not in cookie_value:
        return None
    token, _, sig = cookie_value.partition(".")
    if not token or not sig:
        return None
    if hmac.compare_digest(_sign(secret, token), sig):
        return token
    return None


def validate(secret: str, cookie_value: str | None, form_token: str | None) -> bool:
    """True when the cookie is authentic and the form token matches it."""
    token = token_from_cookie(secret, cookie_value)
    if token is None or not form_token:
        return False
    return hmac.compare_digest(token, form_token)
