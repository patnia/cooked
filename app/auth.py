"""Real user accounts: password hashing + opaque session tokens.

Separate from app/tiers.py -- tier is "what can this request do", identity is
"who is this". Stdlib-only (hashlib PBKDF2 + secrets) to avoid a new
dependency for a single-developer-scale app; swap for a stronger KDF
(argon2/bcrypt) if this ever needs to scale past personal use.
"""

import hashlib
import hmac
import secrets

from fastapi import Request

from app import users_db

SESSION_COOKIE = "wc_session_token"

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations, salt, hex_digest = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), hex_digest)
    except (ValueError, AttributeError):
        return False


def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    users_db.create_session(user_id, token)
    return token


def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return users_db.get_session_user(token)
