"""Google ID token verification + the FastAPI ``get_current_user`` dependency.

Auth model:
  Frontend (Vercel + NextAuth) handles the Google OAuth flow and stores the
  Google-issued ``id_token`` in the session. ``/api/auth/jwt`` on the frontend
  hands that raw ID token to the browser, which forwards it to this backend as
  ``Authorization: Bearer <token>`` (or as ``?token=<token>`` for SSE / <a href>
  / <img src> URLs that cannot set a header).

  This backend verifies the ID token directly against Google's JWKS at
  https://www.googleapis.com/oauth2/v3/certs -- no shared secret with the
  frontend, no encrypted JWE handling, no NextAuth internals to track. The
  trust anchor is Google's signing keys + our OAuth Client ID as the expected
  audience.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from .db import User, get_db, get_or_create_user

logger = logging.getLogger("ai_qa_portal.auth")


# Google's published JWKS endpoint (rotated keys; PyJWKClient caches internally).
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"]

# Lazy singleton -- PyJWKClient does its own key caching with a default TTL.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_GOOGLE_JWKS_URL, cache_keys=True)
    return _jwks_client


def verify_google_id_token(token: str) -> dict[str, Any]:
    """Verify a Google-issued ID token via JWKS. Raises 401 on any failure."""
    client_id = settings.google_client_id.strip()
    if not client_id:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Backend auth not configured: GOOGLE_CLIENT_ID is empty",
        )

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=_GOOGLE_ISSUERS,
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google ID token expired") from None
    except jwt.InvalidAudienceError:
        # Almost always indicates the frontend's GOOGLE_CLIENT_ID and the backend's
        # GOOGLE_CLIENT_ID came from different OAuth clients. Flagged loudly because
        # this would otherwise be confusing in production.
        logger.warning(
            "Google ID token audience mismatch -- frontend GOOGLE_CLIENT_ID and "
            "backend GOOGLE_CLIENT_ID likely point at different OAuth clients"
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token audience mismatch -- frontend/backend Google client IDs differ",
        ) from None
    except jwt.InvalidIssuerError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token issuer") from None
    except jwt.InvalidTokenError as exc:
        logger.info("Google ID token verification failed: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google ID token") from None
    except Exception as exc:  # JWKS network failure, etc.
        logger.exception("JWKS verification failed unexpectedly")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not verify token (JWKS error): {exc}",
        ) from None


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
    """Resolve the current logged-in user from a Google ID token.

    Token source: ``Authorization: Bearer <token>`` header preferred; falls back
    to ``?token=<jwt>`` query param for SSE / <a href> / <img src> calls that
    can't attach a header.

    Behaviour with ``settings.auth_disabled``: returns a synthetic ``dev@local``
    admin so the unauthenticated demo build keeps working.
    """
    if settings.auth_disabled:
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

    payload = verify_google_id_token(token)

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token is missing 'email' claim")

    domain = settings.allowed_email_domain.strip().lower()
    if domain:
        # Google sets `hd` (hosted domain) on Workspace ID tokens. Prefer that
        # claim when present; fall back to email-suffix matching for personal
        # accounts that happen to share the domain (rare but defensible).
        hd = (payload.get("hd") or "").strip().lower()
        if hd:
            if hd != domain:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Only @{domain} accounts may access this portal",
                )
        elif not email.endswith("@" + domain):
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
    """Dependency that 403s for non-admin users. Used by admin-only endpoints."""
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return current_user


# --- Project ownership helper (used by /personas, /orgs, /user-stories) ---

def user_owns_project_uuid(current_user: User, project_id: UUID | str) -> bool:
    """True if *current_user* owns the project this UUID maps to.

    Admin always true. Resolves the project_id back to its filesystem slug via
    ``project_registry.slug_for_project_id``; returns False if the slug is
    unknown (cannot establish ownership) so create endpoints fail closed.
    """
    if current_user.is_admin:
        return True
    # Local import to dodge the project_registry -> services circular path.
    from ai_qa_portal.backend.project_registry import slug_for_project_id
    import project_manager

    slug = slug_for_project_id(project_id)
    if not slug:
        return False
    owner = project_manager.get_project_owner(slug)
    return bool(owner) and owner == current_user.id


def assert_user_owns_project(current_user: User, project_id: UUID | str) -> None:
    """Raise 403 unless *current_user* owns the project this UUID maps to."""
    if not user_owns_project_uuid(current_user, project_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You do not have access to this project",
        )
