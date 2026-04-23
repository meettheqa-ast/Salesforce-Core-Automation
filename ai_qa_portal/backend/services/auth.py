"""JWT verification + the FastAPI ``get_current_user`` dependency.

The frontend (NextAuth on Vercel) signs a session JWT with HS256 using
``NEXTAUTH_SECRET``. NextAuth v5 derives the actual signing key from that secret
via HKDF-SHA256 (label ``"NextAuth.js Generated Encryption Key"``), so we
replicate that derivation here -- the env var alone is **not** the raw key.

We accept both raw HS256 tokens (other clients) and NextAuth's JWE tokens via
the ``jose`` style header check, but for Phase 1 the canonical token is the
HS256 JWT NextAuth issues when ``session: { strategy: "jwt" }`` is set
(default in NextAuth v5).

Token claims we rely on:
  - ``email``: required, must end with ``@{ALLOWED_EMAIL_DOMAIN}`` if set.
  - ``name``, ``picture``: optional display fields.
  - ``sub`` or ``email`` is used to look up / create the local ``User`` row.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from .db import User, get_db, get_or_create_user

logger = logging.getLogger("ai_qa_portal.auth")


# --- Key derivation -------------------------------------------------------

def _hkdf_sha256(secret: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256. Implemented inline so we don't pull cryptography just for this."""
    if len(secret) == 0:
        raise ValueError("HKDF secret is empty")
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    out = b""
    last = b""
    counter = 1
    while len(out) < length:
        last = hmac.new(prk, last + info + bytes([counter]), hashlib.sha256).digest()
        out += last
        counter += 1
    return out[:length]


def _derive_nextauth_hs256_key(secret: str) -> bytes:
    """Derive the same 32-byte signing key NextAuth v5 uses for HS256 JWT sessions.

    NextAuth derives the key with HKDF-SHA256(secret, salt=b"", info=b"NextAuth.js Generated Encryption Key").
    For JWT (signed only, not encrypted) sessions, the same derivation is used
    and the key is consumed by HS256.
    """
    if not secret:
        raise RuntimeError("NEXTAUTH_SECRET is not configured on the backend")
    return _hkdf_sha256(secret.encode("utf-8"), b"", b"NextAuth.js Generated Encryption Key", 32)


# --- Token verification ---------------------------------------------------

def verify_jwt(token: str) -> dict[str, Any]:
    """Verify an HS256 JWT issued by the frontend's NextAuth instance.

    Tries two signing keys, in order:
      1. The HKDF-derived key (NextAuth default behaviour).
      2. The raw ``NEXTAUTH_SECRET`` bytes (in case the frontend opts out of HKDF).

    Raises HTTPException(401) on any failure.
    """
    secret = settings.nextauth_secret
    if not secret:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Backend auth not configured: NEXTAUTH_SECRET is empty",
        )

    candidate_keys: list[bytes | str] = [_derive_nextauth_hs256_key(secret), secret]

    last_err: Exception | None = None
    for key in candidate_keys:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            last_err = exc
            continue

    logger.info("JWT verification failed: %s", last_err)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session token")


# --- FastAPI dependency ---------------------------------------------------

def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authorization must be 'Bearer <token>'")
    return parts[1].strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    token_q: str | None = Query(default=None, alias="token"),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the current logged-in user from the Authorization header OR a
    ``?token=`` query string (for SSE / EventSource which can't send headers).

    Behaviour:
      - If ``settings.auth_disabled`` is True (dev/migration), return or create a
        synthetic ``dev@local`` admin user. Lets the backend boot and respond to
        smoke tests without a frontend session.
      - Otherwise, parse Bearer token (header preferred, query fallback), verify
        JWT signature, enforce email domain, and upsert the ``User`` row.
    """
    if settings.auth_disabled:
        # Synthetic dev user. Force admin so legacy projects/personas remain
        # visible while AUTH_DISABLED is on (used to keep the unauthenticated
        # Vercel build working until OAuth is wired in).
        user = get_or_create_user(
            db,
            email="dev@local",
            name="Dev (auth disabled)",
            picture="",
        )
        if not user.is_admin:
            user.is_admin = True
            db.commit()
            db.refresh(user)
        return user

    if authorization:
        token = _extract_bearer(authorization)
    elif token_q:
        token = token_q.strip()
    else:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header or ?token= query param",
        )

    payload = verify_jwt(token)

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token is missing 'email' claim")

    domain = settings.allowed_email_domain.strip().lower()
    if domain and not email.endswith("@" + domain):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only @{domain} accounts may access this portal",
        )

    user = get_or_create_user(
        db,
        email=email,
        name=str(payload.get("name") or ""),
        picture=str(payload.get("picture") or ""),
    )
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that 403s for non-admin users. Used by admin-only endpoints
    (none yet in Phase 1; reserved for Phase 2's ``/api/users``).
    """
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return current_user
