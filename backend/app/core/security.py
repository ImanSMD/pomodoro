"""Password hashing and token issuing.

Two different kinds of secret live here and they are deliberately handled
differently:

* Passwords are low-entropy and user-chosen, so they get argon2 — slow by
  design, to make offline cracking expensive if the table ever leaks.
* Refresh tokens are 256 bits from `secrets`, so brute force is not a threat
  and a slow hash would only add latency to every refresh. They get SHA-256,
  which is enough to stop a leaked database row from being replayed as a
  bearer token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from passlib.context import CryptContext

ACCESS_TOKEN_TYPE = "access"

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_access_token(user_id: UUID, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate an access token, raising jwt exceptions on failure.

    algorithms is pinned to HS256: without it, a token carrying `"alg": "none"`
    would be accepted as valid and unsigned.
    """
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        # Refuse anything that is not an access token, so a token minted for
        # another purpose can never be replayed as one.
        raise jwt.InvalidTokenError("not an access token")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
